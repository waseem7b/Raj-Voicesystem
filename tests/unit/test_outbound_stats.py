"""The daily digest must report what Ashley's outbound calls actually did.

Before this, archive_call classified every call with the INBOUND classifier, so
outbound calls archived as OTHER; and format_digest only rendered labels listed
in OUTCOME_ORDER, so an outbound label was counted and then silently dropped.
Net effect: the day's stats could not tell a confirmation from a no-answer.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from src.webhooks import call_archive
from src.webhooks.stats import compute_day_stats, format_digest


def _payload(duration_sec=120):
    m, s = divmod(duration_sec, 60)
    return {
        "call": {"id": "call-out-1", "customer": {"number": "+15555550100"}},
        "startedAt": "2026-05-12T14:30:00Z",
        "endedAt": f"2026-05-12T{14 + (m + 30) // 60:02d}:{(m + 30) % 60:02d}:{s:02d}Z",
        "endedReason": "customer-ended-call",
        "recordingUrl": None,
        "transcript": "User: yes\nAI: great",
        "analysis": {"summary": "Test", "successEvaluation": True},
        "cost": 0.42,
        "messages": [],
    }


# --- archive persists the outbound truth ---------------------------------

@pytest.mark.asyncio
async def test_archive_records_outbound_outcome_and_call_type(tmp_path):
    folder = await call_archive.archive_call(
        _payload(), base_dir=tmp_path,
        outcome_override=("✅", "CONFIRMED"), call_type="confirmation")
    s = json.loads((folder / "summary.json").read_text())
    assert s["outcome_label"] == "CONFIRMED"
    assert s["call_type"] == "confirmation"


@pytest.mark.asyncio
async def test_archive_without_override_keeps_inbound_classification(tmp_path):
    folder = await call_archive.archive_call(_payload(), base_dir=tmp_path)
    s = json.loads((folder / "summary.json").read_text())
    assert s["outcome_label"] != "CONFIRMED"
    assert s["call_type"] == ""


# --- aggregation splits inbound from outbound ----------------------------

def _write(base: Path, name: str, label: str, call_type: str):
    d = base / name
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({
        "call_id": name, "phone": "+15555550100",
        "started_at": "2026-05-12T14:30:00Z", "duration_sec": 100,
        "outcome_label": label, "call_type": call_type, "cost_usd": 0.10,
    }))


def _stats(tmp_path) -> dict:
    _write(tmp_path, "a", "CONFIRMED", "confirmation")
    _write(tmp_path, "b", "CONFIRMED", "reminder")
    _write(tmp_path, "c", "RESCHEDULED", "confirmation")
    _write(tmp_path, "d", "NO ANSWER", "confirmation")
    _write(tmp_path, "e", "BOOKED", "")          # Jon, inbound
    return compute_day_stats(date(2026, 5, 12), tmp_path)


def test_outbound_and_inbound_are_counted_separately(tmp_path):
    st = _stats(tmp_path)
    assert st["outbound_calls"] == 4
    assert st["inbound_calls"] == 1
    assert st["outbound_outcomes"]["CONFIRMED"] == 2
    assert st["outbound_outcomes"]["RESCHEDULED"] == 1
    assert st["inbound_outcomes"] == {"BOOKED": 1}


def test_confirmed_rate_is_over_confirmation_calls_only(tmp_path):
    st = _stats(tmp_path)
    assert st["confirmation_calls"] == 4
    assert st["confirmed_count"] == 2
    assert st["confirmed_rate_pct"] == 50.0


def test_digest_reports_the_outbound_block(tmp_path):
    out = format_digest(_stats(tmp_path))
    assert "Ashley: 4 calls" in out
    assert "Confirmed: 2 (50%)" in out
    assert "CONFIRMED: 2" in out
    assert "RESCHEDULED: 1" in out
    assert "NO ANSWER: 1" in out
    assert "BOOKED: 1" in out


def test_unrecognised_label_is_not_silently_dropped():
    out = format_digest({
        "date": "2026-05-12", "total_calls": 1,
        "outcomes": {"WAT": 1}, "inbound_outcomes": {"WAT": 1},
        "outbound_outcomes": {},
    })
    assert "WAT: 1" in out


def test_old_stats_blob_without_the_split_still_renders():
    """A blob archived before call_type existed must still produce a digest."""
    out = format_digest({
        "date": "2026-05-12", "total_calls": 2, "booked_count": 1,
        "booked_rate_pct": 50.0, "outcomes": {"BOOKED": 1, "HUNG UP": 1},
    })
    assert "BOOKED: 1" in out
    assert "HUNG UP: 1" in out
