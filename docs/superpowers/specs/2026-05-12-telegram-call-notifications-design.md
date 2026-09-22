# Telegram Call Notifications + VPS Recording Archive — Design

**Date:** 2026-05-12
**Status:** Approved (verbal) → ready for implementation plan

## Goal

The Voicsito Type-2 inbound number is now live on Texts CRM 2. Real prospects
will dial it. The operator (Waseem) needs:

1. A **real-time push notification** in Telegram when a call starts.
2. A **summary + recording + transcript push** when the call ends.
3. **All recordings + transcripts saved to the VPS** in a per-call folder so the
   operator can SCP them locally for review at any time.

This must be the simplest, fastest thing that works. No dashboard, no analytics,
no bot commands.

## What's already in place

- FastAPI webhook server at `src/webhooks/server.py` on the VPS.
- Vapi `server-events` endpoint at `/vapi/server-events` already receives
  `status-update`, `end-of-call-report`, `speech-update` — currently
  ack-and-ignore (`server.py:906-907`).
- Vapi end-of-call-report payload includes the recording URL, transcript,
  call metadata, cost, and `analysis.summary` / `analysis.successEvaluation`.
- Telegram bot `@vsc7a_bot` created. Bot token in `config/.env`.
- Group chat **Voiscito Group** (`chat_id=-1000000000`). Chat ID in `config/.env`.

## Architecture

Everything ships inside the existing FastAPI webhook process. No new
process, no new infra, no DB.

```
Vapi server-events POST
  ↓
  /vapi/server-events  (existing endpoint)
    ↓
    if type == 'status-update' AND status flips to 'in-progress':
       → _handle_call_started(message)
          → resolve caller via GHL phone lookup (cached)
          → telegram.send_message("📞 Call started from ...")
    if type == 'end-of-call-report':
       → _handle_end_of_call(message)
          → if duration_sec < 30: log "skipped (under-30s)" and RETURN.
            No Telegram, no VPS folder. Vapi dashboard still has the raw record.
          → mkdir /opt/webhooks/voicsito/live-recordings/<ts>-<call_id_short>/
          → download recordingUrl → recording.mp3
          → write transcript.txt
          → write summary.json
          → telegram.send_message(outcome summary, parse_mode=Markdown)
          → telegram.send_audio(recording.mp3, caption=...)
          → if len(transcript) <= 3500: telegram.send_message(transcript, plain text)
            else: telegram.send_document(transcript.txt)
```

## New module: `src/webhooks/telegram_notify.py`

Thin wrapper around the Telegram Bot HTTP API. Reads `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` from env. Uses `httpx.AsyncClient` (already a webhook dep).

Functions:

```python
async def send_message(text: str, *, parse_mode: str | None = "Markdown") -> bool
async def send_audio(path: Path, *, caption: str | None = None) -> bool
async def send_document(path: Path, *, caption: str | None = None) -> bool
```

Behaviour:

- All three log success / failure and return a boolean. They never raise.
  A failed Telegram push must NEVER break the webhook response to Vapi.
- If `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID` is unset, every function is a
  no-op that logs a warning and returns `False`. This is the disable switch.
- All requests use a 10 s timeout.

## Storage layout

```
/opt/webhooks/voicsito/live-recordings/
  20260512-143022-019e1a0a/
    recording.mp3           # downloaded from Vapi's recordingUrl
    transcript.txt          # plain-text transcript
    summary.json            # {phone, from_crm, duration_sec, outcome,
                            #  success_evaluation, summary_text,
                            #  booked_slot_iso, cost_usd, ended_reason,
                            #  recording_url, call_id, assistant_id}
```

