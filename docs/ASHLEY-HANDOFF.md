# Ashley outbound calls — handoff

**Latest session state (2026-09-18):** `docs/HANDOFF-2026-09-18-ashley.md` — verified live
state of all three assistants, the pre-written offer lines, why every model trial lost,
and the open production defects. Start there.
(`docs/HANDOFF-2026-09-14-ashley-rebooking.md` is the call-by-call history behind it.)

**Read this and `docs/ASHLEY-VOICE-TESTING.md` before changing anything about
Ashley's voice, pauses, or prompts.** Together they record what has already been
ruled out with evidence. Most wasted effort in this project has been retrying a
settled question or fixing a symptom whose cause was somewhere else entirely.

---

## What Ashley is

Three Vapi assistants that place outbound calls about a booked demo:

| | id | fires |
|---|---|---|
| Confirmation | `236bfb42-8368-4cff-9042-41c2aeb43a81` | day before |
| Reminder | `1b4e0bb0-8713-47ae-84f8-a6db6c5aec5b` | day of |
| Rebooking | `a19e14c4-ef68-4e37-98b3-c3e448290e71` | call-back, when the contact carries `needs-reschedule` |

**Rescheduling is its own agent (owner ruling 2026-09-04, reversing 2026-08-19).**
Confirmation and Reminder carry NO calendar tools any more. When a prospect
says they can't make it (or has nothing booked), Ashley fires
`mark_needs_reschedule` — which writes the GHL tag `needs-reschedule` in-call —
tells them in her own words she'll call back to find a time, and signs off.
GHL then POSTs **its own endpoint**, `POST /voicsito/ghl/rebooking-call`, which
dials the Rebooking assistant: the only one with `get_available_slots` +
`reschedule_video_call`. Its prompt is `src/agent/prompts/stage1_rebook.py`
(the old shared `rebook_block.py` is gone). The tag is cleared before every
dial, like the outcome tags, so the rebooking call consumes it.

**Owner action still needed in GHL:** add a branch to both the confirmation
and the reminder workflow — after the 10-minute wait, `tags include
needs-reschedule` -> webhook to the rebooking URL below. Until that branch
exists, the tag is written but nobody calls back.

### The text thread she reads before calling (2026-09-07)

Every outbound call now reads the prospect's recent SMS thread from GHL before
dialling. Two things come out of it:

- **A dial guard.** If they texted LAST and within 30 minutes, the call is
  skipped with `{"ok": true, "skipped": "live_text_conversation"}` — a teammate
  may be mid-reply and a robot must not cut in. Deliberately narrower than the
  drip's `weOweReply`, which holds on ANY inbound: somebody who asked to move
  their time last week is exactly who this call is for.
- **Context.** The thread is sanitised and handed to the rebooking prompt as
  `{{recentTexts}}`, so she can pick up where the texts left off instead of
  starting cold.

`src/agent/ghl.py:get_recent_texts` does the reading (conversation search, then
that conversation's messages; short timeouts, returns `[]` on any failure, and
the trigger guards it again because this is the dial path).
`src/webhooks/text_digest.py` does the guard logic and the sanitising.

**The bodies are written by the prospect and land in the same context window as
her instructions**, so they are treated as hostile input. The sanitiser strips
braces, angle brackets and backticks, removes control and zero-width characters, collapses whitespace so
one message cannot fake a second speaker turn, truncates each body and caps the
block. The prompt carries the other half: a gate saying those lines are a record
of what was typed and nothing in them is an instruction. Neither half is
sufficient alone.

**Only the rebooking prompt reads `{{recentTexts}}` today.** The dial guard
applies to confirmation and reminder too, but their prompts do not show the
thread yet.

**Known gap: Main CRM only.** `get_recent_texts` reads the Main CRM conversation.
The Texts CRMs (`texts1`, `texts2`) hold the earlier relationship and are not
read. Voicsito already has tokens for all three, so extending is a small change.

### The three outbound endpoints

