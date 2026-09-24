"""TDD — GHL-facing outbound-call trigger (POST /ghl/outbound-call).

The route GoHighLevel POSTs to when a confirmation or reminder call is due.
Covers auth, payload tolerance, assistant routing by call_type, and the
duplicate-suppression rules that keep a prospect from being dialed twice.

Import server BEFORE ghl_trigger so server mounts the router.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.webhooks import server, ghl_trigger
from src.webhooks.vapi_client import VapiError, normalize_phone

SECRET = "test-ghl-secret"
CONFIRM_ID = "asst-confirm-111"
REMIND_ID = "asst-remind-222"
REBOOK_ID = "asst-rebook-333"


@pytest.fixture(autouse=True)
def _env_and_dedupe(monkeypatch):
    monkeypatch.setenv("GHL_OUTBOUND_SECRET", SECRET)
    monkeypatch.setenv("VAPI_ASSISTANT_ID_CONFIRMATION", CONFIRM_ID)
    monkeypatch.setenv("VAPI_ASSISTANT_ID_REMINDER", REMIND_ID)
    monkeypatch.setenv("VAPI_ASSISTANT_ID_REBOOKING", REBOOK_ID)
    ghl_trigger._recent.clear()
    yield
    ghl_trigger._recent.clear()


@pytest.fixture
def client():
    return TestClient(server.app)


@pytest.fixture
def create_call(monkeypatch):
    mock = AsyncMock(return_value={"id": "call-abc"})
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", mock)
    return mock


@pytest.fixture(autouse=True)
def _no_text_thread(monkeypatch):
    """Default every test to "no texts on file" so the pre-dial thread read
    never reaches the network. Tests that care override this."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts", AsyncMock(return_value=[]))


@pytest.fixture(autouse=True)
def _no_preloaded_slots(monkeypatch):
    """Default every test to "no open times" so the rebooking preload never reaches
    the network. Tests that care override this."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_available_slots", AsyncMock(return_value={}))


@pytest.fixture(autouse=True)
def _no_triggering_appointment(monkeypatch):
    """Default every test to "the triggering appointment cannot be read" so the
    rebooking missed-day read never reaches the network. Tests that care override this."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment", AsyncMock(return_value={}))


@pytest.fixture(autouse=True)
def _no_dnd(monkeypatch):
    """Default every test to "not on DND" so the pre-dial DND read never reaches
    the network. Tests that care override this."""
    monkeypatch.setattr(ghl_trigger.ghl, "has_call_dnd", AsyncMock(return_value=False))


def post(client, body, secret=SECRET):
    headers = {"X-GHL-Secret": secret} if secret is not None else {}
    return client.post("/ghl/outbound-call", json=body, headers=headers)


BASE = {
    "call_type": "confirmation",
    "phone": "(555) 123-4567",
    "contact_id": "ghl-c1",
    "appointment_id": "appt-1",
    "first_name": "Dana",
}


# --------------------------------------------------------------------------- #
# normalize_phone
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    ("(555) 123-4567", "+15551234567"),
    ("555-123-4567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+15551234567", "+15551234567"),
    ("+44 20 7946 0958", "+442079460958"),
    ("{{contact.phone}}", None),   # unrendered GHL merge field
    ("123", None),
    ("", None),
    (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def test_rejects_missing_secret(client, create_call):
    assert post(client, BASE, secret=None).status_code == 401
    create_call.assert_not_awaited()


def test_rejects_wrong_secret(client, create_call):
    assert post(client, BASE, secret="nope").status_code == 401
    create_call.assert_not_awaited()


def test_500_when_secret_unconfigured(client, create_call, monkeypatch):
    monkeypatch.delenv("GHL_OUTBOUND_SECRET", raising=False)
    assert post(client, BASE).status_code == 500
    create_call.assert_not_awaited()


# --------------------------------------------------------------------------- #
# payload handling
# --------------------------------------------------------------------------- #
def test_places_call_with_confirmation_assistant(client, create_call):
    res = post(client, BASE)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "callId": "call-abc"}

    kwargs = create_call.await_args.kwargs
    assert kwargs["assistant_id"] == CONFIRM_ID
    assert kwargs["customer_number"] == "+15551234567"
    assert kwargs["variable_values"]["firstName"] == "Dana"
    assert kwargs["metadata"]["ghl_appointment_id"] == "appt-1"
    assert kwargs["metadata"]["call_type"] == "confirmation"


def test_reminder_routes_to_reminder_assistant(client, create_call):
    post(client, {**BASE, "call_type": "reminder"})
    assert create_call.await_args.kwargs["assistant_id"] == REMIND_ID


