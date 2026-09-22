# Call-Review System — Design Spec

**Status:** Drafted 2026-05-20. Pending implementation. Approach validated by 3-agent roundtable audit (sales / ops / engineering perspectives).
**Owner:** Waseem (driver) + Jon (co-reviewer of calls offline)
**Implementation horizon:** Build immediately; run manual phase for ~2 weeks; revisit gates before automating.

---

## Motivation

Voicsito's Type 2 (Inbound Texts) flow is live. Now we need a structured, repeatable way to **review real calls, find what the agent does wrong, fix the prompts, and learn what works — without losing what we learned**.

We will run this manually for ~2 weeks. Every manual session must produce structured artifacts that later become the training corpus and rubric for an automated review agent. If the manual phase produces only free-form notes, the auto-agent will be built on a foundation it can't actually use.

The design's load-bearing claim: **today's manual review work IS tomorrow's automated review agent.**

## Non-goals

- Not building the auto-review agent itself. That comes after manual phase stabilizes.
- Not replacing `prompt-review` (Hormozi mode-shift) — composing with it.
- Not building staging/canary deploy infrastructure. Sim-call.py becomes an opt-in deploy flag; full staging is out of scope.
- Not pulling full transcripts to local disk. Only the specific quotes that matter get persisted, inside finding YAMLs.

## Architecture overview

```
.claude/skills/call-review/
└── SKILL.md                          # the skill itself (trigger + flow + gates)

voicsito/call-review/                  # project artifacts (data, grows over time)
├── checklist/
│   ├── CL-001.yaml … CL-NNN.yaml     # one item per file (structured)
│   └── INDEX.md                       # generated human-readable rubric view
├── sessions/
│   ├── 2026-05-22T14-30-<id>.md      # one file per review session
│   └── INDEX.md                       # generated
├── changes/
│   ├── CH-2026-05-22-001.yaml         # one file per prompt edit
│   └── INDEX.md                       # generated
├── observations/
│   ├── OBS-2026-05-22-001.yaml       # one file per finding-that-didn't-become-an-edit
│   └── INDEX.md                       # generated
└── render.py                          # regenerates the three INDEX.md views

scripts/
├── fetch_call.py                      # canonical Vapi-pull helper (used by skill)
└── check_checklist_sync.py            # validates atomic rule on demand + at session-end
```

**Why split skill internals from project artifacts:**
- `.claude/skills/` is harness-coupled. Durable project data lives in the project tree.
- `voicsito/call-review/` is discoverable, greppable, and obviously part of the codebase.
- Generated `INDEX.md` files give the human a single-pane skim without us hand-maintaining a growing log.

## Triggers and session modes

The skill activates on any of:

| Trigger phrase | Mode |
|---|---|
| `review call <vapi-id>` | Single-call review |
| `review calls` / `let's review calls` / `call review` | Ambiguous → skill asks which mode |
| `review last N calls [filtered by X]` | Batch / cross-call pattern review |
| `let's adjust the <section>` / `change <X> in the script` | Direct-edit, no call pull |

Internally one unified flow with three optional context-loading paths (no call / one call / N calls). Trigger phrasing is the user surface; mechanics are shared.

### Single-call mode
1. Skill loads (see "What gets loaded" below).
2. Skill invokes `scripts/fetch_call.py --id <vapi-id>` — normalized dict with `transcript`, `summary`, `endedReason`, `duration`, `recordingUrl`, `customer.number`, `assistantName`, `stageReached`.
3. Skill surfaces 1-line summary + the full transcript in context.
4. User narrates Google-Doc notes; pasted text and verbal narration both work.
5. Each finding gets tagged against existing CHECKLIST items or proposes a new one.
6. When a finding triggers an edit, the skill follows the freshness contract + atomic rule.

### Batch mode
1. User asks: `review last 10 calls filtered by biz_type=cleaning` (or `endedReason`, `stageReached`, `objection_raised`).
2. Skill pulls list, surfaces per-call summaries.
3. We look for cross-call patterns. Findings become `observations/` entries with `pattern_type: cross_call`.
4. Cross-call patterns can promote to a new checklist item.

### Direct-edit mode
1. User: `let's tighten the reveal.`
2. No call pull. Skill loads the relevant prompt file fresh, the checklist items in that category, and recent change banners.
3. Edit + checklist update atomically (same turn).

## What gets loaded at session start

