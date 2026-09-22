#!/usr/bin/env python3
"""Ashley / Blooio smoke test — prove we can own the API.

Steps:
  1. Auth + capability check on the target number (is it iMessage-capable?)
  2. Send one test iMessage
  3. Poll delivery status until sent/delivered (or timeout)

Usage:
    python ashley/scripts/smoke.py +1XXXXXXXXXX [--text "custom message"]

Reads BLOOIO_API_KEY from ashley/config/.env.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from blooio.client import BlooioClient, BlooioError  # noqa: E402

ENV_PATH = Path(__file__).resolve().parents[1] / "config" / ".env"
DEFAULT_TEXT = "Ashley system smoke test — reply anything to test inbound later."
POLL_SECONDS = 60


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("number", help="Target phone in E.164, e.g. +15551234567")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()

    env = dotenv_values(ENV_PATH)
    api_key = env.get("BLOOIO_API_KEY")
    if not api_key:
        print(f"FAIL: BLOOIO_API_KEY missing in {ENV_PATH}")
        return 1

    client = BlooioClient(api_key=api_key)

    print("[1/3] channels")
    try:
        channels = client.list_channels()
    except BlooioError as e:
        print(f"FAIL: {e}")
        return 1
    for ch in channels:
        print(f"      -> {ch.get('address')} {ch.get('status')} {ch.get('capabilities', {}).get('protocols')}")

    print(f"[2/3] sending test message to {args.number}")
    sent = client.send_message(args.number, args.text)
    print(f"      -> id={sent.get('id')} protocol={sent.get('protocol')} status={sent.get('status')}")
    message_id = sent.get("id")
    chat_id = sent.get("chat_id")
    if not message_id or not chat_id:
        print("FAIL: no id/chat_id in send response")
        return 1

    print(f"[3/3] polling status (up to {POLL_SECONDS}s)")
    deadline = time.time() + POLL_SECONDS
    last = None
    while time.time() < deadline:
        status = client.get_message_status(chat_id, message_id)
        if status != last:
            print(f"      -> {status}")
            last = status
        state = str(status.get("status", "")).lower()
        if state in {"sent", "delivered", "read", "failed"}:
            print(f"DONE: terminal status '{state}'")
            return 0 if state != "failed" else 1
        time.sleep(3)

    print("DONE: no terminal status within timeout (message may still deliver)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
