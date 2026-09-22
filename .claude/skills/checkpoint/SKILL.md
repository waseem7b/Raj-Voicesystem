---
name: checkpoint
description: Save a Voicsito project state snapshot. Invoke when the user says "checkpoint" — pulls live Vapi config via API, updates docs/system-summary.md to current truth, appends a timestamped entry to docs/checkpoints.md, runs staleness checks, and reports a tight summary. Use whenever the user wants to mark a known-good state or have docs reflect reality before walking away.
---

# checkpoint — Voicsito state snapshot

When the user invokes "checkpoint", do everything below. Be thorough but tight — the user wants to know the system state is captured so they can stop worrying about drift.

## 1. Pull live truth from Vapi

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
VAPI_KEY=$(grep '^VAPI_API_KEY=' "$REPO_ROOT/config/.env" | cut -d= -f2-)
ASSISTANT_ID=261bf1a0-3527-4fcb-956c-b6e227d0bae1
PHONE_ID=cb93e61d-d222-42c9-8fb2-74500dc13234

# Snapshot assistant
curl -s "https://api.vapi.ai/assistant/$ASSISTANT_ID" -H "Authorization: Bearer $VAPI_KEY" > /tmp/vapi_assistant.json

# Tools
curl -s "https://api.vapi.ai/tool" -H "Authorization: Bearer $VAPI_KEY" > /tmp/vapi_tools.json

# Phone
curl -s "https://api.vapi.ai/phone-number/$PHONE_ID" -H "Authorization: Bearer $VAPI_KEY" > /tmp/vapi_phone.json

# Webhook health
curl -fsS https://webhooks.c7a.io/voicsito/health > /tmp/wh_health.json
```

Extract the values that matter (use `python3` or `jq`):
- assistant: `model.provider`, `model.model`, `model.maxTokens`, `model.temperature`, len(`model.messages[0].content`), len(`model.toolIds`), `model.tools` (system tools)
- voice: `voice.provider`, `voice.voiceId`, `voice.model`, `voice.stability`, `voice.similarityBoost`, `voice.style`, `voice.speed`, `voice.useSpeakerBoost`, `voice.optimizeStreamingLatency`, `voice.autoMode`, `voice.enableSsmlParsing`
- transcriber: `transcriber.provider`, `transcriber.model`, `transcriber.endpointing`
- speaking plans: `startSpeakingPlan` (especially `transcriptionEndpointingPlan`), `stopSpeakingPlan`
- timeouts: `silenceTimeoutSeconds`, `maxDurationSeconds`
- voicemail: `voicemailDetection.provider`
- tools list: name, async, server.url, timeoutSeconds for each
- phone: number, provider, assistantId

## 2. Update docs/system-summary.md

`docs/system-summary.md` is the canonical shareable doc. Update these sections to match live truth:
- Status header line (date, latest meaningful change)
- Stack inventory table (especially LLM model, voice settings, transcriber settings)
- Audio pipeline section (voice + speaking plan values)
- Latency budget (re-estimate if any knobs changed)
- File structure (if new scripts/files added)

Use `Edit` not `Write` to preserve sections that didn't change. Keep diffs minimal.

## 3. Append to docs/checkpoints.md

`docs/checkpoints.md` is a reverse-chronological log of every checkpoint. Each entry follows this template:

```markdown
## YYYY-MM-DD HH:MM — checkpoint #N

**Live state:**
- Vapi assistant: `<id>` (`<name>`)
- Phone: `<number>` (provider=`<provider>`)
- LLM: `<provider>/<model>`, temp=`<t>`, maxTokens=`<n>`
- Voice: ElevenLabs `<model>` voice `<voiceId>` (stability=`<s>`, similarity=`<sb>`, style=`<style>`, speed=`<sp>`, useSpeakerBoost=`<usb>`, optimizeStreamingLatency=`<osl>`, autoMode=`<am>`)
- Transcriber: `<provider>` `<model>` endpointing=`<e>`ms
- Speaking plans: waitSeconds=`<w>`, smartEndpointing=`<se>`, transcriptionEndpointingPlan=`<tep>`, stopSpeakingPlan=`<ssp>`
- Tools: N webhook tools + system tools — one line per tool (name, async, url-suffix)
- Webhook server: `<status>` at `https://webhooks.c7a.io/voicsito/health`
- Background sound: `<bs>`
- Voicemail detection: `<vmd>`

