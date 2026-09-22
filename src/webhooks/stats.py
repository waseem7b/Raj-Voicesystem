"""Aggregate call stats from per-call summary.json files.

Reads the summary.json files written by call_archive.archive_call and produces
per-day / per-week / per-month stat blobs. Pure-function-friendly so it can be
unit-tested with tmp_path fixtures.

Output shape (per-day stats blob):
    {
        "date": "2026-05-12",                  # local date in display tz
        "generated_at": "2026-05-13T03:00:00+00:00",
        "total_calls": 14,
        "outcomes": {"BOOKED": 3, "HUNG UP": 5, ...},
        "booked_count": 3,
        "booked_rate_pct": 21.4,
        "total_cost_usd": 3.42,
        "avg_duration_sec": 142,
        "calls": [                              # one entry per call, for review
            {"call_id": "...", "started_at": "...", "phone": "...",
             "outcome": "BOOKED", "duration_sec": 270, "cost_usd": 0.43,
             "booked_slot_iso": "...", "folder": "..."}
        ]
    }
"""
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# Display order for the digest. Matches the icons used elsewhere in the system.
OUTCOME_ORDER = ("BOOKED", "HUNG UP", "NOT INTERESTED", "DISQUALIFIED", "OTHER")

# Ashley's outbound calls use their own vocabulary, written by the outbound
# chain into summary.json. Without these the digest counted them and then
# printed nothing, because format_digest only renders labels it has an order
# entry for.
OUTBOUND_ORDER = ("CONFIRMED", "RESCHEDULED", "ANSWERED", "VOICEMAIL",
                  "NO ANSWER", "NOT INTERESTED", "OTHER")
OUTCOME_ICONS = {
    "BOOKED": "✅",
    "HUNG UP": "☎️",
    "DISQUALIFIED": "❌",
    "NOT INTERESTED": "🚫",
    "OTHER": "⚠️",
    "CONFIRMED": "✅",
    "RESCHEDULED": "📅",
    "ANSWERED": "☎️",
    "VOICEMAIL": "📼",
    "NO ANSWER": "❌",
}


def iter_summaries(base_dir: Path) -> Iterable[tuple[Path, dict]]:
    """Yield (summary_path, parsed_summary_dict) for every call archived under
    base_dir. Handles both the new MM-YYYY/<call>/ layout and the legacy
    <call>/ layout at root. Silently skips malformed files."""
    for path in list(base_dir.glob("*/*/summary.json")) + list(base_dir.glob("*/summary.json")):
        # Skip the stats/ subtree if it ever grows to 2 levels deep.
        if "stats" in path.parts:
            continue
        try:
            yield path, json.loads(path.read_text())
        except Exception as e:
            log.warning("stats: skipping unreadable %s err=%s", path, e)


def _local_date(iso_string: str, tz: ZoneInfo) -> Optional[date]:
    """Parse an ISO timestamp and return its local date in `tz`, or None."""
    if not iso_string:
        return None
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).date()
    except Exception:
        return None


def compute_day_stats(target_date: date, base_dir: Path,
                       tz: ZoneInfo = ZoneInfo("America/New_York")) -> dict:
    """Aggregate every summary.json whose local started_at falls on target_date."""
    summaries: list[tuple[Path, dict]] = []
    for path, s in iter_summaries(base_dir):
        d = _local_date(s.get("started_at", ""), tz)
        if d == target_date:
            summaries.append((path, s))
    return _aggregate(summaries, target_date)


