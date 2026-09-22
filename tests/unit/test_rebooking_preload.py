"""Everything the rebooking call needs is loaded before dialling (owner, 2026-09-14).

Two failures drove it. On call 01a0a1fd the in-call slot tool ran the texts-CRM phone
lookup, replaced the main-CRM contact with a Texts CRM #1 one, and the booking was
rejected (HTTP 422 "The contact id is invalid."). And Ashley read weekdays off the
calendar ("Tuesday") when the prospect's day was "tomorrow".
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from src.webhooks import server  # first: importing ghl_trigger before server leaves its routes unmounted
from src.webhooks import ghl_trigger, outbound

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=NY)  # Monday evening

SLOTS = {
    "2026-09-15": {"slots": ["2026-09-15T10:00:00-04:00", "2026-09-15T11:00:00-04:00"]},
    "2026-09-17": {"slots": ["2026-09-17T13:00:00-04:00"]},
    "traceId": "abc",
}


def test_days_are_named_relative_to_the_prospects_today():
    text, _ = ghl_trigger._format_preloaded_slots(SLOTS, "America/New_York", now=NOW)
    lines = text.splitlines()
    assert lines[1] == ("tomorrow (Tuesday Sep 15): 10:00am [iso:2026-09-15T10:00:00-04:00], "
                        "11:00am [iso:2026-09-15T11:00:00-04:00]")
    assert lines[3].startswith("Thursday (Thursday Sep 17): 1:00pm")


def test_every_day_of_the_week_gets_a_line_and_empty_days_say_so():
    """Call 01a0a622: Wednesday had no line, so "tomorrow" was a gap she filled with
    today's slot. Now every day is on the list, empty ones marked."""
    text, _ = ghl_trigger._format_preloaded_slots(SLOTS, "America/New_York", now=NOW)
    lines = text.splitlines()
    assert len(lines) == 8
    assert lines[0] == "today (Monday Sep 14): no openings"
    assert lines[2] == "Wednesday (Wednesday Sep 16): no openings"
    assert lines[7] == "next Monday (Monday Sep 21): no openings"


def test_the_prospects_clock_is_written_out():
    line = ghl_trigger._current_day_line("America/New_York", now=NOW)
    assert line == "It is 6:00pm on Monday, September 14. Tomorrow is Tuesday, September 15."


