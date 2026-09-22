"""TDD tests for two hardening fixes on the booking path:

  1. `_handle_book_video_call` polls the main CRM with `max_attempts=5,
     interval_seconds=3.0` (15s ceiling, was 4×12s=48s). Keeps total
     handler latency under Vapi's 60s tool timeout.

  2. Server-side `slot_iso` validation: each `get_available_slots` call
     stashes the offered ISOs into the per-call cache. `book_video_call`
     rejects a slot_iso that's NOT in that set (catches LLM hallucinations).
     Empty set = state-loss degraded mode → allow with WARNING log.
"""
import re
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.webhooks import server


@pytest.fixture(autouse=True)
def _clear_cache():
    server._call_cache.clear()
    yield
    server._call_cache.clear()


def _seed_cache(call_id: str, contact: dict | None, offered: set | None = None) -> None:
    """Pre-seed the per-call cache. Optionally include a set of offered ISOs."""
    server._call_cache[call_id] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": contact,
        "lookup_done": True,
        "captured": {"first_name": "Dan", "last_name": "", "business": "Foo", "email": ""},
        "offered_slots": set() if offered is None else set(offered),
    }


# --------------------------------------------------------------------------- #
# Item #1 — polling tighten
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_polls_with_tight_args():
    """`wait_for_main_crm_contact` must be called with the documented tight
    args (5 attempts × 3s = 15s) so total handler latency stays under Vapi's
    60s tool budget."""
    _seed_cache("call-poll-1",
                {"id": "tc1", "crm": "texts1", "first_name": "Dan",
                 "business": "Foo", "email": "dan@example.com"},
                offered={"2026-05-09T10:00:00-04:00"})

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})) as poll_mock, \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-1"}})):
        await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "dan@example.com",
             "prospect_first_name": "Dan"},
            "call-poll-1", "+15551234567",
        )

    poll_mock.assert_awaited_once()
    kwargs = poll_mock.await_args.kwargs
    assert kwargs.get("max_attempts") == 5, \
        f"polling must be 5 attempts, got {kwargs.get('max_attempts')}"
    assert kwargs.get("interval_seconds") == 3.0, \
        f"polling interval must be 3.0s, got {kwargs.get('interval_seconds')}"


# --------------------------------------------------------------------------- #
# Item #2 — slot_iso validation
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_available_slots_populates_offered_set():
    """After `get_available_slots` returns, the cache entry must hold every
    ISO the LLM was shown so we can validate the booking against it."""
    _seed_cache("call-cache-1", {"id": "c1", "timezone": "America/New_York"})

    fake_slots = {
        "2026-05-09": {"slots": [
            "2026-05-09T10:00:00-04:00",
            "2026-05-09T14:00:00-04:00",
            "2026-05-09T17:00:00-04:00",
        ]},
        "2026-05-10": {"slots": ["2026-05-10T11:00:00-04:00"]},
    }
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)):
        await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-cache-1", "+15551234567"
        )

    offered = server._call_cache["call-cache-1"]["offered_slots"]
    assert "2026-05-09T10:00:00-04:00" in offered
    assert "2026-05-09T14:00:00-04:00" in offered
    assert "2026-05-09T17:00:00-04:00" in offered
    assert "2026-05-10T11:00:00-04:00" in offered
    assert len(offered) == 4


@pytest.mark.asyncio
async def test_book_video_call_accepts_slot_in_offered_set():
    """Happy path — the agent picked an ISO we actually offered."""
    _seed_cache("call-ok",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00", "2026-05-09T14:00:00-04:00"})

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-ok", "+15551234567",
        )

    book_mock.assert_awaited_once()
    assert "Booked" in result


@pytest.mark.asyncio
async def test_book_video_call_rejects_hallucinated_slot():
    """Hallucinated path — LLM passed an ISO that wasn't in the offered set.
    Must reject with a helpful error and NOT call ghl.book_video_call."""
    _seed_cache("call-bad",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00"})

    with patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T11:00:00-04:00",  # hallucinated
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-bad", "+15551234567",
        )

    book_mock.assert_not_awaited()
    # Error message must steer the LLM back to get_available_slots.
    assert re.search(r"(get_available_slots|not in.*offered|wasn't in)", result, re.I), \
        f"reject message should tell the agent to re-fetch slots; got: {result!r}"


