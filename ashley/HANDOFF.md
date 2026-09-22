# Handoff — Ashley / Demo Confirmation System

Written 2026-08-06 for the engineer or agent picking this up inside the Sales
Takeover Dashboard. Everything here was verified against live systems on the
date written; where something is designed-but-not-built, it says so explicitly.

**Your job, in one sentence:** build the automation that decides *when* a
prospect gets confirmed, fires the iMessage branding webhook and the voice call,
and records what came back.

Two of the three layers already exist and work. The third — call origination and
outcome capture — does not exist at all. That is the gap you are filling.

---

## 1. What this system is

A prospect books a demo in GoHighLevel. Between "booked" and "showed" nothing
used to happen, and no-shows ate the calendar. This system fills that gap with
two touchpoints:

1. **An iMessage group chat** containing the prospect, a closer, and "Ashley
   Miller" (an AI persona). Branded with the prospect's business name and a
   company icon, with Ashley's contact card shared so she appears as a real
   saved contact rather than an unknown number.
2. **An AI voice call** that confirms the demo the day before, and a second
   variant that reminds them the day of.

GoHighLevel remains the system of record. Conversations sync back into the CRM
so humans and other agents can read and reply in one place.

---

## 2. System map

```
                    ┌──────────────────────────────────────┐
                    │  GoHighLevel  (system of record)     │
                    │  Main CRM: G1yFGT1liEZgvT3dYgEx      │
                    └──────────────────────────────────────┘
                       │                              ▲
       appointment     │                              │  tags, notes,
       booked          │                              │  conversation sync
                       ▼                              │
        ┌──────────────────────────┐      ┌───────────────────────────┐
        │ Bloo GHL plugin actions  │      │  YOUR AUTOMATION          │
        │ • check iMessage avail.  │      │  (does not exist yet)     │
        │ • create group           │      │  • schedules touchpoints  │
        │ • send message           │─────▶│  • calls ashley webhook   │
        │ • react to last message  │      │  • originates Vapi calls  │
        └──────────────────────────┘      │  • records outcomes       │
                       │                  └───────────────────────────┘
                       ▼                          │              │
        ┌──────────────────────────┐              │              │
        │  Blooio (iMessage)       │◀─────────────┘              │
        │  org_kOvFBVMIn0xdKZcnn…  │   ashley webhook            │
        │  +1555010018         │   :8082                     │
        └──────────────────────────┘                             ▼
                                                  ┌───────────────────────────┐
                                                  │  Vapi (voice)             │
                                                  │  squad 18d03512-…         │
                                                  │  +1555010017          │
                                                  │  tools → voicsito :8081   │
                                                  └───────────────────────────┘
```

**Layer status:**

| Layer | State |
|---|---|
| iMessage branding (`ashley/`) | **Built, deployed, live-verified end to end** |
| GHL wiring | **Built** (workflow + Bloo actions), one merge field was fixed 2026-08-06 |
| Voice confirmation | **Assistants exist and are deployed; nothing can start a call** |
| Automation / scheduling / outcomes | **Does not exist — your job** |

---

## 3. Layer 1 — the iMessage system (`ashley/`)

Self-contained by design: zero imports from Voicsito code, so this folder can be
lifted wholesale into the dashboard.

### What it does

GHL creates the group chat and sends the first message. It then calls our
webhook, which:

1. Finds the group by the **exact title GHL created it with**
   (`Growth System - <business name>`), waiting up to 4 minutes for it to appear.
2. **Renames** it to that same title — this is what makes the *device* apply the
   title (see gotchas; this is not a no-op even though the string is identical).
3. Uploads the **group icon** (a 📈 on the C7A purple gradient, pre-rendered).
4. Shares **Ashley's contact card** into the chat.

Each step reports independently. A failed icon never costs the card.

### HTTP contract

