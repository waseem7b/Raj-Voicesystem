#!/usr/bin/env python3
"""Push a local prompt module to a Vapi assistant's system prompt.

Usage:
    .venv-extract/bin/python scripts/sync-vapi-prompt.py <prompt_module> [--assistant <id>] [--dry-run]

Examples:
    # Sync the inbound_texts prompt to whichever assistant is named that way in Vapi
    .venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts

    # Sync to a specific assistant id
    .venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts --assistant 0ffb1d95-aaaa-bbbb-cccc-dddddddddddd

    # Show what would change without sending
    .venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts --dry-run

The script reads the `PROMPT` constant from `src/agent/prompts/<module>.py`
and PATCHes Vapi's `model.messages[0].content` with that string.

Env: requires VAPI_API_KEY in config/.env (already gitignored).
"""

from __future__ import annotations

import argparse
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

VAPI_BASE = "https://api.vapi.ai"
HEADERS = {"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"}


def load_prompt(module_name: str) -> str:
    """Import src/agent/prompts/<module_name>.py and return its PROMPT constant."""
    sys.path.insert(0, str(ROOT / "src" / "agent"))
    try:
        from prompts import get_prompt  # type: ignore
    except Exception as e:
        sys.exit(f"✗ Could not import prompts package: {e}")
    try:
        return get_prompt(module_name)
    except KeyError:
        sys.exit(f"✗ Prompt module '{module_name}' not registered in prompts/__init__.py")


def find_assistant_by_name(name_substr: str) -> dict | None:
    r = httpx.get(f"{VAPI_BASE}/assistant", headers=HEADERS, timeout=15.0)
    r.raise_for_status()
    for a in r.json():
        if name_substr.lower() in (a.get("name") or "").lower():
            return a
    return None


def get_assistant(assistant_id: str) -> dict:
    r = httpx.get(f"{VAPI_BASE}/assistant/{assistant_id}", headers=HEADERS, timeout=15.0)
    r.raise_for_status()
    return r.json()


def patch_assistant(assistant_id: str, body: dict) -> dict:
    r = httpx.patch(
        f"{VAPI_BASE}/assistant/{assistant_id}",
        headers=HEADERS,
        json=body,
        timeout=20.0,
    )
    if r.status_code >= 400:
        sys.exit(f"✗ Vapi PATCH failed {r.status_code}: {r.text[:500]}")
    return r.json()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("module", help="prompt module name, e.g. inbound_texts")
    p.add_argument("--assistant", help="Vapi assistant id (otherwise looked up by name)")
    p.add_argument("--name-match", default=None, help="substring to find an assistant by name")
    p.add_argument("--dry-run", action="store_true", help="show diff but don't PATCH")
    args = p.parse_args()

    new_prompt = load_prompt(args.module)
    print(f"Loaded prompt '{args.module}': {len(new_prompt)} chars")

    if args.assistant:
        assistant = get_assistant(args.assistant)
    else:
        match = args.name_match or args.module.replace("_", " ")
        assistant = find_assistant_by_name(match)
        if not assistant:
            sys.exit(
                f"✗ No assistant matched name substring '{match}'.\n"
                f"  Pass --assistant <id> or --name-match <substr> explicitly."
            )

    aid = assistant["id"]
    aname = assistant.get("name", "(unnamed)")
    print(f"Target assistant: {aname}  ({aid})")

    model = assistant.get("model") or {}
    messages = model.get("messages") or []
    current = ""
    for m in messages:
        if m.get("role") == "system":
            current = m.get("content", "")
            break
    delta = len(new_prompt) - len(current)
    print(f"Current system prompt: {len(current)} chars; new: {len(new_prompt)} chars (Δ {delta:+d})")

    if current.strip() == new_prompt.strip():
        print("No changes — prompts are identical. Done.")
        return

    if args.dry_run:
        print("--- DRY RUN: not sending PATCH ---")
        print("First 200 chars of new prompt:")
        print(new_prompt[:200])
        return

    # Build new messages: replace existing system or prepend a new one
    new_messages = []
    replaced = False
    for m in messages:
        if m.get("role") == "system" and not replaced:
            new_messages.append({"role": "system", "content": new_prompt})
            replaced = True
        else:
            new_messages.append(m)
    if not replaced:
        new_messages.insert(0, {"role": "system", "content": new_prompt})

    body = {"model": {**model, "messages": new_messages}}
    patch_assistant(aid, body)
    print(f"✓ Synced. Assistant {aname} ({aid}) updated.")


if __name__ == "__main__":
    main()
