"""TDD — outbound demo-confirmation webhook (Type 4).

Covers the SEPARATE outbound path:
  - _normalize_spoken_phone: spoken/typed number -> E.164
  - _handle_get_prospect_appointment: lookup + cache seed + structured result
  - _handle_reschedule_video_call: offered-slot validation + reschedule
  - POST /vapi/outbound/tool route smoke

Import server BEFORE outbound so server mounts outbound's router (server imports
outbound at its bottom, after all shared helpers are defined — no import cycle).
"""
from unittest.mock import AsyncMock

import pytest

from src.webhooks import server, outbound


@pytest.fixture(autouse=True)
def _clear_cache():
    server._call_cache.clear()
    yield
    server._call_cache.clear()


# --------------------------------------------------------------------------- #
# _normalize_spoken_phone
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("(555) 123-4567", "+15551234567"),
    ("5551234567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+1 555 123 4567", "+15551234567"),
    ("555.123.4567", "+15551234567"),
    ("123", None),
    ("", None),
    (None, None),
])
def test_normalize_spoken_phone(raw, expected):
    assert outbound._normalize_spoken_phone(raw) == expected


# --------------------------------------------------------------------------- #
# get_prospect_appointment handler
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_prospect_appointment_seeds_cache_on_hit(monkeypatch):
    out = {
        "ok": True, "contact_id": "main-c1", "first_name": "Dan",
        "business": "Ridgeline", "city": "Spokane", "state": "WA",
        "timezone": "America/Los_Angeles", "appointment_id": "appt-1",
        "start_iso": "2026-06-05T14:00:00-07:00", "status": "confirmed",
    }
    spy = AsyncMock(return_value=out)
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", spy)

    res = await outbound._handle_get_prospect_appointment(
        {"phone_number": "(555) 123-4567"}, "c1", None)

    assert "appointment_found=true" in res
    assert "start_iso=2026-06-05T14:00:00-07:00" in res
    spy.assert_awaited_once_with("+15551234567")  # normalized to E.164

    entry = server._call_cache["c1"]
    assert entry["confirm_flow"] is True
    assert entry["lookup_done"] is True
    assert entry["contact"]["id"] == "main-c1"
    assert entry["contact"]["crm"] == "main"
    assert entry["contact"]["timezone"] == "America/Los_Angeles"
    assert entry["appointment"]["appointment_id"] == "appt-1"


