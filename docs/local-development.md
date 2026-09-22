# Local Development Workflow

**As of 2026-05-18** — Voicsito's orchestration runs on a 3-stage **Vapi Squad** (managed). The daily edit-deploy-test loop has three surfaces:

1. **The stage prompts** (`src/agent/prompts/shared_persona.py` + `stage1_inbound_texts.py` + `stage2_qualify.py` + `stage3_book.py`) — local files are the source of truth, pushed to the Vapi assistants via `scripts/push_squad.py`.
2. **The webhook server** (`src/webhooks/server.py`) — FastAPI app that receives Vapi tool calls AND phone-level server-events (assistant-request, status-update, end-of-call-report).
3. **The offline simulator** (`scripts/sim-call.py` + `scripts/judge-call.py`) — LLM-vs-LLM persona testing for fast iteration without burning real call minutes.

The legacy LiveKit-on-VPS workflow has been removed. LiveKit was decommissioned 2026-05-05. The pre-split monolithic prompt (`src/agent/prompts/inbound_texts.py`) is preserved as a rollback target.

---

## Repo structure

```
src/
├── agent/                         — shared library code
│   ├── ghl.py                     — GHL API client (REUSED — imported by webhooks)
│   └── prompts/
│       ├── __init__.py            — registry: get_prompt() + get_stage_prompt()
│       ├── shared_persona.py      — imported into every stage (persona, voice, output formatting)
│       ├── stage1_inbound_texts.py — Type 2 INTEREST stage
│       ├── stage2_qualify.py      — QUALIFY stage (shared across call types)
│       ├── stage3_book.py         — BOOK stage (shared across call types)
│       └── inbound_texts.py       — pre-split monolithic prompt (rollback target)
│       # (future per-call-type Stage 1s: stage1_outbound_discovery.py, etc.)
│
└── webhooks/                      — Vapi webhook server
    ├── server.py                  — FastAPI app: /vapi/tool dispatcher + /vapi/server-events handler
    ├── call_archive.py            — per-call folder under /opt/webhooks/voicsito/calls/
    ├── call_summary.py            — Telegram message formatters
    ├── telegram_notify.py         — Telegram bot client
    ├── tz_resolver.py             — stored-IANA → city/state → Eastern default chain
    ├── stats.py                   — daily stats helpers
    └── requirements.txt           — fastapi, uvicorn, httpx, python-dotenv

audio/                             — pre-rendered clips (firstmessage-opener.mp3, car-bg-v3.mp3)

recordings/                        — Jon voice cloning pipeline (gitignored, unchanged)
├── raw/                           — drop call recordings here
├── jon-only/                      — per-call Jon-only WAVs (61 clean — re-clone source)
└── jon-combined.wav               — final concatenated Jon audio for ElevenLabs upload

scripts/
├── push_squad.py                  — idempotent build/update of 3 stage assistants + Squad
├── sync-vapi-prompt.py            — legacy monolithic prompt sync (rollback flow)
├── deploy-webhooks.sh             — deploy src/webhooks/ to VPS
├── setup-webhook-infra.sh         — one-time VPS infra setup
├── sim-call.py                    — LLM-vs-LLM simulator (Jon vs persona)
├── judge-call.py                  — Claude-judged sim transcripts
├── run_daily_stats.py             — cron-callable daily stats report
├── extract-jon.py                 — Deepgram diarization → Jon-only audio
├── extract-prospect.py            — sister of extract-jon.py
├── stitch-jon-clean.py            — stitch clean Jon-only WAVs into a re-clone source
├── deep-clean-jon.py              — audio cleanup pre-clone
├── tts-test.py                    — render TTS for offline A/B
├── audio-glitch-check.py          — librosa glitch detector
└── compare-jon.py                 — voice-clone metrics vs reference

tests/
├── personas/                      — 12 contractor personas for sim testing
├── runs/                          — sim run outputs (JSON)
├── recordings/                    — saved live-call transcripts
└── lib/                           — test markers + cleanup helpers (live-system-testing skill)

config/
├── .env                           — credentials (gitignored)
└── .env.example                   — template

docs/
├── architecture.md                — system design (Vapi)
├── system-summary.md              — shareable technical summary (Vapi)
├── vapi-migration.md              — cutover playbook
├── call-types.md                  — all 6 call types
├── prompt-engineering-guide.md    — how to write call-type prompts
├── voice-cloning.md               — Jon voice clone pipeline
└── base-script-original.md        — Jon's original human script
```

---

## Daily workflow (Vapi — current)

### Path A — Iterating on the stage prompts (fastest)

The stage prompts are what each squad-member LLM sees on every turn. Three places to edit them:

**Option 1: Offline simulator (no Vapi cost, ~3-5s per run)**

