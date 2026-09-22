---
schema_version: 1
session_id: 2026-06-04-opener-text-reason-vs-business
mode: direct_edit
duration_min: 40
calls_reviewed: []
findings: []
changes: [CH-2026-06-04-002, CH-2026-06-04-004]
deferred: []
follow_ups:
  - "NOT pushed to Vapi. CH-2026-06-04-002 + CH-2026-06-04-004 are local only (deployed:false). Run push_squad.py on explicit 'ship it'."
  - "shared_persona.py first-name-frequency drift (CH-2026-06-04-003 backfill, CL-031) is also deployed:false — confirm its Vapi push status separately."
  - "Optional: fold a text+name opener check into a bank persona (curious_first_timer has no expectations) so this is covered without one-shots going forward."
---

# Session notes — opener: text-mention = reason vs. bare business-name (direct edit)

**Trigger (operator, no specific call):** "If they mention the text message, acknowledge it 'thanks for getting back to me' — also when they say the business name. This might be conflicting with the name rule. If they ever mention texts (any version of that word) use that as the context to move to the reveal."

**Scope clarified (AskUserQuestion):** keep Step 2 (advance toward the reveal, do not skip the services question); name-first (capture the name before advancing if still missing).

## What the script already did
- The `"thanks for getting back to me"` acknowledgment already fires on text-mention OR business-name (Step 1 anchor exception).
- The follow-up table already routed text (= reason) toward Step 2 and bare business name (no reason) to "how can I help you".
- The real seam: the acknowledgment groups the two triggers, so the model can blur them.

## Edits
1. **CH-2026-06-04-002 / CL-030 (new):** added a bullet pair under the follow-up table making the distinction explicit — text mention IS the reason (advance; grab name first if missing); a bare business name is NOT a reason (ask how you can help). Structure-only, no anchor wording. `b865350e…` → from `42234ab…`.
2. **CH-2026-06-04-004 / CL-030 (modified):** condition fix. Sim revealed the bullet alone did not fix the **name + text in one utterance** case ("I got your text — it's Mike") — Jon still asked "how can I help" on 3/3 runs because the "name … no reason → how can I help" branch matched (model didn't register the text as the reason). Added "and no text mention" to that branch condition so name+text routes to the "reason → advance" branch. `b865350e…` → `d68c8bb3…`.

## Verification (local sim only — NOT pushed)
One-shots built under `tests/personas/_oneshot/` (base: curious_first_timer): text-name, text-no-name, business-name. Transcripts in `tests/transcripts/14-opener-text-reason/`.
- Pre-fix (CH-002 only): text-name **0/3** (runs 1,4,5 all re-asked "how can I help"); text-no-name 3/3; business-name 2/2.
- Post-fix (CH-004): text-name **3/3 on each of 3 runs** (runs 6,7,8); text-no-name 3/3 (run 9); business-name 2/2 (run 10).
- Transcript read (run-6): Jon acknowledges the text + stops, then advances to the Step 2 services question. Driving caveat fires once (CL-028 intact).

## Drift note
`check_checklist_sync.py` was red at session start on `shared_persona.py` (uncommitted first-name-frequency rule). It was backfilled by **CH-2026-06-04-003 / CL-031** (separate closing-stall session) during this work; sync is green at session end. That edit and both opener edits remain `deployed:false`.
