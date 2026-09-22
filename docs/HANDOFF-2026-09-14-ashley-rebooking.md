# Ashley rebooking call — agent handoff, 2026-09-14

> **Superseded by `docs/HANDOFF-2026-09-18-ashley.md`** for current state. This file is
> kept for the call-by-call history: what each fix was, which call showed it, and what
> was ruled out along the way.

For the next agent picking up Ashley's **rebooking** call. Read this first, then
`docs/ASHLEY-HANDOFF.md` (the long-lived reference). Everything below was done
on 2026-09-14 in one session. The owner tests with live calls to his own test
contacts and reviews them turn by turn.

**Rebooking is DEV-only.** It runs for the owner's test contacts and the CRM's TEST
rebooking pipeline, not for live prospects. Don't point production at it
without the owner's say-so.

---

## What is live now

Voicsito `main` = `28f49f4`. The VPS webhook files and the Vapi Rebooking
assistant (`a19e14c4-ef68-4e37-98b3-c3e448290e71`) were deployed from it and
checked by sha256 and a live read-back.

| Commit | What |
|---|---|
| `7aa92c7` | **Preload before dialling.** Jon's open times for 7 days go into `{{availableSlots}}`, with day labels relative to the prospect ("tomorrow (Tuesday Sep 15)"). Past times are dropped and the offered ISOs are pre-registered. The call cache is seeded with the MAIN-CRM contact and `lookup_done`, so the texts-CRM lookup can't swap the contact. That swap caused HTTP 422 "contact id is invalid" on call 01a0a1fd. |
| `faf055d` | Prompt: Step 4 lists the preloaded times. The `get_available_slots` pull stays only as a fallback when the list is empty. |
| `ab90c87` | (Session 81) Telegram header shows the call type. |
| `327d6ec` | Vapi says "Hold on a sec." as `reschedule_video_call` starts (request-start message, booking tool only). When both offered times are on the same day, the day is said once. |
| `dd3dcf3` | Offer = first morning opening + first afternoon opening (owner: not back to back). |
| `9d2b90f` | **Missed day.** The missed appointment is the no-show/cancel whose status changed most recently, within 7 days either side, deleted events skipped. A cancel of a booking later today is named "today". If the trigger sends `appointment_id`, that appointment is read directly and wins. |
| `3c4176a` + `28f49f4` | **Why-calling line.** The webhook builds the owner's full line with the day filled in (`_why_calling_line` → `{{whyCalling}}`), and Step 3 says it verbatim. The model had been re-punctuating it around `{{missedDay}}`, which the owner heard as choppy. |

