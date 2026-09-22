"""has_call_dnd: reading GHL Do Not Disturb before a call.

Shapes taken from real contacts on 2026-09-13: `dnd` is a boolean, and
`dndSettings` maps a channel name to {"status": ...}. Lewain Dysart carried
{"SMS": {"status": "active"}} from a Twilio 30005 carrier error, which must not
stop a phone call.
"""
import pytest

from src.agent import ghl


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **kw):
        return self._response


@pytest.fixture
def contact(monkeypatch):
    def _set(payload, status_code=200):
        monkeypatch.setattr(ghl.httpx, "AsyncClient",
                            lambda *a, **kw: _Client(_Resp(status_code, payload)))
    return _set


@pytest.mark.asyncio
async def test_global_dnd_blocks_the_call(contact):
    contact({"contact": {"dnd": True}})
    assert await ghl.has_call_dnd("c1") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "permanent", "Active"])
async def test_call_channel_dnd_blocks_the_call(contact, status):
    contact({"contact": {"dnd": False, "dndSettings": {"Call": {"status": status}}}})
    assert await ghl.has_call_dnd("c1") is True


@pytest.mark.asyncio
async def test_inactive_call_dnd_does_not_block(contact):
    contact({"contact": {"dnd": False, "dndSettings": {"Call": {"status": "inactive"}}}})
    assert await ghl.has_call_dnd("c1") is False


@pytest.mark.asyncio
async def test_sms_only_dnd_does_not_block_a_call(contact):
    contact({"contact": {"dnd": False, "dndSettings": {"SMS": {"status": "active"}}}})
    assert await ghl.has_call_dnd("c1") is False


@pytest.mark.asyncio
async def test_no_dnd_settings_at_all(contact):
    contact({"contact": {"dnd": False, "dndSettings": {}}})
    assert await ghl.has_call_dnd("c1") is False


@pytest.mark.asyncio
async def test_a_ghl_error_fails_open(contact):
    contact({}, status_code=500)
    assert await ghl.has_call_dnd("c1") is False