def test_rebooking_routes_to_rebooking_assistant(client, create_call):
    """Rescheduling is its own call-back since 2026-09-04: GHL fires it when
    the contact carries `needs-reschedule` after a confirmation or reminder."""
    post(client, {**BASE, "call_type": "rebooking"})
    kwargs = create_call.await_args.kwargs
    assert kwargs["assistant_id"] == REBOOK_ID
    assert kwargs["metadata"]["call_type"] == "rebooking"


def test_rebooking_dials_from_the_built_in_id_when_env_is_unset(client, create_call, monkeypatch):
    """The VPS .env is never edited by a deploy; the id ships in code so the
    call-back works the moment the webhook is deployed."""
    monkeypatch.delenv("VAPI_ASSISTANT_ID_REBOOKING", raising=False)
    post(client, {**BASE, "call_type": "rebooking"})
    fallback = ghl_trigger.FALLBACK_ASSISTANT_ID_BY_CALL_TYPE["rebooking"]
    assert fallback and "__" not in fallback, "placeholder never replaced with the real id"
    assert create_call.await_args.kwargs["assistant_id"] == fallback


def test_defaults_to_confirmation_when_call_type_absent(client, create_call):
    body = {k: v for k, v in BASE.items() if k != "call_type"}
    post(client, body)
    assert create_call.await_args.kwargs["assistant_id"] == CONFIRM_ID


def test_reads_from_customData(client, create_call):
    post(client, {"customData": {"phone": "5551234567", "first_name": "Sam"},
                  "appointment_id": "appt-cd"})
    kwargs = create_call.await_args.kwargs
    assert kwargs["customer_number"] == "+15551234567"
    assert kwargs["variable_values"]["firstName"] == "Sam"


def test_422_on_unrendered_merge_field(client, create_call):
    res = post(client, {**BASE, "phone": "{{contact.phone}}"})
    assert res.status_code == 422
    assert res.json()["error"] == "no_phone"
    create_call.assert_not_awaited()


def test_422_on_unknown_call_type(client, create_call):
    res = post(client, {**BASE, "call_type": "telepathy"})
    assert res.status_code == 422
    create_call.assert_not_awaited()


def test_500_when_assistant_id_unconfigured(client, create_call, monkeypatch):
    monkeypatch.delenv("VAPI_ASSISTANT_ID_REMINDER", raising=False)
    res = post(client, {**BASE, "call_type": "reminder"})
    assert res.status_code == 500
    create_call.assert_not_awaited()


def test_no_name_falls_back_without_inventing_one(client, create_call):
    body = {k: v for k, v in BASE.items() if k != "first_name"}
    post(client, body)
    assert create_call.await_args.kwargs["variable_values"]["firstName"] == "there"


# --------------------------------------------------------------------------- #
# duplicate suppression — the reason this endpoint exists in this shape
# --------------------------------------------------------------------------- #
def test_second_fire_same_appointment_is_deduped(client, create_call):
    first = post(client, BASE)
    second = post(client, BASE)
    assert first.json()["ok"] is True
    assert second.json() == {"ok": True, "deduped": True}
    assert create_call.await_count == 1


def test_reschedule_to_a_new_appointment_is_allowed_through(client, create_call):
    """A GHL reschedule mints a NEW appointment id — that is a real second call,
    not a duplicate, and must not be suppressed."""
    post(client, BASE)
    post(client, {**BASE, "appointment_id": "appt-2"})
    assert create_call.await_count == 2


def test_both_call_types_may_fire_for_one_appointment(client, create_call):
    """Day-before and day-of are two legitimate calls on the same booking."""
    post(client, BASE)
    post(client, {**BASE, "call_type": "reminder"})
    assert create_call.await_count == 2


def test_dedupes_on_phone_when_no_appointment_id(client, create_call):
    body = {k: v for k, v in BASE.items() if k != "appointment_id"}
    post(client, body)
    post(client, body)
    assert create_call.await_count == 1


# --------------------------------------------------------------------------- #
# failure handling — never hand GHL a retryable status
# --------------------------------------------------------------------------- #
def test_vapi_rejection_returns_200_and_frees_the_claim(client, monkeypatch):
    """Vapi answered and refused, so no call exists — a later retry should be
    able to try again."""
    mock = AsyncMock(side_effect=VapiError(400, "bad number"))
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", mock)
    res = post(client, BASE)
    assert res.status_code == 200
    assert res.json() == {"ok": False, "error": "call_failed"}

    ok = AsyncMock(return_value={"id": "call-retry"})
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", ok)
    assert post(client, BASE).json()["ok"] is True


def test_ambiguous_failure_keeps_the_claim(client, monkeypatch):
    """A timeout may mean the call IS ringing. Suppress the retry — dialing
    twice is worse than not dialing."""
    mock = AsyncMock(side_effect=TimeoutError("connection dropped"))
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", mock)
    assert post(client, BASE).json()["ok"] is False

    ok = AsyncMock(return_value={"id": "call-should-not-happen"})
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", ok)
    assert post(client, BASE).json() == {"ok": True, "deduped": True}
    ok.assert_not_awaited()