@pytest.mark.asyncio
async def test_book_video_call_allows_when_offered_set_empty(caplog):
    """Degraded mode — cache was lost (server restart, fresh call_id, etc.).
    With an empty offered set, allow the booking but log a WARNING. We must
    not block a real booking just because we lost state."""
    import logging
    caplog.set_level(logging.WARNING, logger="voicsito.webhooks")

    _seed_cache("call-empty",
                {"id": "tc1", "crm": "texts1"},
                offered=set())  # explicit empty — degraded

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-empty", "+15551234567",
        )

    book_mock.assert_awaited_once()
    assert "Booked" in result
    # Must surface the degraded mode in logs.
    assert any("offered_slots empty" in r.message or "no slot cache" in r.message
               for r in caplog.records), \
        "must log a WARNING when booking proceeds with empty offered_slots"


@pytest.mark.asyncio
async def test_book_video_call_allows_when_cache_entry_missing_offered_key(caplog):
    """Backwards-compat: a cache entry created before this change has no
    `offered_slots` key at all. Must not crash; must allow + log."""
    import logging
    caplog.set_level(logging.WARNING, logger="voicsito.webhooks")

    # Seed without the offered_slots key — older cache shape.
    server._call_cache["call-legacy"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": {"id": "tc1", "crm": "texts1"},
        "lookup_done": True,
        "captured": {"first_name": "", "last_name": "", "business": "", "email": ""},
        # no "offered_slots" key
    }

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-legacy", "+15551234567",
        )

    book_mock.assert_awaited_once()
    assert "Booked" in result


# --------------------------------------------------------------------------- #
# Main CRM contact name update — the data the user sees in the dashboard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_updates_main_contact_with_name_and_business():
    """The main CRM contact (created earlier by mark_for_transfer + GHL
    automation) must get the prospect's first name + business + email written
    onto it during book_video_call. Otherwise the dashboard shows whatever
    stale/scraped data was on the texts contact when the transfer fired."""
    server._call_cache["call-name-1"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": {"id": "tc1", "crm": "texts1"},
        "lookup_done": True,
        # Captured from update_prospect_info during the call.
        "captured": {"first_name": "Dan", "last_name": "",
                     "business": "Ridgeline Floors", "email": ""},
        "offered_slots": {"2026-05-09T10:00:00-04:00"},
    }

    update_calls: list[dict] = []

    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(side_effect=record_update)), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-contact-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-1"}})):
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "dan@ridgelinefloors.com",
             "prospect_first_name": "Dan",
             "business_name": "Ridgeline Floors"},
            "call-name-1", "+15551234567",
        )

    assert "Booked" in result

    # Texts pre-book update is the existing call. The NEW behavior is the
    # main-contact update — expect at least one call with crm_key="main"
    # carrying the prospect's name + business + email.
    main_updates = [c for c in update_calls if c.get("crm_key") == "main"]
    assert main_updates, (
        f"main CRM contact must be updated with the prospect's name/business; "
        f"saw only crm_keys={[c.get('crm_key') for c in update_calls]}"
    )
    main = main_updates[0]
    assert main.get("contact_id") == "main-contact-1"
    assert main.get("first_name") == "Dan"
    assert main.get("business") == "Ridgeline Floors"
    assert main.get("email") == "dan@ridgelinefloors.com"


@pytest.mark.asyncio
async def test_main_contact_update_is_best_effort_does_not_block_booking():
    """If the main-contact name update fails (network blip, GHL 5xx), the
    booking must still succeed — name update is best-effort, the appointment
    is the load-bearing artifact."""
    _seed_cache("call-name-2",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00"})

    async def fail_main_update(**kw):
        if kw.get("crm_key") == "main":
            raise RuntimeError("simulated GHL 503")
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(side_effect=fail_main_update)), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-contact-2"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "appt-2"}})):
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "dan@ridgelinefloors.com",
             "prospect_first_name": "Dan",
             "business_name": "Ridgeline Floors"},
            "call-name-2", "+15551234567",
        )

    assert "Booked" in result, "booking must succeed even when main-contact update fails"