```
POST https://webhooks.c7a.io/ashley/group/setup
X-Ashley-Secret: <ASHLEY_WEBHOOK_SECRET>
Content-Type: application/json

{ "phone": "+1555010010",
  "business_name": "Cloud 7 Agency",
  "share_card": true }          // optional, defaults true
```

Returns **202** `{"accepted": true, "run_id": "<12 hex>"}` immediately — the
device calls take far longer than GHL will wait, so the work runs in the
background.

| Route | Auth | Purpose |
|---|---|---|
| `GET /ashley/health` | none | liveness |
| `POST /ashley/group/setup` | `X-Ashley-Secret` | the branding run |
| `GET /ashley/runs` | `X-Ashley-Secret` | last 50 run outcomes, newest first |
| `GET /ashley/payloads` | `X-Ashley-Secret` | last 50 raw request bodies |

`business_name` is what matters — it *is* the lookup key. `phone` is required but
only identifies the run in logs. If GHL sends its standard contact payload, the
business name is read from `company_name` automatically, so Custom Data is
optional.

Status codes: 401 bad/missing secret · 422 non-JSON body or no phone ·
500 if `ASHLEY_WEBHOOK_SECRET` is unset server-side. **No status code ever
reflects the Blooio outcome** — read `/ashley/runs` or `journalctl -u
ashley-webhooks`.

A run that cannot find its group returns `error: "group_not_found"` and
**performs zero writes**. That is deliberate: an earlier version fell back to the
newest group holding the same phone number and branded a stale thread.

### Layout

```
ashley/
├── src/blooio/client.py        Blooio v4 HTTP client (17 methods)
├── src/blooio/group_lookup.py  find/poll for a group by exact title
├── src/blooio/group_setup.py   rename → icon → card, per-step reporting
├── src/blooio/group_icon.py    render the icon (build-time only, needs Chrome)
├── src/webhook/server.py       FastAPI app, 4 routes, JSONL persistence
├── src/webhook/fields.py       pull phone/business out of a GHL POST
├── scripts/deploy.sh           7-step deploy to the VPS
├── scripts/smoke.py            live send + delivery-status check
├── scripts/setup_group.py      brand one group by hand
├── assets/group-icon.png       pre-rendered 1024×1024
└── tests/                      50 tests, all offline
```

Run the tests with `pytest ashley/tests` — the repo root's `pyproject.toml`
scopes `testpaths` to `tests/unit`, so a bare `pytest` will not pick them up.

---

## 4. Layer 2 — how GoHighLevel is wired

### The Bloo plugin

Installed on the Main CRM. Its workflow actions are limited to four: **check
iMessage availability, create group, send a message, react to last message.**
Everything else — naming, icons, contact cards — must go through the API, which
is why `ashley/` exists.

### The sync bridge (important)

When the Bloo plugin creates a group, it creates a **GHL contact** representing
that group:

```
email:     grp_<lowercased_group_id>@groups.blooio.com
firstName: <the group name>
source:    iMessage Group
phone:     null
```

A `TYPE_PHONE` conversation hangs off that contact and holds the group's
messages. **That contact is the only thing making the conversation visible in
GHL.** Groups created directly through the Blooio API get no such contact and
are invisible to the CRM — verified.

Consequence: **do not create groups via the API.** Let GHL create them.

Second consequence: Blooio pushes a group's name to GHL **only at creation**.
Renaming later updates the device but not the CRM contact, so we always rename to
the identical title GHL already used.

### Live IDs

| Thing | Value |
|---|---|
| Main CRM location | `G1yFGT1liEZgvT3dYgEx` |
| Video Call calendar | `ULFPF484I2XcTfSR2uIC` |
| Test workflow | `8aea8040-4768-4ec1-b5ad-39db4f75651b` ("Imessage test") |
| Blooio org | `org_kOvFBVMIn0xdKZcnnJGr9` |
| Blooio channel | `ch_019fd3b4-20bc-70ee-9d57-086afb8ce261` |
| Blooio number | `+1555010018` (dedicated) |

### The number-assignment constraint

