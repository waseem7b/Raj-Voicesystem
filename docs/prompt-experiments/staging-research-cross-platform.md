# Cross-Platform Research: How Voice Agents Stage Prompts

**Audience:** Voicsito architecture decision — sales agent on Vapi + GPT-4.1, ~8 stages.
**Question:** Survey staging patterns across the rest of the industry to pressure-test our approach.
**Date:** 2026-05-11

---

## TL;DR

The industry has converged on **one abstraction with three names**: a directed graph of **nodes/states**, where each node owns a focused prompt + tool set + transition rules, and a runtime layer outside the LLM tracks where you are. Bland calls them "Pathways," Retell calls them "Multi-Prompt / Conversation Flow," Pipecat calls them "Flows," ElevenLabs calls them "Workflows," LiveKit calls them "Agent handoffs," LangGraph calls them "state graphs." Same shape, different marketing.

The disagreement is *who decides transitions*: LLM-judged conditions (Bland default, Retell single-prompt), deterministic rules on extracted variables (Pipecat, LiveKit, LangGraph), or hybrids with explicit edge types (ElevenLabs has all three).

**~8 stages for a sales call is normal**, not excessive. Retell flips users from single→multi-prompt the moment a prompt exceeds 1000 words or 5 tools — by that bar, anything sales-shaped is multi-stage.

---

## 1. Bland AI Pathways

**Abstraction:** Visual node graph. Each node has: type (Default / Webhook / KB / Transfer / EndCall / Wait), prompt OR static text, exit condition, variable extraction config, optional "Global Node" flag.

**Transition trigger:** LLM-evaluated. Edges are "pathway labels" (natural-language conditions like "user gave a date AND time"). The agent stays on the current node until its condition is met, then picks the matching outbound edge.

**Shared context:** Variables extracted at any node become `{{var_name}}` references in downstream nodes. Built-ins like `{{lastUserMessage}}` and `{{prevNodePrompt}}` exist globally. Webhook responses flow into downstream variable scope.

**Killer feature:** **Global Nodes** — a single node wired (invisibly) to every other node. Lets the agent jump out of the current node anytime the prospect triggers a global intent (e.g., "transfer to human", "I want to buy now"). This is the right pattern for handling sales objections that can appear at any stage.

**Sources:**
- https://docs.bland.ai/tutorials/pathways
- https://university.bland.ai/modules/3/lesson-1
- https://university.bland.ai/modules/3/lesson-2
- https://www.51d.co/guide-implementing-bland-ai-conversational-pathways/

---

## 2. Retell AI — Multi-Prompt + Conversation Flow

Retell ships **three** tiers and tells you which to use:

| Tier | When to use |
|---|---|
| Single-prompt | < 1000 words, ≤ 5 tools |
| Multi-prompt | Tree of focused prompts, LLM-decided transitions |
| Conversation Flow | Full state machine, deterministic transitions, complex branching |

**Multi-prompt anatomy:** Each state = its own prompt (typically <500 tokens) + its own tool list. Transitions live inside the prompt as natural-language rules: *"if users say 'yes', transition to `confirm_appointment`. If no, call `end_call`."*

**Shared context:** Variables extracted in earlier nodes are pre-filled into later node prompts. Example from their docs: "If in Node1 the agent learns the customer's name and issue, Node2's prompt can include those as pre-filled variables."

**Conversation Flow:** Adds deterministic routing — "the AI isn't left to decide when to change topics; the designer defines it." This is their answer to LLMs jumping topics or repeating questions mid-call.

**Sources:**
- https://docs.retellai.com/build/single-multi-prompt/prompt-overview
- https://docs.retellai.com/build/single-multi-prompt/write-multi-prompt
- https://www.retellai.com/blog/unlocking-complex-interactions-with-retell-ais-conversation-flow
- https://www.retellai.com/blog/prompt-based-vs-conversational-pathways-choosing-the-right-approach

---

## 3. Pipecat Flows (Daily.co, open source)

**Abstraction:** Node graph managed by `FlowManager` outside the LLM context window. Each node has:
- **Role messages** (optional, set once — persona/tone)
- **Task messages** (required — the immediate objective)
- Tools available at this node
- Transition handler (Python code)

**Transition trigger:** Tool calls. When the LLM calls a tool inside a node, your transition handler runs, validates, mutates `flow_manager.state`, and returns the next node. This is the most explicit "code-controls-state" pattern of any platform.

**Shared context:** `flow_manager.state` — a persistent dict accessible across nodes. Plus configurable context strategies per-node: `APPEND` (default), `RESET`, or `RESET_WITH_SUMMARY`.

