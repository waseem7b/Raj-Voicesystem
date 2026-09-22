# Outbound Calling Architecture — Design

**Date:** 2026-05-18
**Status:** Spec only. Not ready to build yet — captured now so we don't lose the design.
**Scope:** How Voicsito does outbound calls (Type 1 scheduled discovery, Type 4 demo confirmation, Type 5 rebooking) without porting numbers out of GHL.

---

## The constraint that drives everything

GHL LC Phone numbers live in **GHL's** Twilio account. We don't have Twilio API credentials for them. That means **Vapi cannot originate a call that presents an LC Phone number as caller-ID** — Vapi can only originate from numbers it can authenticate against at the carrier.

So Vapi cannot dial outbound on our behalf. GHL must originate every outbound call, and Vapi joins on the bridged leg after the prospect answers.

Per CLAUDE.md hard constraints: no porting, no BYOT. Verified 2026-05-18: numbers are GHL LC Phone native.

---

## Architecture: GHL originates, Vapi joins via call forwarding

### Per-call flow

```
1. Trigger fires in GHL (appointment time, no-show state, etc.)
       ↓
2. GHL workflow enqueues a job in OUR queue
   (workflow does NOT dial directly — it just POSTs to our webhook)
       ↓
3. Our dispatcher picks up the job when:
   - scheduled_at <= now
   - a pool number is free
   - priority order honored
       ↓
4. Dispatcher claims a free LC number from the pool, marks it busy
       ↓
5. Dispatcher hits a GHL "inbound webhook trigger" workflow that
   does the actual dial. Workflow dials prospect FROM the claimed
   LC number.
       ↓
6. Prospect answers
       ↓
7. GHL forwards/bridges the answered call to Vapi via the LC
   number's `forwardingNumber` field (pre-configured per pool entry)
       ↓
8. Vapi receives the bridged leg, fires `assistant-request` to our
   webhook with FROM = the LC number (NOT the prospect's number)
       ↓
9. Our webhook resolves:
   - LC number → which pool entry is busy → which job is in flight
   - Job → contact_id, call_type
   - Look up CRM data for the contact
   - Return { squadId, variableValues: { call_type, biz_type, ... } }
       ↓
10. Vapi loads the outbound Squad with the right Stage 1 opener
    based on call_type variable
       ↓
11. Stage 2 (Qualify) and Stage 3 (Book) reuse the existing
    inbound Squad's Stage 2 + Stage 3 unchanged
       ↓
12. Call ends → Vapi end-of-call-report → our webhook releases
    the pool number, marks job completed
```

### Why we control the queue, not GHL

GHL workflows have no concept of "wait for a free line." If 100 rebookings hit at 9am, GHL fires 100 calls at 9am and 90 of them fail because we only have ~10 lines. We insert our queue between "the event happens" and "GHL actually dials" so we never overwhelm the pool.

GHL is still the originator. We're just the brain deciding when GHL dials.

---

## Phone number setup

### Pool of AI-only LC Phone numbers

- **Use existing 7-10 LC Phone numbers** that we already have available in GHL. They become AI-dedicated.
- **Each pool number is permanently forwarded to its own paired Vapi bridge number.** One-time setup via GHL API — set `forwardingNumber` per LC number.
- **No personal use of pool numbers.** They are AI-only forever. Mixing personal calls is what creates the "random callback hits AI" failure mode.

### Pre-call SMS notification

Send an SMS to every prospect ~10 minutes before the AI calls them:

> "Hey [name], Jon's gonna give you a buzz at [time] from ([area]) [number], look out for it."

This handles the caller-ID mismatch concern. Prospect knows the number to expect; doesn't matter that it's not the same number they were texted from in the cold pipeline.

### Vapi bridge numbers

- One Vapi inbound bridge number per LC pool entry, paired 1:1.
- Each bridge number routes inbound to the **outbound Squad** (different from the inbound Type 2 Squad).
- The existing `+1555010011` inbound Type 2 number stays untouched — it's a separate routing path.

---

## The queue

### Schema

