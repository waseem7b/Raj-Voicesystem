#!/usr/bin/env python3
"""Build the 3-stage Vapi Squad for Voicsito (inbound texts call type).

Creates or updates:
  - Voicsito - Stage 1 - Inbound Texts  (INTEREST: opener -> reveal)
  - Voicsito - Stage 2 - Qualify         (QUALIFY: discovery + FAQs) [shared across call types]
  - Voicsito - Stage 3 - Book            (BOOK: close + book the slot) [shared across call types]
  - Voicsito - Squad - Inbound Texts     ([Stage 1, Stage 2, Stage 3])

Idempotent — re-running updates existing resources by name; no duplicates.

Each stage assistant inherits voice/transcriber/speaking-plans from the
current monolithic assistant so behavior baseline matches.

Stage 1 has a `handoff` tool inline -> Stage 2.
Stage 2 has a `handoff` tool inline -> Stage 3.
Stage 3 has no handoff (terminal).

Usage:
    .venv-extract/bin/python scripts/push_squad.py [--dry-run]

Env: requires VAPI_API_KEY in config/.env.
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
sys.path.insert(0, str(ROOT))

from src.agent.prompts import get_stage_prompt  # noqa: E402

VAPI_KEY = os.environ.get("VAPI_API_KEY")
if not VAPI_KEY:
    sys.exit("✗ VAPI_API_KEY missing from config/.env")

VAPI_BASE = "https://api.vapi.ai"
HEADERS = {"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"}

# Source-of-truth assistant we clone voice/transcriber/plans from
MONOLITHIC_ASSISTANT_ID = "261bf1a0-3527-4fcb-956c-b6e227d0bae1"

# Existing shared tool IDs (look these up if they ever change)
TOOL_IDS = {
    "update_prospect_info": "278ff15a-4c7f-47cf-854f-86c132290667",
    "get_available_slots": "4936203d-b9d6-4dc9-a46a-609fded29abb",
    "mark_for_transfer": "dcdba267-0cb0-4af2-8010-9b3ffecdd976",
    "book_video_call": "cdc4465d-e827-4453-8e29-819d6f6028f3",
}

STAGE_NAMES = {
    "stage1": "Voicsito - Stage 1 - Inbound Texts",
    "stage2": "Voicsito - Stage 2 - Qualify",
    "stage3": "Voicsito - Stage 3 - Book",
}
SQUAD_NAME = "Voicsito - Squad - Inbound Texts"


# ─────────────────────────── Idle / silence handling ───────────────────────────
# Caller-silence handling via assistant.hooks — the correct Vapi API. The legacy
# messagePlan.idleMessages can't cancel a queued nudge, so it talks over a caller
# who starts speaking right after the timer (verified broken 2026-05-07 via call
# 019e04c8-006e-733f-b979-b832387e61f5). triggerResetMode="onUserSpeech" resets the
# silence timer every time the caller speaks. Set on the shared base so every stage
# carries it — a caller can go quiet in any stage and still get checked on.
#   10s silence → one check-in (3 approved variants, one chosen at random), max 1/call
#   36s silence → graceful sign-off + endCall
#   silenceTimeoutSeconds=60 (in the base config below) is the hard safety net.
# Spoken lines are reused verbatim from the monolith's working config (setup-vapi.py).
IDLE_HOOKS = [
    {
        "on": "customer.speech.timeout",
        "options": {
            "timeoutSeconds": 10,
            "triggerMaxCount": 1,
            "triggerResetMode": "onUserSpeech",
        },
        "do": [
            {
                "type": "say",
                "exact": [
                    "Hey, you still there brother?",
                    "Hello, you still with me?",
                    "Hey, did I lose you for a sec?",
                ],
            }
        ],
        "name": "idle_nudge_10s",
    },
    {
        "on": "customer.speech.timeout",
        "options": {
            "timeoutSeconds": 36,
            "triggerMaxCount": 1,
            "triggerResetMode": "onUserSpeech",
        },
        "do": [
            {
                "type": "say",
                "exact": "Alright brother, sounds like I caught you at a bad time. I'll catch up with you another time. Take care.",
            },
            {"type": "tool", "tool": {"type": "endCall"}},
        ],
        "name": "graceful_hangup_after_silence",
    },
]


# ─────────────────────────── HTTP helpers ───────────────────────────


def vapi_get(path: str) -> dict | list:
    r = httpx.get(f"{VAPI_BASE}{path}", headers=HEADERS, timeout=20.0)
    r.raise_for_status()
    return r.json()


def vapi_post(path: str, body: dict) -> dict:
    r = httpx.post(f"{VAPI_BASE}{path}", headers=HEADERS, json=body, timeout=30.0)
    if r.status_code >= 400:
        sys.exit(f"✗ POST {path} failed {r.status_code}: {r.text[:1000]}")
    return r.json()


def vapi_patch(path: str, body: dict) -> dict:
    r = httpx.patch(f"{VAPI_BASE}{path}", headers=HEADERS, json=body, timeout=30.0)
    if r.status_code >= 400:
        sys.exit(f"✗ PATCH {path} failed {r.status_code}: {r.text[:1000]}")
    return r.json()


# ─────────────────────────── Resource lookups ───────────────────────────


def find_assistant_by_name(name: str) -> dict | None:
    for a in vapi_get("/assistant"):
        if a.get("name") == name:
            return a
    return None


def find_squad_by_name(name: str) -> dict | None:
    for s in vapi_get("/squad"):
        if s.get("name") == name:
            return s
    return None


# ───────────────────────── Stage assistant builders ─────────────────────────


def base_config_from_monolithic() -> dict:
    """Pull voice/transcriber/speaking-plans/etc from the live monolithic assistant.

    Stage assistants inherit these so behavior baseline matches.
    Returns a dict with only the inheritable fields.
    """
    mono = vapi_get(f"/assistant/{MONOLITHIC_ASSISTANT_ID}")
    stop_plan = mono.get("stopSpeakingPlan") or {}
    return {
        # speed 0.90 (down from the monolith's 1.0) for a slower, more natural
        # question cadence — tuned via offline A/B + live call (2026-06-02),
        # then 0.92 -> 0.88 -> 0.90 on 2026-06-08 (pairs with break tags for pacing).
        "voice": {**mono["voice"], "enableSsmlParsing": True, "speed": 0.90},
        "transcriber": mono["transcriber"],
        "startSpeakingPlan": mono.get("startSpeakingPlan"),
        # Require 3 words before an interruption counts (up from 2).
        # backchannelingEnabled handles the common case ("yeah"/"mhm"/"okay")
        # so the agent never stops for those at all.
        "stopSpeakingPlan": {**stop_plan, "numWords": 3, "backoffSeconds": 1.5},
        "backchannelingEnabled": True,
        "voicemailDetection": mono.get("voicemailDetection"),
        "backgroundSound": mono.get("backgroundSound"),
        # Re-attaches the idle check-in + graceful-hangup hooks that were lost in
        # the monolith → Squad split (see IDLE_HOOKS above).
        "hooks": IDLE_HOOKS,
        "silenceTimeoutSeconds": mono.get("silenceTimeoutSeconds", 60),
        "maxDurationSeconds": mono.get("maxDurationSeconds", 1800),
        "endCallMessage": mono.get("endCallMessage", "Alright brother, take care."),
        "voicemailMessage": mono.get("voicemailMessage"),
    }


def endcall_tool() -> dict:
    """Vapi's built-in endCall tool with a rejection plan.

    The rejectionPlan blocks Vapi from firing endCall unless the prospect's most
    recent turn contains a goodbye / transactional-close / disqualifier signal.
    Prevents the model from hanging up after 2 turns on borderline-hostile
    prospects (e.g. someone calls Jon a bot but is still on the line).

    Happy path still works: after booking, prospects typically say "yeah",
    "thanks", "sounds good", "that's it" — all whitelisted.
    Hard disqualifier path still works: "retiring", "not interested",
    "no business", "wrong number" — all whitelisted.
    """
    return {
        "type": "endCall",
        "messages": [
            {"type": "request-start", "content": ""},
        ],
        "rejectionPlan": {
            "conditions": [
                {
                    "type": "regex",
                    "regex": (
                        r"(?i)\b("
                        r"bye|goodbye|take care|talk soon|talk later|see ya|"
                        r"have a good (one|day|night|rest)|sounds good|sounds great|perfect|"
                        r"thanks|appreciate|cheers|cool|alright|got it|yep|all good|"
                        r"stop calling|take me off|don'?t call|remove me|not interested|"
                        r"retiring|no business|out of business|wrong number|hang up|"
                        r"that'?s all|we'?re done|that'?s it"
                        r")\b"
                    ),
                    "target": {"position": -1, "role": "user"},
                    "negate": True,
                }
            ]
        },
    }


def handoff_tool_to(destination_name: str, description: str, extract_vars: dict) -> dict:
    """Build an inline `handoff` tool that transfers to another squad member by name."""
    return {
        "type": "handoff",
        "destinations": [
            {
                "type": "assistant",
                "assistantName": destination_name,
                "description": description,
                "contextEngineeringPlan": {"type": "userAndAssistantMessages"},
                "variableExtractionPlan": {
                    "schema": {
                        "type": "object",
                        "properties": extract_vars,
                        "required": list(extract_vars.keys()),
                    }
                },
            }
        ],
        # Silence the filler — we want the transition to feel seamless
        "messages": [
            {"type": "request-start", "content": ""},
        ],
    }


def build_stage1_assistant(base: dict) -> dict:
    """Stage 1 — INTEREST: opener -> reveal -> handoff to Stage 2."""
    handoff = handoff_tool_to(
        destination_name=STAGE_NAMES["stage2"],
        description=(
            "Fire this tool AFTER the prospect verbally agrees to the marketing "
            "conversation at the end of the reveal anchor. Fire it earlier ONLY "
            "if the prospect explicitly says yes to having you ask discovery "
            "questions about their business. Do not fire on a hard disqualifier "
            "(no business, retiring out, outside service area) — end the call instead."
        ),
        extract_vars={
            "first_name": {"type": "string", "description": "Prospect's first name."},
            "biz_type": {"type": "string", "description": "Prospect's trade / business type."},
            "reason": {"type": "string", "description": "Reason the prospect gave for the call."},
            "raised_objections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Any objection labels you already handled in Stage 1 (e.g. 'not_interested', 'has_marketing_guy', 'text_promise_complaint').",
            },
        },
    )

    return {
        "name": STAGE_NAMES["stage1"],
        "firstMessage": "https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3",
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "temperature": 0.6,
            "maxTokens": 350,
            "promptCacheRetention": "24h",
            "messages": [
                {
                    "role": "system",
                    "content": get_stage_prompt("inbound_texts", "stage1"),
                }
            ],
            "toolIds": [TOOL_IDS["update_prospect_info"]],
            "tools": [handoff, endcall_tool()],
        },
        **base,
    }


def build_stage2_assistant(base: dict) -> dict:
    """Stage 2 — QUALIFY: discovery + FAQs -> handoff to Stage 3."""
    handoff = handoff_tool_to(
        destination_name=STAGE_NAMES["stage3"],
        description=(
            "Fire this tool AFTER the last discovery question has been answered, "
            "OR when the prospect explicitly asks to book / schedule the call right now. "
            "Do not fire earlier in discovery. Do not fire on a hard disqualifier "
            "(no business, retiring out, outside service area) — end the call instead."
        ),
        extract_vars={
            "first_name": {"type": "string"},
            "biz_type": {"type": "string"},
            "business": {"type": "string", "description": "The prospect's company name."},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "crew_size": {"type": "string", "description": "How many people on their crew."},
            "lead_source": {"type": "string", "description": "Where their leads come from (referrals, Google, etc.)."},
            "job_volume": {"type": "string", "description": "Roughly how many jobs per month."},
        },
    )

    return {
        "name": STAGE_NAMES["stage2"],
        # No firstMessage — handoff drops in mid-call; the model speaks first turn naturally
        "firstMessageMode": "assistant-speaks-first-with-model-generated-message",
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "temperature": 0.6,
            "maxTokens": 350,
            "promptCacheRetention": "24h",
            "messages": [
                {
                    "role": "system",
                    "content": get_stage_prompt("inbound_texts", "stage2"),
                }
            ],
            "toolIds": [TOOL_IDS["update_prospect_info"]],
            "tools": [handoff, endcall_tool()],
        },
        **base,
    }


def build_stage3_assistant(base: dict) -> dict:
    """Stage 3 — BOOK: close transition -> book the slot -> sign off. Terminal."""
    # Stage 3 is the only stage that captures an email out loud. Callers often
    # say it slowly with pauses ("ryan ... at gmail ... dot com"); with the
    # default 0.4s no-punctuation endpointing, Jon barges in mid-email. Vapi's
    # customEndpointingRules raise the silence timeout to 3s on the turn that
    # follows Jon's email ask / repeat-back, so the caller can finish. Placed
    # after **base so it overrides the startSpeakingPlan inherited from mono.
    stage3_start_plan = {
        **(base.get("startSpeakingPlan") or {}),
        "customEndpointingRules": [
            {"type": "assistant", "regex": "(email|repeat that again)", "timeoutSeconds": 3.0},
        ],
    }
    return {
        "name": STAGE_NAMES["stage3"],
        "firstMessageMode": "assistant-speaks-first-with-model-generated-message",
        "model": {
            "provider": "openai",
            "model": "gpt-4.1",
            "temperature": 0.6,
            "maxTokens": 350,
            "promptCacheRetention": "24h",
            "messages": [
                {
                    "role": "system",
                    "content": get_stage_prompt("inbound_texts", "stage3"),
                }
            ],
            "toolIds": [
                TOOL_IDS["update_prospect_info"],
                TOOL_IDS["get_available_slots"],
                TOOL_IDS["mark_for_transfer"],
                TOOL_IDS["book_video_call"],
            ],
            "tools": [endcall_tool()],
        },
        **base,
        "startSpeakingPlan": stage3_start_plan,
    }


# ─────────────────────────── Upsert logic ───────────────────────────


def upsert_assistant(config: dict, dry_run: bool) -> str:
    name = config["name"]
    existing = find_assistant_by_name(name)
    if existing:
        aid = existing["id"]
        if dry_run:
            print(f"  [dry-run] would PATCH '{name}' ({aid})")
            return aid
        # PATCH only the fields we own; Vapi rejects some fields on PATCH
        body = {k: v for k, v in config.items() if k != "name"}
        vapi_patch(f"/assistant/{aid}", body)
        print(f"  ✓ patched '{name}' ({aid})")
        return aid
    if dry_run:
        print(f"  [dry-run] would CREATE '{name}'")
        return "dry-run-id"
    new = vapi_post("/assistant", config)
    print(f"  ✓ created '{name}' ({new['id']})")
    return new["id"]


def upsert_squad(member_ids: list[str], dry_run: bool) -> str:
    body = {
        "name": SQUAD_NAME,
        "members": [{"assistantId": mid} for mid in member_ids],
    }
    existing = find_squad_by_name(SQUAD_NAME)
    if existing:
        sid = existing["id"]
        if dry_run:
            print(f"  [dry-run] would PATCH squad '{SQUAD_NAME}' ({sid})")
            return sid
        # Vapi squad PATCH accepts members
        vapi_patch(f"/squad/{sid}", {"members": body["members"]})
        print(f"  ✓ patched squad '{SQUAD_NAME}' ({sid})")
        return sid
    if dry_run:
        print(f"  [dry-run] would CREATE squad '{SQUAD_NAME}'")
        return "dry-run-id"
    new = vapi_post("/squad", body)
    print(f"  ✓ created squad '{SQUAD_NAME}' ({new['id']})")
    return new["id"]


# ─────────────────────────── Main ───────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="show what would change without sending")
    args = p.parse_args()

    print("[1/5] Pulling base config from monolithic assistant...")
    base = base_config_from_monolithic()
    print(f"  ✓ voice={base['voice']['voiceId']}, speed={base['voice']['speed']}, useSpeakerBoost={base['voice']['useSpeakerBoost']}")

    print(f"\n[2/5] Stage 3 (BOOK)...")
    s3 = build_stage3_assistant(base)
    s3_id = upsert_assistant(s3, args.dry_run)

    print(f"\n[3/5] Stage 2 (QUALIFY)...")
    s2 = build_stage2_assistant(base)
    s2_id = upsert_assistant(s2, args.dry_run)

    print(f"\n[4/5] Stage 1 (INTEREST)...")
    s1 = build_stage1_assistant(base)
    s1_id = upsert_assistant(s1, args.dry_run)

    print(f"\n[5/5] Squad...")
    squad_id = upsert_squad([s1_id, s2_id, s3_id], args.dry_run)

    print("\n" + "=" * 60)
    print("Squad assembly:")
    print(f"  Stage 1 (INTEREST)  →  {s1_id}")
    print(f"  Stage 2 (QUALIFY)   →  {s2_id}")
    print(f"  Stage 3 (BOOK)      →  {s3_id}")
    print(f"  Squad               →  {squad_id}")
    print()
    if not args.dry_run:
        print("To cut the phone number over to this squad:")
        print(f"  curl -X PATCH https://api.vapi.ai/phone-number/cb93e61d-d222-42c9-8fb2-74500dc13234 \\")
        print(f"    -H 'Authorization: Bearer $VAPI_API_KEY' \\")
        print(f"    -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"squadId\":\"{squad_id}\",\"assistantId\":null}}'")
        print()
        print("To roll back to the monolithic assistant:")
        print(f"  curl -X PATCH https://api.vapi.ai/phone-number/cb93e61d-d222-42c9-8fb2-74500dc13234 \\")
        print(f"    -H 'Authorization: Bearer $VAPI_API_KEY' \\")
        print(f"    -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"squadId\":null,\"assistantId\":null}}'  # webhook still routes via assistant-request")

        # Post-push health check: a push attaches tools by id but never sets
        # their server.url/secret, so a tool that got stripped (dashboard edit,
        # etc.) stays broken silently. Verify + auto-repair standalone tools;
        # fail loudly if anything is left unwired.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from verify_tools import post_push_check  # noqa: E402
        if not post_push_check(scope="inbound", fix=True):
            sys.exit("✗ Post-push tool check failed — fix the above and re-run.")


if __name__ == "__main__":
    main()