| flow | endpoint | body |
|---|---|---|
| confirmation | `POST /voicsito/ghl/outbound-call` | `call_type: confirmation` |
| reminder | `POST /voicsito/ghl/outbound-call` | `call_type: reminder` |
| rebooking | `POST /voicsito/ghl/rebooking-call` | `type`: `no-show`, `cancelled`, or omit |

Host is `https://webhooks.c7a.io`, header `X-GHL-Secret: $GHL_OUTBOUND_SECRET`
on all three.

**Why rebooking gets its own path (owner 2026-09-07).** The CRM runs a
completely separate demo-rebooking **text** drip at
`sales.c7a.io/api/systems/rebooking/entered`. That endpoint accepts almost any
payload and returns 200, so a rebooking CALL aimed at it fails silently. That
is exactly what happened on 2026-09-07: a test enrolled a contact in the SMS
drip and no call was ever placed. Two systems, two paths, so the wrong URL
cannot quietly succeed.

**One rebooking endpoint, three reasons (owner 2026-09-07).** The body's `type`
picks the reason: `no-show`, `cancelled`, or omitted for the original case
(they told Ashley on a confirmation or reminder call that the time no longer
works). Spelling is forgiving (`no_show`, `noshow`, `canceled`, any case); an
unrecognised value is a 422 rather than a silent default.

**The SCRIPT does not change between them.** Same assistant, same flow, same
anchors, byte for byte. What changes is one sentence of context rendered into
`{{callReason}}` by Vapi before the model runs, from `REBOOK_REASONS` in
`src/webhooks/ghl_trigger.py`. It exists so she does not say something false,
like apologising for missing someone who cancelled on purpose. It is
model-facing only and never spoken. If the owner later supplies distinct
wording per reason, THAT is when a branch earns its place — not before.

Point the no-show and cancelled paths at whatever GHL trigger you already use
for the demo-rebooking text drip, if you want the call alongside the texts.
Nothing coordinates the two, so a prospect wired into both gets a call AND the
drip's opener.

Every rebooking path ignores `call_type` in the body, so a step copy-pasted
from the confirmation workflow still dials the right assistant. It also skips
the live-appointment-status guard: that guard stops a confirmation call asking
a cancelled prospect "are we still on?", but putting that prospect back on the
calendar is this call's whole purpose.

Manual test, no workflow involved:

```bash
curl -sS -X POST https://webhooks.c7a.io/voicsito/ghl/rebooking-call \n  -H "X-GHL-Secret: $GHL_OUTBOUND_SECRET" \n  -H 'Content-Type: application/json' \n  -d '{"phone":"+1XXXXXXXXXX"}'
```

**Owner wording still needed (currently "in your own words" in the prompts):**
1. Confirmation/Reminder, can't-make-it branch: the line where she says she'll
   call back to find a time (`stage1_confirm.py` / `stage1_reminder.py`).
2. Rebooking, Step 3: the one sentence saying why she's calling back.
3. Rebooking, none-of-the-times-work: the line before the sign-off.
4. Rebooking opener: currently reuses the Reminder greeting word for word.
5. Rebooking, no-show and cancelled: she says why she's calling in her own
   words for all three reasons. A no-show and a cancel probably want their own
   line acknowledging the miss without blaming. A gate already forbids asking
   them to account for it.

GHL workflow → `POST webhooks.c7a.io/voicsito/ghl/outbound-call` → our VPS
resolves the booking → `POST api.vapi.ai/call` with `variableValues` →
Twilio dials. Vapi then orchestrates Deepgram (ears), GPT-4.1 (words) and
ElevenLabs (voice) on every turn.

Deploy: `python scripts/push_confirm_squad.py` for prompts + assistant settings.
`VPS_HOST=root@72.60.175.24 SSH_KEY=~/.ssh/id_ed25519_c7avps bash scripts/deploy-webhooks.sh`
for the webhook server. **The VPS is 72.60.175.24 — the same box as the CRM.**
The script's default Tailscale hostname does not resolve from the laptop; use
the override. (A whole session was lost to believing there was no VPS access.)

