"""Unit tests for stats aggregation."""
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.webhooks import stats


# ─────────── fixtures ───────────

def _write_summary(base: Path, *, folder: str, call_id: str, started_at: str,
                   duration_sec: int, outcome: str, cost: float = 0.10,
                   nested_month: str | None = "05-2026") -> Path:
    """Write a fake summary.json under base[/nested_month]/<folder>/summary.json."""
    parent = base / nested_month / folder if nested_month else base / folder
    parent.mkdir(parents=True, exist_ok=True)
    data = {
        "call_id": call_id,
        "phone": "+15555550100",
        "started_at": started_at,
        "ended_at": started_at,
        "duration_sec": duration_sec,
        "outcome_icon": "✅" if outcome == "BOOKED" else "⚠️",
        "outcome_label": outcome,
        "cost_usd": cost,
        "booked_slot_iso": "2026-05-14T10:00:00-04:00" if outcome == "BOOKED" else None,
    }
    (parent / "summary.json").write_text(json.dumps(data))
    return parent / "summary.json"


@pytest.fixture
def base_dir(tmp_path):
    """A live-recordings/ root with several fake calls on 2026-05-12 Eastern.

    14:00 UTC = 10:00 EDT on 2026-05-12.
    20:00 UTC = 16:00 EDT on 2026-05-12.
    04:00 UTC on 2026-05-13 = 00:00 EDT 2026-05-13 (next day in local tz).
    """
    base = tmp_path / "live-recordings"
    _write_summary(base, folder="12-05-26 10-00 - Rock Epoxy",
                   call_id="c1", started_at="2026-05-12T14:00:00Z",
                   duration_sec=270, outcome="BOOKED", cost=0.45)
    _write_summary(base, folder="12-05-26 10-15 - Joe",
                   call_id="c2", started_at="2026-05-12T14:15:00Z",
                   duration_sec=42, outcome="HUNG UP", cost=0.05)
    _write_summary(base, folder="12-05-26 16-00 - West Texas Coatings",
                   call_id="c3", started_at="2026-05-12T20:00:00Z",
                   duration_sec=130, outcome="DISQUALIFIED", cost=0.18)
    # A legacy-layout (root) call also on the same day:
    _write_summary(base, folder="legacy-call-d",
                   call_id="c4", started_at="2026-05-12T19:30:00Z",
                   duration_sec=80, outcome="NOT INTERESTED", cost=0.10,
                   nested_month=None)
    # A call on the NEXT day (Eastern) — must NOT count toward 05-12:
    _write_summary(base, folder="13-05-26 00-00 - Late",
                   call_id="c5", started_at="2026-05-13T04:00:00Z",
                   duration_sec=60, outcome="OTHER", cost=0.07)
    return base


# ─────────── compute_day_stats ───────────

def test_compute_day_stats_counts_correctly(base_dir):
    s = stats.compute_day_stats(date(2026, 5, 12), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    assert s["date"] == "2026-05-12"
    assert s["total_calls"] == 4
    assert s["booked_count"] == 1
    assert s["booked_rate_pct"] == 25.0
    assert s["outcomes"] == {
        "BOOKED": 1, "HUNG UP": 1, "DISQUALIFIED": 1, "NOT INTERESTED": 1,
    }
    assert s["total_cost_usd"] == pytest.approx(0.78)
    # avg duration = (270 + 42 + 130 + 80) / 4 = 130
    assert s["avg_duration_sec"] == 130


def test_compute_day_stats_excludes_other_days(base_dir):
    s = stats.compute_day_stats(date(2026, 5, 13), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    assert s["total_calls"] == 1
    assert s["outcomes"] == {"OTHER": 1}


def test_compute_day_stats_zero_calls(base_dir):
    s = stats.compute_day_stats(date(2025, 1, 1), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    assert s["total_calls"] == 0
    assert s["booked_rate_pct"] == 0.0
    assert s["calls"] == []


def test_compute_day_stats_includes_call_list_sorted(base_dir):
    s = stats.compute_day_stats(date(2026, 5, 12), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    started_times = [c["started_at"] for c in s["calls"]]
    assert started_times == sorted(started_times)


# ─────────── format_digest ───────────

def test_format_digest_with_calls(base_dir):
    s = stats.compute_day_stats(date(2026, 5, 12), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    msg = stats.format_digest(s, period_label="Today")
    assert "📊 Today — 2026-05-12" in msg
    assert "Total: 4 calls · Booked: 1 (25%)" in msg
    assert "✅ BOOKED: 1" in msg
    assert "☎️ HUNG UP: 1" in msg
    assert "🚫 NOT INTERESTED: 1" in msg
    assert "❌ DISQUALIFIED: 1" in msg
    assert "Cost: $0.78" in msg
    assert "Avg duration: 130s" in msg


def test_format_digest_no_calls():
    msg = stats.format_digest({"date": "2026-05-12", "total_calls": 0},
                              period_label="Today")
    assert "No calls" in msg
    assert "2026-05-12" in msg


# ─────────── save_daily_stats ───────────

def test_save_daily_stats_writes_file(base_dir):
    s = stats.compute_day_stats(date(2026, 5, 12), base_dir,
                                 tz=ZoneInfo("America/New_York"))
    out = stats.save_daily_stats(s, base_dir)
    assert out == base_dir / "stats" / "daily" / "2026-05-12.json"
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["total_calls"] == 4
    assert loaded["booked_count"] == 1


def test_iter_summaries_skips_stats_subtree(base_dir):
    # Pre-create a stats/daily file with a stray summary.json under it —
    # should NOT be picked up by iter_summaries.
    stats_dir = base_dir / "stats" / "daily"
    stats_dir.mkdir(parents=True, exist_ok=True)
    (stats_dir / "summary.json").write_text('{"call_id": "DECOY"}')
    found_ids = [s.get("call_id") for _, s in stats.iter_summaries(base_dir)]
    assert "DECOY" not in found_ids
