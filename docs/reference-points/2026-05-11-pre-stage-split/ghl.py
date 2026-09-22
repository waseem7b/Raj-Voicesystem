"""GHL API client — caller lookup + calendar booking."""
import asyncio
import os
import time
from datetime import datetime, timedelta

import httpx

GHL_BASE = "https://services.leadconnectorhq.com"

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


async def get_available_slots(days_ahead: int = 7, timezone: str = "") -> dict:
    """Fetch open slots from the Video Call Calendar for the next N days.

    When `timezone` is a non-empty IANA name (e.g. 'America/Los_Angeles'),
    GHL returns slot ISOs already in that zone — no client-side conversion
    needed. When blank, GHL falls back to the calendar's configured zone.
    """
    crm = CRMS["main"]
    pit = crm["pit"]
    start_ms = int(time.time() * 1000)
    end_ms = start_ms + (days_ahead * 86400 * 1000)
    params: dict[str, str | int] = {"startDate": start_ms, "endDate": end_ms}
    if timezone:
        params["timezone"] = timezone
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{GHL_BASE}/calendars/{VIDEO_CALL_CALENDAR_ID}/free-slots",
            params=params,
            headers={
                "Authorization": f"Bearer {pit}",
                "Version": "2021-04-15",
            },
            timeout=6.0,
        )
        r.raise_for_status()
        return r.json()


async def book_video_call(slot_iso: str, contact_id: str, email: str = None) -> dict:
    """Book an appointment on the Video Call Calendar.
    slot_iso: ISO 8601 datetime with offset, e.g. 2026-05-07T14:00:00-05:00
    """
    crm = CRMS["main"]
    pit = crm["pit"]
    location_id = crm["location_id"]
    end_dt = (datetime.fromisoformat(slot_iso) + timedelta(minutes=30)).isoformat()
    payload = {
        "calendarId": VIDEO_CALL_CALENDAR_ID,
        "locationId": location_id,
        "contactId": contact_id,
        "startTime": slot_iso,
        "endTime": end_dt,
        "title": "Video Demo Call",
        "appointmentStatus": "confirmed",
        "ignoreDateRange": False,
        "toNotify": True,
    }
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
