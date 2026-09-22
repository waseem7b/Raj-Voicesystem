# Voicsito — Full System Breakdown

> Generated 2026-06-01 from a source-level audit of the repo. Where the docs and
> the actual code disagree, this document reflects **what's in the source**, with
> discrepancies flagged. Citations are `file:line`.

**One-paragraph overview:** Voicsito is an inbound AI voice-sales agent. A prospect
calls a phone number, Vapi (a managed voice-AI cloud) answers and runs a 3-stage
scripted sales conversation as a persona named "Jon," and a self-hosted FastAPI
webhook server brokers all the business logic into GoHighLevel (the CRM + calendar).
The entire realtime media pipeline (speech-to-text, the LLM, text-to-speech) runs
*inside Vapi* — the repo's code never calls those providers per-turn; it only
configures them and handles tool calls + CRM work.

---

## 1. Architecture & Call Flow

**Summary:** Two planes. The **realtime plane** is 100% Vapi cloud (telephony, STT,
LLM, TTS, turn-taking, the 3-stage squad orchestration). The **business-logic plane**
is the FastAPI server on a Hostinger VPS, which Vapi calls via webhooks to do CRM
lookups and bookings against GoHighLevel. A call is routed *dynamically* — the phone
number has no assistant bound to it; Vapi asks the server "who should handle this
call?" on every call.

End-to-end trace (verified against `src/webhooks/server.py`):

```
Caller dials +1555010011  ─────────────────────────────┐
                                                              ▼
Vapi cloud answers, POSTs "assistant-request" to ───►  /vapi/server-events
   webhooks.c7a.io/voicsito/vapi/server-events           (_handle_assistant_request, server.py:898)
                                                              │ GHL phone lookup (2.5s timeout, fail-open)
                                                              │ returns {squadId, variableValues:{biz_type,business,city,state}}
                                                              ▼
Vapi loads the Squad ──► Stage 1 INTEREST ──handoff──► Stage 2 QUALIFY ──handoff──► Stage 3 BOOK
   (GPT-4.1, ElevenLabs voice, Deepgram STT — all in Vapi)                              │
                                                              tool calls (POST /vapi/tool)
                                                                                        ▼
                                              server.py dispatcher ──► src/agent/ghl.py ──► GoHighLevel
                                              (update_prospect_info, get_available_slots,
                                               mark_for_transfer, book_video_call)
```

Key design points:

- **Dynamic routing toggle:** `_build_routing_response` (`server.py:882`) returns a
  `squadId` when env `VAPI_SQUAD_ID` is set, else falls back to the monolithic
  `assistantId`. Rollback = unset one env var, no code change.
- **One CRM lookup per call**, done at `assistant-request`, cached in an in-memory
  dict keyed by Vapi `call.id` so downstream handlers don't re-hit GHL.
- **Stage handoff** is an inline Vapi `handoff` tool (LLM-fired), not a webhook — it
  carries conversation context forward and extracts structured variables. Stage 3 is
  terminal (no handoff).
- **Everything fails open:** the assistant-request handler can never crash a call
  (outer try/except returns the monolithic assistant on any error).

**Where it runs:** Vapi cloud (media + AI) · Hostinger VPS (FastAPI bridge) · GHL
cloud (3 CRMs + Video Call calendar + the texts→main transfer automation, which lives
in the GHL UI, not this repo).

---

## 2. External API Connections

**Summary:** At runtime the code only calls **two** external services — GoHighLevel
and Telegram. Everything else (LLM, STT, TTS, telephony) is delegated to Vapi, whose
dashboard holds those provider keys. A handful of services are declared in the env
template but have **zero consuming code** (dead config).

| Service | Used for | How connected | Who calls it |
|---|---|---|---|
| **GoHighLevel / LeadConnector** | CRM lookup, contact create/update, tagging, calendar slots, booking | `httpx` async → `services.leadconnectorhq.com`, per-CRM Bearer PIT | **Runtime** (`ghl.py`) |
| **Telegram Bot API** | Operator notifications (call started/ended, transcript, recording) | `httpx` → `api.telegram.org`, token in URL | **Runtime** (`telegram_notify.py`) |
| **Vapi** | Voice orchestration | REST `api.vapi.ai` (Bearer) — *scripts only*; Vapi calls *back* via webhooks | Scripts (deploy) + inbound webhooks |
| **OpenAI GPT-4.1** | Production LLM | Configured in Vapi, keyed in Vapi | Vapi cloud |
| **Deepgram nova-3** | STT | Configured in Vapi (also direct in offline audio scripts) | Vapi cloud |
| **ElevenLabs** | TTS | Configured in Vapi (also direct in `tts-test.py`) | Vapi cloud |
| **Google** | Voicemail detection | Configured in Vapi | Vapi cloud |
| **OpenRouter / Anthropic** | LLM-vs-LLM simulator + judge | `anthropic` SDK w/ base-URL override | Offline tooling only |