1. Edit the relevant stage file (`shared_persona.py` if persona-wide; `stage1_inbound_texts.py` / `stage2_qualify.py` / `stage3_book.py` for stage-specific changes)
2. Pick a persona from `tests/personas/` that exercises the change (or run via the `simulation-test` skill)
3. Run the sim:
   ```bash
   .venv-tests/bin/python scripts/sim-call.py --persona hard_head --stage stage2
   .venv-tests/bin/python scripts/judge-call.py tests/runs/<latest>.json
   ```
4. Iterate until the judge passes. Only push to Vapi after the local sim is green.

**Option 2: Edit locally, push to the live Squad**

1. Edit the stage file(s)
2. Push all three stages + Squad in one shot:
   ```bash
   .venv-extract/bin/python scripts/push_squad.py
   ```
   The script is idempotent — it updates existing assistants by name, re-points the Squad's members, and inherits voice/transcriber/speaking-plans from the monolithic assistant baseline.
3. Place a test call to **+1555010011**.

**Option 3: Vapi dashboard (fastest for ad-hoc tuning of one stage)**

1. Open the Vapi dashboard → Assistants → `Voicsito - Stage N - …` → System Prompt
2. Edit, hit Save, place a test call within seconds
3. Once happy, **copy the final prompt back into the corresponding `src/agent/prompts/stage*.py`** so the file stays the source of truth. `push_squad.py` will otherwise overwrite the dashboard edit on the next push.
4. Commit

### Path B — Iterating on a tool / webhook

When you need to change CRM logic, calendar lookup, etc.:

1. Edit the tool module under `src/webhooks/tools/<tool>.py` or `src/webhooks/server.py` itself
2. (Optional) Run locally for unit-testing: `uvicorn src.webhooks.server:app --reload --port 8081`
3. Deploy:
   ```bash
   /Users/waseembaluch/CLAUDSITO/Voicsito/scripts/deploy-webhooks.sh
   ```
   This script:
   - Validates Python syntax across `src/webhooks/`
   - Rsyncs the directory to the host
   - Restarts the `voicsito-webhooks` systemd unit (or container)
   - Verifies the `/health` endpoint returns 200 over TLS
   - Tails the recent log lines
4. Place a test call

### Path C — Iterating on Vapi assistant config (voice settings, speaking plans, etc.)

Most Vapi tunables live in the assistant JSON (not in our repo) and are edited via the Vapi dashboard. The key knobs are listed in **Vapi configuration tuning** below.

### Test

Call **+1555010011** from any phone. Routing on Vapi:

```
PSTN → Vapi-bundled number → Vapi POSTs assistant-request to our webhook
     → Webhook does GHL lookup, returns {squadId, variableValues}
     → Vapi loads the Squad (Stage 1 first)
     → Vapi runs the loop (STT/LLM/TTS) and hits our webhook for tool calls
     → On `handoff` from Stage 1/2, Vapi switches to next stage in the same call
```

### Inspect logs

**Vapi side:** dashboard → Calls → click the call → full timeline (STT segments, LLM tokens, TTS chunks, tool-call payloads, latency per stage).

**Our webhook side:**
```bash
ssh voicsito-vps \
  'journalctl -u voicsito-webhooks -f'
```

---

## Vapi configuration tuning

The Vapi-side knobs that materially affect call feel. All are set per-assistant in the Vapi dashboard or via the Vapi REST API.

