#!/usr/bin/env python3
"""Build the outbound Ashley assistants — day-before confirmation + day-of reminder.

Creates or updates BY NAME (idempotent, no duplicates):
  - Voicsito - Ashley - Confirmation   (day-before: confirm -> locked-in tag, or hand off)
  - Voicsito - Ashley - Reminder       (day-of: logistics, or hand off)
  - Voicsito - Ashley - Rebooking      (call-back: find + lock in the new time)

No squad, no router. GHL POSTs to /ghl/outbound-call, which picks the assistant
server-side and calls Vapi /call — so the spoken bot-ID handshake the old Stage 0
router existed to parse is gone, and with it the "10 fragile digits over ASR"
risk the 2026-06-04 spec flagged as the flow's central reliability problem.

Rescheduling is its OWN assistant (owner ruling 2026-09-04, reversing 2026-08-19).
Confirmation and Reminder carry no calendar tools: a prospect who can't make it
is tagged `needs-reschedule` in-call (mark_needs_reschedule) and the GHL workflow
fires /ghl/outbound-call with call_type=rebooking, which dials the Rebooking
assistant as a separate call. Still no in-call handoff anywhere.

ISOLATION (verified at startup): this script ONLY operates on the names
above. It never reads, PATCHes, or deletes the inbound squad, the inbound
assistants, the inbound phone number, or any shared tool. A startup assertion
fails if any target name collides with a known inbound resource. All tools are
INLINE on the assistants — it creates zero shared tool resources.

The superseded Jon-era resources (Stage 0/1/2 Confirm + the Outbound Confirmation
squad) are deliberately left alone rather than deleted — they are the rollback
path alongside docs/reference-points/2026-08-19-pre-ashley-confirm/. Delete them
by hand once the Ashley flow is proven on real calls.

Usage:
    python scripts/push_confirm_squad.py [--dry-run]
Env: VAPI_API_KEY in config/.env.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")
sys.path.insert(0, str(ROOT))

from src.agent.prompts import get_stage_context, get_stage_prompt  # noqa: E402

VAPI_KEY = os.environ.get("VAPI_API_KEY")
if not VAPI_KEY:
    sys.exit("✗ VAPI_API_KEY missing from config/.env")

VAPI_BASE = "https://api.vapi.ai"
HEADERS = {"Authorization": f"Bearer {VAPI_KEY}", "Content-Type": "application/json"}

# Clone transcriber/speaking-plans from the live monolithic assistant so the
# baseline matches. We only READ this assistant; we never modify it.
MONOLITHIC_ASSISTANT_ID = "261bf1a0-3527-4fcb-956c-b6e227d0bae1"

# Ashley's ElevenLabs voice (owner-supplied 2026-08-19). Overrides the
# monolithic assistant's voice, which is Jon's.
ASHLEY_VOICE_ID = "x8GG27Jf8JBB9yLaSos4"

# TTS model, flipped one line at a time so an A/B shows up as a clean diff.
#   eleven_turbo_v2_5      — balanced. What she ran on all week.
#   eleven_multilingual_v2 — ElevenLabs' most emotionally rich; on their
#                            supported list for <break time="..." />.
#   eleven_flash_v2_5      — ~75ms but LOWER emotional depth than turbo.
# A/B in progress (2026-08-29): step 1 isolates similarityBoost 1.00 -> 0.75
# on turbo, step 2 flips this to multilingual_v2 with nothing else moving.
ASHLEY_TTS_MODEL = "eleven_turbo_v2_5"

# Outbound webhook endpoint — separate from the inbound /vapi/tool path.
OUTBOUND_TOOL_URL = "https://webhooks.c7a.io/voicsito/vapi/outbound/tool"
# End-of-call reports land here and get classified into a GHL contact tag, which
# is what the show-up workflow's "if no answer" branches read. Without this the
# reports go nowhere and GHL cannot tell an answered call from a missed one.
OUTBOUND_EVENTS_URL = "https://webhooks.c7a.io/voicsito/vapi/outbound/events"

# call_type (as sent by GHL to /ghl/outbound-call) -> assistant name + prompt key
FLOWS = {
    "confirmation": {
        "name": "Voicsito - Ashley - Confirmation",
        "prompt": ("demo_confirmation", "stage1"),
        "env": "VAPI_ASSISTANT_ID_CONFIRMATION",
        "tools": ["mark_not_interested", "mark_locked_in", "mark_needs_reschedule"],
        # Vapi renders this server-side and hands ElevenLabs one complete string —
        # no model, no chunker — which is what stopped the greeting losing words.
        "first_message": "<break time=\"1.5s\" />{{firstName}}! good {{greeting}}! It's Ashley from Cloud 7, I've been texting you. How is everything?",
        # ";" never occurs in the script, so splits land on commas rather than on
        # the sentence ends where the break tags sit.
        "voice_overrides": {
            "chunkPlan": {
                "enabled": True, "minCharacters": 80, "punctuationBoundaries": [";"],
            },
            # Matched to Rebooking at the owner's call (2026-09-16): more
            # stability, less style, so the pieces sound alike across TTS cuts.
            "stability": 0.6,
            "style": 0.5,
        },
        # Endpointing off + no wait, matched to Rebooking. Its endpointing
        # latency went from ~500-1700ms to a flat ~101ms this way, the biggest
        # single latency win of the session; it falls back to the fixed
        # transcriptionEndpointingPlan timers. Watch for cut-ins on a
        # mid-thought pause — call 01a0a7fe clipped one on Rebooking.
        "smart_endpointing": False,
        "wait_seconds": 0.0,
        # The quiet room tone that replaced the office loop. Vapi has no volume
        # field for the built-in one, so a hosted file is the only way down.
        # NOTE: optimizeStreamingLatency is deliberately NOT matched to
        # Rebooking's 3 — that is the value that dropped two words from the
        # audio on 01a0ab4a and is queued for revert. These two stay at the
        # base 1.
        "background_sound": "https://webhooks.c7a.io/voicsito/static/room-tone-quiet.mp3",
    },
    "reminder": {
        "name": "Voicsito - Ashley - Reminder",
        "prompt": ("demo_reminder", "stage1"),
        "env": "VAPI_ASSISTANT_ID_REMINDER",
        # Owner 2026-08-21: acknowledging the reminder counts as confirming
        # again, tagged `locked-in-reminder` on top of `locked-in`.
        "tools": ["mark_not_interested", "mark_locked_in", "mark_needs_reschedule"],
        "first_message": "<break time=\"1.5s\" />Hey {{firstName}}, good {{greeting}}! It's Ashley. How've you been?",
        "voice_overrides": {
            "chunkPlan": {
                "enabled": True, "minCharacters": 80, "punctuationBoundaries": [";"],
            },
            # Matched to Rebooking at the owner's call (2026-09-16): more
            # stability, less style, so the pieces sound alike across TTS cuts.
            "stability": 0.6,
            "style": 0.5,
        },
        # Endpointing off + no wait, matched to Rebooking. Its endpointing
        # latency went from ~500-1700ms to a flat ~101ms this way, the biggest
        # single latency win of the session; it falls back to the fixed
        # transcriptionEndpointingPlan timers. Watch for cut-ins on a
        # mid-thought pause — call 01a0a7fe clipped one on Rebooking.
        "smart_endpointing": False,
        "wait_seconds": 0.0,
        # The quiet room tone that replaced the office loop. Vapi has no volume
        # field for the built-in one, so a hosted file is the only way down.
        # NOTE: optimizeStreamingLatency is deliberately NOT matched to
        # Rebooking's 3 — that is the value that dropped two words from the
        # audio on 01a0ab4a and is queued for revert. These two stay at the
        # base 1.
        "background_sound": "https://webhooks.c7a.io/voicsito/static/room-tone-quiet.mp3",
    },
    # The call-back that finds a new time. Fired by GHL when a contact carries
    # `needs-reschedule` after a confirmation or reminder call. The only
    # assistant with the calendar tools; no mark_locked_in because a rebooked
    # appointment re-enters the confirmation workflow, which writes locked-in.
    "rebooking": {
        "name": "Voicsito - Ashley - Rebooking",
        "prompt": ("demo_rebooking", "stage1"),
        "env": "VAPI_ASSISTANT_ID_REBOOKING",
        "tools": ["mark_not_interested", "get_available_slots", "reschedule_video_call"],
        # The owner's rebooking opener (updated script, 2026-09-14).
        "first_message": "<break time=\"1.5s\" />{{firstName}}, good {{greeting}}! This is Ashley from Cloud 7 Agency. How's the day?",
        "voice_overrides": {
            "chunkPlan": {
                "enabled": True, "minCharacters": 80, "punctuationBoundaries": [";"],
            },
            # Owner's A/B on the choppy why-calling line (2026-09-14). Vapi cuts
            # the TTS input at sentence ends and every piece is a fresh
            # generation (call 01a0a281). More stability and less style make the
            # pieces sound alike across the cuts. Rebooking only; base 0.45 / 0.75.
            "stability": 0.6,
            "style": 0.5,
            # Owner, 2026-09-15: voice start-up (0.4-1.3s a turn) is the biggest
            # slice left. ElevenLabs scale 0-4; base holds 1. Rebooking only;
            # revert is this one line. Earlier ruled OUT as a dropped-word cause
            # (call 01a03697 lost words at 0), so it is safe to raise.
            "optimizeStreamingLatency": 3,
        },
        # Owner's first latency test (2026-09-15). LiveKit smart endpointing
        # reads prosody before deciding the prospect is done; on call 01a0a653 it
        # waited 12s through "Good. How are you?" until "Hello?". Off, Vapi falls
        # back to the fixed transcriptionEndpointingPlan timers (0.1 / 0.4 / 0.5s)
        # inherited in base_config. Rebooking only; the other flows keep LiveKit.
        "smart_endpointing": False,
        # Owner, 2026-09-15: "turn down voice start by 0.2" — the pause between the
        # prospect stopping and Ashley starting, 0.2s -> 0. Rebooking only.
        "wait_seconds": 0.0,
        # Owner, 2026-09-15: "reduce the background office noise". Vapi's built-in
        # "office" loop has no volume field, so the only way to make it quieter
        # is a custom file: a synthesised quiet room tone (brown noise, 40-400 Hz,
        # -46 dBFS RMS, 16 dB under Jon's car loop), 20 min, served by nginx from
        # /opt/webhooks/voicsito/static like car-bg-v3.mp3. Source file is
        # audio/room-tone-quiet.mp3; upload by scp, deploy-webhooks.sh also ships
        # audio/*.mp3. Vapi fails silently on a 404 (no background at all), so
        # curl -I the URL before pushing. Rebooking only; the others keep "office".
        "background_sound": "https://webhooks.c7a.io/voicsito/static/room-tone-quiet.mp3",
        # Owner's model latency tests (2026-09-15): 4.1-mini reverted for ignoring
        # the day the prospect asked for; gpt-5.5 at minimal reasoning next, then
        # MODEL TRIALS, 2026-09-15/16 — SETTLED, do not retry without new evidence.
        # Rebooking runs gpt-4.1 (the flow.get default below). Every alternative
        # was measured on live calls and lost:
        #   gpt-4.1-mini      — ignored the day the prospect asked for (01a0a7a6).
        #   gpt-5.5 minimal   — model 722-1831ms vs 627-631 on 4.1 (01a0a7ef);
        #                       ~$0.117/turn, 17x 4.1.
        #   gpt-5.4-mini low  — booked with no time agreed and announced a slot
        #                       nobody picked (01a0a7fe); model 872ms.
        #   gpt-5.4 low       — held the rules, but turn 2.64s vs 1.58s on 4.1
        #                       and model 1047ms (2309ms on turn 1), ~6x the cost
        #                       (01a0ab4a, $0.1253). The fidelity it was meant to
        #                       buy came from the pre-written firstOffer/dayOffers
        #                       lines, not the model — they came out verbatim.
        # The lesson: pre-write the line, don't buy a bigger model to compose it.
        # A reasoning model reasons before every turn, which is the one thing a
        # voice call cannot spend. The per-flow "model"/"reasoning_effort" keys
        # below stay wired up for the next trial; they are just unset.
        # gpt-4.1-mini was tried here for one call (37a113f, 2026-09-15): it saved
        # ~150-300ms per turn and on call 01a0a7a6 treated a Friday the list
        # showed as a day it did not, then read the Thursday offer anyway. Back
        # on the shared gpt-4.1 at the owner's call. A "model" key here still
        # overrides per flow if a better small model is trialled on TEST first.
    },
    # Barebones twin of the reminder: identical voice config and identical
    # spoken words, but a 1.2k prompt instead of 24k and zero gates. Exists to
    # answer one question — is prompt size what breaks the pause markers?
    # Carries no tools on purpose, so a test call tags nothing in GHL.
    "test": {
        "name": "Voicsito - Ashley - TEST",
        "prompt": ("demo_test", "stage1"),
        "env": "VAPI_ASSISTANT_ID_TEST",
        "tools": [],
        # She speaks the moment they pick up instead of waiting for "hello?".
        # Delivered by Vapi as ONE ElevenLabs generation with no model in the
        # loop, which is also why the greeting stops losing words ("It's Ash").
        # Leading <break> is the ONLY way to delay an opener — Vapi has no field
        # for it, and startSpeakingPlan.waitSeconds only applies after the
        # customer speaks, which never happens before a first message.
        # Requires voice.enableSsmlParsing (on). A break at position zero has no
        # preceding word, so the word-echo failure logged against break tags
        # elsewhere in this project cannot occur here.
        "first_message": "<break time=\"1.5s\" />{{firstName}}! good {{greeting}}! It's Ashley from Cloud 7, I've been texting you. How is everything?",
        # SSML survives only when ElevenLabs receives ONE complete document.
        # chunkPlan OFF atomises every token ('<s' 'peak' 'break' ' time' '0' '5' 's').
        # chunkPlan ON at 80 splits the turn nine ways, so  opens in chunk 1
        # and closes in chunk 9. Keeping it ON but above a whole turn's length means
        # nothing qualifies to split and Vapi flushes the buffer once at turn end —
        # the tail always flushes regardless of the minimum (every call ends on a
        # 5-char 'okay?' chunk). Costs a little latency: she waits for the full
        # model response before speaking.
        "voice_overrides": {"chunkPlan": {
            "enabled": True,
            "minCharacters": 80,
            "punctuationBoundaries": [";"],   # never occurs in the script -> nothing splits
        }},
    },
    # Outcome-driven twin (2026-09-02). Same voice pipeline and same greeting as
    # TEST, but the prompt hands her the JOB instead of anchor lines to reproduce,
    # and carries no SSML at all. Isolates one variable: does wording the model
    # composes itself survive this TTS better than wording it copies?
    "test2": {
        "name": "Voicsito - Ashley - TEST V2",
        "prompt": ("demo_test_v2", "stage1"),
        "env": "VAPI_ASSISTANT_ID_TEST2",
        "tools": [],
        "first_message": "<break time=\"1.5s\" />{{firstName}}! good {{greeting}}! It's Ashley from Cloud 7, I've been texting you. How is everything?",
        "voice_overrides": {"chunkPlan": {
            "enabled": True,
            "minCharacters": 80,
            "punctuationBoundaries": [";"],
        }},
    },
}

# Inbound resources this script must NEVER touch — used as a collision guard.
PROTECTED_NAMES = {
    "Voicsito - Stage 1 - Inbound Texts",
    "Voicsito - Stage 2 - Qualify",
    "Voicsito - Stage 3 - Book",
    "Voicsito - Squad - Inbound Texts",
}

_targets = {f["name"] for f in FLOWS.values()}
assert _targets.isdisjoint(PROTECTED_NAMES), (
    f"ISOLATION GUARD TRIPPED — a target name collides with an inbound resource: "
    f"{_targets & PROTECTED_NAMES}"
)


# ─────────────────────────── HTTP helpers ───────────────────────────
def vapi_get(path: str):
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


def _print_vapi_response(resp: dict) -> None:
    """Surface proof Vapi accepted the write (reaching here means HTTP < 400)."""
    model = resp.get("model") or {}
    msgs = model.get("messages") or []
    prompt_chars = len(msgs[0]["content"]) if msgs and msgs[0].get("content") else 0
    tools = [t.get("function", {}).get("name")
             for t in model.get("tools", []) if t.get("type") == "function"]
    voice = (resp.get("voice") or {}).get("voiceId", "?")
    print(f"      ← HTTP 2xx · id={resp.get('id','?')} · updatedAt={resp.get('updatedAt','?')} · "
          f"voice={voice} · prompt={prompt_chars} chars · tools={tools}")


def find_by_name(kind: str, name: str) -> dict | None:
    for item in vapi_get(f"/{kind}"):
        if item.get("name") == name:
            return item
    return None


# ─────────────────────────── Config builders ───────────────────────────
def base_config() -> dict:
    """Transcriber / speaking plans cloned from the live monolithic assistant
    (READ-ONLY), with Ashley's voice swapped in over Jon's."""
    mono = vapi_get(f"/assistant/{MONOLITHIC_ASSISTANT_ID}")
    stop_plan = mono.get("stopSpeakingPlan") or {}
    return {
        # speed/stability/style carried over from the confirm-flow A/B (2026-06-19):
        # lower stability + higher style reads less flat on short calls.
        # Declared in full, NOT spread from mono["voice"]. Jon's voice block used
        # to be splatted in here and anything not overridden came along silently:
        # that is how Ashley ended up on his TTS model, his similarityBoost of
        # 1.00, and a car-engine background loop. Nothing here is inherited any
        # more — every field is a decision, and an unset one is visible as a gap
        # rather than quietly becoming his.
        "voice": {
            "provider": "11labs",
            "voiceId": ASHLEY_VOICE_ID,
            "model": ASHLEY_TTS_MODEL,
            "enableSsmlParsing": True,
            # 0.90 -> 0.95 -> 0.975 -> 1.0 -> 0.97 (2026-08-20) -> 0.94
            # -> 0.92 -> 0.93 -> 0.90 (2026-08-30). 0.92 read as flat once; 0.90 is
            # the owner's call and sits at the edge — ElevenLabs' floor is 0.7 but
            # the voice starts to drag below about 0.9. 1.0, the voice's natural pace, read as too fast live.
            # The drop to 0.94 is now the pacing lever in its own right: break
            # markers are ruled out (the model renders them as a stutter, not a
            # pause), so slowing the whole delivery is the only way left to open
            # the gaps up, and unlike a marker it cannot corrupt a word.
            # ElevenLabs' floor is 0.7; below ~0.9 the voice starts to drag.
            # Back to 1.00 after the A/B (2026-08-29): 0.75 sounded fine but was
            # not an improvement worth the drift from the source recording, and
            # holding the owner's long-standing baseline makes the TTS model the
            # only variable in the next call. ElevenLabs warn that high values
            # reproduce artefacts and noise from the source recording, so if
            # audio grain shows up later this is the first slider to revisit.
            "similarityBoost": 1.0,
            "speed": 0.88,
            # 0.40 -> 0.45 (2026-08-28, owner). Consistency between generations;
            # ElevenLabs default is 0.50. Raised as the counterweight to style 0.65.
            "stability": 0.45,
            # 0.45 -> 0.60 -> 0.65 (2026-08-25, owner): more emotion. Style
            # trades against consistency, and consistency is what is currently
            # failing (see optimizeStreamingLatency note) — if words keep
            # dropping, stability above is the counterweight to raise with it.
            "style": 0.75,
            # Vapi defaults this to 3 = max latency optimisation at significant
            # quality cost. Ruled OUT as the cause of the dropped words: call
            # 01a03697 ran at 0 and still lost "office"/"so" and truncated
            # "Ashley" to "Ash". Held at 1 (~half the latency benefit of 3,
            # minor quality cost) as the working compromise.
            "optimizeStreamingLatency": 1,
            # Was arriving as false purely because Jon's block set it. ElevenLabs
            # default it ON. Held at false so removing the splat changes nothing
            # audible — flipping it is its own single-variable test.
            "useSpeakerBoost": False,
            # Vapi splits the model's text into chunks and sends each to
            # ElevenLabs separately. Every boundary is a fresh generation, and
            # words die at them: call 01a04fec sent "Hey Ryan, good evening!
            # It's Ashley." / "How've you been?" as two chunks and the audio came
            # back "It's Ash How have you been?". Vapi's own flush-syntax docs
            # warn that more boundaries cause "audio fragmentation".
            #   punctuationBoundaries: the default set includes the COMMA, which
            #     is why the 38-word joining line was being cut into three
            #     generations. Sentence-enders only.
            #   minCharacters 80: stops short fragments ("okay?", "How've you
            #     been?") becoming their own generation.
            "chunkPlan": {
                "enabled": True,
                "minCharacters": 80,
                "punctuationBoundaries": [".", "!", "?"],
            },
        },
        "transcriber": mono["transcriber"],
        # smartEndpointingEnabled (bool) is deprecated; smartEndpointingPlan
        # supersedes it and wins when both are present. LiveKit is Vapi's
        # recommendation for English — it reads prosody to tell a mid-thought
        # pause from a finished turn, instead of firing on a fixed timer.
        "startSpeakingPlan": {
            **(mono.get("startSpeakingPlan") or {}),
            "smartEndpointingPlan": {"provider": "livekit"},
        },
        "stopSpeakingPlan": {**stop_plan, "numWords": 3, "backoffSeconds": 1.5},
        "backchannelingEnabled": True,
        # Declared here rather than inherited from Jon. His block is
        # {provider: google, type: audio} with NO backoffPlan, so detection ran
        # on Vapi's defaults, and 13 calls in the 2026-09-07 sample delivered
        # two or more turns of script into an answering machine (11.2 minutes
        # of it) before hanging up. Three reasons it was missing them:
        #   - firstMessageMode is assistant-speaks-first, so Ashley is talking
        #     OVER the greeting from second zero. Vapi's own provider is the
        #     one documented as gracefully handling that overlap.
        #   - the systems that got through are carrier "this person is not
        #     available" screens, not classic beep-at-the-tone machines.
        #   - with no backoffPlan the checks stop early; these greetings run
        #     20-30s ("Thanks. Please stay on the line." lands first, the
        #     "not available" line only after that).
        # startAtSeconds 2 / frequencySeconds 2.5 (the documented minimum) /
        # maxRetries 10 keeps checking to ~27s, which covers the observed
        # greetings. beepMaxAwaitSeconds is deliberately unset: voicemailMessage
        # is empty, so there is no message to wait for a beep to leave.
        "voicemailDetection": {
            "provider": "vapi",
            "backoffPlan": {
                "startAtSeconds": 2,
                "frequencySeconds": 2.5,
                "maxRetries": 10,
            },
        },
        # The monolithic assistant is Jon, who takes calls from his truck, so it
        # carries a hosted car-engine loop. Ashley is at a desk — do NOT inherit
        # it here. "office" is Vapi's built-in ambience, no asset to host.
        "backgroundSound": "office",
        "silenceTimeoutSeconds": mono.get("silenceTimeoutSeconds", 60),
        "maxDurationSeconds": mono.get("maxDurationSeconds", 1800),
        # Vapi speaks this whenever end_call fires, whatever the model did.
        #
        # Three consecutive live calls (01a01c9f, 01a01cf9, 01a01d8d) ended with
        # the model firing end_call and saying nothing — hanging up on the
        # prospect mid-goodbye. Prompt gates were tried three ways and did not
        # hold, so the guarantee moves out of the model's hands and into the
        # platform: it is structurally impossible to end one of these calls in
        # silence now.
        #
        # The sign-off is spoken BY ASHLEY, from the prompt, and saying it is
        # what ends the call — see endCallPhrases below.
        #
        # Five live calls (01a01c9f, 01a01cf9, 01a01d8d, 01a0202f, 01a02158)
        # ended with end_call firing and no goodbye, across five prompt gates.
        # The cause is structural: GPT-4.1 returns either content or a tool
        # call in a turn, so "say the goodbye AND call end_call together" asks
        # for something it reliably will not do. endCallPhrases removes the
        # conflict — she only has to speak, and the platform hangs up on hearing
        # it, so the line keeps her name in it and stays in the script.
        #
        # Empty: a goodbye here would talk over the one she just spoke.
        "endCallMessage": "",
        # Saying this ends the call. Must match the tail of the sign-off anchor
        # in stage1_confirm.py / the rebook branch — change both together.
        # Substring match on what she says. "Byeeeee" is the scripted spelling,
        # but GPT-4.1 shortens stretched vowels, so every landing point between
        # the full stretch and a bare "Bye" is listed. Missing the match does not
        # degrade gracefully — the call stays open to the 60s silence timeout.
        # "Bye for now" stays listed so a rollback of the prompts still hangs up.
        "endCallPhrases": ["BYE", "Byeee", "Bye for now", "Bye"],
        # Only end-of-call-report is needed; subscribing to everything would put
        # every transcript chunk through this endpoint for no benefit.
        "server": _events_server(),
        "serverMessages": ["end-of-call-report"],
        # DELIBERATELY EMPTY: the monolithic assistant's voicemailMessage is in
        # Jon's voice. Ashley leaving Jon's voicemail would be a hard character
        # break, and inventing her wording is not this script's call. Empty means
        # no message is left. Owner must supply Ashley's voicemail script.
        "voicemailMessage": "",
    }


def endcall_tool() -> dict:
    # An empty request-start message fires end_call silently, suppressing Vapi's
    # default spoken "Goodbye" so the model's own branch sign-off is the last
    # thing heard. Mirrors the proven-silent pattern on the inbound squad.
    return {"type": "endCall", "messages": [{"type": "request-start", "content": ""}]}


# An empty request-start message suppresses Vapi's default spoken filler while a
# tool runs ("give me a moment"). The only spoken lines come from the prompt.
SILENT_TOOL_MESSAGES = [{"type": "request-start", "content": ""}]

# The booking is the one tool the prospect waits on: GHL takes 4-9s, and call
# 01a0a234 sat in 8.4s of silence after "Let's do eleven." Vapi speaks this the
# instant the request starts, so it cannot be skipped or said twice the way a
# model-spoken line can (GPT-4.1 returns content OR a tool call, never both).
# Owner's words, 2026-09-14.
BOOKING_HOLD_MESSAGES = [{"type": "request-start", "content": "Hold on a sec."}]


def _events_server() -> dict:
    """Server block for end-of-call reports. Same shared secret as the tools."""
    server: dict = {"url": OUTBOUND_EVENTS_URL}
    secret = os.environ.get("VAPI_WEBHOOK_SECRET", "").strip()
    if secret:
        server["secret"] = secret
    return server


def _outbound_server() -> dict:
    """Server block for an inline function tool that calls the outbound webhook.
    The outbound webhook enforces VAPI_WEBHOOK_SECRET; put it on the tool so Vapi
    sends x-vapi-secret (without it the call 401s). Read from env — never hardcode."""
    server: dict = {"url": OUTBOUND_TOOL_URL}
    secret = os.environ.get("VAPI_WEBHOOK_SECRET", "").strip()
    if secret:
        server["secret"] = secret
    return server


def get_prospect_appointment_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_prospect_appointment",
            "description": (
                "Look up the prospect's booked video call. Call this once at the very "
                "start of the call, before you speak — it is always your first action. "
                "Takes no arguments: the server already knows which number this call is "
                "to. Returns whether a booking was found, its day and time, the "
                "prospect's first name, and whether it is morning, afternoon, or evening "
                "where they are."
            ),
            # No required args by design. Requiring phone_number made the model
            # skip the tool entirely — it has no way to know the number — and
            # invent a booked time instead (live-observed 2026-08-19).
            "parameters": {"type": "object", "properties": {}},
        },
        "messages": SILENT_TOOL_MESSAGES,
        "server": _outbound_server(),
    }


def mark_locked_in_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "mark_locked_in",
            "description": (
                "Tag the prospect's contact as locked-in. Call this the moment they "
                "confirm they're good for their booked video call. Fire-and-forget — "
                "no arguments, keep talking."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        "messages": SILENT_TOOL_MESSAGES,
        "server": _outbound_server(),
    }


def mark_not_interested_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "mark_not_interested",
            "description": (
                "Tag the prospect as not interested. Call this the moment they say "
                "they no longer want the demo, want to cancel outright, or are not "
                "interested any more. Fire-and-forget — no arguments, keep talking. "
                "Never call it because they simply need a different time; that is a "
                "reschedule, not a cancellation."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        "messages": SILENT_TOOL_MESSAGES,
        "server": _outbound_server(),
    }


def mark_needs_reschedule_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "mark_needs_reschedule",
            "description": (
                "Tag the prospect's contact as needing a new time. Call this the moment "
                "they say they can't make their booked time, or when they have no booking "
                "on file. Fire-and-forget — no arguments, keep talking. A separate call-back "
                "finds them the new time, so this call offers no days or times."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        "messages": SILENT_TOOL_MESSAGES,
        "server": _outbound_server(),
    }


def get_available_slots_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": (
                "Returns the rep's next available video-call slots. Call this the moment "
                "the prospect is ready to look at times, before you name any day or time. "
                "Every day and time you offer must come from this tool's result; never "
                "offer a slot from memory or one you have not fetched this turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "Number of days from today to look ahead for available slots.",
                    }
                },
                "required": ["days_ahead"],
            },
        },
        "messages": SILENT_TOOL_MESSAGES,
        "server": _outbound_server(),
    }


def reschedule_video_call_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "reschedule_video_call",
            "description": (
                "Lock in the video-call time the prospect agreed to — it moves an existing "
                "booking, or creates one if they had none. Pass the exact ISO value from the "
                "[iso:...] tag of the slot they agreed to, from the open times you were given "
                "or a get_available_slots result — copy it character for character. Never "
                "type or guess a date, time, or year yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_iso": {
                        "type": "string",
                        "description": (
                            "ISO-8601 datetime with timezone, copied exactly from the "
                            "slot's [iso:...] tag, e.g. 2026-06-18T14:00:00-07:00."
                        ),
                    }
                },
                "required": ["slot_iso"],
            },
        },
        "messages": BOOKING_HOLD_MESSAGES,
        "server": _outbound_server(),
    }


TOOL_BUILDERS = {
    "mark_not_interested": mark_not_interested_tool,
    "mark_locked_in": mark_locked_in_tool,
    "mark_needs_reschedule": mark_needs_reschedule_tool,
    "get_available_slots": get_available_slots_tool,
    "reschedule_video_call": reschedule_video_call_tool,
}


def build_assistant(flow: dict, base: dict) -> dict:
    # No get_prospect_appointment tool: the booking is resolved BEFORE the call
    # and handed over as {{firstName}} / {{bookedTime}} / {{greeting}}. Two live
    # calls proved the model will not fire a "call this first" tool under
    # firstMessageMode=assistant-waits-for-user — it greets instead, then
    # invents a time. A step that does not exist cannot be skipped.
    # Each flow names its tools explicitly, so the calendar tools exist only on
    # the Rebooking assistant and the hand-off tag only on the two that hand off.
    # No endCall tool on any of them. With both an end_call tool AND
    # endCallPhrases available, the model fires the tool first and its goodbye
    # lands after the hangup, truncated ("...Have a good day, Ryan. Bye" — live
    # call 01a02165). Removing the tool leaves exactly one way to end a call: say
    # the phrase. The words come first because they ARE the trigger.
    tools = [TOOL_BUILDERS[name]() for name in flow["tools"]]
    cfg = {
        "name": flow["name"],
        # We dial them; the prospect says "hello?" first and the prompt gates on it.
        "firstMessageMode": (
            "assistant-speaks-first" if flow.get("first_message") else "assistant-waits-for-user"
        ),
        **({"firstMessage": flow["first_message"]} if flow.get("first_message") else {}),
        "model": {
            "provider": "openai",
            # Per-flow override for A/B tests; production flows default to 4.1.
            "model": flow.get("model", "gpt-4.1"),
            # Only reasoning models take this; leave it out everywhere else.
            **({"reasoningEffort": flow["reasoning_effort"]} if flow.get("reasoning_effort") else {}),
            # 0.6 (inherited from the inbound sales squad) is tuned for a bot
            # that improvises its way through objections. This one reads a fixed
            # script, and the sampling was showing up as word-level drift no
            # amount of prompt rules fixed: "breakdown" -> "break"/"brake down",
            # "I'm good too" -> "I'm too", "How is everything?" -> "Is
            # everything?", dropped question marks. Low temperature is the right
            # instrument for script fidelity; the warmth comes from the wording,
            # not the sampling.
            "temperature": 0.2,
            "maxTokens": 350,
            "promptCacheRetention": "24h",
            # Static prompt first, then (for flows that have one) the per-call
            # context as its own system message, so the static text stays a
            # cacheable prefix across calls (2026-09-15).
            "messages": [
                {"role": "system", "content": get_stage_prompt(*flow["prompt"])},
                *(
                    [{"role": "system", "content": get_stage_context(*flow["prompt"])}]
                    if get_stage_context(*flow["prompt"]) else []
                ),
            ],
            "tools": tools,
        },
        # NOTE: no idle/silence hooks. The inherited ones are in Jon's voice
        # ("Hey, you still there brother?") and would break Ashley's character.
        # Behaviour without them matches the live inbound stages, which also run
        # hook-free — silenceTimeoutSeconds is the safety net. Add Ashley's own
        # nudge + graceful-hangup wording when the owner supplies it.
        **base,
    }
    # Per-flow voice overrides, applied on top of the shared base voice block.
    # Used for TEST-only experiments so production keeps the agreed settings.
    if flow.get("voice_overrides"):
        cfg["voice"] = {**cfg["voice"], **flow["voice_overrides"]}
    # Per-flow endpointing: False drops the smart plan so the fixed timers apply.
    # The whole startSpeakingPlan object is sent, so the key really goes away.
    if flow.get("smart_endpointing") is False:
        cfg["startSpeakingPlan"] = {
            k: v for k, v in cfg["startSpeakingPlan"].items() if k != "smartEndpointingPlan"
        }
        cfg["startSpeakingPlan"]["smartEndpointingEnabled"] = False
    if flow.get("wait_seconds") is not None:
        cfg["startSpeakingPlan"] = {**cfg["startSpeakingPlan"], "waitSeconds": flow["wait_seconds"]}
    # Per-flow background sound: "off", "office", or a public audio URL.
    if flow.get("background_sound"):
        cfg["backgroundSound"] = flow["background_sound"]
    # Per-flow hang-up phrases, added on top of the shared BYE variants.
    if flow.get("extra_end_call_phrases"):
        cfg["endCallPhrases"] = [*cfg.get("endCallPhrases", []), *flow["extra_end_call_phrases"]]
    return cfg


# ─────────────────────────── Upsert ───────────────────────────
def upsert_assistant(config: dict, dry_run: bool) -> str:
    name = config["name"]
    if name in PROTECTED_NAMES:  # belt-and-suspenders; guard already asserted
        sys.exit(f"✗ refusing to touch protected inbound assistant '{name}'")
    existing = find_by_name("assistant", name)
    if existing:
        aid = existing["id"]
        if dry_run:
            print(f"  [dry-run] would PATCH '{name}' ({aid})")
            return aid
        resp = vapi_patch(f"/assistant/{aid}", {k: v for k, v in config.items() if k != "name"})
        print(f"  ✓ patched '{name}' ({aid})")
        _print_vapi_response(resp)
        return aid
    if dry_run:
        print(f"  [dry-run] would CREATE '{name}'")
        return "dry-run-id"
    new = vapi_post("/assistant", config)
    print(f"  ✓ created '{name}' ({new['id']})")
    _print_vapi_response(new)
    return new["id"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="show what would change without sending")
    args = p.parse_args()

    # Windows consoles default to cp1252 and choke on the status glyphs below.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not os.environ.get("VAPI_WEBHOOK_SECRET", "").strip():
        print("  ⚠ VAPI_WEBHOOK_SECRET not set — the assistants' tools will 401 against"
              " prod (which enforces it). Add it to config/.env before pushing.\n")

    print("Outbound Ashley assistants")
    print(f"  Targets (only these are touched): {sorted(_targets)}")
    print(f"  Protected (never touched):        {sorted(PROTECTED_NAMES)}\n")

    total = 1 + len(FLOWS)
    print(f"[1/{total}] Reading base config from monolithic assistant (read-only)...")
    base = base_config()
    print(f"  ✓ voice={base['voice'].get('voiceId')} (Ashley) speed={base['voice'].get('speed')}\n")

    ids: dict[str, str] = {}
    for i, (call_type, flow) in enumerate(FLOWS.items(), start=2):
        print(f"[{i}/{total}] {flow['name']}...")
        ids[flow["env"]] = upsert_assistant(build_assistant(flow, base), args.dry_run)
        print()

    print("=" * 64)
    print("Add these to the webhook server's .env, then restart it:\n")
    for env_name, aid in ids.items():
        print(f"  {env_name}={aid}")
    print("\nAlso required there (not printed — set them yourself):")
    print("  GHL_OUTBOUND_SECRET=<shared secret you also put in the GHL webhook header>")
    print("  VAPI_PHONE_NUMBER_ID=<the Vapi number to dial FROM>")
    print("\nGHL Custom Webhook target:")
    print("  POST https://webhooks.c7a.io/voicsito/ghl/outbound-call")
    print("  Header: X-GHL-Secret: <GHL_OUTBOUND_SECRET>")
    print('  Body:   {"call_type":"confirmation","phone":"{{contact.phone}}",')
    print('           "contact_id":"{{contact.id}}","appointment_id":"{{appointment.id}}",')
    print('           "first_name":"{{contact.first_name}}"}')
    print("  call_type is one of: confirmation | reminder | rebooking")
    print("  rebooking = the branch to take when the contact carries `needs-reschedule`")
    print("\n⚠ No voicemail message is set (Jon's would break character) and no idle")
    print("  hooks are attached. Supply Ashley's wording for both before volume use.")


if __name__ == "__main__":
    main()
