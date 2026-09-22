#!/usr/bin/env python3
"""Make the Stage 3 tool fillers silent (config-only, idempotent).

Empties the `request-response-delayed` filler ("Just one sec, brother —
pulling that up.") on `mark_for_transfer` and `book_video_call` so the only
spoken stall in Stage 3 is the scripted "Give me a second..." line on
get_available_slots. These tools are referenced by ID in push_squad.py, which
never sets their `messages`, so this targeted PATCH is the source of truth for
the silent state. Re-run any time to re-apply.

Rollback: set content back to "Just one sec, brother — pulling that up." / 4000.

Usage:
    .venv-test/Scripts/python scripts/_set_tool_fillers.py

Env: requires VAPI_API_KEY in config/.env.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

VAPI_KEY = os.environ.get("VAPI_API_KEY")
if not VAPI_KEY:
    sys.exit("✗ VAPI_API_KEY missing from config/.env")

HEADERS = {"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"}

# Silent convention mirrored from get_available_slots (empty content = no filler).
SILENT = [{"type": "request-response-delayed", "content": "", "timingMilliseconds": 4000}]

TOOLS = {
    "mark_for_transfer": "dcdba267-0cb0-4af2-8010-9b3ffecdd976",
    "book_video_call": "cdc4465d-e827-4453-8e29-819d6f6028f3",
}


def main() -> None:
    for name, tid in TOOLS.items():
        r = httpx.patch(f"https://api.vapi.ai/tool/{tid}", headers=HEADERS,
                        json={"messages": SILENT}, timeout=30)
        if r.status_code >= 300:
            sys.exit(f"✗ PATCH {name} failed ({r.status_code}): {r.text[:400]}")
        d = httpx.get(f"https://api.vapi.ai/tool/{tid}", headers=HEADERS, timeout=30).json()
        print(f"✓ {name}: messages={json.dumps(d.get('messages'), ensure_ascii=False)}")


if __name__ == "__main__":
    main()
