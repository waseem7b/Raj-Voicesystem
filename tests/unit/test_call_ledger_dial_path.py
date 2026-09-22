"""Call ledger on the dial path — POST /ghl/outbound-call.

Every decision _place_call makes about a confirmation/reminder call leaves one
ledger line: placed, skipped (with the guard's reason) or failed. A GHL
double-fire (dedupe) is NOT a decision and writes nothing.

Import server BEFORE ghl_trigger so server mounts the router.
"""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.webhooks import server
from src.webhooks import call_ledger, ghl_trigger

SECRET = "test-ghl-secret"

BASE = {
    "call_type": "confirmation",
    "phone": "(555) 123-4567",
    "contact_id": "ghl-c1",
    "appointment_id": "appt-1",
    "appointment_status": "confirmed",
    "first_name": "Dana",
}

LOOKUP = {
    "first_name": "Dana", "contact_id": "c1", "appointment_id": "a1",
    "business": "Dana's Dirt Works", "booked_time": "", "greeting": "",
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("GHL_OUTBOUND_SECRET", SECRET)
    monkeypatch.setenv("VAPI_ASSISTANT_ID_CONFIRMATION", "asst-confirm")
    monkeypatch.setenv("VAPI_ASSISTANT_ID_REMINDER", "asst-remind")
    monkeypatch.setattr(call_ledger, "LEDGER_DIR", tmp_path)
    ghl_trigger._recent.clear()
    monkeypatch.setattr(ghl_trigger, "_resolve_booking", AsyncMock(return_value=dict(LOOKUP)))
    monkeypatch.setattr(ghl_trigger.ghl, "get_recent_texts", AsyncMock(return_value=[]))
    monkeypatch.setattr(ghl_trigger.ghl, "has_opted_out", AsyncMock(return_value=False))
    monkeypatch.setattr(ghl_trigger.ghl, "has_call_dnd", AsyncMock(return_value=False))
    monkeypatch.setattr(ghl_trigger.ghl, "get_appointment_status", AsyncMock(return_value="confirmed"))
    monkeypatch.setattr(ghl_trigger.ghl, "remove_tag", AsyncMock(return_value=True))
    yield
    ghl_trigger._recent.clear()


@pytest.fixture
def create_call(monkeypatch):
    mock = AsyncMock(return_value={"id": "call-abc"})
    monkeypatch.setattr(ghl_trigger, "create_outbound_call", mock)
    return mock


def _post(body):
    return TestClient(server.app).post("/ghl/outbound-call", json=body, headers={"X-GHL-Secret": SECRET})


def _lines():
    return [
        json.loads(raw)
        for f in sorted(call_ledger.LEDGER_DIR.glob("*.jsonl"))
        for raw in f.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]


def test_a_placed_call_writes_a_placed_line(create_call):
    assert _post(BASE).status_code == 200
    [line] = _lines()
    assert line["event"] == "placed"
    assert line["call_type"] == "confirmation"
    assert line["vapi_call_id"] == "call-abc"
    assert line["ghl_contact_id"] == "c1"  # the resolved booking wins over the hint
    assert line["ghl_appointment_id"] == "a1"
    assert line["business"] == "Dana's Dirt Works"


def test_texted_last_writes_a_skip_and_no_call(create_call, monkeypatch):
    monkeypatch.setattr(ghl_trigger, "should_hold_for_reply", lambda texts: True)
    _post({**BASE, "call_type": "reminder"})
    create_call.assert_not_awaited()
    [line] = _lines()
    assert line["event"] == "skipped"
    assert line["call_type"] == "reminder"
    assert line["reason"] == "awaiting_our_reply"
    assert line["vapi_call_id"] is None


@pytest.mark.parametrize("guard,reason", [("has_opted_out", "opted_out"), ("has_call_dnd", "dnd")])
def test_opt_out_and_dnd_write_their_reason(create_call, monkeypatch, guard, reason):
    monkeypatch.setattr(ghl_trigger.ghl, guard, AsyncMock(return_value=True))
    _post(BASE)
    create_call.assert_not_awaited()
    [line] = _lines()
    assert (line["event"], line["reason"]) == ("skipped", reason)


def test_a_dead_appointment_writes_its_status(create_call):
    _post({**BASE, "appointment_status": "cancelled"})
    [line] = _lines()
    assert line["event"] == "skipped"
    assert line["reason"] == "status_cancelled"
    assert line["ghl_appointment_id"] == "appt-1"
    assert line["ghl_contact_id"] == "ghl-c1"


def test_a_refused_call_writes_failed(monkeypatch):
    monkeypatch.setattr(ghl_trigger, "create_outbound_call",
                        AsyncMock(side_effect=RuntimeError("assistant not configured")))
    _post(BASE)
    [line] = _lines()
    assert (line["event"], line["reason"]) == ("failed", "RuntimeError")


def test_a_ghl_double_fire_writes_one_line_not_two(create_call):
    _post(BASE)
    _post(BASE)
    assert [ln["event"] for ln in _lines()] == ["placed"]


def test_an_unwritable_ledger_never_stops_the_call(create_call, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setattr(call_ledger, "LEDGER_DIR", blocker / "sub")
    res = _post(BASE)
    assert res.status_code == 200
    assert res.json().get("callId") == "call-abc"
