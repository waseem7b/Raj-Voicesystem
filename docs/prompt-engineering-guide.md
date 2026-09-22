# Prompt Engineering Guide for Voicsito Call Scripts

Source: Anthropic's official [prompt engineering best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), validated against Claude Sonnet 4.6 / Opus 4.7. This is the playbook for writing every call-type prompt in `src/agent/prompts/`.

Use this when adding Type 1, 3, 4, 5, 6 — and when iterating on Type 2.

---

## The 6 rules that matter most

### 1. Positive instructions, never negative

Anthropic's docs explicitly state:

> "Focus on positive instructions rather than negative constraints. For example, instead of telling Claude 'Do not use markdown,' instruct it to 'Compose your response using smoothly flowing prose paragraphs.'"

> "When aiming to decrease verbosity in Claude's responses, providing positive examples of concise communication is generally more effective than using negative instructions or telling the model what not to do."

**Why this matters:** Negative instructions like "DO NOT ask for last name" act like a "don't think about a pink elephant" — they prime the model toward the exact behavior we want to avoid. The model gives weight to the concept it's told to suppress.

| ❌ Avoid | ✅ Use instead |
|---|---|
| "DO NOT ask for last name" | (don't mention last name at all — script doesn't either) |
| "DO NOT hang up the call" | "Stay on the line until the prospect ends the call themselves" |
| "DO NOT recite multiple sentences" | "Read it like a real person on a casual phone call" |
| "Don't be robotic" | "Use contractions, pause naturally, react to what they actually say" |
| "Never sound like a salesman" | "You sound like a buddy in the trade calling another buddy" |

If a constraint really matters, find the positive frame. If you can't find one, the constraint probably isn't important enough to include.

### 2. Define the role first, in one sentence

The opening sentence of the prompt sets identity. Make it specific and concrete.

```
You are John, a contractor who calls other contractors about taking on
more business through marketing. You sound like a buddy in the trade
calling another buddy.
```

Not:
```
You are John (also spelled Jon), a highly successful yet incredibly
relatable and grounded entrepreneur calling contractor-to-contractor.
```

The second is bloated, hedged, and contains noise ("(also spelled Jon)" — useless to the model).

### 3. Structure with clear section headers

Markdown headers (`# Voice`, `# Context`, `# How the call goes`) work great for Claude. XML tags also work but are heavier visually.

Standard structure for a call-type prompt:

```
You are [identity, one sentence].

# Voice
[how they talk — vocabulary, mannerisms, energy]

# What the prospect knows
[context the prospect has from prior touchpoints — SMS thread, prior calls, etc.]
[any business reality they need to navigate, like the bait-and-switch]

# How the call goes
[numbered list, each step is a clear directive or quoted line they say]

# Handling reactions
[how to respond to common pushback, in-character]

# Capturing data
[when to call which tools]

# Style
[one final reminder of voice/tone]
```

Keep sections short. If a section grows past 8 lines, look for cuts.

### 4. Trust the script, don't over-scaffold

If the script already covers something, don't add a meta-rule about it.

- Script captures email at booking step → no need for a "remember to ask for email" rule
- Script asks 5 discovery questions → no need for a "make sure to ask all 5" rule
- Script ends with sign-off → no need for "always end the call gracefully" rule

The model reads the script. It will follow it if the script is clear. Adding rules ABOUT the script (rather than IN the script) is noise.

### 5. Give exact words for critical lines

Quoted phrases the model should say verbatim go in quotes within the script. The model will paraphrase if you describe a line ("greet them and acknowledge"); it will say close to the actual words if you quote ("Yeah, thanks for the call").

For sales calls, certain lines have been tuned over many calls. Lock those by quoting them. Let the model improvise around them, but the load-bearing lines stay exact.

### 6. Tool usage goes near tools, not in prose

When the LLM has tools available, the tool's docstring is what the model reads to decide when to use it. Put the "when to use" guidance there, not in the system prompt.