**Their thesis (load-bearing for our decision):** Even with huge context windows, monolithic prompts suffer "context rot" (instructions from earlier get ignored) and "loss of focus" (LLM exits workflow prematurely). Their fix: "providing access to everything is not the same as providing guidance." The structure layer lives **outside** the LLM.

**Sources:**
- https://docs.pipecat.ai/guides/features/pipecat-flows
- https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/
- https://newsletter.akapulu.com/p/building-a-reliable-ai-voice-agent-for-lead-qualification-with-pipecat-flows
- https://github.com/pipecat-ai/pipecat-flows

---

## 4. LiveKit Agents

**Abstraction:** Multi-agent handoff. Each "agent" is effectively a stage with its own instructions + tools. Handoffs happen by **returning a new agent instance from a tool call**:

```python
@function_tool
async def information_gathered(self, context: RunContext, name: str, location: str):
    context.userdata.name = name
    context.userdata.location = location
    story_agent = StoryAgent(name, location)
    return story_agent  # returning an agent triggers handoff
```

**Shared context:** A typed `dataclass` (e.g., `StoryData`) attached to the session as `userdata`. Every agent reads/writes it through `RunContext`. Chat history can optionally be carried across the boundary by passing `chat_ctx`.

**Transition trigger:** Tool call returns an Agent. Fully deterministic — the LLM only signals intent by calling the tool; your code decides the actual handoff.

**Sources:**
- https://github.com/livekit/agents/blob/main/examples/voice_agents/multi_agent.py
- https://livekit.com/blog/supervisor-pattern-voice-agents
- https://livekit.com/blog/sequential-pipeline-architecture-voice-agents

---

## 5. ElevenLabs Conversational AI — Workflows

**Abstraction:** Visual graph with typed nodes:
- **Subagent nodes** — override/append system prompt, LLM, voice, KB, tools at this point
- **Tool nodes** — guaranteed tool execution with success/failure edges
- **Transfer nodes** — to another agent or to a human phone line
- **End Call nodes**

**Transition trigger (three flavors):**
1. **LLM Conditions** — natural-language edges
2. **Expressions** — deterministic logic on variables
3. **Unconditional** — auto-advance

Backward edges enable retry loops. Tool nodes route on success vs failure.

**Shared context:** Implicit through workflow state + tool results. Per-node system prompt overrides stack on the base agent config.

**Sources:**
- https://elevenlabs.io/docs/eleven-agents/customization/agent-workflows
- https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow
- https://www.webfuse.com/blog/a-deep-dive-into-elevenlabs-agent-workflows

---

## 6. OpenAI Realtime Agents (reference SDK)

Two canonical patterns from OpenAI's own demo repo:

1. **Chat-Supervisor**: Realtime model handles the conversation; a smarter text model is called for tool-heavy / complex turns. Reduces latency without sacrificing reasoning quality on hard moments.
2. **Sequential Handoff**: Specialized agents transfer the user between themselves based on detected intent.

**State pattern:** "Define your conversation as a JSON structure that encodes both states and transitions." Same shape as everyone else.

**Sources:**
- https://github.com/openai/openai-realtime-agents
- https://developers.openai.com/api/docs/guides/voice-agents
- https://openai.github.io/openai-agents-python/sessions/

---

## 7. LangGraph / general framework view

**Abstraction:** Stateful graph of nodes; checkpointer persists state across turns via a thread ID.

**Their pitch:** "Linear, stateless approaches work for simple Q&A, but they fail when faced with real-world conversational complexity, such as conducting a dynamic survey, qualifying a sales lead, or guiding a user through a multi-step support process. To build agents that can navigate complex dialogues, you must adopt an event-driven state machine paradigm."

**Production example:** `lucasboscatti/sales-ai-agent-langgraph` — virtual sales agent with product inquiries, order management, recommendations. Confirms staged graphs are the production pattern for sales.

**Sources:**
- https://www.langchain.com/langgraph
- https://github.com/lucasboscatti/sales-ai-agent-langgraph
- https://dev.to/jamesli/langgraph-state-machines-managing-complex-agent-task-flows-in-production-36f4

---

## Cross-cutting answers to the brief

### Is there a canonical pattern?

Yes. **Graph of nodes, each node = focused prompt + scoped tools + transition rules; runtime state held outside the LLM**. The variations are surface-level naming and *who decides the transition*.

### What triggers a transition?

| Platform | Trigger |
|---|---|
| Bland (default) | LLM evaluates pathway label condition |
| Retell multi-prompt | LLM follows in-prompt rule ("if yes, go to X") |
| Retell Conversation Flow | Deterministic edge rules |
| Pipecat | Tool call → handler code returns next node |
| LiveKit | Tool call returns an Agent instance |
| ElevenLabs | LLM-condition OR expression OR unconditional, per-edge |
| OpenAI Realtime | Tool-call-driven handoff |
| LangGraph | Code-defined graph edges |