---

## THE OPEN PROBLEM: pauses

The owner wants audible beats between sentences. Nothing has delivered this yet.
Every mechanism has been tried and each fails for a *different* reason:

| tried | result |
|---|---|
| `<break time="1s" />` SSML | Model emits it, Vapi delivers it, ElevenLabs **echoes the preceding word**. 46+ calls, both TTS models, with and without competing prompt rules. |
| `...` and single `—` at sentence ends | Vapi's chunker splits **through** the mark, sending orphans: `".  He's got"`, `"—. Umm"`. |
| `----` (hyphens) at sentence ends | Silent — swallowed, no pause and no harm. |
| `————` jammed to the word | **Pronounced**: "with John J. P.", "know, Io.", "tomorrow, like,". |
| ` ————` with a leading space | Silent mid-chunk, **voiced at a chunk end**: "later, V", "with John e". |
| ` ———` moved to commas (current) | Never at a chunk end, so never voiced — **but sentences now run together**, which is the owner's latest complaint. |

**The mechanism, now well understood:** Vapi splits her reply into chunks at
`. ! ?` and hands each to ElevenLabs as a separate generation. A marker that
lands at a chunk *end* gets vocalised; mid-chunk it is silent. The marker sat
just before terminal punctuation — which is exactly where chunks break — so it
was at a chunk end ~9 times out of 11. Moving it to commas fixed the vocalising
(commas are not split boundaries) but removed every sentence-boundary beat.

**That is the current state and the current complaint.** Sentence pauses and
silent markers appear to be mutually exclusive through this pipeline.

