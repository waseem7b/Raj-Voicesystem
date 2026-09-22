"""One-off: backfill the call ledger for the time before it existed.

The ledger (src/webhooks/call_ledger.py) records every call decision from its
deploy onward. This rebuilds the earlier history once, into the same dated
ledger files, which the CRM ingests like any other line:

  placed / ended  every confirmation, reminder and rebooking call Vapi still
                  keeps (14 days on this plan). The outcome is classified by the
                  same _classify_outcome / _outbound_header as the live path;
                  in-call tool results stand in for the live cache's
                  confirm_outcome.
  skipped         the journal's "skipped: opted out", "skipped: DND" and
                  "paused: prospect replied last" lines (journal retention sets
                  how far back). Appointment-status skips are not recoverable:
                  that log line names neither the call type nor the contact.

Only decisions BEFORE --until are written, so nothing the live ledger already
has is duplicated. Re-running is harmless: the CRM dedupes placed/ended by Vapi
call id and skips by (type, contact, time).

Run on the VPS:
  cd /opt/webhooks/voicsito
  venv/bin/python scripts/backfill_call_ledger.py --until 2026-09-15T03:00:00Z [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.webhooks import server  # noqa: E402,F401 — mounts routers before outbound is imported
from src.webhooks import call_ledger  # noqa: E402
from src.webhooks.outbound import _classify_outcome, _outbound_header  # noqa: E402

TRACKED = {"confirmation", "reminder", "rebooking"}
TOOL_OUTCOME = {
    "mark_locked_in": "confirmed",
    "mark_not_interested": "not_interested",
    "mark_needs_reschedule": "reschedule_requested",
}
SKIP_RE = re.compile(
    r"ghl/outbound-call (?:skipped: (?P<why>opted out|DND)|paused: prospect replied last)"
    r" call_type=(?P<ct>\w+) contact=(?P<contact>\S+)"
)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _vapi_key() -> str:
    key = os.environ.get("VAPI_API_KEY", "")
    if key:
        return key
    # Read-only fallback to the service env file; the value is never printed.
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VAPI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("VAPI_API_KEY not found")


def fetch_calls(since: datetime) -> list[dict]:
    headers = {"Authorization": f"Bearer {_vapi_key()}"}
    seen: dict[str, dict] = {}
    before = None
    while True:
        params = {"limit": 1000, "createdAtGt": since.isoformat()}
        if before:
            params["createdAtLt"] = before
        r = httpx.get("https://api.vapi.ai/call", headers=headers, params=params, timeout=60)
        page = r.json()
        if not isinstance(page, list):
            raise SystemExit(f"Vapi list failed: HTTP {r.status_code}")
        fresh = [c for c in page if c.get("id") not in seen]
        for c in fresh:
            seen[c["id"]] = c
        if len(page) < 1000 or not fresh:
            return list(seen.values())
        before = min(c["createdAt"] for c in page)


def confirm_outcome_from(messages: list) -> str:
    """The in-call outcome a successful tool call would have cached live."""
    outcome = ""
    for m in messages or []:
        if m.get("role") != "tool_call_result":
            continue
        name = m.get("name")
        if name in TOOL_OUTCOME and "=false" not in str(m.get("result") or ""):
            outcome = TOOL_OUTCOME[name]
    return outcome


def call_decisions(calls: list[dict], until: datetime):
    for c in calls:
        meta = c.get("metadata") or {}
        call_type = (meta.get("call_type") or "").lower()
        if c.get("type") != "outboundPhoneCall" or call_type not in TRACKED:
            continue
        created = _parse(c["createdAt"])
        if created >= until:
            continue
        target = {
            "vapi_call_id": c["id"],
            "ghl_contact_id": meta.get("ghl_contact_id"),
            "ghl_appointment_id": meta.get("ghl_appointment_id"),
            "business": meta.get("ghl_business"),
        }
        yield "placed", created, call_type, target

        ended = c.get("endedAt")
        if c.get("status") != "ended" or not ended or _parse(ended) >= until:
            continue  # still live at --until, or no end: the live ledger owns its end
        messages = c.get("messages") or (c.get("artifact") or {}).get("messages") or []
        confirm = confirm_outcome_from(messages)
        tag = _classify_outcome(c.get("endedReason") or "", confirm, messages)
        cost = c.get("cost")
        yield "ended", _parse(ended), call_type, {
            **target,
            "outcome": call_ledger.outcome_key(_outbound_header({"confirm_outcome": confirm}, tag)),
            "outcome_tag": tag,
            "ended_reason": c.get("endedReason"),
            "duration_sec": call_ledger.seconds_between(c.get("startedAt"), ended),
            "cost": cost if isinstance(cost, (int, float)) else None,
        }


def journal_skips(since: datetime, until: datetime):
    out = subprocess.run(
        ["journalctl", "-u", "voicsito-webhooks", "--no-pager", "-o", "json",
         f"--since=@{int(since.timestamp())}", f"--until=@{int(until.timestamp())}"],
        capture_output=True, text=True, check=True,
    ).stdout
    for raw in out.splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = entry.get("MESSAGE")
        if not isinstance(msg, str):
            continue
        m = SKIP_RE.search(msg)
        if not m:
            continue
        at = datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000, tz=timezone.utc)
        reason = {"opted out": "opted_out", "DND": "dnd"}.get(m.group("why") or "", "awaiting_our_reply")
        yield "skipped", at, m.group("ct").lower(), {"ghl_contact_id": m.group("contact"), "reason": reason}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", required=True, help="ISO time the live ledger went live")
    ap.add_argument("--days", type=int, default=14, help="how far back to read (Vapi keeps 14)")
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = ap.parse_args()

    until = _parse(args.until)
    since = until - timedelta(days=args.days)
    decisions = [*call_decisions(fetch_calls(since), until), *journal_skips(since, until)]

    counts = collections.Counter((event, call_type) for event, _, call_type, _ in decisions)
    written = 0
    if not args.dry_run:
        for event, at, call_type, fields in sorted(decisions, key=lambda d: d[1]):
            source = "backfill-journal" if event == "skipped" else "backfill-vapi"
            written += call_ledger.record(event, call_type=call_type, at=at, source=source, **fields)
    for (event, call_type), n in sorted(counts.items()):
        print(f"{event:8} {call_type:13} {n}")
    print(f"decisions: {len(decisions)}  written: {written}  dry_run: {args.dry_run}")


if __name__ == "__main__":
    main()
