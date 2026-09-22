"""GHL API client — caller lookup + calendar booking."""
import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

GHL_BASE = "https://services.leadconnectorhq.com"

log = logging.getLogger("voicsito.ghl")

# Three CRMs. PITs come from env (config/.env locally, /opt/webhooks/voicsito/.env on VPS).
# Location IDs are not secret; they're public references.
# biz_type_field_id is the per-location custom field ID for "Biz Type" (TEXT field
# storing the prospect's primary trade — used for industry adaptation in the prompt).
CRMS = {
    "main": {
        "pit": os.environ["GHL_MAIN_CRM_PIT"],
        "location_id": "G1yFGT1liEZgvT3dYgEx",
        "biz_type_field_id": None,  # not used in main; main is the booking destination
    },
    "texts1": {
        "pit": os.environ["GHL_TEXTS_CRM_1_PIT"],
        "location_id": "9U157i1e8MRql0vtiDi2",
        "biz_type_field_id": "pCK4An2qtrLGj71bsbM1",
    },
    "texts2": {
        "pit": os.environ["GHL_TEXTS_CRM_2_PIT"],
        "location_id": "Z7oeWKLyzFp85JQN346m",
        "biz_type_field_id": "iIbJvGHFTa1RR8WWpepz",
    },
}

# Main CRM Video Call Calendar
VIDEO_CALL_CALENDAR_ID = "ULFPF484I2XcTfSR2uIC"
# CONFIRM_CALENDAR_ID = the calendar the confirm flow LOOKS UP the prospect's
# existing demo appointment on. Rebook availability and the reschedule destination
# are always VIDEO_CALL_CALENDAR_ID, so a rebook MOVES the appointment onto the
# Video Call Calendar without disturbing anything already booked there.
#   TESTING (now): lookup reads "Z - DO NOT USE - Discovery Call" — test
#     appointments live there, and rebooking moves them onto the real calendar.
#   GO-LIVE: set this to VIDEO_CALL_CALENDAR_ID so lookup + availability +
#     reschedule all use the one calendar (the move becomes a no-op).
# GO-LIVE taken 2026-08-31: lookup, availability and reschedule now all use
# the one calendar, so a rebook MOVES the appointment instead of creating a
# second one. Every test call before this ran with bookedTime empty, because
# the lookup was reading a calendar nothing was booked on — which is what
# produced the invented times ("two in the afternoon", "your time") and the
# broken "for the call at later today". Revert = put the Discovery id back.
CONFIRM_CALENDAR_ID = VIDEO_CALL_CALENDAR_ID

# Bookings made before the GO-LIVE flip still sit on the old Discovery calendar,
# and after the flip the lookup stopped finding them at all -- every call since
# ran with bookedTime empty and said "for the call at later today". LOOKUP now
# accepts both calendars so a confirm/reminder call finds the appointment
# wherever it was booked. Availability and reschedule still target
# VIDEO_CALL_CALENDAR_ID alone, so a rebook keeps MOVING the appointment onto
# the real calendar rather than creating a second one.
DISCOVERY_CALENDAR_ID = "LnHtEHrzCMu8LgcTN0c5"  # "Z - DO NOT USE - Discovery Call"
LOOKUP_CALENDAR_IDS = {CONFIRM_CALENDAR_ID, DISCOVERY_CALENDAR_ID}

# Rebooking calls book with JON ONLY (owner, 2026-09-10). The Video Call
# Calendar is round_robin across Waseem (priority 0) and Jon (priority 1), so
# left alone it offers the pooled availability and lets the round-robin assign.
# Both halves matter: filter the SLOTS so we never offer a time Jon cannot take,
# and set assignedUserId on the WRITE so the round-robin cannot hand the booking
# to Waseem anyway. Only the rebooking flow passes this — confirmation and
# reminder calls are about an existing booking and must not be re-pointed.
REBOOKING_ASSIGNED_USER_ID = "jUagFOxhVAtBf1f0fTSe"  # Jon Uhl <jon@c7a.io>

# GHL returns appointment times NAIVE (no offset), in the account's configured
# timezone — this location is America/Bogota (UTC-5, no DST). Naive appointment
# times must be localized to this, NOT UTC, or a same-day appointment reads ~5h
# early and gets wrongly dropped as "past" in _select_upcoming_video_appointment.
GHL_ACCOUNT_TZ = ZoneInfo("America/Bogota")