@pytest.mark.asyncio
async def test_main_contact_update_fires_for_email_only_when_name_missing():
    """Per reviewer: email is included in the guard because the texts→main
    snapshot's email is often blank while the booking-time email is fresh.
    Even if the LLM forgot the name, the email still gets written."""
    server._call_cache["call-email-only"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": {"id": "tc1", "crm": "texts1"},
        "lookup_done": True,
        "captured": {"first_name": "", "last_name": "", "business": "", "email": ""},
        "offered_slots": {"2026-05-09T10:00:00-04:00"},
    }

    update_calls: list[dict] = []

    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(side_effect=record_update)), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-email"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})):
        await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "fresh@booking.com",
             "prospect_first_name": "",  # empty — agent didn't capture
             "business_name": ""},
            "call-email-only", "+15551234567",
        )

    main_updates = [c for c in update_calls if c.get("crm_key") == "main"]
    assert main_updates, "main update must fire to write the fresh email"
    assert main_updates[0].get("email") == "fresh@booking.com"


@pytest.mark.asyncio
async def test_main_contact_update_uses_cached_fields_when_args_empty():
    """If `book_video_call` args don't carry a name (LLM forgot to pass it)
    but the cache has captured fields from earlier in the call (discovery
    dialog, mark_for_transfer, etc.), the main update must still fire with
    the cached values. The contract is: 'use whatever real data we have'."""
    server._call_cache["call-cached"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": {"id": "tc1", "crm": "texts1"},
        "lookup_done": True,
        "captured": {
            "first_name": "Cached-Dan",
            "last_name": "",
            "business": "Cached-Floors",
            "email": "",
        },
        "offered_slots": {"2026-05-09T10:00:00-04:00"},
    }

    update_calls: list[dict] = []

    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(side_effect=record_update)), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-cached"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})):
        await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "x@y.com",
             # Note: no prospect_first_name or business_name in args.
             },
            "call-cached", "+15551234567",
        )

    main_updates = [c for c in update_calls if c.get("crm_key") == "main"]
    assert main_updates, "main update must fire using cached fields when args are empty"
    main = main_updates[0]
    assert main.get("first_name") == "Cached-Dan"
    assert main.get("business") == "Cached-Floors"


@pytest.mark.asyncio
async def test_offered_set_accumulates_across_multiple_get_slots_calls():
    """Both old AND new ISOs must be present after a re-fetch."""
    _seed_cache("call-acc", {"id": "c1", "timezone": "America/New_York"})

    first_slots = {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}
    second_slots = {"2026-05-10": {"slots": ["2026-05-10T11:00:00-04:00"]}}

    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(side_effect=[first_slots, second_slots])):
        await server._handle_get_available_slots({}, "call-acc", "+15551234567")
        await server._handle_get_available_slots({}, "call-acc", "+15551234567")

    offered = server._call_cache["call-acc"]["offered_slots"]
    # The contract is *accumulate* — both fetches' ISOs are valid for booking.
    assert "2026-05-09T10:00:00-04:00" in offered
    assert "2026-05-10T11:00:00-04:00" in offered


# --------------------------------------------------------------------------- #
# Reviewer A finding — ISO normalization
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_accepts_iso_with_canonical_reformat():
    """The LLM may rewrite the ISO it received (drops seconds, swaps `+04:00`
    for the same offset spelled differently, adds whitespace from TTS round-
    trips). We compare canonical isoformat() on both sides so a same-instant
    ISO is accepted regardless of textual reformatting."""
    _seed_cache("call-norm",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00"})

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})) as book_mock:
        # LLM sent the same instant with surrounding whitespace.
        result = await server._handle_book_video_call(
            {"slot_iso": " 2026-05-09T10:00:00-04:00 ",
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-norm", "+15551234567",
        )

    book_mock.assert_awaited_once()
    assert "Booked" in result


@pytest.mark.asyncio
async def test_book_video_call_still_rejects_truly_different_instant():
    """ISO normalization must NOT cause same-day-different-hour to slip
    through. 10:00 vs 11:00 are different absolute moments → reject."""
    _seed_cache("call-diff",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00"})

    with patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T11:00:00-04:00",  # different hour
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-diff", "+15551234567",
        )

    book_mock.assert_not_awaited()
    assert "wasn't in" in result.lower() or "get_available_slots" in result


# --------------------------------------------------------------------------- #
# Wrong-year snap — LLM stamps the wrong year/offset instead of copying [iso:]
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_snaps_wrong_year_to_offered():
    """Observed on live calls (2026-06): GPT-4.1 rebuilds the timestamp from
    its training prior — it keeps the right month/day/time but stamps year
    2024 (and sometimes a wrong offset) instead of copying the [iso:...] value.
    When the wall-clock month/day/hour/minute uniquely matches one offered
    slot, snap to that offered ISO and book it rather than rejecting."""
    _seed_cache("call-snap",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-06-14T19:00:00-05:00"})

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2024-06-14T19:00:00-07:00",  # wrong year + wrong offset
             "email": "x@y.com", "prospect_first_name": "Ryan"},
            "call-snap", "+15551234567",
        )

    book_mock.assert_awaited_once()
    # The booked slot must be the authoritative offered ISO (right year+offset),
    # not the LLM's mangled value.
    assert book_mock.await_args.kwargs.get("slot_iso") == "2026-06-14T19:00:00-05:00"
    assert "Booked" in result


