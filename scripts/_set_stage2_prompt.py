"""One-off: push ONLY the Stage 2 (Qualify) system prompt to the live assistant.

Surgical prompt-only PATCH — deliberately avoids push_squad.py so the uncommitted
local edits to stage1/stage3/shared_persona are NOT deployed alongside.

Safety:
  - Backs up the current live prompt to scripts/_stage2_prompt_backup_<ts>.json
    BEFORE patching (one-line revert: pass that file to --revert).
  - Refuses to push if the only delta between live and local is NOT the break
    tags (guards against shipping unrelated drift). Override with --force.

Usage:
    python scripts/_set_stage2_prompt.py            # backup + safety-check + push
    python scripts/_set_stage2_prompt.py --force     # skip the drift check
    python scripts/_set_stage2_prompt.py --revert scripts/_stage2_prompt_backup_<ts>.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")
sys.path.insert(0, str(ROOT))

from src.agent.prompts import get_stage_prompt  # noqa: E402

STAGE2_ID = "7b2666e0-154d-4c85-82f0-07b4c51ad264"
BASE = "https://api.vapi.ai"
KEY = os.environ["VAPI_API_KEY"]
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# Drift guard: with every <break ...> tag stripped, local and live must be identical.
# This allows adding/retuning break tags while still catching any unrelated change
# (e.g. an uncommitted shared_persona edit that isn't live yet).
_BREAK_RE = re.compile(r'\s*<break time="[^"]*"/>')


def strip_breaks(s: str) -> str:
    return _BREAK_RE.sub("", s)


def get_assistant() -> dict:
    return httpx.get(f"{BASE}/assistant/{STAGE2_ID}", headers=H, timeout=20).json()


def system_msg(model: dict) -> dict:
    for m in model["messages"]:
        if m.get("role") == "system":
            return m
    raise SystemExit("x no system message found on Stage 2")


def patch_content(new_content: str) -> None:
    cur = get_assistant()
    model = cur["model"]
    system_msg(model)["content"] = new_content
    r = httpx.patch(
        f"{BASE}/assistant/{STAGE2_ID}", headers=H, json={"model": model}, timeout=30
    )
    if r.status_code >= 400:
        raise SystemExit(f"x PATCH FAILED {r.status_code}: {r.text[:500]}")
    back = system_msg(get_assistant()["model"])["content"]
    print(f"  [ok] live prompt now {len(back)} chars | break tags: {back.count('<break time=')}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="skip the drift safety-check")
    ap.add_argument("--revert", metavar="BACKUP.json", help="restore prompt from a backup file")
    args = ap.parse_args()

    if args.revert:
        data = json.loads(Path(args.revert).read_text(encoding="utf-8"))
        print(f"Reverting Stage 2 prompt from {args.revert} ({len(data['old_content'])} chars)...")
        patch_content(data["old_content"])
        return

    live = system_msg(get_assistant()["model"])["content"]
    local = get_stage_prompt("inbound_texts", "stage2")

    # Backup BEFORE any change.
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = ROOT / "scripts" / f"_stage2_prompt_backup_{ts}.json"
    backup.write_text(
        json.dumps({"assistant_id": STAGE2_ID, "ts": ts, "old_content": live}, indent=2),
        encoding="utf-8",
    )
    print(f"Backed up live prompt -> {backup}")

    # Drift safety-check: ignoring all break tags, local must equal live.
    if strip_breaks(local) != strip_breaks(live) and not args.force:
        print("x DRIFT DETECTED: local prompt differs from live beyond the break tags.")
        print(f"  live={len(live)} chars, local={len(local)} chars")
        print("  Refusing to push (would ship unrelated changes). Re-run with --force to override.")
        sys.exit(2)

    print(f"Safety-check passed. Pushing Stage 2 prompt ({len(live)} -> {len(local)} chars)...")
    patch_content(local)
    print(f"Revert with: python scripts/_set_stage2_prompt.py --revert {backup}")


if __name__ == "__main__":
    main()