def _extract_biz_type(contact: dict, biz_type_field_id: str | None) -> str:
    """Pull the Biz Type custom field value off a contact dict.
    Returns empty string if missing, no field ID configured, or value blank."""
    if not biz_type_field_id:
        return ""
    for f in contact.get("customFields") or []:
        if f.get("id") == biz_type_field_id:
            return (f.get("value") or "").strip()
    return ""


def _str(v) -> str:
    """Coerce a GHL field to a stripped str. Tolerates None and non-str values."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


async def lookup_contact_in_crm(client: httpx.AsyncClient, crm_key: str, phone: str):
    """Look up contact by phone in a single CRM. Returns contact dict or None."""
    crm = CRMS[crm_key]
    pit = crm["pit"]
    location_id = crm["location_id"]
    try:
        r = await client.get(
            f"{GHL_BASE}/contacts/search/duplicate",
            params={"locationId": location_id, "number": phone},
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
            },
            timeout=4.0,
        )
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                # Defensive: GHL 5xx sometimes returns HTML error pages.
                return None
            contact = data.get("contact") if isinstance(data, dict) else None
            if contact and contact.get("id"):
                return {
                    "crm": crm_key,
                    "location_id": location_id,
                    "id": contact.get("id"),
                    "name": _str(contact.get("contactName")) or _str(contact.get("firstName")),
                    "first_name": _str(contact.get("firstName")),
                    "business": _str(contact.get("companyName")),
                    "email": _str(contact.get("email")),
                    "phone": _str(contact.get("phone")),
                    "city": _str(contact.get("city")),
                    "state": _str(contact.get("state")),
                    "timezone": _str(contact.get("timezone")),
                    "tags": contact.get("tags") if isinstance(contact.get("tags"), list) else [],
                    "biz_type": _extract_biz_type(contact, crm.get("biz_type_field_id")),
                }
    except Exception:
        pass
    return None


def _has_real_data(c: dict) -> bool:
    """Filter out empty/scraped contacts (no name + no business)."""
    if not c:
        return False
    name = (c.get("name") or "").strip()
    first = (c.get("first_name") or "").strip()
    business = (c.get("business") or "").strip()
    return bool(name or first or business)


async def lookup_contact_in_texts_crms(phone: str):
    """Parallel lookup across Texts1 + Texts2 only (for Type 2/3 Inbound Texts).
    Skips contacts with no name/business (filters scraped junk)."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            lookup_contact_in_crm(client, "texts1", phone),
            lookup_contact_in_crm(client, "texts2", phone),
            return_exceptions=True,
        )
    for r in results:
        if isinstance(r, dict) and _has_real_data(r):
            return r
    return None


async def lookup_contact_in_main_crm(phone: str):
    """Lookup in Main CRM only (for Type 6 Inbound Main, future)."""
    async with httpx.AsyncClient() as client:
        r = await lookup_contact_in_crm(client, "main", phone)
    return r if _has_real_data(r) else None


async def lookup_contact_all_crms(phone: str):
    """Parallel lookup across all 3 CRMs. Texts1/2 preferred over Main."""
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            lookup_contact_in_crm(client, "texts1", phone),
            lookup_contact_in_crm(client, "texts2", phone),
            lookup_contact_in_crm(client, "main", phone),
            return_exceptions=True,
        )
    for r in results:
        if isinstance(r, dict) and _has_real_data(r):
            return r
    return None


async def get_available_slots(
    days_ahead: int = 7,
    timezone: str = "",
    calendar_id: str = VIDEO_CALL_CALENDAR_ID,
    user_id: str = "",
) -> dict:
    """Fetch open slots from `calendar_id` (defaults to the Video Call Calendar)
    for the next N days.

    When `timezone` is a non-empty IANA name (e.g. 'America/Los_Angeles'),
    GHL returns slot ISOs already in that zone — no client-side conversion
    needed. When blank, GHL falls back to the calendar's configured zone.

    `user_id` narrows availability to ONE team member on a round-robin calendar.
    Measured against the live Video Call Calendar on 2026-09-10: the pool returns
    26 slots over 7 days, Jon 12, Waseem 26 — Jon's set is a strict SUBSET, so
    without this filter more than half of what we offer is time only Waseem can
    take. Blank keeps the pooled behaviour every other flow relies on.
    """
    crm = CRMS["main"]
    pit = crm["pit"]
    start_ms = int(time.time() * 1000)
    end_ms = start_ms + (days_ahead * 86400 * 1000)
    params: dict[str, str | int] = {"startDate": start_ms, "endDate": end_ms}
    if timezone:
        params["timezone"] = timezone
    if user_id:
        params["userId"] = user_id
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{GHL_BASE}/calendars/{calendar_id}/free-slots",
            params=params,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-04-15",
            },
            timeout=6.0,
        )
        r.raise_for_status()
        return r.json()