@pytest.mark.asyncio
async def test_get_prospect_appointment_bad_number_skips_lookup(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", spy)

    res = await outbound._handle_get_prospect_appointment({"phone_number": "12"}, "c2", None)

    assert "appointment_found=false" in res
    assert "bad_number" in res
    spy.assert_not_awaited()
    assert server._call_cache["c2"]["confirm_outcome"] == "bad_number"


@pytest.mark.asyncio
async def test_get_prospect_appointment_no_appointment(monkeypatch):
    out = {"ok": False, "reason": "no_appointment", "contact_id": "main-c1", "first_name": "Dan"}
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", AsyncMock(return_value=out))

    res = await outbound._handle_get_prospect_appointment({"phone_number": "5551234567"}, "c3", None)

    assert "appointment_found=false" in res
    assert "no_appointment" in res
    assert server._call_cache["c3"]["confirm_outcome"] == "no_appointment"


# --------------------------------------------------------------------------- #
# reschedule_video_call handler
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_reschedule_happy_path(monkeypatch):
    entry = await server._cache_get("c4")
    entry["appointment"] = {"appointment_id": "appt-1", "start_iso": "old"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    spy = AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-1"}})
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", spy)

    res = await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-10T15:00:00-07:00"}, "c4", None)

    assert "rescheduled=true" in res
    # No call_type on this entry, so it is not a rebooking call and the
    # assignee stays empty — the calendar's round-robin decides, as before.
    spy.assert_awaited_once_with(
        "appt-1", "2026-06-10T15:00:00-07:00", assigned_user_id=""
    )
    assert server._call_cache["c4"]["confirm_outcome"] == "rescheduled"


@pytest.mark.asyncio
async def test_reschedule_rejects_unoffered_slot(monkeypatch):
    entry = await server._cache_get("c5")
    entry["appointment"] = {"appointment_id": "appt-1"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", spy)

    res = await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-11T09:00:00-07:00"}, "c5", None)  # not offered

    assert "get_available_slots" in res
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_reschedule_books_new_when_contact_known_but_nothing_booked(monkeypatch):
    """A recognised contact with no appointment gets one CREATED, not refused —
    that is the `no_appointment` branch of the lookup (owner ruling 2026-08-19:
    "we need to find a booking"). The model still calls one tool; which GHL
    operation that becomes is decided server-side."""
    entry = await server._cache_get("c6")
    entry["contact"] = {"id": "main-c6", "crm": "main"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    resched = AsyncMock()
    book = AsyncMock(return_value={"ok": True, "appointment_id": "appt-new"})
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", resched)
    monkeypatch.setattr(outbound.ghl, "book_video_call", book)

    res = await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-10T15:00:00-07:00"}, "c6", None)

    assert "booked=true" in res
    book.assert_awaited_once_with(
        "2026-06-10T15:00:00-07:00", "main-c6", assigned_user_id=""
    )
    resched.assert_not_awaited()
    assert server._call_cache["c6"]["confirm_outcome"] == "booked"


@pytest.mark.asyncio
async def test_a_REBOOKING_call_pins_the_move_to_jon(monkeypatch):
    """The scoping test. Rebooking books with Jon only (owner, 2026-09-10);
    every other flow keeps the round-robin."""
    entry = await server._cache_get("c4-jon")
    entry["call_type"] = "rebooking"
    entry["appointment"] = {"appointment_id": "appt-9"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    spy = AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-9"}})
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", spy)

    await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-10T15:00:00-07:00"}, "c4-jon", None)

    spy.assert_awaited_once_with(
        "appt-9", "2026-06-10T15:00:00-07:00",
        assigned_user_id=outbound.ghl.REBOOKING_ASSIGNED_USER_ID,
    )


@pytest.mark.asyncio
async def test_a_REBOOKING_call_pins_a_new_booking_to_jon(monkeypatch):
    entry = await server._cache_get("c6-jon")
    entry["call_type"] = "rebooking"
    entry["contact"] = {"id": "main-c6", "crm": "main"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    book = AsyncMock(return_value={"ok": True, "appointment_id": "appt-new"})
    monkeypatch.setattr(outbound.ghl, "book_video_call", book)

    await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-10T15:00:00-07:00"}, "c6-jon", None)

    book.assert_awaited_once_with(
        "2026-06-10T15:00:00-07:00", "main-c6",
        assigned_user_id=outbound.ghl.REBOOKING_ASSIGNED_USER_ID,
    )


@pytest.mark.asyncio
async def test_a_CONFIRMATION_call_does_not_get_pinned(monkeypatch):
    entry = await server._cache_get("c4-conf")
    entry["call_type"] = "confirmation"
    entry["appointment"] = {"appointment_id": "appt-c"}
    entry["offered_slots"] = {"2026-06-10T15:00:00-07:00"}
    spy = AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-c"}})
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", spy)

    await outbound._handle_reschedule_video_call(
        {"slot_iso": "2026-06-10T15:00:00-07:00"}, "c4-conf", None)

    spy.assert_awaited_once_with(
        "appt-c", "2026-06-10T15:00:00-07:00", assigned_user_id=""
    )


@pytest.mark.asyncio
async def test_reschedule_refuses_when_contact_unknown(monkeypatch):
    """No appointment AND no contact = we cannot identify who answered, so
    booking would put a stranger on the closer's calendar."""
    entry = await server._cache_get("c6b")
    entry["offered_slots"] = {"2099-06-10T15:00:00-07:00"}  # future; skips the past-slot guard
    resched = AsyncMock()
    book = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "reschedule_appointment", resched)
    monkeypatch.setattr(outbound.ghl, "book_video_call", book)

    res = await outbound._handle_reschedule_video_call(
        {"slot_iso": "2099-06-10T15:00:00-07:00"}, "c6b", None)

    assert "no_contact_on_file" in res
    resched.assert_not_awaited()
    book.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Route smoke — the separate /vapi/outbound/tool endpoint exists and dispatches
# --------------------------------------------------------------------------- #
def test_outbound_tool_route_dispatches(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment",
                        AsyncMock(return_value={"ok": False, "reason": "no_contact"}))
    client = TestClient(server.app)
    body = {"message": {
        "type": "tool-calls",
        "call": {"id": "rt1", "customer": {"number": "+15550001111"}},
        "toolCallList": [{"id": "t1", "function": {
            "name": "get_prospect_appointment",
            "arguments": '{"phone_number":"5551234567"}'}}],
    }}
    r = client.post("/vapi/outbound/tool", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["toolCallId"] == "t1"
    assert "appointment_found=false" in data["results"][0]["result"]


# --------------------------------------------------------------------------- #
# Fixes from the first live Ashley call (2026-08-19, call 01a01c9f)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_lookup_uses_call_number_when_model_passes_no_args(monkeypatch):
    """The regression that broke the first real call: the model has no way to
    know the prospect's number, so requiring it made it skip the tool and invent
    a booked time. With no args it must fall back to the call's own number."""
    out = {"ok": True, "contact_id": "main-x", "first_name": "Dana",
           "appointment_id": "appt-x", "start_iso": "2026-08-21T14:00:00-04:00",
           "timezone": "America/New_York", "status": "confirmed"}
    spy = AsyncMock(return_value=out)
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", spy)

    res = await outbound._handle_get_prospect_appointment({}, "cA", "+1555010013")

    assert "appointment_found=true" in res
    spy.assert_awaited_once_with("+1555010013")


@pytest.mark.asyncio
async def test_lookup_returns_local_greeting(monkeypatch):
    """Greeting comes from the PROSPECT's timezone, not Vapi's UTC clock —
    which produced "good afternoon" at 8:43pm on the first live call."""
    out = {"ok": True, "contact_id": "main-y", "first_name": "Sam",
           "appointment_id": "appt-y", "start_iso": "2026-08-21T14:00:00-04:00",
           "timezone": "America/New_York", "status": "confirmed"}
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", AsyncMock(return_value=out))

    res = await outbound._handle_get_prospect_appointment({}, "cB", "+1555010013")

    assert "greeting=" in res
    greeting = res.split("greeting=")[1].split(";")[0].strip()
    assert greeting in {"morning", "afternoon", "evening"}


@pytest.mark.asyncio
async def test_no_appointment_seeds_contact_so_rebooking_can_book(monkeypatch):
    out = {"ok": False, "reason": "no_appointment", "contact_id": "main-z",
           "first_name": "Alex", "timezone": "America/New_York"}
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", AsyncMock(return_value=out))

    res = await outbound._handle_get_prospect_appointment({}, "cC", "+1555010013")

    assert "reason=no_appointment" in res
    assert server._call_cache["cC"]["contact"]["id"] == "main-z"


@pytest.mark.asyncio
async def test_no_contact_seeds_nothing(monkeypatch):
    """An unrecognised number must NOT seed a contact — otherwise the rebooking
    branch would book a stranger onto the closer's calendar."""
    out = {"ok": False, "reason": "no_contact"}
    monkeypatch.setattr(outbound.ghl, "get_prospect_appointment", AsyncMock(return_value=out))

    res = await outbound._handle_get_prospect_appointment({}, "cD", "+1555010013")

    assert "reason=no_contact" in res
    # The cache pre-seeds `contact: None`; what matters is that no contact was
    # populated, so the rebooking branch has nobody to book for.
    assert not server._call_cache["cD"].get("contact")


# --------------------------------------------------------------------------- #
# Spoken time must be the PROSPECT's clock, not the agency's
# (live call 01a02200: NY prospect told "nine at night" about a 10pm booking)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tz_name,expected", [
    ("America/Bogota",      "nine at night"),        # account tz — unchanged
    ("America/New_York",    "ten at night"),         # UTC-4: one hour later
    ("America/Los_Angeles", "seven in the evening"), # UTC-7: two hours earlier
])
def test_spoken_time_converts_to_prospect_timezone(tz_name, expected):
    # Naive string = 21:00 in the GHL account tz (Bogota).
    assert outbound._spoken_booked_time("2026-08-21T21:00:00", tz_name) == expected


def test_spoken_time_without_tz_keeps_account_clock():
    """No timezone = old behaviour, correct only for Bogota-local prospects."""
    assert outbound._spoken_booked_time("2026-08-21T21:00:00") == "nine at night"


def test_spoken_time_ignores_an_unusable_timezone():
    assert outbound._spoken_booked_time("2026-08-21T21:00:00", "Mars/Olympus") == "nine at night"


# --------------------------------------------------------------------------- #
# Call-outcome classification -> the GHL tag the show-up workflow branches on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ended_reason,confirm_outcome,expected", [
    # What happened IN the call wins over how it ended.
    ("customer-ended-call",            "confirmed",   None),               # mark_locked_in already tagged
    ("customer-ended-call",            "rescheduled", "call-rescheduled"),
    ("assistant-said-end-call-phrase", "booked",      "call-rescheduled"),
    # Every number comes from a CRM appointment, so a no-contact lookup is a
    # data problem, not an outcome — tagged as no-answer to keep the GHL
    # branches exhaustive (owner ruling 2026-08-21).
    ("assistant-ended-call",           "bad_number",  "call-no-answer"),
    ("customer-ended-call",            "not_interested", None),  # tagged in-call
    # Otherwise the ended reason decides.
    ("assistant-said-end-call-phrase", "",  "call-answered"),
    ("customer-ended-call",            "",  "call-answered"),
    ("voicemail",                      "",  "call-voicemail"),
    ("customer-did-not-answer",        "",  "call-no-answer"),
    ("customer-busy",                  "",  "call-no-answer"),
    ("twilio-failed-to-connect-call",  "",  "call-no-answer"),
    # Never connected = nobody was reached, whatever the specific error.
    ("call.start.error-vapi-number-outbound-daily-limit", "", "call-no-answer"),
    ("call.start.error-vapi-number-international",        "", "call-no-answer"),
])
def test_outcome_classification(ended_reason, confirm_outcome, expected):
    assert outbound._classify_outcome(ended_reason, confirm_outcome) == expected


@pytest.mark.asyncio
async def test_end_of_call_report_tags_the_contact(monkeypatch):
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)
    await outbound._tag_call_outcome({"message": {
        "type": "end-of-call-report",
        "endedReason": "customer-did-not-answer",
        "call": {"id": "cx1", "metadata": {"ghl_contact_id": "main-x"}},
    }})
    spy.assert_awaited_once_with("main-x", "main", "call-no-answer")


@pytest.mark.asyncio
async def test_confirmed_call_gets_no_outcome_tag(monkeypatch):
    """mark_locked_in already wrote `locked-in` during the call."""
    entry = await server._cache_get("cx2")
    entry["confirm_outcome"] = "confirmed"
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)
    await outbound._tag_call_outcome({"message": {
        "type": "end-of-call-report",
        "endedReason": "customer-ended-call",
        "call": {"id": "cx2", "metadata": {"ghl_contact_id": "main-x"}},
    }})
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_contact_means_no_tag(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)
    await outbound._tag_call_outcome({"message": {
        "type": "end-of-call-report", "endedReason": "voicemail",
        "call": {"id": "cx3", "metadata": {}},
    }})
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_not_interested_tags_and_records_outcome(monkeypatch):
    entry = await server._cache_get("ni1")
    entry["contact"] = {"id": "main-ni", "crm": "main"}
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)

    res = await outbound._handle_mark_not_interested({}, "ni1", None)

    assert "not_interested=true" in res
    spy.assert_awaited_once_with("main-ni", "main", "not-interested")
    assert server._call_cache["ni1"]["confirm_outcome"] == "not_interested"


