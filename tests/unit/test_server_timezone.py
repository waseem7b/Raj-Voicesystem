"""TDD tests for the webhook tool handler — timezone forwarding.

`_handle_get_available_slots` must:
  1. Pull the cached prospect timezone (loaded by `_ensure_contact_loaded`).
  2. Pass it to `ghl.get_available_slots(timezone=...)`.
  3. Default to empty string when no contact / no timezone.
  4. Format the resulting slots into the human-readable response the LLM sees,
     preserving the offset GHL returned (the `[iso:...]` tag).

Tests pre-seed `_call_cache` with `lookup_done=True`, which short-circuits
`_ensure_contact_loaded` before it issues any GHL request. That keeps these
tests focused on the read path; an integration test elsewhere should cover
the actual lookup-then-read flow.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.webhooks import server


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the per-call cache between tests so entries don't bleed across."""
    server._call_cache.clear()
    yield
    server._call_cache.clear()


def _seed_cache(call_id: str, contact: dict | None) -> None:
    """Pre-populate the call cache as if `_ensure_contact_loaded` already ran.

    Use `time.monotonic()` for `created` so the entry isn't TTL-evicted on read.
    """
    server._call_cache[call_id] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": contact,
        "lookup_done": True,
        "captured": {"first_name": "", "last_name": "", "business": "", "email": ""},
    }


@pytest.mark.asyncio
async def test_handle_get_available_slots_forwards_cached_tz():
    """Handler reads `entry.contact.timezone` and passes it to ghl.get_available_slots."""
    _seed_cache("call-1", {"id": "c1", "timezone": "America/Phoenix"})

    fake_slots = {
        "2026-05-09": {
            "slots": ["2026-05-09T08:00:00-07:00", "2026-05-09T09:00:00-07:00"]
        }
    }
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        result = await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-1", "+15551234567"
        )

    mocked.assert_awaited_once()
    kwargs = mocked.await_args.kwargs
    assert kwargs.get("timezone") == "America/Phoenix"
    assert kwargs.get("days_ahead") == 7
    # Sanity: the formatter still produces the [iso:...] tags from the slots GHL returned.
    assert "[iso:2026-05-09T08:00:00-07:00]" in result


@pytest.mark.asyncio
async def test_handle_get_available_slots_falls_back_to_state_when_tz_missing():
    """Contact has no `timezone` but has city/state → resolver fills it.
    Never serve calendar-default (Bogota) times to a US prospect."""
    _seed_cache("call-2", {"id": "c2", "city": "Los Angeles", "state": "CA"})

    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T08:00:00-07:00"]}}
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-2", "+15551234567"
        )

    assert mocked.await_args.kwargs.get("timezone") == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_handle_get_available_slots_split_state_uses_city_override():
    """Texas (split state) — El Paso resolves to Mountain, not Central."""
    _seed_cache("call-tx", {"id": "ctx", "city": "El Paso", "state": "TX"})

    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T08:00:00-06:00"]}}
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-tx", "+15555550000"
        )

    assert mocked.await_args.kwargs.get("timezone") == "America/Denver"


@pytest.mark.asyncio
async def test_handle_get_available_slots_no_contact_falls_back_to_eastern():
    """No contact at all (lookup miss) → resolver hard-defaults to Eastern,
    NEVER serves calendar-default (Bogota) times. Loud error logged."""
    _seed_cache("call-3", None)

    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-3", "+15551234567"
        )

    forwarded = mocked.await_args.kwargs.get("timezone")
    assert forwarded == "America/New_York"
    assert forwarded != "America/Bogota"
    assert forwarded != ""


@pytest.mark.asyncio
async def test_handle_get_available_slots_drops_invalid_iana_uses_state():
    """If GHL stored garbage (e.g. 'PST'), the resolver drops it and uses
    city/state instead — never forwards garbage to GHL or Bogota times to the agent."""
    _seed_cache("call-bad", {"id": "cbad", "timezone": "PST", "city": "Brooklyn", "state": "NY"})

    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}
    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        await server._handle_get_available_slots(
            {"days_ahead": 7}, "call-bad", "+15551234567"
        )

    assert mocked.await_args.kwargs.get("timezone") == "America/New_York"


@pytest.mark.asyncio
async def test_dispatcher_forwards_call_id_and_phone_to_slots_handler():
    """The dispatcher must pass call_id+phone through so the handler can read the cache."""
    _seed_cache("dispatch-1", {"id": "c1", "timezone": "America/Chicago"})

    tool_call = {
        "id": "tc-abc",
        "function": {"name": "get_available_slots", "arguments": "{\"days_ahead\":7}"},
    }
    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T09:00:00-06:00"]}}

    with patch.object(server.ghl, "get_available_slots",
                      new=AsyncMock(return_value=fake_slots)) as mocked:
        result = await server._dispatch_tool_call(
            tool_call, message={}, call_id="dispatch-1", phone="+15551234567"
        )

    assert result["toolCallId"] == "tc-abc"
    # If the handler signature change is wired through, the cached TZ reaches GHL.
    assert mocked.await_args.kwargs.get("timezone") == "America/Chicago"