```
ALWAYS:
  - voicsito/call-review/checklist/CL-*.yaml      (the rubric — small, current)
  - voicsito/call-review/sessions/<last-2>.md    (recency context)
  - Recent-changes banner: grep changes/CH-*.yaml from last 7 days,
    surface 1-line summary per change touching files we're about to edit
  - Current prompt files we'll touch

ON DEMAND:
  - voicsito/call-review/changes/*.yaml          (only when user asks
                                                  "did we already try X?" or
                                                  "show me what changed last week")
  - voicsito/call-review/observations/*.yaml     (only when explicitly referenced)

PER CALL (if applicable):
  - scripts/fetch_call.py <id>                    (transcript + metadata)
  - Vapi assistant config (if voice/tool/assistant ID involved in change)
```

The recent-changes banner replaces "always read the full changelog" — it's the cheap recency check that solves "you forgot what changed last week" without dragging the full log into context.

## Pre-edit freshness contract

Every prompt edit during a session runs these steps in order. No exceptions.

1. **Read target prompt file fresh.** Edit tool enforces a recent Read; the SKILL.md restates this as a hard step.
2. **Pull live Vapi config IF the edit touches voice ID / tool ID / assistant ID / squad ID.** Don't trust docs (see 2026-05-20 incident where docs claimed `F2BOg7Ry3zTPV6Zd62Cd` was a stock voice — it was the deleted Jon PVC).
3. **Atomic rule: stage the prompt edit AND the corresponding checklist update in the same turn.** Enforced by `scripts/check_checklist_sync.py` (see below).

## The atomic rule (enforced)

**Rule:** every prompt-file modification under `src/agent/prompts/` must be paired with either (a) a new or modified `voicsito/call-review/checklist/CL-*.yaml`, OR (b) a `changes/CH-*.yaml` entry whose `escape_reason` field is set with a justification.

**Enforcement (no git — project is not a repo):** `scripts/check_checklist_sync.py` is a manual gate runnable on demand and called by the skill at session end. It scans:
- Each prompt file's current sha256 vs the most recent `changes/CH-*.yaml` entry that references it.
- If a prompt's content sha256 doesn't match any CH entry's `prompt_file_sha256_after`, the sync is dirty: the prompt drifted without being logged. Exit non-zero with a clear list.

The skill MUST call `check_checklist_sync.py` at end-of-session and again at "ship it" time. Failure blocks deploy.

**Escape valve scope:** `escape_reason` on a CH-*.yaml is allowed only for edits that cannot change agent behavior — typos in comments, whitespace, file header reformatting, dead-code removal, docstring polish. Any change to wording the LLM reads, gate logic, tool routing, conditional branches, or step ordering is behavior-affecting and MUST be paired with a checklist diff. The reason string is captured in the CH entry; abuse is visible by grepping `escape_reason:` across `changes/`.

```python
# scripts/check_checklist_sync.py (sketch)
# Inputs: staged diff
# Returns: 0 if prompt+checklist coupled or escape token present, 1 otherwise
# Exit message names the specific prompt file changed and which checklist items
# are likely-relevant (category match), so the user can quickly decide whether
# to add/update/skip.
```

## Data shapes (the structured artifacts)

### Checklist item — `checklist/CL-NNN.yaml`

```yaml
schema_version: 1
id: CL-007
category: discovery
severity: high           # critical | high | medium | low — weighted for call score
status: confirmed        # probationary (first N exercises) | confirmed | retired
created: 2026-05-22
created_from:
  session: 2026-05-22T14-30-abc
  driving_calls: [019e462b-...]
applies_to:
  stages: [stage2]                  # which stage prompt(s) this checks
  biz_type_scope: all               # all | [concrete, cleaning, hvac, ...]
question: |
  Did Jon recap the prospect's pain back to them in their own words
  before pitching the demo?
why: |
  Pain Cycle step 6 (recap). Skipping it kills close-rate — prospect doesn't
  feel heard.
how_to_spot_a_fail: |
  Transcript shows Jon transitioning to booking ask within 1-2 turns of last
  discovery question, no "so it sounds like..." line.
auto_agent_rubric: |
  Judge-LLM prompt: "In the call between the discovery questions ending
  and the booking ask, did Jon (the agent) summarize the prospect's stated
  problem back to them using phrases like 'so it sounds like', 'let me
  make sure I got that', 'to recap'? Return pass / fail / not_applicable
  with one-sentence evidence."
outcome_correlation:                # populated by later analytics
  joined_to_ghl: false
  notes: ""
retired_reason: ""                   # if status=retired
retired_at: ""
```

### Observation — `observations/OBS-NNN.yaml`