@pytest.mark.asyncio
async def test_mark_not_interested_without_contact_is_safe(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)
    res = await outbound._handle_mark_not_interested({}, "ni2", None)
    assert "no_contact_on_file" in res
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_needs_reschedule_tags_and_records_outcome(monkeypatch):
    """Rescheduling is its own call-back (2026-09-04). The hand-off is a tag
    written in-call, so the workflow's wait reads it the same way it reads
    locked-in."""
    entry = await server._cache_get("nr1")
    entry["contact"] = {"id": "main-nr", "crm": "main"}
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)

    res = await outbound._handle_mark_needs_reschedule({}, "nr1", None)

    assert "needs_reschedule=true" in res
    spy.assert_awaited_once_with("main-nr", "main", "needs-reschedule")
    assert server._call_cache["nr1"]["confirm_outcome"] == "reschedule_requested"


@pytest.mark.asyncio
async def test_mark_needs_reschedule_without_contact_is_safe(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)
    res = await outbound._handle_mark_needs_reschedule({}, "nr2", None)
    assert "no_contact_on_file" in res
    spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_routes_mark_needs_reschedule(monkeypatch):
    spy = AsyncMock(return_value="needs_reschedule=true")
    monkeypatch.setattr(outbound, "_handle_mark_needs_reschedule", spy)
    out = await outbound._dispatch_outbound_tool(
        {"id": "tc1", "function": {"name": "mark_needs_reschedule", "arguments": "{}"}},
        "d1", None,
    )
    assert out == {"toolCallId": "tc1", "result": "needs_reschedule=true"}
    spy.assert_awaited_once()


