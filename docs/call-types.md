# Voicsito — Call Types Specification

Six distinct call types, each with its own trigger, script, source CRM, and post-call behavior. This is the master reference for prompt routing, CRM targeting, and workflow design.

---

## Summary table

| # | Call Type | Direction | Source CRM | Trigger | Calendar Target |
|---|---|---|---|---|---|
| 1 | **Scheduled discovery** | Outbound | Texts CRM 1 / 2 | Appointment time hit | Main CRM video call calendar |
| 2 | **Inbound — Texts** | Inbound | Texts CRM 1 / 2 | Warm lead dials TF number | Main CRM video call calendar |
| | *Opener:* "Hey, Jon here — who's this calling?" | | | | |
| 3 | **Callback after miss** | Inbound | Texts CRM 1 / 2 | Missed-outbound prospect calls back | Main CRM video call calendar |
| 4 | **Demo confirmation** | Outbound | Main CRM | Day-of demo (T-Xh) | (Confirm or reschedule existing) |
| 5 | **Rebooking** | Outbound | Main CRM | No-show / cancel state | Main CRM video call calendar |
| 6 | **Inbound — Main** | Inbound | Main CRM | Already-booked prospect dials Main CRM TF | (Confirm, reschedule, cancel, or general Q) |

---

## Call Type 1 — Scheduled Outbound Discovery

**Source:** Texts CRM 1 or Texts CRM 2 (warm SMS lead → agreed to phone discovery)
**Direction:** Outbound (GHL fires the dial)
**Caller ID seen by prospect:** GHL TF number (the same one they were texting with)
**Goal:** Qualify (BANT-style) → handle objections → book video demo on Main CRM calendar

**Trigger:** GHL workflow fires at the scheduled appointment time. Contact has tag like `discovery-scheduled`.

**Pre-call context loaded:**
- Name, business name, industry, location, email
- Source CRM = Texts CRM (1 or 2)
- Direction = `outbound`
- Type = `discovery`

**Opening (warm, references SMS):**
> "Hey [Name], this is Voicsito from Cloud 7 Agency. We were chatting on text about taking on more business in [Location] — figured I'd give you a call as planned. How's everything going?"

**Flow:**
1. Soft open + rapport
2. Discovery questions (jobs/month, current marketing, growth goals, decision maker, timeline)
3. Objection handling
4. Transition: "Sounds like a great fit, let's book a video walkthrough"
5. Book in Main CRM video call calendar
6. Confirm + email + commitment lock

**Post-call:**
- Update contact stage in source Texts CRM → "Discovery Complete"
- Create appointment in Main CRM calendar (if booked)
- Push call to Main CRM as opportunity (transferring contact across CRMs is part of existing workflow)
- Tags: `qualified`, `disqualified`, `callback-requested`, etc.

---

## Call Type 2 — Inbound Direct (Cold Inbound)

**Source:** Texts CRM 1 or Texts CRM 2 (prospect calls in after seeing SMS, before scheduled call)
**Direction:** Inbound (prospect dials)
**Goal:** Same as Type 1 — qualify, book video demo. But different opener and pacing.

**Trigger:** Prospect dials a Texts CRM TF number → GHL forwards to AI.

**Pre-call context (looked up by caller ID after call connects):**
- Match prospect's phone against contacts in BOTH Texts CRMs
- Pull their name + last SMS thread
- Direction = `inbound`
- Type = `inbound_texts`

**Opening (they called us — natural, casual, no AI giveaway):**
> "Hey, Jon here — who's this calling?"

**Branching by their response:**

*If they give a name + reference SMS:*
> Prospect: "This is Mike from Spokane Flooring, you texted me about having more jobs."
> AI: "Yeah, thanks for the call. [Continue to discovery script]"

*If they don't give a name:*
> AI: "Sorry, I didn't catch your name?"
> [Get name, then continue]

*If they give a name but don't reference SMS:*
> AI: "Yeah thanks for calling [Name]. We'd been texting about taking on more business in your area. Got a few minutes to chat?"

**Why this opening matters:**
- "Jon here" = prospect hears a familiar name (the same name on the SMS thread)
- "Who's this calling?" = forces prospect to ID themselves → we get their name + business naturally
- No AI disclosure here per business decision (compliance parked, see architecture.md §10)

**Flow:** Same discovery → objections → book as Type 1. Diverges only at the opening.

**Post-call:** Same as Type 1.

---

## Call Type 3 — Callback After Missed Outbound

