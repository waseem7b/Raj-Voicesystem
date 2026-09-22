#!/usr/bin/env python3
"""verify_tools.py — health-check (and optionally repair) the live Vapi tool wiring.

Pulls the live assistants and asserts every server-backed (function) tool still
points at the right webhook endpoint — and that outbound tools still carry a
secret. Catches the silent breakage where a push or a dashboard edit strips a
tool's server.url/secret and booking/lookup dies while everything else looks
fine (the 2026-06-18 book_video_call "No result returned" incident).

Checks, per target assistant:
  - every function tool's server.url == the squad's expected endpoint
  - tools on the OUTBOUND endpoint have a secret set
  - the CRITICAL tools are actually attached (catastrophic if missing)

Follow-up handler (`--fix`, or fix=True from a push script):
  - STANDALONE tools (attached by toolId) with a wrong/missing url are
    auto-restored via PATCH /tool/{id} to the canonical server block.
  - INLINE tools (the confirm squad builds these itself) are NOT auto-patched —
    the owning push script writes them, so the fix is to re-run that script with
    VAPI_WEBHOOK_SECRET present. These are reported as unrepairable so the push
    fails loudly instead of pretending it's fine.

Read-only unless --fix is passed. Exit 0 = clean, exit 1 = unresolved problems.

Usage:
    .venv-test/Scripts/python.exe scripts/verify_tools.py [--fix] [--scope inbound|confirm|all]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

KEY = os.environ.get("VAPI_API_KEY")
if not KEY:
    sys.exit("✗ VAPI_API_KEY missing from config/.env")
WEBHOOK_SECRET = os.environ.get("VAPI_WEBHOOK_SECRET", "")

BASE = "https://api.vapi.ai"
H = {"Authorization": f"Bearer {KEY}"}
HJSON = {**H, "Content-Type": "application/json"}

INBOUND_URL = "https://webhooks.c7a.io/voicsito/vapi/tool"
OUTBOUND_URL = "https://webhooks.c7a.io/voicsito/vapi/outbound/tool"

# Canonical per-tool timeouts, used when repairing a standalone tool's server.
TIMEOUTS = {
    "update_prospect_info": 12,
    "get_available_slots": 25,
    "mark_for_transfer": 20,
    "book_video_call": 45,
    "get_prospect_appointment": 25,
    "mark_locked_in": 12,
    "reschedule_video_call": 25,
}

# Target assistant -> (expected endpoint, tools that MUST be attached).
INBOUND_TARGETS = {
    "Voicsito - Stage 1 - Inbound Texts": (INBOUND_URL, ["update_prospect_info"]),
    "Voicsito - Stage 2 - Qualify": (INBOUND_URL, ["update_prospect_info"]),
    "Voicsito - Stage 3 - Book": (
        INBOUND_URL,
        ["update_prospect_info", "get_available_slots", "mark_for_transfer", "book_video_call"],
    ),
}
CONFIRM_TARGETS = {
    "Voicsito - Confirm - Stage 1 - Confirm": (OUTBOUND_URL, ["get_prospect_appointment"]),
    "Voicsito - Confirm - Stage 2 - Rebook": (OUTBOUND_URL, ["reschedule_video_call"]),
}
ALL_TARGETS = {**INBOUND_TARGETS, **CONFIRM_TARGETS}


def _desired_server(tool_name: str, endpoint: str) -> dict:
    """The canonical server block a tool on `endpoint` should carry."""
    s = {"url": endpoint, "timeoutSeconds": TIMEOUTS.get(tool_name, 20)}
    if endpoint == OUTBOUND_URL and WEBHOOK_SECRET:
        s["secret"] = WEBHOOK_SECRET
    return s


def _fetch_assistants() -> dict:
    r = httpx.get(f"{BASE}/assistant", headers=H, timeout=30)
    r.raise_for_status()
    return {a.get("name"): a for a in r.json()}


def _resolve_tools(assistant: dict) -> dict:
    """{tool_name: {"server", "tool_id", "inline"}} for all function tools,
    covering inline `model.tools` AND attached `model.toolIds`."""
    model = assistant.get("model", {}) or {}
    out: dict = {}
    for t in (model.get("tools") or []):
        fn = (t.get("function") or {}).get("name")
        if t.get("type") == "function" and fn:
            out[fn] = {"server": t.get("server") or {}, "tool_id": t.get("id"), "inline": True}
    for tid in (model.get("toolIds") or []):
        try:
            t = httpx.get(f"{BASE}/tool/{tid}", headers=H, timeout=30).json()
        except Exception as e:  # noqa: BLE001 — report, don't crash the audit
            out[f"<unresolved {tid}>"] = {"server": {"_error": str(e)}, "tool_id": tid, "inline": False}
            continue
        fn = (t.get("function") or {}).get("name")
        if t.get("type") == "function" and fn:
            out[fn] = {"server": t.get("server") or {}, "tool_id": tid, "inline": False}
    return out


def audit(targets: dict) -> list[dict]:
    """Return a list of discrepancy dicts (empty = all good). Prints as it goes."""
    assistants = _fetch_assistants()
    discrepancies: list[dict] = []

    for name, (endpoint, critical) in targets.items():
        print(f"\n=== {name} ===")
        a = assistants.get(name)
        if not a:
            print("  ✗ assistant NOT FOUND")
            discrepancies.append({"assistant": name, "tool": None, "tool_id": None,
                                  "inline": False, "endpoint": endpoint,
                                  "problem": "assistant not found", "repairable": False})
            continue

        tools = _resolve_tools(a)
        for tname, info in sorted(tools.items()):
            srv = info["server"]
            if "_error" in srv:
                print(f"  ✗ {tname}: {srv['_error']}")
                discrepancies.append({"assistant": name, "tool": tname, "tool_id": info["tool_id"],
                                      "inline": info["inline"], "endpoint": endpoint,
                                      "problem": "tool unresolved", "repairable": False})
                continue
            url, secret_set = srv.get("url"), bool(srv.get("secret"))
            problems = []
            if url != endpoint:
                problems.append(f"url={url!r} (expected {endpoint!r})")
            if endpoint == OUTBOUND_URL and not secret_set:
                problems.append("secret MISSING")
            timeout = srv.get("timeoutSeconds")
            mark = "✓" if not problems else "✗"
            print(f"  {mark} {tname}: url={'ok' if url == endpoint else url!r} "
                  f"secret={secret_set}{f' timeout={timeout}s' if timeout else ''}"
                  f"{' [inline]' if info['inline'] else ''}")
            for p in problems:
                # Standalone url drift is auto-repairable; inline drift and
                # missing secrets need the owning push script / env var.
                repairable = (not info["inline"]) and p.startswith("url=") and bool(info["tool_id"])
                discrepancies.append({"assistant": name, "tool": tname, "tool_id": info["tool_id"],
                                      "inline": info["inline"], "endpoint": endpoint,
                                      "problem": p, "repairable": repairable})

        for tname in critical:
            if tname not in tools:
                print(f"  ✗ CRITICAL tool '{tname}' NOT attached")
                discrepancies.append({"assistant": name, "tool": tname, "tool_id": None,
                                      "inline": False, "endpoint": endpoint,
                                      "problem": "critical tool not attached", "repairable": False})
    return discrepancies


def repair(discrepancies: list[dict]) -> list[dict]:
    """Auto-restore repairable (standalone) tools. Returns the still-unresolved
    discrepancies (inline drift, missing secret, missing tool, etc.)."""
    unresolved, repaired_ids = [], set()
    for d in discrepancies:
        if not d["repairable"] or d["tool_id"] in repaired_ids:
            if not d["repairable"]:
                unresolved.append(d)
            continue
        desired = _desired_server(d["tool"], d["endpoint"])
        r = httpx.patch(f"{BASE}/tool/{d['tool_id']}", headers=HJSON, json={"server": desired}, timeout=30)
        if r.status_code < 300:
            print(f"  🔧 repaired {d['tool']} ({d['tool_id']}) -> {desired['url']}")
            repaired_ids.add(d["tool_id"])
        else:
            print(f"  ✗ repair FAILED for {d['tool']}: {r.status_code} {r.text[:200]}")
            unresolved.append(d)
    return unresolved


def post_push_check(scope: str = "all", fix: bool = True) -> bool:
    """Entry point for push scripts. Audit -> optionally repair -> re-audit.
    Returns True if everything is wired correctly afterward, False otherwise."""
    targets = {"inbound": INBOUND_TARGETS, "confirm": CONFIRM_TARGETS}.get(scope, ALL_TARGETS)
    print("\n" + "=" * 60 + "\nTOOL WIRING CHECK\n" + "=" * 60)
    problems = audit(targets)
    if problems and fix:
        repairable = [d for d in problems if d["repairable"]]
        if repairable:
            print(f"\nAuto-repairing {len({d['tool_id'] for d in repairable})} standalone tool(s)...")
            repair(problems)
            print("\nRe-auditing after repair...")
            problems = audit(targets)
    print("\n" + "=" * 60)
    if problems:
        print(f"TOOL CHECK FAILED — {len(problems)} unresolved problem(s):")
        for d in problems:
            hint = " (re-run the owning push with VAPI_WEBHOOK_SECRET set)" if (d["inline"] or "secret" in d["problem"]) else ""
            print(f"  - {d['assistant']}/{d['tool']}: {d['problem']}{hint}")
        return False
    print("TOOL CHECK PASSED — all tools wired correctly.")
    return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fix", action="store_true", help="auto-restore drifted standalone tools")
    p.add_argument("--scope", choices=["inbound", "confirm", "all"], default="all")
    args = p.parse_args()
    ok = post_push_check(scope=args.scope, fix=args.fix)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