The Blooio number is assigned to the **GoHighLevel integration**, not to our API
key. That assignment is exclusive and dashboard-only — there is no API to change
it (the endpoint exists at `PUT /dashboard/phone-numbers/{n}/owners/…` but is
Firebase-authenticated).

This is the right setting, because it is what makes GHL create groups and sync
conversations. It costs us three things, all documented in `README.md`:

| resolver | what it covers | status |
|---|---|---|
| org-scoped | chats, groups, messages, contacts, events | **works** |
| number-ownership | contact card **content** (`/me/numbers/*`) | 403 |
| device-passthrough | chat backgrounds, Find My, capability checks | 502/404/409 |

Ashley's card can be **shared** but not **edited** while GHL owns the number. Her
card is already set (name + photo). Changing it means temporarily reassigning the
number in the Blooio dashboard — a one-time manual step, not a per-prospect one.

---

## 5. Layer 3 — the voice confirmation agent

### ⚠️ This layer is being rebuilt — do not build toward its current shape

The owner's plan, as stated 2026-08-06:

- **Collapse to a single assistant.** No Stage 0 / Stage 1 / Stage 2 handoffs.
  It is a confirmation call; it is simple.
- **Two variants, or one that adapts:** a day-before *confirmation* and a
  day-of *reminder*. The scripts differ only in the opening; everything after is
  the same. Possibly one assistant that adjusts its opener by day/time.
- **A different voice.** Today it is Jon (the inbound sales persona) calling on
  behalf of Jon, which is wrong for this use case.
- Rebooking does not need its own stage.

Treat what follows as *current state for orientation*, not as the target.

### What exists today (live on Vapi)

| Resource | ID |
|---|---|
| Squad "Voicsito - Squad - Outbound Confirmation" | `18d03512-77f7-414c-ad0d-85727555ba1c` |
| Stage 0 — Router | `ad606c08-be2e-4f97-aaf1-8a182320515d` |
| Stage 1 — Confirm | `5715972b-3067-4ab6-9aec-d86ce8f33bf4` |
| Stage 2 — Rebook | `8148a45e-1566-4959-8984-f72a974f8288` |
| Outbound number (bound to the squad) | `+1555010017` → `f01ae694-9dfa-4ecc-8d2a-86c434f8400c` |
| Inbound number (different system) | `+1555010011` → `cb93e61d-d222-42c9-8fb2-74500dc13234` |

Deployed by `scripts/push_confirm_squad.py` (idempotent upsert **by name**).
Assistant IDs are generated at push time and not recorded in the repo — always
look them up live via `GET /assistant`.

Prompts live in `src/agent/prompts/`: `confirm_persona.py` (shared),
`stage0_router.py`, `stage1_confirm.py`, `stage2_rebook.py`, registered in
`__init__.py` under `("demo_confirmation", "stage0"|"stage1"|"stage2")`.

### Why Stage 0 exists (and why it dies)

The current design assumed **GHL originates the call**, because GHL's LC phone
numbers live in GHL's own Twilio account and Vapi cannot present them as caller
ID. So GHL dials the prospect, bridges the leg into a Vapi number, and a GHL bot
*speaks* a routing code and the prospect's 10-digit phone number, which Stage 0
transcribes.

The spec itself calls that "the central reliability risk — one mis-transcription
and you confirm the wrong contact's demo."

**The newer design (`docs/superpowers/specs/2026-08-03-demo-confirmation-design.md`)
kills it.** Because the Bloo iMessage now announces "you'll get a call from
(XXX) XXX-XXXX", caller ID no longer has to be an LC number, so Vapi can
originate directly via API with structured variables. Zero ASR risk, no Stage 0.
That document is design-only — nothing from it is built.

### The voice tool webhook (exists and works)

Separate service from `ashley/`:

```
POST https://webhooks.c7a.io/voicsito/vapi/outbound/tool     tool calls
POST https://webhooks.c7a.io/voicsito/vapi/outbound/events   phone events (STUB)
```

