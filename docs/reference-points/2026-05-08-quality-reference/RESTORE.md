# Reference Point — 2026-05-08 Quality Reference

**Saved at:** 2026-05-08 evening, immediately after the user confirmed call quality was "perfect."

**What this captures:** the exact state Voicsito was in when the call quality was great. If a future change regresses quality, the user says "go back to reference point" and you restore from this directory.

## Snapshot contents

| File | What |
|---|---|
| `assistant.json` | Full Vapi assistant config (`/assistant/261bf1a0-...`) — model, voice, hooks, speaking plans, all of it |
| `phone.json` | Vapi phone number config (`/phone-number/cb93e61d-...`) — assistantId, server.url |
| `tools.json` | All Vapi tools (`/tool`) — function defs, server URLs, timeouts |
| `inbound_texts.py` | The exact system prompt at this moment |
| `server.py` | Webhook server source code |
| `ghl.py` | GHL API client source code |

## Critical settings at this reference point

- **LLM:** anthropic `claude-sonnet-4-6`, temp=0.6, maxTokens=350, prompt **20,740 chars / ~5,200 tokens**
- **Voice:** ElevenLabs `eleven_multilingual_v2`, voiceId `LJ3VmFT7KIfEw9jSLImM` (Jon PVC clone)
  - stability=0.40, similarityBoost=0.75, style=0, speed=0.95
  - useSpeakerBoost=true, optimizeStreamingLatency=2, autoMode=false, enableSsmlParsing=false
  - **chunkPlan: null (using Vapi defaults — DO NOT touch)**
- **Phone:** `+1555010011` (Vapi-bundled), `assistantId=None` (dynamic via `assistant-request`)
- **Webhook:** `https://webhooks.c7a.io/voicsito/vapi/server-events`
- **GHL polling:** `wait_for_main_crm_contact` = 5 attempts × 3s
- **GHL update:** `update_prospect_fields` skips GET when business is provided

## Restore procedure

If the user says "go back to reference point":

1. **Vapi assistant** — PATCH `/assistant/261bf1a0-3527-4fcb-956c-b6e227d0bae1` with the full `assistant.json` body. Easiest: read the saved JSON, send as PATCH body.

   ```python
   import json, urllib.request
   key = [l for l in open('config/.env').read().splitlines() if l.startswith('VAPI_API_KEY=')][0].split('=',1)[1]
   H = {"Authorization": f"Bearer {key}", "User-Agent": "curl/8.0", "Content-Type": "application/json"}
   asst = json.load(open('docs/reference-points/2026-05-08-quality-reference/assistant.json'))
   # Strip read-only fields
   for k in ['id','orgId','createdAt','updatedAt','isServerUrlSecretSet']:
       asst.pop(k, None)
   req = urllib.request.Request(
       "https://api.vapi.ai/assistant/261bf1a0-3527-4fcb-956c-b6e227d0bae1",
       data=json.dumps(asst).encode(), headers=H, method="PATCH")
   urllib.request.urlopen(req)
   ```

2. **Vapi phone** — PATCH `/phone-number/cb93e61d-d222-42c9-8fb2-74500dc13234` with `phone.json` (strip read-only fields the same way).

3. **Files** — copy back:
   ```bash
   cp docs/reference-points/2026-05-08-quality-reference/inbound_texts.py src/agent/prompts/
   cp docs/reference-points/2026-05-08-quality-reference/server.py src/webhooks/
   cp docs/reference-points/2026-05-08-quality-reference/ghl.py src/agent/
   # Post-2026-05-09 timezone-resolver work added a new module. Restoring the
   # snapshot's server.py removes the import, so the tz_resolver file becomes
   # dead weight — delete it on rollback to keep the tree clean.
   rm -f src/webhooks/tz_resolver.py
   ```

4. **Webhook deploy** — run `bash scripts/deploy-webhooks.sh` to push the restored code to the VPS.

5. **Verify** — pull the live state and diff against the snapshot. Each of the critical settings above should match.

## What this reference point does NOT capture

- Vapi credentials, secrets, or API keys (those are in `.env`, never snapshot)
- Background mp3 audio assets on VPS (`audio/car-bg-v2.mp3` — stable, no need to capture)
- GHL CRM state (live data, not config)
- Past call recordings or transcripts in `tests/recordings/` (separate per-call artifacts)

## When NOT to restore

- If only the prompt regressed but voice quality is still good → just restore `inbound_texts.py` + push to Vapi.
- If only voice settings regressed → just PATCH the `voice` block back from `assistant.json`.
- Full restore is only when multiple things have drifted and the user wants the whole snapshot.
