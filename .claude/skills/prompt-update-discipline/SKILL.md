---
name: prompt-update-discipline
description: Invoke BEFORE editing any string whose content reaches an LLM — system message, assistant message, tool description, voicemail message, end-call message, idle message, first-utterance text, sub-agent prompt, sim/judge prompt — regardless of which file it lives in. Loads principles-based prompt-engineering discipline (citations to OpenAI, Anthropic, and production voice-agent platforms) and runs the pre-edit checklist.
---

# prompt-update-discipline

Invoke this skill before editing any **model-facing text** — any string whose content reaches an LLM at runtime. The file path and variable name don't matter; what reaches the model matters.

What follows is principles with citations. Apply them to whatever prompt you're touching.

---

## Core principles (cite the source if you ever defend a choice)

### 1. Ambiguity is the failure mode. Not length.

OpenAI's GPT-4.1 guide: *"ambiguity or conflicting instructions = degraded performance."* A 4K-token prompt with one internal contradiction performs worse than a 2K-token prompt with zero. The ConInstruct study (Liu et al., 2025) found that modern instruction-tuned LLMs DETECT conflicts in ~87-91% of cases but flag them to the user almost never — the model silently picks a winner via opaque heuristics, so contradictions surface as erratic behavior rather than errors.

**Operational rule:** before adding a new instruction, search the prompt for anything it could contradict. If you find a conflict, resolve at write time. Prefer the "exception-baked-into-rule" pattern (`"X only when Y"`) over the "rule + override" pattern (`"Always X. Exception: ..."`) — the former eliminates the contradiction; the latter creates one.