async def book_video_call(
    slot_iso: str, contact_id: str, email: str = None, assigned_user_id: str = ""
) -> dict:
    """Book an appointment on the Video Call Calendar.
    slot_iso: ISO 8601 datetime with offset, e.g. 2026-05-07T14:00:00-05:00

    `assigned_user_id` pins the booking to one team member. Without it the
    calendar's round-robin chooses, which on a rebooking call could hand a
    Jon-only slot to Waseem.
    """
    crm = CRMS["main"]
    pit = crm["pit"]
    location_id = crm["location_id"]
    end_dt = (datetime.fromisoformat(slot_iso) + timedelta(hours=1)).isoformat()
    payload = {
        "calendarId": VIDEO_CALL_CALENDAR_ID,
        "locationId": location_id,
        "contactId": contact_id,
        "startTime": slot_iso,
        "endTime": end_dt,
        # No `title` — GoHighLevel generates the meeting title from the
        # calendar's own config. Sending one overrides it and produces
        # inconsistent appointment names in the dashboard.
        "appointmentStatus": "confirmed",
        "ignoreDateRange": False,
        "toNotify": True,
    }
    if assigned_user_id:
        payload["assignedUserId"] = assigned_user_id
    if email:
        payload["meta"] = {"email": email}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{GHL_BASE}/calendars/events/appointments",
            json=payload,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-04-15",
                "Content-Type": "application/json",
            },
            timeout=8.0,
        )
        if r.status_code in (200, 201):
            return {"ok": True, "appointment": r.json()}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


async def create_contact_in_texts_crm(
    phone: str,
    first_name: str,
    last_name: str = "",
    email: str = "",
    business: str = "",
) -> dict:
    """Create a contact in Texts CRM 1 when caller-ID lookup fails.
    Returns {ok: bool, contact_id: str | None, error: str | None}.
    Handles the duplicate case (returns the existing contact_id).
    """
    crm = CRMS["texts1"]
    pit = crm["pit"]
    location_id = crm["location_id"]
    payload = {
        "locationId": location_id,
        "firstName": first_name,
        "lastName": last_name,
        "phone": phone,
        "email": email,
        "companyName": business,
        "source": "AI Voice Agent",
        "tags": ["voicsito-inbound"],
    }
    payload = {k: v for k, v in payload.items() if v}
    payload["locationId"] = location_id  # always required

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{GHL_BASE}/contacts/",
            json=payload,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=8.0,
        )
        if r.status_code in (200, 201):
            data = r.json()
            c = data.get("contact", data)
            return {"ok": True, "contact_id": c.get("id"), "error": None}
        # Duplicate-phone path: GHL returns 400 with the existing contact ID in `meta`
        try:
            data = r.json()
            existing_id = (data.get("meta") or {}).get("contactId")
            if existing_id:
                return {"ok": True, "contact_id": existing_id, "error": None}
        except Exception:
            pass
        return {"ok": False, "contact_id": None, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


async def add_tag(contact_id: str, crm_key: str, tag: str) -> bool:
    """Add a single tag to a contact (idempotent — GHL ignores duplicates)."""
    crm = CRMS[crm_key]
    pit = crm["pit"]
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{GHL_BASE}/contacts/{contact_id}/tags",
            json={"tags": [tag]},
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=5.0,
        )
        return r.status_code in (200, 201)


OPT_OUT_TAGS = {"notinterested", "donotcontact", "dnc"}


def _fold_tag(t) -> str:
    """GHL tags are typed by hand, so compare them folded rather than literally."""
    return str(t or "").lower().replace(" ", "").replace("-", "").replace("_", "")