**The deterministic camp (Pipecat / LiveKit / LangGraph / Retell Flow) is gaining ground** specifically because LLM-judged transitions drift in long calls. Voxam's "Stop Letting the LLM Drive Your Voice Agent's State Machine" (https://voxam.hashnode.dev/stop-letting-llm-drive-voice-agent-state-machine) is the canonical rant.

### How do they keep prospect context across stages?

Three approaches, often combined:

1. **Variable extraction + template injection** (Bland, Retell, ElevenLabs) — `{{name}}`, `{{business_type}}` get filled into the next stage's prompt.
2. **Typed shared state object** (Pipecat `flow_manager.state`, LiveKit `userdata` dataclass) — code reads/writes a dict or struct across stages.
3. **Truncated chat history** — optional and often *avoided* on stage transitions. Pipecat explicitly offers `RESET` and `RESET_WITH_SUMMARY` so you don't re-pollute the new node with the old node's exchange.

**No one re-injects the full history every stage.** That's the failure mode they're fixing.

### How many stages is normal for sales?

No platform publishes a "correct" count. But:
- Retell's threshold (1000 words or 5 tools → split) means anything beyond a greeting+qualify+book agent crosses it.
- Pipecat's lead-qualification example: greet → ask budget → check timeline → pick service → decide → book = **6 nodes minimum**.
- LangGraph sales examples routinely have 8-15 nodes when handling product inquiries + objection paths + booking.

**8 stages is normal.** The risk isn't too many stages; it's stages that aren't tightly scoped, or stages whose transitions are LLM-judged on vague natural-language conditions.

### What failure mode does staging fix?

Cited consistently across sources:

- **Context rot** (Daily.co/Pipecat) — instructions earlier in a long prompt get ignored even when technically in-window.
- **Loss of focus** (Daily.co/Pipecat) — LLM "loses its way, confusing tasks or prematurely exiting a workflow."
- **Topic drift / question repetition** (Retell) — single prompt has no notion of conversation phases, so the model jumps around or re-asks.
- **Tool-call confusion** (Voiceflow / Future AGI) — when too many tools are listed in one prompt, the agent picks wrong ones or hallucinates parameters.
- **Latency from prompt size** (Future AGI) — 2000-token system prompt + 10k-token KB = 1200ms; 300-token system + 500-token max injection = 200ms.

---

## Recommendation for Voicsito (Vapi + GPT-4.1, sales call)

Vapi doesn't ship a first-class node graph runtime the way Bland/Retell/Pipecat do — its primitives are assistants, squads (multi-assistant handoff), and tool calls. So we're picking a pattern, not a UI.

The closest match to industry consensus, given GPT-4.1 + Vapi:

1. **Variable-driven shared state** — extract `prospect.name`, `prospect.business_type`, `prospect.budget`, `prospect.objections_raised[]` early and pass them forward as template fills. Like Bland/Retell.
2. **Tool-call-driven transitions** (deterministic camp) — each stage exposes a tool like `advance_to_objection_handling(reason: str)` rather than asking the LLM to infer "should I move on now?" from a natural-language rule. Like Pipecat/LiveKit.
3. **A global-style escape hatch** — like Bland's Global Nodes — to catch "transfer me / I'm ready to buy / unsubscribe" intents from any stage without modeling them as edges from every node.
4. **Per-stage prompt: persona block (shared, cached) + stage task block (focused, <500 tokens)** — mirrors Pipecat's Role/Task split exactly.
5. **Don't re-inject prior stage transcripts**; pass the extracted facts. This is the universal pattern.

8 stages is fine. The leverage is per-stage scope tightness and deterministic transitions, not stage count.

---

## Key sources

- https://docs.bland.ai/tutorials/pathways
- https://docs.retellai.com/build/single-multi-prompt/prompt-overview
- https://docs.retellai.com/build/single-multi-prompt/write-multi-prompt
- https://www.retellai.com/blog/unlocking-complex-interactions-with-retell-ais-conversation-flow
- https://docs.pipecat.ai/guides/features/pipecat-flows
- https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/
- https://github.com/livekit/agents/blob/main/examples/voice_agents/multi_agent.py
- https://livekit.com/blog/supervisor-pattern-voice-agents
- https://elevenlabs.io/docs/eleven-agents/customization/agent-workflows
- https://github.com/openai/openai-realtime-agents
- https://github.com/lucasboscatti/sales-ai-agent-langgraph
- https://voxam.hashnode.dev/stop-letting-llm-drive-voice-agent-state-machine
- https://futureagi.substack.com/p/why-your-voice-agent-fails-in-production
