---
schema_version: 1
session_id: 2026-05-31-opener-driving-caveat-once
mode: direct_edit
duration_min: 25
calls_reviewed: []
findings: []
changes: [CH-2026-05-31-001, CH-2026-05-31-002]
deferred:
  - "SSML <break/> output exception (commit 4724448) is now documented in CH-2026-05-31-002 but has no rubric item. Create a CL if rubric coverage is wanted."
follow_ups:
  - "Run a multi-turn Stage 1 sim (greeting-only -> name, or name-without-reason) to confirm the driving caveat fires once and drops on turn 2 before any Vapi push. Opener change is NOT YET VERIFIED."
  - "Verify the resume-from-cutoff behavior (CL-021) on a real/sim call where the prospect interrupts mid-anchor."
  - "Confirm whether d1b970e / 4724448 shared_persona changes are live on the Vapi squad (CH-2026-05-31-002 left deployed:false — push status unverified)."
---

# Session notes — opener driving-caveat one-time rule (direct edit)

**Trigger:** "we want to update the opening script" → narrowed (AskUserQuestion) to the Step 1 anchor, then to the "I'm just driving right now" caveat.

**Finding (operator-reported, no specific call):** Jon repeats the driving caveat ("I'm just driving right now so I might cut out here and there") across Stage 1 turns. Root cause: the caveat is baked into the Step 1 anchor, and Step 1 can span multiple turns (greeting-only → ask name; name-only → "how can I help you?"; name still missing → re-ask). With no rule scoping the caveat to the opening utterance, GPT-4.1 re-renders it each Step 1 turn.

**Edit applied (CH-2026-05-31-001):** structural one-time-delivery rule inserted under the Step 1 anchor in `stage1_inbound_texts.py`:
> "The driving caveat is a one-time line." "I'm just driving right now so I might cut out here and there" belongs to this opening utterance only. On any later Step 1 turn — re-asking for the name, or asking how you can help — speak without it.

Locked caveat wording untouched (structure-only, per CLAUDE.md RULE 2). Positive-led with the failure mode named (prompt-update-discipline principles 2/3). Paired with **CL-028** (new: "driving caveat delivered at most once", auto-rubric anchored on `just driving`, deliberately not `cut out` to avoid the Bluetooth AI-challenge line).

**Drift discovered + fixed (CH-2026-05-31-002, backfill):** the sync gate was red on `shared_persona.py` — two committed changes after CH-2026-05-28-002 had no ledger entry:
1. `d1b970e` (2026-05-30): interruption rule → "resume from the exact sentence where you were cut off — never re-recite from the top."
2. `4724448`: Plain-spoken-English SSML `<break/>` timing-marker exception.

Backfilled one CH (after-sha = current on-disk sha, the value the gate compares against — note CRLF working-tree shas differ from `git show` LF-blob shas). Extended **CL-021** (interruption handling) with a resume-from-cutoff / no-re-recite criterion per operator choice. SSML change documented but left without its own rubric item (deferred).

**Gate:** `check_checklist_sync.py` → clean (exit 0). `render.py` → all four INDEX files refreshed.

**Not done this session:** no Vapi push, no real/sim call. Both behavior changes remain unverified on a call.
