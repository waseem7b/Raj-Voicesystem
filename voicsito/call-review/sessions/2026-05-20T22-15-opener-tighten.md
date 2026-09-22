---
schema_version: 1
session_id: 2026-05-20T22-15-opener-tighten
mode: direct_edit
duration_min: 20
calls_reviewed: []
findings: []
changes: [CH-2026-05-20-006]
deferred: []
follow_ups:
  - "Pull next 10 inbound Stage 1 calls and verify the opener no longer asks 'how can I help you'."
  - "Watch for any prospect cases where the implicit text-callback assumption is wrong (e.g., someone calling for a completely unrelated reason). If it happens, decide whether to add an explicit prospect-clarification branch."
---

# Session notes — opener tighten (first real direct-edit session)

**Trigger:** "lets make some improvements to the prompts and agents. lets start off with the opening section."

**Finding (verbal, not from a specific call):** Step 1's Opening asked "how can I help you?" in two of the four prospect-response cases. We always know why the prospect is calling — we texted them. Asking the question signals the bot doesn't have the context the prospect already shared via the text callback. Robot tell.

**Edit applied (CH-2026-05-20-006):**

- Anchor changed: `"Yeah, thanks for the call [first name if known]. I'm just driving right now so I might cut out here and there, but..."` → `"Yeah, appreciate you calling [first name if known]. I'm just driving right now so I might cut out here and there."`
- Dropped trailing "but..." since some follow-up cases no longer have a continuation. When continuation IS needed (Greeting-only, Reason-only), `"...sorry, I didn't catch your name?"` provides the connector.
- "Name only" case: no continuation — Jon stops after the anchor, lets the prospect either confirm or naturally surface context, then Step 2 fires on Jon's next turn. Previous "How can I help you?" removed.
- Gate dropped the `AND a reason` requirement. Reason is implicit; only name capture gates the advance.

**Atomic-rule companion update:** `CL-005` rewritten to:
- Capture the new question (name + acknowledge-text-callback, instead of name + intent).
- Add explicit "how can I help you" / "what can I do for you" / etc. to `how_to_spot_a_fail`.
- Tighten `auto_agent_rubric` to a regex check that allows the Greeting-only exception (one allowed "sorry, I didn't catch your name?" turn).

**Deploy status:** NOT shipped. Local edit + log only. Waiting for user "ship it" before pushing to Vapi.

**Reference point at session start:** `docs/reference-points/2026-05-20-call-review-launch/` (captured earlier today; covers pre-edit state for all 5 prompt files).

**Sanity check:** Composed PROMPT still loads (SHARED_PERSONA + STAGE_SPECIFIC concatenation unchanged). check_checklist_sync.py expected to remain clean post-edit because CH-006 logs the new sha256.