def test_reschedule_requested_gets_no_end_of_call_tag():
    """Like locked-in and not-interested: written in-call, so the report
    must not write a second tag over it."""
    assert outbound._classify_outcome("assistant-said-end-call-phrase", "reschedule_requested") is None


def test_needs_reschedule_is_cleared_before_every_dial():
    """The rebooking call consumes the tag; a later confirmation attempt on a
    new appointment must start without it."""
    assert "needs-reschedule" in outbound.OUTCOME_TAGS


def test_bad_number_tag_is_retired():
    """Owner ruling: it cannot occur in this flow, and an unhandled tag would
    fall through every GHL branch."""
    assert "call-bad-number" not in outbound.OUTCOME_TAGS


# --------------------------------------------------------------------------- #
# mark_locked_in writes BOTH the CRM tag and the stage tag (owner 2026-08-21)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("call_type,stage_tag", [
    ("confirmation", "locked-in-confirmed"),
    ("reminder",     "locked-in-reminder"),
])
@pytest.mark.asyncio
async def test_locked_in_writes_crm_tag_and_stage_tag(monkeypatch, call_type, stage_tag):
    entry = await server._cache_get(f"li-{call_type}")
    entry["contact"] = {"id": "main-li", "crm": "main"}
    entry["call_type"] = call_type
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)

    res = await outbound._handle_mark_locked_in({}, f"li-{call_type}", None)

    assert "locked_in=true" in res
    written = [c.args[2] for c in spy.await_args_list]
    # `locked-in` is non-negotiable: applyLockInFromTags normalises to
    # "lockedin", so a stage-suffixed tag alone never matches and
    # calendar_events.locked_in_at is never stamped.
    assert "locked-in" in written
    assert stage_tag in written


