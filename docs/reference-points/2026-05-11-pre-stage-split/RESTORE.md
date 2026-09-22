# Pre-Stage-Split Reference Point — 2026-05-11

## What this is

Full snapshot of the Voicsito system taken **immediately before splitting the monolithic prompt into a 3-stage Vapi Squad**. If the stage split goes sideways, this is the rollback target.

This is the last known-good MONOLITHIC config — single assistant, single 33K-char prompt, all behavior in one place.

## Snapshot summary

### LLM + prompt
- Provider: `openai/gpt-4.1`
- Temperature: 0.6, maxTokens: 350, `promptCacheRetention: 24h`
- System prompt: **33,466 chars**, v4-hybrid descendent (anchor-line philosophy + Markdown structure + 8 numbered steps merged from Steps 1+2+3 into one Opening step, then Steps 2–8)
- Prompt file: `inbound_texts.py` in this dir

### Voice (ElevenLabs)
- voiceId: `ljX1ZrXuDIIRVcmiVSyR` ("Michael - Genuine and Approachable" stock voice)
- model: `eleven_multilingual_v2`
- speed: **1.1**
- stability: 0.4
- similarityBoost: 0.75
- style: 0
- useSpeakerBoost: **true** (re-enabled today after being false for the old Jon PVC reasons)
- enableSsmlParsing: false
- optimizeStreamingLatency: 2

### Transcriber
- Provider: `deepgram` model `nova-3`, endpointing 150ms

### firstMessage
- Set to: `https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3`
- Pre-rendered MP3 with **1.5s of leading silence + "Hey, Jon here — who is this calling?"** spoken in Michael voice
- Local source at `audio/firstmessage-opener.mp3`

### Background sound
- URL: `https://webhooks.c7a.io/voicsito/static/car-bg-v3.mp3`
- 20 min mono car ambience at -31 LUFS, 200–4500 Hz band-pass

### Tools (4 total)
- `update_prospect_info` (async, fires on name/business/email)
- `get_available_slots` — **description tightened today**: "Call only at booking phase after close-transition pitch delivered AND prospect stated time preference"
- `mark_for_transfer`
- `book_video_call`
- **All filler messages emptied** (`request-start: ""`, `request-response-delayed: ""`) — silent tool execution per the §6.1 silent-tool rule in the prompt

### Speaking plans
- startSpeakingPlan.waitSeconds: 0.2
- stopSpeakingPlan: numWords=2, voiceSeconds=0.2, backoffSeconds=1.0
- transcriptionEndpointingPlan: onPunctuation=0.1, onNoPunctuation=0.4, onNumber=0.5

### Phone
- Number: `+1555010011`
- Provider: `vapi` (no GHL/Twilio middle layer for voice)
- `assistantId: None` — DYNAMIC routing via `assistant-request` webhook (so CRM lookup runs per call)
- Server: `https://webhooks.c7a.io/voicsito/vapi/server-events`, timeoutSeconds=8

### Webhook
- Healthy: `https://webhooks.c7a.io/voicsito/health` returns `{"ok":true,"service":"voicsito-webhooks"}`

## Where the split is going

Target architecture is in `Stages-target-architecture.md` (Waseem's authored vision):
- **Stage 1 — INTEREST**: opener + name + reason + services question + reveal pitch. Handles "not interested" / "have a marketing guy" / "no marketing" objections.
- **Stage 2 — QUALIFY**: discovery questions + FAQs. Starts once prospect agrees to the marketing conversation.
- **Stage 3 — BOOK**: slot offering + email + booking. Starts after the last discovery question.

Will be implemented as a **Vapi Squad** with three specialist assistants and tool-call-triggered handoffs (per the round-table research in `docs/prompt-experiments/staging-research-vapi.md`, `staging-research-cross-platform.md`, `staging-proposal-boundaries.md`).

## How to restore (rollback procedure)

If the squad rollout fails, restore the monolithic config:

```bash
cd /Users/waseembaluch/CLAUDSITO/Voicsito

# 1. Restore prompt file
cp docs/reference-points/2026-05-11-pre-stage-split/inbound_texts.py \
   src/agent/prompts/inbound_texts.py

# 2. Push the prompt back to Vapi
.venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts \
  --assistant 261bf1a0-3527-4fcb-956c-b6e227d0bae1

# 3. PATCH voice config back to known-good
source config/.env
curl -X PATCH "https://api.vapi.ai/assistant/261bf1a0-3527-4fcb-956c-b6e227d0bae1" \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "voice":{"useSpeakerBoost":true,"model":"eleven_multilingual_v2","speed":1.1,"style":0,"voiceId":"ljX1ZrXuDIIRVcmiVSyR","autoMode":false,"provider":"11labs","stability":0.4,"similarityBoost":0.75,"enableSsmlParsing":false,"optimizeStreamingLatency":2},
    "firstMessage":"https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3",
    "backgroundSound":"https://webhooks.c7a.io/voicsito/static/car-bg-v3.mp3"
  }'

# 4. Point the phone number back at the monolithic assistant
curl -X PATCH "https://api.vapi.ai/phone-number/cb93e61d-d222-42c9-8fb2-74500dc13234" \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"assistantId":null,"squadId":null}'
# (assistantId stays null because the assistant-request webhook does dynamic routing;
#  squadId clears any squad assignment from the split rollout)

# 5. Verify: place a test call, check transcript looks like pre-split behavior
```

## State NOT captured here (intentional)

- Audio files (`audio/car-bg-v3.mp3`, `audio/firstmessage-opener.mp3`) — already version-stable in the repo and on the VPS; no need to duplicate
- `.env` — gitignored, not for reference points
- Recordings and call transcripts — Vapi retains them; pull via API if needed

## Behaviors verified working at this snapshot

Per the most recent test calls (`019e18c2`, `019e188d`, etc.):
- Pre-rendered opener with leading silence plays correctly
- No premature `get_available_slots` calls (tightened description holding)
- No "Hold on a sec" filler interruptions (request-start emptied)
- Anchor-line philosophy producing natural delivery (no choppy 2-4 word reaction fragments)
- Step 1 anchor chains directly into Step 3 question in one utterance
- "Are you an AI?" handled gracefully in-character

## Known unresolved at this snapshot

- Voice still feels somewhat monotone in places (the reason we're staging — overloaded prompt is one suspected cause)
- Random behavior in mid-call — model interpolating awkward filler ("Right?" mid-sentence) — suspected to improve with focused per-stage prompts
- Background sound has 1-2s delay before kicking in after firstMessage (Vapi limitation, partially masked by the 1.5s pre-rendered silence)
