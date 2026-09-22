# Voice-Agent-Specific Prompting Principles

Research date: 2026-05-11. Scope: principles that hold across multiple production voice-agent platforms (Vapi, OpenAI Realtime, Retell, ElevenLabs, LiveKit, Bland, Pipecat) and that differ meaningfully from chat-prompt advice. Every claim cites a source URL.

---

## 1. TTS-aware writing: numbers, symbols, punctuation, no markdown

The single most universal voice-agent rule across every platform: **the model's output is read aloud by a TTS engine, so any token that doesn't speak well must be rewritten before generation, not after.**

- **No markdown, no bullets, no code blocks, no JSON.** LiveKit: *"Respond in plain text only. Never use JSON, markdown, lists, tables, code, emojis, or other complex formatting."* ([LiveKit prompting guide](https://docs.livekit.io/agents/start/prompting/))
- **Spell out numbers, money, times, dates, phone numbers, emails.**
  - Vapi: *"Spell out numbers in prompts for more natural-sounding speech."* — `Four Thirty PM` not `4:30 PM`, `January Twenty Four` not `1/24`. ([Vapi prompting guide](https://docs.vapi.ai/prompting-guide))
  - Retell: phone as `"four one five - eight nine two - three two four five"`, email as `"n-a-m-e-@-c-o-m-p-a-n-y-dot-com"`, time `"Three thirty PM"`. ([Retell situation guide](https://docs.retellai.com/build/prompt-situation-guide))
  - ElevenLabs: *"Digits and symbols such as '@' or '£' are more likely to cause incorrect pronunciations"*, recommends rewriting `123` → `one-hundred and twenty three`. ([ElevenLabs prompting guide](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide))
  - awesome-voice-prompts: *"NEVER type out a number or symbol, instead ALWAYS type it in word form"* — `$130K` → `one hundred and thirty thousand dollars`. ([awesome-voice-prompts](https://github.com/langgptai/awesome-voice-prompts))
- **Acronyms split letter-by-letter** unless they're spoken as a word. LiveKit: *"Avoid acronyms and words with unclear pronunciation, when possible."* OpenAI Realtime: ship a `# Reference Pronunciations` block — *"Pronounce 'SQL' as 'sequel.'"* ([OpenAI Realtime prompting guide](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- **Omit URL scaffolding.** LiveKit: *"Omit `https://` and other formatting if listing a web URL."* ([LiveKit](https://docs.livekit.io/agents/start/prompting/))
- **Two implementation strategies** for the same goal — ElevenLabs documents both: the `system_prompt` strategy (instruct the LLM to write words) vs. the `elevenlabs` strategy (normalize TTS-side after generation). The system-prompt path is preferred when the LLM already controls phrasing. ([ElevenLabs](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide))

---

## 2. Length: 1–3 sentences, hard cap by token-budget

Voice agents fail at chat-prompt length. Long monologues hurt latency, comprehension, and turn-taking.

- LiveKit: *"Keep replies brief by default: one to three sentences. Ask one question at a time."* ([LiveKit](https://docs.livekit.io/agents/start/prompting/))
- OpenAI Realtime: *"2–3 sentences per turn"* under Personality & Tone, with per-state length overrides. ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- VoiceInfra: spoken content should be *"60–70% shorter than text equivalents since spoken content cannot be re-read"*; cap system prompts at *"200–500 tokens"*. ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))
- awesome-voice-prompts: *"NEVER SPEAK MORE THAN 2 SENTENCES"* and *"Under 20 words per sentence"*. ([awesome-voice-prompts](https://github.com/langgptai/awesome-voice-prompts))
- Layercode: *"Keep responses under 20 words."* ([Layercode/Toyo](https://toyo.ai/))

---

## 3. Latency cost of prompt size is real but small per token; conversation accumulation matters more

The chat-prompt instinct of "give the model a huge system prompt with every edge case" is a latency tax in voice.

- Talkative empirical test on GPT-4o (280 → 13,883 tokens, 200 reqs per size): *"Every additional 500 tokens in the AI prompt adds around 20–30 milliseconds of latency"* — ~0.04–0.06ms per token. Baseline OpenAI round-trip is ~800ms regardless. ([Talkative](https://gettalkative.com/info/ai-prompt-size-vs-llm-response-time))
- VoiceInfra claims a stronger effect: reducing a system prompt from 2,000 → 300 tokens combined with streaming cut response time *"by 60–85%"*; one 430→85 token prompt rewrite gave *"40% faster responses"*. ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))
- The bigger long-term cost is conversation accumulation: *"A typical minute of transcribed speech is ~150 tokens, and a 30-minute call eats 4,500 tokens, plus your system prompt, plus tool results."* ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))
- Target: *"median TTFT of 250ms or less"*; under 500ms feels responsive, above 1s *"feels wrong"*. ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))

**Implication:** the per-token latency is modest, so don't strip out clarity for 20ms. But cut anything the model doesn't need on *this turn* — and prefer state-machine prompts (OpenAI Realtime swaps system prompt via `session.update` based on the active phase: *"you only provide what's relevant to the active phase of the conversation"*). ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))

---

## 4. Fragmentation prevention: LLMs produce written-text rhythm by default

The core failure mode: trained models produce clean grammatical sentences with terminal periods. TTS reads each sentence as a discrete unit — separated, with a beat between each. That sounds choppy and stilted. The fix is to instruct the model to write speech, not prose.

- LiveKit blog (the canonical write-up): *"LLMs are trained on a lot of text, then post-trained to produce clean, grammatically correct writing. That's great for chatbots and emails, but it's not how humans talk... the model will fight you unless you're very explicit."* ([LiveKit blog: realistic prompting](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- The fix is structural: **start sentences with conjunctions**, **chain clauses with "so" / "and" / "but"**, **use ellipses for hesitation**, **let phrases trail or self-correct**.
  - LiveKit: *"Break grammar rules. Start sentences with 'And,' 'But,' or 'So.'"* ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
  - LiveKit: *"Pauses are fine; when you fill them, use 'ya' <break time='300ms'/>, or 'so yeah', or 'anyway'."* ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- **Reinforce repeatedly.** LiveKit: *"The model almost always needs more redundancy than you expect"* — say the rule in multiple places in the prompt. ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- **Examples beat instructions.** OpenAI Realtime: *"Guide with examples: The model strongly closely follows sample phrases."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))

---

## 5. Disfluencies and prosody markers

Convergent across LiveKit, Vapi, OpenAI Realtime, awesome-voice-prompts: **explicit instructions to insert fillers, with pairing rules**.

- Vapi: *"Use repeated letters or sounds (e.g., 'I-I-I don't know')"*; *"Add fillers like 'uh,' 'um,' or 'well'"*; *"Use ellipses ('...') to indicate a pause"*. ([Vapi](https://docs.vapi.ai/prompting-guide))
- LiveKit: *"After every standalone 'um', immediately insert <break time='300ms'/>"* and *"If you use 'um', make sure you always follow up with a 'so' after the pause!"* ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- awesome-voice-prompts (anchor openers, rotating): *"makes sense", "got it", "oh", "ok", "hmm", "oh wow", "well", "I see", "gotcha!", "right!"* — and *"Never repeat fillers: Vary anchor phrases throughout conversation"*. ([awesome-voice-prompts](https://github.com/langgptai/awesome-voice-prompts))

**Caveat (contested):** ElevenLabs' guide is silent on disfluencies, and its v3 alpha TTS treats them as audio cues encoded in the script. Some platforms (Bland's pathway-based design) prefer cleaner script control and let the TTS layer handle prosody. So: heavy filler instructions are most valuable when using a neutral cascading TTS (e.g. ElevenLabs Flash, Cartesia) with no native disfluency model; less needed with speech-to-speech models like `gpt-realtime`.

---

## 6. Reaction-first vs response-first — convergent: pair reaction with the line, not as a separate "step"

No platform recommends a literal two-step "first react, then respond" protocol — that produces the fragmentation failure mode above. Instead, the consensus is that the **single utterance should open with an acknowledgment token** and flow into the substantive reply in one breath.

- LiveKit anchor openers (loop-back without re-naming): *"Loop back without referring to the specific subject when you need to go back. 'About that other thing you mentioned'"*. ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- LiveKit explicit anti-repetition: *"Don't open consecutive turns with the same word or acknowledgment. Rotate through different short phrases."* ([LiveKit](https://docs.livekit.io/agents/start/prompting/))
- The OpenAI Realtime guide's "preamble" pattern is the same idea applied to tool calls (see §9).

---

## 7. Anchor / locked phrasing: prefer variations, gate strict verbatim only when required

Tension between consistency and natural delivery. Convergent answer: provide *sample phrases* not mandates, mark a small whitelist as strict.

- OpenAI Realtime: *"Below are sample examples that you should use for inspiration. DO NOT ALWAYS USE THESE EXAMPLES, VARY YOUR RESPONSES."* AND *"Whitelist must-keep phrases (legal/compliance/brand), and allow tighter phrasing where consistency matters."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- OpenAI Realtime anti-repetition: *"Do not repeat the same sentence twice. Vary your responses so it doesn't sound robotic."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- Retell on consistency under follow-ups: include explicit guidelines so responses remain *"consistent...even if customers double-check"*. ([Retell](https://docs.retellai.com/build/prompt-situation-guide))
- Bland's architecture inverts this — strict phrasing is achieved by **static node text** rather than LLM-generated speech in pathways. *"Use the Prompt field to give instructions on what the agent should do at this point in the conversation. This is the recommended way to generate responses as it makes the conversation more human and natural"* (with the static-text option still available when verbatim is required). ([Bland pathways](https://docs.bland.ai/tutorials/pathways))

---

## 8. Interruption handling — platform-mediated, prompt-supplemented

The platform handles barge-in detection. The prompt's job is to instruct the model what to *say* on resume and what to do when the user signals "hold on, don't talk".

- ElevenLabs is explicit: *"The system prompt controls conversational behavior and response style, but does not control conversation flow mechanics like turn-taking."* Turn-taking is at the platform level. ([ElevenLabs](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide))
- Retell's `NO_RESPONSE_NEEDED` pattern: for non-reasoning LLMs, use a hardcoded stop sequence the agent emits when the user says "hold on / give me a sec / wait" — the prompt instructs the model to output this exact token. *"Reasoning models: Prompt the agent to simply not respond, as the stop-sequence feature isn't supported."* ([Retell](https://docs.retellai.com/build/prompt-situation-guide))
- OpenAI Realtime "wait tool" equivalent: *"Call this when the latest audio does not need a spoken response, such as silence, background noise, hold music, TV audio, side conversation. Never say 'I'm here' or 'I didn't catch that.'"* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- VoiceInfra reports response-window targets: *"stop immediately and listen"* on interruption with a 200ms window, and *"wait 300–500ms after customer silence before responding"*. ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))

---

## 9. Tool-call audio: preambles to mask latency, silent for transfers

Two opposite patterns based on what the tool does.

- **Preamble pattern** for any latent operation (DB lookup, knowledge search): OpenAI Realtime: *"Before any tool call, say one short line like 'I'm checking that now.' Then call the tool immediately."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- **Per-tool override** in OpenAI Realtime: tools can be marked `PROACTIVE` (no preamble) or `CONFIRMATION FIRST` (must confirm before calling). ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- **Silent for transfers.** Vapi: *"If the AI determines that the user needs to be transferred, do not send any text response back to the user. Instead, silently call the appropriate tool for transferring the call to ensure a seamless user experience and avoid confusion."* And: *"Never say the word 'function' nor 'tools' nor the name of the Available functions"*. ([Vapi](https://docs.vapi.ai/prompting-guide))
- **Summarize structured returns, don't recite.** LiveKit: *"When tools return structured data, summarize it to the user in a way that is easy to understand, and don't directly recite identifiers or other technical details."* ([LiveKit](https://docs.livekit.io/agents/start/prompting/))

---

## 10. Transcription noise: prompt the model to assume the input may be corrupted

Voice prompts must acknowledge STT errors and instruct graceful clarification rather than confident guessing.

- OpenAI Realtime: *"Only respond to clear audio or text. If the user's audio is not clear (e.g. ambiguous input/background noise/silent/unintelligible) or if you did not fully hear or understand the user, ask for clarification."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- OpenAI Realtime sample: *"Sorry, could you repeat that clearly?"* — short English phrase, no reasoning, no tool calls on unclear input. ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- Toyo/Layercode includes context-of-environment as a required prompt section: *"Understand the user's spoken requests, even if the speech-to-text transcription contains errors. If you don't understand something, politely ask the guest to repeat."* ([Toyo](https://toyo.ai/))
- ElevenLabs raises a downstream issue: STT can put **spoken-form values** (`john at gmail dot com`) into the conversation context, so tool parameter schemas must specify the expected canonical format with examples. ([ElevenLabs](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide))
- VoiceInfra recovery pattern: *"'I'm sorry, I didn't catch that' followed by 'Did you say [option A] or [option B]?' — avoiding forced customer repetition."* ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))

---

## 11. Conversation state without architecture-speak: phases, goals, exit criteria

Multi-turn voice flows need a state machine, but the state machine should not be leaked to the model as "node 3 of 7". Instead: name the phase, give its goal, give its exit criterion.

- OpenAI Realtime structures states with: `id`, `description`, `instructions`, `examples`, `transitions: [{next_step, condition}]`. Example: *"Exit when: Intent and address are known."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- Dynamic-flow alternative: swap the system prompt itself on phase transition — *"you only provide what's relevant to the active phase of the conversation"*. ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- LiveKit recommends a hierarchical goal structure — *"overarching goal"* in base instructions; *"more specific and immediate goal"* per workflow stage. ([LiveKit](https://docs.livekit.io/agents/start/prompting/))
- Retell's "multi-prompt" architecture is the same idea at the platform level: each node has its own focused prompt, and *"dynamic variables"* carry state forward — *"if in Node1 the agent learns the customer's name and issue, Node2's prompt can include those as pre-filled variables."* ([Retell](https://www.retellai.com/blog/prompt-based-vs-conversational-pathways-choosing-the-right-approach))
- Pipecat Flows is the open-source equivalent: *"a necessary layer of control and logic that sits outside the context window, guiding the LLM step-by-step"*. ([Daily/Pipecat Flows](https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/))

---

## 12. First-message handling: identity + invite, kept short, in greeting state

- OpenAI Realtime: embed the greeting in a conversation-flow state with sample phrases. *"Identify as NorthLoop Internet Support. Keep the opener brief and invite the caller's goal."* Sample: *"Thanks for calling NorthLoop Internet—how can I help today?"* (marked as variation, not mandate). ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- Most platforms (Vapi, Retell, ElevenLabs, LiveKit, Bland) expose first-message as a **separate platform field** rather than something the LLM generates on turn one — this guarantees consistent identity/branding regardless of model state. ([ElevenLabs](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide))

---

## 13. Persona under stress: calm baseline, explicit recovery phrases

Phone calls have weird moments. Prompts that hold up specify the **baseline temperament** plus **specific recovery utterances**.

- LiveKit: *"Default to a calm, peaceful baseline."* and *"You carry a steady, positive energy without being syrupy about it."* ([LiveKit](https://docs.livekit.io/agents/start/prompting/), [LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- LiveKit on emotional ping-pong: *"Humans don't ping-pong between multiple emotions in one sentence."* — calm-adjacent baseline with selective stronger emotions. ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- LiveKit confusion recovery example: *"Sorry, <break time='300ms'/>, I think I missed that, what did you say?"* — authenticity by being visibly human in stressful moments. ([LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))
- VoiceInfra frustration handler: detect *"raised voice, negative language"* → *"Acknowledge immediately: 'I understand this is frustrating'"* → take ownership with a specific action. ([VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))

---

## 14. Pacing as content, not just a TTS knob

OpenAI Realtime explicitly recommends instructing **the LLM** to control pacing through the words it picks, because raw TTS speed flattens prosody.

- *"Deliver your audio response fast, but do not sound rushed. Do not modify the content of your response, only increase speaking speed for the same response."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- For end-of-turn signaling, give the model concrete punctuation cues (em-dash + stop, terminal question, brief tail like "right?"). The cross-platform synthesis is implicit but consistent: end-of-turn is signaled by **a complete clause + terminal punctuation + no trailing comma**. Ellipses are explicitly reserved for *mid-utterance hesitation*, not end-of-turn. ([Vapi](https://docs.vapi.ai/prompting-guide), [LiveKit blog](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic))

---

## 15. Language pinning

Voice agents code-switch under STT error or user prompts more readily than chat. Pin language explicitly.

- OpenAI Realtime: *"The conversation will be only in English. Do not respond in any other language even if the user asks."* ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))
- OpenAI Realtime fallback: *"Always respond in the same language the user is speaking in, if unintelligible."* (chooses language-stability over language-matching when STT is noisy.) ([OpenAI Realtime](https://developers.openai.com/cookbook/examples/realtime_prompting_guide))

---

## Contested / divergent points

1. **How much disfluency to script.** LiveKit and Vapi push hard for explicit fillers and pause tags. ElevenLabs and Bland (pathway mode) leave most of this to the TTS or the static script. The right answer depends on your stack: heavy script-level disfluency when using a flat cascading TTS; less needed with native speech-to-speech models.
2. **Single big prompt vs. multi-prompt / state-machine.** Retell explicitly contrasts the two, with both viable — *"single-prompt is simpler; multi-prompt scales to complex flows but requires state passing via dynamic variables"*. OpenAI Realtime supports both via `session.update`. Bland goes furthest into pathway architecture; ElevenLabs and Vapi sit closer to single-prompt with tools. ([Retell single vs multi](https://www.retellai.com/blog/prompt-based-vs-conversational-pathways-choosing-the-right-approach))
3. **Prompt-size latency magnitude.** Talkative's empirical numbers are modest (20–30ms per 500 tokens on GPT-4o); VoiceInfra reports 60–85% wins from prompt cuts that probably include other co-changes (streaming, model swap). The honest read: prompt size matters less than commonly claimed for TTFT, but matters more for accumulated context across long calls. ([Talkative](https://gettalkative.com/info/ai-prompt-size-vs-llm-response-time), [VoiceInfra](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide))

---

## Sources

- [Vapi — Voice AI Prompting Guide](https://docs.vapi.ai/prompting-guide)
- [OpenAI Cookbook — Realtime Prompting Guide](https://developers.openai.com/cookbook/examples/realtime_prompting_guide)
- [OpenAI — Realtime models prompting](https://developers.openai.com/api/docs/guides/realtime-models-prompting)
- [LiveKit — Prompting guide](https://docs.livekit.io/agents/start/prompting/)
- [LiveKit blog — Prompting voice agents to sound more realistic](https://livekit.com/blog/prompting-voice-agents-to-sound-more-realistic)
- [ElevenLabs — Conversational AI prompting guide](https://elevenlabs.io/docs/conversational-ai/best-practices/prompting-guide)
- [Retell — Prompt situation guide](https://docs.retellai.com/build/prompt-situation-guide)
- [Retell — Prompt-based vs. Conversational Pathway](https://www.retellai.com/blog/prompt-based-vs-conversational-pathways-choosing-the-right-approach)
- [Bland — Conversational Pathways](https://docs.bland.ai/tutorials/pathways)
- [Pipecat Flows — Beyond the Context Window (Daily blog)](https://www.daily.co/blog/beyond-the-context-window-why-your-voice-agent-needs-structure-with-pipecat-flows/)
- [VoiceInfra — Voice AI Prompt Engineering Guide](https://voiceinfra.ai/blog/voice-ai-prompt-engineering-complete-guide)
- [Talkative — How Prompt Size Affects LLM Response Time](https://gettalkative.com/info/ai-prompt-size-vs-llm-response-time)
- [awesome-voice-prompts](https://github.com/langgptai/awesome-voice-prompts)
- [Toyo / Layercode — Voice AI Prompting Guide](https://toyo.ai/)
- [Murf — How to Design Prompts for Voice Agents](https://murf.ai/blog/voice-agent-prompt-design)
