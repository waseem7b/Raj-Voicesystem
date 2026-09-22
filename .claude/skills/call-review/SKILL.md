---
name: call-review
description: Voicsito's structured call-review skill. Invoke when the user says "review calls", "review call <id>", "let's review calls", "call review", "review last N calls", or any direct-edit phrase like "let's adjust the opener" / "change X in the script". Drives one-call-at-a-time review (or batch / direct-edit) against the live rubric. Enforces freshness + atomic rule on every prompt edit. Composes with prompt-update-discipline (mandatory before any prompt edit) and prompt-review (optional Hormozi mode-shift). Spec at docs/superpowers/specs/2026-05-20-call-review-system-design.md.
---

# call-review — Voicsito review session orchestrator

## When you enter this skill

Acknowledge in one sentence: "Entering call-review mode for <single-call|batch|direct-edit>." Then load the standing context and wait for the user's next move.

## Standing context — load at session start

Always read these (in this order):
1. `voicsito/call-review/checklist/CL-*.yaml` — the rubric (every item).
2. `voicsito/call-review/sessions/INDEX.md` — find the last 2 sessions; read their YAML frontmatter only.
3. Grep `voicsito/call-review/changes/CH-*.yaml` for any entry with `created` in the last 7 days. Build a one-line banner per matching change: `<id> | <stage> | <section> | <one-line hypothesis>`. Surface this banner ONCE at the start of the session. Do NOT re-read changelog entries on every turn.
4. Read the prompt files you expect to touch: `src/agent/prompts/{shared_persona,stage1_inbound_texts,stage2_qualify,stage3_book}.py`.

## Modes (dispatched from trigger phrasing)

### Single-call mode
Trigger: user names a Vapi call id, or "review this call", or "review call <id>".

1. Run: `python scripts/fetch_call.py --id <vapi-id>` and ingest the resulting JSON.
2. Surface a 1-line summary in chat: `<call_id> — <duration>s — <ended_reason> — stage_reached=<stage>`.
3. Wait for the user's notes. They will narrate from their Google Doc — paste-or-verbal both fine.
4. For each finding, you do one of:
   - tag against an existing `CL-NNN` and create an `observations/OBS-*.yaml`
   - propose a new `CL-NNN` if the pattern is new
   - propose a prompt edit (subject to freshness contract + atomic rule below)
5. At session end: write `voicsito/call-review/sessions/<ISO-timestamp>-<short-id>.md`, run `voicsito/call-review/render.py`, run `scripts/check_checklist_sync.py`.

### Batch mode
Trigger: "review last N calls" or "review calls filtered by <key>=<value>".

1. Run: `python scripts/fetch_call.py --last <N> --filter <k>=<v>`.
2. Surface a per-call 1-line summary. Do NOT auto-deep-dive any one call.
3. Look for cross-call patterns; capture them as observations with `pattern_type: cross_call`.
4. Single-call deep-dives within a batch are fine — just pivot to single-call mode for that call.

### Direct-edit mode
Trigger: any phrase asking to change a prompt section without referencing a call ("let's tighten the reveal", "change X to Y").

1. No call pull. Skip to the freshness contract.
2. Edit proceeds normally; observation entry uses `pattern_type: direct_edit` and `call_id: null`.

## Freshness contract — runs before EVERY prompt edit

You may NOT call the Edit tool on any file under `src/agent/prompts/` until you have done these in this order:

1. **Read the target file fresh.** Even if you "just" read it three turns ago. Re-Read immediately before staging the diff. The Edit tool requires this anyway; the skill restates it as a hard step.
2. **If the edit touches voice ID / tool ID / assistant ID / squad ID:** pull live Vapi config first. Do NOT trust documentation. Use `curl` with `Authorization: Bearer $VAPI_API_KEY` against `https://api.vapi.ai/{assistant|tool|phone-number|squad}` to confirm current values. (This is what the user pulled this morning when the docs claimed F2BOg7Ry3zTPV6Zd62Cd was a stock voice — it was the deleted Jon PVC.)
3. **Confirm a checklist update is planned for the same turn.** Either you are editing an existing `checklist/CL-*.yaml`, or you are creating a new one, or you can articulate why this edit qualifies for `escape_reason` (typo/whitespace/comment-only).