| Knob | What it controls | Recommended start |
|---|---|---|
| `startSpeakingPlan.waitSeconds` | How long Vapi waits after the user stops speaking before the LLM starts generating. **Default 1.5 s — DROP THIS.** | `0.2` (current production) |
| `startSpeakingPlan.smartEndpointingEnabled` | Use Vapi's ML model to predict end-of-turn (vs. fixed silence threshold) | `false` — its sigmoid added 1-2s mid-confidence latency in our testing |
| `startSpeakingPlan.transcriptionEndpointingPlan.onNoPunctuationSeconds` | Wait after non-final transcript without punctuation. **Default 1.5 s — DROP THIS.** | `0.4` |
| `stopSpeakingPlan.numWords` | How many words of user speech before Vapi interrupts the assistant (barge-in sensitivity) | `2` |
| `stopSpeakingPlan.voiceSeconds` | Min user-voice duration to trigger barge-in (gates against breath/cough) | `0.2` |
| `voice.provider` / `voice.voiceId` | TTS provider + ElevenLabs voice ID | `11labs` / `F2BOg7Ry3zTPV6Zd62Cd` (stock voice; Jon PVC retired) |
| `voice.model` | ElevenLabs model | `eleven_turbo_v2_5` |
| `voice.stability` / `voice.similarityBoost` / `voice.style` / `voice.speed` | ElevenLabs voice settings | `0.30` / `1.00` / `1.00` / `1.00` |
| `voice.useSpeakerBoost` | Quality knob | `true` |
| `model.provider` / `model.model` | LLM provider + model | `openai` / `gpt-4.1` (Anthropic Sonnet 4.6 was used through 2026-05-07; swapped because Vapi doesn't expose Anthropic auto-caching) |
| `model.temperature` | LLM temperature | `0.6` (objection handling needs some warmth, not max determinism) |
| `model.maxTokens` | Per-turn output cap | `350` |
| `model.promptCacheRetention` | OpenAI auto-cache retention | `24h` (cached turns cut TTFT from ~250-500 ms to ~80-150 ms) |
| `transcriber.provider` / `transcriber.model` | STT | `deepgram` / `nova-3` |
| `transcriber.endpointing` | Silence ms to consider speech ended | `150` |
| `backgroundSound` | Custom mp3 URL | `https://webhooks.c7a.io/voicsito/static/car-bg-v3.mp3` |
| `firstMessage` | Pre-rendered MP3 (Stage 1 only) | `https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3` |
| `firstMessageMode` | How the opener fires | Stage 1: `assistant-speaks-first`. Stages 2/3: `assistant-speaks-first-with-model-generated-message`. |
| `voicemailDetection.provider` / `.type` | Voicemail detection | `google` / `audio` |
| `voicemailMessage` | What to leave on voicemail | `Hey, this is Jon. Sorry I missed you — I'll try back later. Talk soon.` |

**Hot tip:** `startSpeakingPlan.waitSeconds` + `transcriptionEndpointingPlan.onNoPunctuationSeconds` together are the single biggest perceived-latency levers. Both default to 1500 ms; both tuned to ≤ 400 ms get you near sub-700 ms mouth-to-ear.

---

## Adding a new call-type prompt

Stages 2 (QUALIFY) and 3 (BOOK) are call-type-agnostic and are reused. You only write a new Stage 1.

1. Create `src/agent/prompts/stage1_<call_type>.py` (e.g. `stage1_outbound_discovery.py`) — clone `stage1_inbound_texts.py` as a starting template, import `SHARED_PERSONA`, write the stage-specific opener + flow + entry objections.
2. Register the new `(call_type, "stage1")` entry in `PROMPT_BY_STAGE` in `src/agent/prompts/__init__.py`.
3. Extend `scripts/push_squad.py` to build a new Squad for the call type (re-uses Stage 2 + Stage 3 assistants by ID).
4. Follow the rules in [`docs/prompt-engineering-guide.md`](prompt-engineering-guide.md).
5. Push: `.venv-extract/bin/python scripts/push_squad.py`
6. Wire the new Vapi phone number's `serverUrl` to `/vapi/server-events` and return the new `squadId` from `_handle_assistant_request` based on caller-ID lookup.
7. Test via sim, then a live call.

---

## Local TTS A/B testing (still valid)

`scripts/tts-test.py` still works. Renders a fixed phrase corpus through ElevenLabs at multiple voice-setting combinations and emits a side-by-side HTML player. Use this when comparing voice IDs or settings before pushing them into the Vapi assistant.

```bash
.venv-extract/bin/python /Users/waseembaluch/CLAUDSITO/Voicsito/scripts/tts-test.py
open /Users/waseembaluch/CLAUDSITO/Voicsito/audio/test/index.html
```

`scripts/audio-glitch-check.py` and `scripts/compare-jon.py` are also platform-agnostic — useful for QAing TTS output and quantifying clone-vs-Jon similarity respectively.

---

## Common operations

### Place a test call (the simple way)

Just dial **+1555010011** from any phone.

### Trigger an outbound test call via Vapi

```bash
VAPI_KEY="$(grep VAPI_API_KEY config/.env | cut -d= -f2)"
VAPI_ASSISTANT_ID="<your assistant id>"

curl -s -X POST "https://api.vapi.ai/call" \
  -H "Authorization: Bearer ${VAPI_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
        "assistantId": "'"${VAPI_ASSISTANT_ID}"'",
        "phoneNumberId": "<your vapi phone id>",
        "customer": {"number": "+1XXXYOURXXXX"}
      }'
```

### Pull recent calls

```bash
curl -s -H "Authorization: Bearer ${VAPI_KEY}" \
  "https://api.vapi.ai/call?limit=10" | jq '.[].id'
```

### Restart webhook server

```bash
ssh voicsito-vps 'systemctl restart voicsito-webhooks'
```

---

## Tools / MCP servers configured

| Tool | Purpose |
|---|---|
| **Context7 MCP** | Live API docs (Anthropic, Vapi, ElevenLabs, etc.) — `mcp__context7__query-docs` |
| **Superpowers plugin** | Workflow skills (writing-plans, systematic-debugging, brainstorming, etc.) |
| **GitHub MCP** | Repo + issue management |
| **statusline-setup agent** | Configure macOS terminal status line |