async def has_opted_out(contact_id: str, crm_key: str = "main") -> bool:
    """True when the contact carries a tag that means 'stop contacting me'.

    The read side of `add_tag(..., "not-interested")`. Ashley fires
    `mark_not_interested` the moment someone says they are done, and until
    2026-09-10 nothing ever read the result back: a prospect who said "can you
    please not call me back" was dialled five more times inside half an hour
    (call 01a08929, 2026-09-10).

    FAILS OPEN on purpose. A GHL blip must not silently stop every outbound
    call; the caller logs and dials. The cost of the other choice is an outage
    nobody notices.
    """
    crm = CRMS[crm_key]
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers={
                "Authorization": f"Bearer {crm['pit']}",
                "Version": "2021-07-28",
            },
            timeout=5.0,
        )
        if r.status_code != 200:
            log.warning("has_opted_out lookup failed status=%s id=%s", r.status_code, contact_id)
            return False
        tags = (r.json().get("contact") or {}).get("tags") or []
        return any(_fold_tag(t) in OPT_OUT_TAGS for t in tags)


async def has_call_dnd(contact_id: str, crm_key: str = "main") -> bool:
    """True when GHL says this contact must not be phoned.

    Blocks on the global `dnd` flag, or on the Call channel's own DND
    (`dndSettings.Call.status` anything but "inactive"). DND on SMS alone does
    NOT block a call: GHL sets SMS DND automatically on carrier failures such as
    Twilio 30005 (unknown handset), which says nothing about whether the person
    agreed to be called.

    Added 2026-09-13. Nothing on the dial path read DND before this, and because
    Vapi places the call rather than GHL, GHL's own DND suppression never applied.

    FAILS OPEN, the same as has_opted_out: a GHL blip must not silently stop every
    outbound call. The caller logs the failure.
    """
    crm = CRMS[crm_key]
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{GHL_BASE}/contacts/{contact_id}",
            headers={
                "Authorization": f"Bearer {crm['pit']}",
                "Version": "2021-07-28",
            },
            timeout=5.0,
        )
        if r.status_code != 200:
            log.warning("has_call_dnd lookup failed status=%s id=%s", r.status_code, contact_id)
            return False
        contact = r.json().get("contact") or {}
        if contact.get("dnd") is True:
            return True
        for channel, setting in (contact.get("dndSettings") or {}).items():
            if str(channel).strip().lower() != "call" or not isinstance(setting, dict):
                continue
            status = str(setting.get("status") or "").strip().lower()
            return bool(status) and status != "inactive"
        return False


async def get_appointment_status(appointment_id: str) -> str:
    """Current status of one appointment, straight from GHL. "" if unknowable.

    The GHL "Appointment Status" trigger fires on cancelled and no-show as well
    as booked, and the workflow's webhook body does not reliably carry the
    status merge field. Reading it here means the guard cannot be bypassed by a
    workflow edit that drops the field.
    """
    appt = await get_appointment(appointment_id)
    return str(appt.get("appointmentStatus") or appt.get("status") or "").lower()


async def get_appointment(appointment_id: str) -> dict:
    """One appointment straight from GHL, or {} if unknowable. Unlike the contact's
    appointment list, this still answers for an appointment that has been deleted."""
    crm = CRMS["main"]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GHL_BASE}/calendars/events/appointments/{appointment_id}",
                headers={"Authorization": f"Bearer {crm['pit']}", "Version": "2021-04-15"},
                timeout=6.0,
            )
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}
    appt = data.get("appointment") if isinstance(data, dict) else None
    if not isinstance(appt, dict):
        appt = data if isinstance(data, dict) else {}
    return appt


async def remove_tag(contact_id: str, crm_key: str, tag: str) -> bool:
    """Remove a single tag from a contact (idempotent — removing a tag they do
    not have is not an error).

    Clears the previous attempt's call-outcome tag before we redial, so a 2pm
    retry cannot leave the GHL show-up workflow branching on this morning's
    result as if it were this call's.
    """
    crm = CRMS[crm_key]
    pit = crm["pit"]
    async with httpx.AsyncClient() as client:
        r = await client.request(
            "DELETE",
            f"{GHL_BASE}/contacts/{contact_id}/tags",
            json={"tags": [tag]},
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=5.0,
        )
        return r.status_code in (200, 201, 204)


async def lookup_in_main_crm_by_phone(phone: str) -> dict | None:
    """Raw main-CRM phone lookup — no _has_real_data filter.
    Used after texts→main automation transfer to find the freshly-created contact."""
    async with httpx.AsyncClient() as client:
        return await lookup_contact_in_crm(client, "main", phone)