# --------------------------------------------------------------------------- #
# Day-of reminder: the booking must actually be today
# --------------------------------------------------------------------------- #
from datetime import datetime, timedelta      # noqa: E402
from zoneinfo import ZoneInfo                 # noqa: E402


def _iso_on(tz_name: str, days: int):
    """A fixed 3pm slot `days` from today IN THAT TIMEZONE.

    Deliberately date-based, not "now + N hours": the guard compares dates, and
    an hours-offset test flips its own expected result when the suite happens to
    run late enough in the evening for the offset to cross midnight.
    """
    tz = ZoneInfo(tz_name)
    day = (datetime.now(tz) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, 15, 0, tzinfo=tz).isoformat()


@pytest.mark.parametrize("days,expect_time", [
    (0, True),    # today    -> keep the time
    (1, False),   # tomorrow -> drop it rather than say "later today"
])
def test_reminder_only_says_a_time_when_the_booking_is_today(
    client, create_call, monkeypatch, days, expect_time
):
    """The lookup returns the soonest upcoming appointment whatever day it is
    on. Without this guard, someone whose only booking is tomorrow is told it is
    'later today'."""
    tz = "America/New_York"
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Dana",
        "contact_id": "c1",
        "appointment_id": "a1",
        "booked_time": "three thirty",
        "greeting": "morning",
        "start_iso": _iso_on(tz, days),
        "timezone": tz, "city": "", "state": "",
    }))
    post(client, {**BASE, "call_type": "reminder", "appointment_id": f"appt-{days}"})
    got = create_call.await_args.kwargs["variable_values"]["bookedTime"]
    assert (got == "three thirty") is expect_time


def test_confirmation_keeps_tomorrows_time(client, create_call, monkeypatch):
    """The guard is day-of only — the day-before call is SUPPOSED to name
    tomorrow's time."""
    tz = "America/New_York"
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Dana", "contact_id": "c1", "appointment_id": "a1",
        "booked_time": "three thirty", "greeting": "morning",
        "start_iso": _iso_on(tz, 1), "timezone": tz, "city": "", "state": "",
    }))
    post(client, {**BASE, "call_type": "confirmation", "appointment_id": "appt-conf"})
    assert create_call.await_args.kwargs["variable_values"]["bookedTime"] == "three thirty"


def test_deliberate_retry_is_not_deduped(client, create_call, monkeypatch):
    """The show-up workflow calls the same appointment twice on purpose (9am,
    then 2pm if nobody answered). A 6-hour dedupe window swallowed the second
    call entirely — the claim is only released on a Vapi refusal, never on
    success."""
    import time as _t
    post(client, BASE)
    assert create_call.await_count == 1

    # Anything past the window is a deliberate retry, not a duplicate.
    for k in list(ghl_trigger._recent):
        ghl_trigger._recent[k] = _t.time() - (ghl_trigger.DEDUPE_WINDOW_S + 5)

    post(client, BASE)
    assert create_call.await_count == 2, "the 2pm retry was suppressed as a duplicate"


def test_rapid_double_fire_is_still_deduped(client, create_call):
    """What the guard is actually for: GHL retrying the webhook, or firing twice
    on one booking. Those arrive seconds apart."""
    post(client, BASE)
    post(client, BASE)
    assert create_call.await_count == 1


# --------------------------------------------------------------------------- #
# Guards that do not depend on GHL sending the right merge fields
# --------------------------------------------------------------------------- #
def _lookup_at(tz, hours):
    return {
        "first_name": "Dana", "contact_id": "c1", "appointment_id": "a1",
        "booked_time": "three thirty", "greeting": "morning",
        "start_iso": (datetime.now(ZoneInfo(tz)) + timedelta(hours=hours)).isoformat(),
        "timezone": tz, "city": "", "state": "",
    }


def test_status_is_read_from_ghl_when_the_payload_omits_it(client, create_call, monkeypatch):
    """The workflow does not always carry appointment_status, and the trigger
    fires on cancellations — so the guard asks GHL rather than assuming."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status",
                        AsyncMock(return_value="cancelled"))
    body = {k: v for k, v in BASE.items() if k != "appointment_status"}
    res = post(client, body)
    assert res.json()["skipped"] == "status_cancelled"
    create_call.assert_not_awaited()


def test_payload_status_wins_without_an_extra_lookup(client, create_call, monkeypatch):
    spy = AsyncMock(return_value="cancelled")
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status", spy)
    monkeypatch.setattr(ghl_trigger, "_resolve_booking",
                        AsyncMock(return_value=_lookup_at("America/New_York", 48)))
    post(client, {**BASE, "appointment_status": "confirmed"})
    spy.assert_not_awaited()
    create_call.assert_awaited_once()


def test_same_day_booking_still_gets_called(client, create_call, monkeypatch):
    """Owner 2026-08-23: a booking made the same day must STILL get its
    confirmation call — the workflow decides the timing, not the server. There
    is deliberately no minimum-lead-time guard."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status", AsyncMock(return_value=""))
    monkeypatch.setattr(ghl_trigger, "_resolve_booking",
                        AsyncMock(return_value=_lookup_at("America/New_York", 2)))
    post(client, {**BASE, "call_type": "reminder", "appointment_status": "confirmed",
                  "appointment_id": "rem-close"})
    create_call.assert_awaited_once()


