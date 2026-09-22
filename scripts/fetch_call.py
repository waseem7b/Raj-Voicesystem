#!/usr/bin/env python3
"""Pull Vapi call data, normalized for the call-review subsystem.

Filtered to Voicsito-owned stage assistants only (and the monolithic
rollback target). Calls handled by other assistants raise ValueError.

CLI:
    python scripts/fetch_call.py --id <vapi-call-id>
    python scripts/fetch_call.py --last 10
    python scripts/fetch_call.py --since 2026-05-19T00:00:00Z
    python scripts/fetch_call.py --last 25 --filter biz_type=cleaning
    python scripts/fetch_call.py --last 25 --filter ended_reason=customer-ended-call
    python scripts/fetch_call.py --last 25 --filter stage_reached=stage2

Output: JSON list of normalized dicts on stdout.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

VAPI_BASE = "https://api.vapi.ai"

VOICSITO_SQUAD_ID = "bea22dc5-24eb-42ed-ba81-f52cf1986ebf"

VOICSITO_ASSISTANT_IDS = {
    "46e47e95-674a-4624-9029-69bb39c4daf0": "stage1",  # Stage 1 Inbound Texts
    "7b2666e0-154d-4c85-82f0-07b4c51ad264": "stage2",  # Stage 2 Qualify
    "7600c2b6-d669-4f2e-8387-e371b0c193d4": "stage3",  # Stage 3 Book
    "261bf1a0-3527-4fcb-956c-b6e227d0bae1": "monolithic",  # Rollback target
    # Outbound Ashley assistants (2026-08-19) — day-before confirm + day-of reminder
    "236bfb42-8368-4cff-9042-41c2aeb43a81": "ashley-confirmation",
    "1b4e0bb0-8713-47ae-84f8-a6db6c5aec5b": "ashley-reminder",
    "67641961-542c-4b5f-831f-6b80bef8833b": "ashley-test",  # barebones twin (2026-08-30)
    "4f11df02-4401-4dfa-8413-a0e98a03099c": "ashley-test-v2",  # outcome-driven twin (2026-09-02)
    # Rebooking recovery call (2026-09-04). Was MISSING from this map until
    # 2026-09-10, which meant every rebooking call raised "not a Voicsito
    # assistant" and was invisible to --last/--since — the one tool the
    # rebooking handoff tells you to read those calls with.
    "a19e14c4-ef68-4e37-98b3-c3e448290e71": "ashley-rebooking",
}


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp (with optional trailing Z)."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _normalize(raw: dict) -> dict:
    """Convert a raw Vapi /call response into our normalized shape."""
    assistant_id = raw.get("assistantId")
    squad_id = raw.get("squadId")
    artifact = raw.get("artifact") or {}

    is_direct_assistant = assistant_id in VOICSITO_ASSISTANT_IDS
    is_squad = squad_id == VOICSITO_SQUAD_ID

    if not is_direct_assistant and not is_squad:
        raise ValueError(
            f"call {raw.get('id')} is not a Voicsito call "
            f"(assistantId={assistant_id}, squadId={squad_id})"
        )

    if is_squad:
        activations = artifact.get("assistantActivations") or []
        if activations:
            last = activations[-1]
            assistant_id = last.get("assistantId")
            assistant_name = last.get("assistantName")
            stage = VOICSITO_ASSISTANT_IDS.get(assistant_id, "unknown")
        else:
            assistant_id = None
            assistant_name = "Voicsito Squad"
            stage = "unknown"
    else:
        stage = VOICSITO_ASSISTANT_IDS[assistant_id]
        assistant_name = (raw.get("assistant") or {}).get("name")

    started = raw.get("startedAt")
    ended = raw.get("endedAt")
    duration_s = None
    if started and ended:
        duration_s = int(
            (_parse_iso(ended) - _parse_iso(started)).total_seconds()
        )

    customer = raw.get("customer") or {}
    metadata = raw.get("metadata") or {}

    transcript = raw.get("transcript") or artifact.get("transcript") or ""
    recording_url = raw.get("recordingUrl") or artifact.get("recordingUrl")

    return {
        "call_id": raw.get("id"),
        "started_at": started,
        "ended_at": ended,
        "duration_s": duration_s,
        "ended_reason": raw.get("endedReason"),
        "assistant_id": assistant_id,
        "assistant_name": assistant_name,
        "stage_reached": stage,
        "customer_number": customer.get("number"),
        "customer_first_name": customer.get("firstName"),
        "biz_type": metadata.get("biz_type"),
        "summary": raw.get("summary") or "",
        "transcript": transcript,
        "recording_url": recording_url,
        "raw_metadata": metadata,
    }


ALLOWED_FILTER_KEYS = {"biz_type", "ended_reason", "stage_reached", "objection_raised"}


def parse_filters(raw_filters: list) -> dict:
    """Parse a list of 'key=value' strings into a dict.

    Raises ValueError on unknown keys so typos surface immediately.
    """
    out = {}
    for raw in raw_filters:
        if "=" not in raw:
            raise ValueError(f"filter '{raw}' is not in key=value form")
        key, value = raw.split("=", 1)
        key = key.strip()
        if key not in ALLOWED_FILTER_KEYS:
            raise ValueError(
                f"unknown filter key '{key}'. allowed: {sorted(ALLOWED_FILTER_KEYS)}"
            )
        out[key] = value.strip()
    return out


def fetch_list(
    api_key: str,
    limit: int | None = None,
    since: str | None = None,
    filters: dict | None = None,
) -> list:
    """Pull recent Voicsito calls, optionally bounded and filtered.

    Vapi `/call` list endpoint params used: `limit`, `createdAtGt`.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {}
    if limit:
        # over-fetch since we'll filter to Voicsito assistants client-side
        params["limit"] = max(limit * 5, 50)
    if since:
        params["createdAtGt"] = since

    r = httpx.get(f"{VAPI_BASE}/call", headers=headers, params=params, timeout=30.0)
    r.raise_for_status()

    raw_calls = r.json()
    out = []
    for raw in raw_calls:
        try:
            norm = _normalize(raw)
        except ValueError:
            continue  # not a Voicsito call — skip silently

        if filters:
            if filters.get("biz_type") and norm.get("biz_type") != filters["biz_type"]:
                continue
            if filters.get("ended_reason") and norm.get("ended_reason") != filters["ended_reason"]:
                continue
            if filters.get("stage_reached") and norm.get("stage_reached") != filters["stage_reached"]:
                continue
            if filters.get("objection_raised"):
                ors = (norm.get("raw_metadata") or {}).get("objection_raised")
                if ors != filters["objection_raised"]:
                    continue

        out.append(norm)
        if limit and len(out) >= limit:
            break
    return out