Code at `src/webhooks/outbound.py`, mounted from `src/webhooks/server.py:1190`.
Auth accepts either a literal `VAPI_WEBHOOK_SECRET` match or an HMAC-SHA256 of
the raw body.

Vapi sends `{"message": {"type": "tool-calls", "toolCallList": [...], "call": {...}}}`
and expects `{"results": [{"toolCallId": "...", "result": "<string>"}]}`. Handlers
always return HTTP 200, even on error, so Vapi never retries.

Tools available: `get_prospect_appointment` (phone → Main CRM contact + soonest
appointment, seeds a call cache), `mark_locked_in` (tags the contact
`locked-in`), `get_available_slots`, `reschedule_video_call`, `end_call`.

---

## 6. What you need to build

### 6a. Originate the call

**Nothing in this repo can start an outbound call.** There is no
`POST https://api.vapi.ai/call` anywhere. This is the single biggest gap.

The shape you want:

```http
POST https://api.vapi.ai/call
Authorization: Bearer $VAPI_API_KEY
Content-Type: application/json

{
  "assistantId":   "<the rebuilt single confirmation assistant>",
  "phoneNumberId": "f01ae694-9dfa-4ecc-8d2a-86c434f8400c",
  "customer": { "number": "+1XXXXXXXXXX" },
  "assistantOverrides": {
    "variableValues": {
      "first_name":   "...",
      "business":     "...",
      "appt_time":    "...",   // already formatted in the prospect's timezone
      "contact_id":   "..."
    }
  }
}
```

Notes that will save you time:

- **Verify the call API against current Vapi docs before writing it.** Nobody
  here has exercised it; treat the shape above as a starting point.
- **Do not send `squadId` for the current squad.** Stage 0 waits for a bot to
  speak a routing code that will never come on a direct call, and the flow
  stalls. Target a single assistant.
- Pass values as `variableValues` rather than relying on a mid-call lookup — the
  2026-08-03 design chose this deliberately. `get_prospect_appointment` still
  exists as a fallback and does resolve `call.customer.number`, but that path is
  untested.
- Guards specified but never built: idempotency per `(contactId, apptId)`,
  business-hours window, skip when the appointment is under 15 minutes away or
  already past, skip when already tagged confirmed, retry once on no-answer
  (~45 min, max 2 attempts).

### 6b. Capture the outcome

`POST /voicsito/vapi/outbound/events` currently **logs the event type and returns
`{}`**. Three handlers write `entry["confirm_outcome"]` into an in-memory cache
and **nothing ever reads it.** So today, no confirm call results in a GHL tag, a
note, or a notification.

The intended outcome set, from the design doc:

| Result | GHL tag | Follow-up |
|---|---|---|
| Confirmed | `demo-confirmed` | none |
| Wants reschedule | `demo-reschedule-requested` | notify a human |
| Cancelled | `demo-canceled` | rebooking drip |
| Voicemail / no answer | `demo-call-no-answer` | retry, then iMessage fallback |
| Error | `demo-confirm-error` | alert |

The owner also wants the dashboard to receive: were they confirmed, were they
reminded, did they answer. `SALES_DASHBOARD_WEBHOOK_URL` and
`SALES_DASHBOARD_WEBHOOK_TOKEN` already exist in `config/.env` and are referenced
by **no code** — that is very likely the intended channel.

### 6c. Own the scheduling

Deciding *when* to fire each touchpoint. Today GHL workflows own timing. Whether
that stays in GHL or moves into the dashboard is your call, but note that
GHL must remain the thing that **creates the group**, for the sync reason in §4.

---

## 7. Infrastructure

Shared host `webhooks.c7a.io`, registry at `~/CLAUDSITO/webhooks-infra/REGISTRY.md` (own repo: `github.com/waseem7b/webhooks-infra`).