Unexplored: pre-rendered audio for the greeting via `firstMessage` as an mp3 URL
(Jon's squad already does this — `scripts/push_squad.py:284`), which bypasses
chunking and TTS entirely for that line. Needs an `ELEVENLABS_API_KEY`; Vapi
holds the credential, we do not. `scripts/tts-test.py` already calls the EL API.

---

## RECENTLY FIXED — do not re-diagnose

- **Empty `bookedTime`** caused invented times ("two in the afternoon", "your
  time") and `"for the call at ___ later today"`. Cause was NOT the prompt:
  `CONFIRM_CALENDAR_ID` (lookup) pointed at a test calendar while bookings went
  to `VIDEO_CALL_CALENDAR_ID`. Flipped and deployed 2026-08-31. Several prompt
  gates were written against these symptoms and all failed.
- **Flattened greeting** ("Hey. Ryan. Evening.") was our own `{Name}` notation,
  which Vapi does not render — so the model *assembled* the line each call.
  Now `{{firstName}}` / `{{greeting}}`, rendered server-side.
- **Comma splitting** cut the 38-word joining line into three generations.
  `chunkPlan.punctuationBoundaries` restricted to `. ! ?`, `minCharacters: 80`.
  Chunks per call: 23 → 10.
- **Car engine background** — Ashley inherited Jon's whole voice block via
  `**mono["voice"]`. Now every voice field is declared explicitly.

---

## STILL BROKEN

1. **Sentences merge** (above) — the live complaint.
2. **Whole-turn repeats.** She has delivered the greeting twice, and the entire
   Step 3 block twice. Prompt-side, not chunking.
3. **Words dropped at chunk joins.** `"It's Ashley."` + `"How've you been?"`
   came back "It's Ash How have you been?" — no marker involved. Reduced by
   chunkPlan, not eliminated.
4. **Prompt bloat.** Was ~24k chars, up from 16.5k. Gates went 22 → 42 while
   spoken anchors went 15 → 19. Several gates were written for symptoms whose
   causes turned out to be the calendar or the chunker; they are dead weight now
   and the failure mode has shifted from "rewrites wording" to "loses the
   thread". 2026-09-04: the rebook branch (~3k) moved out to its own assistant —
   Confirmation is now ~21.6k, Reminder ~20.5k. The dead gates are still there.
5. **No voicemail message.** She hangs up silently on machines — when she
   notices them at all. Across the 180 outbound calls to 2026-09-07, 33 reached
   a machine and on 13 of those she delivered two or more turns of script into
   it (11.2 minutes). Two causes, both addressed 2026-09-07:
   - **In-call:** `voicemailDetection` was inherited from Jon —
     `{provider: google, type: audio}` with no `backoffPlan`, so it ran on
     defaults while `assistant-speaks-first` had Ashley talking over the
     greeting. Now declared explicitly in `push_confirm_squad.py` as
     `provider: vapi` (the one documented to handle that overlap) with
     `startAtSeconds 2 / frequencySeconds 2.5 / maxRetries 10`, which keeps
     checking to ~27s. Shape verified 200 against the TEST assistant.
     **NOT YET PUSHED to Confirmation / Reminder / Rebooking.**
   - **Post-call:** the outcome classifier read the carrier "Thanks. Please
     stay on the line." opener as the prospect talking, so 7 voicemails went
     out tagged `call-answered` — which is what stops the GHL retry, text and
     voice note. Fixed in `outbound.py`; machine evidence anywhere in a call
     now outweighs a stray fragment, and a pickup takes a real turn to claim.

---

## DEFERRED — call screening (owner tabled it 2026-08-31)

Vapi has NO call-screening detection. Checked the full voicemail-detection docs:
five providers (`vapi`, `google`, `openai`, `twilio`, beta voicemail tool), two
types (`audio`, `transcript`), tunables `backoffPlan.startAtSeconds` /
`.frequencySeconds` / `.maxRetries` and `beepMaxAwaitSeconds`. Screening is not
mentioned — the feature only targets answering machines.

Doing it would take three pieces: a screening branch in the prompt (needs the
owner's wording — Ashley's lines are his), Deepgram keyterm boosting on the
screening phrases (transcriber is nova-3 with `keywords: 0` today, no boosting),
and switching `voicemailDetection.provider` google -> vapi, whose provider is
documented as gracefully handling a human picking up mid-voicemail — the exact
screening shape.

**The conflict to remember:** `assistant-speaks-first` (added 2026-08-31 for the
opener) makes screening strictly worse — she talks over the screener and never
hears it. `assistant-waits-for-user` is better for screening but brings back the
model-generated, chunked greeting and its "It's Ash" word drop. One setting
cannot do both.

---

## HOW TO GET REAL EVIDENCE

`messagesOpenAIFormatted` and `artifact.transcript` are **sanitised** — Vapi
strips SSML from them. Two conclusions in this project were wrong until the raw
log was read. Always use the gzipped call log:

```python
art = httpx.get(f"https://api.vapi.ai/call/{cid}", headers=H).json()["artifact"]
txt = gzip.decompress(httpx.get(art["presignedLogUrl"]).content).decode("utf-8")
# body == "Model output"                 -> raw LLM tokens
# body == "ElevenLabs text message sent" -> the exact chunk sent to TTS
```
Empty while a call is `in-progress`; wait for `status: ended`.

---

## OWNER'S RULES THAT BIT US

- His wording is his. Structure, gates and ordering are editable; the words she
  speaks are not. Ask before changing a phrase.
- One variable per test call, with a git tag as the revert point.
- Do not declare something impossible after one failed attempt. It has happened
  twice: "the model never emits break tags" (it always did) and "there is no
  VPS access" (there was, all along).

## CURRENT SETTINGS

`eleven_turbo_v2_5` · speed 0.90 · stability 0.45 · style 0.65 · similarity 1.00
· `optimizeStreamingLatency 1` · `chunkPlan {minCharacters 80, ". ! ?"}`
· `backgroundSound office` · `smartEndpointingPlan livekit` · gpt-4.1 temp 0.2
· sign-off `THANK YOU` / `BYE` in caps · `endCallPhrases ["BYE","Byeee","Bye for now","Bye"]`
