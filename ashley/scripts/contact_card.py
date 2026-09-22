#!/usr/bin/env python3
"""Manage Ashley's sender contact card (iMessage Name & Photo).

    python ashley/scripts/contact_card.py show
    python ashley/scripts/contact_card.py set-name Ashley Miller
    python ashley/scripts/contact_card.py set-photo path/to/photo.jpg
    python ashley/scripts/contact_card.py share chat_019fd417-...

IMPORTANT — reading or editing the card is number-scoped, so it returns 403
while the Blooio number is assigned to the GoHighLevel integration (which is the
normal, desired state). To change Ashley's name or photo you must temporarily
reassign the number to this API key in the Blooio dashboard, make the change,
then reassign it back to GoHighLevel.

Sharing the card is chat-scoped and works regardless — that is what the webhook
does on every run.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from blooio.client import BlooioClient, BlooioError  # noqa: E402

ENV_PATH = Path(__file__).resolve().parents[1] / "config" / ".env"
MAX_AVATAR_BYTES = 10_000_000  # v4 limit


def sender_number(client: BlooioClient, env: dict) -> str:
    """The number the card belongs to, from .env or the first active channel."""
    if env.get("BLOOIO_SENDER_NUMBER"):
        return env["BLOOIO_SENDER_NUMBER"]
    for ch in client.list_channels():
        if ch.get("status") == "active" and ch.get("address"):
            return ch["address"]
    raise SystemExit("FAIL: no active channel; set BLOOIO_SENDER_NUMBER in .env")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    p_name = sub.add_parser("set-name")
    p_name.add_argument("first")
    p_name.add_argument("last")
    p_photo = sub.add_parser("set-photo")
    p_photo.add_argument("path")
    p_share = sub.add_parser("share")
    p_share.add_argument("chat_id", help="chat_... id (NOT a phone or grp_ id)")
    args = parser.parse_args()

    env = dotenv_values(ENV_PATH)
    client = BlooioClient(api_key=env["BLOOIO_API_KEY"], timeout=60)

    try:
        if args.cmd == "share":
            print(json.dumps(client.share_contact_card(args.chat_id), indent=2))
            return 0

        number = sender_number(client, env)
        print(f"sender number: {number}")

        if args.cmd == "show":
            card = client.get_contact_card(number)
            card.pop("avatar", None)
            print(json.dumps(card, indent=2))
            return 0

        # Always send the whole card: partial writes persist in Blooio's
        # database but never reach the device.
        current = client.get_contact_card(number)
        card = {
            "first_name": current.get("first_name"),
            "last_name": current.get("last_name"),
            "avatar": current.get("avatar"),
            "sharing": current.get("sharing")
            or {"enabled": True, "audience": 1, "name_format": 0},
        }

        if args.cmd == "set-name":
            card["first_name"], card["last_name"] = args.first, args.last
        else:
            raw = Path(args.path).read_bytes()
            if len(raw) > MAX_AVATAR_BYTES:
                raise SystemExit(f"FAIL: photo is {len(raw)} bytes; max 10 MB")
            card["avatar"] = base64.b64encode(raw).decode()

        out = client.update_contact_card(number, **card)
        print(json.dumps({k: v for k, v in out.items() if k != "avatar"}, indent=2))
        print("note: the device takes ~1-3 min to apply the change")
    except BlooioError as e:
        if e.status_code == 403:
            print(
                "FAIL: 403 — the card is number-scoped and this number is "
                "assigned to the GoHighLevel integration.\n"
                "Reassign it to this API key in the Blooio dashboard to edit "
                "the card, then reassign it back."
            )
        else:
            print(f"FAIL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