| Port | Path | Service | systemd unit | Deploy |
|---|---|---|---|---|
| 8081 | `/voicsito/*` | Vapi tools (inbound + outbound) | `voicsito-webhooks` | `scripts/deploy-webhooks.sh` |
| 8082 | `/ashley/*` | Blooio iMessage branding | `ashley-webhooks` | `ashley/scripts/deploy.sh` |
| **8083** | — | **next free — use this** | | |

- Host `root@voicsito-vps` over Tailscale; fall back with
  `VPS_HOST=root@voicsito-vps-direct` when Tailscale is down (it was, on
  2026-08-06). SSH key `~/.ssh/voicsito_vps`.
- nginx `proxy_pass` carries a **trailing slash**, which strips the path prefix.
  The app therefore serves `/group/setup`, not `/ashley/group/setup`. Match this
  or your routes will 404.
- Deploy scripts **never overwrite an existing `.env` on the VPS**. A key
  rotation is a manual step.
- Logs: `journalctl -u ashley-webhooks -f`, `journalctl -u voicsito-webhooks -f`.
- Ashley's run history persists to `/opt/webhooks/ashley/state/*.jsonl` so it
  survives restarts. Those files grow unbounded; only the in-memory view is
  capped at 50.

### Credentials (`config/.env`, gitignored)

`VAPI_API_KEY` · `GHL_MAIN_CRM_PIT` · `GHL_TEXTS_CRM_1_PIT` ·
`GHL_TEXTS_CRM_2_PIT` · `ELEVENLABS_API_KEY` · `DEEPGRAM_API_KEY` ·
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` · `SALES_DASHBOARD_WEBHOOK_URL` /
`_TOKEN` · plus `ashley/config/.env` holding `BLOOIO_API_KEY` and
`ASHLEY_WEBHOOK_SECRET`.

**Absent locally:** `VAPI_WEBHOOK_SECRET`, `VAPI_SQUAD_ID`.

---

## 8. Known bugs and gaps, worst first

1. **`CONFIRM_CALENDAR_ID` points at a test calendar.** `src/agent/ghl.py:44` is
   `"LnHtEHrzCMu8LgcTN0c5"` — *"Z - DO NOT USE - Discovery Call"*. The
   appointment lookup filters on it, so against a real demo on the Video Call
   calendar it returns `appointment_found=false; reason=no_appointment`. Go-live
   requires flipping it to `VIDEO_CALL_CALENDAR_ID`. **This silently breaks every
   confirm call today.**
2. **Vapi webhook auth fails open.** `src/webhooks/server.py:157` returns `True`
   when `VAPI_WEBHOOK_SECRET` is unset — and it is unset locally. Anyone who
   guesses the URL can drive the tools. The 2026-08-03 spec mandates a required
   secret for the new service.
3. **No call origination.** §6a.
4. **No outcome capture.** §6b.
5. **`ashley/scripts/contact_card.py` is broken** — calls a `list_numbers()`
   method that does not exist, and passes `avatar_b64=` where the client expects
   `avatar=`. Not on any runtime path; only bites if you try to edit Ashley's
   card. (Which is 403 anyway while GHL owns the number.)
6. **`list_groups` ignores pagination.** `has_more` / `next_cursor` are returned
   by Blooio and dropped. Fine at 3 groups, silently wrong past one page (~50).
7. **No inbound Blooio webhook.** `create_webhook` exists on the client with no
   caller, and there is no route to receive prospect replies. When you want to
   react to what prospects say in iMessage, this is net-new — and note v2's
   webhook-create endpoint now returns 410, so it must be registered on v4.
8. `docs/checkpoints.md` is stale — last entry 2026-05-18, predating all of this.

---

## 9. Gotchas that cost real time

Every one of these was learned the hard way. Ignoring them repeats a day of work.

**Blooio / iMessage**

- **`grp_` and `chat_` are different namespaces.** Rename and icon take the group
  id; the contact card takes the chat id. Passing a group id to the card endpoint
  is what made card sharing look impossible for hours. `GET /groups` returns both.
- **A rename only reaches the device when the value CHANGES.** GHL writes the
  title to Blooio's database but never to the Mac. An identical PATCH is a silent
  no-op, and since we find the group *by* its title the stored name always
  already matches. The fix is to write a temp name, then the real one.
- **Apple reuses a chat for an identical participant set.** A "new" group with
  the same members silently becomes the existing thread. Stale test groups
  containing a real prospect's number will swallow new runs.
- **One non-iMessage member downgrades the whole thread to SMS**, and cards,
  titles and icons do not exist on SMS. Check capability before creating.
- **Use v4, not v2.** v2 is gated by number ownership and refuses nearly
  everything while GHL owns the number; v4's org-scoped endpoints work with the
  same key. v4 also deletes groups instantly where v2 hangs.
- **404 proves nothing on v4** — its router 404s on method mismatch instead of
  405. Check `https://api.blooio.com/v4/openapi.json` before concluding an
  endpoint is absent.
