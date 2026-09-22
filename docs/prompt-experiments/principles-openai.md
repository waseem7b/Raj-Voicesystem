# OpenAI-Native Prompting Principles for GPT-4.1

Transferable principles, mined directly from OpenAI's official guides (GPT-4.1 Prompting Guide, Realtime Prompting Guide, Prompt Migration Guide, Prompt Guidance) and supplemented with community/empirical findings. Every principle is cited.

## The framing principle (read first)

GPT-4.1 follows instructions **more literally and more closely** than 4o. This is the single biggest behavior change and the lens for every other principle here:

> "GPT-4.1 is trained to follow instructions more closely and more literally than its predecessors, which tended to more liberally infer intent from user and system prompts."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "existing prompts optimized for other models may not immediately work with this model, because existing instructions are followed more closely and implicit rules are no longer being as strongly inferred."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "Models such as GPT-4.1 excel at closely following instructions, but this precision means it can interpret unclear or poorly phrased instructions literally."
> — OpenAI Prompt Migration Guide (https://developers.openai.com/cookbook/examples/prompt_migration_guide)

Implication: every ambiguity, every internal contradiction, every "obvious" rule you used to leave implicit is now a bug surface. Vague directives produce literal but wrong behavior. The fix is more explicit specification, not more text.

---

## 1. Positive vs negative instructions

**OpenAI's position:** Both are needed. GPT-4.1's literal interpretation means *you must explicitly specify what to do AND what not to do* — the model will not infer prohibitions from positive instructions, and vice versa.

> "since the model follows instructions more literally, developers may need to include explicit specification around what to do or not to do"
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

OpenAI's classic Prompt Engineering guidance ("instead of just saying what not to do, say what to do instead") still applies as a *default*, but for GPT-4.1 the new emphasis is that *implicit* prohibitions are no longer reliably inferred. So if a behavior matters, name it explicitly in both directions where ambiguous.

> "a single sentence firmly and unequivocally clarifying your desired behavior is almost always sufficient to steer the model."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

**Principle:** Write the positive instruction. Then for each common failure mode, add an explicit negative one. Don't rely on the positive instruction to imply the negative.

---

## 2. Format: Markdown, XML, or JSON?

**OpenAI's position (GPT-4.1 specifically):** Markdown is the recommended starting point. XML also works well, especially for long context. JSON is discouraged for document-heavy prompts.

> "We recommend starting here, and using markdown titles for major sections and subsections."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "XML performed well in our long context testing."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "JSON is particularly poor in this setting" (referring to document-heavy long-context prompts)
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

**Empirical note:** A 2024 study found Markdown 81.2% vs JSON 73.9% on reasoning tasks for GPT-4. Community consensus is that format matters less than internal consistency. ("It turns out that it doesn't make much of a difference." — OpenAI dev forum, https://community.openai.com/t/xml-vs-markdown-for-high-performance-tasks/1260014)

**Principle:** Use Markdown headings for top-level sections. Use XML tags only when you need delimiters that won't collide with the content (e.g. wrapping retrieved documents). Don't mix formats arbitrarily — pick one structural backbone.

---

## 3. Where critical instructions go (long context)

**OpenAI's position:** Sandwich. Top AND bottom. If forced to pick one, top wins.

> "If you have long context in your prompt, ideally place your instructions at both the beginning and end of the provided context, as we found this to perform better than only above or below."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "If you'd prefer to only have your instructions once, then above the provided context works better than below."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

Note: this contradicts Anthropic's documented preference (long documents at top, query at end). It is a GPT-4.1-specific finding. Needle-in-haystack: GPT-4.1 retrieves at 100% accuracy at any position up to 1M tokens — the placement rule is about *instruction salience*, not retrieval (https://openai.com/index/gpt-4-1/).

**Principle:** Critical behavioral rules go at the top of the system prompt AND repeated at the bottom. Don't bury them in the middle.

---

## 4. Verbosity

**OpenAI's position:** Specific and clear beats brief. But empty/vague text is worse than no text. Length itself is not the enemy — ambiguity is.

> "making instructions as specific and clear as possible"
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

The Realtime guide explicitly warns:

> "Ambiguity or conflicting instructions = degraded performance"
> — OpenAI Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide)

And on format:

> "Prefer bullets over paragraphs: Clear, short bullets outperform long paragraphs"
> — OpenAI Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide)

