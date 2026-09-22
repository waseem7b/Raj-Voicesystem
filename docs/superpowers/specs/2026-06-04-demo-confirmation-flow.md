# Demo Confirmation Flow (Type 4) — Implementation Plan

**Date:** 2026-06-04 (rev d — GHL speaks bot-ID + phone number; number is ASR-captured and used for the data lookup)
**Status:** Plan. Not yet built.
**Call type:** Type 4 — Demo Confirmation (outbound). See [docs/call-types.md §Call Type 4](../../call-types.md).
**Goal:** Outbound call that confirms a booked demo still stands (time + that they're coming); if they can't make it, rebook on the same call.

---

## Decisions locked (2026-06-04)

| Decision | Choice | Why it matters |
|---|---|---|
| **Origination** | **GHL originates.** GHL's agent dials the prospect and **bridges the answered call into a Vapi entry number.** | Satisfies the LC-caller-ID constraint in `2026-05-18-outbound-calling-architecture.md` (GHL originates; Vapi only receives). We do **not** `POST /call`. |
| **Routing handshake** | GHL's bot **speaks both** the **bot-ID** and the **phone number** on the bridged leg. Stage 0 captures both, routes on the bot-ID, passes the number forward. | One Vapi entry number multiplexes GHL's campaigns; bot-ID selects the flow, the spoken number is the data key. |
| **Signal format** | **Both spoken (bot-ID + phone number), via ASR.** | ⚠️ **The spoken phone number is the central reliability risk** — 10 fragile digits; one mis-transcription → no match, or worse the *wrong* contact's demo time. Validation mandatory (§F). DTMF would be safer but **GHL can't auto-send DTMF (checked 2026-06-04)** — so spoken + validation it is. |
| **Prospect data injection** | **Spoken number → mid-call lookup tool.** Stage 1's first action: `get_prospect_appointment(number)` → Main CRM → name + demo time, then open. **Outbound only.** | Number isn't known until GHL speaks it, so data comes from a tool result mid-call, not `{{template}}` variables. |
| **Squad shape** | **Stage 0 (router) → Stage 1 (Intro + Confirm) → Stage 2 (Rebook)** | Stage 0 is non-conversational (talks to GHL's bot). Stage 2 only runs when confirmation fails. |

> **Relationship to `2026-05-18-outbound-calling-architecture.md`:** back inside its bridge model (GHL originates → Vapi receives), but **replacing the LC-number→pool→job resolution with an in-band spoken bot-ID + number handshake.** Removes the number-pool + queue + dispatcher for this flow (only needed later for concurrency control across many simultaneous campaigns).

---

## Isolation from the inbound flow (explicit requirement)

This flow must be **completely separate** from the live inbound Type-2 squad.

| Surface | Inbound (live) | Confirmation (new) | Shared? |
|---|---|---|---|
| Trigger | prospect dials in | **GHL workflow** dials + bridges | No — different entry entirely |
| Phone number | `+1555010011` | **new Vapi entry number** | No |
| Squad + assistants | Inbound Texts squad | **new squad, new names** | No |
| Prompt modules | `stage1_inbound_texts` + `shared_persona` | **stage0/1/2 + `confirm_persona.py`** | No — `shared_persona` left untouched |
| GHL client `ghl.py` | texts-CRM lookups | **new fns, Main-CRM** | Shared module, **additive only** |
| Webhook server | `/vapi/tool`, `/vapi/server-events` | same endpoints | Shared infra, **additive + gated** |

**The two genuinely shared pieces are infrastructure, not logic:**
- **`ghl.py`** — only *add* functions (`get_prospect_appointment`, `reschedule_appointment`); existing inbound functions are not modified.
- **Webhook server** — new tool handlers are branches in `_dispatch_tool_call` that only fire when a confirmation-only tool is called; outcome tagging is **gated on a confirmation-flow flag** so it never touches inbound contacts. Verify the *existing* end-of-call handler degrades gracefully when an outbound call flows through it (texts-CRM lookup miss on the bridged number is harmless).

**Proof of isolation:** zero edits to any inbound-only file; `push_confirm_squad.py --dry-run` confirms it only CREATEs new-named resources (never PATCHes an inbound assistant); inbound smoke test passes after deploy.

---

## The flow

```
GHL workflow fires T-Xh before demo (day-of)
  → (optional) pre-call SMS to prospect
  → GHL's agent dials the prospect FROM a GHL/LC number
       ↓
Prospect answers → GHL bridges the call INTO the Vapi entry number
       ↓
GHL's bot speaks the bot-ID + the phone number
       ↓
STAGE 0 — Router (entry assistant; waits for caller, says nothing)
  capture spoken bot-ID + phone number → route on bot-ID, pass number forward
    ├─ "bot 1" → STAGE 1 (Outbound Confirmation)
    └─ "bot 2" → (other outbound flow, future)
       ↓
STAGE 1 — Intro + Confirm
  first action: get_prospect_appointment(spoken number) → Main CRM → name + demo time (VALIDATE)
  opener → "calling to confirm our zoom at [time], you still good?"
    ├─ Confirmed        → brief reminder + sign-off → endCall (terminal)
    ├─ Can't make it    → handoff → STAGE 2
    └─ Cancel / hard no → empathetic close → endCall (tag for human / rebooking)
       ↓
STAGE 2 — Rebook
  empathetic "let's grab a new time" → time pref → offer slot → confirm → reschedule → sign-off → endCall
       ↓
Vapi end-of-call-report → our /vapi/server-events
  → classify outcome → tag Main CRM (confirmed | rescheduled | cancelled | voicemail | no_answer | bad_number)
  → existing archive + Telegram summary
```

---

## Component-by-component work

### A. Stage 0 — Router assistant (NEW)

- **Role:** silent multiplexer. Talks to GHL's bot, not the prospect. Captures the spoken **bot-ID + phone number**, routes on the bot-ID, passes the number to Stage 1 via the handoff `variableExtractionPlan`.
- **Config:**
  - `firstMessageMode` = **assistant-waits-for-user** (GHL speaks first; Stage 0 emits nothing).
  - Tiny prompt, low `maxTokens`; classify-and-handoff only.
  - Tool: one `handoff` with **multiple destinations** (bot 1 → Stage 1 Confirm, bot 2 → …), `variableExtractionPlan` extracting `{phone_number}`. (Existing `handoff_tool_to()` builds one destination — extend for a destinations list.)
- **ASR-defensiveness (the fragile part):**
  - Tolerant bot-ID matching ("bot one / bot 1 / but one" → bot 1; "bot two / bot to / bot too" → bot 2).
  - **Extract the phone number robustly** — strip words, normalize digits to E.164, "oh"→0, handle grouping. Riskiest capture (10 digits) → §F validation.
  - We can't control GHL's wording. Fallback when bot-ID unrecognized: default-route + log (GHL's bot won't usefully repeat).

### B. Prompt modules (NEW) — `src/agent/prompts/`

- **`stage0_router.py`** — minimal router prompt (above).
- **`stage1_confirm.py`** — Intro + Confirm. First action = lookup tool; then open → confirm. Branch: confirmed → reminder + sign-off (terminal); can't-make-it → `handoff` → Stage 2; cancel/hard-no → empathetic close + `endCall`.
- **`stage2_rebook.py`** — Rebook. **Do NOT reuse `stage3_book.py` verbatim** — its Step 1 re-pitches the offer ([stage3_book.py:49](../../../src/agent/prompts/stage3_book.py)). Reuse the booking *mechanics* (slot offer → mark → book), open empathetically, **skip email capture** (already on file).

> **✅ Maximum isolation — leave `shared_persona.py` untouched; give confirmation its own persona module.**
> [shared_persona.py](../../../src/agent/prompts/shared_persona.py) is sell-shaped (Context: "cold-texted… calling back"; Reminder #1: "keep going until booked") — wrong for confirmation. Rather than *split* the live shared file the inbound squad depends on, the confirmation stages import their **own** `confirm_persona.py` — universal voice/TTS/anti-robot rules (copied) + a confirmation-specific context/goal. **`shared_persona.py` stays byte-untouched** → inbound unaffected by construction. Cost: voice rules duplicated across two files (keep in sync); a future DRY cleanup can extract a shared voice base, but isolation wins now.

### C. Registry wiring — `src/agent/prompts/__init__.py`

Add to `PROMPT_BY_STAGE`: `("demo_confirmation","stage0"/"stage1"/"stage2")`.

### D. GHL helpers (NEW) — `src/agent/ghl.py`

Reuse as-is: `get_available_slots`, `book_video_call`, `add_tag`, `lookup_contact_in_main_crm`, `update_contact`.
New:
- **`get_prospect_appointment(phone)`** — `lookup_contact_in_main_crm` + fetch the upcoming appointment → `{first_name, demo_day, demo_time, appointment_id, timezone}`. Backs the Stage 1 lookup tool. **Required** — Stage 1 can't state a time without it. Returns a not-found/ambiguous signal for the validation path.
- **`reschedule_appointment(appointment_id, new_slot_iso)`** — `PUT /calendars/events/appointments/{id}` with new start/end (+1h). True reschedule (no stale record).
- *(decision)* cancel path: `cancel_appointment(appointment_id)` vs tag-for-human.

### E. Vapi entry/bridge number + squad deploy

- **Entry number:** a Vapi number GHL calls into, routed to the **outbound squad** (Stage 0 first). Separate from the inbound Type-2 number (`+1555010011`).
- **Deploy script (NEW) `scripts/push_confirm_squad.py`** — model on [push_squad.py](../../../scripts/push_squad.py); reuse `base_config_from_monolithic()`, `endcall_tool()`, `handoff_tool_to()` (extended for multi-destination), `upsert_assistant()`, `upsert_squad()`, `TOOL_IDS`. Builds **3 assistants** (Stage 0/1/2) + squad `"Voicsito - Squad - Outbound (Confirmation)"`.
- Stage 1 opener is dynamic (uses the looked-up demo time from the tool result, not a `{{var}}` or the static opener mp3).

### F. Prospect data injection — spoken number → mid-call lookup tool (outbound only)

GHL **speaks the prospect's phone number** (NOT caller ID; NOT known before the LLM runs). Stage 0 captures it; Stage 1's **first action** calls `get_prospect_appointment(number)` against **Main CRM**, then opens with the real demo time. Data arrives as a **tool result mid-call**, not `{{template}}` variables.

- **Outbound-confirmation only** — must NOT change the inbound Type-2 Texts-CRM path.
- **⚠️ Validation is mandatory** — a spoken 10-digit number is the central failure point. One wrong digit → no match (no data) or, worse, the *wrong* contact (confirm someone else's time). Mitigations:
  - Normalize the transcript to E.164 (digits only, "oh"→0).
  - **Cross-check the match** — the matched contact must have an appointment ~today; if not, treat as a bad capture.
  - On no/ambiguous match: don't guess a time — graceful fallback (generic confirm or flag-for-human), tag `bad_number`, log the raw transcript.
  - If GHL also preserves caller ID, use it as a cross-check against the spoken digits.
- **Strongly recommended:** ask GHL to send the number as **DTMF** (keypad tones) rather than speech — far more reliable than spoken digits. (Separate from the bot-ID wording we can't control.)

### G. Server changes — NO new webhook endpoints — `src/webhooks/server.py`

Verified 2026-06-04: the server has a **single tool endpoint** [`/vapi/tool`](../../../src/webhooks/server.py#L781) that dispatches by tool name (`_dispatch_tool_call`), plus [`/vapi/server-events`](../../../src/webhooks/server.py#L1093) for phone-level events.
- **New tools** (`get_prospect_appointment`, `reschedule_video_call`) → **handler branches inside `_dispatch_tool_call`.** No new route.
- **Outcome tagging** → extend the existing `/vapi/server-events` end-of-call handler: detect the confirmation flow (squad/assistant id or metadata flag), classify outcome (confirmed / rescheduled / cancelled / voicemail / no_answer / **bad_number**), `add_tag` on Main CRM, extend archive + Telegram labels.
- **GHL trigger** → none. GHL dials + bridges directly, so there's no GHL-facing endpoint.

### H. Voicemail behavior

`voicemailDetection` + dynamic `voicemailMessage` on Stage 1 (base config already inherits both — [push_squad.py:130-135](../../../scripts/push_squad.py#L130-L135)). Note: with GHL bridging, GHL may already screen for a human before bridging — confirm whether Vapi-side detection is still needed.

### I. Tool inventory

| Tool | Source | Stage |
|---|---|---|
| `handoff` (multi-destination, extracts `phone_number`) | inline (extend `handoff_tool_to`) | 0 |
| `get_prospect_appointment` | **NEW** (tool def + server handler + GHL helper) | 1 (first action) |
| `handoff` (→ Stage 2) | inline | 1 |
| `endCall` | inline `endcall_tool()` | 1, 2 |
| `get_available_slots` | existing `4936203d…` | 2 |
| `reschedule_video_call` | **NEW** (tool def + handler + GHL `reschedule_appointment`) | 2 |
| `update_prospect_info` | existing `278ff15a…` | 1, 2 (if info corrected) |

---

## Wording / prompt discipline (BLOCKING before any prompt text)

- `prompt-update-discipline` mandatory before writing/editing any stage prompt (Stage 0/1/2).
- Spoken anchors (opener, confirm line, reschedule offer, sign-off) are the user's to author/approve (Rule 2). Drafts in [call-types.md §4](../../call-types.md) are drafts, not approved copy.
- Positive instruction; surgical negatives only for named GPT-4.1 failure modes (Rule 3).

---

## Testing plan

1. **Handshake first (highest risk):** verify Stage 0 reliably captures BOTH the spoken bot-ID AND the spoken phone number — deliberately test mis-transcribed digits. This is make-or-break; test before building the stages out.
2. **Prompt iteration (text-only):** `simulation-test`. ⚠️ The 12 bank personas are inbound cold leads — none model a booked prospect being confirmed. `simulation-test` forbids inventing personas without your say-so → you must approve a confirmation persona + judge rubric.
3. **Live (mutating):** `live-system-testing` mandatory — real bridged call + the rebook path writes to the prod calendar. Use test phone prefix `+15550199` + `_test_dry_run` ([server.py:500](../../../src/webhooks/server.py)) so reschedule doesn't mutate a real appointment.
4. No "works/live/deployed" claims without a tool result in hand (CLAUDE.md).

---

## Build order

1. `confirm_persona.py` — own persona module (universal voice rules + confirmation context). Leave `shared_persona.py` untouched.
2. `stage0_router.py` + extend `handoff_tool_to` for multi-destination + `phone_number` extraction (after `prompt-update-discipline`).
3. **Prove the handshake on Vapi** — Stage 0 capturing the bot-ID AND a spoken phone number accurately (test mis-transcription) — before building stages out.
4. GHL `get_prospect_appointment` helper + `get_prospect_appointment` tool/handler + number validation.
5. `stage1_confirm.py` (lookup-on-entry) + `stage2_rebook.py` (user-approved anchors).
6. Registry wiring.
7. GHL `reschedule_appointment` + `reschedule_video_call` tool/handler.
8. `scripts/push_confirm_squad.py` → 3 assistants + squad (dry-run first).
9. Vapi entry/bridge number → route to the outbound squad.
10. GHL workflow: dial prospect, bridge to Vapi, speak bot-ID + number (+ optional SMS).
11. End-of-call outcome tagging; voicemail behavior.
12. Test: handshake → simulation-test → live-system-testing.

---

## Open questions to resolve at build time

- **DTMF for the number? — NO (checked 2026-06-04).** GHL has no automated send-DTMF action; its DTMF is *gather*-only (IVR) or manual dialpad. In-band number stays **spoken** → §F validation is mandatory. *Alternative to evaluate:* GHL **Voice AI Custom Action → webhook** can send the number/contact_id as structured JSON (zero ASR risk), but needs a key to **correlate that webhook with the bridged Vapi call leg** (the in-band handshake exists precisely to avoid that correlation). Trade: ASR risk vs. correlation complexity.
- **Number validation strategy** (§F): normalize + cross-check the matched contact has a ~today appointment; fallback + `bad_number` tag on no/ambiguous match. Never confirm the wrong contact's time.
- **Does GHL also preserve caller ID?** If so, use it to cross-check the spoken digits.
- **Does GHL screen for human** before bridging? (Decides whether Vapi-side voicemail detection is still needed.)
- **Cancel path:** `appointmentStatus="cancelled"` vs tag-for-human / hand to Type-5.
- **`demo_time` formatting:** format to spoken English server-side (in `get_prospect_appointment`) — preferred — vs in-prompt.
- **Reschedule outcome capture:** deterministic (tool fired) vs post-call classification.

---

## Rollback

- Squad + the 3 stage assistants live only on Vapi until the GHL workflow + entry-number routing are wired; deleting them (by name, via the push-script targets) fully removes the flow.
- `shared_persona.py` and all inbound-only files are left untouched → live inbound Type-2 is unaffected by construction.
- No `POST /call` dialer is added; nothing on our side originates calls.