def _aggregate(items: list[tuple[Path, dict]], target_date: date) -> dict:
    outcomes: dict[str, int] = {}
    inbound_outcomes: dict[str, int] = {}
    outbound_outcomes: dict[str, int] = {}
    total_cost = 0.0
    total_duration = 0
    booked = 0
    confirmed = 0
    confirmation_calls = 0
    calls_list: list[dict] = []
    for path, s in items:
        label = s.get("outcome_label", "OTHER") or "OTHER"
        call_type = (s.get("call_type") or "").strip().lower()
        outcomes[label] = outcomes.get(label, 0) + 1
        if call_type:
            outbound_outcomes[label] = outbound_outcomes.get(label, 0) + 1
            if call_type in ("confirmation", "reminder"):
                confirmation_calls += 1
                if label == "CONFIRMED":
                    confirmed += 1
        else:
            inbound_outcomes[label] = inbound_outcomes.get(label, 0) + 1
        cost = s.get("cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += cost
        dur = s.get("duration_sec", 0)
        if isinstance(dur, int):
            total_duration += dur
        if label == "BOOKED":
            booked += 1
        calls_list.append({
            "call_id": s.get("call_id"),
            "started_at": s.get("started_at"),
            "phone": s.get("phone"),
            "outcome": label,
            "call_type": call_type,
            "duration_sec": dur,
            "cost_usd": cost,
            "booked_slot_iso": s.get("booked_slot_iso"),
            "folder": path.parent.name,
        })
    calls_list.sort(key=lambda c: c.get("started_at") or "")
    total = len(items)
    return {
        "date": target_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_calls": total,
        "outcomes": outcomes,
        "inbound_outcomes": inbound_outcomes,
        "outbound_outcomes": outbound_outcomes,
        "inbound_calls": sum(inbound_outcomes.values()),
        "outbound_calls": sum(outbound_outcomes.values()),
        "confirmed_count": confirmed,
        "confirmation_calls": confirmation_calls,
        "confirmed_rate_pct": (round(100 * confirmed / confirmation_calls, 1)
                               if confirmation_calls else 0.0),
        "booked_count": booked,
        "booked_rate_pct": round(100 * booked / total, 1) if total else 0.0,
        "total_cost_usd": round(total_cost, 2),
        "avg_duration_sec": total_duration // total if total else 0,
        "calls": calls_list,
    }


def _outcome_lines(counts: dict, order: tuple) -> list[str]:
    """Render "<icon> LABEL: n" for every non-zero outcome.

    Labels outside `order` are still printed, after the known ones. The digest
    used to iterate the order tuple alone, so any outcome it did not recognise
    was counted and then silently dropped from the report.
    """
    out = []
    for label in order:
        if counts.get(label, 0) > 0:
            out.append(f"{OUTCOME_ICONS.get(label, '⚠️')} {label}: {counts[label]}")
    for label in sorted(counts):
        if label not in order and counts.get(label, 0) > 0:
            out.append(f"{OUTCOME_ICONS.get(label, '⚠️')} {label}: {counts[label]}")
    return out


def format_digest(stats: dict, *, period_label: str = "Today") -> str:
    """Build the plain-text Telegram digest message."""
    date_str = stats.get("date", "?")
    total = stats.get("total_calls", 0)
    if total == 0:
        return f"📊 {period_label} — {date_str}\n\nNo calls."

    booked = stats.get("booked_count", 0)
    rate = stats.get("booked_rate_pct", 0.0)

    lines = [
        f"📊 {period_label} — {date_str}",
        "",
        f"Total: {total} calls · Booked: {booked} ({rate:.0f}%)",
        "",
    ]
    inbound = stats.get("inbound_outcomes")
    outbound = stats.get("outbound_outcomes")

    if inbound is None and outbound is None:
        # Stats blob written before outbound calls were split out.
        lines.extend(_outcome_lines(stats.get("outcomes", {}), OUTCOME_ORDER))
    else:
        lines.extend(_outcome_lines(inbound or {}, OUTCOME_ORDER))
        if outbound:
            n_out = stats.get("outbound_calls", 0) or sum(outbound.values())
            head = f"Ashley: {n_out} call{'' if n_out == 1 else 's'}"
            if stats.get("confirmation_calls"):
                head += (f" · Confirmed: {stats.get('confirmed_count', 0)}"
                         f" ({stats.get('confirmed_rate_pct', 0.0):.0f}%)")
            if inbound:
                lines.append("")
            lines.append(head)
            lines.extend(_outcome_lines(outbound, OUTBOUND_ORDER))

    cost = stats.get("total_cost_usd", 0.0)
    avg = stats.get("avg_duration_sec", 0)
    if cost or avg:
        lines.append("")
        lines.append(f"Cost: ${cost:.2f}  ·  Avg duration: {avg}s")
    return "\n".join(lines)


def save_daily_stats(stats: dict, base_dir: Path) -> Path:
    """Write the stats blob to `<base_dir>/stats/daily/<date>.json`."""
    out_dir = base_dir / "stats" / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stats['date']}.json"
    path.write_text(json.dumps(stats, indent=2, sort_keys=True))
    return path