@pytest.mark.asyncio
async def test_locked_in_still_tags_crm_without_a_known_stage(monkeypatch):
    entry = await server._cache_get("li-none")
    entry["contact"] = {"id": "main-li", "crm": "main"}
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "add_tag", spy)

    await outbound._handle_mark_locked_in({}, "li-none", None)

    written = [c.args[2] for c in spy.await_args_list]
    assert written == ["locked-in"]


# --------------------------------------------------------------------------- #
# Answered-vs-missed is decided by whether the prospect SPOKE, not by matching
# Vapi's endedReason strings (which are guesses until seen in a real call)
# --------------------------------------------------------------------------- #
_SPOKE = [{"role": "bot", "message": "Hey Ryan"}, {"role": "user", "message": "Hello"}]
_SILENT = [{"role": "bot", "message": "Hey Ryan"}]


def test_transcript_decides_answered():
    assert _cls("assistant-ended-call", _SPOKE) == "call-answered"


def test_transcript_decides_no_answer_even_on_an_answered_looking_reason():
    """The case that matters: Vapi reports a reason we would have read as
    'answered', but nobody ever spoke. Trusting the string would kill the retry."""
    assert _cls("assistant-ended-call", _SILENT) == "call-no-answer"


def test_unknown_reason_with_no_transcript_defaults_to_no_answer():
    """Safer default: a missed call wrongly marked answered stops the retry chain
    dead; a connected call wrongly marked missed costs one extra call."""
    assert outbound._classify_outcome("some-reason-vapi-invented", "", None) == "call-no-answer"