- Icon and avatar uploads are **multipart**, not the JSON `icon_url` field the v2
  docs advertise.
- Contact-card updates must send the **whole card**; partial writes persist in
  Blooio's database and never reach the device.

**Vapi**

- Tool handlers must always return **HTTP 200**, even on failure, or Vapi retries.
- Tools on the confirm assistants are **inline**, not shared tool resources —
  `verify_tools.py` cannot repair them; re-run the push script.
- Spoken output must never contain a raw ISO timestamp; TTS reads it as
  "oh nine thirty". Convert to words before returning it from a tool.

**General**

- Verify at the **sink**, not the API response. A 200 from Blooio does not mean
  the device applied anything — several times today it did not. Look at the phone.
- Before declaring a third-party capability impossible, read
  `.claude/skills/api-capability-probing/SKILL.md` in this repo. It exists
  because three capabilities were declared impossible in one session and all
  three worked once the API was called correctly.

---

## 10. Verifying the current system works

```bash
# iMessage layer — health
curl -s https://webhooks.c7a.io/ashley/health

# iMessage layer — recent outcomes
curl -s https://webhooks.c7a.io/ashley/runs \
  -H "X-Ashley-Secret: $ASHLEY_WEBHOOK_SECRET" | python3 -m json.tool

# Brand a group by hand (must already exist in Blooio, created by GHL)
python ashley/scripts/setup_group.py --business "Tree City"

# Tests
pytest ashley/tests            # 50 tests, no network

# Live Vapi resources
curl -s https://api.vapi.ai/squad -H "Authorization: Bearer $VAPI_API_KEY"
```

A healthy branding run in `/ashley/runs` looks like:

```json
{"result": {"ok": true,
            "group_id": "grp_…", "chat_id": "chat_…",
            "name": "Growth System - Cloud 7 Agency",
            "steps": [{"step": "rename", "ok": true},
                      {"step": "icon",   "ok": true},
                      {"step": "card",   "ok": true}]}}
```

Ashley's card renders on the recipient's device as **"Maybe: Ashley Miller"**
until they tap Add. That is correct behaviour, not a failure.

---

## 11. Reference documents

| Path | What it holds |
|---|---|
| `ashley/README.md` | Blooio v4 rationale, the resolver table, the full gotcha list |
| `docs/superpowers/specs/2026-08-03-demo-confirmation-design.md` | The current target design — direct API origination, single-stage assistant, outcome tags. **Design only.** |
| `docs/superpowers/specs/2026-06-04-demo-confirmation-flow.md` | The spec the shipped squad implements (GHL bridge, three stages) |
| `docs/call-types.md` | Type 4 confirmation call spec — tone, flow, post-call tags |
| `~/CLAUDSITO/webhooks-infra/REGISTRY.md` | Port allocation on `webhooks.c7a.io` (own repo since 2026-08-11) |
| `CLAUDE.md` | Project rules. Rule 4 and `api-capability-probing` matter most here. |
