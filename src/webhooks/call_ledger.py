"""Append-only ledger of every outbound call decision, read by the CRM.

One JSON line per decision in ``call-ledger/<UTC YYYY-MM-DD>.jsonl``:

  placed   we dialled and Vapi accepted the call
  skipped  a guard stopped the call (appointment not live, opted out, DND,
           prospect texted last)
  failed   Vapi refused the call or the request errored
  ended    the end-of-call report arrived, with the outcome

The CRM runs on the same VPS and reads these files on a cron into its
Confirmations report. The line contract lives on the CRM side in
``src/lib/ashley-calls/ledger.ts`` — change both together. There is no network
call and no shared secret: a file append cannot time out or 401.

Never raises. A ledger write sits on the dial path and the end-of-call path;
losing one line is far cheaper than losing a call or a tag.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# <project root>/call-ledger — /opt/webhooks/voicsito/call-ledger on the VPS.
LEDGER_DIR = Path(
    os.environ.get("CALL_LEDGER_DIR")
    or Path(__file__).resolve().parents[2] / "call-ledger"
)

_FIELDS = (
    "vapi_call_id",
    "ghl_contact_id",
    "ghl_appointment_id",
    "reason",
    "outcome",
    "outcome_tag",
    "ended_reason",
    "duration_sec",
    "cost",
    "business",
)

# _outbound_header's LABEL → the ledger's outcome key. One source of truth for
# what a call's outcome was: the Telegram header, the GHL comment and this
# ledger all read the same header.
_OUTCOME_BY_LABEL = {
    "CONFIRMED": "confirmed",
    "NOT INTERESTED": "not_interested",
    "NEEDS RESCHEDULE": "needs_reschedule",
    "RESCHEDULED": "rescheduled",
    "ANSWERED": "answered",
    "VOICEMAIL": "voicemail",
    "NO ANSWER": "no_answer",
}


def outcome_key(header: Optional[tuple[str, str]]) -> str:
    """(icon, LABEL) from _outbound_header → "voicemail" etc; "unknown" when None."""
    if not header:
        return "unknown"
    return _OUTCOME_BY_LABEL.get(header[1], "unknown")


def seconds_between(started: Any, ended: Any) -> Optional[int]:
    """Whole seconds between two Vapi ISO timestamps, or None if either is missing."""
    try:
        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds()))
    except Exception:  # noqa: BLE001 — absent or malformed timestamps
        return None


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(
    event: str,
    *,
    call_type: str,
    at: Optional[datetime] = None,
    source: str = "live",
    base_dir: Optional[Path] = None,
    **fields: Any,
) -> bool:
    """Append one line. Returns True when written. Never raises.

    Only the known fields are written; anything else passed is ignored, so a
    typo cannot put an unexpected key on the CRM's side of the contract.
    """
    try:
        when = at or datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        line: dict[str, Any] = {
            "v": 1,
            "event": event,
            "at": _iso_z(when),
            "call_type": (call_type or "").strip().lower() or "unknown",
        }
        for key in _FIELDS:
            value = fields.get(key)
            line[key] = value if value not in ("", None) else None
        line["source"] = source

        directory = Path(base_dir) if base_dir else LEDGER_DIR
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{when.astimezone(timezone.utc):%Y-%m-%d}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — never touch the call path
        log.warning("call ledger write failed event=%s call_type=%s: %s", event, call_type, e)
        return False
