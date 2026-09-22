# Vapi Migration Playbook

**Created:** 2026-05-05
**Status:** COMPLETED 2026-05-05. **Subsequent changes (through 2026-05-07):** LLM trialed Sonnet 4.6 → Haiku 4.5 → back to Sonnet 4.6 (quality testing won). TTS Flash v2.5 → Multilingual v2 (fidelity). Voice swapped to Jon PVC clone `LJ3VmFT7KIfEw9jSLImM` (from community library voice). Booking flow rewritten to use the GHL `transfer` tag → main-CRM auto-migration pattern via `mark_for_transfer` + polling `book_video_call`. Idle nudge ladder shipped. Phone number is `+1555010011` (Vapi-bundled — Twilio import is still the open path). Endpointing tuned to `transcriptionEndpointingPlan.onNoPunctuationSeconds=0.4` (Vapi default 1.5s was hidden bloat). Latest authoritative state lives in [`docs/checkpoints.md`](checkpoints.md). The text below is preserved as the original cutover playbook.
**Goal:** Cut Voicsito's realtime orchestration from self-hosted LiveKit Agents on the Hostinger VPS to **Vapi** (managed). Type 2 (Inbound Texts) goes first; the other 5 call types follow once Type 2 is stable.

**What changed:** the realtime media plane (SIP ingress, VAD, turn detection, STT/LLM/TTS streaming, voicemail detection, barge-in). Post-migration tunings: LLM model (Sonnet 4.6 → Haiku 4.5 → Sonnet 4.6), TTS model (Flash v2.5 → Multilingual v2), voice ID (community library → Jon PVC clone).
**What did not change:** Deepgram nova-3, the GHL client (`src/agent/ghl.py`), the system prompt (`src/agent/prompts/inbound_texts.py`), all the call-type specs (`docs/call-types.md`), the voice-cloning pipeline.

Estimated wall-clock: **2–3 days** for Type 2 cutover. Full effort to set up + test + tune is roughly **5 hours of focused work on Day 1**.

---

## Phase 0 — Prerequisites

Before starting Phase 1, confirm:

- [ ] **Vapi account** created (https://dashboard.vapi.ai), payment method on file
- [ ] **`VAPI_API_KEY`** added to `/Users/waseembaluch/CLAUDSITO/Voicsito/config/.env` and to the VPS `/opt/webhooks/voicsito/.env`
- [ ] **Twilio creds** confirmed valid: `TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`, `TWILIO_AUTH_TOKEN`, Elastic SIP trunk SID `TKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
- [ ] **ElevenLabs API key** valid + voice ID `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone, swapped 2026-05-07 from `ljX1ZrXuDIIRVcmiVSyR`) accessible from the account
- [ ] **Anthropic API key direct** (NEW — we were on OpenRouter; Vapi prefers direct keys). Provision in Anthropic console, add as `ANTHROPIC_API_KEY` to `.env`
- [ ] **Deepgram API key** valid (already in `.env`)
- [ ] **3 GHL PITs** still valid (Main, Texts CRM 1, Texts CRM 2)
- [ ] **A public HTTPS endpoint** to host the webhook server. nginx + certbot (already serving sales.c7a.io and others) is the TLS terminator; certbot auto-renews Let's Encrypt certs. Public path: `https://webhooks.c7a.io/voicsito/vapi/tool`
- [ ] **Vapi dashboard access** for at least one team member (whoever owns the assistant config)

If any of the above is missing, fix before Phase 1.

---

## Phase 1 — Vapi assistant + webhook server (Day 1, ~5 hours)

### 1.1 Create the Vapi assistant for Type 2

In the Vapi dashboard → Assistants → New:

- **Name:** `voicsito-inbound-texts`
- **Model:** Anthropic, `claude-sonnet-4-6`, `temperature=0.6`, `maxTokens=300`
- **System prompt:** paste the full contents of `src/agent/prompts/inbound_texts.py` `PROMPT` constant
- **Transcriber:** Deepgram `nova-3`, language `en`, `endpointing=150`
- **Voice:** ElevenLabs `eleven_multilingual_v2`, voice ID `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone), `stability=0.50`, `similarityBoost=0.90`, `style=0.0`, `useSpeakerBoost=true`, `autoMode=false`, `optimizeStreamingLatency=2`
- **First message:** `Hey, Jon here — who is this calling?`
- **First message mode:** `assistant-speaks-first`
- **Start speaking plan:** `waitSeconds=0.2`, `smartEndpointingEnabled=false`, `transcriptionEndpointingPlan.onNoPunctuationSeconds=0.4`
- **Stop speaking plan:** `numWords=2`, `voiceSeconds=0.2`
- **Background sound:** `office` (placeholder; can upload `car-interior.ogg` later)
- **Voicemail detection:** enabled
- **Voicemail message:** (write a one-liner per call type — Type 2: "Hey, this is Jon — sorry I missed you. Give me a call back at this number when you have a sec.")
- **End call function enabled:** true (Vapi gives the LLM a built-in `endCall` tool — replaces our previous custom one)

Save. Note the assistant ID (`asst_…`).

### 1.2 Define the tool set

Vapi calls our webhook for each tool. Three custom tools cover Type 2, plus Vapi's built-in `endCall` system tool for hangup. All three custom tools point at a single dispatch endpoint (`/vapi/tool`); the server reads `function.name` from the payload to route.

| Tool name | What it does |
|---|---|
| `update_prospect_info` | Write back name / business / email when captured during the call (also creates a Texts CRM contact on the fly when caller-ID lookup misses) |
| `get_available_slots` | Return next N free video-call slots from the GHL calendar `ULFPF484I2XcTfSR2uIC` |
| `book_video_call` | Book a slot, returns confirmation + ICS time |

Plus the system tool `endCall` (Vapi-built-in, no webhook handler needed).

For each tool, in Vapi: define name, description (what the LLM sees), parameter schema, and the same single webhook URL (`https://webhooks.c7a.io/voicsito/vapi/tool`). Vapi will sign each request — capture the signing secret to verify in our server.

### 1.3 Build the webhook server

New directory: `src/webhooks/`. Skeleton:

```
src/webhooks/
├── __init__.py
├── server.py               # FastAPI app, single /vapi/tool dispatch endpoint, /health, signature verification
├── tools/
│   ├── __init__.py
│   ├── update_prospect_info.py
│   ├── get_available_slots.py
│   └── book_video_call.py
└── requirements.txt        # fastapi, uvicorn[standard], httpx, python-dotenv
```

Each tool module exports an `async def handle(payload: dict) -> dict` that:
1. Pulls call context (Vapi sends `call.id`, `call.assistantOverrides`, etc.)
2. Imports `src/agent/ghl.py` and reuses the existing client functions verbatim
3. Returns a JSON-serializable result that matches Vapi's expected shape: `{"result": ..., "error": null}`

`server.py` responsibilities:
- Verify the Vapi `X-Vapi-Signature` header against the per-tool signing secret (HMAC-SHA256 over the raw body)
- Route to the right tool handler
- Log structured JSON with `call_id`, `tool_name`, `latency_ms`, `error`
- Expose `/health` returning 200 for the deploy script's TLS check

Local smoke test:
```bash
cd /Users/waseembaluch/CLAUDSITO/Voicsito
python -m venv .venv-webhooks
.venv-webhooks/bin/pip install -r src/webhooks/requirements.txt
.venv-webhooks/bin/uvicorn src.webhooks.server:app --reload --port 8081
curl -s http://localhost:8081/health
```

### 1.4 Write `scripts/sync-vapi-prompt.py`

Tiny helper:
```
Usage: sync-vapi-prompt.py <call_type>
  - imports src.agent.prompts.<call_type> and reads PROMPT
  - PATCH https://api.vapi.ai/assistant/<assistant_id> with {"model":{"messages":[{"role":"system","content": PROMPT}]}}
  - prints new assistant version
```

Assistant-ID-by-call-type map lives in `config/.env` as `VAPI_ASSISTANT_ID_INBOUND_TEXTS=asst_...`. Script reads it.

### 1.5 Write `scripts/deploy-webhooks.sh`

For `src/webhooks/`:
1. Validate Python syntax across `src/webhooks/`
2. rsync to `root@72.60.175.24:/opt/webhooks/voicsito/`
3. `systemctl restart voicsito-webhooks`
4. Wait 2 s, curl `https://webhooks.c7a.io/voicsito/health` → expect 200
5. Tail recent journalctl lines

