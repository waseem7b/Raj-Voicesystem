"""One-off: set voice.speed on the 3 live Inbound-Texts stage assistants.

Surgical voice-only PATCH (full voice block preserved, only `speed` changes).
Does NOT touch prompts/tools — intentionally avoids push_squad.py so uncommitted
local prompt edits are not deployed. Usage: python scripts/_set_voice_speed.py <speed>
"""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv("config/.env")

NEW_SPEED = float(sys.argv[1]) if len(sys.argv) > 1 else 0.92

k = os.environ["VAPI_API_KEY"]
h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
ids = {
    "Stage1": "46e47e95-674a-4624-9029-69bb39c4daf0",
    "Stage2": "7b2666e0-154d-4c85-82f0-07b4c51ad264",
    "Stage3": "7600c2b6-d669-4f2e-8387-e371b0c193d4",
}

for name, i in ids.items():
    cur = httpx.get(f"https://api.vapi.ai/assistant/{i}", headers=h, timeout=20).json()
    voice = cur["voice"]
    old = voice.get("speed")
    voice["speed"] = NEW_SPEED
    r = httpx.patch(
        f"https://api.vapi.ai/assistant/{i}", headers=h, json={"voice": voice}, timeout=30
    )
    if r.status_code >= 400:
        print(f"{name}: PATCH FAILED {r.status_code}: {r.text[:300]}")
        continue
    back = httpx.get(f"https://api.vapi.ai/assistant/{i}", headers=h, timeout=20).json()["voice"]
    print(
        f"{name}: speed {old} -> {back.get('speed')} | "
        f"ssml={back.get('enableSsmlParsing')} | voiceId={back.get('voiceId')} | "
        f"model={back.get('model')} | style={back.get('style')} | "
        f"stability={back.get('stability')}"
    )
