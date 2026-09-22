# Staging Research — Vapi-Native Infrastructure

**Date:** 2026-05-11
**Question:** How do we split the 33k-char monolithic `inbound_texts.py` prompt into per-stage prompts using Vapi primitives, without leaving the platform?
**Scope:** 8 stages — opener → name/reason → services Q → pitch reveal → openness check → discovery (6 Qs) → close transition → booking (3 tools).
**Verdict (TL;DR):** **Use Vapi Squads** with 3 specialized assistants (Qualifier, Pitcher, Closer/Booker) and the `handoff` tool between them. Workflows are deprecated by Vapi for new builds. Mid-call system-prompt replacement does not exist as an API surface — the closest thing (`add-message` with role:system) just appends to history.

---

## 1) Vapi Workflows

### What it is
A visual graph of `conversation` / `transferCall` / `tool` nodes connected by `edges`. Each node has its own `systemPrompt`, `firstMessage`, optional `model`/`voice`/`transcriber` overrides, and `extractVariables`. Transitions are LLM-evaluated plain-language conditions ("User wants to speak to a human") OR liquid conditions on extracted variables (`{{ city == "San Francisco" }}`), or both combined.

### How you create one (API)
`POST https://api.vapi.ai/workflow` with `{ name, nodes[], edges[] }`. Update via `PATCH /workflow/{id}`. Server SDK: `vapi.workflows.create(...)`. Each node JSON looks like:

```json
{
  "id": "qualify",
  "type": "conversation",
  "firstMessage": "Hey is this {{first_name}}?",
  "systemPrompt": "You are Jon. Confirm identity and ask why they texted. Keep it under 25 words.",
  "extractVariables": [
    { "name": "reason_for_texting", "type": "string" }
  ]
}
```

Variables extracted in one node are available in every later node via liquid syntax. There is no separate "globalContext" object — variables are the threading mechanism.

### Pros
- Per-node prompt, per-node model, per-node voice, per-node tools — exactly what we want.
- Deterministic transitions when you want them (liquid conditions on variables).
- Variable extraction is first-class — no manual state tracking.
- Programmatic + visual builder both work; same JSON shape.

### Cons (this is the killer)
- **Vapi itself says: "We no longer recommend Workflows for new builds. Prefer Assistants or Squads depending on complexity."** Their stated reason: LLMs can't reliably maintain awareness of "current node + all valid next steps + their conditions." The decision tree is too rigid, and the LLM can't "jump" or reuse training from other branches. Source: docs.vapi.ai/workflows/overview.
- No latency claim published, but each node transition forces a fresh system-prompt swap; expect a small overhead per hop similar to Squads.
- Edge conditions become brittle at 8+ nodes — same problem we're trying to solve.

### Verdict for our use case
**No.** Vapi has explicitly steered us away. Even if the per-node prompt model fits our 8 stages on paper, building on a publicly-deprecated primitive is a self-inflicted wound. Skip.

---

## 2) Vapi Squads

### What it is
A persistent group of full Assistants (`members[]`) that can hand off to each other mid-call via the `handoff` tool. Each member is a **complete** assistant — its own `model.provider`, `model.model`, `model.messages` (system prompt), `tools`, `voice`, `transcriber`, `firstMessage`. The first member in the array initiates the call. Inbound and outbound both work.

### Create endpoint
`POST https://api.vapi.ai/squad` with:

```json
{
  "name": "Voicsito Inbound Texts",
  "members": [
    { "assistantId": "asst_qualifier" },
    { "assistantId": "asst_pitcher", "assistantOverrides": { "voice": {...} } },
    { "assistant": {
        "name": "Inline Booker",
        "model": { "provider": "openai", "model": "gpt-4.1", "messages": [...], "tools": [...] },
        "voice": {...}
    }}
  ],
  "memberOverrides": {
    "transcriber": { "provider": "deepgram", "model": "nova-2" }
  }
}
```

Members can be saved assistants (by id) or transient (inline). `memberOverrides` is broadcast to every member (good for sharing transcriber config). `assistantOverrides` per-member lets us tweak voice or model temperature without forking the assistant.

### The handoff tool (the actual stage transition)
Lives **on the source assistant**, not on the squad. Schema (`type: "handoff"`):