---

## Phase 2 — Deploy webhook server with TLS, point Vapi at it (Day 1–2)

### 2.1 — TLS via nginx + certbot

nginx + certbot are the TLS terminator. The shared webhook infra (`/opt/webhooks-infra/bin/bootstrap.sh`) already installs them and provisions `webhooks.c7a.io`. Per-project route blocks live in `/etc/nginx/sites.d/webhooks/<project>.conf` and are imported by the main vhost.

See [`docs/webhook-infrastructure.md`](webhook-infrastructure.md) for the canonical layout.

### 2.2 systemd unit for the webhook server

`/etc/systemd/system/voicsito-webhooks.service`:
```ini
[Unit]
Description=Voicsito Vapi webhook server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/webhooks/voicsito
EnvironmentFile=/opt/webhooks/voicsito/.env
ExecStart=/opt/webhooks/voicsito/venv/bin/uvicorn src.webhooks.server:app --host 127.0.0.1 --port 8081
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Enable + start, verify `/health` returns 200 over HTTPS from outside the VPS.

### 2.3 Wire up Vapi tools

For each tool defined in 1.2, set the webhook URL to `https://webhooks.c7a.io/voicsito/vapi/tool` (a single dispatch endpoint — `function.name` from the Vapi tool-call payload is used to route internally). Save the signing secret in `/opt/webhooks/voicsito/.env` so `server.py` can verify.

### 2.4 Smoke test from Vapi's test console

Vapi dashboard → Assistant → "Test" — speak into the in-browser test, verify tool calls hit our webhook (check logs), verify responses come back, verify the LLM uses them. No phone calls yet.

---

## Phase 3 — Twilio cutover (Day 2)

This is the operationally riskiest step — we're flipping the SIP trunk's origination URI live.

**Two paths, pick one:**

### Option A — Import the toll-free number directly into Vapi
- Vapi dashboard → Phone Numbers → Import → Twilio
- Enter Twilio Account SID + Auth Token + the phone number
- Vapi takes over carrier routing for that number
- Pro: simplest config; Vapi handles all SIP plumbing
- Con: takes the number out of our trunk; if we want it back, we re-import to Twilio

### Option B — Keep the number on our Twilio Elastic SIP trunk, change origination URI
- Twilio Console → Elastic SIP Trunking → trunk `TK5897...` → Origination
- Change URI from `sip:<vps-ip>:5060` to the Vapi-provided SIP URI (Vapi gives one per assistant or per account)
- Pro: number ownership stays on our Twilio; reversible by changing the URI back
- Con: more SIP-config moving parts

**Recommended:** Option B for the first cutover (cleaner rollback path), Option A later if we want simpler ops.

### Cutover window
- Pick a low-traffic time (Type 2 is inbound-only and traffic is sporadic, so any off-hour works)
- Make the change, place an immediate test call to `+1555010012` to confirm routing reached Vapi
- Watch Vapi's call timeline for the test call — STT, LLM, tool calls, TTS
- If broken, revert the origination URI to the VPS — total downtime <5 min

---

## Phase 4 — Test calls, prompt iteration, voicemail tuning, ambience (Day 2–3)

### 4.1 Test-call protocol

Run **at least 10 real calls** before declaring Type 2 stable. For each:
- Note the perceived latency (does it feel snappy?)
- Note any cut-offs / barge-in misfires
- Note tool-call failures (slot lookup, booking)
- Save the Vapi call recording + transcript link

### 4.2 Prompt iteration

Edits flow either dashboard-first (fastest) or local-first. After any dashboard edit, copy back to `src/agent/prompts/inbound_texts.py` so the file remains the source of truth. Run `scripts/sync-vapi-prompt.py inbound_texts` to push from local.

### 4.3 Voicemail tuning

- Verify Vapi's voicemail detection actually fires on test voicemails (call from another phone, let the answering machine pick up)
- Tune `voicemailMessage` — short, in-character, ends with a callback ask
- Confirm the Vapi end-of-call webhook fires with `endedReason=voicemail` so we can branch correctly server-side

