"""Ashley's call result posted into the prospect's GHL conversation.

Two pieces:
  - ghl.post_internal_comment / ghl.get_assigned_user — the client calls
  - outbound._build_comment_text / _post_call_comment — what gets written

Contract notes, verified against live GHL 2026-09-09 by POSTing with a contactId
that cannot exist (so nothing was written): the main PIT already carries
conversations scope, `InternalComment` is an accepted type, and the
documented-as-required `mentions` and `status` are optional.

All HTTP is mocked via httpx.MockTransport, same pattern as test_confirmation_ghl.

Import server BEFORE outbound, same as test_outbound_confirmation: server imports
outbound at its bottom, and importing outbound first leaves it half-initialised so
server silently declines to mount its router — which shows up as a 404 on the route
test rather than as an import error.
"""
import json

import httpx
import pytest

from src.webhooks import server  # noqa: F401 — import order matters, see docstring
from src.agent import ghl
from src.webhooks import outbound


def _patch_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)


# --------------------------------------------------------------------------- #
# post_internal_comment
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_posts_internal_comment_with_the_right_shape(monkeypatch):
    captured = {}

    def handler(request):
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["version"] = request.headers.get("Version")
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "m1", "conversationId": "c1"})

    _patch_client(monkeypatch, handler)
    ok = await ghl.post_internal_comment("contact-1", "main", "call went well")

    assert ok is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/conversations/messages"
    assert captured["version"] == "2021-07-28"
    assert captured["auth"].startswith("Bearer ")
    assert captured["body"] == {
        "type": "InternalComment",
        "contactId": "contact-1",
        "message": "call went well",
    }


