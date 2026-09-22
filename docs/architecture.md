# Voicsito — AI Voice Calling Agent System
**Project:** Replace Jon (human discovery caller) with AI agent for Cloud 7 Agency contractor outreach
**Status (2026-05-18):** Vapi migration complete. **3-stage Vapi Squad live** (split shipped 2026-05-11): Stage 1 INTEREST → Stage 2 QUALIFY → Stage 3 BOOK, all running OpenAI **GPT-4.1** (`promptCacheRetention=24h`). Voice is ElevenLabs **Turbo v2.5** stock voice `F2BOg7Ry3zTPV6Zd62Cd` (Jon PVC was retired after prospect-voice contamination diagnosed; re-clone from `recordings/jon-only/` is unblocked but pending). STT is Deepgram nova-3. Phone is `+1555010011` (Vapi-bundled — Twilio import is the open path to cut phone-side latency). State snapshots: [`docs/checkpoints.md`](checkpoints.md). Cutover playbook: [`docs/vapi-migration.md`](vapi-migration.md).
**Last updated:** 2026-05-18

---

## Quick links
- **The prompts (source of truth, pushed to each stage assistant via `scripts/push_squad.py`):**
  - `src/agent/prompts/shared_persona.py` — imported into every stage
  - `src/agent/prompts/stage1_inbound_texts.py` — Stage 1 INTEREST (per call type)
  - `src/agent/prompts/stage2_qualify.py` — Stage 2 QUALIFY (shared across call types)
  - `src/agent/prompts/stage3_book.py` — Stage 3 BOOK (shared across call types)
  - `src/agent/prompts/inbound_texts.py` — pre-split monolithic prompt (rollback target)
- **Stage spec:** [`Stages.md`](../Stages.md)
- **Webhook server (FastAPI, Vapi tool target):** `src/webhooks/`
- **Prompt engineering rules:** [`docs/prompt-engineering-guide.md`](prompt-engineering-guide.md)
- **Vapi migration playbook:** [`docs/vapi-migration.md`](vapi-migration.md)
- **All 6 call types:** [`docs/call-types.md`](call-types.md)
- **Original sales script:** [`docs/base-script-original.md`](base-script-original.md)
- **Local dev workflow:** [`docs/local-development.md`](local-development.md)
- **Test number (inbound):** +1555010011 (Vapi-bundled — Twilio import is the open path to migrate the legacy +1555010012)
- **VPS (webhook server + nginx):** root@72.60.175.24 (Hostinger KVM 4)
- **Latest checkpoint:** [`docs/checkpoints.md`](checkpoints.md)

---

## 1. Goal

Build a production AI voice agent that handles outbound discovery calls (and inbound fallback) to contractor prospects, qualifies them, handles objections, and books them onto a video call calendar in GHL — at SOTA quality and latency.

**Replaces:** Jon, freeing him for closing calls.

---

## 2. Use Case — 6 Distinct Call Types

See **[docs/call-types.md](call-types.md)** for full specs of each. Summary:

| # | Type | Direction | CRM | Trigger |
|---|---|---|---|---|
| 1 | Scheduled discovery | Outbound | Texts CRM 1/2 | Appointment time hits |
| 2 | Inbound — Texts | Inbound | Texts CRM 1/2 | Warm lead dials TF |
| 3 | Callback after miss | Inbound | Texts CRM 1/2 | Missed-outbound prospect rings back |
| 4 | Demo confirmation | Outbound | Main CRM | T-Xh before booked demo |
| 5 | Rebooking | Outbound | Main CRM | No-show or cancel state |
| 6 | Inbound — Main | Inbound | Main CRM | Already-booked prospect dials Main TF |

