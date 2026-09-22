"""End-to-end integration: FastAPI webhook → handler → ghl client → mocked GHL HTTP.

Exercises the real call path (no monkeypatching of internal handlers) with the
GHL HTTP layer mocked at httpx-transport level. Confirms:
  1. The timezone GHL stored on the contact reaches the GHL `/free-slots`
     request as a query param.
  2. The webhook tool response carries slot ISOs in the prospect's offset.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.agent import ghl
from src.webhooks import server


@pytest.fixture(autouse=True)
def _clear_call_cache():
    server._call_cache.clear()
    yield
    server._call_cache.clear()


def _make_client_factory(handler):
    """Replace ghl's `httpx.AsyncClient` with one whose transport is our mock.

    Returns the patched callable so we can monkeypatch it onto the ghl module.
    """
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    return _factory


def _mk_vapi_tool_call(call_id: str, phone: str, tool_name: str, args: dict) -> dict:
    """Build a minimal Vapi `tool-calls` message body."""
    return {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id, "customer": {"number": phone}},
            "toolCallList": [
                {
                    "id": "tc-int-1",
                    "function": {"name": tool_name, "arguments": json.dumps(args)},
                }
            ],
        }
    }


def test_get_available_slots_end_to_end_forwards_prospect_tz(monkeypatch):
    """Full path: webhook receives Vapi tool-call → handler → ghl → GHL HTTP.

    GHL contact lookup returns a contact with timezone=America/Los_Angeles.
    Free-slots endpoint returns Pacific-offset ISOs.
    Verify the GHL /free-slots request has timezone=America/Los_Angeles
    and the webhook response carries the [iso:...] tags in Pacific offset.
    """
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-contact-1",
                    "firstName": "Dan",
                    "companyName": "Ridgeline Floors",
                    "email": "dan@example.com",
                    "phone": "+15551234567",
                    "city": "Los Angeles",
                    "state": "CA",
                    "timezone": "America/Los_Angeles",
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            # Calling shape GHL would actually return when given timezone=America/Los_Angeles.
            return httpx.Response(200, json={
                "2026-05-09": {
                    "slots": [
                        "2026-05-09T08:00:00-07:00",
                        "2026-05-09T11:00:00-07:00",
                    ]
                }
            })
        return httpx.Response(404, json={"error": f"unhandled path {path}"})

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-call-1", "+15551234567", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    payload = resp.json()
    assert payload["results"][0]["toolCallId"] == "tc-int-1"
    result_text = payload["results"][0]["result"]

    # The slot ISOs the agent sees must be in Pacific offset.
    assert "[iso:2026-05-09T08:00:00-07:00]" in result_text
    assert "[iso:2026-05-09T11:00:00-07:00]" in result_text

    # The /free-slots request must have included the timezone query param.
    free_slots_requests = [r for r in seen_requests if "/free-slots" in r.url.path]
    assert len(free_slots_requests) == 1
    assert free_slots_requests[0].url.params.get("timezone") == "America/Los_Angeles"


def test_get_available_slots_end_to_end_lookup_miss_falls_back_to_eastern(monkeypatch):
    """When GHL has no contact for the phone, the resolver hard-defaults to
    America/New_York (NEVER to the calendar default / Bogota). The /free-slots
    request must carry that resolved tz, not be empty."""
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            # Empty contact = lookup miss in our convention.
            return httpx.Response(200, json={"contact": None})
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-10": {"slots": ["2026-05-10T10:00:00-04:00"]}
            })
        return httpx.Response(404, json={"error": "unhandled"})

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-call-2", "+15559999999", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen_requests if "/free-slots" in r.url.path]
    assert len(free_slots_requests) == 1
    forwarded_tz = free_slots_requests[0].url.params.get("timezone")
    assert forwarded_tz == "America/New_York", \
        f"lookup miss must default to Eastern, never blank; got {forwarded_tz!r}"


def test_get_available_slots_when_ghl_lookup_is_slow(monkeypatch):
    """GHL contact lookup is slow enough to fail (httpx ReadTimeout fires first
    at the inner 4s timeout; if it didn't, the outer asyncio.wait_for(4s) in
    `_ensure_contact_loaded` would catch it). Either path leaves contact=None,
    the resolver hard-defaults to Eastern, and slots are STILL served. The key
    invariant: the prospect never gets Bogota times because GHL was slow."""
    import asyncio

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            await asyncio.sleep(10)  # blows past both 4s timeouts
            return httpx.Response(200, json={"contact": None})
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}
            })
        return httpx.Response(404)

    seen_free_slots: list[httpx.Request] = []
    async def tracking_handler(request: httpx.Request) -> httpx.Response:
        if "/free-slots" in request.url.path:
            seen_free_slots.append(request)
        return await slow_handler(request)

    real_async_client = httpx.AsyncClient
    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(tracking_handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-timeout", "+15551111111", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    # Critical assertion: free-slots was requested with America/New_York (Eastern
    # default), NEVER absent (which would let GHL serve calendar-default Bogota times).
    assert len(seen_free_slots) == 1
    assert seen_free_slots[0].url.params.get("timezone") == "America/New_York"
    payload = resp.json()
    assert "[iso:" in payload["results"][0]["result"]


def test_get_available_slots_when_ghl_returns_500_on_lookup(monkeypatch):
    """GHL contact lookup 5xx → contact=None → resolver Eastern default.
    Slots are still served; never Bogota."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(503, json={"error": "GHL down"})
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-10": {"slots": ["2026-05-10T10:00:00-04:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-503", "+15552222222", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen if "/free-slots" in r.url.path]
    assert free_slots_requests[0].url.params.get("timezone") == "America/New_York"


