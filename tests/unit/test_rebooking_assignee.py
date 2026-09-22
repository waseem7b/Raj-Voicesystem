"""Rebooking calls book with Jon, and only with Jon.

The Video Call Calendar is round_robin across Waseem (priority 0) and Jon
(priority 1). Measured against the live calendar on 2026-09-10: the pool returns
26 free slots over 7 days, Jon 12, Waseem 26 — Jon's set is a strict SUBSET. So
an unfiltered rebooking call reads out times where more than half cannot be
honoured by the person who is supposed to take the meeting.

Two halves, and both are needed:
  1. the SLOTS we offer are filtered to Jon's availability
  2. the WRITE pins assignedUserId, because a round-robin calendar will
     otherwise assign whoever it likes — and a *move* keeps whatever assignee
     the appointment already had

Scoped to the rebooking flow: confirmation and reminder calls are about a
booking that already exists and must not be re-pointed.
"""
import pytest

from src.agent import ghl


def test_jon_is_the_rebooking_assignee():
    # Guards the id itself. A wrong one here silently assigns every rebooked
    # demo to the wrong person, and nothing would fail loudly.
    assert ghl.REBOOKING_ASSIGNED_USER_ID == "jUagFOxhVAtBf1f0fTSe"


# ── slots: userId reaches the free-slots query ───────────────────────────────

class _CaptureResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {}


class _CaptureClient:
    """Records the params of the last GET so the query can be asserted."""

    last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        type(self).last_params = params
        return _CaptureResponse()

    async def post(self, url, json=None, headers=None, timeout=None):
        type(self).last_params = json
        return _CaptureResponse()

    async def put(self, url, json=None, headers=None, timeout=None):
        type(self).last_params = json
        return _CaptureResponse()


@pytest.fixture
def capture(monkeypatch):
    _CaptureClient.last_params = None
    monkeypatch.setattr(ghl.httpx, "AsyncClient", lambda *a, **kw: _CaptureClient())
    return _CaptureClient


@pytest.mark.asyncio
async def test_user_id_is_sent_as_a_free_slots_filter(capture):
    await ghl.get_available_slots(user_id=ghl.REBOOKING_ASSIGNED_USER_ID)
    assert capture.last_params["userId"] == ghl.REBOOKING_ASSIGNED_USER_ID


@pytest.mark.asyncio
async def test_no_user_id_means_the_pooled_availability(capture):
    # Every other flow relies on this. An inbound booking should still see the
    # whole team's calendar.
    await ghl.get_available_slots()
    assert "userId" not in capture.last_params


@pytest.mark.asyncio
async def test_the_timezone_filter_still_works_alongside_it(capture):
    await ghl.get_available_slots(timezone="America/New_York", user_id="u1")
    assert capture.last_params["timezone"] == "America/New_York"
    assert capture.last_params["userId"] == "u1"


# ── writes: assignedUserId reaches the appointment payload ───────────────────

@pytest.mark.asyncio
async def test_booking_pins_the_assignee(capture):
    await ghl.book_video_call(
        "2026-09-15T14:00:00-04:00", "c1", assigned_user_id=ghl.REBOOKING_ASSIGNED_USER_ID
    )
    assert capture.last_params["assignedUserId"] == ghl.REBOOKING_ASSIGNED_USER_ID


@pytest.mark.asyncio
async def test_booking_without_an_assignee_leaves_it_to_the_round_robin(capture):
    await ghl.book_video_call("2026-09-15T14:00:00-04:00", "c1")
    assert "assignedUserId" not in capture.last_params


@pytest.mark.asyncio
async def test_a_MOVE_also_re_pins_the_assignee(capture):
    # The half that is easy to miss. Rescheduling keeps whatever assignee the
    # appointment already carried, so a demo originally round-robined to Waseem
    # would stay on his calendar even after Ashley moved it to a Jon-only slot.
    await ghl.reschedule_appointment(
        "appt1", "2026-09-15T14:00:00-04:00", assigned_user_id=ghl.REBOOKING_ASSIGNED_USER_ID
    )
    assert capture.last_params["assignedUserId"] == ghl.REBOOKING_ASSIGNED_USER_ID


@pytest.mark.asyncio
async def test_a_move_without_an_assignee_does_not_touch_it(capture):
    await ghl.reschedule_appointment("appt1", "2026-09-15T14:00:00-04:00")
    assert "assignedUserId" not in capture.last_params


@pytest.mark.asyncio
async def test_the_write_still_targets_the_video_call_calendar(capture):
    # Pinning the assignee must not disturb which calendar we book onto.
    await ghl.book_video_call("2026-09-15T14:00:00-04:00", "c1", assigned_user_id="u1")
    assert capture.last_params["calendarId"] == ghl.VIDEO_CALL_CALENDAR_ID
