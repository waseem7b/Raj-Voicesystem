# Cross-Model Prompt Engineering Principles (Industry Survey)

Survey of broader prompt-engineering and instruction-following literature. Goal: principles that hold across vendors (OpenAI, Anthropic, Google) and survive model changes. Confidence labels:

- **HIGH** — 3+ independent sources converge
- **MEDIUM** — 2 independent sources
- **LOW** — 1 source

---

## 1. Position matters: primacy and recency dominate, the middle is dead [HIGH]

The "Lost in the Middle" phenomenon (Liu et al., Stanford, TACL 2024) is the most-cited finding in long-prompt research. Performance is highest when relevant information is at the **beginning or end** of the context, and degrades sharply in the middle. The effect grows worse as context grows longer, and it shows up even in models with formally large context windows.

- Liu et al. paper: https://arxiv.org/abs/2307.03172
- Serial-position effects replicated across GPT-3.5/4, GPT-J, Claude-instant: https://arxiv.org/abs/2406.15981
- Practitioner write-up on positional dynamics: https://atlassc.net/2026/03/30/the-architecture-of-prompt-sequencing
- Promptlayer on why long system prompts hurt: https://medium.com/data-science-collective/why-long-system-prompts-hurt-context-windows-and-how-to-fix-it-7a3696e1cdf9

**Implication:** put the most important rules at the **top OR bottom** of the prompt, not in the middle. Several sources report primacy slightly dominates recency in GPT-family models, but both beat the middle by a wide margin.

---

## 2. Prompt length degrades reliability before it hits the context limit [HIGH]

Multiple sources report instruction-following accuracy drops well before the technical context window is exhausted. Promptlayer reports reasoning degrades around ~3,000 tokens. MLOps Community calls this "prompt bloat" and reports a 16K-token RAG prompt outperformed a 128K-token prompt. Anthropic frames it as "context rot" — recall accuracy decreases as token count grows.

- Promptlayer "Disadvantage of Long Prompt for LLM": https://blog.promptlayer.com/disadvantage-of-long-prompt-for-llm/
- MLOps Community "Impact of Prompt Bloat": https://mlops.community/blog/the-impact-of-prompt-bloat-on-llm-output-quality
- Anthropic on context rot: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Grit Daily summary: https://gritdaily.com/impact-prompt-length-llm-performance/

**Implication:** every token must earn its place. Cut, don't add. Anthropic: *"strive for the minimal set of information that fully outlines your expected behavior."*

---

## 3. Negative instructions ("do not X") are weaker than positive ones [HIGH]

Three independent sources cite the "pink elephant"/ironic-process problem: telling an LLM not to do something can make it more likely to surface that thing. Recommended fix: reframe as a positive instruction stating what TO do.

- 16x Engineer "The Pink Elephant Problem": https://eval.16x.engineer/blog/the-pink-elephant-negative-instructions-llms-effectiveness-analysis
- Hacker News thread with practitioner consensus: https://news.ycombinator.com/item?id=44495665
- Prompting Guide tips: https://www.promptingguide.ai/introduction/tips

**Implication:** "Use real data" beats "Do not use mock data." Reserve negative instructions for hard boundaries (safety, refusal), and always pair them with the affirmative version.

---

## 4. Repeating critical instructions helps, especially at the end [HIGH]

Google Research (Leviathan, Dec 2025) showed simple prompt duplication improves non-reasoning model accuracy by up to 76%. Practitioners independently report the "say it twice" trick — once at the top, once at the bottom — works because of causal attention: every token gets a second pass over the rules.

- Leviathan paper, "Prompt Repetition Improves Non-Reasoning LLMs": https://arxiv.org/abs/2512.14982
- VentureBeat coverage: https://venturebeat.com/orchestration/this-new-dead-simple-prompt-technique-boosts-accuracy-on-llms-by-up-to-76-on
- UX-Republic on the repetition trick: https://www.ux-republic.com/en/prompting-tip-repetition-instruction-llm/
- Promptlayer: https://blog.promptlayer.com/prompt-repetition-improves-llm-accuracy/

