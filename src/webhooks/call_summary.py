"""Pure formatting for Telegram messages. No I/O, no env access.

Anything here is pure-function-friendly so unit tests can run offline.
"""
import json
from datetime import datetime
from typing import Optional


# ─────────────────────── Outcome classification ───────────────────────

def _booked_slot_iso(payload: dict) -> Optional[str]:
    """If book_video_call was invoked AND returned success, extract the slot ISO."""
    messages = payload.get("messages") or []
    booked_slot = None
    for m in messages:
        if m.get("role") == "tool_calls":
            for tc in m.get("toolCalls") or []:
                fn = tc.get("function") or {}
                if fn.get("name") == "book_video_call":
                    raw = fn.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                        if isinstance(args, dict) and args.get("slot_iso"):
                            booked_slot = args["slot_iso"]
                    except Exception:
                        pass
        elif m.get("role") == "tool_call_result" and m.get("name") == "book_video_call":
            result = m.get("result") or ""
            if "Booked successfully" in result and booked_slot:
                return booked_slot
    return None


def _transfer_initiated(payload: dict) -> bool:
    """True if mark_for_transfer was invoked — i.e. the prospect gave a verbal
    yes to booking. In the Stage 3 flow this fires BEFORE book_video_call, so a
    transfer with no successful book_video_call means they agreed but the
    booking never completed."""
    for m in (payload.get("messages") or []):
        if m.get("role") == "tool_calls":
            for tc in (m.get("toolCalls") or []):
                if ((tc.get("function") or {}).get("name")) == "mark_for_transfer":
                    return True
        elif m.get("role") == "tool_call_result" and m.get("name") == "mark_for_transfer":
            return True
    return False


# Substrings in analysis.summary that flag a genuine disqualifier vs the
# default "not interested" bucket. Tuned conservatively — anything that
# doesn't match goes into NOT INTERESTED. Lowercase comparison.
_DISQUALIFIER_HINTS = (
    "out of area",
    "outside service",
    "outside our service",
    "not in service area",
    "wrong area",
    "not a fit",
    "doesn't fit",
    "doesnt fit",
    "retiring",
    "no business",
    "shutting down",
    "no longer in business",
    "wrong service",
    "wrong industry",
)


def classify_outcome(payload: dict) -> tuple[str, str, Optional[str]]:
    """Return (icon, label, booked_slot_iso_or_None).

    Buckets and icons (checked in this order):
      ✅ BOOKED              — book_video_call completed (real calendar booking)
      🟡 AGREED – NOT BOOKED — prospect agreed (mark_for_transfer fired) but
                               book_video_call never completed: a hot lead that
                               needs manual follow-up, NOT a loss
      ☎️ HUNG UP             — customer ended the call without booking
      ❌ DISQUALIFIED        — assistant ended, summary mentions a disqualifier
      🚫 NOT INTERESTED      — assistant ended, no booking, no disqualifier keyword
      ⚠️ OTHER               — anything else (silence-timeout, error, etc.)

    A confirmed book_video_call result is ground truth for BOOKED — we do NOT
    gate it on analysis.successEvaluation (the LLM judge), which can disagree
    with a booking that actually landed.
    """
    analysis = payload.get("analysis") or {}
    ended_reason = payload.get("endedReason", "")

    slot_iso = _booked_slot_iso(payload)
    if slot_iso:
        return ("✅", "BOOKED", slot_iso)
    if _transfer_initiated(payload):
        return ("🟡", "AGREED – NOT BOOKED", None)

    summary = (analysis.get("summary") or "").lower()
    looks_disqualified = any(h in summary for h in _DISQUALIFIER_HINTS)

    if ended_reason == "customer-ended-call":
        return ("☎️", "HUNG UP", None)
    if ended_reason == "assistant-ended-call":
        if looks_disqualified:
            return ("❌", "DISQUALIFIED", None)
        return ("🚫", "NOT INTERESTED", None)
    return ("⚠️", "OTHER", None)


