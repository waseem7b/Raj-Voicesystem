"""TDD tests for GHL-side timezone handling.

Pins behavior the production change must satisfy:
  1. Contact lookup returns the IANA `timezone` field GHL sends.
  2. `get_available_slots` forwards a `timezone` query param to GHL when set,
     and omits it when blank/missing.
"""
import httpx
import pytest

from src.agent import ghl


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _contact_payload(timezone: str | None) -> dict:
    """Shape of the GHL /contacts/search/duplicate response (subset)."""
    contact = {
        "id": "contact-123",
        "firstName": "Dan",
        "companyName": "Ridgeline Floors",
        "email": "dan@example.com",
        "phone": "+15551234567",
        "city": "Portland",
        "state": "OR",
        "tags": [],
        "customFields": [],
    }
    if timezone is not None:
        contact["timezone"] = timezone
    return {"contact": contact}


def _slots_payload() -> dict:
    """Single-day GHL free-slots response."""
    return {
        "2026-05-09": {
            "slots": [
                "2026-05-09T10:00:00-05:00",
                "2026-05-09T11:00:00-05:00",
            ]
        }
    }


def _mock_transport(handler):
    """Build an httpx MockTransport from a single sync handler `(request) -> Response`."""
    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------- #
# Contact lookup — extracts timezone
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_lookup_contact_extracts_timezone(monkeypatch):
    """When GHL returns a contact with `timezone`, the dict we return includes it."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_contact_payload("America/Los_Angeles"))

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        result = await ghl.lookup_contact_in_crm(client, "texts1", "+15551234567")

    assert result is not None, "expected a contact dict, not None"
    assert result.get("timezone") == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_lookup_contact_missing_timezone_defaults_to_empty():
    """If GHL omits the timezone field, our dict should carry an empty string (never raise/None)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_contact_payload(None))

    async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
        result = await ghl.lookup_contact_in_crm(client, "texts1", "+15551234567")

    assert result is not None
    assert result.get("timezone") == ""


# --------------------------------------------------------------------------- #
# get_available_slots — forwards timezone param
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_get_available_slots_forwards_timezone_param(monkeypatch):
    """When called with timezone='America/New_York', the GHL request URL has that param."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_slots_payload())

    # Monkeypatch httpx.AsyncClient so the call inside ghl.get_available_slots
    # uses our mock transport.
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    await ghl.get_available_slots(days_ahead=7, timezone="America/New_York")

    assert captured.get("params", {}).get("timezone") == "America/New_York"


@pytest.mark.asyncio
async def test_get_available_slots_omits_timezone_when_blank(monkeypatch):
    """When called with timezone='', the GHL request URL must NOT include timezone."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_slots_payload())

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    await ghl.get_available_slots(days_ahead=7, timezone="")

    assert "timezone" not in captured.get("params", {}), \
        f"timezone must not be sent when blank; got params={captured.get('params')}"


@pytest.mark.asyncio
async def test_get_available_slots_default_call_omits_timezone(monkeypatch):
    """Calling with no timezone kwarg defaults to empty / not sent."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_slots_payload())

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)

    await ghl.get_available_slots(days_ahead=7)

    assert "timezone" not in captured.get("params", {})