@pytest.mark.asyncio
async def test_book_video_call_snap_does_not_fire_on_different_time():
    """Snap is conservative: a wrong year combined with a DIFFERENT hour is not
    a recoverable year-typo — there's no single safe slot to snap to, so it must
    still reject (never silently book a time the prospect didn't agree to)."""
    _seed_cache("call-snap-diff",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-06-14T19:00:00-05:00"})

    with patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2024-06-14T11:00:00-05:00",  # wrong year AND wrong hour
             "email": "x@y.com", "prospect_first_name": "Ryan"},
            "call-snap-diff", "+15551234567",
        )

    book_mock.assert_not_awaited()
    assert "wasn't in" in result.lower() or "get_available_slots" in result


@pytest.mark.asyncio
async def test_book_video_call_snap_skips_when_ambiguous():
    """If two offered slots share the same wall-clock month/day/hour/minute
    (different offset/year), the snap target is ambiguous → reject rather than
    guess. Defensive; should never happen with a single calendar's slots."""
    _seed_cache("call-snap-ambig",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-06-14T19:00:00-05:00", "2025-06-14T19:00:00-04:00"})

    with patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True})) as book_mock:
        result = await server._handle_book_video_call(
            {"slot_iso": "2024-06-14T19:00:00-07:00",
             "email": "x@y.com", "prospect_first_name": "Ryan"},
            "call-snap-ambig", "+15551234567",
        )

    book_mock.assert_not_awaited()
    assert "wasn't in" in result.lower() or "get_available_slots" in result


# --------------------------------------------------------------------------- #
# Reviewer A finding — race in batched parallel tool calls
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dispatcher_runs_get_slots_before_book_in_same_batch():
    """If Vapi ever batches `get_available_slots` + `book_video_call` in one
    request, the slot stash MUST complete before book reads — otherwise book
    sees empty offered_slots and degraded-mode-allows a hallucinated slot.

    Strengthened: `get_available_slots` is artificially delayed. If the
    dispatcher runs them in parallel (current bug), book finishes first under
    degraded mode. The test asserts book WAITED for slots and validated
    against a non-empty offered set."""
    import asyncio
    server._call_cache.clear()

    book_observed_offered: list[set] = []

    async def slow_slots(**kw):
        await asyncio.sleep(0.3)  # plenty of time for parallel book to finish first
        return {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}

    async def fake_book(**kw):
        # Snapshot what offered_slots looked like at the moment book ran.
        entry = server._call_cache.get("race-call-1") or {}
        book_observed_offered.append(set(entry.get("offered_slots") or set()))
        return {"ok": True, "appointment": {"id": "a"}}

    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "race-call-1", "customer": {"number": "+15551234567"}},
            "toolCallList": [
                # Book listed FIRST in the batch — race-prone if not sequenced.
                {"id": "tc-book", "function": {
                    "name": "book_video_call",
                    "arguments": '{"slot_iso":"2026-05-09T10:00:00-04:00","email":"x@y.com","prospect_first_name":"Dan"}',
                }},
                {"id": "tc-slots", "function": {
                    "name": "get_available_slots",
                    "arguments": '{"days_ahead":7}',
                }},
            ],
        }
    }

    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    with patch.object(server.ghl, "get_available_slots", new=AsyncMock(side_effect=slow_slots)), \
         patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",       new=AsyncMock(side_effect=fake_book)):
        resp = client.post("/vapi/tool", json=body)

    assert resp.status_code == 200
    # If sequencing works, by the time book ran, offered_slots had the ISO.
    # If the race exists, book ran while slots was still sleeping → empty set.
    assert book_observed_offered, "book_video_call must have run"
    assert "2026-05-09T10:00:00-04:00" in book_observed_offered[0], \
        "race detected: book ran before get_available_slots stashed its ISOs"