```sql
CREATE TABLE outbound_call_queue (
  id              BIGSERIAL PRIMARY KEY,
  contact_id      TEXT NOT NULL,        -- GHL contact ID
  call_type       TEXT NOT NULL,        -- 'type1', 'type4', 'type5'
  scheduled_at    TIMESTAMPTZ NOT NULL, -- earliest time to dial
  priority        INT  NOT NULL,        -- higher fires first
  status          TEXT NOT NULL,        -- pending|dialing|completed|failed
  attempts        INT  NOT NULL DEFAULT 0,
  last_attempt_at TIMESTAMPTZ,
  claimed_lc      TEXT,                 -- which pool LC number, when dialing
  vapi_call_id    TEXT,                 -- set after Vapi assistant-request fires
  failure_reason  TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_queue_dispatch
  ON outbound_call_queue (status, scheduled_at, priority DESC);
```

### Pool tracking

```sql
CREATE TABLE outbound_pool (
  lc_number       TEXT PRIMARY KEY,     -- E.164 GHL LC Phone number
  vapi_bridge     TEXT NOT NULL,        -- paired Vapi inbound number
  status          TEXT NOT NULL,        -- 'free' or 'busy'
  busy_since      TIMESTAMPTZ,
  current_job_id  BIGINT REFERENCES outbound_call_queue(id),
  notes           TEXT
);
```

Stale safety: a background job force-frees any pool entry where `busy_since < now - 15min` and pushes the in-flight queue job to `failed (timeout)` for review.

### Priorities

| Call type | Priority | Rationale |
|---|---|---|
| Type 4 — Demo confirmation | 100 | Tied to a meeting time. Confirmation after the meeting is worthless. |
| Type 1 — Scheduled discovery | 90 | We promised a specific time. |
| Type 5 — Rebooking | 50 | Flexible — can dial anytime in business hours. |

Dispatcher sort:
```sql
SELECT * FROM outbound_call_queue
WHERE status = 'pending' AND scheduled_at <= NOW()
ORDER BY priority DESC, scheduled_at ASC
LIMIT 1;
```

### Stagger for Type 1 batches

When multiple Type 1 jobs are enqueued for the same minute (e.g., 5 prospects all said "yeah, 9am works"), the enqueue endpoint auto-offsets:

- First job: `scheduled_at = 09:00:00`
- Second: `09:00:30`
- Third: `09:01:00`
- Fourth: `09:01:30`
- Etc.

This is for naturalness — robot dialing 5 prospects at exactly 09:00:00 to the second is a tell.

### What we explicitly do NOT do

- **No expiry field.** If the queue is healthy and priorities are right, confirmations always fire before their meeting. If it's not healthy, monitoring + alerts catch it — we don't bake "give up" into the code.
- **No batching at GHL level.** Every job is one dial.
- **No multi-tenancy.** Single-org system.

---

## Dispatcher

A background loop inside the existing FastAPI webhook server. Lightweight asyncio task; no separate service needed for current volume.

```python
async def dispatcher_loop():
    while True:
        try:
            job = await pick_next_job()       # SELECT + claim atomically
            if job is None:
                await asyncio.sleep(2)
                continue
            pool_entry = await claim_pool_slot()  # atomic update
            if pool_entry is None:
                await release_job(job)         # release back to pending
                await asyncio.sleep(2)
                continue
            await fire_ghl_dial(job, pool_entry)
        except Exception as e:
            log.exception("dispatcher tick failed")
            await asyncio.sleep(5)
```

`fire_ghl_dial` POSTs to a GHL inbound-webhook-triggered workflow with `{contact_id, lc_number_to_use}` in the body. That workflow runs the Call action that dials from the specified LC number.

### Pool release path

Vapi `end-of-call-report` fires → our `/vapi/server-events` handler → look up which pool entry holds this `vapi_call_id` → mark pool entry `free`, mark queue job `completed`.

If end-of-call never arrives (Vapi dropped, edge case), the 15-min stale-safety sweeper releases the pool entry.

---

## Concurrency math

- Pool size N = number of paired LC↔Vapi bridge numbers
- Concurrent outbound calls capped at N
- With our existing 7–10 LC numbers, we get 7–10 concurrent outbound calls
- Inbound (Type 2) uses a separate Vapi number; doesn't compete for pool slots
- Vapi plan-level concurrent-call cap is independent — both inbound and outbound count against it. Monitor; upgrade plan when needed.

