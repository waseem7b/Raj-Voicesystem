"""TDD tests for `update_prospect_info` — fire-and-forget early capture.

Contract:
  1. Handler returns IMMEDIATELY (<50ms) regardless of GHL latency.
  2. Captured fields are written to the cache synchronously, before return.
  3. The actual GHL write runs in a background task (asyncio.create_task).
  4. `book_video_call` prefers cached fields over its args (cache is the
     authoritative source of truth — captured at the moment the prospect
     said it, not 5 minutes later from the LLM's degrading short-term memory).
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.webhooks import server


@pytest.fixture(autouse=True)
def _clear_cache():
    server._call_cache.clear()
    yield
    server._call_cache.clear()


# --------------------------------------------------------------------------- #
# Latency — handler returns instantly even when GHL is glacially slow
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_prospect_info_returns_in_under_50ms_even_when_ghl_slow():
    """Even if the GHL update takes 30 seconds, the handler must return
    in milliseconds. The LLM keeps talking — no perceived delay."""
    async def slow_ghl_update(**kw):
        await asyncio.sleep(30)
        return {"ok": True}

    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(side_effect=slow_ghl_update)):
        start = time.monotonic()
        result = await server._handle_update_prospect_info(
            {"first_name": "Dan", "business_name": "Ridgeline Floors"},
            "call-fast", "+15551234567",
        )
        elapsed_ms = (time.monotonic() - start) * 1000

    # LLM round-trip budget for instant tools is ~50ms server-side.
    assert elapsed_ms < 50, f"handler took {elapsed_ms:.0f}ms — must be <50ms"
    assert isinstance(result, str)


# --------------------------------------------------------------------------- #
# Sync cache stash — captured fields available IMMEDIATELY for book_video_call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_prospect_info_stashes_in_cache_synchronously():
    """The handler returns. The cache MUST already have the captured fields.
    book_video_call (which may run any time after) must see them."""
    async def slow_ghl_update(**kw):
        await asyncio.sleep(10)
        return {"ok": True}

    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(side_effect=slow_ghl_update)):
        await server._handle_update_prospect_info(
            {"first_name": "Dan", "business_name": "Ridgeline Floors",
             "email": "dan@example.com"},
            "call-stash", "+15551234567",
        )

    captured = server._call_cache["call-stash"]["captured"]
    assert captured["first_name"] == "Dan"
    assert captured["business"] == "Ridgeline Floors"
    assert captured["email"] == "dan@example.com"


@pytest.mark.asyncio
async def test_update_prospect_info_partial_args_preserves_existing_cache():
    """Calling with just first_name shouldn't blow away an earlier-set business."""
    server._call_cache["call-partial"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": None,
        "lookup_done": False,
        "captured": {"first_name": "", "last_name": "",
                     "business": "Ridgeline Floors",  # set by an earlier call
                     "email": ""},
        "offered_slots": set(),
    }

    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(return_value={"ok": True})):
        await server._handle_update_prospect_info(
            {"first_name": "Dan"},  # only first name in this call
            "call-partial", "+15551234567",
        )

    captured = server._call_cache["call-partial"]["captured"]
    assert captured["first_name"] == "Dan"
    assert captured["business"] == "Ridgeline Floors", "earlier-captured business must be preserved"


# --------------------------------------------------------------------------- #
# Background task — GHL write happens, just not on the critical path
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_prospect_info_eventually_writes_to_ghl():
    """The background task must actually call ghl.update_prospect_fields.
    We can't await the task directly from the handler, so wait briefly."""
    update_calls: list[dict] = []

    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1",
                                                   "first_name": "", "business": "",
                                                   "email": ""})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(side_effect=record_update)):
        await server._handle_update_prospect_info(
            {"first_name": "Dan", "business_name": "Ridgeline Floors"},
            "call-bg", "+15551234567",
        )
        # Give the background task a tick to run.
        for _ in range(50):
            if update_calls:
                break
            await asyncio.sleep(0.02)

    assert update_calls, "background task must call ghl.update_prospect_fields"
    call = update_calls[0]
    assert call.get("first_name") == "Dan"
    assert call.get("business") == "Ridgeline Floors"


# --------------------------------------------------------------------------- #
# Cache as source of truth in book_video_call
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_book_video_call_prefers_cached_name_over_args_when_both_present():
    """Captured was set in real-time when the prospect spoke. Args came from
    LLM memory 5 minutes later. If they conflict, trust captured."""
    server._call_cache["call-pref"] = {
        "created": time.monotonic(),
        "phone": "+15551234567",
        "contact": {"id": "tc1", "crm": "texts1"},
        "lookup_done": True,
        "captured": {"first_name": "Dan",          # captured early — accurate
                     "last_name": "",
                     "business": "Ridgeline Floors",
                     "email": ""},
        "offered_slots": {"2026-05-09T10:00:00-04:00"},
    }

    update_calls: list[dict] = []
    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "update_prospect_fields", new=AsyncMock(side_effect=record_update)), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-pref"})), \
         patch.object(server.ghl, "book_video_call",
                      new=AsyncMock(return_value={"ok": True, "appointment": {"id": "a"}})):
        await server._handle_book_video_call(
            {"slot_iso": "2026-05-09T10:00:00-04:00",
             "email": "dan@example.com",
             "prospect_first_name": "DANIEL",            # LLM hallucinated/changed
             "business_name": "Wrong Business Name"},   # LLM drifted
            "call-pref", "+15551234567",
        )

    # The MAIN-contact update must use the cached values, not the args.
    main_updates = [c for c in update_calls if c.get("crm_key") == "main"]
    assert main_updates, "main update must fire"
    main = main_updates[0]
    assert main.get("first_name") == "Dan", \
        f"cached 'Dan' must beat args 'DANIEL'; got {main.get('first_name')!r}"
    assert main.get("business") == "Ridgeline Floors", \
        f"cached business must beat args; got {main.get('business')!r}"