@pytest.mark.asyncio
async def test_mentions_and_user_id_are_sent_only_when_present(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    _patch_client(monkeypatch, handler)
    await ghl.post_internal_comment(
        "contact-1", "main", "heads up", mentions=["user-9"], user_id="user-9"
    )
    assert captured["body"]["mentions"] == ["user-9"]
    assert captured["body"]["userId"] == "user-9"


@pytest.mark.asyncio
async def test_non_2xx_returns_false_and_does_not_raise(monkeypatch):
    def handler(request):
        return httpx.Response(400, text="Contact not found")

    _patch_client(monkeypatch, handler)
    assert await ghl.post_internal_comment("nope", "main", "x") is False


@pytest.mark.asyncio
async def test_empty_inputs_never_reach_the_network(monkeypatch):
    def handler(request):  # pragma: no cover — must not be called
        raise AssertionError("should not have made a request")

    _patch_client(monkeypatch, handler)
    assert await ghl.post_internal_comment("", "main", "x") is False
    assert await ghl.post_internal_comment("c1", "main", "") is False
    assert await ghl.post_internal_comment("c1", "nosuchcrm", "x") is False


# --------------------------------------------------------------------------- #
# get_assigned_user
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_assigned_user_resolves_id_and_name(monkeypatch):
    def handler(request):
        if request.url.path == "/contacts/contact-1":
            return httpx.Response(200, json={"contact": {"assignedTo": "user-9"}})
        if request.url.path == "/users/user-9":
            return httpx.Response(200, json={"name": "Jon Uhl"})
        raise AssertionError(request.url.path)

    _patch_client(monkeypatch, handler)
    assert await ghl.get_assigned_user("contact-1") == {"id": "user-9", "name": "Jon Uhl"}


@pytest.mark.asyncio
async def test_assigned_user_falls_back_to_first_and_last_name(monkeypatch):
    def handler(request):
        if request.url.path == "/contacts/contact-1":
            return httpx.Response(200, json={"contact": {"assignedTo": "user-9"}})
        return httpx.Response(200, json={"user": {"firstName": "Jon", "lastName": "Uhl"}})

    _patch_client(monkeypatch, handler)
    assert await ghl.get_assigned_user("contact-1") == {"id": "user-9", "name": "Jon Uhl"}


@pytest.mark.asyncio
@pytest.mark.parametrize("contact_json", [
    {"contact": {}},                    # nobody assigned
    {"contact": {"assignedTo": ""}},    # assigned to empty
])
async def test_unassigned_contact_gives_no_mention(monkeypatch, contact_json):
    def handler(request):
        return httpx.Response(200, json=contact_json)

    _patch_client(monkeypatch, handler)
    assert await ghl.get_assigned_user("contact-1") is None


@pytest.mark.asyncio
async def test_assigned_user_degrades_on_failure(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    _patch_client(monkeypatch, handler)
    assert await ghl.get_assigned_user("contact-1") is None


# --------------------------------------------------------------------------- #
# The comment text
# --------------------------------------------------------------------------- #
def _msg(call_id="call-1", started="2026-09-09T10:00:00Z", ended="2026-09-09T10:01:12Z"):
    return {"call": {"id": call_id}, "startedAt": started, "endedAt": ended}


@pytest.fixture
def no_recording(monkeypatch):
    monkeypatch.setattr(outbound, "public_recording_url", lambda cid: None)


def test_confirmed_call_reads_as_confirmed(no_recording):
    text = outbound._build_comment_text(
        _msg(), {"confirm_outcome": "confirmed", "call_type": "confirmation"}, None, None
    )
    assert text == "✅ CONFIRMED — Ashley, Confirmation call (day before) · 1m 12s"


def test_in_call_outcome_beats_the_end_of_call_tag(no_recording):
    """A confirmed call comes back with tag=None, so reading the tag alone would
    mislabel the entire happy path as a bare ANSWERED."""
    text = outbound._build_comment_text(
        _msg(), {"confirm_outcome": "confirmed", "call_type": "confirmation"},
        "call-answered", None,
    )
    assert text.startswith("✅ CONFIRMED")


def test_voicemail_uses_the_tag(no_recording):
    text = outbound._build_comment_text(
        _msg(), {"call_type": "reminder"}, "call-voicemail", None
    )
    assert text.startswith("\U0001F4FC VOICEMAIL — Ashley, Reminder call (day of)")


def test_rebooking_has_a_real_label(no_recording):
    """It used to fall through to the ungrammatical lowercase fallback."""
    text = outbound._build_comment_text(
        _msg(), {"call_type": "rebooking"}, "call-no-answer", None
    )
    assert "Rebooking call-back" in text
    assert "rebooking call" not in text


def test_unknown_outcome_still_produces_a_comment(no_recording):
    text = outbound._build_comment_text(_msg(), {"call_type": "confirmation"}, None, None)
    assert text.startswith("⚠️ CALL ENDED")


def test_mention_markup_and_recording_are_separate_lines(monkeypatch):
    monkeypatch.setattr(
        outbound, "public_recording_url",
        lambda cid: "https://webhooks.c7a.io/voicsito/static/rec/" + cid + ".mp3",
    )
    text = outbound._build_comment_text(
        _msg(), {"confirm_outcome": "confirmed", "call_type": "confirmation"}, None,
        {"id": "user-9", "name": "Jon Uhl"},
    )
    assert text.splitlines() == [
        "✅ CONFIRMED — Ashley, Confirmation call (day before) · 1m 12s",
        "@Jon Uhl<userId>user-9</userId>",
        "Recording: https://webhooks.c7a.io/voicsito/static/rec/call-1.mp3",
    ]


def test_no_recording_means_no_recording_line(no_recording):
    text = outbound._build_comment_text(_msg(), {"call_type": "confirmation"}, None, None)
    assert "Recording:" not in text


def test_missing_timestamps_drop_the_duration(no_recording):
    text = outbound._build_comment_text(
        {"call": {"id": "call-1"}}, {"call_type": "confirmation"}, "call-answered", None
    )
    assert text == "☎️ ANSWERED — Ashley, Confirmation call (day before)"


# --------------------------------------------------------------------------- #
# _post_call_comment — orchestration
# --------------------------------------------------------------------------- #
def _report(call_id="call-1", contact_id="contact-1"):
    return {"message": {
        "type": "end-of-call-report",
        "call": {"id": call_id, "metadata": {"ghl_contact_id": contact_id,
                                             "call_type": "confirmation"},
                 "customer": {"number": "+15550001111"}},
        "startedAt": "2026-09-09T10:00:00Z",
        "endedAt": "2026-09-09T10:01:12Z",
        "endedReason": "assistant-said-end-call-phrase",
    }}


@pytest.fixture
def comment_spies(monkeypatch):
    """Patch the two GHL calls and the sentinel; hand back the post spy."""
    from unittest.mock import AsyncMock

    posted = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "post_internal_comment", posted)
    monkeypatch.setattr(outbound.ghl, "get_assigned_user",
                        AsyncMock(return_value={"id": "user-9", "name": "Jon Uhl"}))
    monkeypatch.setattr(outbound, "public_recording_url", lambda cid: None)
    flags: set = set()
    monkeypatch.setattr(outbound, "already_commented", lambda cid: cid in flags)
    monkeypatch.setattr(outbound, "mark_commented", lambda cid: flags.add(cid))
    return posted


@pytest.mark.asyncio
async def test_posts_once_with_the_mention(comment_spies):
    await outbound._post_call_comment(
        _report(), {"confirm_outcome": "confirmed", "call_type": "confirmation"}, None
    )
    assert comment_spies.await_count == 1
    args, kwargs = comment_spies.call_args
    assert args[0] == "contact-1"
    assert args[1] == "main"
    assert "CONFIRMED" in args[2]
    assert kwargs["mentions"] == ["user-9"]


@pytest.mark.asyncio
async def test_a_resent_report_does_not_double_post(comment_spies):
    entry = {"confirm_outcome": "confirmed", "call_type": "confirmation"}
    await outbound._post_call_comment(_report(), entry, None)
    await outbound._post_call_comment(_report(), entry, None)
    assert comment_spies.await_count == 1


@pytest.mark.asyncio
async def test_a_failed_post_is_retried_on_the_next_report(monkeypatch):
    """The sentinel is only committed on success, so a GHL blip does not
    silently cost the contact their comment forever."""
    from unittest.mock import AsyncMock

    posted = AsyncMock(return_value=False)
    monkeypatch.setattr(outbound.ghl, "post_internal_comment", posted)
    monkeypatch.setattr(outbound.ghl, "get_assigned_user", AsyncMock(return_value=None))
    monkeypatch.setattr(outbound, "public_recording_url", lambda cid: None)
    flags: set = set()
    monkeypatch.setattr(outbound, "already_commented", lambda cid: cid in flags)
    monkeypatch.setattr(outbound, "mark_commented", lambda cid: flags.add(cid))

    await outbound._post_call_comment(_report(), {"call_type": "confirmation"}, None)
    await outbound._post_call_comment(_report(), {"call_type": "confirmation"}, None)
    assert posted.await_count == 2


@pytest.mark.asyncio
async def test_no_contact_means_no_post(comment_spies):
    body = _report(contact_id="")
    body["message"]["call"]["metadata"]["ghl_contact_id"] = ""
    await outbound._post_call_comment(body, {"call_type": "confirmation"}, None)
    assert comment_spies.await_count == 0


@pytest.mark.asyncio
async def test_an_unassigned_contact_still_gets_a_comment(monkeypatch):
    from unittest.mock import AsyncMock

    posted = AsyncMock(return_value=True)
    monkeypatch.setattr(outbound.ghl, "post_internal_comment", posted)
    monkeypatch.setattr(outbound.ghl, "get_assigned_user", AsyncMock(return_value=None))
    monkeypatch.setattr(outbound, "public_recording_url", lambda cid: None)
    monkeypatch.setattr(outbound, "already_commented", lambda cid: False)
    monkeypatch.setattr(outbound, "mark_commented", lambda cid: None)

    await outbound._post_call_comment(_report(), {"call_type": "confirmation"}, None)
    assert posted.await_count == 1
    assert posted.call_args.kwargs["mentions"] is None
    assert "<userId>" not in posted.call_args.args[2]


# --------------------------------------------------------------------------- #
# The comment must never cost the tag or the operator's call record
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_crashing_comment_leaves_tag_and_telegram_intact(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    tagged = AsyncMock(return_value=({}, "call-answered"))
    pushed = AsyncMock(return_value=None)
    monkeypatch.setattr(outbound, "_tag_call_outcome", tagged)
    monkeypatch.setattr(outbound, "_push_outbound_telegram", pushed)
    monkeypatch.setattr(outbound, "_post_call_comment",
                        AsyncMock(side_effect=RuntimeError("GHL down")))

    r = TestClient(server.app).post("/vapi/outbound/events", json=_report())

    assert r.status_code == 200
    assert tagged.await_count == 1
    assert pushed.await_count == 1