def test_voicemail_beats_the_transcript():
    """A machine answering is neither answered nor missed, and voicemail
    detection is more reliable than counting turns."""
    assert _cls("voicemail", _SPOKE) == "call-voicemail"


def test_in_call_actions_still_win():
    assert _cls("assistant-ended-call", _SILENT, "rescheduled") == "call-rescheduled"


def _cls(reason, messages, outcome=""):
    return outbound._classify_outcome(reason, outcome, messages)


# --------------------------------------------------------------------------- #
# An unknown location must not shift the stated time (live call 01a0309e:
# a 6:30pm booking was announced as "seven thirty")
# --------------------------------------------------------------------------- #
def test_no_location_means_no_conversion():
    out = {"timezone": "", "city": "", "state": ""}
    assert outbound._prospect_tz_if_known(out) == ""


def test_known_location_still_converts():
    out = {"timezone": "America/Los_Angeles", "city": "", "state": ""}
    assert outbound._prospect_tz_if_known(out) == "America/Los_Angeles"


def test_state_counts_as_known():
    out = {"timezone": "", "city": "", "state": "CA"}
    assert outbound._prospect_tz_if_known(out).startswith("America/")


def test_unknown_location_speaks_the_booked_time_as_stored():
    """18:30 on the account clock is announced as six thirty, not seven thirty."""
    out = {"timezone": "", "city": "", "state": ""}
    spoken = outbound._spoken_booked_time("2026-08-24T18:30:00", outbound._prospect_tz_if_known(out))
    assert spoken == "six thirty"


def test_greeting_still_works_without_a_location():
    """morning/afternoon only has to be roughly right, so it keeps the fallback."""
    assert outbound._local_greeting({"timezone": "", "city": "", "state": ""}) in {
        "morning", "afternoon", "evening"
    }


# --------------------------------------------------------------------------- #
# A voicemail greeting is transcribed as role=user — it must not read as the
# prospect answering (live calls 01a03094, 01a03248: customer-did-not-answer
# was tagged call-answered, which skips the whole retry chain)
# --------------------------------------------------------------------------- #
_VM_FORWARD = [{"role": "user", "message": "Your call has been forwarded to an automatic "
                "voice messaging system. At the tone, please record your message."}]
_VM_PERSONAL = [{"role": "user", "message": "Hey. Can't make it to the phone right now. "
                 "Please leave your name, number, and message, and I'll get back to you."}]
_REAL = [{"role": "user", "message": "Hello, Ryan speaking."}]


