#!/usr/bin/env python3
"""One-shot Vapi setup: creates the 3 webhook tools + Type 2 assistant for Voicsito.

Usage:
    .venv-extract/bin/python scripts/setup-vapi.py --webhook-base https://your-tls-host.example.com [--phone-id <id>]

Required: a public HTTPS base URL for your webhook server. The script will:
  1. Create the 3 webhook tools (update_prospect_info, get_available_slots, book_video_call)
     pointing at <webhook-base>/vapi/tool
  2. Create the Type 2 (Inbound Texts) assistant with the prompt from
     src/agent/prompts/inbound_texts.py and ElevenLabs voice LJ3VmFT7KIfEw9jSLImM (Jon PVC clone)
  3. (Optional) Attach the assistant to a Vapi phone number via --phone-id

Re-run safe: if tools or an assistant with the same name already exist, the script
re-uses them and PATCHes config rather than creating duplicates. Pass --force to
delete-and-recreate instead.

Env: requires VAPI_API_KEY in config/.env. ANTHROPIC_API_KEY, DEEPGRAM_API_KEY,
ELEVENLABS_API_KEY are configured in the Vapi dashboard separately (under Org
Settings → Provider Keys).
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

ASSISTANT_NAME = "Voicsito - Type 2 Inbound (Jon)"
VOICE_ID = "LJ3VmFT7KIfEw9jSLImM"


def load_inbound_texts_prompt() -> str:
    sys.path.insert(0, str(ROOT / "src" / "agent"))
    from prompts import get_prompt  # type: ignore
    return get_prompt("inbound_texts")


def list_tools() -> list[dict]:
    r = httpx.get(f"{VAPI_BASE}/tool", headers=HEADERS, timeout=15.0)
    r.raise_for_status()
    return r.json()


def list_assistants() -> list[dict]:
    r = httpx.get(f"{VAPI_BASE}/assistant", headers=HEADERS, timeout=15.0)
    r.raise_for_status()
    return r.json()


def post_json(path: str, body: dict) -> dict:
    r = httpx.post(f"{VAPI_BASE}{path}", headers=HEADERS, json=body, timeout=30.0)
    if r.status_code >= 400:
        sys.exit(f"✗ POST {path} → {r.status_code}: {r.text[:600]}")
    return r.json()


def patch_json(path: str, body: dict) -> dict:
    r = httpx.patch(f"{VAPI_BASE}{path}", headers=HEADERS, json=body, timeout=30.0)
    if r.status_code >= 400:
        sys.exit(f"✗ PATCH {path} → {r.status_code}: {r.text[:600]}")
    return r.json()


def delete_resource(path: str) -> None:
    r = httpx.delete(f"{VAPI_BASE}{path}", headers=HEADERS, timeout=15.0)
    if r.status_code >= 400 and r.status_code != 404:
        sys.exit(f"✗ DELETE {path} → {r.status_code}: {r.text[:500]}")


def make_tool_payload(name: str, description: str, params_schema: dict, webhook_url: str, timeout_seconds: int = 12) -> dict:
    return {
        "type": "function",
        "async": False,
        "function": {
            "name": name,
            "description": description,
            "parameters": params_schema,
        },
        "server": {
            "url": webhook_url,
            "timeoutSeconds": timeout_seconds,
        },
        "messages": [
            {"type": "request-response-delayed", "content": "Just one sec, brother — pulling that up.", "timingMilliseconds": 4000}
        ],
    }


# Each tuple: (name, description, params_schema, server_timeout_seconds)
TOOL_DEFINITIONS = [
    (
        "get_available_slots",
        "Return the next available video-call slots in the rep's calendar. Call when prospect mentions a day/time preference.",
        {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7,
                                "description": "Look-ahead window in days."}
            },
            "required": [],
        },
        25,
    ),
    (
        "mark_for_transfer",
        "Tag the prospect for transfer to the main CRM. Call this the MOMENT the prospect agrees to book a video call — BEFORE asking for their email. The transfer must run while you collect/confirm the email so the main-CRM contact is ready by booking time.",
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
        12,
    ),
    (
        "book_video_call",
        "Book the chosen ISO-8601 slot on the rep's calendar and email the prospect a video-call invite. Call mark_for_transfer FIRST, then collect email, then call this.",
        {
            "type": "object",
            "properties": {
                "slot_iso":             {"type": "string", "description": "ISO-8601 datetime with timezone, e.g. 2026-05-08T14:30:00-07:00. Must come from a get_available_slots [iso:...] tag."},
                "email":                {"type": "string", "description": "Prospect's email address."},
                "prospect_first_name":  {"type": "string", "description": "Prospect's first name."},
                "business_name":        {"type": "string", "description": "Prospect's business / company name."},
            },
            "required": ["slot_iso", "email", "prospect_first_name"],
        },
        60,
    ),
]


def ensure_tool(name: str, description: str, params: dict, webhook_url: str, existing: list[dict], force: bool, timeout_seconds: int = 12) -> str:
    """Returns the tool id (creating or updating as needed)."""
    found = next((t for t in existing if (t.get("function") or {}).get("name") == name), None)
    payload = make_tool_payload(name, description, params, webhook_url, timeout_seconds)
    if found:
        if force:
            print(f"  - DELETE existing {name} ({found['id']})")
            delete_resource(f"/tool/{found['id']}")
        else:
            # PATCH rejects immutable fields like `type` and `async` — strip them
            patch_payload = {k: v for k, v in payload.items() if k not in ("type", "async")}
            print(f"  - PATCH {name} ({found['id']})")
            patch_json(f"/tool/{found['id']}", patch_payload)
            return found["id"]
    created = post_json("/tool", payload)
    print(f"  - CREATE {name} → {created['id']}")
    return created["id"]


def build_assistant_body(prompt: str, tool_ids: list[str], background_sound: str | None) -> dict:
    body = {
        "name": ASSISTANT_NAME,
        "firstMessage": "Hey, Jon here — who is this calling?",
        "firstMessageMode": "assistant-speaks-first",

        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "temperature": 0.6,
            "maxTokens": 350,
            "messages": [{"role": "system", "content": prompt}],
            "toolIds": tool_ids,
            "tools": [{"type": "endCall"}],
        },

        "transcriber": {
            "provider": "deepgram",
            "model": "nova-3",
            "language": "en",
            "smartFormat": True,
            "endpointing": 150,
        },

        "voice": {
            "provider": "11labs",
            "voiceId": VOICE_ID,
            "model": "eleven_multilingual_v2",
            "stability": 0.50,
            "similarityBoost": 0.90,
            "style": 0.0,
            "useSpeakerBoost": True,
            "speed": 1.0,
            "optimizeStreamingLatency": 2,
            "enableSsmlParsing": False,
            "autoMode": False,
        },

        "voicemailDetection": {
            "provider": "google",
            "type": "audio",
        },
        "voicemailMessage": "Hey, this is Jon. Sorry I missed you — I'll try back later. Talk soon.",

        "recordingEnabled": True,

        "startSpeakingPlan": {
            "waitSeconds": 0.2,
            "smartEndpointingEnabled": False,
            "transcriptionEndpointingPlan": {
                "onPunctuationSeconds": 0.1,
                "onNoPunctuationSeconds": 0.4,
                "onNumberSeconds": 0.5,
            },
        },
        "stopSpeakingPlan": {
            "numWords": 2,
            "voiceSeconds": 0.2,
            "backoffSeconds": 1.0,
        },

        # Silence handling via assistant.hooks (the CORRECT Vapi API per
        # docs.vapi.ai/assistants/idle-messages). The legacy messagePlan.idleMessages
        # we used initially has a bug: it can't cancel a queued nudge once the timer
        # fires, so user speech that starts within ~300ms of the timeout still gets
        # talked over (verified 2026-05-07 via call 019e04c8-006e-733f-b979-b832387e61f5).
        # The hooks API supports triggerResetMode="onUserSpeech" which is what we want.
        #
        # Ladder:
        #   12s of silence → "Hey, you still there brother?" (3 randomized variants, max 2 fires)
        #   36s of silence → graceful goodbye + endCall
        # silenceTimeoutSeconds=60 is the safety net if hooks fail.
        "hooks": [
            {
                "on": "customer.speech.timeout",
                "options": {
                    "timeoutSeconds": 12,
                    "triggerMaxCount": 2,
                    "triggerResetMode": "onUserSpeech",
                },
                "do": [{
                    "type": "say",
                    "exact": [
                        "Hey, you still there brother?",
                        "Hello, you still with me?",
                        "Hey, did I lose you for a sec?",
                    ],
                }],
                "name": "idle_nudge_12s",
            },
            {
                "on": "customer.speech.timeout",
                "options": {
                    "timeoutSeconds": 36,
                    "triggerMaxCount": 1,
                    "triggerResetMode": "onUserSpeech",
                },
                "do": [
                    {"type": "say", "exact": "Alright brother, sounds like I caught you at a bad time. I'll catch up with you another time. Take care."},
                    {"type": "tool", "tool": {"type": "endCall"}},
                ],
                "name": "graceful_hangup_after_silence",
            },
        ],

        "silenceTimeoutSeconds": 60,
        "maxDurationSeconds": 1800,
        "endCallMessage": "Alright brother, take care.",
    }
    if background_sound:
        body["backgroundSound"] = background_sound
    else:
        # In-truck ambience hosted on the shared infra. Audio served from
        # /opt/webhooks/voicsito/static/ via nginx (see deploy script).
        # v2 processing (2026-05-07): 5min slice, mono 44.1kHz 96kbps, with a
        # band-pass filter (highpass 300Hz, lowpass 3400Hz) to match phone
        # codec passband — makes the car noise sound like it's coming through
        # Jon's phone mic, not a separate hi-fi layer. Volume at 28%.
        body["backgroundSound"] = "https://webhooks.c7a.io/voicsito/static/car-bg-v2.mp3"
    return body


def attach_phone(phone_id: str, assistant_id: str) -> None:
    print(f"\n→ Attaching assistant to phone {phone_id}...")
    patch_json(f"/phone-number/{phone_id}", {"assistantId": assistant_id})
    print(f"  ✓ phone {phone_id} → assistant {assistant_id}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--webhook-base", required=True, help="HTTPS base URL of webhook server, e.g. https://abc.trycloudflare.com")
    p.add_argument("--phone-id", help="Vapi phone number id to attach the new assistant to")
    p.add_argument("--background-sound", default="office", help='"office" | "off" | https URL to mp3/wav (default: office)')
    p.add_argument("--force", action="store_true", help="delete and recreate tools/assistant instead of patching")
    p.add_argument("--dry-run", action="store_true", help="print the payloads without sending")
    args = p.parse_args()

    webhook_base = args.webhook_base.rstrip("/")
    webhook_url = f"{webhook_base}/vapi/tool"
    print(f"Webhook tool URL: {webhook_url}")

    print("\n[1/3] Loading prompt...")
    prompt = load_inbound_texts_prompt()
    print(f"  - {len(prompt)} chars from src/agent/prompts/inbound_texts.py")

    if args.dry_run:
        print("\n--- DRY RUN: tool payloads ---")
        for name, desc, params, timeout in TOOL_DEFINITIONS:
            print(json.dumps(make_tool_payload(name, desc, params, webhook_url, timeout), indent=2))
        print("\n--- DRY RUN: assistant payload ---")
        print(json.dumps(build_assistant_body(prompt, ["{tool_id}"]*len(TOOL_DEFINITIONS), args.background_sound), indent=2))
        return

    print("\n[2/3] Creating/updating tools...")
    existing_tools = list_tools()
    tool_ids = []
    for name, desc, params, timeout in TOOL_DEFINITIONS:
        tool_ids.append(ensure_tool(name, desc, params, webhook_url, existing_tools, args.force, timeout))

    print("\n[3/3] Creating/updating assistant...")
    body = build_assistant_body(prompt, tool_ids, args.background_sound)
    existing_assistants = list_assistants()
    found = next((a for a in existing_assistants if a.get("name") == ASSISTANT_NAME), None)
    if found:
        if args.force:
            print(f"  - DELETE existing assistant ({found['id']})")
            delete_resource(f"/assistant/{found['id']}")
            created = post_json("/assistant", body)
            print(f"  - CREATE assistant → {created['id']}")
            assistant_id = created["id"]
        else:
            print(f"  - PATCH assistant ({found['id']})")
            patch_json(f"/assistant/{found['id']}", body)
            assistant_id = found["id"]
    else:
        created = post_json("/assistant", body)
        print(f"  - CREATE assistant → {created['id']}")
        assistant_id = created["id"]

    if args.phone_id:
        attach_phone(args.phone_id, assistant_id)

    print("\n" + "=" * 60)
    print("  ✓ Vapi setup complete")
    print(f"    assistant_id: {assistant_id}")
    print(f"    tool_ids:     {', '.join(tool_ids)}")
    if args.phone_id:
        print(f"    phone_id:     {args.phone_id} (attached)")
    else:
        print("    Next: attach a phone number in the Vapi dashboard, or pass --phone-id <id>")
    print("=" * 60)


if __name__ == "__main__":
    main()
