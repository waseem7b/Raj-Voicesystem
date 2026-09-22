# Voicsito Checkpoints

Reverse-chronological log of project state snapshots. Newest first.

A checkpoint captures: live Vapi assistant config, webhook server health, recent changes, open issues, and what to test next. Run by saying "checkpoint" — see [`.claude/skills/checkpoint/SKILL.md`](../.claude/skills/checkpoint/SKILL.md).

---

## 2026-05-18 16:55 — checkpoint #7 (3-stage Squad live + voice retune + docs resync)

**Live state (pulled from Vapi API):**
- Squad: `Voicsito - Squad - Inbound Texts` (`bea22dc5-24eb-42ed-ba81-f52cf1986ebf`) — 3 members
  - Stage 1 — `Voicsito - Stage 1 - Inbound Texts` (`46e47e95-674a-4624-9029-69bb39c4daf0`) — prompt 19,570 chars; tools: `update_prospect_info` + inline `handoff` + inline `endCall`
  - Stage 2 — `Voicsito - Stage 2 - Qualify` (`7b2666e0-154d-4c85-82f0-07b4c51ad264`) — prompt 19,731 chars; tools: `update_prospect_info` + inline `handoff` + inline `endCall`
  - Stage 3 — `Voicsito - Stage 3 - Book` (`7600c2b6-d669-4f2e-8387-e371b0c193d4`) — prompt 14,506 chars; tools: `update_prospect_info` + `get_available_slots` + `mark_for_transfer` + `book_video_call` + inline `endCall`
- Monolithic assistant (`261bf1a0-3527-4fcb-956c-b6e227d0bae1`) — rollback target only; tool list partially stripped (no `update_prospect_info`), no system message visible.
- Phone: `+1555010011` (`cb93e61d-d222-42c9-8fb2-74500dc13234`) — provider=`vapi`, `serverUrl=https://webhooks.c7a.io/voicsito/vapi/server-events`, `timeoutSeconds=8`. Dynamic routing via `assistant-request`.
- LLM (every stage): **`openai/gpt-4.1`**, temp=0.6, maxTokens=350, **`promptCacheRetention: "24h"`**
- Voice (every stage): ElevenLabs **`eleven_turbo_v2_5`**, voiceId **`F2BOg7Ry3zTPV6Zd62Cd`** (stock), stability=0.30, similarityBoost=1.00, style=1.00, speed=1.00, useSpeakerBoost=true
- Transcriber: deepgram nova-3 endpointing=150ms smartFormat=true
- Speaking plans: waitSeconds=0.2, smartEndpointing=false, transcriptionEndpointingPlan={onPunctuation:0.1, onNoPunctuation:0.4, onNumber:0.5}, stopSpeakingPlan={numWords:2, voiceSeconds:0.2, backoffSeconds:1.0}
- firstMessage MP3: `https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3?v=20260512-122624` (Stage 1 plays it via `assistant-speaks-first`; Stages 2/3 use `assistant-speaks-first-with-model-generated-message`)
- Background sound: `car-bg-v3.mp3` on every stage
- Voicemail detection: `google` audio
- Tools (live timeouts): `update_prospect_info` 12s async, `get_available_slots` 25s, `mark_for_transfer` 12s, `book_video_call` 60s — all sync at `webhooks.c7a.io/voicsito/vapi/tool`
- Hooks: **NONE on the stage assistants** (regression from monolith); only `silenceTimeoutSeconds=60` remains as safety net
- Webhook: `{"ok":true,"service":"voicsito-webhooks"}` ✓

**Recent changes since #6 (latest first):**
- **3-stage Vapi Squad shipped (2026-05-11)** per `Stages.md`. The monolithic 33K-char prompt was split into `stage1_inbound_texts.py` (INTEREST, 184 lines) → `stage2_qualify.py` (QUALIFY, 211 lines, shared across call types) → `stage3_book.py` (BOOK, 142 lines, shared). All three import `shared_persona.py` (172 lines) so persona/voice/output rules live in one place. Pre-split reference at `docs/reference-points/2026-05-11-pre-stage-split/`.
- `scripts/push_squad.py` added — idempotent build/update of all 3 stage assistants + the Squad in one shot.
- **Voice retune (2026-05-13)** — swapped from Michael stock (`ljX1ZrXuDIIRVcmiVSyR`) + `eleven_multilingual_v2` to current `F2BOg7Ry3zTPV6Zd62Cd` + `eleven_turbo_v2_5`. Faster TTFB on the stock voice (no PVC to protect). Settings: stability 0.40→0.30, similarityBoost 0.75→1.00, style 0→1, speed 1.1→1.0.
- Background sound updated to `car-bg-v3.mp3` (20-min mono, -31 LUFS, band-passed 200-4500 Hz). Was `car-bg-v2.mp3`.
- `firstMessage` shifted to pre-rendered MP3 (`firstmessage-opener.mp3?v=20260512-122624`) on Stage 1; Stages 2/3 generate their continuation message from the model.
- Webhook server: `_handle_assistant_request` now returns either `{assistantId, ...}` (legacy) or `{squadId, ...}` depending on `VAPI_SQUAD_ID` env var. CRM variables (`biz_type/business/city/state`) propagate via `assistantOverrides.variableValues`.
- `_handle_book_video_call` now wraps the entire handler in `asyncio.wait_for(timeout=45.0)` so the LLM gets a clean error before the 60s Vapi tool budget breaches. Internal main-CRM polling also enforces a 15s ceiling.
- `book_video_call` now validates `slot_iso` against the cached `offered_slots` set with a canonical-ISO comparison — rejects hallucinated slots while tolerating textual reformatting (e.g. `Z` vs `+00:00`).
- Tool-call dispatcher runs `update_prospect_info` + `get_available_slots` first (priming tools) before other batched calls, so `book_video_call` sees the cache primed.
- End-of-call archive + Telegram push pipeline (`call_archive.py`, `call_summary.py`, `telegram_notify.py`) ships summary + transcript + audio per call; idempotent via `pushed.flag` sentinel.
- Reference points added: `2026-05-10-claude-xml-prompt/`, `2026-05-11-jon-clone-voice-baseline/`, `2026-05-11-pre-stage-split/`.