# --------------------------------------------------------------------------- #
# POST /ghl/rebooking-call — its own path (owner 2026-09-07)
#
# The CRM runs a SEPARATE demo-rebooking TEXT drip at
# sales.c7a.io/api/systems/rebooking/entered. It accepts almost any payload and
# returns 200, so a rebooking CALL aimed at it fails silently — which is exactly
# what happened on 2026-09-07. One path per system, so a wrong URL cannot
# quietly succeed.
# --------------------------------------------------------------------------- #
def post_rebook(client, body, secret=SECRET):
    headers = {"X-GHL-Secret": secret} if secret is not None else {}
    return client.post("/ghl/rebooking-call", json=body, headers=headers)


def test_rebooking_path_dials_the_rebooking_assistant(client, create_call):
    res = post_rebook(client, {k: v for k, v in BASE.items() if k != "call_type"})
    assert res.status_code == 200
    kwargs = create_call.await_args.kwargs
    assert kwargs["assistant_id"] == REBOOK_ID
    assert kwargs["metadata"]["call_type"] == "rebooking"


def test_rebooking_path_ignores_a_wrong_call_type_in_the_body(client, create_call):
    """A GHL step copy-pasted from the confirmation workflow still carries
    call_type=confirmation. The path decides, not the body."""
    post_rebook(client, {**BASE, "call_type": "confirmation"})
    assert create_call.await_args.kwargs["assistant_id"] == REBOOK_ID


def test_rebooking_path_still_requires_the_secret(client, create_call):
    assert post_rebook(client, BASE, secret="wrong").status_code == 401
    create_call.assert_not_awaited()


def test_rebooking_path_needs_a_phone(client, create_call):
    res = post_rebook(client, {k: v for k, v in BASE.items() if k != "phone"})
    assert res.status_code == 422
    create_call.assert_not_awaited()