### 4.4 Ambience

Once basic flow is solid:
- Try `backgroundSound: office` for a few calls — does it feel right for a "Jon at his desk" persona?
- If yes, leave it
- If no, upload `audio/car-interior.ogg` to a CDN (R2) and set `backgroundSound: <url>` in the assistant config
- A/B by ear over 2–3 calls

### 4.5 Latency tuning

If turns feel sluggish:
1. Check `startSpeakingPlan.waitSeconds` — must be `0.4`, not the 1.5 default. **This is the #1 cause of slow-feel.**
2. Check `transcriber.endpointing` — `200` is right; `500` is way too long
3. Check tool latency in our webhook logs — `/tools/get_slots` over 1.5 s means GHL is the bottleneck and we need pre-cached slots

---

**Phase 5 (DONE 2026-05-05):** LiveKit Docker containers stopped, voicsito-agent.service disabled, src/agent/agent.py removed from repo.

---

## Cost estimate

At the planned **18,000 talk-minutes/month** (50 calls/day × 12 min), all-in monthly cost on Vapi is approximately **$2,025/mo**:

| Item | Monthly |
|---|---|
| Vapi platform fee (~$0.05/min × 18,000) | ~$900 |
| Twilio SIP minutes | ~$235 |
| Deepgram nova-3 (passthrough) | ~$77 |
| Anthropic Sonnet 4.6 direct (with prompt cache) | ~$60 |
| ElevenLabs Pro (Multilingual v2 + PVC seat) | ~$330 |
| Vapi recording storage / egress | ~$10 |
| R2 (recording mirror — optional) | ~$5 |
| Buffer (concurrency, voicemails, retries) | ~$408 |
| **Total** | **~$2,025/mo** |

Compared to the LiveKit-on-VPS estimate (~$710/mo), the delta is ~$1,300/mo — bought back from realtime ops time and reliability. Recheck at scale; Vapi platform fee is per-minute linear, so 500 calls/day → ~$15–18k/mo.

---

## Known gotchas

1. **`startSpeakingPlan.waitSeconds` defaults to 1.5 s.** This is the single most common reason a fresh Vapi assistant feels obviously-AI slow. Drop to 0.4 s with `smartEndpointingEnabled=true` from day one.
2. **Vapi has a concurrency cap per plan tier.** Past the cap, Vapi rejects calls at SIP ingress (no queueing). Track peak concurrent and pre-upgrade before promo blasts.
3. **Tool-call timeout is short** (~5–10 s). Our GHL slot lookup occasionally hits 3–4 s; keep the webhook fast or pre-cache.
4. **Anthropic Sonnet 4.6 direct uses a different rate-limit pool** than OpenRouter did. Watch the Anthropic dashboard for 429s during scale tests; request a tier bump preemptively.
5. **First-token caching:** Vapi handles Anthropic prompt caching when the system prompt repeats across turns within a call. Make sure the prompt doesn't change between turns (don't dynamically interpolate things into the system message — put dynamic context in tool returns or `assistantOverrides.variableValues`).
6. **Vapi's recording URL retention** is finite. Pull the URL from the end-of-call webhook and mirror to our R2 if we want long-term retention.
7. **Voice swap when Jon's PVC clears** — should be a single Vapi assistant config edit (`voice.voiceId`). Worth a regression test on tone after the swap.
8. **Twilio origination URI cutover drops in-flight calls** during the change. Pick a low-traffic window.

---

## Definition of done (per call type)

A call type's migration is "done" when:
- [ ] Vapi assistant exists, configured per the knobs in Phase 1
- [ ] All tools defined + webhook handlers shipped + signature verification works
- [ ] 10 consecutive real test calls without manual intervention
- [ ] Latency p50 measured at <1 s mouth-to-ear
- [ ] Voicemail detection fires correctly on at least one test
- [ ] End-of-call webhook fires + writes back to GHL (stage move + transcript URL)
- [ ] System prompt copied back to `src/agent/prompts/<call_type>.py` and committed
- [ ] `docs/system-summary.md` "Active hacks" / "Not built" sections updated to reflect the new state

After Type 2 hits these criteria for a full week, run Phase 5 and start Type 6.
