#!/usr/bin/env python3
"""Compute the day's call stats, save to stats/daily/YYYY-MM-DD.json, and post
a digest to the Voiscito Telegram Group.

Designed to run nightly via systemd timer at 02:00 Eastern (after the prior
day has fully closed — Vapi can take minutes to deliver end-of-call-report;
running at 23:00 same-day was dropping calls that ended after the cutoff).
Default target is YESTERDAY (local time). Use --date to override.

Usage:
    python3 scripts/run_daily_stats.py                  # yesterday (local)
    python3 scripts/run_daily_stats.py --date 2026-05-12
    python3 scripts/run_daily_stats.py --today          # same-day stats
    python3 scripts/run_daily_stats.py --no-telegram    # save JSON only
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ─── env loading: VPS path first, local fallback ─────────────────────
for _env in ("/opt/webhooks/voicsito/.env",
             str(Path(__file__).resolve().parent.parent / "config" / ".env")):
    if os.path.exists(_env):
        for _line in open(_env):
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k, _v)
        break

# ─── make src importable regardless of CWD ───────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.webhooks.stats import (  # noqa: E402
    compute_day_stats,
    format_digest,
    save_daily_stats,
)
from src.webhooks.telegram_notify import send_message  # noqa: E402


async def amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday in display tz)")
    parser.add_argument("--today", action="store_true",
                        help="report on today (incomplete) instead of yesterday")
    parser.add_argument("--base-dir",
                        default=os.environ.get("LIVE_RECORDINGS_DIR",
                                               "/opt/webhooks/voicsito/live-recordings"))
    parser.add_argument("--tz", default=os.environ.get("DISPLAY_TZ", "America/New_York"))
    parser.add_argument("--no-telegram", action="store_true",
                        help="save JSON only; skip the Telegram digest push")
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        period_label = target.strftime("%a %b %d")
    elif args.today:
        target = datetime.now(tz).date()
        period_label = "Today"
    else:
        target = (datetime.now(tz) - timedelta(days=1)).date()
        period_label = "Yesterday"

    base = Path(args.base_dir)
    if not base.exists():
        print(f"ERROR: base-dir {base} does not exist", file=sys.stderr)
        return 1

    print(f"computing stats for {target} (tz={args.tz})")
    stats = compute_day_stats(target, base, tz=tz)
    out_path = save_daily_stats(stats, base)
    print(f"  wrote {out_path}  ({stats['total_calls']} calls, "
          f"{stats['booked_count']} booked, {stats['booked_rate_pct']:.1f}% rate)")

    if args.no_telegram:
        print("  --no-telegram set; skipping Telegram push")
        return 0

    digest = format_digest(stats, period_label=period_label)
    mid = await send_message(digest, parse_mode=None)
    if mid:
        print(f"  telegram digest sent (message_id={mid})")
    else:
        print("  telegram digest failed or disabled (check TELEGRAM_BOT_TOKEN)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
