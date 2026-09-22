# Voicsito — System Technical Summary

**As of:** 2026-05-18
**Status:** **3-Stage Vapi Squad live (split shipped 2026-05-11, voice retune 2026-05-13).** The monolithic single-assistant prompt was split into three specialist assistants (Stage 1 INTEREST → Stage 2 QUALIFY → Stage 3 BOOK) wired together as a Vapi Squad. Per-call CRM lookup happens once at `assistant-request` and the webhook returns the squad reference (`VAPI_SQUAD_ID` env var); CRM variables (`biz_type/business/city/state`) propagate to every stage via `assistantOverrides.variableValues`. Handoff between stages is tool-driven (`handoff` inline tool on stages 1 and 2). LLM is **OpenAI GPT-4.1** on every stage, `temperature=0.6, maxTokens=350, promptCacheRetention=24h`. Voice is ElevenLabs **Turbo v2.5** with stock voice `F2BOg7Ry3zTPV6Zd62Cd` (`stability=0.3, similarityBoost=1, style=1, speed=1, useSpeakerBoost=true`) — the Jon PVC was retired after prospect-voice contamination was diagnosed (see checkpoint #6) and the Michael stock voice in the pre-split reference was swapped out during 2026-05-13 retuning. Deepgram nova-3 STT. Stage-1 prompt 19,570 chars, stage-2 19,731 chars, stage-3 14,506 chars (shared persona module imported into each). Booking flow unchanged: GHL `transfer` tag → main-CRM auto-migration with 5×3s (15s) polling on `book_video_call`. Background ambience: `car-bg-v3.mp3`. **Hooks were removed from the stage assistants** — only `silenceTimeoutSeconds=60` remains as a hard safety net; the 12s nudge and 36s graceful hangup that lived on the monolith are not yet re-attached to the stages. Reference points: pre-split monolith ([`reference-points/2026-05-11-pre-stage-split/`](reference-points/2026-05-11-pre-stage-split/)), quality baseline ([`reference-points/2026-05-08-quality-reference/`](reference-points/2026-05-08-quality-reference/)), Jon-clone voice baseline ([`reference-points/2026-05-11-jon-clone-voice-baseline/`](reference-points/2026-05-11-jon-clone-voice-baseline/)), Claude XML prompt ([`reference-points/2026-05-10-claude-xml-prompt/`](reference-points/2026-05-10-claude-xml-prompt/)). SSH via Tailscale (`ssh voicsito-vps`). State snapshots: [`docs/checkpoints.md`](checkpoints.md).
**Test number:** **+1555010011** (Vapi-bundled — Twilio import is the next move to cut phone-side latency). The legacy +1555010012 routing through GHL is no longer wired.

---

## 1. Architecture overview (Vapi — current)

End-to-end call flow:

```
Caller phone (PSTN)
    │
    ▼
Vapi-bundled inbound number +1555010011     [Vapi cloud]
    │ (no assistantId / squadId on the number — DYNAMIC routing)
    │ Vapi POSTs `assistant-request` to:
    │   https://webhooks.c7a.io/voicsito/vapi/server-events
    ▼
src/webhooks/server.py — _handle_assistant_request [our infra]
    │  - GHL lookup_contact_in_texts_crms (2.5s timeout)
    │  - Returns { squadId, assistantOverrides.variableValues:
    │      { biz_type, business, city, state } }
    │
    ▼ Vapi resolves squad reference and loads Stage 1
Vapi Squad: Voicsito - Squad - Inbound Texts      [Vapi cloud]
    ┌─────────────────────────────────────────────┐
    │ Members:                                     │
    │   1. Voicsito - Stage 1 - Inbound Texts     │
    │   2. Voicsito - Stage 2 - Qualify           │
    │   3. Voicsito - Stage 3 - Book              │
    │ Each member shares:                          │
    │   - Deepgram nova-3 streaming STT           │
    │   - OpenAI GPT-4.1 (Vapi-bundled)           │
    │   - ElevenLabs Turbo v2.5 voice              │
    │     F2BOg7Ry3zTPV6Zd62Cd                    │
    │   - VAD, voicemail detection (google audio) │
    │   - Background ambience car-bg-v3.mp3       │
    │ Handoff: `handoff` inline tool fires on     │
    │   stage1→stage2 (reveal yes) and stage2→3   │
    │   (last discovery answered). Stage 3 owns   │
    │   all booking tools.                         │
    └─────────────────────────────────────────────┘
    │ tool calls (HTTPS POST, Bearer auth) →
    ▼
src/webhooks/server.py (FastAPI)                  [our infra: Hostinger VPS, TLS-fronted]
    │  - validates Vapi signature
    │  - dispatches: update_prospect_info, get_available_slots,
    │    mark_for_transfer, book_video_call
    │  - calls into src/agent/ghl.py (REUSED)
    │  - returns JSON tool result
    ▼ response back to Vapi
Vapi resumes LLM → speaks ElevenLabs reply
    ▼
Caller's phone
```

**Where things run now:**

| Component | Location |
|---|---|
| Realtime media + STT/LLM/TTS orchestration | Vapi (managed cloud) |
| Provider keys (Deepgram, ElevenLabs, Anthropic) | Held in Vapi; Vapi makes upstream calls |
| Webhook server (FastAPI) | Hostinger KVM 4 VPS (Boston, Ubuntu 24.04), systemd service `voicsito-webhooks` (or container) behind TLS |
| GHL CRM + GHL inbound number forward | GHL cloud (unchanged) |
| Twilio Elastic SIP trunk + toll-free numbers | Twilio cloud (unchanged) |
| Voice cloning pipeline | Local Mac, offline (unchanged) |
| Local dev workstation | macOS, edits + sync-to-Vapi + webhook deploy + offline TTS A/B |

---

## 2. Stack inventory

**Runtime stack:**

| Layer | Where it runs | Notes |
|---|---|---|
| Realtime orchestrator | **Vapi** (managed cloud) | Per-call-type Squad (Inbound Texts ships first). Each Squad member is its own assistant. |
| Webhook server | Hostinger VPS (Ubuntu 24.04, Python 3.12) | FastAPI app `voicsito-webhooks`, systemd-managed, TLS via nginx + certbot, port 8081 behind `webhooks.c7a.io/voicsito/*` |
| Telephony | Vapi-bundled inbound number | `+1555010011` provider=`vapi`. Twilio Elastic SIP Trunk import is the open path to cut phone-side latency. |

**Provider config (held by Vapi, configured per-assistant):**

| Layer | Provider | Model / Setting |
|---|---|---|
| STT | Deepgram | `nova-3`, `language=en`, `endpointing=150` |
| LLM | OpenAI (Vapi-bundled) | `gpt-4.1`, `temperature=0.6`, `maxTokens=350`, `promptCacheRetention=24h`. Anthropic Sonnet 4.6 was production through 2026-05-07; swapped to GPT-4.1 because Vapi exposes OpenAI auto-caching but not Anthropic. The Claude-XML version of the prompt is preserved at `docs/reference-points/2026-05-10-claude-xml-prompt/` for rollback. |
| TTS | ElevenLabs | `eleven_turbo_v2_5`, voice ID `F2BOg7Ry3zTPV6Zd62Cd` (stock voice), `stability=0.30`, `similarityBoost=1.00`, `style=1.00`, `speed=1.00`, `useSpeakerBoost=true`. Same voice across all three stage assistants. Earlier voices in the timeline: `LJ3VmFT7KIfEw9jSLImM` (Jon PVC — retired due to prospect-voice contamination in training data), `ljX1ZrXuDIIRVcmiVSyR` (Michael stock voice — used during pre-split monolith). |
| VAD + turn detection | Vapi `startSpeakingPlan` | `smartEndpointingEnabled=false` (smart endpointing's sigmoid added 1-2s mid-confidence latency — disabled), `waitSeconds=0.2`, `transcriptionEndpointingPlan.{onPunctuation=0.1, onNoPunctuation=0.4, onNumber=0.5}` |
| Voicemail detection | Vapi built-in | `provider=google, type=audio` |
| Background sound | Vapi `backgroundSound` | Custom car-driving ambience at `https://webhooks.c7a.io/voicsito/static/car-bg-v3.mp3` (20-min mono mp3, band-passed 200-4500Hz to match phone codec, -31 LUFS). Set identically on all three stage assistants. |
| First message | Pre-rendered MP3 | `https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3?v=20260512-122624` — 1.5s leading silence + "Hey, Jon here — who is this calling?" baked in. Stage 1 plays it via `firstMessageMode=assistant-speaks-first`; Stages 2/3 use `assistant-speaks-first-with-model-generated-message` so the LLM generates the handoff opener. |
| CRM / Booking | GHL (LeadConnector) | `services.leadconnectorhq.com` REST + Calendar API; 3 PITs (main / texts1 / texts2). Reached from `src/webhooks/server.py` via `src/agent/ghl.py`. |

**Transport:**
- Phone ↔ Vapi: PSTN (Vapi-bundled number)
- Vapi ↔ Deepgram / OpenAI / ElevenLabs: handled by Vapi
- Vapi ↔ our webhook: HTTPS POST (Bearer-authenticated), JSON, signed via `x-vapi-signature` or `x-vapi-secret`
- Webhook ↔ GHL: HTTPS

**Webhook endpoints (FastAPI, all under `https://webhooks.c7a.io/voicsito/`):**
- `GET  /health` — liveness probe
- `POST /vapi/tool` — single dispatcher for all Vapi tool calls (`update_prospect_info`, `get_available_slots`, `mark_for_transfer`, `book_video_call`, `handoff`-style continuations). Batches multiple tool calls per request; runs `update_prospect_info` + `get_available_slots` first so they prime the per-call cache, then runs the rest. Hard 45s ceiling on `book_video_call`.
- `POST /vapi/server-events` — phone-number-level events:
  - `assistant-request` → GHL lookup + return `{squadId, assistantOverrides.variableValues}` (or `{assistantId, ...}` if `VAPI_SQUAD_ID` env var unset).
  - `status-update` with status=`in-progress` → push "📞 Call started" to Telegram, idempotent per call.id
  - `end-of-call-report` → archive recording + transcript to VPS folder, push Telegram summary + transcript + audio.

**Orchestration code (ours):**
- `src/webhooks/server.py` (1099 lines) — FastAPI app; tool dispatcher + phone-level server-events handler; signature verification; per-call cache; background GHL writes for `update_prospect_info`; tz-aware slot offering; canonical-ISO hallucinated-slot rejection on `book_video_call`.
- `src/webhooks/call_archive.py`, `call_summary.py`, `telegram_notify.py`, `tz_resolver.py`, `stats.py` — end-of-call archiving (VPS folder + idempotent pushed.flag), Telegram message formatting, timezone resolution chain (stored IANA → city/state → Eastern default), daily stats helpers.
- `src/agent/ghl.py` (~391 lines) — GHL REST client (caller lookup across 3 CRMs, contact create, calendar slots, booking, custom field updates, tag add for `transfer` and `voicsito-booked`).
- `src/agent/prompts/` — local source of truth for ALL stage prompts:
  - `shared_persona.py` (172 lines) — persona + anti-robot + ElevenLabs formatting + anti-recitation + phone-call reality + CRM context block + forbidden language. Imported into every stage.
  - `stage1_inbound_texts.py` (184 lines) — Stage 1 INTEREST for the inbound-texts call type.
  - `stage2_qualify.py` (211 lines) — Stage 2 QUALIFY, shared across all call types.
  - `stage3_book.py` (142 lines) — Stage 3 BOOK, shared across all call types.
  - `inbound_texts.py` (516 lines) — pre-split monolithic prompt, kept in the registry as a rollback target. Not pushed to live assistants.
- `scripts/push_squad.py` — idempotent build/update of the 3 stage assistants + Squad. Inherits voice/transcriber/speaking-plans from the monolithic assistant.
- `scripts/sync-vapi-prompt.py` — legacy single-assistant prompt sync (still works for the monolithic rollback target).
- `scripts/sim-call.py`, `scripts/judge-call.py` — LLM-vs-LLM simulator + plain-English judge for offline prompt testing against 12 personas in `tests/personas/`.

**Removed:** previous LiveKit Agents entrypoint (`src/agent/agent.py`) was deleted on 2026-05-05.

---

## 3. Audio pipeline specifics (Vapi)

The realtime audio path is now Vapi-managed. We don't run a media server; we configure an assistant.

**Inbound (caller → LLM):**
- Phone codec: G.711 μ-law, 8 kHz mono (set by carrier — unchanged)
- Twilio SIP → Vapi (Vapi handles transcoding to whatever Deepgram needs)
- Deepgram nova-3 streaming STT, `endpointing=150` ms
- `startSpeakingPlan.transcriptionEndpointingPlan`: `onPunctuationSeconds=0.1`, `onNoPunctuationSeconds=0.4`, `onNumberSeconds=0.5` (Vapi default for `onNoPunctuationSeconds` is 1.5 — that hidden default was the biggest single delay before we tuned it; further trimmed 0.6 → 0.4 on 2026-05-07)
- `startSpeakingPlan.waitSeconds=0.2`, `smartEndpointingEnabled=false` (smart endpointing sigmoid was adding 1-2s mid-confidence — removed)
- `stopSpeakingPlan.numWords=2, voiceSeconds=0.2, backoffSeconds=1.0`

**LLM (every stage of the Squad):**
- OpenAI `gpt-4.1` (Vapi-bundled), `temperature=0.6`, `maxTokens=350`, `promptCacheRetention=24h`
- Tool calls → HTTPS POST to `https://webhooks.c7a.io/voicsito/vapi/tool`. Stages expose different tool sets:
  - Stage 1: `update_prospect_info` + inline `handoff` + inline `endCall`
  - Stage 2: `update_prospect_info` + inline `handoff` + inline `endCall`
  - Stage 3: `update_prospect_info` + `get_available_slots` + `mark_for_transfer` + `book_video_call` + inline `endCall`
- Webhook server timeouts (live values pulled from Vapi tool config):
  - `update_prospect_info` 12s (async — Vapi doesn't block on it)
  - `get_available_slots` 25s
  - `mark_for_transfer` 12s
  - `book_video_call` 60s (polls main CRM for transferred contact; handler also enforces an internal 45s ceiling so the LLM gets a clean error before the 60s Vapi budget breaches)
- Vapi auto-handles OpenAI prompt caching across turns with the 24h retention setting; the per-stage prompts (≈19-20K chars on stages 1/2, ≈14K on stage 3) get re-used many times across multi-turn calls.

**Outbound (LLM → caller):**
- ElevenLabs `eleven_turbo_v2_5` streaming TTS — same voice on every stage so the handoff is inaudible.
- Voice ID `F2BOg7Ry3zTPV6Zd62Cd`; voice settings `stability=0.30, similarityBoost=1.00, style=1.00, speed=1.00, useSpeakerBoost=true`.
- Vapi pipes PCM back through to the caller.

**Idle / silence handling:**
- **The stage assistants currently have NO `hooks` configured.** Only `silenceTimeoutSeconds=60` (hard safety net — ends the call) is in place. The 12s nudge / 36s graceful-hangup hooks that lived on the monolithic assistant were not re-attached when the Squad was pushed. **Open issue** (see §5): re-attach hooks to each stage, or use squad-level idle handling once Vapi supports it.

**Background ambience:**
- Vapi `backgroundSound` set to `https://webhooks.c7a.io/voicsito/static/car-bg-v3.mp3`. 20-min mono mp3 at -31 LUFS, band-passed 200-4500 Hz to match phone codec passband. Same URL on every stage so the ambience continuity across stage handoffs is preserved. Source mp3 lives in repo at `audio/car-bg-v3.mp3`; deploy script ships any `audio/*.mp3` to `/opt/webhooks/voicsito/static/` and nginx serves directly with 7-day cache.

**Barge-in:**
- `stopSpeakingPlan.numWords=2`, `stopSpeakingPlan.voiceSeconds=0.2`, `backoffSeconds=1.0` — gates breath/cough so they don't trigger false barge-in. Same on every stage.

**Turn-taking:**
- Pre-rendered opener fires from Vapi `firstMessage` field on Stage 1 (`firstMessageMode=assistant-speaks-first`): MP3 with 1.5s leading silence + "Hey, Jon here — who is this calling?".
- Stages 2 and 3 use `firstMessageMode=assistant-speaks-first-with-model-generated-message` — the LLM generates a brief continuation right after handoff.
- Hangup is LLM-driven via the Vapi-built-in `endCall` tool, gated by a rejectionPlan regex so the model can only fire it on real goodbye phrases ("bye", "take care", "talk soon", "thanks", "stop calling", "remove me", etc.).

---

## 4. Latency budget (Vapi — estimated)

Vapi's marketing claim is sub-700 ms end-to-end with proper config. We re-estimate per stage:

| Hop | Time | Notes |
|---|---|---|
| Mic → Vapi ingress | ~100–200 ms | PSTN + Vapi edge (Vapi-bundled number, no Twilio in path yet) |
| Vapi STT (Deepgram nova-3, `endpointing=150`) | 150–250 ms | endpointing dominated |
| `transcriptionEndpointingPlan.onNoPunctuationSeconds=0.4` | 400 ms | Vapi default 1.5s (too slow); 0.4s is the current production sweet spot |
| `startSpeakingPlan.waitSeconds=0.2`, `smartEndpointingEnabled=false` | 200 ms | smart endpointing sigmoid was adding 1-2s mid-confidence — disabled |
| OpenAI GPT-4.1 first token | 250–500 ms cold / 80–150 ms cached | `promptCacheRetention=24h`, cache hits dominate after the first turn of a call. Anthropic was retired because Vapi doesn't expose Anthropic auto-caching. |
| Phone-side transport (PSTN → Vapi vs WebRTC) | +600 ms vs +100 ms | known overhead, only fix is BYO Twilio SIP trunk with edge pinning + IP allowlist |
| ElevenLabs Turbo v2.5 TTFB | ~120–220 ms | Turbo trades a small amount of fidelity for ~100ms faster TTFB than Multilingual v2. Acceptable on this stock voice. |
| Vapi → PSTN | 100–200 ms | reverse of inbound |
| **TOTAL (mic-end → speaker, p50)** | **~600–900 ms** | sub-second with cached LLM + tuned `waitSeconds` |
| **p95** | **~1,200 ms** | tail-heavy on LLM TTFT and provider routing |

Single biggest lever now: **`startSpeakingPlan.waitSeconds`** + LLM cache hit rate. The cache is the reason GPT-4.1 was chosen over Anthropic.

---

## 5. Known issues

### 5.1 Resolved by Vapi migration

Mid-word stutter and LiveKit segment-race issues went away with the Vapi migration.

### 5.2 New Vapi-specific issues to watch

1. **`startSpeakingPlan.waitSeconds` defaults to 1.5 s** — leaving it at default is the #1 reason a Vapi assistant feels obviously-AI slow. **Action:** set to 0.4 s and tune from there. (Most-confused early-cutover symptom — feels like "the LLM is slow" but is really this knob.)
2. **Stage assistants have no idle hooks** — only `silenceTimeoutSeconds=60` is in place. The monolithic assistant's 12s nudge + 36s graceful hangup hooks were not re-attached when the Squad was pushed. Calls that go quiet die silently at 60s instead of being nudged. Re-attach hooks per stage, or wait for Vapi to support squad-level idle handling.
3. **Concurrency cliff at the Vapi plan tier** — Vapi caps simultaneous calls per account/plan; running into the cap manifests as call-rejected at SIP ingress, not as queueing. Track peak concurrent and pre-upgrade.
4. **Tool-call timeout is short by default** — Vapi will abandon and apologize if our webhook takes longer than the per-tool timeout. Our worst case is `book_video_call` at 60s (15s of main-CRM polling + several GHL ops); the handler enforces a 45s internal ceiling so the LLM gets a clean error before the 60s budget breaches.
5. **Recording / transcript retention** is on Vapi's terms — we now mirror to the VPS via `call_archive.py` (folder per call under `/opt/webhooks/voicsito/calls/<date>/<call-id>/`) and push summary + transcript + audio to Telegram on every end-of-call-report.
6. **Stage hand-off** is `handoff`-tool driven (inline tool on Stages 1 and 2). If the LLM forgets to fire it, the stage gets stuck. The prompt repeats the rule in 3+ places per stage; sim-tests have not yet exhaustively covered the boundary cases.
7. **Twilio import deferred** — Vapi-bundled number still in production. The fix path is BYO Twilio Elastic SIP Trunk + IP allowlist (Vapi `44.229.228.186`/`44.238.177.138`) for ~500ms phone-side savings. Estimated, not measured against current setup.

### 5.3 Issues that are independent of platform (still ours)

- **Anthropic prompt-cache trial never produced hits on Vapi** (`cachedPromptTokens=0` on every call). Provider-side limitation — Vapi doesn't expose Anthropic caching. Was the proximate cause of the OpenAI swap on 2026-05-08.
- **Jon PVC retired** — original training file mixed Jon + prospect audio, baking volume drift into the model. The clean diarized slices in `recordings/jon-only/` (61 files, ~5 GB) exist but a re-clone has not been re-attempted. Currently running on a stock voice (`F2BOg7Ry3zTPV6Zd62Cd`) — voice character is not Jon.
- **3 of 64 Jon recordings had Deepgram diarization mis-split** Jon's voice across multiple speaker labels. Quarantined to `recordings/suspect/`. Lost ~25 s of training audio out of 295 min.
- **VPS SSH intermittent unreachability** from current network — affects webhook deploy only, not prod calls. Tailscale (`ssh voicsito-vps`) is the workaround.

---

## 6. What's NOT built / hacks / workarounds

**Not built:**
- 5 of 6 call types (only Type 2 Inbound Texts ships): Outbound Discovery, Callback After Miss, Demo Confirmation, Rebooking, Inbound Main. Stages 2 and 3 are written to be call-type-agnostic and reusable; Stage 1 is the per-call-type unit.
- Outbound dispatch via GHL workflow (no outbound orchestrator yet)
- Idle hooks on the stage assistants (regressed from monolith — see §5)
- Backchanneling ("mhm", "yeah" while user talks)
- Pre-recorded locked-phrase hybrid playback (Jon's actual voice for opener/sign-off, TTS only for dynamic parts) — partially shipped (`firstmessage-opener.mp3`), not extended to sign-off
- LLM output text normalization (currency to words, AI-speak strip, phone-number digit-pause) — enforced via prompt rules instead
- Per-call cost guardrails (token cap, max turns, TTS char limit)
- Post-call CRM writeback beyond what `book_video_call` already does (stage move, custom field updates beyond name/business/email)
- Sales dashboard (parked, see project memory `project_sales_dashboard_is_company_hub`)
- Re-cloned Jon voice from `recordings/jon-only/` — diagnosed and unblocked, just not done
- Real comparison-vs-Jon analysis pipeline (`scripts/compare-jon.py` exists but unverified)

**Active hacks / temp workarounds:**
- Voice is a stock ElevenLabs voice (`F2BOg7Ry3zTPV6Zd62Cd`), not a Jon clone
- The pre-rendered opener MP3 has the speaker's name "Jon" baked in — re-clone effort needs to re-render this asset
- GHL PIT tokens have hardcoded fallback values in `ghl.py:CRMS` if env vars missing — should be env-only in prod
- Stage 1's `update_prospect_info` tool is also exposed to Stages 2 and 3 in case the LLM picks up a name or email later — this is by design but blurs the "tools are stage-locked" mental model
- The 3 quarantined recordings (`recordings/suspect/`) were excluded by hand, not by automatic threshold
- Type 2 detection logic is implicit: the squad is hardcoded to the Inbound Texts call type via `VAPI_SQUAD_ID` env var; no real type routing exists yet
- `caller_phone` lookup falls back to `lookup_contact_in_texts_crms` (Texts CRM only) — when Type 6 is built, this becomes wrong
- The monolithic assistant (`261bf1a0...`) is still in Vapi but no longer in active routing (server-events returns the squadId when `VAPI_SQUAD_ID` is set). Its toolIds list is partially stale (missing `update_prospect_info`). Kept around as the documented rollback target.

---

## 7. File / repo structure

```
Voicsito/
├── CLAUDE.md                          project rules for AI assistance
├── .gitignore                         excludes audio assets + recordings + venvs + .env
├── config/
│   ├── .env                           credentials (gitignored)
│   └── .env.example                   template
│
├── src/
│   ├── agent/                         shared library code (GHL client + prompts)
│   │   ├── ghl.py                     GHL REST client (~391 lines, used by webhooks)
│   │   └── prompts/
│   │       ├── __init__.py            registry: get_prompt() + get_stage_prompt()
│   │       ├── shared_persona.py      shared persona/voice/output rules imported into every stage
│   │       ├── stage1_inbound_texts.py  Type 2 INTEREST stage (opener → reveal → handoff)
│   │       ├── stage2_qualify.py      QUALIFY stage (shared across call types)
│   │       ├── stage3_book.py         BOOK stage (shared across call types)
│   │       └── inbound_texts.py       Pre-split monolithic prompt (rollback target)
│   │
│   └── webhooks/                      Vapi webhook server
│       ├── server.py                  FastAPI app, /vapi/tool dispatcher + /vapi/server-events
│       ├── call_archive.py            per-call folder under /opt/webhooks/voicsito/calls/
│       ├── call_summary.py            Telegram message formatters (started + ended)
│       ├── telegram_notify.py         Telegram bot client (message/audio/document)
│       ├── tz_resolver.py             stored-IANA → city/state → Eastern default chain
│       ├── stats.py                   daily stats helpers
│       └── requirements.txt           fastapi, uvicorn, httpx, python-dotenv
│
├── audio/                             pre-rendered assets (TTS A/B test outputs)
│   └── test/                          tts-test.py output
│       ├── current/*.mp3              14 phrases at current TTS settings
│       └── index.html                 side-by-side audio player table
│   (NOTE: car-interior.ogg lives only on the VPS)
│
├── recordings/                        Jon voice cloning pipeline (gitignored)
│   ├── raw/                           64 source call recordings (mono 8 kHz)
│   ├── jon-only/                      61 per-call Jon-only WAVs (Deepgram diarized)
│   ├── prospect-only/                 56 per-call prospect-only WAVs
│   ├── suspect/                       3 quarantined (diarization mis-split)
│   ├── jon-combined.wav               2.4 GB raw WAV, 295 min Jon audio
│   ├── jon-combined.flac              622 MB lossless (verified bit-identical)
│   ├── jon-3min.mp3                   7.2 MB IVC-ready slice
│   ├── jon-3min.wav                   25 MB IVC-ready slice (PCM)
│   └── jon-30min.wav                  247 MB PVC-ready slice
│
├── scripts/
│   ├── push_squad.py                  idempotent build/update of the 3 stage assistants + Squad
│   ├── sync-vapi-prompt.py            push monolithic prompt to Vapi assistant (rollback flow)
│   ├── deploy-webhooks.sh             deploy src/webhooks/ to VPS
│   ├── setup-webhook-infra.sh         one-time VPS infra setup (nginx + systemd + certbot)
│   ├── sim-call.py                    LLM-vs-LLM simulator (Jon vs persona)
│   ├── judge-call.py                  plain-English Claude judge for sim transcripts
│   ├── run_daily_stats.py             cron-callable daily stats report
│   ├── extract-jon.py                 Deepgram diarization → Jon-only audio
│   ├── extract-prospect.py            Deepgram diarization → prospect-only audio
│   ├── stitch-jon-clean.py            stitch jon-only WAVs into a single re-clone source
│   ├── deep-clean-jon.py              audio cleanup pre-clone
│   ├── tts-test.py                    render test corpus through ElevenLabs for offline A/B
│   ├── audio-glitch-check.py          librosa glitch detection (platform-agnostic QA)
│   └── compare-jon.py                 cadence/pitch/vocab metrics vs reference Jon audio
│
├── docs/
│   ├── architecture.md                full system design
│   ├── call-types.md                  6 call type specifications
│   ├── prompt-engineering-guide.md    prompt rules (currently OpenAI-aligned, was Anthropic-aligned)
│   ├── local-development.md           daily workflow + naturalness tuning section
│   ├── voice-cloning.md               Jon clone pipeline
│   ├── system-summary.md              this document
│   ├── checkpoints.md                 rolling state log (newest first — authoritative)
│   ├── webhook-infrastructure.md      shared webhooks.c7a.io VPS docs
│   ├── sales-framework-handoff.md     legacy one-shot doc (content already integrated into stage prompts)
│   ├── base-script-original.md        Jon's original human script
│   ├── reference-points/              frozen rollback snapshots
│   ├── prompt-experiments/            staging research + principles writeups
│   └── superpowers/                   skills documentation
│
├── .venv-extract/                     local Python venv for offline scripts (extract-jon, tts-test, glitch-check, etc.)
│                                      libs: httpx, python-dotenv, librosa, numpy, scipy, soundfile
│
└── (Host-only, NOT in repo)
    /opt/webhooks/voicsito/            deployed copy of src/webhooks/*
    ├── .env                           prod credentials (Vapi API key, GHL PITs, ElevenLabs/Deepgram/OpenAI, VAPI_SQUAD_ID, Telegram bot)
    ├── static/                        firstmessage-opener.mp3, car-bg-v3.mp3 (nginx serves direct)
    ├── calls/<date>/<call-id>/        per-call recording + transcript + pushed.flag
    └── (logs via journalctl -u voicsito-webhooks)
    /etc/systemd/system/
    └── voicsito-webhooks.service      systemd unit running the FastAPI webhook server
    (TLS terminated by nginx + certbot; reverse-proxies 127.0.0.1:8081 → https://webhooks.c7a.io/voicsito/*)
```

---

## 8. Vapi resource IDs (live as of 2026-05-18)

| Resource | ID |
|---|---|
| Phone number `+1555010011` | `cb93e61d-d222-42c9-8fb2-74500dc13234` |
| Squad — Voicsito - Squad - Inbound Texts | `bea22dc5-24eb-42ed-ba81-f52cf1986ebf` |
| Stage 1 assistant — Voicsito - Stage 1 - Inbound Texts | `46e47e95-674a-4624-9029-69bb39c4daf0` |
| Stage 2 assistant — Voicsito - Stage 2 - Qualify | `7b2666e0-154d-4c85-82f0-07b4c51ad264` |
| Stage 3 assistant — Voicsito - Stage 3 - Book | `7600c2b6-d669-4f2e-8387-e371b0c193d4` |
| Monolithic assistant (rollback) | `261bf1a0-3527-4fcb-956c-b6e227d0bae1` |
| Tool — `update_prospect_info` (async) | `278ff15a-4c7f-47cf-854f-86c132290667` |
| Tool — `get_available_slots` | `4936203d-b9d6-4dc9-a46a-609fded29abb` |
| Tool — `mark_for_transfer` | `dcdba267-0cb0-4af2-8010-9b3ffecdd976` |
| Tool — `book_video_call` | `cdc4465d-e827-4453-8e29-819d6f6028f3` |

---

## Summary of state

The system is end-to-end functional for inbound Type 2 calls on a 3-stage Vapi Squad. As of 2026-05-18 the LLM is **OpenAI GPT-4.1** on every stage (`promptCacheRetention=24h`), the voice is **ElevenLabs Turbo v2.5** with stock voice ID `F2BOg7Ry3zTPV6Zd62Cd` (the Jon PVC was retired after diagnostic confirmed prospect-voice contamination in the training data; a re-clone from the diarized `recordings/jon-only/` slices is unblocked but not yet attempted). The speaking-plan endpointing knobs are tuned (`onNoPunctuationSeconds=0.4`, `waitSeconds=0.2`, `smartEndpointingEnabled=false`). Booking flow uses the GHL `transfer` tag → main-CRM auto-migration pattern via `mark_for_transfer` + polling `book_video_call` (5×3s = 15s ceiling on polling, 45s ceiling on the entire handler). CRM variables (`biz_type/business/city/state`) inject per-call via `assistant-request` → `assistantOverrides.variableValues` on the squad. End-of-call archiving + Telegram push (summary + transcript + audio) runs from `call_archive.py`. Background ambience: `car-bg-v3.mp3` (20-min mono, band-passed to phone codec range). SSH access via Tailscale (`ssh voicsito-vps`). LiveKit fully decommissioned. **Known regression from the Squad split:** the 12s nudge + 36s graceful hangup hooks on the monolith were not re-attached to the stage assistants — only `silenceTimeoutSeconds=60` remains. **Open issue:** phone-side latency (~600ms transport overhead vs ~100ms for WebRTC). Fix path is BYO Twilio number with Elastic SIP Trunk + IP allowlist — see `docs/checkpoints.md` for the latest open-issues list.
