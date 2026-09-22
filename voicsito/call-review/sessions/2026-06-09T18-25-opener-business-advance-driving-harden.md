---
schema_version: 1
session_id: 2026-06-09-opener-business-advance-driving-harden
mode: direct_edit
duration_min: 45
calls_reviewed: []
findings: []
changes: [CH-2026-06-09-001, CH-2026-06-09-002, CH-2026-06-09-006, CH-2026-06-09-003, CH-2026-06-09-004, CH-2026-06-09-005]
deferred: []
follow_ups:
  - "BEHAVIOR NOT YET VERIFIED ON A CALL. Operator chose 'ship now, skip sims.' Live prompt CONTENT was verified present via Vapi pull, but no sim/real call exercised the new behavior. Run a Stage 1 sim (business-name opener, trade opener, multi-turn driving) to confirm #1/#3 actually fire before trusting them on live calls."
  - "stage2 6->5 discovery rework (CH-2026-06-09-006 / CL-032) shipped as part of the full-squad push but was pre-existing working-tree WIP — its review rationale was NOT captured. CONFIRM with operator: is the five-question set + lead_source/job_volume handoff the intended contract? Verify Stage 3 / booking webhook does not depend on the removed referral_dependence / capacity_for_more fields."
  - "Confirmation-flow prompts (stage0_router, stage1_confirm, confirm_persona) were ledger-backfilled (CH-2026-06-09-003/004/005, bootstrap escape_reason) to clear the gate. They are NOT shipped by push_squad (separate confirm squad / push_confirm_squad.py). Give them real rubric coverage when the confirmation flow is reviewed."
---

# Session notes — opener: business name/trade advances + driving caveat hardened (direct edit, full-squad ship)

**Trigger (operator, no specific call):** "work on the intro script" → narrowed to three observed live-call failures:
1. Jon asks "how can I help" when the caller already gave their business name / said what they do.
2. When the caller mentions the text, just move into the pitch.
3. Jon repeats the driving caveat — add a clause to never repeat it once said.

## Diagnosis (live Vapi pull, stage1 id 46e47e95…, pre-edit updatedAt 2026-06-08T23:37)
- **#3 driving:** the one-time rule was ALREADY live but purely positive-framed and scoped only to "later Step 1 turns." GPT-4.1 re-renders it (likely on resume-after-cutoff / Step 2-3).
- **#2 text:** the 06-04 text-routing fix (CH-2026-06-04-002/004) was in the working tree but `deployed:false` — confirmed MISSING from live. A deploy gap, not a wording defect.
- **#1 business name:** genuine NEW change. Live + on-disk both treated a bare business name as NOT a reason → "how can I help" (CL-030, decided 2026-06-04). Operator now wants the reverse.

## Edits (stage1_inbound_texts.py)
1. **CH-2026-06-09-001 / CL-030 (reversed):** follow-up routing — a business name or stated trade now counts as established context → acknowledge in Jon's own words and advance to Step 2; only a bare name with zero context still routes to "how can I help." "gotcha" left as guidance, NOT locked (operator: "he can use other words"). `d68c8bb3…` → `29bda3be…`.
2. **CH-2026-06-09-002 / CL-028 (hardened):** driving caveat rule gets an explicit prohibition ("Once you have said it, do not say it again on any later turn") + scope broadened to the services question, the reveal, and resume-after-cutoff. Locked "I'm just driving…" phrase untouched. `29bda3be…` → `326f1578…`.

## Ledger hygiene
- **CH-2026-06-09-003/004/005 (backfill):** stage0_router / stage1_confirm / confirm_persona had no ledger entries (new on feat/outbound-demo-confirmation), blocking the gate. Bootstrap escape_reason entries restore green.
- **CH-2026-06-09-006 / CL-032 (backfill):** stage2 carried an unlogged 6→5 discovery rework; the gate missed it because stage2's only prior entry (bootstrap) suppresses the sha check via escape_reason. Recorded factually + provisional rubric so the deploy is tracked and the gate now strictly covers stage2.

## Ship (full squad, operator choice)
- Pre-deploy snapshot: `docs/reference-points/2026-06-09-opener-business-advance-driving-harden/`.
- `push_squad.py` PATCHed stage1/2/3 + squad ✓. Live pull confirmed all markers present (stage1 + stage2 updatedAt 2026-06-09T18:25).
- Shipped bundle: opener (#1/#2/#3) + stage3 stall-filler scoping (CH-2026-06-04-001) + shared_persona name-frequency rule (CH-2026-06-04-003) + stage2 6→5 rework (CH-2026-06-09-006). Marked deployed:true; also reconciled stale flags on CH-2026-05-31-001/002 and CH-2026-06-04-002/004 (their content is now live).

## Verification status
Live prompt CONTENT verified present. Call BEHAVIOR **NOT YET VERIFIED** — sims skipped per operator. See follow_ups.