```json
{
  "type": "handoff",
  "function": {
    "name": "advance_to_pitch",
    "description": "Call when the prospect has confirmed identity and given a reason for texting in.",
    "parameters": { "type": "object", "properties": {}, "required": [] }
  },
  "destinations": [
    {
      "type": "assistant",
      "assistantName": "Pitcher",
      "description": "Use after qualification is complete.",
      "contextEngineeringPlan": { "type": "userAndAssistantMessages" },
      "variableExtractionPlan": {
        "schema": {
          "type": "object",
          "properties": {
            "first_name": { "type": "string" },
            "reason_for_texting": { "type": "string" },
            "services": { "type": "string" }
          },
          "required": ["first_name", "reason_for_texting"]
        }
      }
    }
  ],
  "messages": [
    { "type": "request-start", "content": "", "blocking": false }
  ]
}
```

### Context handling (this is the part that matters for our long script)
`contextEngineeringPlan.type` options:
- `"all"` — entire conversation history forwarded
- `"userAndAssistantMessages"` — strips system msgs, tool calls, tool results (keeps the dialogue)
- `"lastNMessages"` with `maxMessages` — recent window only
- `"previousAssistantMessages"` — only history before current assistant
- `"none"` — next assistant starts fresh

For our flow, **`userAndAssistantMessages`** is the sweet spot — Pitcher gets the qualification dialogue verbatim but doesn't inherit Qualifier's 8k-token system prompt. That's the whole point of staging.

### Per-handoff `variableExtractionPlan`
The LLM is forced to fill the schema before the handoff fires. This is how we pass clean structured state (first_name, biz_type, services, openness_score) forward without relying on the next assistant to re-extract from chat history.

### Conversation history during transfer (separate mechanism, for non-handoff transfers)
`historyMode` options on the transfer-call tool:
- `"rolling-history"` — append, new assistant sees everything
- `"swap-system-message-in-history-and-remove-transfer-tool-messages"` — replaces system msg, hides transfer tool calls from LLM
- `"delete-history"` — clean slate

For handoff tool specifically, `contextEngineeringPlan` (above) supersedes `historyMode`.

### Latency
Vapi explicitly markets handoffs as **"without audio artifacts. No dead air. No clipped words."** They make no published millisecond claim, but the platform's baseline end-to-end target is ~465ms. A handoff is essentially a system-prompt swap + first-message generation on the new model — empirically users report sub-second perceived latency. Net effect: per-stage prompts are *shorter*, so per-turn TTFT drops, which likely offsets the one-time handoff cost.

### Real example configs found
- **Fleetworks case study**: 240,000 calls/day through Squads with greeting → routing → scheduling/support agents. Vapi blog cites this as the production reference.
- **Vapi outbound sales doc**: lead research → lead qualification → booking pattern documented as the canonical sales squad shape (page slug `examples/outbound-sales`; some redirects observed).
- **YouTube — "VAPI Squads Tutorial for Beginners (Build an AI Call Center)"** (`youtube.com/watch?v=9WNYqIcZCnI`, posted Nov 2025) — beginner walkthrough of multi-assistant routing.
- **YouTube — "The Vapi Squad! A Full In-Depth Guide || Transcripts + Google Calendar (Inbound)"** (`youtube.com/watch?v=20bHFA7lzB8`) — inbound booking with calendar tool, directly applicable to our Type 2 flow.

### Pros
- Each member is a fully-formed Assistant with its own prompt, tools, voice, model.
- `contextEngineeringPlan` solves the "do I carry the system prompt forward?" question explicitly.
- `variableExtractionPlan` forces clean state transfer at the handoff boundary.
- Single phone number, single call ID, one Vapi billing event — caller sees one conversation.
- Vapi is actively investing here ("the Squads pattern has consistently led to better results for customers" — their words).
- Each assistant is independently testable in the Vapi dashboard before squad assembly.