@pytest.mark.parametrize("messages", [_VM_FORWARD, _VM_PERSONAL])
def test_voicemail_greeting_is_not_the_prospect(messages):
    assert outbound._prospect_spoke(messages) is False
    assert outbound._classify_outcome("customer-did-not-answer", "", messages) == "call-voicemail"


def test_a_real_greeting_still_counts():
    assert outbound._prospect_spoke(_REAL) is True
    assert outbound._classify_outcome("customer-ended-call", "", _REAL) == "call-answered"


def test_silence_is_still_no_answer():
    assert outbound._classify_outcome("customer-did-not-answer", "", []) == "call-no-answer"


def test_a_real_conversation_after_a_machine_line_still_counts():
    """Defensive: if a human picks up mid-greeting, they answered."""
    mixed = _VM_FORWARD + [{"role": "user", "message": "Sorry, hello? This is Ryan."}]
    assert outbound._prospect_spoke(mixed) is True


# --------------------------------------------------------------------------- #
# Cordero incident, 2026-09-03 — voicemail tagged as answered
# --------------------------------------------------------------------------- #
class TestNoAnswerReasonOutranksTranscript:
    """Two voicemails came back endedReason=customer-did-not-answer but were
    tagged `call-answered`, because Deepgram renders the machine greeting as
    role=user and the phrase list missed the wording. The GHL show-up workflow
    reads these tags, so a misread outcome is what let the contact get dialled
    four times.

    Verbatim transcripts from calls 01a06797 and 01a067cb.
    """

    def test_mangled_record_your_message_is_not_a_human(self):
        # Deepgram dropped the "re" from "record"
        msgs = [{"role": "user",
                 "message": "Cord your message. When you have finished recording, you may hang up."}]
        assert outbound._classify_outcome("customer-did-not-answer", "", msgs) == "call-voicemail"

    def test_bare_recording_tail_is_not_a_human(self):
        msgs = [{"role": "user", "message": "Recording, you may hang up."}]
        assert outbound._classify_outcome("customer-did-not-answer", "", msgs) == "call-voicemail"

    def test_genuine_pickup_on_a_normal_reason_still_counts_as_answered(self):
        msgs = [{"role": "user", "message": "From where?"},
                {"role": "user", "message": "Yeah."}]
        assert outbound._classify_outcome("customer-ended-call", "", msgs) == "call-answered"

    def test_no_answer_with_no_transcript_stays_no_answer(self):
        assert outbound._classify_outcome("customer-did-not-answer", "", []) == "call-no-answer"
        assert outbound._classify_outcome("customer-did-not-answer", "", None) == "call-no-answer"

    def test_unknown_reason_still_defers_to_the_transcript(self):
        # The case _prospect_spoke exists for: an unrecognised reason string must
        # not silently mean "answered", so the transcript still decides.
        msgs = [{"role": "user", "message": "Yeah I'm here."}]
        assert outbound._classify_outcome("some-reason-we-have-never-seen", "", msgs) == "call-answered"
        assert outbound._classify_outcome("some-reason-we-have-never-seen", "", []) == "call-no-answer"

    def test_in_call_outcome_still_outranks_everything(self):
        assert outbound._classify_outcome("customer-did-not-answer", "confirmed", None) is None
        assert outbound._classify_outcome("customer-did-not-answer", "rescheduled", None) == "call-rescheduled"


