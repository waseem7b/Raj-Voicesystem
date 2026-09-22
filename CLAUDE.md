# Voicsito — Claude Code Configuration

> **Working on Ashley (the outbound confirmation/reminder calls)? Start with
> `docs/ASHLEY-HANDOFF.md`.** It carries the current state, the open pause
> problem, what is already fixed, and how to read a call log properly.
>
> **Before changing anything about Ashley's voice, pauses or TTS settings, read
> `docs/ASHLEY-VOICE-TESTING.md` first.** It records what has already been ruled
> out with evidence — break tags, punctuation beats, text normalization,
> streaming latency — so the same dead ends are not retried.

## CRITICAL RULES

1. **USE SUPERPOWERS SKILLS. STOP. CHECK. INVOKE. THEN ACT.**
   Every prompt — before any other tool call, before any clarifying question, before writing a single line of text — ask: which superpowers skill fits this? If one fits, invoke it via the `Skill` tool FIRST and follow its content. Skills exist because they encode workflows that work. Skipping them is how I ship broken features and the user has to catch it.

   **Mandatory triggers — these phrasings (and their paraphrases) MUST invoke a skill:**
   - "let's build", "add", "design", "create", "what should we", "how should we", "could we", "thinking about", "ideas for" → `superpowers:brainstorming`
   - "implement", "write", "code up", "do the implementation" → `superpowers:test-driven-development`
   - "bug", "broken", "doesn't work", "fix", "isn't working", "weird", "off", "wrong", "error", "failing", "why is it", "stopped working" → `superpowers:systematic-debugging`
   - "double check", "verify", "make sure", "are you sure", "test it", "is it live", "is it deployed", "does it work", "did you check", "look at it again" → `superpowers:verification-before-completion`
   - "plan", "spec", "break this down", "step by step", "what's the approach" → `superpowers:writing-plans`
   - "execute the plan", "run the spec", "do the plan" → `superpowers:executing-plans`
   - 2+ independent things in one prompt → `superpowers:dispatching-parallel-agents`
   - "review", "code review", "look over my work" → `superpowers:requesting-code-review`
   - "isolate", "separate branch", "don't touch main" → `superpowers:using-git-worktrees`
   - "merge", "ship it", "wrap this up", "finish branch" → `superpowers:finishing-a-development-branch`
   - editing any string whose content reaches an LLM as system message, assistant message, tool description, voicemail message, end-call message, idle message, or first-utterance text — regardless of which file it lives in (prompts under `src/agent/prompts/`, deploy scripts like `scripts/setup-vapi.py`, simulator personas/judges in `scripts/sim-call.py` and `scripts/judge-call.py`, anywhere) → `prompt-update-discipline`
   - "test the booking", "test the webhook live", "real test", "deep test", "integration test", "e2e test", "smoke test", "run it for real", "hit the live X", "verify the booking landed", "cleanup", "clean up the test", "delete the test artifacts", "sync to GHL/Cal/Twilio", or any test that will POST/PUT/DELETE against a third-party API used by production → `live-system-testing` (this skill encodes the 2026-05-11 Jared/Hector incident — DO NOT run booking tests, cleanups, or write-mutating integration tests without it)
   - any non-2xx from a third-party API, a 2xx write whose effect never shows up at the real sink, or me being about to say a capability is unsupported/broken/a vendor bug → `api-capability-probing` (rule 4; encodes the 2026-08-05 Blooio session)

   **Lookup carve-out (narrow):** pure single-file read OR single read-only command that returns the answer with no opinion formed and no proposed change. Examples: "when did X happen", "what's the path to Y", "is Z installed", "what time is it". The moment I form an opinion, propose a change, or chain a second tool call, lookup is over — a skill applies, re-evaluate before the next action.

   **Claim words are gated.** I cannot write "done", "fixed", "works", "verified", "deployed", "live", "passing", or "shipped" in a response unless that same response contains either (a) a tool-call result in this turn demonstrating it, OR (b) the explicit tag `NOT YET VERIFIED`. UI/visual changes specifically require a real browser test (load page, exercise behavior, assert state) — `tsc --noEmit` is not enough.

   **Negative claim words are gated the same way** — see rule 4. "not supported", "impossible", "can't", "vendor bug", "platform limitation" require an evidence table, not a hedge.

   **Skip-and-confess gate:** if I'm about to skip a skill that any reasonable reading of this rule says applies, the response must open with `SKIPPING <skill> because <one-sentence reason>`. No silent skips. If I cannot write that sentence honestly, invoke the skill instead.