# --------------------------------------------------------------------------- #
# Multiple update_prospect_info calls in one batch (LLM captures name + email
# in the same turn — realistic case)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_two_update_prospect_info_in_same_batch_both_stash():
    """LLM may capture name AND email from a single prospect utterance and
    emit two `update_prospect_info` tool calls in the same batch. Both must
    land in the cache — different fields, no last-write-wins conflict."""
    server._call_cache.clear()

    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "two-updates", "customer": {"number": "+15551234567"}},
            "toolCallList": [
                {"id": "tc-name", "function": {
                    "name": "update_prospect_info",
                    "arguments": '{"first_name":"Dan"}',
                }},
                {"id": "tc-email", "function": {
                    "name": "update_prospect_info",
                    "arguments": '{"email":"dan@example.com"}',
                }},
            ],
        }
    }

    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(return_value={"ok": True, "error": None})):
        resp = client.post("/vapi/tool", json=body)

    assert resp.status_code == 200
    captured = server._call_cache["two-updates"]["captured"]
    assert captured["first_name"] == "Dan"
    assert captured["email"] == "dan@example.com"


# --------------------------------------------------------------------------- #
# New caller — no contact in CRM at all (lookup miss)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_update_prospect_info_handles_new_caller_no_contact():
    """When the prospect's phone has no contact in any CRM yet, the cache
    stash still happens (so book_video_call can use it later when it creates
    the contact). The background GHL update is a no-op (no contact to update)
    but must NOT raise."""
    update_calls: list[dict] = []

    async def record_update(**kw):
        update_calls.append(kw)
        return {"ok": True, "error": None}

    with patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value=None)), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(side_effect=record_update)):
        result = await server._handle_update_prospect_info(
            {"first_name": "Dan", "business_name": "Ridgeline Floors"},
            "call-newcaller", "+15559999999",
        )
        # Give the background task a chance to land.
        for _ in range(50):
            await asyncio.sleep(0.02)
            if "call-newcaller" in server._call_cache:
                if server._call_cache["call-newcaller"].get("lookup_done"):
                    break

    # Sync return is "ok" regardless.
    assert isinstance(result, str)
    # Cache stashed.
    captured = server._call_cache["call-newcaller"]["captured"]
    assert captured["first_name"] == "Dan"
    assert captured["business"] == "Ridgeline Floors"
    # Background must have early-returned without calling update_prospect_fields
    # (no contact id to update).
    assert not update_calls, "no GHL update when contact doesn't exist"


# --------------------------------------------------------------------------- #
# Dispatcher sequencing — update_prospect_info runs before later tools in batch
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dispatcher_runs_update_prospect_info_before_book_in_same_batch():
    """If update_prospect_info and book_video_call land in the same batch,
    the cache stash from update_prospect_info MUST happen first so book sees
    the captured name."""
    server._call_cache.clear()

    book_observed_captured: list[dict] = []
    async def fake_book(**kw):
        entry = server._call_cache.get("race-name") or {}
        book_observed_captured.append(dict(entry.get("captured") or {}))
        return {"ok": True, "appointment": {"id": "a"}}

    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": "race-name", "customer": {"number": "+15551234567"}},
            "toolCallList": [
                # Book listed FIRST — race-prone if not sequenced.
                {"id": "tc-book", "function": {
                    "name": "book_video_call",
                    "arguments": '{"slot_iso":"2026-05-09T10:00:00-04:00","email":"x@y.com","prospect_first_name":"FromArgs"}',
                }},
                {"id": "tc-update", "function": {
                    "name": "update_prospect_info",
                    "arguments": '{"first_name":"Dan","business_name":"Ridgeline"}',
                }},
                {"id": "tc-slots", "function": {
                    "name": "get_available_slots",
                    "arguments": '{"days_ahead":7}',
                }},
            ],
        }
    }

    fake_slots = {"2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}}

    from fastapi.testclient import TestClient
    client = TestClient(server.app)
    with patch.object(server.ghl, "get_available_slots", new=AsyncMock(return_value=fake_slots)), \
         patch.object(server.ghl, "lookup_contact_in_texts_crms",
                      new=AsyncMock(return_value={"id": "tc1", "crm": "texts1"})), \
         patch.object(server.ghl, "update_prospect_fields",
                      new=AsyncMock(return_value={"ok": True, "error": None})), \
         patch.object(server.ghl, "add_tag",                new=AsyncMock(return_value=True)), \
         patch.object(server.ghl, "wait_for_main_crm_contact",
                      new=AsyncMock(return_value={"id": "main-1"})), \
         patch.object(server.ghl, "book_video_call",        new=AsyncMock(side_effect=fake_book)):
        resp = client.post("/vapi/tool", json=body)

    assert resp.status_code == 200
    assert book_observed_captured, "book must have run"
    assert book_observed_captured[0].get("first_name") == "Dan", (
        "update_prospect_info must complete its sync cache stash before book reads — "
        f"saw captured={book_observed_captured[0]}"
    )
