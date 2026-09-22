# Ashley — iMessage Confirmation / Show-Up System

Portable, self-contained subsystem: Blooio (iMessage/SMS) + GoHighLevel around the
demo confirmation and show-up process. Agent persona: Ashley Miller. Designed to be
lifted whole into the Sales Takeover Dashboard later — no imports from Voicsito code.

**Handing this off?** Read `HANDOFF.md` — it maps all three layers (iMessage,
GoHighLevel, Vapi voice), the live IDs, what is built vs designed, and the
known bugs.

## Layout

- `src/blooio/client.py` — typed Blooio v4 API client
- `src/blooio/group_lookup.py` — find the group by the title GHL gave it
- `src/blooio/group_setup.py` — rename → icon → contact card, per-step reporting
- `src/blooio/group_icon.py` — render the emoji-on-gradient icon (local build only)
- `src/webhook/server.py` — FastAPI endpoint GHL calls
- `assets/group-icon.png` — pre-rendered icon shipped to the VPS
- `scripts/smoke.py` — live smoke test (capabilities → send → delivery status)
- `scripts/setup_group.py` — brand a group by hand
- `scripts/deploy.sh` — deploy to `webhooks.c7a.io` (port 8082)
- `tests/` — `pytest ashley/tests`
- `config/.env` — secrets (gitignored); copy from `config/.env.example`

## The webhook

GHL's automation creates the group chat and opens the conversation, then calls:

```
POST https://webhooks.c7a.io/ashley/group/setup
X-Ashley-Secret: <ASHLEY_WEBHOOK_SECRET>
{"phone": "{{contact.phone}}", "business_name": "{{contact.company_name}}"}
```

`business_name` is what matters: the group is found by the exact title GHL
created it with, `Growth System - <business_name>`. It falls back to "Growth" if
absent. `phone` is required but only identifies the run in the logs. Pass
`"share_card": false` to skip Ashley's card.

The lookup waits up to 4 minutes for that title to appear, because GHL's Bloo
step can take minutes to materialise the chat on the device. If it never
appears the run reports `group_not_found` and touches nothing — it will not fall
back to an older group holding the same prospect.

It returns `202` immediately with a `run_id`, because the device-backed calls
take longer than GHL will wait. Outcomes land at `GET /ashley/runs` (same
secret header) and in `journalctl -u ashley-webhooks`.

## Setup

```bash
pip install -r ashley/requirements.txt
cp ashley/config/.env.example ashley/config/.env   # then fill in the key
python ashley/scripts/smoke.py +1XXXXXXXXXX
```

## Blooio API: why v4

Spec: `https://api.blooio.com/v4/openapi.json` (v4.0.0-beta). v2 remains
supported by Blooio, but this module is on v4 because v4 exposes an org-scoped
`channels` resource that does not consult the per-number ownership allowlist.

Our number is assigned to the **GoHighLevel integration** in the Blooio
dashboard — that is deliberate, since it is what makes GHL create the groups and
sync the conversations into the CRM. Assignment is exclusive (dashboard-only;
no API to change it). Under that assignment there are three resolvers:

| resolver | endpoints | status |
|---|---|---|
| org/binding-scoped | chats, groups, messages, contacts, events, webhooks | **works** |
| number-ownership | `/me/numbers/*` — contact card CONTENT | 403 |
| device-passthrough | chat background, Find My, capability check | 502 / 404 / 409 |

So Ashley's card can be **shared** (chat-scoped) but not **edited**
(number-scoped) while GHL owns the number. Her card is already set; changing her
name or photo means reassigning the number in the dashboard temporarily — a
one-time manual step, not a per-prospect concern.

Live-verified 2026-08-06 end to end, GHL webhook through to the device: rename,
group icon, and Ashley's contact card all succeed on v4 with the number assigned
to GHL. The card lands in a real iMessage **group** — it renders as
"Maybe: Ashley Miller" until the recipient taps Add.

## Gotchas that cost time

- **Group id and chat id are different things.** Rename and icon take `grp_...`;
  the contact card takes `chat_...`. Passing a group id to the card endpoint is
  what made card sharing look impossible under v2. `GET /groups` returns both.
- **The icon needs a device-linked chat.** v4 409s otherwise; `chat_linked` on
  the group record says so up front.
- **Rename to the title GHL already used.** Blooio pushes a group name to GHL
  only at creation, so renaming to anything else desyncs the CRM.
- **A rename only reaches the device when the value CHANGES.** GHL writes the
  title to Blooio's database but never to the Mac, so the iMessage thread keeps
  its old name and an identical PATCH is a silent no-op. The fix is to write a
  temporary name and then the real one — that bounce is what makes the device
  apply it.
- **v4 dropped `share_contact: true` on sends.** Stage the card with
  `POST /chats/{id}/contact-card`; it rides the next outgoing message, which GHL
  sends.
- **Contact card updates must send the whole card.** Partial writes persist in
  Blooio's database but never reach the device.
- **Icon upload is multipart**, field `icon` — not the `icon_url` JSON field the
  v2 docs advertise.
- **Delete works on v4, not v2.** `DELETE /v4/groups/{id}` returns
  `{"deleted": true}` immediately; v2's equivalent hangs on the device-side leave.
- **Apple reuses a chat for an identical participant set.** A "new" group with
  the same members as an existing one silently becomes that existing thread —
  so stale test groups with a real prospect's number will swallow new runs.
- **404 proves nothing on v4** — the router 404s on method mismatch instead of
  405. Check `openapi.json` before concluding an endpoint is absent.

## Design

Working spec: `docs/superpowers/specs/2026-08-03-demo-confirmation-design.md`
(GHL workflows own timing/messaging; Bloo sends iMessage; thin webhook service +
Vapi voice leg come after the messaging layer is proven.)
