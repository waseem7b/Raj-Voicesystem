"""Call ledger — the file the CRM's Confirmations report reads.

Every call decision becomes one JSON line (placed / skipped / failed / ended).
These tests pin the line contract (mirrored in the CRM's
src/lib/ashley-calls/ledger.ts) and that a ledger problem can never cost a call,
a tag, the Telegram record or the GHL comment.

Import server BEFORE outbound so server mounts the router.
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.webhooks import server  # noqa: F401 — import order matters, see docstring
from src.webhooks import call_ledger, ghl_trigger, outbound


def _lines(directory):
    return [
        json.loads(raw)
        for f in sorted(directory.glob("*.jsonl"))
        for raw in f.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]


@pytest.fixture
def ledger_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(call_ledger, "LEDGER_DIR", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #
def test_record_writes_one_v1_line_into_the_utc_day_file(tmp_path):
    at = datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)
    ok = call_ledger.record(
        "placed", call_type=" Reminder ", at=at, base_dir=tmp_path,
        vapi_call_id="v1", ghl_contact_id="c1", ghl_appointment_id="", not_a_field="x",
    )
    assert ok is True
    assert (tmp_path / "2026-09-14.jsonl").exists()
    [line] = _lines(tmp_path)
    assert line["v"] == 1
    assert line["event"] == "placed"
    assert line["at"] == "2026-09-14T23:30:00Z"
    assert line["call_type"] == "reminder"
    assert line["vapi_call_id"] == "v1"
    assert line["ghl_appointment_id"] is None  # blank strings are written as null
    assert line["reason"] is None
    assert line["source"] == "live"
    assert "not_a_field" not in line


def test_record_appends_instead_of_overwriting(tmp_path):
    at = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    call_ledger.record("placed", call_type="confirmation", at=at, base_dir=tmp_path)
    call_ledger.record("ended", call_type="confirmation", at=at, base_dir=tmp_path)
    assert [ln["event"] for ln in _lines(tmp_path)] == ["placed", "ended"]


def test_record_never_raises_when_it_cannot_write(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    assert call_ledger.record("placed", call_type="reminder", base_dir=blocker / "sub") is False


@pytest.mark.parametrize("header,key", [
    (("✅", "CONFIRMED"), "confirmed"),
    (("🚫", "NOT INTERESTED"), "not_interested"),
    (("📅", "NEEDS RESCHEDULE"), "needs_reschedule"),
    (("☎️", "ANSWERED"), "answered"),
    (("📼", "VOICEMAIL"), "voicemail"),
    (("❌", "NO ANSWER"), "no_answer"),
    (("?", "SOMETHING NEW"), "unknown"),
    (None, "unknown"),
])
def test_outcome_key_follows_the_outbound_header(header, key):
    assert call_ledger.outcome_key(header) == key


def test_seconds_between():
    assert call_ledger.seconds_between("2026-09-14T18:01:33.854Z", "2026-09-14T18:01:46.411Z") == 12
    assert call_ledger.seconds_between(None, "2026-09-14T18:01:46Z") is None


# --------------------------------------------------------------------------- #
# end-of-call report → `ended` line
# --------------------------------------------------------------------------- #
def _report(call_type="reminder"):
    return {"message": {
        "type": "end-of-call-report",
        "call": {"id": "call-9", "metadata": {"ghl_contact_id": "contact-1",
                                              "ghl_appointment_id": "appt-1",
                                              "ghl_business": "Zamora Painting LLC",
                                              "call_type": call_type},
                 "customer": {"number": "+15550001111"}},
        "startedAt": "2026-09-14T18:01:33Z",
        "endedAt": "2026-09-14T18:01:45Z",
        "endedReason": "customer-did-not-answer",
        "cost": 0.0122,
    }}


@pytest.fixture
def quiet_downstream(monkeypatch):
    """Telegram + GHL comment stubbed; hand back both spies."""
    pushed = AsyncMock(return_value=None)
    commented = AsyncMock(return_value=None)
    monkeypatch.setattr(outbound, "_push_outbound_telegram", pushed)
    monkeypatch.setattr(outbound, "_post_call_comment", commented)
    return pushed, commented


def test_a_short_voicemail_still_gets_an_ended_line(ledger_dir, quiet_downstream, monkeypatch):
    monkeypatch.setattr(outbound, "_tag_call_outcome",
                        AsyncMock(return_value=({"call_type": "reminder"}, "call-voicemail")))

    r = TestClient(server.app).post("/vapi/outbound/events", json=_report())

    assert r.status_code == 200
    [line] = _lines(ledger_dir)
    assert line["event"] == "ended"
    assert line["call_type"] == "reminder"
    assert line["vapi_call_id"] == "call-9"
    assert line["ghl_contact_id"] == "contact-1"
    assert line["ghl_appointment_id"] == "appt-1"
    assert line["outcome"] == "voicemail"
    assert line["outcome_tag"] == "call-voicemail"
    assert line["ended_reason"] == "customer-did-not-answer"
    assert line["duration_sec"] == 12
    assert line["cost"] == 0.0122
    assert line["business"] == "Zamora Painting LLC"


def test_an_in_call_confirmation_is_recorded_as_confirmed(ledger_dir, quiet_downstream, monkeypatch):
    monkeypatch.setattr(outbound, "_tag_call_outcome", AsyncMock(
        return_value=({"call_type": "confirmation", "confirm_outcome": "confirmed"}, None)))

    TestClient(server.app).post("/vapi/outbound/events", json=_report("confirmation"))

    [line] = _lines(ledger_dir)
    assert line["outcome"] == "confirmed"
    assert line["outcome_tag"] is None


def test_a_crashing_ledger_write_leaves_telegram_and_comment_intact(quiet_downstream, monkeypatch):
    pushed, commented = quiet_downstream
    monkeypatch.setattr(outbound, "_tag_call_outcome", AsyncMock(return_value=({}, "call-answered")))
    monkeypatch.setattr(outbound, "_record_call_ended", lambda *a: (_ for _ in ()).throw(RuntimeError("disk")))

    r = TestClient(server.app).post("/vapi/outbound/events", json=_report())

    assert r.status_code == 200
    assert pushed.await_count == 1
    assert commented.await_count == 1