## Atomic rule — same turn, no exceptions

Every prompt-file Edit (under `src/agent/prompts/`) must be paired in the **same turn** with one of:

- a new or modified `voicsito/call-review/checklist/CL-*.yaml`, AND a new `voicsito/call-review/changes/CH-*.yaml` entry
- OR a new `voicsito/call-review/changes/CH-*.yaml` entry whose `escape_reason` field is non-empty AND describes a non-behavioral edit

**End-of-edit verification:** call `python scripts/check_checklist_sync.py`. If it exits non-zero, your work is incomplete — fix the drift before continuing.

**Escape valve scope:** `escape_reason` is acceptable ONLY for typos in comments, whitespace, file-header reformatting, dead-code removal, docstring polish. Any change to wording the LLM reads, gate logic, tool routing, conditional branches, or step ordering is behavior-affecting and MUST pair with a checklist update.

## Composition with other skills

- **`prompt-update-discipline`** — **MANDATORY** before any prompt edit. Invoke it directly; it enforces word-preservation hygiene and the snapshot-to-reference-points step.
- **`prompt-review`** — **OPTIONAL**. Invoke only when the user explicitly enters sales-thinking mode (e.g., "let's attack the reveal" or "let's Hormozi this"). Skip when the user wants a structural / mechanical edit.
- **`live-system-testing`** — DO NOT invoke during review sessions. Reviews are read-only on third-party APIs.

## Read-only safety (HARD prohibition)

During a review session you have access to real prospect data via Vapi / GHL APIs. You MUST NOT:

- POST/PUT/DELETE to GHL (no contact updates, no calendar mutations, no tag changes)
- POST/PUT/DELETE to Twilio
- POST/PUT/DELETE to Cal.com / any calendar provider
- Call the booking webhook in any non-`_test_dry_run: true` mode

The ONLY write operations allowed in a review session:
- Local file writes (YAMLs, prompt files, snapshots)
- Vapi assistant PATCH **only** on explicit user "ship it" — and that PATCH touches assistant configs (model/voice/prompt), never prospect records

## Reviewer disagreement protocol

When Jon and Waseem score the same call differently:
- Default tie-breaker is Waseem's read.
- BUT: capture both reads in `observations/OBS-*.yaml` `notes` field with reviewer names, mark `confidence: low`, and rewrite the checklist item's `how_to_spot_a_fail` to remove the ambiguity that produced the disagreement.

## "Ship it" flow

User says "ship it" or "push it" or "deploy". You must:

1. Run `python scripts/check_checklist_sync.py` — block on non-zero exit.
2. Run `python scripts/snapshot_reference_point.py --reason <kebab-short> --note "<one-sentence>"` — capture the pre-deploy state.
3. (Optional, recommended for behavior-changing edits) Run `.venv-tests/bin/python scripts/sim-call.py` against the canonical personas; block on any judge failure.
4. Run `python scripts/push_squad.py`.
5. Update the relevant `changes/CH-*.yaml` with `deployed: true`, `deployed_at: <ISO>`, `reference_point: docs/reference-points/<dir>/`.
6. Run `python voicsito/call-review/render.py` to refresh INDEX views.
7. **Git commit + push.** Run: `git add -A` then `git commit` with a one-line message in this format: `<CH-ID> <file-or-section>: <5-7 word description>` — e.g. `CH-2026-05-27-001 shared_persona: remove boss man from casual vocab`. Use the session context (which CH entry, which section, what changed) to write the message. Append the standard co-authorship trailer: `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`. Then run `git push`.
8. Confirm to the user with the call IDs that drove this change + the verify-by note.

## End-of-session

Always:
1. Write the session log to `voicsito/call-review/sessions/<ISO-timestamp>-<short-id>.md` with YAML frontmatter (mode, calls_reviewed, findings, changes, deferred, follow_ups) + free-form narrative.
2. Run `python voicsito/call-review/render.py` — refresh all four INDEX files.
3. Run `python scripts/check_checklist_sync.py` — confirm clean exit. If dirty, surface the drift and fix before closing the session.
4. Brief one-line summary to user: "Session done. N findings, M changes shipped, K deferred. Drift check: clean."