2. **NEVER MODIFY PROMPT WORDING. ONLY STRUCTURE.**
   When fixing a prompt bug (skipping, premature tool calls, gate failures, etc.), I may modify: `<gate>` blocks, `<tool_rules>`, step ordering, XML/Markdown scaffolding, conditional logic, enforcement rules. I MAY NOT modify: the words inside `<verbatim>` blocks, the phrases Jon says, objection language, FAQ answers, dialogue style, or any other content the user wrote.

   **What counts as wording (FORBIDDEN to edit):**
   - Text inside `<verbatim>` blocks
   - Example dialogue, sample phrases, Jon's voice/style
   - Specific words the agent is meant to say to the prospect
   - Objection handler responses, FAQ answers
   - Any prose the user spent time crafting

   **What counts as structure (allowed to edit when requested):**
   - `<gate>` rules and conditions
   - `<tool_rules>` and tool preconditions
   - Step ordering, sub-step breakdown
   - Format scaffolding (XML tags, Markdown headers, indentation)
   - `<if cond="...">` branching logic (the conditions, not the body content)
   - Enforcement language ("MUST", "NEVER", priority statements)

   **Specific forbidden moves:**
   - Wrapping a loose phrase (e.g. `Ask: "How can I help you?"`) in `<verbatim>` to force exact wording — that turns guidance into a robotic mandate.
   - Inventing new phrases the agent should say.
   - Rewriting verbatim blocks for "clarity" or "consistency".
   - Adding example wording to a rule that didn't have any.

   **If a prompt bug seems to require a wording change to fix:** STOP. Flag it to the user. Quote the exact line and explain why structure alone can't address it. Wait for explicit approval before touching words.

   The user has spent weeks crafting the prompt's voice. One wrong wording change ruins the call vibe even if every gate fires perfectly. Structure is mine to fix; words are not.