def test_rebooking_runs_even_on_a_dead_appointment(client, create_call, monkeypatch):
    """The live-status guard stops a CONFIRMATION call asking a cancelled
    prospect "are we still on?". Putting that prospect back on the calendar is
    this call's entire purpose, so the guard must not apply here."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status",
                        AsyncMock(return_value="cancelled"))
    res = post_rebook(client, {**BASE, "appointment_status": "cancelled"})
    assert res.status_code == 200
    assert "skipped" not in res.json()
    create_call.assert_awaited_once()


def test_shared_path_still_skips_a_dead_appointment(client, create_call):
    """Regression guard: loosening the status rule for rebooking must not
    loosen it for confirmation or reminder."""
    res = post(client, {**BASE, "appointment_status": "cancelled"})
    assert res.json()["skipped"] == "status_cancelled"
    create_call.assert_not_awaited()


def test_both_paths_share_one_dedupe_key(client, create_call):
    """Same flow, same appointment — whichever path fired it, the second is a
    duplicate."""
    post_rebook(client, BASE)
    post(client, {**BASE, "call_type": "rebooking"})
    assert create_call.await_count == 1


# --------------------------------------------------------------------------- #
# Rebooking type — carried in the BODY, not the URL (owner 2026-09-07)
#
# `POST /ghl/rebooking-call` with {"type": "no-show" | "cancelled"}. Same shape
# as call_type on the shared endpoint, so a GHL step is one field edit. What the
# type changes is ONE sentence of context rendered into {{callReason}}; the flow
# she runs is byte-identical every time.
# --------------------------------------------------------------------------- #
def post_rebook(client, body, secret=SECRET):
    headers = {"X-GHL-Secret": secret} if secret is not None else {}
    return client.post("/ghl/rebooking-call", json=body, headers=headers)


REBOOK_TYPES = [("no-show", "no_show"), ("cancelled", "cancelled"), (None, "conflict")]


@pytest.mark.parametrize("sent,reason", REBOOK_TYPES)
def test_body_type_selects_the_reason(client, create_call, sent, reason):
    body = {k: v for k, v in BASE.items() if k != "call_type"}
    if sent:
        body["type"] = sent
    post_rebook(client, body)
    kwargs = create_call.await_args.kwargs
    assert kwargs["assistant_id"] == REBOOK_ID
    assert kwargs["variable_values"]["callReason"] == ghl_trigger.REBOOK_REASONS[reason]


def test_absent_type_keeps_the_original_behaviour(client, create_call):
    """The first working rebooking call sent no type at all."""
    post_rebook(client, {"phone": BASE["phone"]})
    assert create_call.await_args.kwargs["variable_values"]["callReason"] ==         ghl_trigger.REBOOK_REASONS["conflict"]


@pytest.mark.parametrize("spelling", ["no-show", "no_show", "noshow", "NO-SHOW", " No Show "])
def test_no_show_spellings_all_land(client, create_call, spelling):
    """Typed by hand into a GHL webhook step, so be generous."""
    post_rebook(client, {**BASE, "type": spelling})
    assert create_call.await_args.kwargs["variable_values"]["callReason"] ==         ghl_trigger.REBOOK_REASONS["no_show"]


@pytest.mark.parametrize("spelling", ["cancelled", "canceled", "CANCELLED", "cancel"])
def test_cancelled_spellings_all_land(client, create_call, spelling):
    post_rebook(client, {**BASE, "type": spelling})
    assert create_call.await_args.kwargs["variable_values"]["callReason"] ==         ghl_trigger.REBOOK_REASONS["cancelled"]


def test_an_unknown_type_is_refused_not_guessed(client, create_call):
    """Silently defaulting is how a misrouted rebooking call goes unnoticed —
    the lesson from 2026-09-07, when the CRM's text drip accepted a call payload
    and returned success."""
    res = post_rebook(client, {**BASE, "type": "no-shwo"})
    assert res.status_code == 422
    assert res.json()["error"] == "unknown_rebooking_type"
    create_call.assert_not_awaited()


def test_type_is_read_from_customdata_too(client, create_call):
    """GHL nests custom fields under customData on some workflow versions."""
    body = {k: v for k, v in BASE.items() if k != "call_type"}
    body["customData"] = {"type": "cancelled"}
    post_rebook(client, body)
    assert create_call.await_args.kwargs["variable_values"]["callReason"] ==         ghl_trigger.REBOOK_REASONS["cancelled"]


def test_the_sub_paths_are_gone(client, create_call):
    """They existed for a few hours. The type lives in the body now, so a stale
    URL must 404 rather than quietly dial with the wrong reason."""
    for path in ("/ghl/rebooking-call/no-show", "/ghl/rebooking-call/cancelled"):
        assert client.post(path, json=BASE,
                           headers={"X-GHL-Secret": SECRET}).status_code == 404
    create_call.assert_not_awaited()


def test_the_three_reasons_read_differently(client):
    vals = list(ghl_trigger.REBOOK_REASONS.values())
    assert len(set(vals)) == len(vals)
    assert ghl_trigger.DEFAULT_REBOOK_REASON in ghl_trigger.REBOOK_REASONS


def test_rebooking_still_runs_on_a_dead_appointment(client, create_call, monkeypatch):
    """No-show and cancelled ARE dead appointments — a live-status guard here
    would block every call the feature exists to make."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status",
                        AsyncMock(return_value="noshow"))
    res = post_rebook(client, {**BASE, "type": "no-show", "appointment_status": "noshow"})
    assert "skipped" not in res.json()
    create_call.assert_awaited_once()


def test_shared_path_still_skips_a_dead_appointment(client, create_call):
    res = post(client, {**BASE, "appointment_status": "cancelled"})
    assert res.json()["skipped"] == "status_cancelled"
    create_call.assert_not_awaited()


def test_rebooking_path_still_requires_the_secret(client, create_call):
    assert post_rebook(client, BASE, secret="wrong").status_code == 401
    create_call.assert_not_awaited()


def test_rebooking_path_needs_a_phone(client, create_call):
    res = post_rebook(client, {k: v for k, v in BASE.items() if k != "phone"})
    assert res.status_code == 422
    create_call.assert_not_awaited()


def test_rebooking_ignores_a_wrong_call_type_in_the_body(client, create_call):
    post_rebook(client, {**BASE, "call_type": "confirmation"})
    assert create_call.await_args.kwargs["assistant_id"] == REBOOK_ID


def test_confirmation_and_reminder_carry_no_call_reason(client, create_call):
    post(client, BASE)
    assert "callReason" not in create_call.await_args.kwargs["variable_values"]


def test_two_rebooking_fires_at_once_still_dial_once(client, create_call):
    post_rebook(client, {**BASE, "type": "no-show"})
    post_rebook(client, {**BASE, "type": "cancelled"})
    assert create_call.await_count == 1