**Caveat:** the benefit shrinks substantially for reasoning models (o-series, Claude with extended thinking) because they internally rephrase the prompt during reasoning.

---

## 5. Instruction hierarchy is real but unreliable as a gate [HIGH]

OpenAI formally trained GPT-4 with an instruction hierarchy (system > user > tool output). But independent academic work shows the hierarchy is leaky — models exhibit strong biases toward certain constraint types regardless of priority labels, and prompt-engineered "PRIORITIES" or "CRITICAL" blocks only modestly help.

- OpenAI "The Instruction Hierarchy": https://openai.com/index/the-instruction-hierarchy/
- arXiv paper: https://arxiv.org/abs/2404.13208
- "Control Illusion: The Failure of Instruction Hierarchies": https://arxiv.org/html/2502.15851v1
- ConInstruct (conflict detection): https://arxiv.org/abs/2511.14342

**Implication:** writing `## CRITICAL` or `[PRIORITY 0]` helps a little, but won't save you from a poorly structured prompt. Don't rely on emphasis labels — fix the structural conflict.

---

## 6. Conflicting instructions get resolved silently, in ways you can't predict [HIGH]

ConInstruct (2025) found that while top models like Claude-4.5-Sonnet and DeepSeek-R1 can DETECT instruction conflicts at 87-91% F1, **they almost never flag them to the user** — they pick a winner silently using whatever heuristic their training provides.

- ConInstruct: https://arxiv.org/abs/2511.14342
- Arbiter cross-vendor analysis: https://arxiv.org/html/2603.08993v1
- Practitioner write-up: https://www.singlegrain.com/content-marketing-strategy-2/how-llms-handle-conflicting-information-across-multiple-pages/

**Implication:** every "exception to the rule" is a roll of the dice. The fix is structural: state the FINAL rule as a single positive instruction with the exception baked in, not "Rule A. Exception: A1."

---

## 7. Personas help for style/tone, hurt or do nothing for factual tasks [HIGH]

"When 'A Helpful Assistant' Is Not Really Helpful" (Zheng et al.) tested 4 LLM families against 2,410 factual questions and found personas in system prompts did not improve performance, and sometimes hurt it. Other work confirms: personas help alignment-dependent tasks (writing, roleplay, safety) and hurt accuracy-based tasks (math, factual recall).

- "When 'A Helpful Assistant'...": https://arxiv.org/html/2311.10054v3
- PHAnToM (ToM reasoning + personas): https://arxiv.org/html/2403.02246v3
- PromptHub roundup: https://www.prompthub.us/blog/role-prompting-does-adding-personas-to-your-prompts-really-make-a-difference
- Search Engine Journal: https://www.searchenginejournal.com/research-you-are-an-expert-prompts-can-damage-factual-accuracy/570397/

