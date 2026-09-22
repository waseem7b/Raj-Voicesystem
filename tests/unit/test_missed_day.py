"""The rebooking opener names the call they missed: "You weren't able to jump on
our call last Monday." (owner's rebooking script, 2026-09-14)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.agent import ghl
from src.webhooks import ghl_trigger

CAL = ghl.CONFIRM_CALENDAR_ID
NY = ZoneInfo("America/New_York")


def ev(days_ago: float, status: str, calendar: str = CAL, updated_hours_ago: float | None = None) -> dict:
    start = datetime.now(NY) - timedelta(days=days_ago)
    e = {"calendarId": calendar, "appointmentStatus": status, "startTime": start.isoformat()}
    if updated_hours_ago is not None:
        e["dateUpdated"] = (datetime.now(NY) - timedelta(hours=updated_hours_ago)).isoformat()
    return e


# ── picking the missed call ─────────────────────────────────────────────────

def test_picks_the_most_recent_past_no_show_or_cancel():
    older, newer = ev(5, "noshow"), ev(1, "cancelled")
    assert ghl._select_last_missed_video_appointment([older, newer, ev(3, "confirmed")]) is newer


def test_the_most_recently_cancelled_booking_wins_even_if_it_has_not_started():
    """Call 01a0a257: tonight's booking was cancelled this evening; she named the
    older cancel from last Friday instead."""
    friday = ev(3, "cancelled", updated_hours_ago=6)
    tonight = ev(-0.1, "cancelled", updated_hours_ago=0.1)
    assert ghl._select_last_missed_video_appointment([tonight, friday]) is tonight


def test_the_latest_change_beats_the_latest_start():
    older_start_just_marked = ev(2, "noshow", updated_hours_ago=1)
    newer_start_marked_earlier = ev(1, "cancelled", updated_hours_ago=20)
    assert ghl._select_last_missed_video_appointment(
        [older_start_just_marked, newer_start_marked_earlier]) is older_start_just_marked


def test_deleted_events_are_ignored():
    gone = {**ev(1, "cancelled", updated_hours_ago=1), "deleted": True}
    assert ghl._select_last_missed_video_appointment([gone]) is None


@pytest.mark.parametrize("days_ago", [-8, 8])
def test_more_than_a_week_either_side_is_ignored(days_ago):
    assert ghl._select_last_missed_video_appointment([ev(days_ago, "cancelled", updated_hours_ago=1)]) is None


def test_a_naive_update_time_is_read_in_the_account_timezone():
    assert ghl._event_updated({"dateUpdated": "2026-09-14 13:05:46"}).tzinfo is not None


def test_other_calendars_are_ignored():
    assert ghl._select_last_missed_video_appointment([ev(1, "noshow", calendar="other")]) is None


def test_nothing_missed_returns_none():
    assert ghl._select_last_missed_video_appointment([ev(1, "confirmed")]) is None


def test_a_naive_start_is_read_in_the_account_timezone():
    e = {"calendarId": CAL, "appointmentStatus": "noshow", "startTime": "2026-09-14 17:00:00"}
    assert ghl._event_start(e).tzinfo is not None


# ── how she says it ─────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 17, 15, 0, tzinfo=NY)  # a Thursday


def phrase(missed: datetime) -> str:
    return ghl_trigger._missed_day_phrase(
        {"missed_start_iso": missed.isoformat(), "timezone": "America/New_York"}, now=NOW)


def test_same_day_is_earlier_today():
    assert phrase(NOW - timedelta(hours=3)) == "earlier today"


def test_a_booking_later_today_cancelled_before_it_started_is_today():
    assert phrase(NOW + timedelta(hours=3)) == "today"


def test_a_booking_cancelled_for_a_later_day_leaves_the_day_out():
    assert phrase(NOW + timedelta(days=1)) == ""


def test_one_day_back_is_yesterday():
    assert phrase(NOW - timedelta(days=1)) == "yesterday"


def test_within_the_week_names_the_day():
    assert phrase(datetime(2026, 9, 14, 17, 0, tzinfo=NY)) == "last Monday"


@pytest.mark.parametrize("days", [7, 30])
def test_a_week_or_more_leaves_the_day_out(days):
    assert phrase(NOW - timedelta(days=days)) == ""


def test_no_missed_call_leaves_the_day_out():
    assert ghl_trigger._missed_day_phrase({}, now=NOW) == ""


def test_an_unreadable_date_leaves_the_day_out():
    assert ghl_trigger._missed_day_phrase({"missed_start_iso": "not a date"}, now=NOW) == ""


def test_the_day_is_the_prospects_not_utc():
    """21:30 Pacific on Monday is already Tuesday in UTC."""
    missed = datetime(2026, 9, 14, 21, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    now = datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    out = ghl_trigger._missed_day_phrase(
        {"missed_start_iso": missed.isoformat(), "timezone": "America/Los_Angeles"}, now=now)
    assert out == "last Monday"