**Open issues / what's not working:**
- **Hooks regression.** The 12s nudge + 36s graceful-hangup hooks on the monolith were NOT re-attached to any of the 3 stage assistants. Calls that go quiet die silently at the 60s `silenceTimeoutSeconds` instead of being nudged + signed off warmly. **Highest-priority fix.**
- Jon PVC re-clone still not done — clean source (`recordings/jon-only/`) exists, just unprioritized. Currently running on stock voice with no Jon character.
- Stage hand-off relies on the LLM firing the inline `handoff` tool; sim coverage of boundary cases (objection deep in Stage 1, prospect skipping discovery, etc.) is light.
- Monolithic assistant (`261bf1a0`) has stale toolIds — missing `update_prospect_info`. Will need a refresh before it's a safe rollback target.
- `docs/sales-framework-handoff.md` and other docs still carry "Wah-seem" / Sonnet / Multilingual refs in reference-point snapshots (intentional — frozen for rollback) and historical checkpoints. Active docs cleaned in this checkpoint.
- 5 of 6 call types still unbuilt — only `inbound_texts` ships.
- Twilio Elastic SIP Trunk import for phone-side latency is still deferred.
- Anthropic prompt caching on Vapi still not exposed by the platform; if/when it lands, re-evaluate the LLM swap.

**What to test next:**
- Re-attach the idle/silence hooks to each stage assistant (either via `push_squad.py` patch or a direct PATCH on each assistant). Live-call test that the 12s nudge fires + the 36s graceful hangup works post-handoff.
- Run the 12-persona sim bank against the live 3-stage prompts; surface any boundary-case failures (handoff misfires, objection routing through the wrong stage).
- Refresh the monolithic assistant's tool list so the documented rollback procedure actually restores a working state.
- Schedule the Jon PVC re-clone using `recordings/jon-only/` + `scripts/stitch-jon-clean.py`.

**Files touched in this checkpoint:**
- `docs/system-summary.md` — full rewrite of status header, stack inventory, audio pipeline, latency budget, file structure, known issues, summary; added §8 Vapi resource IDs table
- `docs/architecture.md` — status header, quick links, stack table, "what stays / what changes" tables, latency budget, Phase 1 checklist, operating cost table
- `docs/local-development.md` — status header, repo structure, Path A workflow, Vapi tuning table, new-call-type instructions, test number
- `docs/checkpoints.md` — this entry

**Staleness sweep results:**
- `caddy`, `/opt/voicsito/webhooks/`, `webhooks.voicsito.cloud7agency.com`, port 8000 — only historical mentions in `docs/checkpoints.md` (OK)
- `Sonnet 4.6` / `Anthropic Claude` / `eleven_multilingual_v2` / `LJ3VmFT7KIfEw9jSLImM` / `ljX1ZrXuDIIRVcmiVSyR` / `pwMBn0SsmN1220Aorv15` — still present in: reference-point snapshots (intentional, frozen for rollback), historical checkpoints (intentional), `docs/prompt-engineering-guide.md` + `docs/vapi-migration.md` (not touched this round — older design docs, less critical; consider sweep next checkpoint)
- `Wah-seem` orphans: `docs/sales-framework-handoff.md` (one-shot handoff doc, content integrated long ago; recommend archive) and historical checkpoints (OK)
- Webhook health: `{"ok":true,"service":"voicsito-webhooks"}` ✓

---

## 2026-05-08 20:40 — checkpoint #6 (OpenAI swap + PVC contamination diagnosed + reference point saved)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi`, `assistantId=None` (dynamic via assistant-request)
- LLM: **`openai/gpt-4.1`**, temp=0.6, maxTokens=350, **`promptCacheRetention: "24h"`**, prompt **20,740 chars**
- Voice: ElevenLabs `eleven_multilingual_v2`, voiceId **`pwMBn0SsmN1220Aorv15`** (TEST VOICE — temporary while we re-clone Jon), stability=0.40, similarityBoost=0.75, style=0, speed=0.95, useSpeakerBoost=**false**, optimizeStreamingLatency=2, autoMode=false
- Transcriber: deepgram nova-3 endpointing=150ms
- Speaking plans: waitSeconds=0.2, smartEndpointing=false, transcriptionEndpointingPlan={onPunctuation:0.1, onNoPunctuation:0.4, onNumber:0.5}, stopSpeakingPlan={numWords:2, voiceSeconds:0.2, backoffSeconds:1.0}
- Tools: 4 (`mark_for_transfer`, `book_video_call`, `get_available_slots`, orphan `update_prospect_info`)
- Webhook: `{"ok":true,"service":"voicsito-webhooks"}` ✓
- Background sound: car ambience mp3 (band-passed 300-3400Hz)
- Voicemail detection: `google`
- Hooks: 12s nudge + 36s graceful-hangup

**Recent changes since #5 (latest first):**
- **PVC contamination root cause confirmed.** User recalled the original training file included prospect voices (raw call audio, not the diarized `jon-only/` slices). Diagnostic test: swapped voiceId to `pwMBn0SsmN1220Aorv15` (test voice) — volume drift went away. Confirms the original PVC `LJ3VmFT7KIfEw9jSLImM` was trained on a mixed-speaker file, baking that variance into the model. Re-clone using `recordings/jon-only/` (61 diarized files, ~5GB) is the fix. Currently running on test voice while we prep the stitch + re-upload.
- `voice.useSpeakerBoost: true → false`. Per ElevenLabs docs: useSpeakerBoost amplifies source variance and "can lead to instability, mispronunciation, extra sounds." Off is the right setting for a noisy clone, and stays right for a clean one.
- **LLM swapped: Anthropic Sonnet 4.6 → OpenAI GPT-4.1** with `promptCacheRetention: "24h"`. Vapi exposes auto-caching for OpenAI but NOT Anthropic — we were paying full prefill on every Sonnet turn (`cachedPromptTokens=0` confirmed across 5 calls). User confirmed quality "solid" after swap. Cached TTFT ~400-600ms vs Sonnet's ~850-1,200ms uncached — net swing 250-700ms per turn after first.
- **Reference point saved** at `docs/reference-points/2026-05-08-quality-reference/` (assistant.json, phone.json, tools.json, inbound_texts.py, server.py, ghl.py, RESTORE.md). User can say "go back to reference point" to restore the exact config they confirmed sounded "perfect" — last known-good Sonnet 4.6 + Jon-PVC + tuned voice settings.
- Webhook performance pass (latency tail fixes):
  - `wait_for_main_crm_contact` polling 4×12s=48s → **5×3s=15s** (tightens worst-case `book_video_call` hang)
  - `update_prospect_fields` skips GET-before-PUT when business is provided (one fewer GHL round-trip on common path)
  - `assistant-request` GHL lookup timeout 5s → **2.5s** (call connects faster on slow GHL)