**Dead/aspirational config** (declared in `config/.env.example`, no code reads it):
**Twilio** (telephony delegated to Vapi), **Cloudflare R2**, **Postgres**, **Redis**,
the **sales-dashboard webhook**, and even the **GHL location-ID env vars** (the
location IDs are hardcoded as string literals in `ghl.py:18,23,28`).

**Three GHL CRMs:** main (`G1yFGT1...`), texts1, texts2. The "transfer" flow tags a
contact in a texts CRM → a GHL automation copies it to the main CRM → the code polls
main for it before booking.

---

## 3. Webhook Server Internals

**Summary:** A stateless FastAPI app with three endpoints: `GET /health`,
`POST /vapi/tool` (all tool calls), `POST /vapi/server-events` (phone-level events).
Both POST endpoints always return HTTP 200 so errors surface to the LLM instead of
breaking the call. Support modules handle archiving, message formatting, Telegram,
timezone resolution, and stats.

- **Signature auth** (`_verify_signature`, `server.py:149`): HMAC-SHA256 or
  literal-secret match via `x-vapi-signature`/`x-vapi-secret`. ⚠️ **Disabled entirely
  when `VAPI_WEBHOOK_SECRET` is unset** — and that var isn't in the env template (see
  Issues).
- **Tool handlers** (5, confirmed): `update_prospect_info` (fire-and-forget early
  capture), `get_available_slots` (tz-resolved, tags every offered slot ISO for
  hallucination rejection), `mark_for_transfer` (tags `transfer`), `book_video_call`
  (the critical path), `end_call` (stub).
- **`book_video_call`** (`server.py:485`) is the heavy one: rejects hallucinated/past
  slots → ensures texts-CRM contact + transfer tag → **polls main CRM (5×3s, hard 15s
  ceiling)** → writes captured fields onto the main contact → books a 1-hour
  appointment → tags `voicsito-booked`. Wrapped in a **45s ceiling** so it fails
  cleanly before Vapi's 60s tool budget.
- **Dispatcher** runs "priming" tools (`update_prospect_info`, `get_available_slots`)
  first so their cache writes land before `book_video_call` reads them.
- **Support modules:** `call_archive.py` (per-call disk archive, skips <30s calls),
  `call_summary.py` (outcome classification: BOOKED / AGREED-NOT-BOOKED / HUNG UP /
  DISQUALIFIED / NOT INTERESTED), `tz_resolver.py` (IANA → city → state → Eastern
  default; never Bogota), `stats.py` (daily digests — *not* imported by the server,
  runs separately).

---

## 4. Agent / Prompt Design (the "brain")

**Summary:** Each stage is a separate Vapi assistant whose system prompt = a shared
persona module + a stage-specific module, composed at import time. Only **one** of the
six designed call types (Type 2, "Inbound Texts") is actually built in code.

- **4 modules** in `src/agent/prompts/`: `shared_persona.py` (the "Jon" persona,
  voice/TTS formatting rules, anti-robot rules, CRM vars) + `stage1_inbound_texts.py`
  (INTEREST), `stage2_qualify.py` (QUALIFY — discovery + FAQs), `stage3_book.py` (BOOK
  — slot offer + booking).
- **Registry** (`__init__.py`): `get_stage_prompt(call_type, stage)`. Stage 2 & 3 are
  call-type-agnostic and reused; you only ever write a new Stage 1 per call type.
- **Handoff** carries structured vars between stages (e.g. Stage 1→2 extracts
  `{first_name, biz_type, reason, raised_objections}`).
- **Monolithic `inbound_texts.py`** (~34 KB single prompt) is the pre-split rollback
  target.
- **Push:** `scripts/push_squad.py` idempotently upserts the 3 assistants + squad by
  name, cloning voice/transcriber/speaking-plans from a live monolithic assistant. All
  stages: GPT-4.1, temp 0.6, maxTokens 350, 24h prompt cache.

---

## 5. Packages & Runtime

**Summary:** Pure Python, no Node, and **no central dependency manifest** —
`pyproject.toml` contains *only* pytest config. Deps are split across two thin
`requirements.txt` files. `httpx` is the universal HTTP client; the only AI SDK in the
repo is `anthropic` (used solely by the offline sim/judge).

- **Runtime** (`src/webhooks/requirements.txt`): `fastapi`, `uvicorn[standard]`,
  `httpx`, `python-dotenv`, `tzdata`.
- **Test/sim** (`tests/requirements.txt`): `anthropic`, `pyyaml`, `python-dotenv`.
  ⚠️ Omits `pytest`/`pytest-asyncio`/`httpx`/`fastapi` that the tests actually need.
- **Python:** 3.12 in prod (VPS, Ubuntu 24.04); the local `.venv-test` is 3.13.3.
- **Audio-pipeline libs** (torch, whisperx, librosa, pyannote, etc.) are never pinned
  anywhere — ad-hoc pip installs into a `.venv-extract`, per the voice-cloning docs.
- ⚠️ **Ruff** is prescribed in CLAUDE.md but has no config and isn't installed —
  relies on a global install.