# --------------------------------------------------------------------------- #
# Reviewer A finding — polling hard ceiling
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_caps_polling_at_15s_wall_clock():
    """If `wait_for_main_crm_contact` takes longer than 15s (e.g. inner httpx
    timeouts blow the documented budget), the handler must abort it. We're
    not willing to risk breaching Vapi's 60s tool budget for a slow GHL."""
    import asyncio

    _seed_cache("call-cap",
                {"id": "tc1", "crm": "texts1"},
                offered={"2026-05-09T10:00:00-04:00"})

    async def slow_poll(**kw):
        # Simulates GHL slowness — would hang for 30s if uncapped.
        await asyncio.sleep(30)
        return {"id": "main-late"}

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(return_value={"ok": True})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(side_effect=slow_poll)), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})):
        start = time.monotonic()
        result = await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "x@y.com", "prospect_first_name": "Dan"},
            "call-cap", "+15551234567",
        )
        elapsed = time.monotonic() - start

    # Hard ceiling: must abort polling before 18s (15s cap + small slack).
    assert elapsed < 18.0, f"polling exceeded 15s hard ceiling — elapsed {elapsed:.1f}s"
    # And must return a graceful error message, not crash.
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_offered_set_persists_across_multiple_get_slots_calls():
    """Calling get_available_slots twice in one call session must accumulate
    (or at minimum reflect) the most-recently-offered set so the agent can
    re-pick after a re-fetch."""
    _seed_cache("call-multi", {"id": "c1", "timezone": "America/New_York"})

    first_slots = {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}
    second_slots = {"2026-05-10": {"slots": ["2026-05-10T11:00:00-04:00"]}}

    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(side_effect=[first_slots, second_slots])):
        await server._handle_get_available_slots({}, "call-multi", "+15551234567")
        await server._handle_get_available_slots({}, "call-multi", "+15551234567")

    offered = server._call_cache["call-multi"]["offered_slots"]
    # The second fetch's ISOs must be present (re-pick after re-fetch must work).
    assert "2026-05-10T11:00:00-04:00" in offered


# --------------------------------------------------------------------------- #
# GHL booking payload — no client-supplied meeting title
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_payload_omits_title(monkeypatch):
    """The GHL appointment payload must NOT carry a `title`. GoHighLevel
    generates the meeting title from the calendar's own config; sending our
    own overrides that and produces an inconsistent name in the dashboard."""
    import json
    import httpx
    from src.agent import ghl

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "appt-xyz"})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    result = await ghl.book_video_call(
        "2026-05-09T10:00:00-04:00", "contact-123", email="dan@example.com"
    )

    assert result["ok"] is True, f"expected ok booking, got {result!r}"
    assert "title" not in captured["body"], (
        "booking payload must not send a title — GHL auto-generates it; "
        f"got {captured['body']!r}"
    )


@pytest.mark.asyncio
async def test_book_video_call_books_one_hour_duration(monkeypatch):
    """The GHL appointment must span 60 minutes (endTime = startTime + 1h).
    Jon's video calls run an hour; a 30-min endTime under-books his calendar."""
    import json
    from datetime import datetime
    import httpx
    from src.agent import ghl

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "appt-xyz"})

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    result = await ghl.book_video_call(
        "2026-05-09T10:00:00-04:00", "contact-123", email="dan@example.com"
    )

    assert result["ok"] is True, f"expected ok booking, got {result!r}"
    start = datetime.fromisoformat(captured["body"]["startTime"])
    end = datetime.fromisoformat(captured["body"]["endTime"])
    assert (end - start).total_seconds() == 3600, (
        "video call must be 60 minutes; got "
        f"{(end - start).total_seconds() / 60:.0f} min "
        f"(start={captured['body']['startTime']} end={captured['body']['endTime']})"
    )