For realistic volume (100 rebookings + 50 confirmations + 20 Type 1s in a day with ~30s avg call setup + 5min avg call), 7-10 lines is well above sustained demand.

### What scaling looks like later

- Want more concurrent outbound? Buy more LC Phone numbers + Vapi bridge numbers. Linear.
- Want a real worker pool (multi-process dispatcher) instead of asyncio task? Move dispatcher to a separate systemd service. No data-model change.
- Want global rate limiting? Add per-minute caps in the dispatcher.

---

## Prompts: how outbound differs from inbound

- **Persona, output formatting, anti-robot rules, FAQs, objections — all reused from the inbound stack.** `shared_persona.py`, `stage2_qualify.py`, `stage3_book.py` stay unchanged.
- **New: `stage1_outbound.py`** — handles the opener for all outbound call types via a `{{call_type}}` variable branch.

### Stage 1 outbound opener branches (anchor lines TBD with sales-review)

| call_type | Opener feel |
|---|---|
| `type1` | "Hey [name], Jon — said I'd give you a buzz now, got a second?" |
| `type4` | "Hey [name], it's Jon — calling to confirm our zoom at [time], you still good?" |
| `type5` | "Hey [name], Jon here — wanted to catch you about getting you rebooked since we missed last time." |

(Final wording goes through the prompt-review skill, not this doc.)

After the opener, Stage 1 outbound runs a SHORT version of the interest check, then hands off:
- Type 1 → Stage 2 (Qualify) → Stage 3 (Book)
- Type 4 → Stage 3 directly (already booked; just confirming/rescheduling)
- Type 5 → Stage 3 directly (going for the rebook)

Stage 2 and Stage 3 don't need to know they came from an outbound — handoff payload is the same shape.

---

## Build order (when we get to this)

1. **Pool setup** — buy/dedicate LC numbers, set `forwardingNumber` on each via GHL API, buy paired Vapi bridge numbers, point each at the outbound Squad. One-time.
2. **DB schema** — Postgres or SQLite. Tables: `outbound_call_queue`, `outbound_pool`. Migration script.
3. **Enqueue endpoint** — `POST /outbound/enqueue` taking `{contact_id, call_type, scheduled_at?, priority?}`. GHL workflows hit this.
4. **Pool claim + release endpoints** + atomic UPDATE logic.
5. **Dispatcher loop** — asyncio task in `server.py` (or its own service). Polls queue, claims pool slot, fires GHL dial.
6. **GHL "dial-this-contact-from-this-LC" workflow** — generic workflow triggered by inbound webhook, configured per pool number.
7. **Outbound Squad in Vapi** — clone existing Squad structure, add `stage1_outbound.py`, wire `call_type` variable into `assistant-request` response.
8. **Pre-call SMS** — workflow action sends the "look out for a call from X" SMS at job enqueue (or at scheduled_at - 10min, TBD).
9. **End-of-call hook update** — extend `/vapi/server-events` to release the pool entry + mark job completed when an outbound call ends.
10. **Monitoring** — Telegram alert if any pending job's `scheduled_at` is more than 60s in the past. Alert if pool has zero free slots for more than 5 minutes. Daily summary of completed/failed counts.

---

## Open questions to resolve at build time

- **Which specific LC numbers** go into the AI pool vs stay for SMS/personal use. (User has 7-10 candidates.)
- **DB choice**: SQLite on the VPS (simpler, no new service) vs Postgres (more headroom, real persistence). Lean SQLite to start; migrate to Postgres if/when we add the sales dashboard.
- **Retry policy** for failed calls — busy → retry in 30min? No-answer → retry in 2h then drop to next-day? Decide at build time, not now.
- **Final Stage 1 outbound prompt wording** — comes from prompt-review session, not this spec.
- **Voicemail behavior** for outbound — leave a message? Drop a follow-up SMS instead? Decide before launch.

---

## What this design is NOT

- Not a porting plan. Numbers stay in GHL.
- Not a Twilio BYOT plan. We're not connecting our own Twilio.
- Not a Vapi-originated outbound plan. Vapi only handles the bridged leg.
- Not a "build it now" plan. This is the reference doc for when outbound becomes the next priority.
