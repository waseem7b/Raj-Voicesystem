"""The pre-dial thread read must return TEXTS, not GHL activity records.

A GHL conversation is not a message list. It also carries:
  TYPE_ACTIVITY_OPPORTUNITY   one per opportunity create / update / delete
  TYPE_ACTIVITY_APPOINTMENT   one per booking
  TYPE_INTERNAL_COMMENT       our own operator notes about the last call
  TYPE_EMAIL, TYPE_LIVE_CHAT

On call 01a08d64 (2026-09-10) all ten items in the window were activity records
and internal comments. Ashley was handed "You texted just now 'Opportunity
deleted'" as though we had sent it, and the prospect's five genuine inbound
texts were nowhere in the window.

That was not cosmetic. `should_hold_for_reply` reads this list, so with every
entry outbound it concluded we had spoken last and let the call go — when the
prospect had texted us last and the call should have paused. The owner's rule
is that any reply pauses everything; the guard was silently inert.

The fix is a SERVER-SIDE type filter. Filtering after the fact cannot work: the
limit truncates before we ever see the rows.
"""
import pytest

from src.agent import ghl


class _Resp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    """Records every GET so the messages query can be asserted."""

    calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None, timeout=None):
        type(self).calls.append((url, params))
        if "/conversations/search" in url:
            return _Resp({"conversations": [{"id": "conv1"}]})
        if "/messages" in url:
            by_type = {
                "TYPE_SMS": [{"messageType": "TYPE_SMS", "direction": "inbound",
                              "body": "Can you give me some times",
                              "dateAdded": "2026-09-10T02:20:28.000Z"}],
                "TYPE_LIVE_CHAT": [],
            }
            return _Resp({"messages": {"messages": by_type.get((params or {}).get("type"), [])}})
        return _Resp({}, 404)