- Round-table research on three latency levers (ElevenLabs chunkPlan tuning, BYO Twilio SIP, custom LLM webhook with Anthropic caching) — all three KILLED by the round table. chunkPlan would compound variance on PVC; BYO SIP savings unverified; custom LLM only ~85ms savings at 5K tokens with 5-min TTL useless for cold-call cadence. OpenAI swap was the one that survived scrutiny.
- Step 4 services_question wrapped in `<verbatim>` + `<gate>` (was loose prose, model was skipping it after biz_type substitution). Now gated like steps 5/7.

**Open issues / what's not working:**
- Test voice `pwMBn0SsmN1220Aorv15` is a stranger's voice — not Jon. Re-clone is the next priority.
- Original PVC training file contained prospect voices alongside Jon's — caused volume/clarity drift on every generation. Fix: re-clone using `recordings/jon-only/` (61 diarized files).
- Mid-reveal stutter (TTS streaming, observed on call 019e0998) — flagged for later.
- Sim doesn't integration-test variable injection (substitutes biz_type from persona YAML).
- Anthropic prompt caching never worked on Vapi (provider-side limit, not actionable client-side).
- 5 of 6 call types still unbuilt (deferred per user).
- `update_prospect_info` orphan tool still in Vapi tool listing.
- `docs/sales-framework-handoff.md` has stale "Wah-seem" references (one-shot doc, content integrated — recommend archive/delete).

**What to test next:**
- **Stitch the 61 `recordings/jon-only/` files into a single audio file** (probably FLAC or WAV, normalized to consistent RMS).
- Upload as new ElevenLabs PVC, get new voice ID.
- Swap `voice.voiceId` from `pwMBn0SsmN1220Aorv15` (test) to the new Jon PVC.
- Listen for: voice character is recognizably Jon, AND volume/clarity stays consistent across the call.
- Optional after re-clone: re-test `useSpeakerBoost: true` (it may be safe now that source is clean — boosts similarity to the cleaned source).

**Files touched since #5:**
- `src/agent/prompts/inbound_texts.py` — Step 4 wrapped in `<verbatim>` + `<gate>`, dead-prose removed
- `src/webhooks/server.py` — `assistant-request` timeout 5s→2.5s
- `src/agent/ghl.py` — `wait_for_main_crm_contact` polling tightened, `update_prospect_fields` GET removed from common path
- `docs/reference-points/2026-05-08-quality-reference/` — NEW directory with full snapshot + RESTORE.md
- Vapi assistant `261bf1a0...` — model+voice settings PATCHed multiple times

**Staleness sweep:**
- `docs/system-summary.md` — updated to reflect OpenAI/PVC state
- `docs/sales-framework-handoff.md` — still has "Wah-seem" references (one-shot doc, content integrated; recommend archive)
- Other staleness clean.

---

