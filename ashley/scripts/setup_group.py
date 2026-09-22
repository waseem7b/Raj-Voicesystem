#!/usr/bin/env python3
"""Brand a prospect's group chat by hand: name, icon, and Ashley's card.

    python ashley/scripts/setup_group.py --business "Tree City"
    python ashley/scripts/setup_group.py --business "C7A" --no-card

Finds the group by the title GHL created it with ("Growth System - <business>"),
then runs the same sequence the webhook does. Idempotent — safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from blooio.client import BlooioClient, BlooioError  # noqa: E402
from blooio.group_setup import setup_prospect_group  # noqa: E402

ENV_PATH = Path(__file__).resolve().parents[1] / "config" / ".env"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--business", required=True, help="Prospect's business name")
    parser.add_argument("--no-card", action="store_true", help="Skip the contact card")
    parser.add_argument(
        "--wait", type=float, default=0,
        help="Seconds to wait for the group to appear (default: don't wait)",
    )
    args = parser.parse_args()

    env = dotenv_values(ENV_PATH)
    client = BlooioClient(api_key=env["BLOOIO_API_KEY"], timeout=60)

    try:
        result = setup_prospect_group(
            client,
            args.business,
            share_card=not args.no_card,
            wait_timeout=args.wait,
        )
    except BlooioError as e:
        print(f"FAIL: {e}")
        return 1

    if not result["group_id"]:
        print(f"FAIL: {result['error']} — {result.get('detail', '')}")
        return 1

    print(f"group {result['group_id']} | chat {result['chat_id']} | {result['name']}")
    for step in result["steps"]:
        status = "ok" if step["ok"] else f"FAIL: {step.get('error')}"
        print(f"  {step['step']:8} {status}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