# --------------------------------------------------------------------------- #
# The "please stay on the line" carrier system, 2026-09-07
#
# This greeting never says the word voicemail. It opens with a short, friendly,
# entirely human-sounding line — "Thanks. Please stay on the line." — and only
# afterwards plays "I'm sorry. This person is not available. If you would like
# to leave an additional message, please reply after the tone."
#
# The old rule was "the first user turn that is not a known machine phrase means
# a human answered", so that opener alone was enough to tag the call
# `call-answered`. Seven live calls went out that way — 01a07d08, 01a0781c,
# 01a06e6f, 01a06cbf, 01a068a9, 01a06797, 01a058e6 — and `call-answered` is what
# stops the GHL show-up workflow firing the retry, the text and the voice note.
#
# Transcripts below are verbatim from those calls.
# --------------------------------------------------------------------------- #
class TestStayOnTheLineIsAMachine:

    _NOT_AVAILABLE = ("I'm sorry. This person is not available. If you would like "
                      "to leave an additional message, please reply after the tone.")

    def test_opener_alone_is_a_machine(self):
        """01a07d08 / 01a06797 — the call ended before the second line played."""
        msgs = [{"role": "user", "message": "Thanks. Please stay on the line."}]
        assert outbound._prospect_spoke(msgs) is False
        assert outbound._classify_outcome(
            "assistant-said-end-call-phrase", "", msgs) == "call-voicemail"

    def test_opener_plus_greeting_is_a_machine(self):
        """01a0781c / 01a06e6f / 01a06cbf — the full two-line system."""
        msgs = [{"role": "user", "message": "Thanks. Please stay on the line."},
                {"role": "user", "message": self._NOT_AVAILABLE}]
        assert outbound._classify_outcome(
            "assistant-said-end-call-phrase", "", msgs) == "call-voicemail"

    def test_deepgram_hearing_ashley_in_the_greeting_changes_nothing(self):
        """01a068a9 — Deepgram rendered it "Thanks, Ashley. Please stay on the
        line.", which is the machine, not the prospect using her name."""
        msgs = [{"role": "user", "message": "Thanks, Ashley. Please stay on the line."},
                {"role": "user", "message": self._NOT_AVAILABLE}]
        assert outbound._classify_outcome(
            "customer-ended-call", "", msgs) == "call-voicemail"

    def test_a_stray_fragment_does_not_rescue_the_call(self):
        """01a058e6 — a one-word fragment ahead of the greeting. Machine
        evidence anywhere outweighs a fragment; it takes a real turn to win."""
        msgs = [{"role": "user", "message": "Available."},
                {"role": "user", "message": "Thanks. Please stay on the line."}]
        assert outbound._classify_outcome(
            "customer-ended-call", "", msgs) == "call-voicemail"

    def test_pound_sign_system_is_a_machine(self):
        """01a06cbf / 01a0697e / 01a0686b / 01a06dcb."""
        msgs = [{"role": "user", "message":
                 "Matthew Thompson. Nothing has been recorded. Record your after the "
                 "tone. Simply hang up when done. Or for delivery options, press the "
                 "pound sign."}]
        assert outbound._classify_outcome(
            "customer-did-not-answer", "", msgs) == "call-voicemail"

    def test_cant_take_your_call_is_a_machine(self):
        """01a07db3 / 01a07ca0."""
        msgs = [{"role": "user", "message":
                 "Can't take your call now. At the tone, please record your message."}]
        assert outbound._classify_outcome(
            "assistant-said-end-call-phrase", "", msgs) == "call-voicemail"

    def test_the_receptionist_who_really_answered_still_counts(self):
        """01a06799 — a real human who is not the prospect. No machine phrase,
        so nothing here may push it towards voicemail."""
        msgs = [{"role": "user", "message":
                 "Hey, Ashley. Good morning. I actually just help out with the work "
                 "calls here for All About Tree Service."},
                {"role": "user", "message": "Just checking in."}]
        assert outbound._classify_outcome(
            "assistant-said-end-call-phrase", "", msgs) == "call-answered"

    def test_short_human_turns_on_a_clean_call_still_count(self):
        """01a07777 — a confirmed prospect answering in one-word turns. The
        word floor applies only once a machine has been heard."""
        msgs = [{"role": "user", "message": "Alright."},
                {"role": "user", "message": "Yes."},
                {"role": "user", "message": "Sounds good."}]
        assert outbound._classify_outcome(
            "customer-ended-call", "", msgs) == "call-answered"