async def wait_for_main_crm_contact(
    phone: str, max_attempts: int = 5, interval_seconds: float = 3.0
) -> dict | None:
    """Poll main CRM by phone until contact appears (post tag-transfer automation).
    Returns contact dict or None after max_attempts. Total wait: ~max_attempts * interval (15s).
    Tightened from 4×12s=48s ceiling — automation typically completes <5s; the long wait
    was hanging the LLM mid-call on book_video_call."""
    for attempt in range(max_attempts):
        if attempt > 0:
            await asyncio.sleep(interval_seconds)
        contact = await lookup_in_main_crm_by_phone(phone)
        if contact and contact.get("id"):
            return contact
    return None


async def update_contact(contact_id: str, crm_key: str, updates: dict) -> bool:
    """Update contact custom fields, tags, etc. Used for post-call writeback."""
    crm = CRMS[crm_key]
    pit = crm["pit"]
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{GHL_BASE}/contacts/{contact_id}",
            json=updates,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=5.0,
        )
        return r.status_code in (200, 201)


async def update_prospect_fields(
    contact_id: str,
    crm_key: str,
    first_name: str = "",
    last_name: str = "",
    business: str = "",
    email: str = "",
) -> dict:
    """Update a contact with real first/last name, business, email.

    Handles the scraped-data case: many existing contacts have the BUSINESS NAME
    in their firstName field (because that's how scraping populated them). When the
    real name is captured during conversation, we override firstName with the
    real name and move the previous content to companyName if companyName is empty.

    Returns {ok: bool, error: str | None}.
    """
    crm = CRMS[crm_key]
    pit = crm["pit"]

    updates = {}
    if first_name:
        updates["firstName"] = first_name.strip()
    if last_name:
        updates["lastName"] = last_name.strip()
    if email:
        updates["email"] = email.strip()
    if business:
        updates["companyName"] = business.strip()

    # Edge case: LLM didn't provide business but is overwriting firstName.
    # Old firstName might be a scraped business name — fetch existing to promote it.
    # Only path that needs the GET; common path (business provided) skips the round-trip.
    if first_name and not business:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GHL_BASE}/contacts/{contact_id}",
                headers={"Authorization": f"Bearer {pit}", "Version": "2021-07-28"},
                timeout=3.0,
            )
            if r.status_code == 200:
                existing = r.json().get("contact", {}) or {}
                if not (existing.get("companyName") or "").strip():
                    old_first = (existing.get("firstName") or "").strip()
                    looks_like_business = (
                        len(old_first.split()) >= 2
                        or any(w in old_first.lower() for w in ["llc", "inc", "co.", "remodel", "construction", "homes", "builders"])
                    )
                    if old_first and looks_like_business:
                        updates["companyName"] = old_first

    if not updates:
        return {"ok": True, "error": None}

    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{GHL_BASE}/contacts/{contact_id}",
            json=updates,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=6.0,
        )
        if r.status_code in (200, 201):
            return {"ok": True, "error": None}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# ─────────────────── Outbound demo-confirmation (Type 4) ───────────────────
# These are used ONLY by the outbound demo-confirmation flow. They read/write
# the Main CRM and never touch the texts CRMs the inbound path uses.


def _select_upcoming_video_appointment(events: list[dict]) -> dict | None:
    """Pick the soonest future, non-cancelled appointment on any calendar in
    LOOKUP_CALENDAR_IDS from a contact's event list. Returns the event dict or None."""
    now = datetime.now(timezone.utc)
    dead = {"cancelled", "canceled", "noshow", "no-show", "no_show"}
    candidates: list[tuple[datetime, dict]] = []
    for ev in events:
        if ev.get("calendarId") not in LOOKUP_CALENDAR_IDS:
            continue
        status = (ev.get("appointmentStatus") or ev.get("status") or "").lower()
        if status in dead:
            continue
        start = ev.get("startTime")
        if not start:
            continue
        try:
            dt = datetime.fromisoformat(start)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=GHL_ACCOUNT_TZ)
        if dt >= now:
            candidates.append((dt, ev))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


MISSED_WINDOW = timedelta(days=7)


def _select_last_missed_video_appointment(
    events: list[dict], now: datetime | None = None
) -> dict | None:
    """The no-show or cancel a rebooking call is about: on a tracked calendar, not
    deleted, starting within a week either side of now, and the one whose status
    changed most recently. Returns the event dict or None.

    Ordered by when it last changed, not by when it starts. A prospect can cancel a
    booking that has not happened yet, and on call 01a0a257 tonight's booking had just
    been cancelled while an older, already-past cancel was named instead ("last
    Friday"). Events with no update time fall back to their start.
    """
    now = now or datetime.now(timezone.utc)
    missed = {"cancelled", "canceled", "noshow", "no-show", "no_show"}
    candidates: list[tuple[datetime, datetime, dict]] = []
    for ev in events:
        if ev.get("calendarId") not in LOOKUP_CALENDAR_IDS or ev.get("deleted"):
            continue
        status = (ev.get("appointmentStatus") or ev.get("status") or "").lower()
        if status not in missed:
            continue
        start = _event_start(ev)
        if not start or abs(start - now) > MISSED_WINDOW:
            continue
        candidates.append((_event_updated(ev) or start, start, ev))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[-1][2]