---

## 6. CI/CD & Deployment

**Summary:** **There is no CI/CD.** No `.github/`, no Actions, no pipeline of any
kind. Deployment is fully manual across two independent planes.

- **Plane A — webhook server:** `scripts/deploy-webhooks.sh` — `scp` + venv install +
  write systemd unit + nginx include + Let's Encrypt TLS + health check, to the
  Hostinger VPS (`voicsito-webhooks` systemd service, port 8081, behind
  `webhooks.c7a.io/voicsito/*`). SSH via Tailscale.
- **Plane B — prompts/assistants:** `scripts/push_squad.py` — HTTP PATCH/POST to the
  Vapi API. Doesn't even cut the phone number over; it just prints the manual `curl`
  commands.
- **Shared infra:** `github.com/waseem7b/webhooks-infra` (local clone
  `~/CLAUDSITO/webhooks-infra`) — bootstrap/add-project/status scripts for the
  multi-tenant `webhooks.c7a.io` host. Extracted from this repo 2026-08-11.
- ⚠️ **Confirmed deploy bug:** `deploy-webhooks.sh` only `scp`s 5 files and **omits
  `telegram_notify.py`, `call_archive.py`, `call_summary.py`** — all three are
  top-level imports in `server.py`. A clean-slot deploy would crash on startup with
  ImportError. Prod only works because those files were placed out-of-band earlier;
  edits to them are **silently never deployed** by this script.

---

## 7. Testing & Tooling

**Summary:** Four pillars — an offline LLM-vs-LLM simulator, a pytest suite, a
live-system safety harness, and a file-based call-review system.

- **Simulator** (`sim-call.py` + `judge-call.py`): two Claudes role-play Jon vs. a
  prospect persona against the *live on-disk prompts*, with all tools mocked. 12 "bank"
  personas + 14 one-shots in `tests/personas/`. ⚠️ Fidelity gap: the sim models Jon as
  **Sonnet 4.6**, but production runs **GPT-4.1**.
- **pytest** (`tests/unit/`): ~16 modules / ~175 test fns — timezone resolution,
  booking hardening, webhook integration (GHL mocked at the httpx transport), call
  archive/summary, etc.
- **Live-system safety harness** (`tests/lib/`): markers (phone `+15550199XXXX` +
  `voicsito-test-artifact` tag), per-run inventory, **delete-by-ID-from-inventory-only**
  cleanup, clean-state verification. Built to prevent the 2026-05-11 incident where a
  list-and-match cleanup cancelled real appointments.
- **Call-review system** (`voicsito/call-review/`): a 28-item rubric (`CL-*`), a
  prompt-edit ledger (`CH-*` with before/after sha256), session logs, and a
  `check_checklist_sync.py` drift gate enforcing "every prompt edit pairs with a
  checklist update."

---

## ⚠️ Notable Issues & Discrepancies

Several of these are doc-vs-reality mismatches worth attention:

1. **Voice model is unverifiable from the repo.** Docs/skills say "ElevenLabs Turbo
   v2.5, voice `F2BOg7Ry3zTPV6Zd62Cd`" — but **no such voice ID or model exists
   anywhere in the repo source.** `setup-vapi.py` declares `eleven_multilingual_v2` /
   `LJ3VmFT7KIfEw9jSLImM`, and `push_squad.py` inherits voice live from Vapi. True
   production voice is only knowable by querying live Vapi. (The call-review skill notes
   `F2BOg7...` was discovered to actually be the *deleted Jon PVC*, not a stock voice —
   so the docs are stale here.)

2. **Security: webhook auth is off by default.** `VAPI_WEBHOOK_SECRET` gates *both*
   endpoints, but it's **not in the env template**, and when unset, signature
   verification is disabled. The insecure path is the documented default — meaning
   anyone who can POST to the server could influence routing/CRM lookups.

3. **Env template is badly out of date.** `config/.env.example` is missing many vars
   the code actually uses: `TELEGRAM_BOT_TOKEN/CHAT_ID`, `OPENROUTER_API_KEY`,
   `ANTHROPIC_BASE_URL`, `VAPI_WEBHOOK_SECRET`, `VAPI_SQUAD_ID`, `VAPI_ASSISTANT_ID`,
   `DISPLAY_TZ`, `LIVE_RECORDINGS_DIR`. A bootstrapper from the template would silently
   get no Telegram, no auth, and monolithic routing.

4. **No database, despite config implying one.** The only persistence is the in-memory
   call cache (lost on restart) + the per-call disk archive. Postgres/Redis/R2 are all
   dead config.

5. **`stats.py` outcome drift.** The recent "AGREED – NOT BOOKED" outcome bucket was
   added to `call_summary.py` but not to `stats.py` — daily digests silently omit that
   bucket.

6. **The git auto-commit hook isn't wired up.** A `git_commit_after_vapi_push.py`
   script exists, but `.claude/settings.json` is empty (`{"permissions":{}}`) — it's not
   registered as a hook.