@pytest.fixture
def client(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(ghl.httpx, "AsyncClient", lambda *a, **kw: _Client())
    return _Client


@pytest.mark.asyncio
async def test_every_messages_query_is_type_filtered_to_sms_or_live_chat(client):
    await ghl.get_recent_texts("c1")
    msg_calls = [(u, p) for (u, p) in client.calls if "/messages" in u]
    assert msg_calls, "no messages request was made"
    types = [p.get("type") for (_, p) in msg_calls]
    assert None not in types, (
        "every messages request must carry a type filter. Without it, activity "
        "records consume the whole window and real texts never arrive."
    )
    assert sorted(types) == ["TYPE_LIVE_CHAT", "TYPE_SMS"]


@pytest.mark.asyncio
async def test_the_limit_is_still_applied(client):
    await ghl.get_recent_texts("c1", limit=4)
    for _, params in [(u, p) for (u, p) in client.calls if "/messages" in u]:
        assert params.get("limit") == 4


@pytest.mark.asyncio
async def test_it_returns_the_messages(client):
    out = await ghl.get_recent_texts("c1")
    assert len(out) == 1
    assert out[0]["body"] == "Can you give me some times"
    assert out[0]["direction"] == "inbound"


@pytest.mark.asyncio
async def test_a_blank_contact_id_makes_no_request(client):
    assert await ghl.get_recent_texts("") == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_unknown_crm_makes_no_request(client):
    assert await ghl.get_recent_texts("c1", crm_key="nope") == []
    assert client.calls == []


class _NoConvoClient(_Client):
    async def get(self, url, params=None, headers=None, timeout=None):
        type(self).calls.append((url, params))
        if "/conversations/search" in url:
            return _Resp({"conversations": []})
        return _Resp({}, 404)


@pytest.mark.asyncio
async def test_no_conversation_degrades_to_empty(monkeypatch):
    _NoConvoClient.calls = []
    monkeypatch.setattr(ghl.httpx, "AsyncClient", lambda *a, **kw: _NoConvoClient())
    # "No thread known" — the caller dials as it would have before the digest
    # existed. Never blocks the call.
    assert await ghl.get_recent_texts("c1") == []


class _BoomClient(_Client):
    async def get(self, url, params=None, headers=None, timeout=None):
        raise RuntimeError("GHL down")


@pytest.mark.asyncio
async def test_a_failure_degrades_rather_than_blocking_the_call(monkeypatch):
    monkeypatch.setattr(ghl.httpx, "AsyncClient", lambda *a, **kw: _BoomClient())
    assert await ghl.get_recent_texts("c1") == []


# -- live chat counts as a reply (2026-09-13) ---------------------------------
# The team answers prospects from the GHL app, which stores the message as
# TYPE_LIVE_CHAT. Reading SMS alone hid those replies, so the call stayed paused
# as "awaiting our reply" after we had in fact answered.

class _ThreadClient(_Client):
    sms: list = []
    chat: list = []
    fail_type = None

    async def get(self, url, params=None, headers=None, timeout=None):
        type(self).calls.append((url, params))
        if "/conversations/search" in url:
            return _Resp({"conversations": [{"id": "conv1"}]})
        msg_type = (params or {}).get("type")
        if msg_type == type(self).fail_type:
            return _Resp({}, 500)
        rows = type(self).sms if msg_type == "TYPE_SMS" else type(self).chat
        return _Resp({"messages": {"messages": rows}})


def _thread_client(monkeypatch, *, sms, chat, fail_type=None):
    _ThreadClient.calls, _ThreadClient.sms, _ThreadClient.chat = [], sms, chat
    _ThreadClient.fail_type = fail_type
    monkeypatch.setattr(ghl.httpx, "AsyncClient", lambda *a, **kw: _ThreadClient())


THEIR_SMS = {"messageType": "TYPE_SMS", "direction": "inbound", "body": "Times",
             "dateAdded": "2026-09-10T03:10:43.000Z"}
OUR_CHAT = {"messageType": "TYPE_LIVE_CHAT", "direction": "outbound",
            "body": "Hey Ryan! Just tried giving you a quick call",
            "dateAdded": "2026-09-11T05:31:00.000Z"}


@pytest.mark.asyncio
async def test_our_live_chat_reply_after_their_sms_ends_the_thread(monkeypatch):
    from src.webhooks.text_digest import should_hold_for_reply

    _thread_client(monkeypatch, sms=[THEIR_SMS], chat=[OUR_CHAT])
    out = await ghl.get_recent_texts("c1")
    assert [m["body"] for m in out] == ["Times", OUR_CHAT["body"]]
    assert should_hold_for_reply(out) is False, (
        "we answered by live chat, so nobody is owed a reply and the call may go"
    )


@pytest.mark.asyncio
async def test_their_sms_after_our_live_chat_still_holds(monkeypatch):
    from src.webhooks.text_digest import should_hold_for_reply

    # Dated relative to now: the reply-hold expires after 3 days, so a fixed date
    # made this test rot (it started failing on 2026-09-15).
    from datetime import datetime, timedelta, timezone
    an_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    later_sms = {**THEIR_SMS, "dateAdded": an_hour_ago}
    _thread_client(monkeypatch, sms=[later_sms], chat=[OUR_CHAT])
    assert should_hold_for_reply(await ghl.get_recent_texts("c1")) is True


@pytest.mark.asyncio
async def test_the_merged_thread_is_trimmed_to_the_newest_limit(monkeypatch):
    sms = [{**THEIR_SMS, "dateAdded": f"2026-09-0{d}T00:00:00.000Z", "body": f"s{d}"} for d in (1, 3, 5)]
    chat = [{**OUR_CHAT, "dateAdded": f"2026-09-0{d}T00:00:00.000Z", "body": f"c{d}"} for d in (2, 4, 6)]
    _thread_client(monkeypatch, sms=sms, chat=chat)
    out = await ghl.get_recent_texts("c1", limit=3)
    assert [m["body"] for m in out] == ["c4", "s5", "c6"]


@pytest.mark.asyncio
async def test_one_type_failing_still_returns_the_other(monkeypatch):
    _thread_client(monkeypatch, sms=[THEIR_SMS], chat=[OUR_CHAT], fail_type="TYPE_LIVE_CHAT")
    out = await ghl.get_recent_texts("c1")
    assert [m["body"] for m in out] == ["Times"]
