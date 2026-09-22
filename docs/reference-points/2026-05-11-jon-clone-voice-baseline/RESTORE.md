# Reference Point — 2026-05-11 Jon Clone Voice Baseline

Snapshot of all 4 Vapi assistants + Squad config taken right after cutting over to the new
Jon clone voice (`F2BOg7Ry3zTPV6Zd62Cd`), BEFORE any stability / similarityBoost / style
tuning rounds. Restore this if upcoming voice-setting experiments make things worse.

## Voice settings at snapshot time (identical across all 4 assistants)

| Setting | Value |
|---|---|
| provider | `11labs` |
| voiceId | `F2BOg7Ry3zTPV6Zd62Cd` (Jon New — PVC fine-tuned on May-11 batch) |
| model | `eleven_multilingual_v2` |
| speed | `1.1` |
| stability | `0.4` |
| similarityBoost | `0.75` |
| style | `0` |
| useSpeakerBoost | `true` |

## Assistant IDs

| Role | ID |
|---|---|
| Monolithic (Type 2 Inbound — fallback) | `261bf1a0-3527-4fcb-956c-b6e227d0bae1` |
| Squad Stage 1 — Inbound Texts | `46e47e95-674a-4624-9029-69bb39c4daf0` |
| Squad Stage 2 — Qualify | `7b2666e0-154d-4c85-82f0-07b4c51ad264` |
| Squad Stage 3 — Book | `7600c2b6-d669-4f2e-8387-e371b0c193d4` |

## Squad

- Squad ID: `bea22dc5-24eb-42ed-ba81-f52cf1986ebf`
- Name: `Voicsito - Squad - Inbound Texts`
- Members: Stage 1 → Stage 2 → Stage 3 (handoff tools)

## Files in this snapshot

- `assistant-261bf1a0.json` — monolithic assistant full config
- `assistant-46e47e95.json` — Stage 1 full config
- `assistant-7b2666e0.json` — Stage 2 full config
- `assistant-7600c2b6.json` — Stage 3 full config
- `squads.json` — squad list at snapshot time
- `squad-detail.json` — squad members + transition tools
- `phone-numbers.json` — phone-number → squad/assistant routing

## Restore procedure

To revert any assistant's voice block to this baseline:

```bash
KEY=$(grep '^VAPI_API_KEY=' config/.env | cut -d= -f2)
ID=261bf1a0-3527-4fcb-956c-b6e227d0bae1   # or whichever assistant
VOICE_JSON=$(python3 -c "import json; print(json.dumps(json.load(open('docs/reference-points/2026-05-11-jon-clone-voice-baseline/assistant-${ID:0:8}.json'))['voice']))")
curl -s -X PATCH -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "{\"voice\":$VOICE_JSON}" "https://api.vapi.ai/assistant/$ID" | python3 -m json.tool | head -30
```

Repeat for all 4 assistant IDs to fully restore the voice baseline.

## Tool configs included in this snapshot

- `tool-4936203d.json` — `get_available_slots` (server.url **fixed** 2026-05-12 02:48 UTC; was missing entirely → tool returned "No result returned" on call `019e1a0a-1acd-...` and model hallucinated 2024 dates → booking blew up → prospect rage-quit)
- `tool-cdc4465d.json` — `book_video_call`
- `tool-278ff15a.json` — `update_prospect_info`
- `tool-dcdba267.json` — `mark_for_transfer`

If a future Vapi UI edit or script run wipes the `server` field again, re-PATCH with:

```bash
KEY=$(grep '^VAPI_API_KEY=' config/.env | cut -d= -f2)
curl -s -X PATCH -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"server":{"url":"https://webhooks.c7a.io/voicsito/vapi/tool","timeoutSeconds":25}}' \
  "https://api.vapi.ai/tool/4936203d-b9d6-4dc9-a46a-609fded29abb"
```

End-to-end webhook verification (no Vapi roundtrip, fires GHL directly):

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"message":{"type":"tool-calls","call":{"id":"manual-test"},"toolCallList":[{"id":"tc","type":"function","function":{"name":"get_available_slots","arguments":"{\"days_ahead\":7}"}}]}}' \
  "https://webhooks.c7a.io/voicsito/vapi/tool" | python3 -m json.tool
```

Healthy response is `{"results":[{"toolCallId":"tc","result":"Available slots ...[iso:2026-...]..."}]}` within ~2s.

## Known state at snapshot

- 3-stage Vapi Squad is live and routed via webhook (`VAPI_SQUAD_ID` env var on VPS).
- `audio/firstmessage-opener.mp3` was regenerated with this voiceId — 1.5s silence + "Hey, Jon here — who is this calling?" — uploaded to `https://webhooks.c7a.io/voicsito/static/firstmessage-opener.mp3`.
- `recordings/jon-clean-may-11.flac` (32.7 min net voice, -20.8 LUFS) is the source material for the PVC behind `F2BOg7Ry3zTPV6Zd62Cd`.
- Background ambience: `car-bg-v3.mp3` (20 min mono, -31 LUFS, 150–4500 Hz bandpass) running as `backgroundSound`.
- `NOT YET VERIFIED` on actual call quality with this voice — snapshot taken BEFORE first real test call.
