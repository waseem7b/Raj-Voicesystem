"""TDD — outbound demo-confirmation GHL helpers (Type 4).

  get_prospect_appointment(phone)
    Main-CRM contact lookup by phone + soonest upcoming Video-Call-Calendar
    appointment. Backs the Stage 1 mid-call lookup tool.

  reschedule_appointment(appointment_id, new_slot_iso)
    True reschedule of the same event record (PUT; endTime = start + 1h).

Outbound-confirmation only — these must NOT touch the inbound texts-CRM path.
All HTTP is mocked via httpx.MockTransport (same pattern as test_booking_hardening).
"""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agent import ghl


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)


CONTACT = {
    "id": "main-c1",
    "firstName": "Dan",
    "companyName": "Ridgeline Floors",
    "city": "Spokane",
    "state": "WA",
    "timezone": "America/Los_Angeles",
    "email": "dan@ridgeline.com",
    "phone": "+15551234567",
}


def _iso(days: int, hour: int = 14) -> str:
    dt = datetime.now(timezone.utc).astimezone() + timedelta(days=days)
    return dt.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# get_prospect_appointment
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_returns_upcoming_video_appointment(monkeypatch):
    start = _iso(days=1, hour=14)
    events = [{
        "id": "appt-1",
        "calendarId": ghl.CONFIRM_CALENDAR_ID,
        "startTime": start,
        "endTime": start,
        "appointmentStatus": "confirmed",
    }]

    def handler(request):
        if request.url.path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={"contact": CONTACT})
        if request.url.path.endswith("/appointments"):
            assert request.headers.get("Version") == "2023-02-21"
            assert request.url.path.endswith("/contacts/main-c1/appointments")
            return httpx.Response(200, json={"events": events})
        return httpx.Response(404, json={})

    _patch_client(monkeypatch, handler)
    out = await ghl.get_prospect_appointment("+15551234567")

    assert out["ok"] is True
    assert out["contact_id"] == "main-c1"
    assert out["first_name"] == "Dan"
    assert out["business"] == "Ridgeline Floors"
    assert out["timezone"] == "America/Los_Angeles"
    assert out["appointment_id"] == "appt-1"
    assert out["start_iso"] == start


@pytest.mark.asyncio
async def test_picks_soonest_upcoming(monkeypatch):
    soon = _iso(days=1, hour=9)
    later = _iso(days=3, hour=9)
    events = [
        {"id": "later", "calendarId": ghl.CONFIRM_CALENDAR_ID,
         "startTime": later, "appointmentStatus": "confirmed"},
        {"id": "soon", "calendarId": ghl.CONFIRM_CALENDAR_ID,
         "startTime": soon, "appointmentStatus": "confirmed"},
    ]

    def handler(request):
        if request.url.path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={"contact": CONTACT})
        return httpx.Response(200, json={"events": events})

    _patch_client(monkeypatch, handler)
    out = await ghl.get_prospect_appointment("+15551234567")
    assert out["appointment_id"] == "soon"


@pytest.mark.asyncio
async def test_ignores_past_cancelled_and_other_calendars(monkeypatch):
    events = [
        {"id": "past", "calendarId": ghl.CONFIRM_CALENDAR_ID,
         "startTime": _iso(days=-2), "appointmentStatus": "confirmed"},
        {"id": "cancelled", "calendarId": ghl.CONFIRM_CALENDAR_ID,
         "startTime": _iso(days=2), "appointmentStatus": "cancelled"},
        {"id": "other-cal", "calendarId": "SOME_OTHER_CALENDAR",
         "startTime": _iso(days=2), "appointmentStatus": "confirmed"},
    ]

    def handler(request):
        if request.url.path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={"contact": CONTACT})
        return httpx.Response(200, json={"events": events})

    _patch_client(monkeypatch, handler)
    out = await ghl.get_prospect_appointment("+15551234567")
    assert out["ok"] is False
    assert out["reason"] == "no_appointment"
    assert out["contact_id"] == "main-c1"  # contact found, just no live appt


@pytest.mark.asyncio
async def test_no_contact_match(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={})  # no contact key
        return httpx.Response(200, json={"events": []})

    _patch_client(monkeypatch, handler)
    out = await ghl.get_prospect_appointment("+15550000000")
    assert out["ok"] is False
    assert out["reason"] == "no_contact"


# --------------------------------------------------------------------------- #
# reschedule_appointment
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reschedule_puts_new_slot_one_hour(monkeypatch):
    captured = {}
    start = "2026-06-10T15:00:00-07:00"

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["version"] = request.headers.get("Version")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "appt-1", "startTime": start})

    _patch_client(monkeypatch, handler)
    out = await ghl.reschedule_appointment("appt-1", start)

    assert out["ok"] is True
    assert captured["method"] == "PUT"
    assert captured["path"].endswith("/calendars/events/appointments/appt-1")
    # reschedule moves the event onto the Video Call Calendar
    assert captured["body"]["calendarId"] == ghl.VIDEO_CALL_CALENDAR_ID
    s = datetime.fromisoformat(captured["body"]["startTime"])
    e = datetime.fromisoformat(captured["body"]["endTime"])
    assert (e - s).total_seconds() == 3600


@pytest.mark.asyncio
async def test_reschedule_returns_error_on_failure(monkeypatch):
    def handler(request):
        return httpx.Response(422, text="bad slot")

    _patch_client(monkeypatch, handler)
    out = await ghl.reschedule_appointment("appt-1", "2026-06-10T15:00:00-07:00")
    assert out["ok"] is False
    assert "422" in out["error"]