# --------------------------------------------------------------------------- #
# Pre-dial text-thread check (owner 2026-09-07)
#
# Ashley calls people who have been texted. Until this shipped she knew nothing
# about that thread and would ring somebody who had texted a minute earlier.
# --------------------------------------------------------------------------- #
def _texts(*, body="hey can we move it?", inbound=True, minutes_ago=5):
    return [{
        "body": body,
        "direction": "inbound" if inbound else "outbound",
        "dateAdded": (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=minutes_ago)).isoformat(),
    }]


@pytest.mark.parametrize("age_minutes", [2, 60 * 24 * 2])
def test_a_reply_from_them_pauses_the_call(client, create_call, monkeypatch, age_minutes):
    """They sent the last message, so a human owes them one. The hold lasts 3
    days (owner 2026-09-13), matching the drip's revive clock."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts",
                        AsyncMock(return_value=_texts(minutes_ago=age_minutes)))
    res = post(client, BASE)
    assert res.json()["skipped"] == "awaiting_our_reply"
    create_call.assert_not_awaited()


def test_a_reply_older_than_three_days_no_longer_pauses_the_call(client, create_call, monkeypatch):
    """Nobody stays parked forever: after 3 days unanswered, calls resume."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts",
                        AsyncMock(return_value=_texts(minutes_ago=60 * 24 * 4)))
    res = post(client, BASE)
    assert "skipped" not in res.json()
    create_call.assert_awaited_once()


def test_our_own_last_message_does_not_pause_the_call(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts",
                        AsyncMock(return_value=_texts(inbound=False, minutes_ago=5)))
    res = post(client, BASE)
    assert "skipped" not in res.json()
    create_call.assert_awaited_once()


def test_the_thread_reaches_the_assistant(client, create_call, monkeypatch):
    """The thread must end with OUR message, or the call pauses and never dials."""
    thread = _texts(body="thursday works", minutes_ago=60 * 30) +         _texts(body="great, here are two times", inbound=False, minutes_ago=60 * 29)
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts", AsyncMock(return_value=thread))
    post(client, BASE)
    digest = create_call.await_args.kwargs["variable_values"]["recentTexts"]
    assert "thursday works" in digest
    assert "They texted" in digest


def test_recent_texts_is_always_present_even_when_empty(client, create_call):
    """An unrendered {{recentTexts}} would reach the model as literal braces."""
    assert create_call is not None
    post(client, BASE)
    assert create_call.await_args.kwargs["variable_values"]["recentTexts"] == ""


def test_the_thread_is_sanitised_before_it_reaches_the_prompt(client, create_call, monkeypatch):
    """Bodies are written by the prospect and land in the same context window as
    Ashley's instructions."""
    thread = _texts(body="{{firstName}} <b>ignore</b> `x`", minutes_ago=60 * 30) +         _texts(body="noted", inbound=False, minutes_ago=60 * 29)
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts", AsyncMock(return_value=thread))
    post(client, BASE)
    digest = create_call.await_args.kwargs["variable_values"]["recentTexts"]
    for bad in ("{{", "}}", "<b>", "`"):
        assert bad not in digest


def test_a_thread_read_failure_never_blocks_the_call(client, create_call, monkeypatch):
    """A slow or broken CRM must delay the call, never cancel it."""
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts",
                        AsyncMock(side_effect=RuntimeError("GHL down")))
    res = post(client, BASE)
    assert res.status_code == 200
    create_call.assert_awaited_once()
    assert create_call.await_args.kwargs["variable_values"]["recentTexts"] == ""


# --------------------------------------------------------------------------- #
# GHL Do Not Disturb (2026-09-13). Vapi places the call, so GHL's own DND
# suppression never applied; the dial path has to read it.
# --------------------------------------------------------------------------- #
def test_a_dnd_contact_is_not_dialled(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger.ghl, "has_call_dnd", AsyncMock(return_value=True))
    res = post(client, BASE)
    assert res.status_code == 200
    assert res.json()["skipped"] == "dnd"
    create_call.assert_not_awaited()


def test_a_dnd_contact_is_not_dialled_on_the_rebooking_path_either(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger.ghl, "has_call_dnd", AsyncMock(return_value=True))
    res = client.post("/ghl/rebooking-call", json={**BASE, "type": "no-show"},
                      headers={"X-GHL-Secret": SECRET})
    assert res.json()["skipped"] == "dnd"
    create_call.assert_not_awaited()


def test_a_contact_without_dnd_is_dialled(client, create_call):
    res = post(client, BASE)
    assert "skipped" not in res.json()
    create_call.assert_awaited_once()


def test_a_dnd_read_failure_never_blocks_the_call(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger.ghl, "has_call_dnd",
                        AsyncMock(side_effect=RuntimeError("GHL down")))
    res = post(client, BASE)
    assert res.status_code == 200
    create_call.assert_awaited_once()


