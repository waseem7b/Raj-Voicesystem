---
name: simulation-test
description: Run a Voicsito simulator test from a natural-language description. Trigger when the user says "simulation test - <description>" or "simulation test" with a follow-up. Parse what they want, pick one of the 12 existing bank personas (never invent a new persona; only build a one-shot if the user explicitly asks), run scripts/sim-call.py + scripts/judge-call.py, and either report once or auto-iterate the local prompt until N consecutive passes. Never push to Vapi without explicit user go-ahead.
---

# simulation-test

## What you have

- `scripts/sim-call.py` — runs an LLM-vs-LLM call between Jon (using `src/agent/prompts/inbound_texts.py`) and a prospect persona.
- `scripts/judge-call.py` — grades a transcript against the persona's `expectations:` block.
- **The 12 bank personas** (`tests/personas/`) — always test with one of these. Never invent a new persona to fit a request; map to the closest of the 12 (build a one-shot ONLY if the user explicitly asks):
  - `curious_first_timer.yaml` — happy path, gives name + biz + reason on opening
  - `just_says_name.yaml` — only name + vague greeting (Step-3 gate)
  - `sends_to_email.yaml` — deflects to email
  - `silent_prospect.yaml` — one-word answers ("yeah.", "yep.")
  - `topic_changer.yaml` — derails to off-topic chitchat
  - `hostile_skeptic.yaml` — accuses scam/bot, hostile from turn 1
  - `hard_head.yaml` — third-gen plumber, argues every claim, tests agree-and-add looping
  - `yapper.yaml` — HVAC contractor, rapid-fire questions + tangents, tests staying on script
  - `evasive.yaml` — roofer, short non-committal answers ("I dunno man"), tests drilling-in
  - `burned_before.yaml` — landscaper screwed by past agencies, tests acknowledging trauma without trash-talking
  - `has_marketing_guy.yaml` — remodeler with existing freelancer Steve, tests displacing without bashing
  - `super_successful.yaml` — $7M commercial contractor, peer-energy, tests not groveling
- One-shots: `tests/personas/_oneshot/` — only when the user explicitly requests one
- Vapi assistant id (push target): `261bf1a0-3527-4fcb-956c-b6e227d0bae1`

## Steps

### 1. Parse

Pull from the user's description:
- **forbidden_behavior** — what Jon must NOT say/do
- **prospect_behavior** — how the prospect opens / responds
- **expected_jon_behavior** — what Jon SHOULD do
- **stage_focus** — `--stop-after-step N` if they only care about a stage (1=acknowledge, 3=get_reason, 4=services, 5=reveal, 6=discovery, 7=close, 8=book)
- **iteration_mode** — `True` if they said "iterate", "till consistent", "till he does it", "keep running"
- **iteration_runs** — default `3` consecutive passes; user override wins ("until 5 in a row")

If too vague to extract, ask ONE clarifying question and stop. Don't guess.

### 2. Persona

**Default: always pick ONE of the 12 bank personas** by prospect behavior. Do NOT invent a new persona, and do NOT auto-create a one-shot — map the request to the closest existing persona and run it as-is:
- "just says name", "no context", "vague greeting" → `just_says_name`
- "hostile", "scam", "robot", "accuses" → `hostile_skeptic`
- "send me email", "deflect to email" → `sends_to_email`
- "silent", "one word", "monosyllabic" → `silent_prospect`
- "off-topic", "derail", "personal questions" → `topic_changer`
- "happy path", "interested", "cooperative" → `curious_first_timer`
- "stubborn", "argues", "pushes back", "dug in", "old-school" → `hard_head`
- "yapper", "lots of questions", "talks a lot", "rapid-fire", "interrupts" → `yapper`
- "evasive", "guarded", "non-committal", "vague answers", "won't share info" → `evasive`
- "burned", "screwed before", "trauma", "skeptical of agencies", "lost money before" → `burned_before`
- "has a marketing guy", "already has someone", "existing freelancer", "loyal to current provider" → `has_marketing_guy`
- "successful", "big contractor", "established", "doesn't need leads", "peer-energy" → `super_successful`

Point `--persona tests/personas/<name>.yaml` at sim-call.py and judge-call.py directly. The judge grades against that persona's existing `expectations:` block. No file creation.

**If no bank persona fits cleanly**, pick the closest one and state which you chose and why — don't invent a persona to fit. If the user wants a behavior or expectation the bank doesn't cover, surface that and ask whether to add the check to the matching persona or build a one-shot. Don't decide for them.

