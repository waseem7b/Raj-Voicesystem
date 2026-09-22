"""Ashley's Telegram summary: call type on the header line, no CRM line (owner, 2026-09-14).

Import server BEFORE outbound, same as test_ghl_internal_comment (see its docstring).
"""
from src.webhooks import server  # noqa: F401 — import order matters
from src.webhooks import outbound

SUMMARY = "\n".join([
    "📞 ANSWERED · 44s",
    "",
    "Caller: +15555550100 / Rock Epoxy / San Jose",
    "CRM: Main",
    "Cost: $0.12",
    "ID: 2026-09-14_rock-epoxy",
    "",
    "Transcript:",
])


def _entry(call_type="reminder", outcome="confirmed"):
    return {"call_type": call_type, "outcome": outcome}


def test_header_carries_call_type_after_duration(monkeypatch):
    monkeypatch.setattr(outbound, "_outbound_header", lambda e, t: ("✅", "CONFIRMED"))
    text = outbound._outbound_telegram_text(SUMMARY, _entry(), None)
    assert text.splitlines()[0] == "✅ CONFIRMED · 44s - Reminder call (day of)"


def test_no_ashley_line_and_no_crm_line(monkeypatch):
    monkeypatch.setattr(outbound, "_outbound_header", lambda e, t: ("✅", "CONFIRMED"))
    text = outbound._outbound_telegram_text(SUMMARY, _entry(), None)
    assert "Ashley:" not in text
    assert "CRM:" not in text
    assert "Caller: +15555550100 / Rock Epoxy / San Jose" in text
    assert "Transcript:" in text


def test_no_call_type_leaves_header_alone(monkeypatch):
    monkeypatch.setattr(outbound, "_outbound_header", lambda e, t: None)
    text = outbound._outbound_telegram_text(SUMMARY, {"call_type": ""}, None)
    assert text.splitlines()[0] == "📞 ANSWERED · 44s"
    assert "CRM:" not in text