**Source:** Texts CRM 1 or Texts CRM 2 (we tried calling earlier today, no answer; now they're calling back)
**Direction:** Inbound (prospect dials)
**Goal:** Same as Type 1 — qualify, book.

**Trigger:** Prospect dials a Texts CRM TF number AND their contact record shows a missed-outbound attempt earlier the same day (e.g., tag `missed-outbound-today` or recent failed call activity).

**Pre-call detection logic:**
- Lookup contact by caller ID
- Check for recent (same-day) missed call activity from us
- If found → load Type 3 context
- If not → fall back to Type 2

**Opening (acknowledge the prior attempt):**
> "Hey [Name], thanks for calling back — I tried you earlier about [topic from SMS]. Got a quick few minutes now to chat?"

**Flow:** Same as Type 1 from there.

**Why this matters:** A prospect calling back is HOT. They're showing intent. The opener acknowledging "I tried you earlier" closes the loop and signals attention.

---

## Call Type 4 — Demo Confirmation

**Source:** Main CRM (prospect already booked a video demo)
**Direction:** Outbound (we call them on the day of the demo)
**Goal:** Confirm attendance, brief them on what to expect, reduce no-show risk. **Not a sales call.**

**Trigger:** GHL workflow fires X hours before the booked demo time on the day-of (e.g., 4 hours before).

**Pre-call context loaded from Main CRM:**
- Name, business, location
- Demo time (today)
- Demo host/closer name (Jon, etc.)
- Direction = `outbound`
- Type = `demo_confirmation`

**Opening (light, expecting confirmation):**
> "Hey [Name], it's Voicsito from Cloud 7 — just touching base. We've got our walkthrough today at [time]. You still good for that?"

**Flow:**
1. Confirm attendance
2. If yes → brief reminder of what they'll see ("[Closer] will walk you through the marketing system, takes about 30 min, bring any questions")
3. If no/needs to reschedule → offer 1-2 alternative slots, rebook
4. If they want to cancel outright → tag for rebooking workflow

**Post-call:**
- Tag confirmation status: `confirmed`, `rescheduled`, `canceled`
- Update appointment if rescheduled
- If canceled → trigger rebooking workflow (Call Type 5)

**Critical:** Tone is friendly + brief. NOT salesy. They've already bought into the next step. Don't re-sell.

---

## Call Type 5 — Rebooking (No-show / Cancel Recovery)

**Source:** Main CRM (prospect booked a demo, then no-showed or canceled)
**Direction:** Outbound
**Goal:** Re-engage and get them on a NEW demo slot. Recover the opportunity.

**Trigger:** GHL workflow fires when appointment status changes to `no-show` or `canceled` (with cooldown — e.g., wait 4 hours after no-show, 24 hours after cancel).

**Pre-call context:**
- Name, business
- Original demo time + date
- Outcome (no-show / canceled / canceled with reason)
- Number of prior rebooking attempts (cap at 2-3)
- Direction = `outbound`
- Type = `rebooking`

**Opening (depends on outcome):**

*If no-show:*
> "Hey [Name], it's Voicsito from Cloud 7 — we had you down for [day/time] for the walkthrough but didn't connect. Everything okay?"

*If canceled:*
> "Hey [Name], Voicsito here from Cloud 7. Saw your spot got canceled — totally fine, life happens. Wanted to see if we should grab a new time."

**Flow:**
1. Empathetic open (don't shame them)
2. Soft probe: "What threw it off?" → addresses any objection that came up since booking
3. If still interested → "Let's grab a new time"
4. Book on Main CRM calendar
5. If hard no → tag as dead, end gracefully

**Tone:** Warm, no pressure, high empathy. People avoid people who shamed them. People come back to people who were chill about it.

**Post-call:**
- Update appointment with new time (if rebooked)
- Tag outcome: `rebooked`, `still-interested-no-time`, `dead`
- If rebooked → Type 4 (confirmation) fires day-of new demo

---

## Call Type 6 — Inbound to Main CRM

**Source:** Main CRM (existing demo-booked prospect / post-demo prospect calling in)
**Direction:** Inbound (they dial Main CRM's TF number)
**Goal:** Service the call — NOT sell. They're already in the funnel, they want help with their appointment or have a pre-demo question.

**Trigger:** Prospect dials Main CRM TF number → forwards to AI.

**Pre-call detection logic:**
- Lookup caller ID in Main CRM contacts (NOT Texts CRMs)
- Pull their booked appointment status: `scheduled`, `completed`, `no-show`, `canceled`, or `none`
- Different prompt branches based on appointment state

**Pre-call context loaded:**
- Name, business
- Appointment status + scheduled time (if any)
- Closer/host name (Jon, etc.)
- Direction = `inbound`
- Type = `inbound_main`

**Opening (acknowledgment of who they are):**
> "Hey [Name], this is Voicsito at Cloud 7 Agency — saw you calling, what's up?"

**Branching by appointment state:**

*If appointment is upcoming (scheduled today/this week):*
- Probably calling to confirm, reschedule, or ask a question
- Tools: `confirm_appointment`, `reschedule`, `cancel`, `flag_for_human`

*If appointment is past + completed:*
- Likely follow-up, post-demo question, or wants next steps
- Tools: `flag_for_human` (route to closer for post-sale)

*If appointment is past + no-show / canceled:*
- They might be calling to rebook themselves
- Tools: `book_new_appointment` (offer to rebook on the spot)

*If no appointment on record:*
- Strange edge case — they're in Main CRM but no demo booked
- Tools: `flag_for_human`, `book_new_appointment`

**Tone:** Helpful, customer-service-like. NOT salesy — they're past the qualification stage.

**Post-call:**
- Update appointment status if changed
- Tag call type: `confirmed_via_call`, `rescheduled`, `canceled_via_call`, `escalated_to_human`, `general_inquiry`
- If escalated → notify human via Slack/text
- Push transcript to sales dashboard

**Why this is fundamentally different from Type 2:**
- Type 2 (Texts inbound): cold-ish lead, sell mode, qualify, book a demo
- Type 6 (Main inbound): warm customer, service mode, manage existing appointment

These two call types share zero script content. They share infrastructure.

---

## Unknown caller policy

For ALL inbound types — if the caller ID doesn't match any contact in the relevant CRM(s):

**Default:** Polite drop.
> "Hey, I don't recognize this number in our system — please call back from the number we texted you on. Thanks!"
> *(Hang up, tag as `unknown_caller_dropped`)*

This protects against spam, wrong numbers, and prospects calling from a different line than the one we have on file. Tag is logged for human review in case we need to manually match a contact.

---

## Implementation notes

### Prompt routing
The agent loads a different system prompt + tool set per call type at runtime. Pre-call context includes a `type` field that selects the config bundle.

```python
CALL_CONFIGS = {
  "discovery":          { prompt: "...discovery script...",        tools: [book_demo] },
  "inbound_texts":      { prompt: "...inbound discovery variant...", tools: [book_demo] },
  "callback":           { prompt: "...callback acknowledgment...", tools: [book_demo] },
  "demo_confirmation":  { prompt: "...confirmation script...",     tools: [confirm, reschedule, cancel] },
  "rebooking":          { prompt: "...empathetic rebooking...",    tools: [book_demo, mark_dead] },
  "inbound_main":       { prompt: "...service-mode script...",     tools: [confirm, reschedule, cancel, book_demo, escalate] },
}
```

### Type detection (inbound)
On call connect, the agent runs a parallel lookup:
1. Query Main CRM contacts by caller ID
2. Query Texts CRM 1 contacts by caller ID
3. Query Texts CRM 2 contacts by caller ID

First match wins. Routing rule:
- Match in **Main CRM** → load `inbound_main` config
- Match in **Texts CRM** + recent missed-outbound today → load `callback` config
- Match in **Texts CRM** (no recent miss) → load `inbound_texts` config
- **No match anywhere** → polite drop, tag `unknown_caller_dropped`

### CRM routing
Different call types live in different CRMs. The orchestrator must route post-call writes to the correct location:

| Type | Read from | Write to |
|---|---|---|
| 1 Scheduled discovery | Texts CRM | Both Texts CRM (status) + Main CRM (booking) |
| 2 Inbound — Texts | Texts CRM (lookup by caller ID) | Both Texts CRM + Main CRM |
| 3 Callback | Texts CRM | Both Texts CRM + Main CRM |
| 4 Demo confirmation | Main CRM | Main CRM (status update) |
| 5 Rebooking | Main CRM | Main CRM (new appointment) |
| 6 Inbound — Main | Main CRM | Main CRM (status update / new booking / escalation) |

### Calendar target
- Types 1, 2, 3 → book on **Main CRM video call calendar** (the demo calendar)
- Type 4 → no new booking (confirms existing) — may reschedule existing
- Type 5 → book on **Main CRM video call calendar** (rebook new slot)
- Type 6 → may book, reschedule, or cancel on **Main CRM video call calendar**

### GHL "Voicsito AI" user setup
We need a "Voicsito AI" user (phone: +1555010016) registered in **all 3 CRMs** (legacy Twilio TF — current test number is +1555010011 on Vapi):
- Texts CRM 1 — for outbound types 1
- Texts CRM 2 — for outbound types 1
- Main CRM — for outbound types 4 and 5

Inbound calls (types 2, 3) use the existing GHL TF forwarding setup.

### Voicemail behavior per type
- Type 1 (discovery): voicemail on 2nd no-answer with custom message about the scheduled time
- Type 4 (confirmation): voicemail with reminder of the demo time + how to reschedule
- Type 5 (rebooking): voicemail with brief "let's get you back on the calendar" + reply-to-text instruction
- Types 2, 3: N/A (they're inbound)

---

## Build order (revised 2026-05-04)

Types 1, 2, 3 share the SAME core flow (qualify → handle objections → book). Only the **opening line** differs. So we build them as one engine with swappable openings.

1. **Type 2 — Inbound Texts** ← starting here
2. **Type 1 — Scheduled Outbound Discovery** (same flow as Type 2, different opener — leverages GHL workflow trigger)
3. **Type 3 — Callback After Miss** (same flow, opener acknowledges prior attempt)
4. **Type 4 — Demo Confirmation** (different flow — confirmation, not sales)
5. **Type 5 — Rebooking** (different flow — empathetic recovery)
6. **Type 6 — Inbound Main** (LAST — service mode, separate engine)