```yaml
schema_version: 1
id: OBS-2026-05-22-001
created: 2026-05-22T14:33
session: 2026-05-22T14-30-abc
checklist_items: [CL-007]            # which checklist items relate
call_id: 019e462b-...                # may be null for cross_call / direct_edit
stage: stage2
biz_type: cleaning
pattern_type: single_call            # single_call | cross_call | direct_edit
verdict: deferred                    # edit | deferred | noted | wontfix | win
fault_attribution: ai                # ai | prospect | infra | list_quality | mixed
salvageable: true                    # was the call still recoverable from this point?
confidence: high                     # high | medium | low
quote: |
  Jon: "Got it, so do you wanna do Tuesday at 3pm or Wednesday at 11am?"
  Prospect: "Wait but you didn't really listen to what I just told you about
  the seasonality issue."
notes: |
  Jon jumped straight from question 4 to booking ask. No recap.
related_changes: []                  # links to CH-* entries if this triggered one
```

### Change — `changes/CH-NNN.yaml`

```yaml
schema_version: 1
id: CH-2026-05-22-001
created: 2026-05-22T14:38
session: 2026-05-22T14-30-abc
driven_by:
  observations: [OBS-2026-05-22-001]
  checklist_items: [CL-007]
prompt_file: src/agent/prompts/stage2_qualify.py
stage: stage2
section: "discovery — between question 6 and booking handoff"
prompt_file_sha256_before: "ab12..."  # sha256 of prompt file before this edit
prompt_file_sha256_after: "cd34..."   # sha256 of prompt file after this edit
escape_reason: ""                     # non-empty ONLY if no checklist diff (see atomic rule)
hypothesis: |
  Adding an explicit recap step before the booking handoff will lift Stage 3
  entry-rate (currently dropping ~20% of prospects between Stage 2 last-question
  and Stage 3 first-utterance).
expected_outcome:
  metric: stage3_entry_rate
  direction: up
  magnitude: "small to moderate"
  verify_after_n_calls: 10
before: |
  <exact lines from the prompt before edit>
after: |
  <exact lines after edit>
word_changes_attempted: false         # if true, must have explicit user approval
                                       # per prompt-update-discipline rules
deployed: true
deployed_at: 2026-05-22T14:42
reference_point: docs/reference-points/2026-05-22-recap-fix/
verify_by: |
  Pull next 10 calls that reach Stage 2 question-6, check transcript for
  "so it sounds like" / "let me make sure" between last discovery turn and
  Stage 3 handoff.
verified:                             # populated later
  ran: false
  pass_count: 0
  fail_count: 0
  conclusion: ""
```

### Session log — `sessions/<ts>-<id>.md`

```markdown
---
schema_version: 1
session_id: 2026-05-22T14-30-abc
mode: single_call                     # single_call | batch | direct_edit | mixed
duration_min: 45
calls_reviewed: [019e462b-...]
findings: [OBS-2026-05-22-001]
changes: [CH-2026-05-22-001]
deferred: []
follow_ups:
  - "Verify recap edit lands on next 10 stage-2 calls"
---

# Session notes (free-form)

Quick narrative of what happened in the session, the conversation flow,
any context that doesn't fit in the structured YAMLs.
```

## Filesystem layout — final

```
voicsito/call-review/
├── checklist/CL-001.yaml ... CL-NNN.yaml
├── checklist/INDEX.md                  (generated by render.py — never hand-edit)
├── sessions/<ts>-<id>.md ... 
├── sessions/INDEX.md                   (generated)
├── changes/CH-*.yaml ...
├── changes/INDEX.md                    (generated)
├── observations/OBS-*.yaml ...
├── observations/INDEX.md               (generated)
└── render.py                           (re-renders all INDEX.md files; runs on demand
                                          and as a git post-commit hook)
```

Recording handling: **not cached locally**. The `recordingUrl` from Vapi gets noted in the session log if relevant, with a one-line reason ("listened to recording to verify pacing"). Recordings contain PII; gitignored if ever cached temporarily.

Quote handling: when a transcript moment matters (a hallucination, a great line, a fumble), the **specific quote gets pasted into the relevant YAML** (`observations.quote` or `changes.before/after`). The lesson is durable; the source transcript can rot.

## Composition with existing skills