### Cons
- Three assistants to maintain (versus one). Mitigated by storing each as a Python `PROMPT` string in `src/agent/prompts/` and pushing all three via API in one script.
- Handoff tool description has to be precise — vague conditions cause premature or stuck transfers (this is in the Vapi docs as the #1 footgun).
- Tool ownership is per-assistant — the booking tool lives only on the Closer. If Pitcher accidentally tries to book, the tool isn't there. Feature, not bug, but you have to be deliberate.
- No native "go back" — once you hand off to Closer, returning to Pitcher requires another handoff tool defined on Closer (rarely needed for our linear sales flow).

### Verdict for our use case
**Yes. This is the answer.** Our 8 stages collapse into 3 natural assistants with clear functional boundaries (qualification, pitch+discovery, close+book). Each assistant gets ~10–12k tokens of focused prompt instead of 33k. Latency drops on every turn because the LLM is processing a shorter system prompt. Tool gating becomes structural (booking tool only exists on Closer) instead of relying on inline `<tool_rules>` enforcement.

---

## 3) Mid-Call System Prompt Updates

### What I looked for
A `PATCH /call/{id}` or `controlUrl` POST that swaps the live system prompt while the call is in progress, so we could keep the monolithic structure but rotate the active prompt by stage.

### What actually exists
**It does not exist as a first-class feature.** Vapi's Live Call Control surface (`controlUrl` returned from `POST /call` inside `monitor.controlUrl`, endpoint pattern `https://aws-us-west-2-production1-phone-call-websocket.vapi.ai/{callId}/control`) supports exactly these message types:

- `say` — make the assistant speak a specific line
- `add-message` — inject a message into history (any role, including `"system"`)
- `control` — mute/unmute the assistant
- `end-call`
- `transfer` — to a phone number or SIP URI
- `handoff` — switch to a different assistant (this is the squad mechanism, callable externally too)

The `PATCH /call/{id}` endpoint only accepts `name` (an internal label). Nothing else.

### The `add-message` workaround
You can POST `{ "type": "add-message", "message": { "role": "system", "content": "..." } }`. This appends a system message to the conversation history; it does **not** replace the assistant's original system prompt. The original prompt still sits at position 0 and is still consumed every turn. Effect: you can *add* nudges, not *swap* prompts. The new system message applies from the next turn forward; it does not retroactively reshape prior context — Vapi's docs are silent on retroactivity, but the mechanism is fundamentally an append.

### Pros
- Useful for small mid-call corrections ("the prospect mentioned they're a roofer — bias toward roofing examples now").
- Zero infra changes — works on existing assistant.

### Cons
- Cannot reduce token usage — original 33k prompt still runs every turn.
- Cannot enforce tool gating — original tool list unchanged.
- Hard to orchestrate from server side reliably (you need to know exactly when to fire each stage change).
- No retroactivity guarantee; you're flying blind on how the LLM reconciles two system messages.

### Verdict for our use case
**No.** Not a substitute for staging. Useful as a *complement* to Squads (e.g., inject "the prospect mentioned a competitor named X" as a runtime nudge), but cannot solve the monolithic-prompt problem.

---

## 4) Migration sketch — Monolithic → 3-Assistant Squad

### Stage → Assistant mapping
| Current stage | Owner assistant | Tools |
|---|---|---|
| Opener / name confirm | **Qualifier** | `handoff_to_pitcher` |
| Reason for texting | Qualifier | — |
| Services question | Qualifier | — |
| **(handoff)** | → | extract: first_name, reason, services |
| Pitch reveal | **Pitcher** | `handoff_to_closer` |
| Openness check | Pitcher | — |
| Discovery (6 Qs) | Pitcher | — |
| **(handoff)** | → | extract: openness_score, biz_type, volume, pain_points |
| Close transition | **Closer** | `check_calendar`, `book_appointment`, `send_confirmation` |
| Booking | Closer | (the 3 booking tools) |

### File structure
```
src/agent/prompts/
  inbound_texts.py          # legacy, keep for rollback
  inbound_texts_qualifier.py
  inbound_texts_pitcher.py
  inbound_texts_closer.py
scripts/
  push_squad.py             # builds + upserts squad via API
```

### Push script outline (Python, ~80 lines)
```python
import requests, os
from src.agent.prompts import (
    inbound_texts_qualifier, inbound_texts_pitcher, inbound_texts_closer,
)

API = "https://api.vapi.ai"
H = {"Authorization": f"Bearer {os.environ['VAPI_API_KEY']}",
     "Content-Type": "application/json"}

def upsert_assistant(name, system_prompt, tools, voice, model="gpt-4.1"):
    body = {
        "name": name,
        "model": {
            "provider": "openai", "model": model,
            "messages": [{"role": "system", "content": system_prompt}],
            "tools": tools,
        },
        "voice": voice,
        "transcriber": {"provider": "deepgram", "model": "nova-2"},
    }
    # POST /assistant or PATCH if it exists
    return requests.post(f"{API}/assistant", headers=H, json=body).json()

qual_tools = [{
    "type": "handoff",
    "function": {"name": "advance_to_pitch",
                 "description": "Fire when prospect has confirmed name AND given a reason for texting AND mentioned what services they do."},
    "destinations": [{
        "type": "assistant", "assistantName": "Pitcher",
        "contextEngineeringPlan": {"type": "userAndAssistantMessages"},
        "variableExtractionPlan": {"schema": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string"},
                "reason_for_texting": {"type": "string"},
                "services": {"type": "string"},
            },
            "required": ["first_name", "reason_for_texting"],
        }},
    }],
}]

# Similar for pitch_tools (handoff to Closer) and closer_tools (booking tools)
# Then:
q = upsert_assistant("Qualifier", inbound_texts_qualifier.PROMPT, qual_tools, JON_VOICE)
p = upsert_assistant("Pitcher",   inbound_texts_pitcher.PROMPT,   pitch_tools, JON_VOICE)
c = upsert_assistant("Closer",    inbound_texts_closer.PROMPT,    closer_tools, JON_VOICE)

squad = requests.post(f"{API}/squad", headers=H, json={
    "name": "Voicsito Inbound Texts",
    "members": [
        {"assistantId": q["id"]},
        {"assistantId": p["id"]},
        {"assistantId": c["id"]},
    ],
}).json()
# Then: assign squad.id to the phone number (PATCH /phone-number/{id}).
```

### Phone number assignment
`PATCH /phone-number/{id}` with `{ "squadId": "..." }` (instead of `assistantId`). Same number, same caller experience.

### Rollback
Flip `phone-number.assistantId` back to the monolithic assistant. Zero-downtime cutover.

---

## 5) Tradeoffs vs current monolithic — bottom line

| Dimension | Monolithic (today) | 3-Assistant Squad |
|---|---|---|
| System prompt tokens per turn | ~33k chars (~8–10k tokens) | ~10–12k chars per stage (~3k tokens) |
| Per-turn LLM cost | High | **~60–70% lower** (input tokens dominate cost on GPT-4.1) |
| Per-turn latency (TTFT) | Heavier prompt parse | **Lower** (shorter prompt per stage) |
| One-time handoff latency | N/A | ~few hundred ms x 2 handoffs (Vapi: "no audio artifacts") |
| Tool gating | Enforced inline via `<tool_rules>` (LLM has to obey) | **Structural** — wrong tool isn't on the wrong assistant |
| Stage drift risk | High — model can skip ahead in monolithic prompt | Low — can't physically use Closer's tools until handoff fires |
| Debuggability | One giant prompt to bisect | Three focused prompts; can test in dashboard independently |
| Prompt iteration speed | Change one line, retest entire flow | Change Qualifier, retest only qualification |
| State carryover | Implicit in chat history | **Explicit** via `variableExtractionPlan` schemas |
| Failure mode | Model loses track at turn 15 | Handoff fires wrong, or doesn't fire — visible in logs |
| Platform alignment | Fighting Vapi's design | Riding Vapi's recommended path |

---

## 6) Sources

- [Vapi Workflows Overview (with deprecation note)](https://docs.vapi.ai/workflows/overview)
- [Vapi Workflows Quickstart](https://docs.vapi.ai/workflows/quickstart)
- [Vapi Squads docs](https://docs.vapi.ai/squads)
- [Vapi Handoff Tool reference](https://docs.vapi.ai/squads/handoff)
- [Vapi blog — Introducing Squads](https://vapi.ai/blog/introducing-squads)
- [Vapi blog — Simplify Complex Voice AI Workflows with Squads](https://vapi.ai/blog/squads)
- [Vapi Live Call Control](https://docs.vapi.ai/calls/call-features)
- [Vapi Update Call API (PATCH /call/{id} — name only)](https://docs.vapi.ai/api-reference/calls/update)
- [Vapi Create Squad API](https://docs.vapi.ai/api-reference/squads/create)
- [Vapi Update Squad API](https://docs.vapi.ai/api-reference/squads/update)
- [Vapi Outbound Sales Example (lead qualification workflow)](https://docs.vapi.ai/examples/outbound-sales)
- [Vapi Speech Latency / sub-500ms target](https://vapi.ai/blog/speech-latency)
- [Vapi Community — Workflows vs Squads](https://vapi.ai/community/m/1370807547749072937)
- [Vapi Community — Assistants vs Squads vs Workflows](https://vapi.ai/community/m/1414880041480749056)
- [Vapi Eval — Validate squad handoffs](https://docs.vapi.ai/observability/evals-quickstart)
- [YouTube — VAPI Squads Tutorial for Beginners (Build an AI Call Center)](https://www.youtube.com/watch?v=9WNYqIcZCnI)
- [YouTube — The Vapi Squad! Full In-Depth Guide (Inbound + Calendar)](https://www.youtube.com/watch?v=20bHFA7lzB8)
- [Medium — Real Time Call Control Vapi + FastAPI](https://mahimairaja.medium.com/realtime-call-control-vapi-fastapi-fb499d75fbb0)
- [GitHub — esplanadeai/call-control-V2 (community call control example)](https://github.com/esplanadeai/call-control-V2)