Folder name: `<UTC YYYYMMDD-HHMMSS>-<call_id[:8]>`. Time component is the call
START time (from Vapi's `startedAt`), not now, so chronological sort works
regardless of when the end-of-call event arrives.

If the folder already exists (Vapi resend), skip the download + summary write
and only re-issue the Telegram push if the previous push failed. Tracked by a
sentinel file `pushed.flag` written after a successful Telegram send.

## Telegram message formats

### Call started

Single message, no attachments. Sent the first time `status-update` with
`status == "in-progress"` arrives for a given `call.id` (track via in-memory
set keyed on call_id; idempotent within a single webhook process lifetime).

```
📞 *Call started*
+1 555-0199-1234
Mike — Rock Epoxy (epoxy flooring, San Jose CA)
```

If no CRM hit, the third line is `(no CRM match — cold caller)`.

### Call ended

First a summary message, then audio attachment with a one-line caption, then
transcript (in-line or as document depending on length).

Summary message:

```
✅ *BOOKED* · 4m 22s
+1 555-0199-1234 · Mike — Rock Epoxy
🗓 Wed May 14 · 10am EST
💰 $0.43
🆔 `vt-019e1a0a` (folder on VPS)
```

Outcome icons:

| Vapi signal | Icon | Label |
|---|---|---|
| `analysis.successEvaluation == true` AND booked slot present | ✅ | BOOKED |
| `endedReason == 'assistant-ended-call'` AND `successEvaluation == false` | 🚫 | DISQUALIFIED |
| `endedReason == 'customer-ended-call'` AND `successEvaluation == false` | ⚠️ | NO BOOKING (customer hung up) |
| else | ⚠️ | OTHER (summary follows verbatim) |

(Dropped <30s calls don't reach this table — they're skipped before any
end-of-call message fires. See "Under-30s skip rule" below.)

After the summary, send audio with caption `<phone> · <duration>`. Then the
transcript: as a plain-text message (no code fence — iPhone Telegram wraps
plain text cleanly, code blocks force horizontal scroll) if
`len(transcript) <= 3500`, otherwise as `transcript.txt` document attachment
with caption `transcript`.

### Under-30s skip rule

Calls where `endedAt - startedAt < 30 seconds` are silently skipped at the
end-of-call handler — no Telegram message, no VPS folder, no audio. The
`📞 Call started` message already fired (we don't know duration at that
point). Operator mental model: **start notification with no follow-up =
dropped call under 30 s**. Raw call data still lives in the Vapi dashboard
if forensics are needed.

## Error handling

- Telegram down / token wrong / chat_id wrong: log error, continue. Vapi gets
  200 from the webhook regardless. Operator notices later when reviewing logs.
- Recording download fails: still write transcript + summary, send Telegram
  message without audio attachment but with note `recording download failed —
  retry later via summary.json.recording_url`.
- Transcript missing in Vapi payload: send summary only, log warning.
- Disk full / mkdir fails: log error, continue with Telegram-only push.

## Configuration

`config/.env` (gitignored) gains:

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=-1000000000
LIVE_RECORDINGS_DIR=/opt/webhooks/voicsito/live-recordings   # optional override
```

Same vars get added to `/opt/webhooks/voicsito/.env` on the VPS.

## What's intentionally NOT in scope

- Web dashboard for browsing calls (use SCP + Vapi dashboard).
- Bot commands like `/last`, `/today`, `/search` (could be added later).
- Aggregate analytics / KPI rollups (Vapi dashboard has totals).
- Speaker diarization beyond what Vapi already provides.
- Sales-team alerting / round-robin (single Telegram group is enough).
- Auto-deletion / TTL of old recordings (revisit when disk usage matters).
- Encryption-at-rest of recordings (PII concern — flagged for later).

## Open questions for the user

None — design is small enough that all decisions were captured above.

## Acceptance criteria

1. A real call to the Type-2 number produces a `📞 Call started` Telegram
   message within 5 s of audio connect.
2. When the call ends, the group receives: outcome summary message, audio
   attachment of the recording, and the transcript (inline or attached).
3. `/opt/webhooks/voicsito/live-recordings/<ts>-<call_id_short>/` exists on
   the VPS with `recording.mp3`, `transcript.txt`, `summary.json`.
4. Removing the env vars disables all Telegram pushes without breaking the
   webhook (verified by removing `TELEGRAM_BOT_TOKEN`, restarting service,
   placing a test call → webhook still 200s, no Telegram, no crash).
5. A repeat `end-of-call-report` for the same `call.id` does NOT duplicate
   the audio attachment in Telegram or overwrite the folder contents.
6. A call shorter than 30 s produces a `📞 Call started` message but
   nothing else — no end-of-call Telegram message, no VPS folder.