```
User says "review call ..."
       │
       ▼
   call-review SKILL  ◀── orchestrator
       │
       ├── invokes scripts/fetch_call.py             (Vapi pull)
       │
       ├── invokes scripts/check_checklist_sync.py   (atomic-rule enforcement)
       │
       ├── invokes prompt-update-discipline SKILL    (mandatory before any edit;
       │                                              word-preservation gate)
       │
       └── invokes prompt-review SKILL               (OPTIONAL — only when
                                                       user explicitly enters
                                                       sales-thinking mode,
                                                       e.g. "attack the reveal")
```

**Key composition rule:** `call-review` does NOT auto-invoke `prompt-review` for every edit. `prompt-update-discipline` is mandatory; `prompt-review` is opt-in for Hormozi mode-shift when we're attacking a specific framework area. This avoids double-loading context on structural edits.

## Vapi pull mechanics

`scripts/fetch_call.py`:

```python
# Inputs:
#   --id <vapi-call-id>          (single)
#   --last <N>                    (recent N calls)
#   --since <iso-datetime>        (window)
#   --filter biz_type=<x>         (optional)
#   --filter ended_reason=<x>     (optional)
#   --filter stage_reached=<x>    (optional)
#
# Filtering scope: only calls handled by Voicsito stage assistants
#   (46e47e95-674a-4624-9029-69bb39c4daf0,
#    7b2666e0-154d-4c85-82f0-07b4c51ad264,
#    7600c2b6-d669-4f2e-8387-e371b0c193d4)
#   or the monolithic rollback target (261bf1a0-3527-4fcb-956c-b6e227d0bae1).
#
# Returns: list of normalized dicts:
#   {
#     "call_id", "started_at", "duration_s", "ended_reason",
#     "assistant_name", "stage_reached",
#     "customer_number", "customer_first_name", "biz_type",
#     "summary", "transcript",
#     "recording_url",
#   }
#
# Reads VAPI_API_KEY from config/.env.
```

Stage assistant IDs are hardcoded in the script; update via push_squad.py output if they ever change (will not under current re-PATCH model, but defensive).

## Deploy gating

When user says "ship it":
1. `scripts/check_checklist_sync.py` must pass (atomic rule).
2. **Reference-point snapshot is created** at `docs/reference-points/<YYYY-MM-DD>-<short-reason>/` using the existing pattern (assistant.json + tools.json + prompt files + RESTORE.md).
3. `scripts/push_squad.py` runs.
4. *(Opt-in)* `--with-sim` flag runs `sim-call.py` against the canonical persona set first; deploy blocks on sim failure.
5. Verify: deploy step completion is recorded on the relevant `changes/CH-*.yaml` entry (deployed_at, reference_point path).

Sim opt-in initially. Strongly nudge to enable for any change marked `expected_outcome.direction != unchanged`. Make mandatory after first regression.

## Read-only safety during review

Reviewing real prospect calls means I have access to real prospect data via Vapi/GHL APIs. Hard prohibition in SKILL.md: **during review sessions, the assistant must not POST/PUT/DELETE to GHL, Twilio, calendar, or any third-party API**. Pure read. The only writes allowed are local files (YAMLs, prompt files, reference-point snapshots) and Vapi assistant PATCH on explicit "ship it" — and that PATCH only touches assistant configs, not prospect records.

## Bridge to automation

The artifacts produced during the manual phase ARE the auto-agent's training input. Specifically:

| Artifact | Auto-agent uses it as |
|---|---|
| `checklist/CL-*.yaml` | The rubric (the `auto_agent_rubric` field is the judge-LLM prompt for each item) |
| `observations/OBS-*.yaml` | Calibration data — examples of pass/fail with human verdicts |
| `changes/CH-*.yaml` | The "what we tried and what happened" corpus |
| `sessions/*.md` | Narrative context for edge cases |
| Per-item `outcome_correlation` (joined to GHL later) | Validation signal — which checklist items actually correlate with bookings |

When we promote to automated review:
1. Pull last 24h of new calls via `fetch_call.py`
2. For each call, score against every `confirmed` checklist item using its `auto_agent_rubric`
3. Aggregate findings; surface patterns
4. Draft proposed prompt diffs (using `prompt-update-discipline` rules)
5. Run `sim-call.py` against personas to test diff doesn't regress other items
6. Surface to human for approval

**Gates before promoting to auto-review (not calendar — signal):**
- Inter-rater agreement >80% if Jon and Waseem both score (blind double-scoring on 5+ calls)
- Checklist churn rate <2 items/week (rubric stable)
- ≥3 items per category (rubric covers the surface area)
- At least one `verified: true` change cycle completed end-to-end manually

## Reviewer disagreement

When Jon and Waseem score the same call differently in their respective Google Doc notes:

- **Default tie-breaker:** Waseem's read, since he drives the editing sessions.
- **When it matters more:** if it's an item whose `auto_agent_rubric` will eventually train the bot, the disagreement itself is signal — the rubric is ambiguous. Capture both reads in the `observations/OBS-*.yaml` `notes` field with reviewer names, mark `confidence: low`, and rewrite the checklist item's `how_to_spot_a_fail` to remove the ambiguity. Once both reviewers would now score the call the same way, the item is calibrated.

This becomes the inter-rater calibration loop the future auto-agent inherits.

## Open questions / things to revisit

1. **GHL outcome join** — the `outcome_correlation` field is reserved on every checklist item but the join script doesn't exist yet. Build when first useful (probably week 2).
2. **Wins logging shape** — `observations.verdict: win` works for now but if wins outnumber edits, may want a dedicated `wins/` directory.
3. **Highlight-reel extraction** — `verdict: win` observations are the precursor. Whether to auto-clip recording snippets to disk is deferred.
4. **Reviewer fatigue tracking** — `sessions.duration_min` field is captured; explicit fatigue logic (e.g., cap reviews/day) lives in SKILL.md as guidance, not enforcement. Revisit if quality drops.
5. **Sim in deploy path** — opt-in initially. Make mandatory after first regression that sim would have caught.
6. **Schema migrations** — every YAML carries `schema_version: 1`. Migration scripts live in `voicsito/call-review/migrations/` when needed (none today).

## Build order

1. **Directory scaffolding** — create `voicsito/call-review/{checklist,sessions,changes,observations}/`. Empty `INDEX.md` placeholders.
2. **`scripts/fetch_call.py`** — minimum viable: takes `--id`, returns normalized dict. Add `--last`, `--filter` later.
3. **`scripts/check_checklist_sync.py`** — runs sha256 sync check across prompt files vs the most recent `changes/CH-*.yaml` per file. Fails when drift detected and no `escape_reason` covers it.
4. **`voicsito/call-review/render.py`** — generates the four `INDEX.md` views from YAML.
5. **Seed `checklist/CL-*.yaml`** — port the implicit items from `prompt-review/SKILL.md` (Hormozi frameworks: opener, reveal, discovery/Pain Cycle, objections, looping, offer, drop-price-and-STFU) plus the technical categories (voice/TTS, tool hygiene, pacing, hang-up, stage handoffs, voicemail, hallucination/truth-promises). Aim for ~25 confirmed items at start; everything else `probationary` until exercised.
6. **`.claude/skills/call-review/SKILL.md`** — write the skill itself with triggers, the freshness contract, the atomic rule, the composition rules, read-only safety bit.
7. **First test session** — review one real call end-to-end. Validate the flow. Fix friction.
8. **Reference-point integration** — wire "ship it" to snapshot creation. Test rollback works.
9. **Install the sync-check gate** — `check_checklist_sync.py` runnable on demand + auto-invoked by SKILL.md at end-of-session and at "ship it".
10. **Run manual phase for ~2 weeks** — every session populates the artifacts. After week 2, evaluate the gates for promoting to auto-review.

---

## Reviewed against the audit roundtable (2026-05-20)

This spec incorporates:
- **Sales perspective**: outcome_correlation field, fault_attribution + salvageable, biz_type_scope, hallucination as Critical tier, batch-review mode, wins capture (via observations.verdict=win), reviewer fatigue tracking, probationary checklist item status, weighted severity for call score.
- **Ops perspective**: enforced atomic rule via content-hash sync-check (no git available), one-file-per-event for race safety, sha256-before/after linkage on every change entry, reference-point snapshot on every deploy, recent-changes banner instead of full-log reads, monthly rotation via INDEX.md generation pattern.
- **Engineering perspective**: YAML-per-item structured storage, `scripts/fetch_call.py` helper instead of inline curl, observations directory for negative findings, stage attribution field, schema_version on every YAML, flattened skill composition (prompt-update-discipline mandatory, prompt-review opt-in).

Explicitly rejected from audits:
- Wholesale transcript caching to local disk — solves a problem we don't have at this iteration cadence; quotes-in-YAML is sufficient.
- Forcing call-anchored evidence for every edit — contradicts user's stated direct-edit workflow.
- Auto-generating CHANGE_LOG from git history — moot since project is not a git repo; hand-maintained YAML with sha256-before/after linkage is the equivalent drift detector.
- Reconciler instead of atomic rule — atomic rule with sync-check script is tighter feedback.
