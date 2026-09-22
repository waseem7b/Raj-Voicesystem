# Claude-Native XML Prompt Backup — 2026-05-10

## What this is

A snapshot of `src/agent/prompts/inbound_texts.py` taken right before we restructure the prompt for GPT-4.1.

This version is the **XML-heavy structure** that was built and tuned while the assistant ran on **Anthropic Claude Sonnet 4.6** (the LLM we used from project start through checkpoint #5). It uses `<step>`, `<verbatim>`, `<gate>`, `<if cond="...">`, `<objections>`, `<faq>`, etc. — Anthropic-recommended XML structure that Claude was RLHF'd to respect as semantic control flow.

## Why we're saving it

On 2026-05-08 (checkpoint #6) we swapped the LLM to **OpenAI GPT-4.1** for caching/latency reasons. A round-table audit on 2026-05-10 found GPT-4.1 reads the custom XML tags as text decoration, not as control flow — direct cause of:

- Step 3 ("How can I help you?") skipped in 5/9 recent calls
- Step 1 verbatim fragmented/paraphrased in 4/9 calls
- Premature `get_available_slots` tool call (1/9)
- Occasional Step 4 → Step 5 collapse

OpenAI's official GPT-4.1 Prompting Guide (April 2025) explicitly recommends Markdown structure, not XML, for control flow. We're rewriting toward that.

**If we ever switch back to Claude (Sonnet/Opus 4.x or later), this is the prompt to restore.** Claude honors XML gates natively — what's a bug on GPT is a feature on Claude.

## How to restore

```bash
cp docs/reference-points/2026-05-10-claude-xml-prompt/inbound_texts.py \
   src/agent/prompts/inbound_texts.py

# Then push to Vapi:
.venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts \
  --assistant 261bf1a0-3527-4fcb-956c-b6e227d0bae1
```

Also flip the assistant `model.provider` back to `anthropic` and `model.model` to `claude-sonnet-4-6` (or current Claude) before re-syncing — otherwise you'll be running the Claude prompt on GPT, which is what we just spent time getting out of.

## State at time of snapshot

- LLM: `openai/gpt-4.1`, temp 0.6, maxTokens 350, `promptCacheRetention: "24h"`
- Voice: ElevenLabs `eleven_multilingual_v2`, voiceId `ljX1ZrXuDIIRVcmiVSyR`
- Prompt size: 22,552 chars (Step 3 hardened + `<tool_rules>` added earlier today)
- Last assistant sync: 2026-05-10 — see `docs/checkpoints.md` entry for that date
