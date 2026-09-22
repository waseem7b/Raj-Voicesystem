# Demo Confirmation System — iMessage + AI Voice (Design)

Date: 2026-08-03 · Status: v1 design, pre-verification
Scope: C7A Main CRM demo funnel. iMessage reminders (Bloo) + AI confirmation call (Vapi). Human persona, no AI disclosure (existing doctrine; compliance remains a parked, accepted risk — revisit before scaling).

## The gap this fills

Textsito books demos into Main CRM (`G1yFGT1liEZgvT3dYgEx`, calendar `ULFPF484I2XcTfSR2uIC` / `Avic6mmnHz98VZNPlY4D`) and sends exactly one confirmation SMS at booking time. Nothing runs between "booked" and "showed". The rebooking drip only catches the aftermath.

## Architecture decision

**GHL workflows own all timing and messaging. One thin webhook service owns only the voice leg.** Prompts are lifted from Voicsito's `feat/outbound-demo-confirmation` branch (`confirm_persona.py`, `stage1_confirm.py` — the "confirmation, not a pitch" doctrine), with Stage-0 and the GHL-bridge dance deleted.

Why the old bridge dies: it existed only so calls could present an LC caller-ID, which forced GHL to originate and speak a 10-digit routing number into Vapi (the spec's own top-listed reliability risk — one mis-heard digit confirms the wrong contact's demo). With iMessage announcing "you'll get a call from (XXX) XXX-XXXX", caller-ID no longer needs to be an LC number. Vapi originates directly via API with structured variables. Zero ASR risk, no queue/pool infra, no Stage-0.

## Flow

```
Booking lands (existing jonsito flow → Main CRM appointment)
└─ GHL workflow "Demo Confirmations" (appointment trigger)
   ├─ T0        Bloo iMessage: locked in for {time}, save this number
   ├─ T-24h     Bloo iMessage: reminder + "reply C to confirm"
   │            └─ reply → tag confirmed-by-text → SKIP CALL branch
   ├─ T-3h      If NOT confirmed-by-text:
   │            ├─ Bloo iMessage: "heads up — calling you in a few from (XXX) XXX-XXXX"
   │            └─ Webhook → confirm service → Vapi outbound call
   └─ Post-call branches on outcome tag (below)
```

### Voice call (Vapi)

- New standalone assistant (NOT in the inbound squad; Voicsito's isolation doctrine carries over). Single stage: confirm → brief → branch.
- Variables injected at call creation (`assistantOverrides.variableValues`): first name, demo time in lead TZ, contact id. No mid-call GHL lookup needed — the trigger already knows who it's calling. (Keep Voicsito's lesson: no dynamic interpolation into the cached system prompt body; variables only.)
- Config patterns stolen from Voicsito push scripts: `startSpeakingPlan.waitSeconds=0.2`, `onNoPunctuationSeconds=0.4`, `smartEndpointingEnabled=false`, idle hooks via `assistant.hooks` (never `messagePlan.idleMessages` — broken), endCall `rejectionPlan`, voicemail detection on.
- New voice: stock ElevenLabs until a clone is chosen. Do NOT inherit config from the Voicsito monolith donor.

### Outcomes → GHL tags

| Call result | Tag | GHL follow-up branch |
|---|---|---|
| Confirmed | `demo-confirmed` | done |
| Wants reschedule | `demo-reschedule-requested` | Telegram + human handles (v1) |
| Cancels | `demo-canceled` | existing rebooking entry webhook |
| Voicemail/no answer | `demo-call-no-answer` | 1 retry in ~45m (service-side, max 2 attempts) → then Bloo iMessage fallback: "couldn't reach you — still good for {time}?" |
| Bad state (no appt found, etc.) | `demo-confirm-error` | Telegram alert |

Agent line for reschedule (v1): "no problem — I'll have someone text you a couple of times that work." No AI rebooking in v1.

## The thin service ("confirmsito")

New subfolder in this project, deployed to the existing `webhooks.c7a.io` VPS pattern (next free port 8082 per Voicsito's `infra/webhooks/REGISTRY.md`).

Routes:
- `POST /confirm/call` — from GHL workflow webhook node. Payload: contactId, phone, firstName, apptTime, tz. Guards: idempotency per (contactId, apptId), business-hours window, skip if `demo-confirmed`/`confirmed-by-text` already tagged (re-check GHL live), skip if appt < 15 min away or past. Then Vapi create-call.
- `POST /vapi/events` — Vapi server webhook (HMAC/`x-vapi-secret`, secret REQUIRED not optional — fixes Voicsito's off-by-default auth gap). end-of-call-report → classify outcome → GHL tag + note + Telegram summary. Schedules retry on no-answer.
- `GET /health`.

Patterns carried over: always-200 tool responses, structured-not-spoken error strings, Telegram as primary observability, exact-prompt logging per call, deploy script that ships ALL files (Voicsito's deploy bug noted and avoided), test markers + DELETE-BY-ID-only cleanup doctrine, `--dry-run` default for anything that touches real contacts.

GHL client: direct REST (`services.leadconnectorhq.com`, PIT auth, Version `2021-07-28`; calendars `2021-04-15`). Reuse Main CRM PIT.

## Bloo setup

- Marketplace app installed on the **Main CRM sub-account** only. Texts CRMs stay on LC Phone — cold-outbound deliverability isolation is preserved; Bloo touches booked leads only.
- Sends via the Bloo workflow action node (not a location-wide default-SMS-provider swap) in v1 — the swap would silently reroute every other Main CRM send (incl. handed-off rebooking drips) through Bloo. Revisit after v1.
- Dedicated number ($289/mo) over shared: the number is in the "calling you from" promise and becomes the saved contact.

## Verification gates before build (in order)

1. **Bloo live test** on Main CRM: install → workflow-triggered iMessage to a test contact → confirm blue bubble, reply lands in GHL and can trigger a workflow branch. (All Bloo behavior so far is from their docs — unconfirmed live.)
2. **Vapi outbound API** against current docs (Context7 unavailable this session — use docs.vapi.ai directly): create-call with `assistantOverrides.variableValues`, caller number, voicemail detection behavior.
3. **GHL appointment trigger** fires with the fields we need (contact, appt time) and webhook node payload shape — verified on a throwaway workflow, not help-center articles.

## Explicitly out of v1

AI reschedule/rebooking on the call, inbound-call handling (Type 6), replacing the rebooking drip, Bloo as default SMS provider, voice cloning, any Textsito changes.