def _event_start(ev: dict) -> datetime | None:
    """An event's start as an aware datetime. GHL returns naive account-local
    strings on some endpoints, so a naive value is read in the account timezone."""
    raw = ev.get("startTime")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=GHL_ACCOUNT_TZ)


def _event_updated(ev: dict) -> datetime | None:
    """When an event last changed (its cancel or no-show), as an aware datetime.
    Naive values are account-local, like startTime."""
    raw = ev.get("dateUpdated")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=GHL_ACCOUNT_TZ)


async def _get_contact_appointments(client: httpx.AsyncClient, contact_id: str) -> list[dict]:
    """GET all appointments/events for a Main-CRM contact."""
    crm = CRMS["main"]
    try:
        r = await client.get(
            f"{GHL_BASE}/contacts/{contact_id}/appointments",
            headers={"Authorization": f"Bearer {crm['pit']}", "Version": "2023-02-21"},
            timeout=6.0,
        )
    except Exception:
        return []
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    events = data.get("events") if isinstance(data, dict) else None
    return events if isinstance(events, list) else []


async def get_prospect_appointment(phone: str) -> dict:
    """Outbound-confirmation only. Look up a Main-CRM contact by phone and
    return their soonest upcoming Video-Call-Calendar appointment.

    Returns on success:
      {"ok": True, "contact_id", "first_name", "business", "city", "state",
       "timezone", "appointment_id", "start_iso", "status"}
    On miss:
      {"ok": False, "reason": "no_contact"}                       (no phone match)
      {"ok": False, "reason": "no_appointment", "contact_id", "first_name"}

    Caller (the Stage 1 tool handler) is responsible for cross-checking the
    appointment is ~today before confirming a specific time.
    """
    async with httpx.AsyncClient() as client:
        contact = await lookup_contact_in_crm(client, "main", phone)
        if not contact or not contact.get("id"):
            return {"ok": False, "reason": "no_contact"}
        contact_id = contact["id"]
        events = await _get_contact_appointments(client, contact_id)

    appt = _select_upcoming_video_appointment(events)
    # The call they missed, for the rebooking opener. Same event list, no extra request.
    missed = _select_last_missed_video_appointment(events)
    missed_start = _event_start(missed) if missed else None
    missed_start_iso = missed_start.isoformat() if missed_start else ""
    if not appt:
        return {
            "ok": False,
            "reason": "no_appointment",
            "contact_id": contact_id,
            "first_name": contact.get("first_name", ""),
            "business": contact.get("business", ""),
            "city": contact.get("city", ""),
            "state": contact.get("state", ""),
            "timezone": contact.get("timezone", ""),
            "missed_start_iso": missed_start_iso,
        }
    return {
        "ok": True,
        "contact_id": contact_id,
        "first_name": contact.get("first_name", ""),
        "business": contact.get("business", ""),
        "city": contact.get("city", ""),
        "state": contact.get("state", ""),
        "timezone": contact.get("timezone", ""),
        "appointment_id": appt.get("id", ""),
        "start_iso": appt.get("startTime", ""),
        "status": (appt.get("appointmentStatus") or appt.get("status") or ""),
        "missed_start_iso": missed_start_iso,
    }