## 2026-05-08 18:05 — checkpoint #5 (sales-framework integration + dynamic phone routing + voice retune)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi`, `assistantId=None` (DYNAMIC — Vapi now calls `assistant-request` on every inbound), `server.url=https://webhooks.c7a.io/voicsito/vapi/server-events`, `server.timeoutSeconds=8`
- LLM: `anthropic/claude-sonnet-4-6`, temp=0.6, maxTokens=350, prompt **20,740 chars** (up from 10,883 in #4 — sales framework + objections + FAQs added, then trimmed via dead-prose audit)
- Voice: ElevenLabs `eleven_multilingual_v2`, voice `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone) — **stability=0.40 (was 0.5), similarityBoost=0.75 (was 0.9), style=0, speed=0.95 (was 1.0)**, useSpeakerBoost=true, optimizeStreamingLatency=2, autoMode=false, enableSsmlParsing=false
- Transcriber: `deepgram` `nova-3` endpointing=150ms
- Speaking plans: waitSeconds=0.2, smartEndpointing=false, transcriptionEndpointingPlan={onPunctuation: 0.1, onNoPunctuation: 0.4, onNumber: 0.5}, stopSpeakingPlan={numWords: 2, voiceSeconds: 0.2, backoffSeconds: 1.0}
- Tools: 4 — `mark_for_transfer` (sync, 12s), `book_video_call` (sync, 60s), `get_available_slots` (sync, 12s), `update_prospect_info` (async, orphan — still flagged)
- Webhook server: `{"ok":true,"service":"voicsito-webhooks"}` at https://webhooks.c7a.io/voicsito/health
- Background sound: car ambience mp3 (band-passed 300-3400Hz)
- Voicemail detection: `google`
- Hooks: 12s nudge + 36s graceful-hangup, both on `customer.speech.timeout`

**Recent changes since #4 (latest first):**
- Voice retune: stability 0.5→0.40, similarityBoost 0.9→0.75, speed 1.0→0.95 — dropped over-locked PVC prosody per ElevenLabs docs (similarityBoost 0.9 = over-locked, ElevenLabs default 0.75)
- Step 4 wrapped in `<verbatim>` + `<gate>` (was loose "Ask:" prose; model was skipping the services question and jumping to step 5 reveal)
- Big dead-prose audit (2 parallel research agents): cut 2,386 chars of defensive notes / contradictions / scaffolding. Examples: deleted `<out>` block (let `<question_framework>` "improvise" rule win), deleted duplicate verify-step in `not_interested_marketing` turn 1, deleted `<global_gate>` exceptions list (replaced with "see <persistence>"), deleted `text_promise_complaint` DO-NOT-FIRE block, deleted step 1's "advance to step 2 immediately" gate (was causing batching), deleted step 2/3 redundant `<note>` blocks, deleted `<do>` lines that restated `<when>`/`<tier>`
- Trigger rename: `text_or_jobs_reference` → `text_promise_complaint`, narrowed to push-back phrasing only (FIRE list = complaints, neutral text mentions explicitly excluded)
- Global gate added: ALL `<objections>` fire only AFTER step 5 reveal — pre-reveal, prospect text/jobs mentions are normal flow input
- `<context>` now exposes 4 CRM fields: Trade / Business / City / State (with empty=unknown rule)
- Webhook (server.py:587) extended to inject `city` and `state` into `assistantOverrides.variableValues` alongside biz_type and business
- **Phone unbound from static assistantId** — Vapi was skipping `assistant-request` because phone had `assistantId=261bf1a0...`. Set to None. Now webhook fires on every inbound and injects CRM data. Verified live: `assistant-request hit phone=+1555010010 biz_type='Epoxy' city='Vancouver' crm=texts1`.
- `{{biz_type}}` empty-fallback branch removed from step 4 (user: "biztype must always have a value")
- Sales framework integrated from `docs/sales-framework-handoff.md`: `<persistence>`, `<objection_framework>` (acknowledge → address → ask, "and" not "but", first-objection verify), `<question_framework>` (answer → pivot, bridge phrases, defer-to-demo list), 5 new objections (text_promise_complaint, not_interested_marketing, has_marketing_guy, covered_up_on_work, cost_question with 3-tier escalation), 11 FAQs (Angie's, where based, company names, how found number, how paid, contract, what marketing, middleman, service radius, competitors, website)
- New business facts in prompt: Vancouver Epoxy Experts (flooring co), Cloud 7 Agency (us), $947-$4K pricing, 3-month minimum, 30-mile radius, exclusivity guarantee
- Block reordering: `<flow>` moved to LAST per Anthropic long-context guidance (executable instructions get max recall when at end)

**Open issues / what's not working:**
- Sim does NOT integration-test variable injection — substitutes `{{biz_type}}` directly from persona YAML, never hits webhook. Real fix requires sim hitting `/vapi/server-events` with synthetic `assistant-request` payload. Deferred.
- Public webhook URL `webhooks.c7a.io/voicsito/health` is flaky from local dev IP (timeout). Vapi reaches it fine. Local-only issue, not blocker.
- Anthropic prompt caching not firing on Vapi: `cachedPromptTokens=0` on every call. Vapi's `promptCacheRetention` field is OpenAI-only; AnthropicModel doesn't expose a caching toggle. Means every turn pays full prefill on the 20.7K-char prompt. Cost/latency tax.
- Mid-reveal stutter (TTS streaming): on call 019e0998, Jon paused mid-sentence during step 5, prospect said "Hello?", Jon resumed. Not prompt issue — TTS streaming or VAD interpretation. Flagged for later.
- `update_prospect_info` orphan tool still in Vapi `/tool` listing — not referenced by assistant. Cleanup deferred.
- 5 of 6 call types still unbuilt (deferred per user: focus on Type 2 quality).

**What to test next:**
- Real call to evaluate the voice retune (stability 0.40 / similarityBoost 0.75 / speed 0.95) — listen for naturalness vs robotic
- Verify step 4 fires reliably with new `<verbatim>`+`<gate>` structure (no more skipping to step 5)
- Verify global_gate prevents pre-reveal objection misfires
- Hormozi-style cadence: ellipses + em-dash punctuation already in pacing rules — measure if it's working; consider adding written disfluencies ("yeah so...", "uh") into LLM output for next iteration

**Files touched since #4:**
- `src/agent/prompts/inbound_texts.py` — multiple iterations (added sales framework, audit-trimmed, fixed step 4 structure)
- `src/webhooks/server.py:587-621` — added city/state to overrides_vars, expanded log line
- `tests/recordings/` — 5 new call transcripts saved (one per test cycle)
- Vapi assistant `261bf1a0...` — voice settings + prompt patched
- Vapi phone `cb93e61d...` — assistantId unset

**Staleness sweep (cleanup TODOs flagged):**
- `docs/sales-framework-handoff.md` — one-shot handoff doc, content now fully integrated into the prompt; still has stale `Wah-seem` references in its verbatim copy of the prompt. Recommend: archive or delete (handoff complete).
- Other staleness clean: caddy / `/opt/voicsito/webhooks/` / `webhooks.voicsito.cloud7agency.com` only appear in `docs/checkpoints.md` (historical log — fine).
- "Sonnet 4.6" appears in 6 docs — current model is Sonnet 4.6, this is correct (no action).

---

## 2026-05-08 14:07 — checkpoint #4 (XML prompt restructure + sim-test harness + 12-persona bank + Waseem cleanup)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi`
- LLM: `anthropic/claude-sonnet-4-6`, temp=0.6, maxTokens=350, prompt **10,883 chars** (up from ~7k pre-restructure), tools=[endCall] + 3 webhook toolIds
- Voice: ElevenLabs `eleven_multilingual_v2`, voice `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone) — stability=0.5, similarityBoost=0.9, style=0, speed=1, useSpeakerBoost=true, optimizeStreamingLatency=2, autoMode=false
- Transcriber: `deepgram/nova-3`, endpointing=150ms, smartFormat=true
- startSpeakingPlan: waitSeconds=0.2, smartEndpointingEnabled=false; transcriptionEndpointingPlan: onPunctuation=0.1, onNoPunctuation=0.4, onNumber=0.5
- stopSpeakingPlan: numWords=2, voiceSeconds=0.2, backoffSeconds=1.0
- silenceTimeoutSeconds=60, maxDurationSeconds=1800
- backgroundSound=`https://webhooks.c7a.io/voicsito/static/car-bg-v2.mp3`
- voicemailDetection: provider=google, type=audio
- hooks: 2 (`idle_nudge_12s` 12s timeout 2x, `graceful_hangup_after_silence` 36s timeout 1x — both on `customer.speech.timeout` with `triggerResetMode=onUserSpeech`)
- Tools: `get_available_slots` (12s), `mark_for_transfer` (12s), `book_video_call` (60s) all sync at `webhooks.c7a.io/voicsito/vapi/tool`. System: `endCall`. Orphan: `update_prospect_info` (id `278ff15a`) still unattached.
- Webhook server: ✓ `{"ok":true,"service":"voicsito-webhooks"}` at `https://webhooks.c7a.io/voicsito/health`
- firstMessage: `Hey, Jon here — who is this calling?`

**Recent changes since checkpoint #3 (latest first):**
- **Waseem spelling cleanup**: removed "Wah-seem" phonetic hack from prompt (3 occurrences) + dropped the `<output_format>` rule mandating it. Sim showed Jon was wrongly applying the partner-name spelling to prospects also named Waseem (3/10 runs). Updated `tests/personas/just_says_name.yaml` prospect Waseem → Jake.
- **12-persona bank**: 6 new detailed personas with real contractor texture — `hard_head` (stubborn third-gen plumber), `yapper` (HVAC, rapid-fire questions), `evasive` (roofer, vague answers), `burned_before` (landscaper screwed by past agencies), `has_marketing_guy` (remodeler with freelancer Steve), `super_successful` ($7M commercial epoxy). Each has baked-in `expectations:` matching its failure mode.
- **`simulation-test` skill** at `.claude/skills/simulation-test/SKILL.md`: invoked by saying "simulation test - <description>", picks one of 12 personas by behavior, runs sim + judge, optionally auto-iterates. Skips oneshot copies when persona is used AS-IS. Stage-scoped tests run in ~3-5s.
- **Plain-English judge** (`scripts/judge-call.py`): reads transcript + persona's `expectations:`, asks Claude to grade pass/fail with one-sentence reasoning. No infrastructure overhead.
- **LLM-vs-LLM simulator** (`scripts/sim-call.py`): runs Jon (using local prompt file) vs prospect persona over text. Mocks the 3 Vapi tools deterministically. v2 fixed two CRITICAL bugs from v1 (history-loss between Jon turns + text drop across tool loop iterations) — also resolved Jon's tool over-calling as a side effect (every persona now shows perfect 1/1/1 tool counts).
- **XML prompt restructure**: rewrote `inbound_texts.py` into `<persona>`, `<context>`, `<delivery>`, `<output_format>`, `<forbidden_language>`, `<flow>` (10 gated steps), `<objections>` (4 entries), `<faqs>` (empty placeholder), `<out>` (fallback line), `<capturing_data>`. Step 1 split into 3 gated steps (acknowledge / get_name / get_reason) so the LLM can't skip the "How can I help you?" branch. Verified: 10/10 runs of `just_says_name` correctly hit the gate.
- Pacing cues section added to prompt — explicit `...` (short pause) and `—` (long pause) rules; previously these were forbidden output characters.
- `<out>` fallback verified working under hostile-skeptic stress test (Frank asked "what does it cost?", Jon delivered the fallback line verbatim).
- `docs/sales-framework-handoff.md` written — bundles business context, voice rules, current `<objections>`/`<faqs>` state, and verbatim prompt — for a Hormozi-style sales agent to build the objection + FAQ libraries.

**Open issues / what's not working:**
- Phone-side ~600ms latency (BYO Twilio SIP trunk import deferred).
- Sonnet 4.6 occasional 6s+ first-token spikes (one prior outlier; not systemic).
- Endpointing thrashes mid-utterance (cosmetic, not user-visible).
- Orphan `update_prospect_info` tool record (id `278ff15a`) still in Vapi.
- 5 of 6 call types still not built (per `docs/call-types.md`).
- `docs/sales-framework-handoff.md` has stale "Wah-seem" in its verbatim-prompt block (snapshot was pre-cleanup); regenerate before next handoff.
- `<faqs>` block in the live prompt is empty — populating it is the Hormozi handoff payload.

**What to test next:**
1. **Live-call test** of the restructured prompt + Waseem cleanup. Verify TTS pronounces "Waseem" naturally without the phonetic hack.
2. **Run the 6 new personas through the sim** (`hard_head`, `yapper`, `evasive`, `burned_before`, `has_marketing_guy`, `super_successful`) — none exercised yet against the v2 prompt. Each has expectations baked in; will surface real prompt gaps Hormozi work should address.
3. **Hormozi-agent handoff**: refresh `docs/sales-framework-handoff.md` (Wah-seem stale), feed it, get back objection + FAQ libraries, integrate.

**Files touched since checkpoint #3:**
- `src/agent/prompts/inbound_texts.py` — XML restructure + Waseem cleanup
- `scripts/sim-call.py` (new), `scripts/judge-call.py` (new)
- `tests/personas/*.yaml` — `just_says_name` updated; 6 new (`hard_head`, `yapper`, `evasive`, `burned_before`, `has_marketing_guy`, `super_successful`)
- `tests/personas/_oneshot/curious-mentions-driving.yaml`
- `tests/requirements.txt` (new), `.venv-tests/` (new venv)
- `tests/recordings/` — live call audio + transcript saved
- `.claude/skills/simulation-test/SKILL.md` (new), `.claude/skills/prompt-review/SKILL.md` (refined)
- `docs/sales-framework-handoff.md` (new), `docs/system-summary.md` (status header updated)

**Staleness sweep:**
- `caddy`, `/opt/voicsito/webhooks/`, `webhooks.voicsito.cloud7agency.com` — only historical hits in `docs/checkpoints.md` (OK).
- port 8000: ✓ none.
- `Wah-seem` orphans: `__pycache__` bytecode (regenerates), `docs/sales-framework-handoff.md` (flagged above), historical checkpoints.

---

## 2026-05-08 — checkpoint #3 (correct silence API + biz_type adaptation + car ambience + Tailscale)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi`, `server.url=https://webhooks.c7a.io/voicsito/vapi/server-events` (assistant-request webhook for biz_type injection)
- LLM: `anthropic/claude-sonnet-4-6`, temp=0.6, maxTokens=350
- Voice: ElevenLabs `eleven_multilingual_v2`, voice `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone)
  - stability=0.5, similarityBoost=0.9, style=0, speed=1
  - useSpeakerBoost=true, optimizeStreamingLatency=2, autoMode=false
- Transcriber: `deepgram/nova-3`, endpointing=150ms
- startSpeakingPlan: waitSeconds=0.2, smartEndpointingEnabled=false, transcriptionEndpointingPlan: onPunctuation=0.1, onNoPunctuation=0.4, onNumber=0.5
- stopSpeakingPlan: numWords=2, voiceSeconds=0.2, backoffSeconds=1.0
- silenceTimeoutSeconds=60 (safety net), maxDurationSeconds=1800, recordingEnabled=true
- backgroundSound=`https://webhooks.c7a.io/voicsito/static/car-bg-v2.mp3` (band-passed 300-3400Hz, vol 28%, mono 96k)
- voicemailDetection: provider=google, type=audio
- messagePlan: REMOVED (was the broken legacy idle API)
- hooks: 2 hooks both `on=customer.speech.timeout`, `triggerResetMode=onUserSpeech`
  - `idle_nudge_12s`: timeoutSeconds=12, triggerMaxCount=2, do=`say` (3 variants)
  - `graceful_hangup_after_silence`: timeoutSeconds=36, triggerMaxCount=1, do=`say` + `endCall` tool
- Tools (3 webhook + 1 system + 1 orphan):
  - `get_available_slots` (id `4936203d`, async=False, timeout=12s)
  - `mark_for_transfer` (id `dcdba267`, async=False, timeout=12s)
  - `book_video_call` (id `cdc4465d`, async=False, timeout=60s)
  - System: `endCall`
  - Orphan: `update_prospect_info` (id `278ff15a`) — async=true, unattached, cosmetic
- Webhook server: ✓ healthy at `https://webhooks.c7a.io/voicsito/health` (verified via Tailscale; direct ISP path was timing out at checkpoint time — known intermittent issue)

**Recent changes since checkpoint #2 (latest first):**
- Background ambience swapped from Vapi preset `office` to custom `car-bg-v2.mp3`. Processed via ffmpeg with `highpass=300, lowpass=3400, volume=0.28` so road noise sounds like it's bleeding through Jon's phone mic (not a separate hi-fi layer).
- Static-file serving added to nginx — `/voicsito/static/` location alias to `/opt/webhooks/voicsito/static/` with 7-day cache. Deploy script (`scripts/deploy-webhooks.sh`) now scps `audio/*.mp3,*.wav` to that dir on every redeploy.
- Silence handling rewritten using **correct Vapi API**: `assistant.hooks` with `customer.speech.timeout` and `triggerResetMode=onUserSpeech`. Old `messagePlan.idleMessages` removed entirely (verified via call log `019e04c8...` that it had no cancellation mechanism — pushed nudge to say queue 290ms after `pipeline.userSpeechStarted`).
- biz_type adaptation shipped: `lookup_contact_in_crm` extracts the `Biz Type` GHL custom field (texts1: `pCK4An2qtrLGj71bsbM1`, texts2: `iIbJvGHFTa1RR8WWpepz`); new `/vapi/server-events` handler returns `{assistantId, assistantOverrides.variableValues.{biz_type, business}}` so prompt's `{{biz_type}}` is filled before LLM loads. Prompt updated with reasoning instructions to generate 1-2 adjacent specialties (epoxy → "concrete polishing or other flooring", etc.).
- Phone number `server.url` set to assistant-request webhook (kept assistantId as fallback).
- Tailscale installed on VPS (`voicsito-vps` node) AND Mac. SSH now via `ssh voicsito-vps` (alias in `~/.ssh/config` → Tailscale IP `100.105.84.1`). Survives ISP routing flakiness — verified working without VPN. Backup alias `voicsito-vps-direct` for the public IP path.
- Deploy script + ops docs updated to use `voicsito-vps` SSH alias instead of raw IP.
- Voice fidelity push verified working: Multilingual v2 + similarityBoost 0.9 + autoMode false = Jon clone is "really solid" per user testing.
- Idle timer behavior root-cause investigation (live Vapi call event log analysis via `pipeline.*` events) confirmed the messagePlan API bug — fix-via-config attempted, no working flag exists, hooks API is the actual answer.

**Open issues / what's not working:**
- The original phone-side latency ~600ms transport overhead is still present — BYO Twilio Elastic SIP Trunk import not yet done. User has accepted this tradeoff for now.
- Sonnet 4.6 occasional 6s+ first-token latency spikes (one outlier turn observed in call log `019e04c8...`). Not systemic — most turns 1-2s. Could revisit if it becomes consistent.
- Endpointing thrashes turns mid-utterance (creates aborted LLM requests, wastes tokens). Cosmetic cost issue, not user-visible. Could tune `transcriptionEndpointingPlan.onPunctuationSeconds` higher to reduce.
- Orphan `update_prospect_info` tool record (id `278ff15a`) still in Vapi, unattached.
- 5 of 6 call types still not built (Outbound Discovery, Callback After Miss, Demo Confirmation, Rebooking, Inbound Main).
- Stale `idleMessages` mention remains in `docs/system-summary.md` and `scripts/setup-vapi.py` (the latter is intentional — the comment block explains *why* it was abandoned, references the call ID + timestamps that proved the bug).

**What to test next:**
1. **Prompt tuning starts now** (user request: "lets start tweaking the prompt"). Areas the user cares about based on prior signals:
   - Discovery question pacing
   - Top 5 most common objections (verbatim) + framework for the rest
   - Industry-adapted services question with `{{biz_type}}`
2. Verify the new hooks-based silence handling works correctly — pause >12s, verify nudge fires; speak before next nudge, verify timer resets.
3. Verify `{{biz_type}}` adaptation on a real call from a number that exists in texts1 or texts2 with a populated Biz Type field.

**Verification status at checkpoint time:**
- Vapi assistant config: ✓ pulled live via API
- Vapi tools: ✓ pulled live (3 attached + 1 orphan)
- Phone routing: ✓ pulled live, attached + server.url wired
- Webhook server health: ✓ `{"ok":true}` (verified via Tailscale; direct ISP path timed out as it has been off and on this whole session — Tailscale solves this)
- Staleness sweep: caddy / `/opt/voicsito/webhooks/` / `webhooks.voicsito.cloud7agency.com` hits all in `docs/checkpoints.md` (historical context only, fine). Stale `idleMessages` references in system-summary.md updated this checkpoint; setup-vapi.py mention is intentional explanatory comment.

---

## 2026-05-07 — checkpoint #2 (booking flow + voice quality + idle ladder)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi` (still Vapi-bundled, BYO Twilio not yet imported)
- LLM: `anthropic/claude-sonnet-4-6`, temp=0.6, maxTokens=350
  - Note: drift from checkpoint #1 (Haiku 4.5). User test-validated Sonnet 4.6 quality on 2026-05-07; keeping Sonnet as production choice.
- Voice: ElevenLabs `eleven_multilingual_v2`, voice `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone)
  - stability=0.5, similarityBoost=0.90, style=0, speed=1
  - useSpeakerBoost=true, optimizeStreamingLatency=2, autoMode=false, enableSsmlParsing=false
- Transcriber: `deepgram/nova-3`, endpointing=150ms
- startSpeakingPlan:
  - waitSeconds=0.2, smartEndpointingEnabled=false
  - transcriptionEndpointingPlan: onPunctuationSeconds=0.1, onNoPunctuationSeconds=0.4, onNumberSeconds=0.5
- stopSpeakingPlan: numWords=2, voiceSeconds=0.2, backoffSeconds=1.0
- silenceTimeoutSeconds=45, maxDurationSeconds=1800, recordingEnabled=true
- backgroundSound="office"
- voicemailDetection: provider=google, type=audio
- messagePlan: idleMessages=["Hey, you still there brother?", "Hello, you still with me?", "Hey, did I lose you for a sec?"], idleTimeoutSeconds=7, idleMessageMaxSpokenCount=3
- firstMessage: `"Hey, Jon here — who is this calling?"` (still has em-dash; not currently a complaint)
- firstMessageMode: assistant-speaks-first
- Tools (3 webhook + 1 system):
  - `get_available_slots` (id `4936203d`, async=False, timeout=12s) → `webhooks.c7a.io/voicsito/vapi/tool`
  - `mark_for_transfer` (id `dcdba267`, async=False, timeout=12s) → same URL — NEW THIS CHECKPOINT
  - `book_video_call` (id `cdc4465d`, async=False, timeout=60s) → same URL — bumped from 12s → 60s
  - System: `endCall`
  - Orphan: `update_prospect_info` (id `278ff15a`) — async=true, no server, unattached. Cosmetic.
- Webhook server: ✓ healthy at `https://webhooks.c7a.io/voicsito/health` (`{"ok":true,"service":"voicsito-webhooks"}`)

**Recent changes since checkpoint #1 (latest first):**
- Idle nudge ladder shipped: `messagePlan` with 3 variants, fires at 7s silence, max 3 nudges per call, then 45s hard timeout
- Silence timeout 60s → 25s → 30s → 45s during this session (settled on 45s as user-requested)
- Pure-latency tune: `transcriptionEndpointingPlan.onNoPunctuationSeconds` 0.6 → 0.4, Deepgram endpointing 200 → 150, `smartEndpointingEnabled` true → false (had been reverted by setup-vapi.py rerun, re-tuned)
- Voice quality push: `eleven_flash_v2_5` → `eleven_turbo_v2_5` → `eleven_multilingual_v2`; similarityBoost 0.75 → 0.90; `optimizeStreamingLatency` 3 → 2; `autoMode` true → false
- Booking flow rewrite: new `mark_for_transfer` tool + rewritten `_handle_book_video_call` in `src/webhooks/server.py` — flow is now: prospect agrees → tag texts CRM contact `transfer` → existing GHL automation copies to main CRM → `book_video_call` polls main CRM for ~48s → books against main contact_id → tags main contact `voicsito-booked`
- New ghl.py helpers: `add_tag()`, `lookup_in_main_crm_by_phone()`, `wait_for_main_crm_contact()` (4×12s polling)
- nginx `proxy_read_timeout` 30s → 90s (so book_video_call's polling fits)
- Prompt updated with strict booking order: get verbal yes → `mark_for_transfer` → ask email → confirm → `book_video_call`
- Setup-vapi.py: removed `update_prospect_info` from TOOL_DEFINITIONS, added `mark_for_transfer`, threaded per-tool `timeout_seconds` through `make_tool_payload`

**Open issues / what's not working:**
- LLM model regression on `setup-vapi.py` rerun: setup-vapi.py still has `claude-sonnet-4-6` baked into `build_assistant_body()`. If we ever want Haiku 4.5 back for latency, we need to update that literal too. (Live state and setup-vapi.py now agree on Sonnet 4.6.)
- Phone-side latency (~600ms transport overhead) still present — only fix is BYO Twilio Elastic SIP Trunk import (not done yet); user has accepted this for now and is focused on product/prompt work
- `firstMessage` still has em-dash ("Hey, Jon here — who is this calling?"). Not currently a complaint, leaving alone.
- Orphan `update_prospect_info` Vapi tool record still exists (id `278ff15a`). Cosmetic.
- 5 of 6 call types still not built: Outbound Discovery, Callback After Miss, Demo Confirmation, Rebooking, Inbound Main

**What to test next:**
1. End-to-end booking: place a call, agree to a slot, give email — verify (a) `mark_for_transfer` fires the moment of verbal yes, (b) main-CRM contact appears within ~48s, (c) appointment lands on Jon's video calendar, (d) `voicsito-booked` tag lands on the main-CRM contact
2. If main-CRM contact takes longer than 48s to appear, the book_video_call returns a "try again in 30 seconds" message — confirm that error message lands cleanly in the conversation (and not a hard fail)
3. Verify idle ladder: stop talking mid-call, confirm a nudge fires at ~7s, talk again, go silent again — confirm a 2nd different nudge fires at ~7s
4. Voice quality on phone — should now sound noticeably closer to real Jon (Multilingual v2 + similarityBoost 0.90)

**Verification status at checkpoint time:**
- Vapi assistant config: ✓ pulled live via API
- Vapi tools: ✓ pulled live (3 attached + 1 orphan)
- Phone routing: ✓ pulled live, attached correctly
- Webhook server health: ✓ `{"ok":true}` from `https://webhooks.c7a.io/voicsito/health`
- Staleness sweep: caddy/`/opt/voicsito/webhooks/`/webhooks.voicsito.cloud7agency.com hits all in `docs/checkpoints.md` (historical context only); `docs/*` Haiku 4.5 mentions are now outdated (live state is Sonnet 4.6 — this checkpoint is the new truth)

---

## 2026-05-06 — checkpoint #1 (baseline post-migration)

**Live state:**
- Vapi assistant: `261bf1a0-3527-4fcb-956c-b6e227d0bae1` (`Voicsito - Type 2 Inbound (Jon)`)
- Phone: `+1555010011` — provider=`vapi` (Vapi-bundled, NOT BYO Twilio)
- LLM: `anthropic/claude-haiku-4-5-20251001`, temp=0.6, maxTokens=220
- Voice: ElevenLabs `eleven_flash_v2_5`, voice `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone, swapped 2026-05-07 from community library voice `ljX1ZrXuDIIRVcmiVSyR`)
  - stability=0.5, similarityBoost=0.75, style=0.0, speed=1.0
  - useSpeakerBoost=true, optimizeStreamingLatency=4, autoMode=false, enableSsmlParsing=false
- Transcriber: `deepgram/nova-3`, endpointing=150ms
- startSpeakingPlan:
  - waitSeconds=0.2, smartEndpointingEnabled=false
  - transcriptionEndpointingPlan: onPunctuationSeconds=0.05, onNoPunctuationSeconds=0.6, onNumberSeconds=0.5
- stopSpeakingPlan: numWords=2, voiceSeconds=0.3, backoffSeconds=1.0
- silenceTimeoutSeconds=60, maxDurationSeconds=1800, recordingEnabled=true
- backgroundSound="off"
- voicemailDetection: provider=google, type=audio
- firstMessage: `"Hey Jon here, who is this calling?"`
- firstMessageMode: assistant-speaks-first
- Tools: 2 webhook (`get_available_slots`, `book_video_call` — both blocking, 12s timeout) + 1 system (`endCall`)
- Webhook server: `https://webhooks.c7a.io/voicsito/*` — UNVERIFIED at checkpoint time (VPS SSH/HTTPS timed out from auditor's network — known intermittent ISP routing issue, system was healthy 30 min before)

**Recent changes since project start (latest first):**
- Cut transcriptionEndpointingPlan.onNoPunctuationSeconds 1.5s → 0.6s (the hidden 1.5s default was the biggest single delay). startSpeakingPlan.waitSeconds 0.3 → 0.2. optimizeStreamingLatency 3 → 4.
- LLM swapped Sonnet 4.6 → Haiku 4.5 for lower TTFT
- Removed `update_prospect_info` from the assistant's toolIds (was causing tool_use+text → tool_result → repeat-response loop on Anthropic). Tool record still exists in Vapi but is unattached. Data capture now happens via `book_video_call` at end of call.
- Stripped all em-dashes and ellipses from system prompt + firstMessage + voicemailMessage (ElevenLabs renders them as literal pauses → "stop-restart" feel)
- Disabled `voice.autoMode` (documented Flash v2.5 mid-word-split bug — livekit/agents#3433, pipecat#981)
- Killed background ambience (was "office", now "off") per user preference
- Smart endpointing sigmoid removed (was adding 1-2s mid-confidence)
- stopSpeakingPlan.voiceSeconds 0.2 → 0.3 (less self-interruption from breath)
- Added hard prompt rule against asterisks/em-dashes/markdown/numbered lists/parenthetical pronunciations
- "Wasim" written as "Wah-seem" everywhere so TTS pronounces correctly (Flash v2.5 doesn't support `<phoneme>` SSML)
- Webhook server deployed at `webhooks.c7a.io/voicsito/` (nginx + Let's Encrypt on Hostinger VPS)
- LiveKit deployment fully decommissioned (voicsito-agent.service stopped + disabled, src/agent/agent.py and related scripts/audio deleted)
- Migration from LiveKit Agents → Vapi completed 2026-05-05

**Open issues / what's not working:**
- Phone calls feel slower than Vapi dashboard test calls — confirmed by user. Diagnosis: phone path has ~600ms+ transport overhead vs WebRTC's ~100ms. Endpointing fix above mitigates partially. Full fix requires importing a Twilio number with Elastic SIP Trunk pinned to Ashburn edge + IP-allowlisted to Vapi (`44.229.228.186`, `44.238.177.138`) — user has not done this yet.
- Anthropic key not added to Vapi credential store (Vapi uses bundled Anthropic access). Working but billed at platform rate.
- ElevenLabs PVC verification failed earlier in May; user contacted support, stalled. IVC slice (`recordings/jon-3min.mp3`) is uploaded-ready but not yet attached to the Vapi assistant. Update 2026-05-07: Jon's PVC clone `LJ3VmFT7KIfEw9jSLImM` is now live on the assistant, replacing the community library voice `ljX1ZrXuDIIRVcmiVSyR`.
- The `update_prospect_info` tool record still exists in Vapi (id `278ff15a-4c7f-47cf-854f-86c132290667`) — orphaned, not attached to any assistant. Cosmetic; can be deleted via `DELETE /tool/<id>`.

**What to test next:**
1. Place a real test call to `+1555010011` — verify the endpointing fix made phone responses snappier
2. If still sluggish on phone → import a Twilio number with Elastic SIP Trunk (see `docs/vapi-migration.md`)
3. If quality looks good, retry ElevenLabs PVC verification once support unblocks; then swap voice ID to the Jon clone and re-checkpoint
4. Optional: clean up the orphan `update_prospect_info` tool

**Verification status at checkpoint time:**
- Vapi assistant config: ✓ pulled live via API
- Vapi tools: ✓ pulled live
- Phone routing: ✓ pulled live, attached correctly
- Webhook server health: ✗ unverified (VPS unreachable from auditor's network — known intermittent ISP routing issue)
- Staleness sweep: ✓ run, all clean (caddy=0, /opt/voicsito/webhooks/=0, port 8000=0, webhooks.voicsito.cloud7agency.com=0)