```python
# In src/webhooks/server.py, the tool handler reads function.name from the
# Vapi tool-call payload and dispatches to a handler:
#
# if name == "update_prospect_info":
#     # Call this the moment you learn the prospect's first name, business, or email.
#     # Send each field as soon as you have it — don't batch.
#     ...
```

In `prompts/inbound_texts.py`:
```
# Capturing data
Call `update_prospect_info` the moment you learn each piece — first name
right after the opener, business during the services question, email when
collected for booking.
```

The prompt says WHEN in the flow. The docstring says HOW the tool works. They reinforce each other.

---

## Anti-patterns I've made and you should avoid

### "(also spelled Jon)" — meta-noise about labels
The model doesn't care about spelling variants of the persona name. Just pick one and stick with it.

### "## Step 1", "## Step 2", "## Step 3"
Turns the prompt into a flowchart that the model executes mechanically. Numbered list (`1.` `2.` `3.`) under one `# How the call goes` heading is enough.

### "Hard rules" section at the bottom with 5 things
Usually all of these are reframings of negative instructions. Reframe positively and integrate into the relevant section, or drop entirely if the script handles it.

### Verbosity rules ("ONE sentence at a time when possible")
This made the agent stop mid-pitch. Some script sections are inherently multi-sentence (the origin story, the close). Trust the model to deliver them naturally if you tell it to "speak the way [persona] would speak".

### Repeating the same instruction in 3 places
"Be casual" in the persona section, "casual tone" in the voice section, "remember to be casual" at the bottom. Pick one place. The model reads the whole thing.

### Explanations of WHY a rule exists ("we do this because...")
The model doesn't need rationale to follow an instruction. Just give the instruction. Save the why for code comments or this doc.

---

## Length target

For a single-call-type prompt:

- **~1500–3000 characters** total (≈ 350–700 tokens)
- **One screen** of content, scrollable but not overwhelming
- If it's longer, cut. If it's shorter and works, leave it.

Compare:
- Old `inbound_texts.py` (pre-cleanup): 8242 chars — bloated, lots of "DO NOT" rules, "Step N" labels, hard-rules section
- New `inbound_texts.py` (current): ~3700 chars — persona + voice + context + numbered flow + reactions + tools + style. Done.

---

## Testing methodology

After every prompt change:

1. Deploy the prompt: `.venv-extract/bin/python scripts/sync-vapi-prompt.py inbound_texts`
2. Place a real test call to +1555010012
3. Listen for these failure modes:
   - **Robotic delivery** → tune voice settings (stability, style) in the Vapi assistant config (Vapi dashboard or `scripts/setup-vapi.py`), OR check if the prompt is making the model recite verbatim
   - **Stops mid-thought** → prompt has a "one sentence" rule, remove it
   - **Re-asking info already given** → prompt isn't telling model to react to what they say — add to Voice section
   - **Goes off-script entirely** → script section isn't directive enough, quote the actual words
   - **Asks for info not in script** (e.g. last name, age) → script has slack — make the asks specific
4. Iterate the prompt only — never tune model temperature or top_p as a fix for prompt issues

---

## When adding a new call-type prompt

1. Copy `prompts/inbound_texts.py` as the starting template
2. Keep the same section structure (`# Voice`, `# Context`, `# How the call goes`, `# Handling reactions`, `# Capturing data`, `# Style`)
3. Replace the script flow (the numbered list) with the new call type's flow
4. Adjust `# What the prospect knows` for the new context (e.g., for Demo Confirmation, they already booked — different mental state)
5. Update `prompts/__init__.py` to register the new module
6. Update the per-number assistant routing in Vapi (each call type gets its own assistant; route by dialed number).
7. Test, iterate, refine

---

## Key Anthropic doc references

- [Be clear and direct](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/be-clear-and-direct)
- [Use XML tags](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/use-xml-tags)
- [System prompts](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/system-prompts)
- [Control response format](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/control-response-format)
- [Multishot prompting (examples)](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/multishot-prompting)

Pull current docs anytime via Context7 MCP — `mcp__context7__query-docs` against `/websites/platform_claude_en`.