async def reschedule_appointment(
    appointment_id: str, new_slot_iso: str, assigned_user_id: str = ""
) -> dict:
    """Move an existing appointment to a new slot on the Video Call Calendar
    (true reschedule — same event record). endTime = startTime + 1h (Jon's calls
    run an hour, and the Video Call Calendar is a 1-hour, one-per-slot calendar).

    `calendarId` is set to VIDEO_CALL_CALENDAR_ID so a confirm rebook MOVES a
    test-calendar appointment onto the Video Call Calendar; at go-live the event
    is already there and this is a no-op.

    new_slot_iso: ISO 8601 with offset, e.g. 2026-06-10T15:00:00-07:00.
    Returns {"ok": bool, "appointment"|"error"}.
    """
    crm = CRMS["main"]
    end_dt = (datetime.fromisoformat(new_slot_iso) + timedelta(hours=1)).isoformat()
    # Mirror book_video_call: keep status confirmed and send the updated invite
    # (toNotify) so the prospect actually gets a reschedule notification.
    payload = {
        "calendarId": VIDEO_CALL_CALENDAR_ID,
        "startTime": new_slot_iso,
        "endTime": end_dt,
        "appointmentStatus": "confirmed",
        "toNotify": True,
    }
    # A move keeps whoever the appointment was already assigned to, so a
    # rebooking that lands on a Jon-only slot would still sit on Waseem's
    # calendar unless we re-assign it here.
    if assigned_user_id:
        payload["assignedUserId"] = assigned_user_id
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{GHL_BASE}/calendars/events/appointments/{appointment_id}",
            json=payload,
            headers={
                "Authorization": f"Bearer {crm['pit']}",
                "Version": "2021-04-15",
                "Content-Type": "application/json",
            },
            timeout=8.0,
        )
        if r.status_code in (200, 201):
            try:
                appt = r.json()
            except Exception:
                appt = {}
            return {"ok": True, "appointment": appt}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}


# Message types that count as the prospect's conversation with us. Each is
# filtered server-side; see get_recent_texts for why each one is here.
THREAD_MESSAGE_TYPES = ("TYPE_SMS", "TYPE_LIVE_CHAT")


async def get_recent_texts(contact_id: str, crm_key: str = "main", limit: int = 10) -> list[dict]:
    """The prospect's most recent messages, newest-last. Never raises.

    Added 2026-09-07. Ashley calls people who have been texted, and until now she
    knew nothing about that thread — so she would ring somebody who had texted
    "running ten minutes late" a minute earlier. The caller decides what to do
    with this; see webhooks/text_digest.py.

    Two hops, because GHL keys messages off a conversation rather than a contact:
    find the contact's conversation, then read that conversation's messages.
    Timeouts are deliberately short — this sits on the pre-dial path, and a slow
    CRM must delay the call, never cancel it. Any failure returns [], which the
    caller reads as "no thread known" and dials as it would have before.

    FILTERED SERVER-SIDE TO TYPE_SMS, and that filter is load-bearing. A GHL
    conversation is not a message list — it also carries activity records
    (TYPE_ACTIVITY_OPPORTUNITY on every opportunity create/update/delete,
    TYPE_ACTIVITY_APPOINTMENT on every booking), emails, live-chat and our own
    TYPE_INTERNAL_COMMENT operator notes. Unfiltered they crowd the window out
    entirely: on call 01a08d64 (2026-09-10) all ten items were activity records
    and internal comments, Ashley was handed "You texted just now 'Opportunity
    deleted'" as if it were a text we had sent, and the prospect's five genuine
    inbound texts were nowhere in the window.

    The consequence was not cosmetic. should_hold_for_reply reads this list, so
    with every entry outbound it concluded we spoke last and let the call go —
    when the prospect had in fact texted us last and the call should have
    paused. Filtering client-side would not fix it: the limit truncates before
    we ever see the rows.

    LIVE CHAT IS INCLUDED TOO (2026-09-13). The team's replies to prospects go
    out through the GHL app and are stored as TYPE_LIVE_CHAT, not TYPE_SMS. With
    an SMS-only filter those replies were invisible, so a prospect whose last
    SMS was inbound stayed "awaiting our reply" forever even after we had
    answered them. Ryan Meat, Marco (AMD Construction) and Lex (SBS Commercial)
    all had calls paused that way, while the drip, which reads every message
    type, correctly saw our reply. Activity records, emails and internal
    comments stay excluded, so the 01a08d64 failure above cannot come back.
    """
    crm = CRMS.get(crm_key)
    if not crm or not contact_id:
        return []
    headers = {"Authorization": f"Bearer {crm['pit']}", "Version": "2021-04-15"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GHL_BASE}/conversations/search",
                params={"locationId": crm["location_id"], "contactId": contact_id},
                headers=headers,
                timeout=4.0,
            )
            if r.status_code != 200:
                return []
            convos = (r.json() or {}).get("conversations") or []
            if not convos:
                return []
            convo_id = convos[0].get("id")
            if not convo_id:
                return []

            # One request per type, concurrently. GHL filters on a single type
            # per request, and the filter must stay server-side (see above).
            responses = await asyncio.gather(*(
                client.get(
                    f"{GHL_BASE}/conversations/{convo_id}/messages",
                    params={"limit": limit, "type": msg_type},
                    headers=headers,
                    timeout=4.0,
                )
                for msg_type in THREAD_MESSAGE_TYPES
            ), return_exceptions=True)
            merged: list[dict] = []
            any_ok = False
            for r2 in responses:
                if isinstance(r2, Exception) or r2.status_code != 200:
                    continue
                any_ok = True
                payload = (r2.json() or {}).get("messages") or {}
                msgs = payload.get("messages") if isinstance(payload, dict) else payload
                if isinstance(msgs, list):
                    merged.extend(m for m in msgs if isinstance(m, dict))
            if not any_ok:
                # The search result already carries the last message. One turn is
                # thin, but it is the difference between knowing they just texted
                # and knowing nothing at all.
                return [convos[0]]
            merged.sort(key=lambda m: str(m.get("dateAdded") or ""))
            return merged[-limit:]
    except Exception as e:  # noqa: BLE001 — degrade, never block the call
        log.warning("get_recent_texts failed for %s: %s", contact_id, e)
        return []