# --------------------------------------------------------------------------- #
# {{missedDay}} — the owner's rebooking opener names the call they missed.
# --------------------------------------------------------------------------- #
def test_rebooking_carries_the_missed_day(client, create_call, monkeypatch):
    yesterday = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Ryan", "contact_id": "ghl-c1",
        "timezone": "America/New_York", "missed_start_iso": yesterday,
    }))
    post_rebook(client, {**BASE, "type": "no-show"})
    values = create_call.await_args.kwargs["variable_values"]
    assert values["missedDay"] == "yesterday"
    assert "our call yesterday" in values["whyCalling"]


def test_the_triggering_appointment_names_the_missed_day(client, create_call, monkeypatch):
    """Call 01a0a257: the contact's list named an older cancel. The appointment the
    workflow fired on is read directly and wins."""
    ny = ZoneInfo("America/New_York")
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Ryan", "contact_id": "ghl-c1", "timezone": "America/New_York",
        "missed_start_iso": (datetime.now(ny) - timedelta(days=4)).isoformat(),
    }))
    yesterday = (datetime.now(ny) - timedelta(days=1)).isoformat()
    fetch = AsyncMock(return_value={"startTime": yesterday, "appointmentStatus": "cancelled"})
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment", fetch)
    post_rebook(client, {**BASE, "type": "cancelled", "appointment_id": "appt-tonight"})
    fetch.assert_any_await("appt-tonight")
    assert create_call.await_args.kwargs["variable_values"]["missedDay"] == "yesterday"