Source: [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide), [ConInstruct (Liu et al. 2025)](https://arxiv.org/abs/2511.14342)

### 2. Modern instruction-following models read literally.

OpenAI on GPT-4.1: *"existing prompts optimized for other models may not immediately work with this model, because existing instructions are followed more closely and implicit rules are no longer being as strongly inferred."*

**Operational rule:** every implicit rule needs to become explicit. If a behavior matters, name it. Don't leave it to the model's "judgment."

Source: [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

### 3. Positive framing is the default; surgical negatives are allowed.

Industry consensus: negative instructions are generally weaker than positive ones (the "pink elephant" effect). Re-frame "don't X" as "do Y" whenever a positive structure exists.

But GPT-4.1's literal interpretation breaks the pure-positive rule: OpenAI explicitly says *"developers may need to include explicit specification around what to do or not to do"* because implicit prohibitions are no longer inferred. Closing a known failure mode with an explicit "do not X" is allowed when there's no clean positive restructure.

**Operational rule:** default positive. Use negatives only for specific named failure modes that positive structure can't address. Never use negatives as the primary mode of instruction.

Source: [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide), [Pink-Elephant analysis](https://eval.16x.engineer/blog/the-pink-elephant-negative-instructions-llms-effectiveness-analysis)

### 4. Critical rules go at top AND bottom. Middle gets forgotten.

The "Lost in the Middle" effect (Liu et al., 2023, replicated cross-model) shows that information at the start or end of a long prompt is recalled reliably; the middle is dead. OpenAI's GPT-4.1 guide: *"if you have long context in your prompt, ideally place your instructions at both the beginning and end."* Google Research (Dec 2025) measured up to 76% accuracy lift from top+bottom repetition on non-reasoning models.

**Operational rule:** the 3-5 rules that would cost most if violated belong at top AND restated at bottom. Don't restate everything — only what's load-bearing.

Source: [Lost in the Middle](https://arxiv.org/abs/2307.03172), [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide), [Google Research on repetition](https://arxiv.org/abs/2512.14982)

### 5. Tool descriptions live in the tool field, not the system prompt.

OpenAI: *"exclusively use the tools field to pass tools, rather than manually injecting tool descriptions into your prompt."* Per-tool "when to fire" guidance belongs in each tool's `description`. Cross-tool orchestration ("never fire X before Y") can live in the system prompt because no single tool description owns it.

**Operational rule:** if you're adding "when to call this tool" language to the system prompt, stop and edit the tool's description instead. The model uses tool descriptions as authoritative.

Source: [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide), [Anthropic tool use docs](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview)

### 6. Strip developer scaffolding from prompt bodies.

The model has no context for your architecture, your call graph, your project history, your debugging journey, or other agents in your system. Every meta-reference is a token the model has to weigh while figuring out what to say.

Anthropic explicitly warns against *"hardcoding complex, brittle logic"* and *"stuffing the system prompt."* Voice-agent platforms (Retell, Vapi, Pipecat) converge on: model sees the **current phase + goal + exit criterion**, never "node 3 of 7" or "stage 1 of 3."

**Operational rule:** write from the model's POV. The model needs: who it is, what its current job is, the flow it runs, the tools available, the output format. It does NOT need: stage names, references to other agents, references to your codebase, explanations of why a rule exists.

Source: [Anthropic — effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [OpenAI Realtime Prompting Guide](https://cookbook.openai.com/examples/realtime_prompting_guide)

### 7. Bullets and structure beat paragraphs.

OpenAI: *"prefer bullets over paragraphs: clear, short bullets outperform long paragraphs."* Paragraphs hide contradictions and bury rules. Markdown headers + bullet lists make instructions scannable AND make conflicts visible at write time.

Format hierarchy for GPT-4.1: Markdown is the default. XML for wrapping immutable content blobs. JSON is poor for instruction context.

**Operational rule:** if a rule is currently in a paragraph, move it to a bullet. If a section is in JSON, convert to Markdown. XML is fine for wrapping a script line or a context blob you want preserved intact.

Source: [OpenAI Realtime Prompting Guide](https://cookbook.openai.com/examples/realtime_prompting_guide), [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

### 8. State the rule. Don't justify it.

OpenAI: *"a single sentence firmly and unequivocally clarifying your desired behavior is almost always sufficient to steer the model. It's generally not necessary to use all-caps or other incentives like bribes or tips."*

Justifications ("because the user's last call sounded robotic", "OpenAI's guide says...") consume tokens, dilute the rule, and don't improve compliance. Caps lock and `## CRITICAL` headers are mostly placebo — the structural placement is what works.

**Operational rule:** write the rule in one declarative sentence. Delete every "because", every shouting label, every justification. If the rule isn't clear enough at one sentence, the rule itself is wrong — rewrite the rule, don't add an explanation.

Source: [OpenAI GPT-4.1 Prompting Guide](https://cookbook.openai.com/examples/gpt4-1_prompting_guide), [Control Illusion paper](https://arxiv.org/html/2502.15851v1)

### 9. Sample phrases get parroted. Mark them as inspiration; whitelist the verbatim ones.

OpenAI Realtime: *"when provided sample phrases, models can use those quotes verbatim and start to sound repetitive to users."* Fix: split phrases into two categories. Inspiration phrases get an explicit "vary these — do not repeat verbatim." Required-verbatim phrases (legal disclosures, brand copy, sales anchors the operator chose) get the opposite: "say this exactly."

**Operational rule:** every quoted phrase in your prompt is on one of two lists. Mixed treatment in a single block is the bug — name the list each phrase belongs to.

Source: [OpenAI Realtime Prompting Guide](https://cookbook.openai.com/examples/realtime_prompting_guide)

### 10. (Voice only) Output goes through TTS — write for the ear.

Unanimous across Vapi, OpenAI Realtime, LiveKit, ElevenLabs, Retell: no Markdown in the model's output, no bullets, no JSON, no code blocks. Numbers, money, times, dates, phone numbers, emails, and most acronyms get spelled out. Punctuation controls prosody — `...` = short beat, `—` = longer pause, period = full stop.

**Operational rule:** the rules above (bullets, Markdown) apply to the SYSTEM PROMPT structure. The MODEL'S OUTPUT must be plain spoken English. Make this distinction explicit in the prompt.

Source: [Vapi prompting guide](https://docs.vapi.ai/prompting-guide), [LiveKit prompting](https://docs.livekit.io/agents/start/prompting/), [OpenAI Realtime](https://cookbook.openai.com/examples/realtime_prompting_guide)

### 11. (Voice only) Fragmentation is the default failure mode.

LLMs are post-trained on clean written prose → they default to sentences with terminal periods → TTS reads each period as a hard stop → output sounds choppy and robotic. Voice-agent platforms explicitly fight this with sample utterances showing chained delivery, conversational connectors ("so", "yeah so", "anyway"), and mid-utterance hesitations.

**Operational rule:** voice prompts need example utterances demonstrating natural connection between clauses. Never just say "speak naturally" — show what natural sounds like in 2-3 example phrases.

Source: [LiveKit on voice realism](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic)

### 12. (Voice only) Encode phase as goal + exit criterion, not architecture position.

Retell, Vapi, Pipecat, OpenAI Realtime all converge: the model in any given turn sees ONE phase, described by its goal and what triggers leaving it. Never "stage 1 of 3", never "node 3 of 7", never "you just came from the previous stage." The conversation history IS the state.

**Operational rule:** if a prompt references its architectural position, delete the reference. Replace with: "Your job right now is X. You move on when Y happens."

Source: [Retell pathway design](https://www.retellai.com/blog/prompt-based-vs-conversational-pathways-choosing-the-right-approach), [OpenAI Realtime](https://cookbook.openai.com/examples/realtime_prompting_guide)

---

## The pre-edit checklist (run before saving any prompt change)

For each line you're adding, changing, or considering keeping, ask:

0. **Is the prompt currently in a known-good state?** If yes, before making destructive changes, snapshot it to `docs/reference-points/<date>-<reason>/` so rollback is one-line. Skip this step if you're already iterating against a recent snapshot.
1. **Is this line ambiguous?** Does it conflict with any other line in the prompt? If yes — resolve at write time. Prefer "X only when Y" (exception baked into the rule) over "Always X. Exception: ..." (two rules creating silent conflict).
2. **Does this line state an explicit rule or behavior?** Or does it rely on implicit inference? If implicit — make it explicit. Modern instruction-tuned LLMs no longer infer implicit rules.
3. **Is this a "do not X" with no positive equivalent?** Try a positive restructure first. If a clean positive structure exists, use it. If a known failure mode persists despite positive structure, an explicit "do not X" is allowed — but never as the primary mode of instruction.
4. **Is this rule load-bearing AND not already restated at the bottom?** The cap is 3-5 top+bottom-repeated rules total. Repeat only what would cost most if violated. Adding a sixth repetition costs reliability — pick the one to drop.
5. **Is this about a single tool's behavior** ("when to fire this tool", "what to pass")? If yes — move it to the tool's `description` field, not the system prompt. Cross-tool orchestration ("never fire X before Y") stays in the system prompt.
6. **Does this line reference architecture, code paths, project history, or other agents the model can't see?** If yes — delete it. If the model needs a capability boundary (e.g. "you cannot access the internet"), keep one bounding sentence and drop the rest.
7. **Is this a paragraph that could be bullets?** If yes — convert. Paragraphs hide contradictions; bullets surface them at write time.
8. **Does this line justify a rule** (with "because", "OpenAI says", "the user complained...")? If yes — delete the justification, keep the rule.
9. **Is this a quoted sample phrase?** If yes — is it marked "inspiration only / vary your responses" OR "say exactly as written"? It must be one or the other, never ambiguous. Mixed treatment in a single block is the bug.
10. **Is this a few-shot example?** If yes — is the BEST example placed last? Recency bias in instruction-tuned models means the last example is weighed most heavily.
11. **Does the prompt handle empty / missing template variables?** Every `{{var}}` that can be empty needs an explicit instruction for the empty case. Empty fallback shouldn't invent data.
12. **Does the prompt tell the model what to do on no-input / silence / unclear audio?** Default behaviors here are usually wrong (re-asking, filling silence, hallucinating). Spell out the desired behavior.
13. **Does the prompt name any tool by its function/SDK name** in spoken instructions? If yes — the model will leak it ("calling get_available_slots now"). Wrap in "silently" or "without naming the function."
14. **(Voice only) Will the model's output flow through TTS?** If yes — does the prompt instruct numbers/money/times as words and no Markdown in the output?
15. **(Voice only) Will the model produce multi-sentence turns?** If yes — does the prompt show example chained utterances, not just abstract "speak naturally"?
16. **(Voice only) Does this line reveal the model's architectural position** (stage number, node ID, agent count, handoff target name)? If yes — delete and replace with "your job right now is X."

A line that fails any check gets rewritten or deleted before saving. No exceptions.

---

## The end-of-edit gate

Before declaring the prompt update complete:

1. Re-run the pre-edit checklist on every line you touched.
2. Verify the file still parses and any composed prompts (shared + stage-specific) load to non-empty strings.
3. If the change touches a Vapi-managed field (tool `description`, `firstMessage`, `voicemailMessage`, etc.), query context7 for the current Vapi field spec (max length, formatting constraints) before syncing — this is the project rule from `MEMORY.md`: "ALWAYS query context7 before making API/SDK changes."
4. Sync via the appropriate deploy script (`scripts/sync-vapi-prompt.py` for the monolithic assistant, `scripts/push_squad.py` for staged Squad).
5. Verify the API response confirms the char-count delta matches what you expected.
6. Either:
   - Include in your response an in-turn tool-call result demonstrating the new behavior on a real test call (for voice changes), OR
   - Tag the response with `NOT YET VERIFIED` AND list the specific tests the operator needs to run.

A "parses successfully" or "sync succeeded" tool result does NOT satisfy the claim-words gate for voice-agent prompt changes. Per CLAUDE.md RULE #1: UI/visual changes require a real browser test; the voice-agent equivalent is a real call (simulator or live). Saying "done" / "live" / "fixed" without a real call result needs the explicit `NOT YET VERIFIED` tag.

---

## When this skill applies

Invoke before editing any string whose content reaches an LLM at runtime, including:

- Any system message or assistant message string (commonly under `src/agent/prompts/`, but the location does not matter — the destination does)
- A Vapi tool's `description` field (the model treats it as authoritative for "when to fire this tool")
- An assistant's `firstMessage`, `voicemailMessage`, `endCallMessage`, or any item in `messagePlan` (`idleMessages`, etc.)
- Sub-agent prompts dispatched from any script (e.g. simulator personas in `scripts/sim-call.py`, judge prompts in `scripts/judge-call.py`)
- Markdown / docs example prompts that get copy-pasted into production

The skill applies even when the edit happens inside a deploy script (e.g. `scripts/setup-vapi.py`) — what matters is whether the string content reaches a model.

Do NOT invoke this skill for:

- Non-content edits to deploy scripts (argparse flags, retry logic, HTTP error handling — anything not touching a model-facing string)
- Python module docstrings that are clearly engineer-facing (file header docstrings above the prompt assignment, with the prompt assigned to a separate named variable below)
- Webhook routing / business logic that doesn't shape model-facing text
- Reading prompts (this skill only applies to writes)

If unsure whether an edit is model-facing: invoke the skill. False positives cost ~30 seconds; false negatives cost a botched deploy.