async def get_assigned_user(contact_id: str, crm_key: str = "main") -> dict | None:
    """The GHL user a contact is assigned to, as {"id", "name"}. Never raises.

    Used to @-mention the contact's owner on an internal comment, which is what
    makes GHL notify them. Returns None whenever the answer is not certain — an
    unassigned contact, a lookup failure, a user record without a name — and the
    caller posts the comment without a mention rather than not posting it.
    """
    crm = CRMS.get(crm_key)
    if not crm or not contact_id:
        return None
    headers = {"Authorization": f"Bearer {crm['pit']}", "Version": "2021-07-28"}
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{GHL_BASE}/contacts/{contact_id}", headers=headers, timeout=5.0
            )
            if r.status_code != 200:
                return None
            user_id = ((r.json() or {}).get("contact") or {}).get("assignedTo") or ""
            if not user_id:
                return None

            r2 = await client.get(
                f"{GHL_BASE}/users/{user_id}", headers=headers, timeout=5.0
            )
            if r2.status_code != 200:
                return None
            u = (r2.json() or {})
            u = u.get("user") or u
            name = (u.get("name") or "").strip() or " ".join(
                x for x in [(u.get("firstName") or "").strip(),
                            (u.get("lastName") or "").strip()] if x
            ).strip()
            if not name:
                return None
            return {"id": user_id, "name": name}
    except Exception as e:  # noqa: BLE001 — degrade to no mention, never block
        log.warning("get_assigned_user failed for %s: %s", contact_id, e)
        return None


async def post_internal_comment(
    contact_id: str,
    crm_key: str,
    message: str,
    mentions: list[str] | None = None,
    user_id: str = "",
) -> bool:
    """Post a private internal comment into a contact's conversation thread.

    An internal comment is NOT a contact note — it lands inline in the
    conversation next to the texts and calls, and the contact never sees it.

    Mention markup is the caller's job: GHL wants the name and the id inline in
    the message as `@Name<userId>abc123</userId>` AND the bare id repeated in
    `mentions`. Sending one without the other renders as literal text or notifies
    nobody.

    Contract verified against live GHL 2026-09-09 with an unresolvable contactId,
    so nothing was written: the main PIT already carries conversations scope (no
    401/403), `InternalComment` is accepted as a type (it fails identically to a
    known-good `SMS` control, i.e. on the contact and nothing else), and the
    documented-as-required `mentions` and `status` are in fact optional — a
    minimal body clears validation. `status` is therefore not sent.

    Non-2xx bodies are logged verbatim, not summarised: this endpoint is new to
    the repo, and the error body is the only schema oracle we have.
    """
    crm = CRMS.get(crm_key)
    if not crm or not contact_id or not message:
        return False
    payload: dict = {
        "type": "InternalComment",
        "contactId": contact_id,
        "message": message,
    }
    if mentions:
        payload["mentions"] = mentions
    if user_id:
        payload["userId"] = user_id
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{GHL_BASE}/conversations/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {crm['pit']}",
                "Version": "2021-07-28",
                "Content-Type": "application/json",
            },
            timeout=8.0,
        )
        if r.status_code not in (200, 201):
            log.error(
                "post_internal_comment HTTP %s for contact %s: %s",
                r.status_code, contact_id, r.text[:500],
            )
            return False
        return True