**2026-09-15 — the prospect's clock and a full week of days (`cf18167` + `2449854`).**
Call 01a0a622: asked "anything tomorrow?", she read today's 8pm as tomorrow's —
Wednesday had no line in the list and Vapi's `currentDateTime` is UTC. Now the webhook
sends `{{currentDay}}` ("It is 2:11pm on Tuesday, September 15. Tomorrow is
Wednesday, September 16.", prospect-local) and `{{availableSlots}}` has one line for
every day from today through 7 days out, empty ones "no openings". Step 4 shows the
clock beside the list; Step 1 forbids working out the day from anything else. Owner's
rule: **everything she needs is loaded before the call fires; she computes nothing.**
Session 03 (ended) deployed a call ledger in the same file at `b107533`; parent hash for
the VPS `ghl_trigger.py` is now `a8bda7b59cedab04`.

**2026-09-15 later — more preloads and two tests.** `{{firstOffer}}` (`9e29173` +
`39c1c90`): the webhook writes the first time offer, day named once, from
`_first_offer_line()`; the prompt reads it verbatim (call 01a0a678 said the day twice
while the model chose between two anchors). The varied reaction (`537b17e`): "Love to
hear it." is no longer a fixed anchor. Endpointing test (`803cf7a`): LiveKit smart
endpointing is OFF on Rebooking only — endpointing latency dropped from ~500–1700ms
to ~100ms on the next two calls, no cut-offs seen yet. Voice test round 2 (`62c7615`):
Rebooking stability 0.6 / style 0.5; the owner heard it and it is "better, not
perfect"; the choppy line is a TTS-chunking effect (see below) — recommended round 3
is dashes instead of full stops so the line reaches ElevenLabs as ONE message (the
single-message slot line "sounds great"). Also: Wednesday shows "no openings" because
Jon's GHL availability has Wednesday blocked — owner confirmed, working as intended.
Owner is on **Pacific** (GMT-7); the Ryan Meat test contact now carries
`America/Los_Angeles`.

Evening 2026-09-15: `dcec7a1` waitSeconds 0 on Rebooking; `fa9c4b1` two times per
offer; `9fd695d` + `8913a8a` shared part of day said once ("seven or ten in the
morning"), "two, not three". Session 33 shipped `37a113f`: **Rebooking runs
gpt-4.1-mini** (owner's model-latency test; Confirmation/Reminder stay gpt-4.1) — the
next calls judge both the model slice (was 555–1327ms) and script fidelity. Owner said
the choppy line "sounds good now" on round 2 (voice 0.6/0.5), so round 3 is parked;
`WHY_CALLING_JOIN_WITH_COMMAS` stays False. Confirmation/Reminder still have every
old setting (LiveKit on, waitSeconds 0.2, voice 0.45/0.75) — owner has the lever list
and hasn't chosen.

Late 2026-09-15 (owner-approved, shipped): `e8efd61` + `dd85121` — the webhook writes
an offer line for EVERY day with openings (`{{dayOffers}}`, `_day_offer_lines()`), she
reads the asked-for day's line verbatim (her self-composed offers kept repeating the
part of day / listing 3–4 times on 4.1 AND 5.5); and a pick gate — nothing books until
a time is named ("okay/sounds good" → "Which one works better, X or Y?", garbled turn →
"Sorry, say that again?"). Calls 01a0a678 and 01a0a7ef had booked real appointments on
"Okay. Sounds good." and on a transcriber garble. Model trials (session 33 owns the
`model` / `reasoning_effort` keys on FLOWS["rebooking"]): 4.1-mini (reverted, ignored the
asked-for day) → 5.5 minimal (slower than 4.1: 722–1831ms) → **gpt-5.4-mini, reasoning
low** (`f667ef4`, current). Voice `optimizeStreamingLatency` 3 on Rebooking (`8b6867b`)
cut the voice slice to 330–510ms. Quiet room tone on Rebooking (`ff00ea4`, session 33).
Two sessions edit this repo: check `git status` before every commit and never run
`push_confirm_squad.py` with a dirty tree.

**2026-09-15 late — the prompt is two messages now (`0d20c2b`).** Rebooking's
`model.messages` is a STATIC system message (persona + flow, 31.4k chars, **no
`{{variable}}` anywhere** — an import-time assert in `stage1_rebook.py` enforces it)
followed by `CONTEXT`, a ~300-char system message Vapi renders per call with every
value the webhook sends. Why: OpenAI caches the longest unchanged prefix, and the
first variable used to sit early (the persona's own `Today is {{ now }}` line), so
most of the prompt re-processed every turn. The flow refers to values by name ("the
whyCalling line in your call context"); "the open times above" became "in your call
context". `get_stage_context()` + `CONTEXT_BY_STAGE` in `prompts/__init__.py` decide
which flows get one — Confirmation and Reminder are still single-message, and
carrying them over is the obvious next step once a rebooking call confirms it.
**Unverified until the next call:** that Vapi renders `{{vars}}` in the SECOND system
message. If a call transcript ever shows a literal `{{currentDay}}`, that is the
cause — put the context back inside the static prompt and keep only the persona-date
swap.

**2026-09-16 — where the three assistants ended up.** All three run **gpt-4.1** with
no reasoning key: every model trial lost. 4.1-mini ignored the day the prospect asked
for (01a0a7a6); 5.5 at minimal reasoning was slower than 4.1 and ~17x the cost
(01a0a7ef); 5.4 at low reasoning was slower again, ~6x the cost, and still repeated
the part of day (01a0ab4a: turn avg 2.64s vs 1.58s on 4.1, model 1047ms vs 627ms,
$0.26 of model spend for six turns). Don't retry a model without reading those cards.
All three also share the latency and audio settings (owner, 2026-09-16: "all the
settings rebooking has except for model"): LiveKit smart endpointing OFF,
`waitSeconds` 0, voice stability 0.6 / style 0.5, the hosted quiet room tone. The ONE
divergence is `optimizeStreamingLatency`: 3 on Rebooking, 1 on the other two, because
3 is what dropped two words from the audio on 01a0ab4a — its revert is still pending
the owner. **Watch:** endpointing off is what cut a prospect off mid-sentence on
01a0a7fe, and it now applies to the two flows that call real prospects.

**The offer lines are all pre-written now.** `{{firstOffer}}` (first offer),
`{{dayOffers}}` (one line per day, day named), `{{moreTimes}}` (one line per day AND
half of the day, day deliberately left out, `[label]` for lookup only — call 01a0ab63
re-named the day and repeated the part of day when asked for other afternoon times).
The rule of this whole session: **a line the webhook writes is spoken as written; a
line she composes is not.** That held on 4.1, 5.4, 5.5 and 4.1-mini alike. If a new
offer shape misbehaves, pre-write it rather than adding another prompt rule.

**Choppy why-calling line — in progress.** The model speaks the line exactly, but
Vapi cuts the text it sends to ElevenLabs at sentence ends. Each piece is voiced as a
fresh generation, so delivery resets at every cut. Call `01a0a281`'s log (`presignedLogUrl`,
`body == "ElevenLabs text message sent"`) shows 162 / 43 / 87-char pieces. That
happens even though Rebooking's `chunkPlan.punctuationBoundaries` is `[";"]`.
Step 1 (owner's pick) shipped after this table: `_why_calling_line` joins his three
sentences with commas, one full stop at the end. **Check the next call's log** for
how many pieces that line went out in. Commas may still be cut points. **Step 2,
owner already approved, once step 1 has been heard:** voice settings on Rebooking,
stability 0.45 → 0.6 and style 0.75 → 0.5. This smooths the pieces but doesn't
remove the cuts, and it changes every line she says.

Review cards for each call are on the CRM board "Ashley call reviews" (Rebooking
column): https://sales.c7a.io/projects/5f8d7885-6916-455b-b3fb-e67718d17a46.
Use the `ashley-call-review` skill in the sales-mission-control repo.

---

## Next up (owner already chose)

**Latency: try endpointing first.** On the last 11 calls (49 turns) the median
turn was 2.4s and p90 6.3s. Model 0.8s median / 3.0s p90. Endpointing 0.54s /
1.3s. Transcriber 0.38s. Voice 0.46s.

The plan: turn LiveKit smart endpointing off **on the Rebooking assistant only** by
dropping `smartEndpointingPlan` on its FLOWS entry in `scripts/push_confirm_squad.py`.
It then falls back to `transcriptionEndpointingPlan` (0.1 / 0.4 / 0.5s). The owner
places test calls, and you compare `artifact.performanceMetrics.turnLatencies`
against those numbers. If she starts cutting people off, keep LiveKit and give it a
less cautious wait function instead.

After that, if the model spikes remain: try `gpt-4.1-mini` on the TEST assistant
first.

Hidden latency to know about: Rebooking's voice `chunkPlan` splits only on `;`,
so she waits for the WHOLE reply before speaking. It was set that way so SSML
survives. Don't change it without a reason; lower splitting dropped words before.

---

## Open, waiting on the owner

1. **Cancel wording.** "You weren't able to jump on our call today" fits a
   no-show better than a cancel. The owner hasn't given cancel-specific wording.
2. **GHL workflow should send `"appointment_id": "{{appointment.id}}"`** to
   `/voicsito/ghl/rebooking-call`. It doesn't today (`appointment=None` in logs). The
   code already uses it when present. Without it, the missed day is inferred from
   the contact's list, and GHL drops **deleted** appointments from that list. The
   owner's test loop creates and cancels a booking on the "Z - DO NOT USE -
   Discovery Call" calendar every few minutes, and some get deleted. The CRM drip
   (`src/lib/rebooking/calls.ts` in sales-mission-control) also sends no
   appointment id.
3. **"Love to hear it" when the prospect only asked "How are you?".** Reaction
   first is a deliberate rule (2026-08-28, matches the owner's script). He decides
   whether it changes.
4. **Booking false-failure** (call 01a0a174): `book_video_call` has an 8s timeout.
   GHL booked the slot, but we reported failure, and the retry got "slot no longer
   available". Fix: check whether the contact already holds the slot before
   reporting failure, and raise the timeout.
5. **Wording for a real booking failure.** She improvised "I'll text you in a bit",
   and nothing sends that text.
6. **CRM high-priority alert when a booking fails.** This needs a new systems row
   and a Voicsito secret, and the owner must say "yes touch the env".
7. **"What company?" / "What call?"** get the epoxy-flooring who-is-this answer,
   which doesn't fit.
8. Greeting once came out "This is from Cloud Seven" and once "This is Ash". This is
   voice generation dropping a word. Watch before changing.

Watch on the next calls: whether she also says "Hold on a sec." herself (heard
twice); whether she adds words after the why-calling line; whether a second
same-day pair of times still says the day once.

---

## How to work here (learned the hard way today)

- **Deploy webhooks by blob, never the working tree:**
  `git show HEAD:<file> | ssh root@72.60.175.24 "cat > /opt/webhooks/voicsito/<file>"`.
  First check that the VPS file matches the parent commit (sha256, `tr -d '\r'`),
  and back up to `/root/voicsito-backups/<ts>/`. Afterwards: sha256 again,
  `systemctl restart voicsito-webhooks`, journal shows "route mounted", and a
  wrong-secret POST to `/ghl/outbound-call` and `/ghl/rebooking-call` returns
  **401**. `scripts/deploy-webhooks.sh` needs an SSH key this machine doesn't have.
- **The VPS has no `src/agent/prompts/`.** Webhook code must not import from
  the prompts package. Spoken text the webhook builds lives in `ghl_trigger.py`
  (e.g. `REBOOK_REASONS`, `_why_calling_line`).
- **Order: webhook first, then Vapi.** If the prompt starts reading a new
  `{{variable}}` before the webhook sends it, live calls render it empty.
  `push_confirm_squad.py` pushes whatever is on disk to all five assistants, so run
  it only with a clean tree.
- **Prompt edits go through the `prompt-authoring` skill.** That means a fresh
  subagent review and their own commit. Structure changes are allowed; wording is
  the owner's.
- **Tests:** baseline is 11 known failures (`test_booking_hardening` x6,
  `test_check_checklist_sync`, `test_server_timezone`, `test_update_prospect_info`,
  `test_webhook_integration` x2). Any other failure is yours. A test file must
  **import `server` before `ghl_trigger`**, or the routes don't mount and later
  tests 404 depending on order. Autouse fixtures in `test_ghl_trigger.py` stub
  every pre-dial network read (texts, DND, slots, triggering appointment). Add
  one for any new read.
- **Shared checkout:** other sessions commit here. Always `git commit -m ... -- <files>`.
- **Finding what happened to an appointment:** GHL's `/contacts/{id}/appointments`
  omits deleted ones. `/calendars/events/appointments/{id}` still answers
  (`deleted: true`). The CRM's Supabase `calendar_events` table keeps every status
  change with `updated_at`; it was the fastest way to see the owner's test loop.
- **Latency per turn:** Vapi `GET /call/{id}` → `artifact.performanceMetrics`.
  **Audio:** `artifact.presignedStereoUrl`, ch1 = Ashley.