**Principle:** Cut paragraphs into bullets. Replace adjectives with concrete criteria. The question to ask each line: "Would two readers interpret this identically?" If no, rewrite or delete.

---

## 5. Exceptions to rules

**OpenAI's position:** Rules without escape hatches fail in edge cases. Acknowledge exceptions explicitly inside the rule rather than letting the model infer them.

> "However, if you don't have enough information to properly call the tool, ask the user for the information you need."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "All criteria should be considered equally unless the user's instructions indicate otherwise"
> — OpenAI Prompt Migration Guide (https://developers.openai.com/cookbook/examples/prompt_migration_guide)

**Principle:** When you write "always do X", immediately add "unless Y, in which case Z." Don't let the model invent the unless clause.

---

## 6. Reasoning / explanation in the prompt ("why")

**OpenAI's position:** Don't lecture the model about why a rule exists. *Do* induce step-by-step reasoning for the task itself.

> "a developer can induce the model to produce an explicit, step-by-step plan by using any variant of the Planning prompt component."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

This induced planning increased SWE-bench Verified performance by 4%. But this is about *task* reasoning ("think step by step about the user's request"), not justifying rules ("the reason this rule exists is...").

**Principle:** Tell the model what to do. Don't tell it why. Reserve "thinking" prompts for the task's reasoning steps, not for explaining your design.

---

## 7. References to outside architecture / system

**OpenAI's position:** Reference the broader system only to bound the model's expectations (what's available, what's not). Don't describe architecture for its own sake.

> "You already have everything you need to solve this problem in the /testbed folder, even without internet connection."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

This pattern works because it prevents the model from hallucinating about resources it doesn't have. Architecture mentions that don't change behavior add tokens without benefit.

**Principle:** Mention outside systems only when bounding capabilities or preventing hallucination ("you cannot access the internet", "the user's data is in X"). Skip the rest.

---

## 8. Tool descriptions: tool field vs system prompt

**OpenAI's position:** Strong, clear preference for the API tools field. Don't manually inject tool schemas into the system prompt.

> "We encourage developers to exclusively use the tools field to pass tools, rather than manually injecting tool descriptions into your prompt."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

> "we saw a 2% increase in SWE-bench Verified pass rate when using API-parsed tool descriptions versus manually injecting the schemas"
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

The Realtime guide adds: per-tool descriptions should specify "Use when" and "Do NOT use when" inline (https://cookbook.openai.com/examples/realtime_prompting_guide).

**Principle:** Tool-specific "when to fire" logic lives in the tool description (inside the schema). The system prompt handles cross-tool orchestration, persistence, and preambles only.

---

## 9. Few-shot examples

**OpenAI's position:** Useful, but examples must agree with rules. Contradiction kills performance.

> "Add examples that demonstrate desired behavior; ensure that any important behavior demonstrated in your examples are also cited in your rules."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

For voice specifically, OpenAI warns that sample phrases get repeated verbatim:

> "When provided sample phrases, models can use those quotes verbatim and start to sound repetitive to users."
> — OpenAI Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide)

Their fix:

> "DO NOT ALWAYS USE THESE EXAMPLES, VARY YOUR RESPONSES."
> — OpenAI Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide)

**Principle:** Examples help when they demonstrate a behavior that's also stated as a rule. If you want variation, explicitly tell the model the examples are for inspiration, not verbatim use.

---

## 10. Length / instruction-following degradation

**OpenAI's position:** GPT-4.1 maintains instruction following up to 1M tokens for *retrieval*. For complex reasoning over the whole context, performance can drop.