def test_the_clock_is_the_prospects_not_utc():
    """9:30pm Pacific on Monday is already Tuesday in UTC."""
    now = datetime(2026, 9, 14, 21, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert ghl_trigger._current_day_line("America/Los_Angeles", now=now).startswith(
        "It is 9:30pm on Monday, September 14.")


def test_same_day_is_today():
    text, _ = ghl_trigger._format_preloaded_slots(
        {"d": {"slots": ["2026-09-14T20:00:00-04:00"]}}, "America/New_York", now=NOW)
    assert text.startswith("today (Monday Sep 14): 8:00pm [iso:2026-09-14T20:00:00-04:00]\ntomorrow (Tuesday Sep 15): no openings")


def test_times_already_passed_are_dropped():
    text, offered = ghl_trigger._format_preloaded_slots(
        {"d": {"slots": ["2026-09-14T17:00:00-04:00", "2026-09-14T20:00:00-04:00"]}},
        "America/New_York", now=NOW)
    assert text.splitlines()[0] == "today (Monday Sep 14): 8:00pm [iso:2026-09-14T20:00:00-04:00]"
    assert offered == {"2026-09-14T20:00:00-04:00"}


def test_a_week_out_is_next_weekday_not_this_one():
    text, _ = ghl_trigger._format_preloaded_slots(
        {"d": {"slots": ["2026-09-21T10:00:00-04:00"]}}, "America/New_York", now=NOW)
    assert text.splitlines()[7].startswith("next Monday (Monday Sep 21): 10:00am")


def test_every_listed_time_is_marked_as_offered():
    _, offered = ghl_trigger._format_preloaded_slots(SLOTS, "America/New_York", now=NOW)
    assert offered == {"2026-09-15T10:00:00-04:00", "2026-09-15T11:00:00-04:00",
                       "2026-09-17T13:00:00-04:00"}


def test_no_slots_is_blank():
    assert ghl_trigger._format_preloaded_slots({}, "America/New_York", now=NOW) == ("", set())


def test_a_metadata_seeded_contact_is_final():
    """The texts-CRM phone lookup must never replace the contact the call was placed for."""
    entry = {"contact": None, "lookup_done": False}
    message = {"call": {"metadata": {"ghl_contact_id": "main-1", "ghl_first_name": "Waseem",
                                     "call_type": "rebooking"}}}
    asyncio.run(outbound._seed_contact_from_metadata(entry, message))
    assert entry["contact"]["id"] == "main-1"
    assert entry["lookup_done"] is True


def test_seeded_call_skips_the_texts_crm_lookup(monkeypatch):
    called = []

    async def texts_lookup(*a, **kw):
        called.append(a)
        return {"id": "texts-1", "crm": "texts1"}

    monkeypatch.setattr(server.ghl, "lookup_contact_in_texts_crms", texts_lookup)
    call_id = "preload-skip-lookup"
    server._call_cache.pop(call_id, None)
    asyncio.run(ghl_trigger._seed_rebooking_call(
        call_id, {"contact_id": "main-1", "first_name": "Waseem"}, "", "+1555010010", set()))
    entry = asyncio.run(server._ensure_contact_loaded(call_id, "+1555010010"))
    assert entry["contact"]["id"] == "main-1"
    assert called == []
    server._call_cache.pop(call_id, None)


# ── the first offer, day named once (call 01a0a678, 2026-09-15) ─────────────

def test_first_offer_names_the_day_once():
    slots = {"d": {"slots": ["2026-09-17T07:00:00-04:00", "2026-09-17T08:00:00-04:00",
                             "2026-09-17T14:00:00-04:00"]}}
    assert ghl_trigger._first_offer_line(slots, "America/New_York", now=NOW) == \
        "How does Thursday at seven in the morning or two in the afternoon work for you?"


def test_first_offer_skips_a_day_without_both_halves():
    slots = {"a": {"slots": ["2026-09-15T20:00:00-04:00"]},
             "b": {"slots": ["2026-09-17T10:00:00-04:00", "2026-09-17T13:00:00-04:00"]}}
    assert ghl_trigger._first_offer_line(slots, "America/New_York", now=NOW) == \
        "How does Thursday at ten in the morning or one in the afternoon work for you?"


def test_first_offer_falls_back_across_days_and_names_each():
    slots = {"a": {"slots": ["2026-09-15T20:00:00-04:00"]},
             "b": {"slots": ["2026-09-17T10:00:00-04:00"]}}
    assert ghl_trigger._first_offer_line(slots, "America/New_York", now=NOW) == \
        "How does tomorrow at eight in the evening or Thursday at ten in the morning work for you?"


def test_first_offer_with_a_single_slot():
    slots = {"a": {"slots": ["2026-09-16T11:00:00-04:00"]}}
    assert ghl_trigger._first_offer_line(slots, "America/New_York", now=NOW) == \
        "How does Wednesday at eleven in the morning work for you?"


def test_first_offer_is_blank_with_no_slots():
    assert ghl_trigger._first_offer_line({}, "America/New_York", now=NOW) == ""


def test_first_offer_is_in_the_prospects_timezone():
    slots = {"d": {"slots": ["2026-09-17T07:00:00-07:00", "2026-09-17T14:00:00-07:00"]}}
    now = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert ghl_trigger._first_offer_line(slots, "America/Los_Angeles", now=now) == \
        "How does Thursday at seven in the morning or two in the afternoon work for you?"


def test_first_offer_says_a_shared_part_of_day_once():
    """Owner, 2026-09-15 (call 01a0a796): not "seven in the morning or ten in the morning"."""
    slots = {"a": {"slots": ["2026-09-18T07:00:00-04:00", "2026-09-18T10:00:00-04:00"]}}
    assert ghl_trigger._first_offer_line(slots, "America/New_York", now=NOW) ==         "How does Friday at seven or ten in the morning work for you?"


# ── one offer line per day (calls 01a0a796, 01a0a7e0) ──────────────────────

def test_day_offer_lines_one_per_day_in_the_owners_shape():
    slots = {"a": {"slots": ["2026-09-15T20:00:00-04:00"]},
             "b": {"slots": ["2026-09-18T07:00:00-04:00", "2026-09-18T10:00:00-04:00",
                             "2026-09-18T13:00:00-04:00"]},
             "c": {"slots": ["2026-09-17T07:00:00-04:00", "2026-09-17T10:00:00-04:00"]}}
    assert ghl_trigger._day_offer_lines(slots, "America/New_York", now=NOW).splitlines() == [
        "How does tomorrow at eight in the evening work for you?",
        "How does Thursday at seven or ten in the morning work for you?",
        "How does Friday at seven in the morning or one in the afternoon work for you?",
    ]


def test_day_offer_lines_blank_with_no_slots():
    assert ghl_trigger._day_offer_lines({}, "America/New_York", now=NOW) == ""


# ── more times on a day, by half of the day (call 01a0ab63, 2026-09-16) ─────

def test_more_times_lines_leave_the_day_out_and_say_the_part_once():
    """Call 01a0ab63: "what other times in the afternoon?" got "tomorrow at three in
    the afternoon or five in the afternoon" — day repeated, part said twice."""
    slots = {"a": {"slots": ["2026-09-15T11:00:00-04:00", "2026-09-15T15:00:00-04:00",
                             "2026-09-15T17:00:00-04:00"]}}
    lines = ghl_trigger._more_times_lines(slots, "America/New_York", now=NOW).splitlines()
    assert lines == [
        "[tomorrow morning] How about eleven in the morning?",
        "[tomorrow afternoon] How about three in the afternoon or five in the evening?",
    ]
    for line in lines:
        assert "tomorrow at" not in line


def test_more_times_lines_cover_every_day_with_openings():
    lines = ghl_trigger._more_times_lines(SLOTS, "America/New_York", now=NOW).splitlines()
    assert lines == [
        "[tomorrow morning] How about ten or eleven in the morning?",
        "[Thursday afternoon] How about one in the afternoon?",
    ]


def test_more_times_lines_blank_with_no_slots():
    assert ghl_trigger._more_times_lines({}, "America/New_York", now=NOW) == ""