def test_rebooking_with_no_known_missed_call_sends_it_blank(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Ryan", "contact_id": "ghl-c1"}))
    post_rebook(client, {**BASE, "type": "no-show"})
    assert create_call.await_args.kwargs["variable_values"]["missedDay"] == ""
    assert "jump on our call" in create_call.await_args.kwargs["variable_values"]["whyCalling"]
    assert "our call ," not in create_call.await_args.kwargs["variable_values"]["whyCalling"]
    assert "our call ." not in create_call.await_args.kwargs["variable_values"]["whyCalling"]


def test_confirmation_and_reminder_carry_no_missed_day(client, create_call):
    post(client, BASE)
    assert "missedDay" not in create_call.await_args.kwargs["variable_values"]


# --------------------------------------------------------------------------- #
# Preload before dialling (owner, 2026-09-14): open times and the main-CRM contact.
# --------------------------------------------------------------------------- #
def test_rebooking_preloads_open_times_and_the_main_contact(client, create_call, monkeypatch):
    from src.webhooks import server
    tomorrow = (datetime.now(ZoneInfo("America/New_York")) + timedelta(days=1)).replace(
        hour=11, minute=0, second=0, microsecond=0)
    iso = tomorrow.isoformat()
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Waseem", "contact_id": "main-1", "timezone": "America/New_York"}))
    monkeypatch.setattr(ghl_trigger.ghl, "get_available_slots",
                        AsyncMock(return_value={"d": {"slots": [iso]}}))
    server._call_cache.pop("call-abc", None)
    post_rebook(client, {**BASE, "type": "no-show"})
    slots_text = create_call.await_args.kwargs["variable_values"]["availableSlots"]
    assert "\ntomorrow (" in slots_text
    assert f"[iso:{iso}]" in slots_text
    # The first offer is written by the webhook, day named once (call 01a0a678).
    assert create_call.await_args.kwargs["variable_values"]["firstOffer"].startswith("How does tomorrow at ")
    assert create_call.await_args.kwargs["variable_values"]["dayOffers"].startswith("How does tomorrow at ")
    # More times on a day, the day left out (call 01a0ab63).
    more = create_call.await_args.kwargs["variable_values"]["moreTimes"]
    assert more.startswith("[tomorrow ") and "How about " in more
    entry = server._call_cache["call-abc"]
    assert entry["contact"]["id"] == "main-1"
    assert entry["lookup_done"] is True
    assert iso in entry["offered_slots"]
    server._call_cache.pop("call-abc", None)


def test_a_slot_fetch_failure_still_places_the_call(client, create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger.ghl, "get_available_slots",
                        AsyncMock(side_effect=RuntimeError("GHL down")))
    res = post_rebook(client, {**BASE, "type": "no-show"})
    assert res.status_code == 200
    create_call.assert_awaited_once()
    assert create_call.await_args.kwargs["variable_values"]["availableSlots"] == ""
    assert create_call.await_args.kwargs["variable_values"]["firstOffer"] == ""
    assert create_call.await_args.kwargs["variable_values"]["dayOffers"] == ""
    assert create_call.await_args.kwargs["variable_values"]["moreTimes"] == ""


def test_confirmation_and_reminder_carry_no_preloaded_slots(client, create_call):
    post(client, BASE)
    assert "availableSlots" not in create_call.await_args.kwargs["variable_values"]
    assert "currentDay" not in create_call.await_args.kwargs["variable_values"]


def test_rebooking_carries_the_prospects_clock(client, create_call, monkeypatch):
    """Owner, 2026-09-15: today's date and time in the prospect's zone, loaded before the call."""
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Ryan", "contact_id": "ghl-c1", "timezone": "America/Los_Angeles"}))
    post_rebook(client, {**BASE, "type": "no-show"})
    line = create_call.await_args.kwargs["variable_values"]["currentDay"]
    expected = ghl_trigger._current_day_line("America/Los_Angeles")
    assert line.split(" on ", 1)[1] == expected.split(" on ", 1)[1]
    assert line.startswith("It is ") and "Tomorrow is " in line


def test_why_calling_line_is_the_owners_words_with_full_stops_when_commas_are_off(monkeypatch):
    monkeypatch.setattr(ghl_trigger, "WHY_CALLING_JOIN_WITH_COMMAS", False)
    assert ghl_trigger._why_calling_line("today") == (
        "You had a call booked with my team member Jon to dive into your business and to "
        "show you how we can help you get more jobs. You weren't able to jump on our call "
        "today. Figured you got tied up so I'm just giving you a call to get something "
        "locked in again."
    )
    assert "You weren't able to jump on our call. Figured" in ghl_trigger._why_calling_line("")


def test_why_calling_line_is_the_owners_words_with_the_day_filled_in(monkeypatch):
    monkeypatch.setattr(ghl_trigger, "WHY_CALLING_JOIN_WITH_COMMAS", True)
    line = ghl_trigger._why_calling_line("today")
    assert line == (
        "You had a call booked with my team member Jon to dive into your business and to "
        "show you how we can help you get more jobs, you weren't able to jump on our call "
        "today, figured you got tied up so I'm just giving you a call to get something "
        "locked in again."
    )
    # Call 01a0a281: Vapi cuts TTS input at every full stop. One at the end only.
    assert line.count(".") == 1 and line.endswith(".")
    assert "you weren't able to jump on our call, figured" in ghl_trigger._why_calling_line("")


def test_every_rebooking_variable_sent_has_a_home_in_the_context_message(client, create_call, monkeypatch):
    """The static prompt holds no variables; whatever the webhook sends must be
    rendered by the context message or it never reaches the model."""
    from src.agent.prompts import get_stage_context
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Ryan", "contact_id": "ghl-c1", "timezone": "America/New_York"}))
    post_rebook(client, {**BASE, "type": "no-show"})
    ctx = get_stage_context("demo_rebooking", "stage1")
    sent = set(create_call.await_args.kwargs["variable_values"])
    missing = {k for k in sent - {"bookedTime"} if "{{" + k + "}}" not in ctx}
    assert not missing, missing


def test_reminder_when_is_pre_written_for_the_opener(client, create_call, monkeypatch):
    """The reminder's opening line is firstMessage, spoken before the model runs,
    so no prompt gate can repair a hole in it — two live calls (01a0b597,
    01a0b4d8) said "the call at later today" from exactly that. The webhook must
    hand Vapi a complete phrase either way, or the braces are read out loud."""
    tz = "America/New_York"

    def _booking(booked_time):
        return AsyncMock(return_value={
            "first_name": "Dana", "contact_id": "c1", "appointment_id": "a1",
            "booked_time": booked_time, "greeting": "morning",
            "start_iso": _iso_on(tz, 0), "timezone": tz, "city": "", "state": "",
        })

    monkeypatch.setattr(ghl_trigger, "_resolve_booking", _booking("three thirty"))
    post(client, {**BASE, "call_type": "reminder", "appointment_id": "appt-when-1"})
    assert create_call.await_args.kwargs["variable_values"]["reminderWhen"] == "later today at three thirty"

    monkeypatch.setattr(ghl_trigger, "_resolve_booking", _booking(""))
    post(client, {**BASE, "call_type": "reminder", "appointment_id": "appt-when-2"})
    assert create_call.await_args.kwargs["variable_values"]["reminderWhen"] == "later today"


def test_reminder_when_is_reminder_only(client, create_call, monkeypatch):
    """Every variable sent must have a home in the prompt that reads it; the
    confirmation and rebooking openers never mention it."""
    tz = "America/New_York"
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value={
        "first_name": "Dana", "contact_id": "c1", "appointment_id": "a1",
        "booked_time": "three thirty", "greeting": "morning",
        "start_iso": _iso_on(tz, 1), "timezone": tz, "city": "", "state": "",
    }))
    post(client, {**BASE, "call_type": "confirmation", "appointment_id": "appt-when-3"})
    assert "reminderWhen" not in create_call.await_args.kwargs["variable_values"]
