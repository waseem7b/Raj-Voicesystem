# Open Issues — surfaced automatically at session start

Known defects and unfinished work, deliberately deferred. A SessionStart hook
reads this file into context every session so these do not get forgotten.

**When one is fixed, delete its entry.** An empty list means nothing outstanding.

Last reviewed: 2026-08-09

---

## P1 — silently breaks production

### 1. Confirm calendar points at a test calendar

`src/agent/ghl.py:44`

```python
CONFIRM_CALENDAR_ID = "LnHtEHrzCMu8LgcTN0c5"   # "Z - DO NOT USE - Discovery Call"
```

`_select_upcoming_video_appointment` filters appointments on this id, so a real
demo booked on the Video Call calendar (`ULFPF484I2XcTfSR2uIC`) is never found —
`get_prospect_appointment` returns `appointment_found=false; reason=no_appointment`.
Every confirmation call fails to find its appointment.

The comment above it says go-live requires setting this to
`VIDEO_CALL_CALENDAR_ID`. That never happened.

**Fix:** flip it, then verify against a real booked demo before trusting it.

---

## P2 — security

### 2. Vapi webhook signature verification fails open

`src/webhooks/server.py:157-158`

When `VAPI_WEBHOOK_SECRET` is unset, `_verify_signature` returns `True` — every
request is accepted. The variable is **absent from local `config/.env`**, and
the VPS copy is never overwritten by deploys, so it must be checked there
directly.

Anyone who knows the URL can drive the GHL-mutating tools
(`mark_locked_in`, `reschedule_video_call`).

**Fix:** require the secret — fail closed. Set it in `config/.env` and on the
VPS at `/opt/webhooks/voicsito/.env`, then re-run the push script so tools carry
`server.secret`. The 2026-08-03 design already mandates this for the new service.

---

## P3 — correctness, low blast radius

### 3. Blooio group listing ignores pagination

`ashley/src/blooio/client.py` — `list_groups()`

v4 returns `{"data": [...], "has_more": bool, "next_cursor": str|null}` and we
drop the last two. Fine at 3 groups; once the org passes one page (~50),
`find_group_by_name` will silently miss newer groups and runs will report
`group_not_found` for groups that exist.

**Fix:** follow `next_cursor` until `has_more` is false.

### 4. Run/payload JSONL files grow unbounded

`ashley/src/webhook/server.py` — `_append`

`/opt/webhooks/ashley/state/{runs,payloads}.jsonl` are append-only. Only the
in-memory view is capped (50). Payload entries hold up to 4 KB of raw body each.

**Fix:** rotate or truncate on boot.

---

## Unbuilt — specified, deliberately not started

These are not bugs. They are the scope of the dashboard-agent handoff
(`ashley/HANDOFF.md` §6) and are listed so their absence is never mistaken for
an oversight.

- **No outbound Vapi call origination.** No `POST /call` exists anywhere.
- **No outcome capture.** `POST /voicsito/vapi/outbound/events` acknowledges and
  does nothing; `confirm_outcome` is written to a cache nothing reads. No GHL tag
  is applied after a confirmation call.
- **No per-call guards** — idempotency on `(contactId, apptId)`, business-hours
  window, skip-if-under-15-minutes, skip-if-already-tagged, no-answer retry.
- **No inbound Blooio webhook**, so prospect replies in iMessage reach nothing.
  Must be registered on v4 — v2's create endpoint returns 410.

## Planned rework — do not "fix", it is being replaced

- **The Vapi confirmation squad is being rebuilt.** Collapsing three stages
  (router → confirm → rebook) into a single assistant, with a different voice
  (not Jon). Two variants — day-before confirmation and day-of reminder —
  differing only in the opener, possibly one assistant that adapts by day/time.
  Stage 0 exists only to transcribe a spoken routing code from the old GHL-bridge
  design and dies with it.

---

## Stale documentation

- `docs/checkpoints.md` — last entry 2026-05-18, predating the entire
  confirmation build, the Ashley iMessage system, and the Blooio v4 migration.
  Run the `checkpoint` skill to refresh.
