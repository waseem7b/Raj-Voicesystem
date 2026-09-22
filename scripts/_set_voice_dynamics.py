#!/usr/bin/env python3
"""One-off: soften ElevenLabs voice dynamics to reduce the barge-in cutoff click.

On interruption Vapi hard-cuts the TTS stream mid-waveform; the click volume
scales with the instantaneous amplitude at the cut. Lowering style/speakerBoost
and raising stability flattens the signal, so the (unavoidable) click is quieter.

Changes, on the 3 live stage assistants + the monolithic source-of-truth:
    style           1   -> 0.35
    stability       0.3 -> 0.5
    useSpeakerBoost true -> false

The full voice object is preserved (GET, override only these 3 keys, PATCH) so
voiceId / model / speed / similarityBoost / enableSsmlParsing can't be dropped.

Usage:
    python scripts/_set_voice_dynamics.py                         # dry-run (show now + plan)
    python scripts/_set_voice_dynamics.py --apply                 # apply + write backup
    python scripts/_set_voice_dynamics.py --revert <backup.json>  # restore exact before-state

Env: requires VAPI_API_KEY in config/.env.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't encode ✓/✗ — force utf-8 so
# status output never crashes mid-apply.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

VAPI_KEY = os.environ.get("VAPI_API_KEY")
if not VAPI_KEY:
    sys.exit("✗ VAPI_API_KEY missing from config/.env")

BASE = "https://api.vapi.ai"
H = {"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"}

# Source-of-truth template push_squad.py inherits voice from. Patch it too or the
# next deploy reverts the change (it does not override style/stability/speakerBoost).
MONO = "261bf1a0-3527-4fcb-956c-b6e227d0bae1"
STAGE_NAMES = [
    "Voicsito - Stage 1 - Inbound Texts",
    "Voicsito - Stage 2 - Qualify",
    "Voicsito - Stage 3 - Book",
]
# Outbound-demo confirmation squad — same Jon voice, separate assistants. These
# inherit voice from the monolith on (re)deploy, but the currently-live copies
# predate the monolith patch, so they need a direct PATCH to update now.
CONFIRM_NAMES = [
    "Voicsito - Confirm - Stage 0 - Router",
    "Voicsito - Confirm - Stage 1 - Confirm",
]
OVERRIDES = {"style": 0.35, "stability": 0.5, "useSpeakerBoost": False}

_VOICE_KEYS = (
    "provider", "model", "voiceId", "speed", "style",
    "stability", "similarityBoost", "useSpeakerBoost", "enableSsmlParsing",
)


def get(path: str):
    r = httpx.get(BASE + path, headers=H, timeout=20.0)
    r.raise_for_status()
    return r.json()


def patch(path: str, body: dict):
    r = httpx.patch(BASE + path, headers=H, json=body, timeout=30.0)
    if r.status_code >= 400:
        sys.exit(f"✗ PATCH {path} failed {r.status_code}: {r.text[:600]}")
    return r.json()


def summ(v: dict) -> dict:
    return {k: v.get(k) for k in _VOICE_KEYS}


def resolve_targets(confirm_squad: bool = False) -> list[dict]:
    alla = get("/assistant")
    by_id = {a["id"]: a for a in alla}
    by_name = {a.get("name"): a for a in alla}
    targets = []
    if confirm_squad:
        names = CONFIRM_NAMES
    else:
        if MONO in by_id:
            targets.append(by_id[MONO])
        else:
            print(f"! monolithic {MONO} NOT FOUND — skipping")
        names = STAGE_NAMES
    for n in names:
        if n in by_name:
            targets.append(by_name[n])
        else:
            print(f"! assistant '{n}' NOT FOUND — skipping")
    return targets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry-run)")
    ap.add_argument("--revert", metavar="BACKUP_JSON", help="restore voice objects from a backup file")
    ap.add_argument("--confirm-squad", action="store_true", help="target the Confirm squad instead of the inbound stages + monolith")
    args = ap.parse_args()

    if args.revert:
        data = json.loads(Path(args.revert).read_text())
        for aid, voice in data.items():
            patch(f"/assistant/{aid}", {"voice": voice})
            print(f"  ✓ reverted {aid} -> style={voice.get('style')} stability={voice.get('stability')} useSpeakerBoost={voice.get('useSpeakerBoost')}")
        print("revert done")
        return

    targets = resolve_targets(confirm_squad=args.confirm_squad)
    print(f"\n{len(targets)} target assistant(s){' [confirm squad]' if args.confirm_squad else ''}:\n")
    backup: dict[str, dict] = {}
    for t in targets:
        v = t["voice"]
        backup[t["id"]] = v
        nv = {**v, **OVERRIDES}
        print(f"  {t.get('name')}  ({t['id']})")
        print(f"    now : {summ(v)}")
        print(f"    new : style={nv['style']}  stability={nv['stability']}  useSpeakerBoost={nv['useSpeakerBoost']}  (rest unchanged)")

    if not args.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply to write.")
        return

    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    bpath = ROOT / "scripts" / f"_voice_backup_{ts}.json"
    bpath.write_text(json.dumps(backup, indent=2))
    print(f"\nbackup -> {bpath}")

    all_ok = True
    for t in targets:
        nv = {**t["voice"], **OVERRIDES}
        patch(f"/assistant/{t['id']}", {"voice": nv})
        chk = get(f"/assistant/{t['id']}")["voice"]
        ok = (chk.get("style") == 0.35 and chk.get("stability") == 0.5 and chk.get("useSpeakerBoost") is False)
        all_ok = all_ok and ok
        print(f"  {'✓ OK ' if ok else '✗ FAIL'} {t.get('name')}: style={chk.get('style')} stability={chk.get('stability')} useSpeakerBoost={chk.get('useSpeakerBoost')}")

    print(f"\n{'✓ all confirmed' if all_ok else '✗ at least one did not confirm — check above'}")
    print(f"revert with:  python scripts/_set_voice_dynamics.py --revert {bpath}")


if __name__ == "__main__":
    main()
