"""Nobody who asked us to stop should be dialled again.

Ashley fires `mark_not_interested` the moment a prospect says they are done, and
until 2026-09-10 nothing ever read the result back. On call 01a08929 the prospect
said "can you please not call me back", she tagged him correctly, and the system
dialled him five more times over the next thirty minutes. The tag was write-only
in both repos.

These cover the read side: the folding rules for a hand-typed GHL tag, and the
gate on the dial path.
"""
import pytest

from src.agent import ghl


# ── tag folding ──────────────────────────────────────────────────────────────
# GHL tags are typed by hand, so the guard folds case, spaces and dashes rather
# than matching one exact string.

@pytest.mark.parametrize(
    "tag",
    [
        "not-interested",
        "Not Interested",
        "NOT_INTERESTED",
        "notinterested",
        "  not-interested  ",
        "do not contact",
        "Do-Not-Contact",
        "DNC",
        "dnc",
    ],
)
def test_opt_out_tags_are_recognised_however_they_were_typed(tag):
    assert ghl._fold_tag(tag) in ghl.OPT_OUT_TAGS


@pytest.mark.parametrize(
    "tag",
    [
        "interested",
        "very-interested",
        "not-interested-in-seo",
        "noshow",
        "demobooked",
        "",
        None,
    ],
)
def test_ordinary_tags_do_not_opt_anyone_out(tag):
    assert ghl._fold_tag(tag) not in ghl.OPT_OUT_TAGS


# ── the GHL read ─────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **kw):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(response):
        monkeypatch.setattr(
            ghl.httpx, "AsyncClient", lambda *a, **kw: _FakeClient(response)
        )
    return _patch


@pytest.mark.asyncio
async def test_tagged_contact_is_reported_as_opted_out(patch_client):
    patch_client(_FakeResponse(200, {"contact": {"tags": ["noshow", "not-interested"]}}))
    assert await ghl.has_opted_out("c1") is True


@pytest.mark.asyncio
async def test_untagged_contact_is_not_opted_out(patch_client):
    patch_client(_FakeResponse(200, {"contact": {"tags": ["noshow", "demobooked"]}}))
    assert await ghl.has_opted_out("c1") is False


@pytest.mark.asyncio
async def test_contact_with_no_tags_at_all(patch_client):
    patch_client(_FakeResponse(200, {"contact": {}}))
    assert await ghl.has_opted_out("c1") is False


@pytest.mark.asyncio
async def test_a_ghl_error_FAILS_OPEN(patch_client):
    # Deliberate. A GHL blip must not silently stop every outbound call across
    # the business — that is an outage nobody would notice for hours. The cost
    # of failing open is one call to someone who opted out, which the CRM drip's
    # own guard also catches.
    patch_client(_FakeResponse(500))
    assert await ghl.has_opted_out("c1") is False


@pytest.mark.asyncio
async def test_a_404_fails_open_too(patch_client):
    patch_client(_FakeResponse(404, {}))
    assert await ghl.has_opted_out("c1") is False