**Recent changes since last checkpoint** (latest first, max 10 bullets — pull from session memory):
- short bullet
- short bullet

**Open issues / what's not working:**
- short bullet

**What to test next:**
- short bullet

**Files touched since last checkpoint:**
- list (only if obvious from session)
```

Number checkpoints starting from 1. If checkpoints.md doesn't exist yet, create it with a heading and a "newest first" note, then append entry #1.

## 4. Staleness sweep

Run these greps; report counts at the end of the checkpoint:
```bash
cd "$(git rev-parse --show-toplevel)"
echo "caddy:"            ; grep -ric "caddy" docs/ infra/ src/webhooks/ CLAUDE.md 2>/dev/null | grep -v ":0$" || echo "  ✓ none"
echo "/opt/voicsito/webhooks/:"; grep -rc "/opt/voicsito/webhooks/" docs/ infra/ src/webhooks/ CLAUDE.md 2>/dev/null | grep -v ":0$" || echo "  ✓ none"
echo "webhooks.voicsito.cloud7agency.com:"; grep -rl "webhooks.voicsito.cloud7agency.com" docs/ infra/ src/webhooks/ CLAUDE.md 2>/dev/null || echo "  ✓ none"
echo "port 8000 (webhook):"; grep -rE "(localhost|127\.0\.0\.1):8000" docs/ infra/ src/webhooks/ CLAUDE.md scripts/ 2>/dev/null | grep -v "agent.py" || echo "  ✓ none"
echo "Sonnet 4.6 references in docs (should be Haiku 4.5 if model swap is current):"
grep -rl -i "sonnet-4-6\|sonnet 4\.6" docs/ 2>/dev/null
```

If any of these surface unexpected hits, flag them in the checkpoint output.

## 5. Verify webhook server still healthy

```bash
curl -fsS https://webhooks.c7a.io/voicsito/health 2>&1
```

If it doesn't return `{"ok": true}`, flag as a blocker in the checkpoint output.

## 6. Report

A tight summary back to the user:
- "Checkpoint #N saved" (one line)
- The 5-7 most important live values (model, voice, key tuning knobs)
- Anything in the staleness sweep that surfaced
- Webhook health
- Total files updated

Do NOT lecture. Do NOT enumerate everything verbatim from the JSON dump — only the values that have *changed since the previous checkpoint* or are critical. The full snapshot lives in checkpoints.md; the user can read it there.

## What NOT to do

- Don't run any code-modifying changes. This is a snapshot/sync skill, not a deploy skill.
- Don't push to git or create commits. Just update the local files.
- Don't dispatch Agent subprocesses. This is a fast read-only-ish operation.
- Don't make up values you don't have. If a Vapi call fails, say so and abort the checkpoint.
- Don't skip the staleness sweep. It's the cheapest correctness signal we have.
- Don't write more than ~150 lines into the new checkpoints.md entry.

## Useful context to remember between checkpoints

- The user's biggest pain point has been audio latency on phone calls (vs the dashboard test which is fine). The fix path is endpointing knobs first, Twilio SIP trunk import second.
- Vapi assistant ID is `261bf1a0-3527-4fcb-956c-b6e227d0bae1`, phone ID is `cb93e61d-d222-42c9-8fb2-74500dc13234`, phone number `+1555010011` (Vapi-bundled provider).
- Anthropic Sonnet 4.6 → Haiku 4.5 swap was made for latency. If it's been swapped back, note it in the checkpoint.
- Webhook server is `voicsito-webhooks` systemd service at port 8081, behind `webhooks.c7a.io/voicsito/*` (nginx + Let's Encrypt on Hostinger VPS).