def test_get_available_slots_contact_with_null_timezone_field(monkeypatch):
    """Contact returned with explicit `timezone: null` (JSON null), not absent.
    Resolver must handle the None gracefully and fall back to city/state."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-null-tz",
                    "firstName": "Mike",
                    "phone": "+15553333333",
                    "city": "Chicago",
                    "state": "IL",
                    "timezone": None,
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-05:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-null-tz", "+15553333333", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen if "/free-slots" in r.url.path]
    assert free_slots_requests[0].url.params.get("timezone") == "America/Chicago"


def test_get_available_slots_contact_with_only_state_and_no_tz(monkeypatch):
    """Contact has state but no timezone field at all. Resolver derives from state."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-state-only",
                    "firstName": "Carlos",
                    "phone": "+15554444444",
                    "city": "Phoenix",
                    "state": "AZ",
                    # no timezone field
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-07:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-state-only", "+15554444444", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen if "/free-slots" in r.url.path]
    assert free_slots_requests[0].url.params.get("timezone") == "America/Phoenix"


def test_get_available_slots_split_state_city_resolution_e2e(monkeypatch):
    """Full path: contact has only city+state for a split state (TX/El Paso).
    Resolver → Mountain. No stored timezone needed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-elpaso",
                    "firstName": "Hector",
                    "phone": "+19155551234",
                    "city": "El Paso",
                    "state": "TX",
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-06:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-elpaso", "+19155551234", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen if "/free-slots" in r.url.path]
    assert free_slots_requests[0].url.params.get("timezone") == "America/Denver"


def test_get_available_slots_legacy_us_eastern_in_contact_uses_state_instead(monkeypatch):
    """Contact stores legacy `US/Eastern` — fixed-offset risk. Resolver rejects it,
    derives from city/state instead."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-legacy",
                    "firstName": "Liz",
                    "phone": "+15555555555",
                    "city": "Boston",
                    "state": "MA",
                    "timezone": "US/Eastern",  # legacy alias — must be rejected
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-legacy", "+15555555555", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen if "/free-slots" in r.url.path]
    forwarded = free_slots_requests[0].url.params.get("timezone")
    assert forwarded == "America/New_York"
    assert forwarded != "US/Eastern"


def test_get_available_slots_invalid_iana_uses_state(monkeypatch):
    """Contact stored a non-IANA tz string ('PST'). Resolver drops it and
    derives from city/state — the /free-slots request carries the derived tz."""
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        path = request.url.path
        if path.endswith("/contacts/search/duplicate"):
            return httpx.Response(200, json={
                "contact": {
                    "id": "ghl-bad",
                    "firstName": "Bad",
                    "phone": "+15551234567",
                    "city": "Brooklyn",
                    "state": "NY",
                    "timezone": "PST",  # not IANA — should be discarded
                    "tags": [],
                    "customFields": [],
                }
            })
        if "/free-slots" in path:
            return httpx.Response(200, json={
                "2026-05-09": {"slots": ["2026-05-09T10:00:00-04:00"]}
            })
        return httpx.Response(404)

    factory = _make_client_factory(handler)
    monkeypatch.setattr(ghl.httpx, "AsyncClient", factory)

    client = TestClient(server.app)
    body = _mk_vapi_tool_call(
        "int-call-3", "+15551234567", "get_available_slots", {"days_ahead": 7}
    )
    resp = client.post("/vapi/tool", json=body)
    assert resp.status_code == 200, resp.text

    free_slots_requests = [r for r in seen_requests if "/free-slots" in r.url.path]
    assert free_slots_requests[0].url.params.get("timezone") == "America/New_York"