**Implication:** for a voice agent, persona is genuinely useful (it's a tone/style task). For tool decisions or factual lookups, persona is dead weight or worse.

---

## 8. Chain-of-thought in the system prompt is task-dependent and can backfire [HIGH]

Wei et al.'s original CoT paper helps reasoning tasks. But Wharton's "Decreasing Value of Chain of Thought" report and Shaikh et al. (Stanford HCI) show CoT can: (a) increase harmful output in sensitive domains, (b) reduce performance on tasks where overthinking hurts (perception, intuition), and (c) provide diminishing returns on modern reasoning-trained models that already think internally.

- Wei et al., original CoT paper: https://arxiv.org/pdf/2201.11903
- Shaikh et al. (Stanford), "On Second Thought": https://hci.stanford.edu/publications/2023/shaikh-stepbystep.pdf
- Wharton "Decreasing Value of CoT": https://gail.wharton.upenn.edu/research-and-insights/tech-report-chain-of-thought/
- "Mind Your Step": https://openreview.net/forum?id=rpbzBXdo4x

**Implication:** for an instruction-following task (follow this script, gate that tool), explicit "think step by step" in the system prompt rarely helps and may add latency. For reasoning models, it's redundant.

---

## 9. Few-shot examples beat verbose rules for format/tone; few-shot can hurt reasoning [HIGH]

Consensus across PromptHub, Prompting Guide, Anthropic, OpenAI Cookbook: 2-5 high-quality, diverse, format-consistent examples teach format and tone better than long prose instructions. But on reasoning-heavy tasks, few-shot can lock the model into surface patterns — zero-shot CoT often wins.

- Prompting Guide on few-shot: https://www.promptingguide.ai/techniques/fewshot
- PromptHub guide: https://www.prompthub.us/blog/the-few-shot-prompting-guide
- Anthropic prompting best practices: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- DataCamp: https://www.datacamp.com/tutorial/few-shot-prompting

**Implication:** for a voice agent, examples of how the AI should say a line are gold — and Anthropic specifically says **put your best example LAST** (recency).

---

## 10. Gate tools at the tool description, not just the system prompt [MEDIUM]

OpenAI's GPT-4.1 prompting guide and Anthropic's tool-use docs both say the same thing: tools should carry their own when-to-use / when-not-to-use guidance in their description. Global "use tools wisely" rules in the system prompt underperform tool-local rules. GPT-4.1 specifically says **use the `tools` field, don't manually inject tool descriptions into the prompt**.

- OpenAI GPT-4.1 prompting guide: https://cookbook.openai.com/examples/gpt4-1_prompting_guide
- Anthropic tool use docs: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- OpenAI community thread on tool prompting: https://community.openai.com/t/prompting-best-practices-for-tool-use-function-calling/1123036

**Implication:** for each tool, include input shape, when-to-call, when-NOT-to-call, and failure-recovery hints in the tool's description, not in a global rules block.

---

## 11. Specific > general — but maximizing specificity overshoots [MEDIUM]

Research on "specificity bands" shows a sweet spot — overly specific verbs in reasoning prompts cause accuracy drops, while overly general prompts produce drift. The practical heuristic: be specific about format, behavior, and constraints; stay general about the model's reasoning approach.

- Analytics Vidhya 26 principles: https://www.analyticsvidhya.com/blog/2024/01/prompting-principles-to-improve-llm-performance/
- Towards Data Science on prompt design: https://towardsdatascience.com/boost-your-llm-outputdesign-smarter-prompts-real-tricks-from-an-ai-engineers-toolbox/
- Anthropic on "the right altitude": https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

---

## 12. Strip developer scaffolding; the LLM doesn't need your meta-commentary [MEDIUM]

Anthropic explicitly warns against engineers "hardcoding complex, brittle logic in their prompts to elicit exact agentic behavior." The "stop stuffing your system prompt" pattern from LangGraph practitioners says the same: split work into role-bound prompts that each do one job, instead of one mega-prompt that tries to plan, execute, and synthesize.

- Anthropic: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- LangGraph practitioner write-up: https://pessini.medium.com/stop-stuffing-your-system-prompt-build-scalable-agent-skills-in-langgraph-a9856378e8f6
- "Stop Prompting, Start Scaffolding": https://dev.to/aibuildersdigest/why-you-should-stop-prompting-and-start-scaffoldingengineeringarchitecture-2jn4

**Implication:** explanations like "we use this rule because in the past X happened" are pure dead weight. The model doesn't need the why. State the rule, move on.

---

## 13. XML/Markdown structural delimiters help organize, especially for Claude [MEDIUM]

Anthropic explicitly fine-tuned Claude to attend to XML tags. OpenAI models are looser but still benefit from structural markers (`###`, Markdown headers). Multiple sources agree: clearly separating `<instructions>`, `<examples>`, `<context>`, and `<output_format>` reduces instruction bleed between sections.

- Anthropic XML guide: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags
- AWS Bedrock prompt-engineering blog: https://aws.amazon.com/blogs/machine-learning/prompt-engineering-techniques-and-best-practices-learn-by-doing-with-anthropics-claude-3-on-amazon-bedrock/
- Vellum: https://www.vellum.ai/blog/prompt-engineering-tips-for-claude

**Caveat (LOW):** GPT-4.1 follows instructions "more literally" than earlier models per OpenAI's own guide, so XML structure can occasionally cause the model to over-fixate. Markdown headers tend to be a safer cross-vendor default.

---

## 14. Voice-agent-specific: brevity is doubly load-bearing [HIGH]

Across voice-agent prompting guides (Vapi, ElevenLabs, OpenAI Realtime, Hume EVI, Layercode): voice prompts should produce responses 60-70% shorter than text equivalents. Human attention for spoken output drops after 8-10 seconds. Latency targets are 200-800ms voice-to-voice — every extra token in the system prompt adds to first-token latency on every turn.

- OpenAI Realtime Prompting Guide: https://developers.openai.com/cookbook/examples/realtime_prompting_guide
- Vapi prompting guide: https://docs.vapi.ai/prompting-guide
- ElevenLabs prompting guide: https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide
- VoiceInfra: https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide
- Cresta on real-time latency: https://cresta.com/blog/engineering-for-real-time-voice-agent-latency

**Voice-specific structural rules cited by multiple guides:**
- Bullets > paragraphs (OpenAI Realtime)
- Pin output language explicitly to prevent drift (OpenAI Realtime)
- Add a "variety rule" to fight robotic repetition across turns (OpenAI Realtime)
- Define organized conversation phases with exit criteria (OpenAI Realtime, Vapi)

---

# The 3-4 Most Important Principles for Voice-Agent System Prompts

Opinionated take, based on what's load-bearing for low-latency conversational agents specifically:

### 1. RUTHLESS BREVITY (combining #2, #12, #14)
A voice prompt's first job is to be short. Every token costs latency on every turn, AND every token dilutes the rules you actually care about (context rot). Cut all developer scaffolding, all rationale, all "we found that..." commentary. State rules, don't justify them. If you can say it in 5 words instead of 25, do.

### 2. POSITION CRITICAL RULES AT THE EDGES, REPEAT IF NECESSARY (combining #1, #4)
Lost-in-the-middle is real. The first ~500 tokens and last ~500 tokens get the most attention. Put hard rules (safety, never-do, must-do) at the top. Put the action-oriented "what to do RIGHT NOW" at the bottom. If a rule is truly critical, write it in both places — Google's research confirms 76% accuracy lift from duplication for non-reasoning models.

### 3. POSITIVE FRAMING + STRUCTURAL FIXES, NOT NEGATIVE RULES (combining #3, #6)
"Don't transfer to a human" works worse than "Resolve every call yourself." When you have a rule with an exception ("never X, except when Y"), rewrite as a single positive: "X only when Y." The model resolves conflicts silently — don't give it conflicts to resolve. This is the single biggest win for a voice agent that needs deterministic behavior.

### 4. GATE TOOLS AT THE TOOL, NOT IN A "RULES" BLOCK (#10)
For a voice agent making tool calls (booking, lookup, transfer), the when/when-not-to-use rules belong in each tool's description, not in a global `## TOOL RULES` section in the system prompt. Both OpenAI and Anthropic converge on this. Tool-local gating works; global tool gating is leaky.

---

# Confidence summary

| # | Principle | Confidence |
|---|---|---|
| 1 | Lost-in-the-middle / position matters | HIGH |
| 2 | Length degrades reliability before context limit | HIGH |
| 3 | Positive framing beats negative | HIGH |
| 4 | Repetition (top+bottom) helps non-reasoning models | HIGH |
| 5 | Instruction hierarchy real but leaky | HIGH |
| 6 | Conflicts resolved silently and unreliably | HIGH |
| 7 | Personas help style, hurt accuracy | HIGH |
| 8 | CoT in system prompt is task-dependent | HIGH |
| 9 | Few-shot beats verbose rules for format/tone | HIGH |
| 10 | Tool gating belongs at the tool | MEDIUM |
| 11 | Specificity has a sweet spot | MEDIUM |
| 12 | Strip developer scaffolding | MEDIUM |
| 13 | XML/Markdown structure helps Claude especially | MEDIUM |
| 14 | Voice agents: brevity is doubly load-bearing | HIGH |