3. **DEFAULT TO POSITIVE INSTRUCTION; USE NEGATIVES SURGICALLY.**
   When writing or editing voice-agent / LLM prompts, instruct the model on what to do, not what to avoid. Re-frame "don't X" as "do Y" whenever a positive structure exists. Structure steps so the desired behavior is the natural path — never add a "do not do X" rule on top of a flawed structure; rewrite the structure.

   GPT-4.1 specifically requires explicit prohibitions for known failure modes it would otherwise commit (it no longer infers implicit rules per OpenAI's official guide). Surgical negatives are allowed for those named failure modes only — never as the primary mode of instruction.

   The `prompt-update-discipline` skill carries the operational checklist, the citations, and the principle list. Invoke it for any edit that reaches an LLM.

4. **A FAILED CALL IS EVIDENCE ABOUT MY REQUEST, NEVER ABOUT THE PLATFORM.**
   A 4xx/5xx, or a 2xx write whose effect never appears at the real sink, tells me my request was wrong. It says nothing about whether the capability exists. "X works" is proven by one success; "X is impossible" is proven only by exhausting the surface. Using a one-trial threshold for both produced three false "limitations" in the 2026-08-05 Blooio session — group titles, the card avatar, and cards in group chats — every one of which worked once the API was called correctly.

   **Trigger — these observable events open an investigation, and it stays open until the bar below is met:**
   - Any non-2xx from a third-party API (Blooio, Vapi, GHL, Cal.com, Twilio, anything)
   - A 2xx write whose effect is not visible at the real sink (device, dashboard, CRM record, live call)
   - A feature behaving differently than its docs describe
   - Me being about to type any word from the forbidden list

   **Forbidden while an investigation is open:** "not supported", "impossible", "doesn't support", "no API call fixes it", "platform/protocol limitation", "vendor bug", "their defect", "we'll have to work around it", "we should stop using this", "we'd need to redesign around it". Also forbidden as artifacts: drafting a support ticket, proposing a product-flow redesign justified by the limitation, recommending abandonment. Recommendation cost scales WITH evidence, never against it.

   **Minimum bar before any of those words or artifacts — all five, shown as a table in the same response:**
   1. **Enumerate the endpoint surface.** Scan the whole doc set for the resource noun and its synonyms; list every endpoint touching it — creation, sub-resource (`/{id}/icon`), field-on-parent. I cannot claim a limitation of an object class while an endpoint that creates or mutates that class is still uncalled.
   2. **Vary request shape on named axes, one at a time:** method (PUT/PATCH/POST); path variants; content-type (json / multipart / form-urlencoded); **payload completeness — full object vs the one field I care about**; value encoding (URL vs base64 vs upload); identifier form. "I retried" never means re-sending the same call.
   3. **Read every error body verbatim before interpreting it.** Bodies are the schema oracle — paste them, don't summarize. Status classes are routing instructions, not verdicts: 404 = wrong path, keep enumerating; 405 = wrong verb; 415 = wrong content-type; 400/422 = right endpoint, wrong shape, the message usually names the fix; 401/403 = token scope, not feature absence; **5xx = I reached the feature and something downstream choked on THIS object — evidence FOR the feature existing.**
   4. **Build a golden control.** Create a fresh resource via the most explicit documented create endpoint with every optional field supplied, then retry against it. If it works there, the capability exists and my object was the wrong kind — fix provenance, not product.
   5. **Audit provenance and cross-check a known-good client.** How was the failing resource created — documented endpoint, shortcut, or UI? Dump its fields for type tells (service markers, GUID prefixes). Then check whether the vendor's own dashboard/SDK does the thing; if it does, "the API can't" is already false.

   **Replacement behavior while the bar is unmet — the only permitted output:** `I haven't found a way to do this yet.` plus (a) the table of what I tried — method, path, content-type, payload, status, response body verbatim — and (b) the top 3 untried hypotheses. An escalation, never a verdict. **The evidence table is exempt from the 1-4 sentence brevity cap**; brevity never justifies a premature conclusion.

   **The user's field report reopens everything.** If the user says "I've seen it work" or otherwise contradicts my claim, the question changes from *whether* to *how*, immediately. Reset to step 1 and ask what conditions produced their working case. Never argue from memory against a live observation.

   **Self-check:** before typing any forbidden word, name the layer I actually observed. If the causal subject of my explanation ("Apple", "the device", "the carrier", "their DB") appears in zero tool outputs this session, I invented it — delete the sentence and run the next variant.

   Probing that only reads, or that writes to my own throwaway resources, runs freely. Probing that mutates artifacts a real prospect or teammate can see still goes through `live-system-testing` first — the two rules compose, they do not override each other. Operational checklist: the `api-capability-probing` skill.

## Behavioral Rules (Always Enforced)

- Do what has been asked; nothing more, nothing less
- NEVER create files unless they're absolutely necessary for achieving your goal
- ALWAYS prefer editing an existing file to creating a new one
- NEVER proactively create documentation files (*.md) or README files unless explicitly requested
- NEVER save working files, text/mds, or tests to the root folder
- ALWAYS read a file before editing it
- NEVER commit secrets, credentials, or .env files

## File Organization

- NEVER save to root folder — use the directories below
- Use `/src` for source code files
- Use `/tests` for test files
- Use `/docs` for documentation and markdown files
- Use `/config` for configuration files
- Use `/scripts` for utility scripts
- Use `/examples` for example code

## Project Architecture

- Follow Domain-Driven Design with bounded contexts
- Keep files under 500 lines
- Use typed interfaces for all public APIs
- Prefer TDD London School (mock-first) for new code
- Use event sourcing for state changes
- Ensure input validation at system boundaries

## Build & Test

This is a Python project (no Node toolchain).

```bash
# Install deps (webhook server — Vapi)
pip install -r src/webhooks/requirements.txt

# Run webhook server locally
uvicorn src.webhooks.server:app --reload --port 8081

# Lint / format
ruff check src/
ruff format src/

# Tests (pytest)
pytest tests/
```

- ALWAYS run tests after making code changes
- ALWAYS verify imports + lint clean before committing

## Security Rules

- NEVER hardcode API keys, secrets, or credentials in source files
- NEVER commit .env files or any file containing secrets
- Always validate user input at system boundaries
- Always sanitize file paths to prevent directory traversal

## Project Documentation

- See `/docs/OPEN-ISSUES.md` for deferred known defects. A SessionStart hook
  (`.claude/hooks/session-open-issues.py`) reads it into context every session so
  they get raised even when nobody asks. **Delete an entry when it is fixed.**
- See `/docs/checkpoints.md` for the latest authoritative state (rolling log of "checkpoint" snapshots — newest entry is the truth)
- See `/docs/system-summary.md` for the shareable technical summary
- See `/docs/architecture.md` for the full system design
- See `/docs/vapi-migration.md` for the LiveKit -> Vapi cutover playbook (completed 2026-05-05)
- See `/docs/local-development.md` for the daily edit/deploy/test workflow
- See `/docs/webhook-infrastructure.md` for Voicsito's slot on the shared `webhooks.c7a.io` host. The infra itself lives in its own repo: `github.com/waseem7b/webhooks-infra` (clone at `~/CLAUDSITO/webhooks-infra`, deployed read-only to `/opt/webhooks-infra/` on the VPS; the `webhooks-infra` user-level skill routes to it)
- See `/docs/call-types.md` for the 6 call type specifications
- See `/docs/base-script-original.md` for the original human-run sales script
- See `/docs/voice-cloning.md` for the Jon voice-cloning pipeline (platform-agnostic)
- See `/docs/reference-points/2026-05-10-claude-xml-prompt/` for the Claude-native XML version of `inbound_texts.py` — restore this if we ever switch the LLM back to Anthropic Claude (XML control flow works on Claude, doesn't on GPT-4.1)
- See `/docs/reference-points/2026-05-11-pre-stage-split/` for the last monolithic single-assistant snapshot (full Vapi config + prompt + voice settings + tool list + RESTORE.md procedure) — rollback target if the 3-stage Squad split fails
- Credentials live in `/config/.env` (gitignored)

## Skills

- Saying "checkpoint" triggers the project-scoped `checkpoint` skill (see `.claude/skills/checkpoint/SKILL.md`) — pulls live Vapi config, updates `docs/system-summary.md` to current truth, appends a timestamped entry to `docs/checkpoints.md`.