**Build a one-shot ONLY when the user explicitly asks for one** (e.g., "make a one-shot", "spin up a custom persona", "test this exact opening line"). Then create `tests/personas/_oneshot/<base>-<slug>.yaml`: copy the closest bank persona, slug = 2-3 words from the request, append (don't replace) expectations. If there's no close base, build from scratch with `name`, `description`, `biz_type`, `business_name`, `prospect_first_name`, `opening`, `persona_prompt` (round-by-round behavior, ending with "Do not break character. Never say you are an AI."), `expectations` (only what the user asked for).

### 3. Pick a test folder (MANDATORY layout)

Every test run goes into ONE folder under `tests/transcripts/`. Never write transcripts anywhere else (not `logs/`, not the `tests/transcripts/` root, never the default `--out` path).

**Folder name format:** `NN-<slug>` (no `-test` suffix — the parent `tests/transcripts/` already says it).
- `NN` = zero-padded 2-digit sequence number, in order tests were run. Find the next number with `ls tests/transcripts/ | grep -E '^[0-9]{2}-' | sort | tail -1` — increment by 1. If output is `04-foo`, your new folder is `05-…`.
- `<slug>` = short kebab-case description. Examples: `01-stress-baseline`, `02-driving`, `03-howcanihelp`, `04-not-interested-marketing`, `05-email-deflect`.
- One test concept = one folder. Whether it's 1 run or 10 (variants, retries, iterations), they all live together as `run-1.md`, `run-2.md`, … inside that folder.
- Each `run-N.md` MUST be paired with `run-N.verdict.txt` from the judge.

```
tests/transcripts/NN-<slug>/
  run-1.md
  run-1.verdict.txt
  run-2.md
  run-2.verdict.txt
  …
```

If the slug already exists for a recent test and you're just adding more runs to it, increment run numbers from the highest existing `run-N` inside that folder. Don't overwrite history. If it's a NEW test concept, always create a NEW numbered folder — never reuse an old slug just because the topic is similar.

### 4. Run

```bash
NEXT=$(ls tests/transcripts/ 2>/dev/null | grep -E '^[0-9]{2}-' | sort | tail -1 | grep -oE '^[0-9]{2}' || echo "00")
NN=$(printf "%02d" $((10#${NEXT:-00} + 1)))
TEST=tests/transcripts/${NN}-<slug>
mkdir -p $TEST
.venv-tests/bin/python scripts/sim-call.py \
  --persona <persona-path> \
  --turns 25 --quiet \
  --out $TEST/run-N.md
```

`--out` is REQUIRED. Append `--stop-after-step N` if `stage_focus` is set.

### 5. Judge

```bash
.venv-tests/bin/python scripts/judge-call.py \
  --transcript $TEST/run-N.md \
  --persona <persona-path> \
  > $TEST/run-N.verdict.txt 2>&1
```

For multiple runs in parallel, background each `(sim … && judge …) &` block, then `wait`. Same shell, single Bash call.

### 6. Report (single-run mode)

Pass count, each FAILED expectation with the judge's reason, transcript path (`$TEST/run-N.md`). Ask: refine expectations, edit prompt, or push?

### 7. Iterate (only if iteration_mode)

Loop:
1. Run sim + judge.
2. **`iteration_runs` consecutive passes** → SUCCESS. Stop.
3. **A run fails** → propose ONE tight edit to `src/agent/prompts/inbound_texts.py`, apply via `Edit`, show the diff (1-3 lines + one-sentence rationale), re-run.
4. **Hard cap 8 iterations.** If still failing, surface the pattern and ask user.
5. **Score regresses** (fewer passes than last iteration) → STOP, surface, ask user. The last edit hurt.

For the consecutive-pass confirmation runs, parallel `Bash` is allowed (saves ~5 min per cycle) — watch OpenRouter rate limits.

### 7. Final

Report total iterations, final pass rate, all edits made (concise diff). The local prompt is staged but NOT pushed. Ask: "push to Vapi?"

If yes, PATCH `https://api.vapi.ai/assistant/<id>` with `urllib.request`, header `User-Agent: curl/8.0` (default urllib UA hits Cloudflare 403). See prior prompt-review sessions for the exact pattern.

## Hard rules

- **Never push to Vapi without explicit go-ahead.** Iterations are local file edits only.
- **One prompt edit per iteration.** You won't know which fix helped if you batch.
- **Show every edit, concisely.** 1-3 lines + one-sentence why.
- **Stop on score regression.** Don't dig deeper — the last edit hurt.
- **Only test what the user asked.** Don't auto-add unrelated expectations.
- **Each sim run ≈ $0.05–0.10.** Surface cost if a long loop is coming.
- **API code change → context7 first.**

## Examples

**"simulation test - jon must not apologize when prospect mentions the text"**
→ Use `curious_first_timer` (mentions text in opening) as-is. The "no apology" check isn't in its `expectations:`, so surface that and ask whether to add the check to the persona or build a one-shot. Don't auto-create a file. Run once after the user decides. Report.

**"simulation test - prospect says only their name no context, jon should ask how can i help, iterate till consistent"**
→ Use `just_says_name`. Verify expectations cover Step-3 gate. iteration_mode=True, iteration_runs=3. Loop.

**"simulation test - opening with hostile prospect"**
→ Use `hostile_skeptic` with `--stop-after-step 3`. Run once.

**"simulation test"**
→ Ask: "what should I test? (e.g., 'jon must not apologize when prospect mentions text', or 'opening with hostile prospect, iterate till consistent')"