> "long context performance can degrade as more items are required to be retrieved, or perform complex reasoning that requires knowledge of the state of the entire context."
> — OpenAI GPT-4.1 announcement (https://openai.com/index/gpt-4-1/)

Documented edge case:

> "we have observed the model being resistant to producing very long, repetitive outputs, for example, analyzing hundreds of items one by one."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

OpenAI does NOT publish a system-prompt-length threshold beyond which instruction-following breaks. The honest answer: there isn't a hard cliff — there's a slow degradation as ambiguity, contradiction, and reasoning load increase.

**Principle:** Length isn't the failure mode. Conflict and ambiguity are. A 4k-token prompt with one contradiction will outperform a 2k-token prompt with three contradictions.

---

## 11. Repetition / reinforcement (top + bottom)

**OpenAI's position:** Explicitly recommended for long prompts and agentic workflows.

> "If the prompt is for long context analysis or long tool use, repeat key Agentic Reminders, Important Reminders and Output Format points at the end."
> — OpenAI Prompt Migration Guide (https://developers.openai.com/cookbook/examples/prompt_migration_guide)

> "For very long prompts, ensure key instructions are duplicated or strategically placed so they remain visible and effective throughout the entire context."
> — OpenAI Prompt Migration Guide (https://developers.openai.com/cookbook/examples/prompt_migration_guide)

For agentic workflows the GPT-4.1 guide names three reminders to repeat: persistence, tool-calling, and planning (https://cookbook.openai.com/examples/gpt4-1_prompting_guide).

**Principle:** The 3-5 rules that matter most go at the top of the prompt AND restated near the bottom. Don't repeat everything — repeat what would cost you most if violated.

---

## 12. Anchor / verbatim phrases ("say this exactly")

**OpenAI's position:** Use sparingly. Models will parrot verbatim phrases robotically.

> "When provided sample phrases, models can use those quotes verbatim and start to sound repetitive to users."
> — OpenAI Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide)

> "It's generally not necessary to use all-caps or other incentives like bribes or tips. We recommend starting without these."
> — OpenAI GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide)

The Realtime guide does endorse capitalization for emphasis on critical rules ("Use capitalized text for emphasis: Capitalizing key rules makes them stand out") — but that's about flagging a rule to the model, not making the model output capitalized text to the user.

**Principle:** Reserve verbatim/anchor phrasing for moments where exact wording is genuinely required (legal disclaimers, brand-mandated greetings, safety scripts). For everything else, give intent + tone + examples-marked-as-inspiration.

---

## OpenAI's recommended prompt skeleton (GPT-4.1)

From the GPT-4.1 Prompting Guide (https://cookbook.openai.com/examples/gpt4-1_prompting_guide):

1. **Role and Objective**
2. **Instructions** (with sub-categories for nuance)
3. **Reasoning Steps**
4. **Output Format**
5. **Examples** (with explicit "vary, don't copy" guidance)
6. **Context** (for long-context tasks; place between top and bottom instruction copies)
7. **Final instructions and prompt to think step by step**

From the Realtime Prompting Guide (https://cookbook.openai.com/examples/realtime_prompting_guide), for voice add:

- **Personality & Tone** (including length per turn and language pinning)
- **Reference Pronunciations**
- **Unclear Audio Handling**
- **Conversation Flow** (state machine)
- **Safety & Escalation**

---

## Where OpenAI and community disagree

- **Format choice:** OpenAI prefers Markdown for GPT-4.1; community research ("The Delimiter Hypothesis") argues format barely matters. Both can be true: format matters less than internal consistency.
- **Negative instructions:** Classic OpenAI guidance favors "say what to do." GPT-4.1-specific guidance says explicit "what not to do" is now necessary because implicit prohibitions aren't inferred. Both apply — say what to do first, then list explicit prohibitions for known failure modes.
- **Instruction placement:** OpenAI: top + bottom (or top if forced to choose). Anthropic: docs at top, query at end. Different model families, different rules.

---

## Sources

- OpenAI GPT-4.1 Prompting Guide: https://cookbook.openai.com/examples/gpt4-1_prompting_guide
- OpenAI Realtime Prompting Guide: https://cookbook.openai.com/examples/realtime_prompting_guide
- OpenAI Prompt Migration Guide: https://developers.openai.com/cookbook/examples/prompt_migration_guide
- OpenAI Prompt Guidance: https://developers.openai.com/api/docs/guides/prompt-guidance
- Introducing GPT-4.1 in the API: https://openai.com/index/gpt-4-1/
- OpenAI dev forum (XML vs Markdown): https://community.openai.com/t/xml-vs-markdown-for-high-performance-tasks/1260014
- Helicone GPT-4.1 dev guide: https://www.helicone.ai/blog/gpt-4.1-full-developer-guide