# ─────────────────────── Duration helper ───────────────────────

def duration_label(started_at: str, ended_at: str) -> str:
    """ '4m 22s' or '42s'. Empty string if either timestamp is malformed."""
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        secs = max(0, int((e - s).total_seconds()))
    except Exception:
        return ""
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _fmt_slot_human(slot_iso: str) -> str:
    """ '2026-05-14T10:00:00-04:00' -> 'Wed May 14 · 10:00am'. Falls back to
    the raw ISO if parsing fails."""
    try:
        dt = datetime.fromisoformat(slot_iso)
        hour = dt.strftime("%I:%M%p").lstrip("0").lower()
        day = dt.strftime("%d").lstrip("0")
        return dt.strftime(f"%a %b {day} · {hour}")
    except Exception:
        return slot_iso


# ─────────────────────── Message formatters ───────────────────────

CRM_LABELS = {
    "main": "Main",
    "texts1": "Texts 1",
    "texts2": "Texts 2",
}


def _crm_label(contact: Optional[dict]) -> Optional[str]:
    if not contact or not isinstance(contact, dict):
        return None
    key = (contact.get("crm") or "").strip()
    if not key:
        return None
    return CRM_LABELS.get(key, key)


def _caller_line(phone: str, contact: Optional[dict]) -> str:
    """Build the 'Number / Business / City' segment.
    Falls back to first_name if business is empty. Skips empty parts.
    Returns 'Number / (cold caller)' when no CRM match.
    """
    parts = [phone or "(unknown)"]
    if not contact or not isinstance(contact, dict):
        parts.append("(cold caller)")
        return " / ".join(parts)
    biz = (contact.get("business") or "").strip()
    if not biz:
        biz = (contact.get("first_name") or "").strip()
    city = (contact.get("city") or "").strip()
    if biz:
        parts.append(biz)
    if city:
        parts.append(city)
    return " / ".join(parts)


def format_started_message(phone: str, contact: Optional[dict]) -> str:
    """📲 NEW PHONE CALL\n\nCaller: Number / Business / City\nCRM: <label>"""
    lines = [
        "📲 NEW PHONE CALL",
        "",
        f"Caller: {_caller_line(phone, contact)}",
    ]
    crm = _crm_label(contact)
    if crm:
        lines.append(f"CRM: {crm}")
    return "\n".join(lines)


def format_ended_summary(payload: dict, contact: Optional[dict],
                         folder_name: str) -> str:
    """Outcome summary for Telegram. Plain text, no Markdown.

    <icon> <label> [· <duration>]      ← header is just the outcome

    Caller: Number / Business / City
    [CRM: <label>]
    [Booked: <slot>]
    Cost: $X.XX
    ID: <folder_name>

    Transcript:           ← label only; transcript follows as a separate msg
    """
    phone = ((payload.get("call") or {}).get("customer", {}) or {}).get("number", "")
    caller_line = _caller_line(phone, contact)

    icon, label, slot_iso = classify_outcome(payload)

    cost = payload.get("cost")
    cost_text = f"${cost:.2f}" if isinstance(cost, (int, float)) else "—"

    has_transcript = bool((payload.get("transcript") or "").strip())

    dur = duration_label(payload.get("startedAt", ""), payload.get("endedAt", ""))
    header_parts = [f"{icon} {label}"]
    if dur:
        header_parts.append(dur)

    lines = [
        " · ".join(header_parts),
        "",
        f"Caller: {caller_line}",
    ]
    crm = _crm_label(contact)
    if crm:
        lines.append(f"CRM: {crm}")
    if slot_iso:
        lines.append(f"Booked: {_fmt_slot_human(slot_iso)}")
    lines += [
        f"Cost: {cost_text}",
        f"ID: {folder_name}",
    ]
    if has_transcript:
        lines.append("")
        lines.append("Transcript:")
    return "\n".join(lines)