Each type has its own system prompt + tool set + CRM context + post-call behavior — but all share infrastructure (one Vapi assistant per type, all pointing at one webhook server, one Twilio SIP trunk). The "type" is detected at call connect via caller-ID lookup across all 3 CRMs (executed in our webhook server's call-start handler, returned to Vapi as dynamic prompt context).

**Build order:** Types 2 → 6 → 3 → 1 → 4 → 5.

### Out of scope (initially)
- Closing calls (Jon still does these on the booked video calls)
- Anything other than discovery + booking + confirm + rebook

---

## 3. Hard Constraints

| Constraint | Why |
|---|---|
| Stay on GHL LC Phone (no porting, no BYOT) | A2P/brand registration was lost once before during a port — non-negotiable |
| Use existing GHL toll-free numbers for outbound | Same numbers as SMS thread = preserves warm-lead trust |
| Orchestration on Vapi (managed) — webhook server self-hosted on Hostinger VPS (4 vCPU, 16GB, Boston, Ubuntu 24.04) | Vapi handles the realtime media plane (the part that's painful to operate); we keep our business logic in our own webhook server we control |
| API-first for AI components (no GPU available) | Quality > self-hosting at this scale |
| Voice currently ElevenLabs stock voice `F2BOg7Ry3zTPV6Zd62Cd` (Turbo v2.5). Jon PVC `LJ3VmFT7KIfEw9jSLImM` was retired after prospect-voice contamination diagnosed; pre-split monolith ran on Michael stock voice `ljX1ZrXuDIIRVcmiVSyR`. Re-clone from clean diarized `recordings/jon-only/` is unblocked but not yet attempted. | Brand consistency (currently degraded) |
| Direct REST API integration only — no n8n | Code is testable, version-controlled, debuggable |
| Compliance work parked (acknowledged risk) | Business decision — flagged in §10 |

---

## 4. System Architecture (Vapi — current)

### 4.1 Vapi orchestration — end-to-end flow

Vapi is a managed voice-agent platform. We hand it the prompt + tool definitions + provider keys, it handles the realtime cascade (STT/LLM/TTS streaming, VAD, turn detection, barge-in, voicemail detection) and calls back into our infrastructure only for business logic (CRM lookup, calendar booking, etc.) via webhook tools.

```
[Caller dials +1555010011]                            PSTN
         ↓
[Vapi-bundled number — no Twilio in path]                   Vapi cloud
         ↓ Vapi POSTs assistant-request to webhook (CRM lookup)
[FastAPI webhook server — _handle_assistant_request]        our infra
         │ returns { squadId, assistantOverrides.variableValues:
         │           { biz_type, business, city, state } }
         ↓
[Vapi loads Squad: Voicsito - Squad - Inbound Texts]        Vapi cloud
   ├── Member 1: Voicsito - Stage 1 - Inbound Texts        (INTEREST)
   ├── Member 2: Voicsito - Stage 2 - Qualify              (QUALIFY)
   └── Member 3: Voicsito - Stage 3 - Book                 (BOOK)
   Each stage runs:
   ├── Vapi-managed Deepgram nova-3 streaming STT
   ├── Vapi-managed OpenAI GPT-4.1 (Vapi-bundled, 24h cache)
   ├── Vapi-managed ElevenLabs Turbo v2.5 (voice F2BOg7Ry3zTPV6Zd62Cd)
   ├── VAD + turn detection + barge-in (Vapi internal)
   ├── Background ambience car-bg-v3.mp3 (Vapi-side)
   └── Tool-call dispatcher
         │ (Stage 1 & 2: update_prospect_info + handoff
         │  Stage 3: update_prospect_info, get_available_slots,
         │           mark_for_transfer, book_video_call)
         ↓ HTTPS POST (Bearer-authenticated)
[FastAPI webhook server — src/webhooks/server.py]           our infrastructure
   ├── Validates Vapi-signed request
   ├── Routes to GHL client (src/agent/ghl.py — reused as-is)
   ├── Returns JSON tool result
         ↓ response back to Vapi
[Vapi resumes LLM → speaks ElevenLabs reply]
         ↓
[Caller's phone]
```

**Where things run now:**

| Component | Location |
|---|---|
| PSTN ingress / egress | Twilio Elastic SIP Trunk (numbers + minutes — unchanged) |
| Realtime media + STT/LLM/TTS orchestration | Vapi (managed) |
| Provider keys (Deepgram / ElevenLabs / OpenAI) | Provided to Vapi; Vapi makes the upstream calls |
| Webhook server (FastAPI) | Hostinger VPS or any TLS-terminated host (we keep VPS for now) |
| GHL CRM, GHL Calendar, GHL telephony for inbound forward | GHL cloud (unchanged) |
| Voice cloning pipeline (`recordings/`, `scripts/extract-jon.py`) | Local Mac, offline (unchanged) |

**Outbound call flow (Vapi):**

```
[Scheduled call time hit in GHL workflow]
         ↓
[GHL workflow webhook → our orchestrator (FastAPI on VPS)]
         ↓
[Orchestrator pulls contact context from GHL API
 → calls Vapi REST API: POST /call with assistantId + assistantOverrides
   (prompt variables, tool config, customer phone)]
         ↓
[Vapi originates the call out through Twilio SIP trunk
 (our toll-free as caller ID — preserves SMS thread trust)]
         ↓
[Same in-call cascade as inbound — Vapi runs the loop, hits our webhooks
 for CRM tools, books calendar, ends call]
         ↓
[Vapi end-of-call webhook → our server → GHL writeback
 (stage move, custom fields, transcript URL, recording URL)]
```

**Inbound call flow (Vapi):**

```
[Prospect dials GHL TF number]
         ↓
[GHL inbound flow → forwards to our Twilio TF (unchanged)]
         ↓
[Twilio SIP origination → Vapi inbound endpoint (per-number assistant routing)]
         ↓
[Vapi runs assistant for that number's call type
 → first webhook tool: lookup_contact_by_ani(phone) → our server → GHL → returns context
 → assistant loads prompt with context interpolated
 → conversation loop, tool calls, end call]
```

### 4.2 Why Vapi (vs. continued self-host)

- Realtime media plane is the most operationally painful piece — Vapi runs it for us
- Sub-700 ms mouth-to-ear is achievable with default-config tuning, no custom VAD work
- Same providers, same voice ID — zero quality regression on the components we care about
- Frees us from segment-race / mid-word-stutter LiveKit class of bugs
- Concurrency scales without us splitting the media plane across VPSes
- Trade-off: we lose deep control of the realtime layer; mitigated by keeping prompt + tools + business logic on our side

### 4.3 What stays the same

- Vapi-bundled phone number `+1555010011` (Twilio import deferred)
- Deepgram nova-3 streaming STT
- The GHL client (`src/agent/ghl.py`) — reused unchanged from the FastAPI webhook server
- Voice cloning pipeline (`recordings/`, `scripts/extract-jon.py`, `compare-jon.py`)
- All 6 call type specifications (`docs/call-types.md`)

### 4.4 What changes

| Was | Now |
|---|---|
| LiveKit Server + LiveKit-SIP + Redis on VPS via Docker Compose | Vapi (managed) |
| `voicsito-agent.service` Python worker (LiveKit Agents) | Vapi Squad of 3 stage assistants + FastAPI webhook server |
| `agent.py` — sessions, VAD, turn detector, TTS plumbing | Configured in Vapi assistant JSON (`startSpeakingPlan`, `stopSpeakingPlan`, `voice.*`, etc.) |
| Single monolithic system prompt | 3 stage prompts sharing `shared_persona.py`; built and pushed via `scripts/push_squad.py` |
| Anthropic Sonnet 4.6 via OpenRouter (LiveKit era) | OpenAI GPT-4.1 (Vapi-bundled, `promptCacheRetention=24h`). Anthropic Sonnet 4.6 was production through 2026-05-07 — swapped because Vapi doesn't expose Anthropic prompt caching. |
| Tool calls executed inside the Python agent process | Tool calls = HTTPS webhooks → `src/webhooks/server.py` → reuses `ghl.py` |
| ElevenLabs Flash v2.5 / Multilingual v2 with Jon PVC `LJ3VmFT7KIfEw9jSLImM` | ElevenLabs Turbo v2.5 with stock voice `F2BOg7Ry3zTPV6Zd62Cd`. Jon PVC was retired after diagnostic confirmed prospect-voice contamination in training data. |
| Deploy = `scripts/deploy-agent.sh` (scp + systemctl restart) | Deploy = `scripts/push_squad.py` (Vapi assistants) + `scripts/deploy-webhooks.sh` (webhooks) |
| `BackgroundAudioPlayer` car-interior at 0.08 vol in agent code | Vapi `backgroundSound` config field (`car-bg-v3.mp3`) |

---

## 5. Stack — Final Picks

| Layer | Choice | Why |
|---|---|---|
| **Telephony origination** | Vapi-bundled inbound number `+1555010011`. Twilio Elastic SIP Trunk import is the open path to cut phone-side latency. | A2P preserved on the legacy GHL TF; Vapi number gives fastest path to ship |
| **Realtime orchestration** | **Vapi Squad** (3 stage assistants) | Handles SIP ingress, STT/LLM/TTS streaming, VAD, turn detection, barge-in, voicemail detection — claimed sub-700 ms with proper config |
| **STT** | Deepgram Nova-3 streaming (configured inside Vapi) | ~150 ms endpointing, best phone-audio accuracy |
| **LLM** | **OpenAI GPT-4.1** (Vapi-bundled), `temperature=0.6`, `maxTokens=350`, `promptCacheRetention=24h` | Auto-caching cuts TTFT on cached turns to ~80–150 ms. Anthropic Sonnet 4.6 was the prior choice; swapped because Vapi doesn't expose Anthropic caching. |
| **Prompt caching** | OpenAI auto-cache via Vapi `promptCacheRetention=24h` | Cold-cache on every turn would blow the latency budget. Confirmed working (was 0 hits on Anthropic). |
| **TTS** | **ElevenLabs Turbo v2.5**, voice ID `F2BOg7Ry3zTPV6Zd62Cd` (stock), `stability=0.30, similarityBoost=1.00, style=1.00, speed=1.00, useSpeakerBoost=true` | Faster TTFB than Multilingual v2 (~120-220 ms). Used pragmatically while Jon clone is retired. |
| **TTS (future — Jon clone)** | ElevenLabs PVC re-clone from `recordings/jon-only/` (61 diarized files, clean) | Original PVC retired due to prospect-voice contamination. Re-clone unblocked, not yet done. |
| **Voicemail detection** | Vapi-managed (built-in detector + voicemail message field) | No more custom VAD-silence + beep code |
| **Webhook server (tool target)** | FastAPI (Python) on VPS at `src/webhooks/server.py` | Receives Vapi tool calls, proxies to GHL, returns JSON results |
| **GHL client** | `src/agent/ghl.py` — reused as a library | Same module, called from the webhook server |
| **Database** | Postgres (planned, deferred) | Per-call state, transcripts cached locally for analytics |
| **Recording storage** | Vapi-stored recordings + downloadable URLs in end-of-call webhook; mirrored to R2 on demand | Vapi handles storage; we pull URLs into our analytics pipeline |
| **Monitoring** | Vapi dashboard (call timelines, latency, transcripts) + our own structured logs from the webhook server | Vapi gives end-to-end timeline per call out of the box |
| **Logging** | Webhook server: structured JSON, `call_id` correlated to Vapi's call ID | End-to-end debug per call |

### 5.1 Why cascade (not GPT-4o Realtime)

Realtime gives lowest latency but:
- Locked into OpenAI's voices → kills voice cloning
- Less control over each pipeline stage
- Worse for fine-tuning objection handling on Jon's actual call data

Cascade trades ~300ms latency for full voice clone + tunable per-layer behavior. Right call for "highest quality."

### 5.2 TTS history: Flash → Multilingual → Turbo

- **Flash v2.5** (initial choice): ~75 ms TTFB but PVC clone fidelity suffered on phone audio.
- **Multilingual v2** (2026-05-07 → 2026-05-11): the production TTS during the Jon PVC era. Better clone fidelity, ~200-400 ms TTFB, `autoMode=false` to honor stability/similarity settings.
- **Turbo v2.5** (current, 2026-05-13 retune): swapped when the voice was changed to the stock `F2BOg7Ry3zTPV6Zd62Cd`. Turbo trades a small amount of fidelity for ~100 ms faster TTFB, which is the right shape for a stock voice (no PVC to protect). Settings: `stability=0.30, similarityBoost=1.00, style=1.00, speed=1.00, useSpeakerBoost=true`.

Wall on phone-side fidelity remains the 8 kHz μ-law codec — only fix there is BYO Twilio SIP trunk with proper edge pinning.

### 5.3 Latency budget on Vapi (per turn, mouth-to-ear)

Vapi's claimed end-to-end latency is sub-700 ms with `startSpeakingPlan.waitSeconds` tuned and a fast TTS. Realistic budget given our current stack:

| Stage | Latency |
|---|---|
| Deepgram endpointing (Vapi-managed) | 150–250 ms |
| `startSpeakingPlan.waitSeconds` (default 1500 ms — tuned to 200 ms) | 200 ms |
| `transcriptionEndpointingPlan.onNoPunctuationSeconds` (default 1500 ms — tuned to 400 ms) | 400 ms |
| OpenAI GPT-4.1 first token | 250–500 ms cold / 80–150 ms cached (24h cache retention) |
| ElevenLabs Turbo v2.5 TTFB | ~120–220 ms |
| Vapi → PSTN egress | 100–200 ms |
| **Realistic p50 mouth-to-ear (after tuning + cached LLM)** | **~600–900 ms** |
| **p95** | **~1200 ms** |

Two things to know:
- Vapi defaults `startSpeakingPlan.waitSeconds` to 1500 ms — which is the dominant contributor to perceived delay if untouched. Tune in Phase 4 of the cutover (see [`docs/vapi-migration.md`](vapi-migration.md)).
- Pure provider stack roundtrip (~700 ms) matches our LiveKit best case after months of tuning. Vapi gets us there with config rather than code.

---

## 6. Critical Gaps Acknowledged (from swarm audit)

These are KNOWN before build, with mitigation built into Phase 1+:

| # | Gap | Mitigation |
|---|---|---|
| G1 | Twilio AMD doesn't work in our topology (we don't originate) | VAD silence + beep tone detection on incoming audio |
| G2 | SIP `From` header = GHL TF, not prospect | Pre-push call_id via webhook, match on custom SIP param or to_uri |
| G3 | GHL has no "true bridge" action | Use GHL "Call → User" with SIP endpoint registered as user — verified before build |
| G4 | Single VPS = SPOF | Phase 1 accepts. Phase 4 splits media plane to 2nd VPS when concurrent > 10 |
| G5 | Realistic concurrent capacity = 8–12, NOT 30–40 | Sufficient for current 5–10 peak. Monitor; split when exceeded |
| G6 | Voice clone source quality capped by 8kHz phone audio | Record Jon fresh on real mic, 30 min, quiet room — Phase 0 deliverable |
| G7 | GHL webhooks unreliable | Idempotency keys + 15-min reconciliation poll on critical events |
| G8 | No native backchannels in cascade | Custom orchestration layer with VAD-triggered low-latency audio (Phase 3) |
| G9 | Disk pressure within 6–9 months | Recordings offloaded to R2 from day 1 |
| G10 | API single-vendor dependency | Circuit breakers + fallback chain (Whisper, OpenAI, Cartesia) — Phase 3 |
| G11 | Multi-tenant for 2 GHL inbound instances | Location-aware data model from day 1 |
| G12 | Cost runaway risk | Per-call token cap, max-turns guard, TTS char limit, daily spend alert — Phase 1 |
| G13 | Deployment kills active calls | Resolved: Vapi runs the media plane; webhook server can roll without dropping in-flight calls |

---

## 7. Build Phases

### Phase 0 — Data + voice prep (PARKED — moved to last)
- [ ] Bulk-download all 1000+ recordings from GHL via API
- [ ] Run Deepgram diarization on entire corpus → split Jon vs prospect
- [ ] Spot-check diarized clips manually (DER reality is 8–15%, contamination risk real)
- [ ] **Record Jon fresh on a real mic — 30 min, quiet room, broadcast quality**
- [ ] Train ElevenLabs PVC on the FRESH recording (not phone audio)
- [ ] Extract objection patterns from transcript corpus → seed objection library
- [ ] Statistical analysis: avg call length, where bookings happen, where calls die

### Phase 1 — Core agent loop (DONE)
- [x] Provision Twilio SIP DID + trunk on our own Twilio account (`+1555010016`, trunk `TKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`)
- [x] Vapi Squad for Type 2 (Inbound Texts) configured: Deepgram nova-3 → OpenAI GPT-4.1 (Vapi-bundled, 24h cache) → ElevenLabs Turbo v2.5 (stock `F2BOg7Ry3zTPV6Zd62Cd`). 3 stage assistants wired together.
- [x] Manual outbound test call working end-to-end (validated 2026-05-01)
- [x] Inbound test call working end-to-end (CRM 2 forwards `+1555010012` → AI)
- [x] Prompt caching via OpenAI 24h cache retention (confirmed working; Anthropic was tried first, never produced hits on Vapi)
- [ ] Cost guardrails: per-call token cap, max turns, TTS char limit (not yet wired)

### Phase 2 — GHL integration (IN PROGRESS — Type 2 done; others pending)
- [x] All 3 GHL CRM PITs validated; location IDs verified (Main: G1yFGT1liEZgvT3dYgEx, Texts1: 9U157i1e8MRql0vtiDi2, Texts2: Z7oeWKLyzFp85JQN346m)
- [x] Caller-ID lookup across CRMs (`ghl.lookup_contact_in_texts_crms` for Type 2)
- [x] Filter scraped junk (contacts with empty name/business)
- [x] On-the-fly contact creation when caller is unknown (`ghl.create_contact_in_texts_crm`)
- [x] CRM update on real name/business capture (`ghl.update_prospect_fields` — handles scraped firstName=business case automatically)
- [x] GHL Video Call Calendar API booking (`ghl.book_video_call`, calendar `ULFPF484I2XcTfSR2uIC`)
- [x] Free-slots query (`ghl.get_available_slots`)
- [x] Type 2 system prompt (`src/agent/prompts/inbound_texts.py`) deployed
- [ ] Type 1 (Outbound Discovery) — same flow, different opener; needs GHL "Call → User" mechanism live-tested
- [ ] Type 3, 4, 5, 6 prompts — separate files per call type
- [ ] Post-call writeback: stage move, custom fields, transcript note (planned)
- [ ] Webhook reconciliation polling for unreliable GHL webhooks (planned)

### Phase 3 — Production hardening (NOT STARTED)
- [ ] Voicemail detection tuning (Vapi built-in) + double-dial logic via GHL workflow
- [ ] Recording fetch from Vapi end-of-call webhook + push to internal sales dashboard
- [ ] Disqualification logic (agent ends call when prospect clearly not fit)

### Phase 4 — Observability + iterate (PARTIAL)
- [x] Vapi dashboard provides per-call timelines (STT segments, LLM tokens, TTS chunks, tool-call latency) out of the box
- [ ] Prometheus + Grafana on VPS (webhook-side latency, error rates)
- [ ] OpenTelemetry traces with `call_id` correlation
- [ ] Synthetic call canary every 15 min
- [ ] Alerting: webhook failures, latency spikes, audio underruns
- [ ] Postgres PITR + off-VPS backups
- [ ] Sales dashboard feedback loop drives prompt refinement (sales dashboard not built yet)

---

## 8. Operating Cost

**Volume assumption:** 50 calls/day × 12 min average = ~18,000 talk-minutes per month.

### 8.1 Vapi (current — 2026-05-05)

Vapi bills per-minute on top of the underlying provider passthrough. Order-of-magnitude estimate:

| Item | Monthly |
|---|---|
| Vapi platform fee (~$0.05/min × 18,000 min) | ~$900 |
| Vapi-bundled inbound number minutes | ~$235 (will shift to Twilio Elastic SIP Trunk on import) |
| Deepgram nova-3 STT (passthrough through Vapi) | ~$77 |
| OpenAI GPT-4.1 (Vapi-bundled, with 24h cache) | ~$60 (cached turns are ~10% the price of cold) |
| ElevenLabs Pro (Turbo v2.5 + PVC seat for re-clone) | ~$330 |
| Webhook server VPS (already paying) | $0 marginal |
| Vapi recording storage / egress | ~$10 |
| Cloudflare R2 (recording mirror — optional) | ~$5 |
| Buffer for Vapi overage (concurrency, voicemails, retries) | ~$408 |
| **Total ongoing (all-in)** | **~$2,025/mo** |

**At 500 calls/day:** ~$15–18k/mo (Vapi platform fee scales linearly). Still cheaper than 1 SDR salary; the trade is "managed convenience" cost vs. the on-call ops burden of self-hosting.

Previous self-host estimate (~$710/mo on LiveKit) is no longer applicable.

---

## 9. Access / Resources Status

All access acquired. Credentials live in `config/.env` (gitignored) and on the VPS at `/opt/webhooks/voicsito/.env`:
- [x] 3 GHL Private Integration Tokens (Main + Texts CRM 1 + Texts CRM 2)
- [x] All 3 GHL location IDs verified
- [x] SSH key access to VPS (`~/.ssh/voicsito_vps`, deployed to `root@72.60.175.24`)
- [x] Twilio account (Account SID: `ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`, number `+1555010016`)
- [x] OpenAI access via Vapi (no separate key needed; Anthropic key kept on file as fallback)
- [x] Deepgram API key
- [x] ElevenLabs API key (Creator/Starter tier — upgrade to Pro before Jon PVC re-clone)
- [x] Telegram bot token + channel ID (for call started / ended push notifications)
- [ ] Sales dashboard webhook endpoint URL + auth token (sales dashboard not yet built — push integration deferred)
- [ ] **Time on Jon's calendar to record the clean voice sample (30 min)** — Phase 0, parked

---

## 10. Parked / Out of Scope (acknowledged risk)

**Compliance** — flagged but parked per business decision:
- TCPA / FCC AI voice ruling (PEWC consent required)
- State AI disclosure laws (CA, UT, CO, others)
- Voice clone licensing agreement with Jon
- Recording disclosure (two-party consent states)
- DNC scrubbing
- Carrier spam labeling on TF (Hiya, Truecaller)

**To revisit before scaling past pilot.** Document acknowledged in this section so the trade-off is explicit.

---

## 11. Success Criteria

**Phase 1 completion:**
- 1 successful end-to-end test call: trigger → bridge → conversation → graceful end
- Latency p50 < 1500ms, p95 < 2200ms

**Phase 3 completion (production-ready):**
- 10 consecutive live calls without manual intervention
- Booking flow successfully creates GHL appointment in correct calendar
- Voicemail flow leaves correct recording on 2nd attempt
- Inbound + outbound both working
- All recordings uploaded, transcripts pushed to dashboard

**Production validation (post-launch, 2 weeks):**
- Booking conversion rate ≥ 80% of Jon's baseline (measured against historical)
- Call completion rate ≥ 90% (no mid-call drops/failures)
- Prospect "is this AI?" rate < 30% (informal — measured by transcript review)
- Zero compliance incidents (parking acknowledged)

---

## 12. Open Decisions

| Decision | Status | Owner |
|---|---|---|
| GHL "Call → User" SIP registration mechanism — does it accept external SIP URI as a user's phone? | Needs live validation before Phase 1 | Voicsito |
| Cloudflare R2 vs AWS S3 for recordings | Default R2 (cheaper, no egress) — confirm | User |
| Sales dashboard webhook contract (payload shape, auth) | Needs spec from user | User |
| Disqualification thresholds (when does agent end call) | Define in Phase 3 with real call data | Voicsito |
| Backchannel aggressiveness (how often to "mhm") | Tune in Phase 3 with A/B | Voicsito |

---

## 12.5. Repository Layout

```
/Users/waseembaluch/CLAUDSITO/Voicsito/
├── docs/
│   ├── architecture.md             — this doc
│   ├── system-summary.md           — shareable technical summary (Vapi)
│   ├── vapi-migration.md           — cutover playbook (Phases 0–5)
│   ├── local-development.md        — daily Vapi edit/deploy/test workflow
│   ├── call-types.md               — all 6 call types with full specs
│   ├── voice-cloning.md            — Jon clone pipeline (platform-agnostic)
│   ├── base-script-original.md     — Jon's original sales script
│   └── prompt-engineering-guide.md — rules for writing call-type prompts
├── src/
│   ├── agent/                      — shared library code (GHL client + prompts)
│   │   ├── ghl.py                  — GHL API client (used by webhooks)
│   │   └── prompts/
│   │       ├── __init__.py         — get_prompt(call_type) registry
│   │       └── inbound_texts.py    — Type 2 prompt (synced to Vapi; SOURCE OF TRUTH)
│   └── webhooks/                   — Vapi webhook server
│       ├── server.py               — FastAPI app, single /vapi/tool dispatch endpoint, Vapi signature verification
│       ├── tools/                  — handlers per Vapi tool (update_prospect_info, get_available_slots, book_video_call)
│       └── requirements.txt        — fastapi, uvicorn, httpx, etc.
├── scripts/
│   ├── sync-vapi-prompt.py         — push src/agent/prompts/*.py to the Vapi assistant via API
│   ├── deploy-webhooks.sh          — deploy src/webhooks/ to host
│   ├── extract-jon.py              — Deepgram diarization → Jon-only audio
│   ├── extract-prospect.py         — sister of extract-jon.py
│   ├── tts-test.py                 — offline ElevenLabs A/B harness
│   ├── audio-glitch-check.py       — librosa glitch detector
│   └── compare-jon.py              — voice-clone metrics vs. reference
├── recordings/                     — Jon voice-cloning corpus (gitignored, unchanged)
├── config/
│   ├── .env                        — credentials (gitignored)
│   └── .env.example                — template
└── CLAUDE.md                       — project rules for Claude Code
```

**Workflow:** edit prompt or webhook code locally → either sync to Vapi (`scripts/sync-vapi-prompt.py`) or deploy webhook server (`scripts/deploy-webhooks.sh`) → test by calling `+1555010012`. See `docs/local-development.md` for full workflow.

## 13. Glossary

- **PVC** — ElevenLabs Professional Voice Clone (high-quality cloned voice)
- **STT** — Speech-to-Text
- **TTS** — Text-to-Speech
- **VAD** — Voice Activity Detection
- **AMD** — Answering Machine Detection (Twilio feature)
- **SIP** — Session Initiation Protocol (telephony signaling)
- **DID** — Direct Inward Dial (a phone number, in telecom-speak)
- **TF** — Toll-Free
- **A2P** — Application-to-Person (SMS registration framework)
- **PEWC** — Prior Express Written Consent (TCPA standard)
- **DER** — Diarization Error Rate
- **MOS** — Mean Opinion Score (audio quality metric)
- **TTFB** — Time To First Byte
- **PITR** — Point-In-Time Recovery (DB backup strategy)
- **GHL** — GoHighLevel (CRM)
- **PVC** — Professional Voice Clone
- **PSTN** — Public Switched Telephone Network
- **B2BUA** — Back-to-Back User Agent (SIP topology)
- **ANI** — Automatic Number Identification (caller's number)
- **CNAM** — Calling Name (caller ID display name)