def _fetch_raw(call_id: str, api_key: str) -> dict:
    """Raw Vapi /call response, retried — the API intermittently drops the
    response body mid-stream on the larger call records."""
    headers = {"Authorization": f"Bearer {api_key}"}
    last: Exception | None = None
    for _ in range(3):
        try:
            r = httpx.get(f"{VAPI_BASE}/call/{call_id}", headers=headers, timeout=90.0)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retry any transport/decode failure
            last = e
            time.sleep(2)
    raise RuntimeError(f"could not fetch call {call_id}: {last}")


def fetch_one(call_id: str, api_key: str) -> dict:
    """Pull a single call by Vapi ID, normalize, validate it's Voicsito."""
    return _normalize(_fetch_raw(call_id, api_key))


def _load_api_key() -> str:
    """Read VAPI_API_KEY from config/.env or env var."""
    if os.environ.get("VAPI_API_KEY"):
        return os.environ["VAPI_API_KEY"]
    env_path = Path(__file__).resolve().parent.parent / "config" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("VAPI_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("VAPI_API_KEY not found in env or config/.env")


def print_call_log(call_id: str, api_key: str) -> None:
    """Print the FULL turn-by-turn record for one call.

    The flat `transcript` string Vapi returns drops tool calls and, worse, the
    ordering between them and the spoken turns — which is where the real bugs
    show up (an endCall firing before the sign-off looks fine in `transcript`).
    This prints every message in sequence instead, with the system prompt
    elided since it is 16K of known content.
    """
    raw = _fetch_raw(call_id, api_key)
    art = raw.get("artifact") or {}
    msgs = art.get("messages") or raw.get("messages") or []

    started, ended = raw.get("startedAt", ""), raw.get("endedAt", "")
    print(f"call {call_id}")
    print(f"  assistant   {VOICSITO_ASSISTANT_IDS.get(raw.get('assistantId'), raw.get('assistantId'))}")
    print(f"  to          {(raw.get('customer') or {}).get('number', '-')}")
    print(f"  started     {started}")
    print(f"  ended       {ended}   reason={raw.get('endedReason', '-')}")
    print(f"  cost        {raw.get('cost', '-')}")
    print(f"  recording   {raw.get('recordingUrl') or '-'}")

    vv = art.get("variableValues") or {}
    slim = {k: v for k, v in vv.items() if not isinstance(v, dict)}
    if slim:
        print(f"  variables   {slim}")

    print("\n  --- turns ---")
    for i, m in enumerate(msgs):
        role = m.get("role", "?")
        if role == "system":
            print(f"  [{i:2d}] SYSTEM      ({len(m.get('message') or '')} chars, elided)")
        elif role == "tool_calls":
            for tc in m.get("toolCalls") or []:
                fn = tc.get("function") or {}
                print(f"  [{i:2d}] TOOL CALL   {fn.get('name')}  args={fn.get('arguments')}")
        elif role == "tool_call_result":
            print(f"  [{i:2d}] TOOL RESULT {m.get('name')} -> {str(m.get('result'))[:300]}")
        else:
            label = "PROSPECT" if role == "user" else "ASHLEY  " if role == "bot" else role.upper()
            print(f"  [{i:2d}] {label:11s} {m.get('message') or m.get('content') or ''}")

    if raw.get("summary"):
        print(f"\n  --- summary ---\n  {raw['summary']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Not required: `--log` alone defaults to the most recent call.
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--id", help="single Vapi call id")
    g.add_argument("--last", type=int, help="N most recent Voicsito calls")
    g.add_argument("--since", help="ISO timestamp; pull all Voicsito calls since this time")
    ap.add_argument("--filter", action="append", default=[],
                    help="key=value filter; repeatable. Keys: biz_type, ended_reason, stage_reached")
    ap.add_argument("--log", action="store_true",
                    help="human-readable turn-by-turn log incl. tool calls (use with --id, "
                         "or alone to log the most recent call)")
    args = ap.parse_args()

    if not args.log and not (args.id or args.last or args.since):
        ap.error("one of --id/--last/--since is required (or use --log on its own)")

    api_key = _load_api_key()

    if args.log:
        call_id = args.id
        if not call_id:
            recent = fetch_list(api_key=api_key, limit=1, since=None, filters={})
            if not recent:
                print("no recent Voicsito calls")
                return 1
            call_id = recent[0]["call_id"]
        print_call_log(call_id, api_key)
        return 0

    if args.id:
        result = [fetch_one(args.id, api_key)]
    else:
        filters = parse_filters(args.filter)
        result = fetch_list(
            api_key=api_key,
            limit=args.last,
            since=args.since,
            filters=filters,
        )

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
