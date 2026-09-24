"""GHL-facing trigger — the one endpoint GoHighLevel POSTs to.

    GHL workflow (T-1day / T-Xh)
        -> POST https://webhooks.c7a.io/voicsito/ghl/outbound-call
        -> this module picks the assistant and calls Vapi /call
        -> Vapi dials the prospect

This is the ONLY inbound route in this repo that is not Vapi calling us, which
is why it lives in its own module with its own secret: a bug or a leaked secret
here must not reach the Vapi tool endpoints the live inbound bot depends on.

Duplicate suppression matters more than delivery here. GHL retries a webhook on
5xx, and a reschedule re-fires "Appointment Booked" without ever firing
"Appointment Cancelled" — so without a guard the same prospect gets dialed
twice. Dialing nobody is recoverable; dialing someone twice is not. Hence: an
in-process dedupe window keyed on (call_type, appointment or phone), and a 200
returned even on a Vapi failure so GHL never retries into a double-dial. Real
failures are loud in the log, not in the status code.

The dedupe is per-process, which is correct while this runs as a single uvicorn
worker under systemd. A multi-worker deploy would need Redis (REDIS_URL is
already in the env template).
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.agent import ghl

from .vapi_client import VapiError, create_outbound_call, normalize_phone
# Reused from the outbound tool handlers so the spoken time format and the
# time-of-day rule have exactly one implementation. Safe at module level:
# server.py mounts outbound before this module, so it is already loaded.
from . import call_ledger
from .outbound import OUTCOME_TAGS
from .outbound import _local_greeting as local_greeting
from .outbound import _prospect_tz_if_known as prospect_tz_if_known
from .outbound import _spoken_booked_time as spoken_booked_time
from .text_digest import build_digest, should_hold_for_reply
from .tz_resolver import resolve_timezone

log = logging.getLogger(__name__)

router = APIRouter()

# call_type -> env var holding that flow's assistant id
ASSISTANT_ENV_BY_CALL_TYPE = {
    "confirmation": "VAPI_ASSISTANT_ID_CONFIRMATION",  # day-before: confirm, or hand off
    "reminder": "VAPI_ASSISTANT_ID_REMINDER",          # day-of: logistics, or hand off
    "rebooking": "VAPI_ASSISTANT_ID_REBOOKING",        # call-back: find + lock in a new time
    "test": "VAPI_ASSISTANT_ID_TEST",                  # barebones reminder twin
    "test2": "VAPI_ASSISTANT_ID_TEST2",                # outcome-driven twin, no script
}

# Fallback assistant ids, used only when the matching env var is unset. This
# exists so the barebones test route works without editing the production .env
# on the VPS. Set VAPI_ASSISTANT_ID_TEST there to override.
FALLBACK_ASSISTANT_ID_BY_CALL_TYPE = {
    # Rebooking split out 2026-09-04; its id lives here so the call-back works
    # before anyone touches the VPS .env. VAPI_ASSISTANT_ID_REBOOKING overrides.
    "rebooking": "a19e14c4-ef68-4e37-98b3-c3e448290e71",
    "test": "67641961-542c-4b5f-831f-6b80bef8833b",  # Voicsito - Ashley - TEST
    "test2": "4f11df02-4401-4dfa-8413-a0e98a03099c",  # Voicsito - Ashley - TEST V2
}

# Why the rebooking call is being placed, keyed by the path that fired it.
#
# The value is MODEL-FACING TEXT: Vapi renders it into {{callReason}} in the
# rebooking prompt before the model runs, so the assistant reads one plain
# statement of fact rather than evaluating a branch. None of it is spoken —
# what she SAYS is still in her own words, per the owner's wording rule.
#
# "conflict" is the default: the original design, where the prospect told
# Ashley on a confirmation or reminder call that the time no longer works.
REBOOK_REASONS = {
    "conflict": (
        "they told you on your last call that they can't make the time they "
        "booked with Jon"
    ),
    "no_show": "they were booked for a call with Jon and did not join it",
    "cancelled": "they cancelled the call they had booked with Jon",
}
DEFAULT_REBOOK_REASON = "conflict"

# What the body's `type` field may say, normalised to a REBOOK_REASONS key.
# Spellings are generous because this is typed by hand into a GHL webhook step.
# Absent `type` = the default above, which keeps the original call working.
# An unrecognised `type` is a 422, never a silent fallback — guessing is how a
# misrouted rebooking call would go unnoticed.
REBOOK_TYPE_ALIASES = {
    "no-show": "no_show", "no_show": "no_show", "noshow": "no_show",
    "cancelled": "cancelled", "canceled": "cancelled", "cancel": "cancelled",
    "conflict": "conflict", "reschedule": "conflict",
    "needs-reschedule": "conflict", "needs_reschedule": "conflict",
}

# Appointment states where a confirmation/reminder call still makes sense.
# Anything else (cancelled, noshow, invalid) must not be dialed.
LIVE_APPOINTMENT_STATUSES = {"booked", "confirmed", "scheduled", "new", "rescheduled", "showed"}

# Narrow guard against an instant double-fire — two webhook steps pointing at
# this endpoint, or a stray manual test — and nothing more.
#
# History: started at 6 hours, which silently swallowed the deliberate 2pm
# retry (a claim is only released when Vapi REFUSES, never on success). Cut to
# 15 minutes, then to 20 seconds (owner 2026-08-22) so nothing intentional can
# ever be blocked. At this width it only catches requests arriving in the same
# breath, which is exactly the accident worth catching.
DEDUPE_WINDOW_S = 20
_recent: dict[str, float] = {}


def _claim(key: str) -> bool:
    """True if this is the first claim for `key` inside the dedupe window."""
    now = time.time()
    for k, ts in list(_recent.items()):
        if now - ts > DEDUPE_WINDOW_S:
            del _recent[k]
    if key in _recent:
        return False
    _recent[key] = now
    return True


def _first(payload: dict, *keys: str) -> Optional[str]:
    """First usable value across the body then customData, skipping unrendered
    GHL merge fields."""
    custom = payload.get("customData") or {}
    for source in (payload, custom if isinstance(custom, dict) else {}):
        for k in keys:
            v = source.get(k)
            if isinstance(v, str):
                s = v.strip()
                if s and not (s.startswith("{{") and s.endswith("}}")):
                    return s
    return None


async def _resolve_booking(number: str) -> dict:
    """Look the prospect's booking up before we dial.

    Never raises: a lookup failure must not stop the call. Empty fields make the
    assistant fall back to a no-time opener, which is the correct degradation —
    far better than a wrong time, and far better than not calling at all.
    """
    try:
        out = await ghl.get_prospect_appointment(number)
    except Exception as e:  # noqa: BLE001 — degrade, never block the call
        log.warning("pre-call lookup failed for %s: %s", number, e)
        return {}

    if not out.get("ok"):
        # Known contact with nothing booked still gives us a name to greet with.
        if out.get("reason") == "no_appointment":
            return {
                "first_name": out.get("first_name", ""),
                "contact_id": out.get("contact_id", ""),
                "business": out.get("business", ""),
                "greeting": local_greeting(out),
                # A rebooking prospect usually has nothing upcoming, so this is the
                # path that has to carry what the missed-day phrase needs.
                "timezone": out.get("timezone", ""),
                "city": out.get("city", ""),
                "state": out.get("state", ""),
                "missed_start_iso": out.get("missed_start_iso", ""),
            }
        return {}

    return {
        "first_name": out.get("first_name", ""),
        "contact_id": out.get("contact_id", ""),
        "business": out.get("business", ""),
        "appointment_id": out.get("appointment_id", ""),
        "booked_time": spoken_booked_time(out.get("start_iso", ""), prospect_tz_if_known(out)),
        "greeting": local_greeting(out),
        # Kept for the day-of "is this actually today?" check; never sent to Vapi.
        "start_iso": out.get("start_iso", ""),
        "timezone": out.get("timezone", ""),
        "city": out.get("city", ""),
        "state": out.get("state", ""),
        "missed_start_iso": out.get("missed_start_iso", ""),
    }


def _missed_day_phrase(lookup: dict, now: Optional[datetime] = None) -> str:
    """How Ashley names the call they missed, in the PROSPECT's timezone:
    "earlier today", "yesterday" or "last Monday", or "today" for a booking later
    today that was cancelled before it started. Blank when no missed call is known,
    it was a week or more ago, or it was cancelled for a later day; the rebooking
    prompt then leaves the day out.

    Owner's rebooking script (2026-09-14): "You weren't able to jump on our call
    last x day."
    """
    iso = lookup.get("missed_start_iso") or ""
    if not iso:
        return ""
    tz_name = resolve_timezone(
        lookup.get("timezone", "") or "",
        lookup.get("city", "") or "",
        lookup.get("state", "") or "",
    )
    try:
        tz = ZoneInfo(tz_name)
        start = datetime.fromisoformat(iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        start_local = start.astimezone(tz)
        now_local = (now or datetime.now(tz)).astimezone(tz)
    except Exception:  # noqa: BLE001 — an unreadable date means no day, never a wrong one
        return ""
    if start_local > now_local:
        # Cancelled before it started (call 01a0a257: tonight's booking). Only a
        # booking for today can be named; "our call tomorrow" is not a missed call.
        return "today" if start_local.date() == now_local.date() else ""
    days = (now_local.date() - start_local.date()).days
    if days == 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if 2 <= days <= 6:
        return f"last {start.astimezone(tz).strftime('%A')}"
    return ""


# Owner's A/B on the choppy why-calling line (2026-09-14): commas sounded better
# but not perfect. Round 2 tests the Rebooking voice settings with his original
# full stops; round 3 sets this back to True to hear both together.
WHY_CALLING_JOIN_WITH_COMMAS = False


def _why_calling_line(missed_day: str) -> str:
    """The owner's "why I'm calling" line for the rebooking call (his script,
    2026-09-14), with the day they missed filled in; a blank day drops it.

    Built here rather than in the prompt because the model, assembling it around
    {{missedDay}}, re-punctuated it (calls 01a0a257, 01a0a26a).

    Joined with commas, one full stop at the end. Vapi cuts TTS input at sentence
    ends and ElevenLabs voices each piece as a fresh generation, so his three
    sentences went out as three pieces and the delivery reset at each cut (call
    01a0a281 log: 162 / 43 / 87-char chunks; owner: "choppy"). His words are
    unchanged; only the punctuation between them is."""
    day = f" {missed_day}" if missed_day else ""
    if WHY_CALLING_JOIN_WITH_COMMAS:
        return (
            "You had a call booked with my team member Jon to dive into your business "
            "and to show you how we can help you get more jobs, "
            f"you weren't able to jump on our call{day}, "
            "figured you got tied up so I'm just giving you a call to get something locked in again."
        )
    return (
        "You had a call booked with my team member Jon to dive into your business "
        "and to show you how we can help you get more jobs. "
        f"You weren't able to jump on our call{day}. "
        "Figured you got tied up so I'm just giving you a call to get something locked in again."
    )


def _spoken_day_label(day, today) -> str:
    """"today", "tomorrow", the weekday, or "next <weekday>" once it is a week or more
    out, the way a person names a day. A 7-day window ends on today's weekday, so
    that day must not be called plain "Monday" on a Monday."""
    delta = (day - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta >= 7:
        return f"next {day.strftime('%A')}"
    return day.strftime("%A")


PRELOAD_DAYS = 8  # today plus the 7 days GHL is asked for




def _prospect_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — an unknown zone falls back, never breaks the call
        return ZoneInfo("America/New_York")


def _current_day_line(tz_name: str, now: Optional[datetime] = None) -> str:
    """The prospect's clock, written out for the prompt: "It is 1:34pm on Tuesday,
    September 15. Tomorrow is Wednesday, September 16." Vapi's own currentDateTime is
    UTC, so the model had no usable "today" and, on call 01a0a622, read today's 8pm
    slot as tomorrow's."""
    tz = _prospect_tz(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    tomorrow = now_local + timedelta(days=1)
    hour = now_local.hour % 12 or 12
    clock = f"{hour}:{now_local.minute:02d}{'am' if now_local.hour < 12 else 'pm'}"
    return (
        f"It is {clock} on {now_local.strftime('%A, %B')} {now_local.day}. "
        f"Tomorrow is {tomorrow.strftime('%A, %B')} {tomorrow.day}."
    )


def _format_preloaded_slots(slots: dict, tz_name: str, now: Optional[datetime] = None) -> tuple[str, set]:
    """Open times for the rebooking prompt: one line for EVERY day of the coming
    week, oldest first, empty days marked "no openings".

    Each day is named relative to the PROSPECT's today ("tomorrow (Tuesday Sep 15)"),
    so Ashley says "tomorrow" instead of reading a weekday off the calendar. Every
    day gets a line even with nothing open (call 01a0a622: Wednesday had no line, so
    "tomorrow" was a gap she filled with today's slot). Times carry the same
    [iso:...] tags the booking tool expects. Returns the text and the set of ISOs
    offered, which the booking tool validates against.
    """
    tz = _prospect_tz(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    by_day = _preloaded_days(slots, tz_name, now_local)
    offered = {iso for pairs in by_day.values() for _, iso in pairs}
    if not by_day:
        return "", offered
    lines = []
    for n in range(PRELOAD_DAYS):
        day = today + timedelta(days=n)
        label = f"{_spoken_day_label(day, today)} ({day.strftime('%A %b')} {day.day})"
        if day in by_day:
            times = [f"{local.hour % 12 or 12}:{local.minute:02d}{'am' if local.hour < 12 else 'pm'} [iso:{iso}]"
                     for local, iso in by_day[day]]
            lines.append(f"{label}: " + ", ".join(times))
        else:
            lines.append(f"{label}: no openings")
    return "\n".join(lines), offered


def _preloaded_days(slots: dict, tz_name: str, now_local: datetime) -> dict:
    """{date: [(local_start, iso), ...]} for every future slot GHL returned, at most
    six per day, in time order. The text list and the first offer both read this."""
    tz = _prospect_tz(tz_name)
    by_day: dict = {}
    for info in (slots or {}).values():
        if not isinstance(info, dict):
            continue
        for iso in (info.get("slots") or [])[:6]:
            try:
                dt = datetime.fromisoformat(iso)
            except (TypeError, ValueError):
                continue
            local = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
            if local <= now_local:
                continue  # already passed — never offer a time she cannot book
            by_day.setdefault(local.date(), []).append((local, iso))
    for pairs in by_day.values():
        pairs.sort(key=lambda pair: pair[0])
    return by_day


def _spoken_pair(a_iso: str, b_iso: str, tz_name: str) -> str:
    """Two spoken times; a shared part of day is said once, after the second
    ("seven or ten in the morning"). Owner, 2026-09-15 (call 01a0a796)."""
    a, b = spoken_booked_time(a_iso, tz_name), spoken_booked_time(b_iso, tz_name)
    for part in (" in the morning", " in the afternoon", " in the evening", " at night"):
        if a.endswith(part) and b.endswith(part):
            return f"{a[: -len(part)]} or {b}"
    return f"{a} or {b}"


def _day_offer(pairs: list, day_label: str, tz_name: str) -> str:
    """One day's offer line in the owner's shape: first morning + first afternoon
    opening; a day with only one half gives its first two; one slot gives one."""
    mornings = [p for p in pairs if p[0].hour < 12]
    afternoons = [p for p in pairs if p[0].hour >= 12]
    if mornings and afternoons:
        times = _spoken_pair(mornings[0][1], afternoons[0][1], tz_name)
    elif len(pairs) > 1:
        times = _spoken_pair(pairs[0][1], pairs[1][1], tz_name)
    else:
        times = spoken_booked_time(pairs[0][1], tz_name)
    return f"How does {day_label} at {times} work for you?"


def _day_offer_lines(slots: dict, tz_name: str, now: Optional[datetime] = None) -> str:
    """An offer line for every day with openings, one per line, e.g.
    "How does Friday at seven in the morning or one in the afternoon work for you?"
    No label in front: she would read it ("Friday. How does Friday...").

    When the prospect asks for a day, Ashley reads that day's line. The prompt rule
    for offers she composed herself did not hold on gpt-4.1 (calls 01a0a796 and
    01a0a7e0: "seven in the morning or ten in the morning", two mornings on a day
    that had afternoons), and every line the webhook has written for her has held.
    Blank when there are no slots.
    """
    tz = _prospect_tz(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    by_day = _preloaded_days(slots, tz_name, now_local)
    return "\n".join(
        _day_offer(by_day[day], _spoken_day_label(day, today), tz_name) for day in sorted(by_day)
    )


def _more_times_lines(slots: dict, tz_name: str, now: Optional[datetime] = None) -> str:
    """One line per day and half of the day with openings, e.g.
    "[tomorrow afternoon] How about three in the afternoon or five in the evening?"

    Said when the day is already the subject — "what else do you have that
    afternoon?" — so the line never names the day again. Call 01a0ab63 answered that
    question with "How does tomorrow at three in the afternoon or five in the
    afternoon work for you?": the day repeated, and the part of day after every time.
    Both faults come from her composing the line herself, which no prompt rule has
    fixed on 4.1, 5.4 or 5.5; every line the webhook writes has been spoken as
    written. The [label] is how she finds the line — the persona already rules out
    speaking brackets. Blank when there are no slots.
    """
    tz = _prospect_tz(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    by_day = _preloaded_days(slots, tz_name, now_local)
    lines = []
    for day in sorted(by_day):
        label = _spoken_day_label(day, today)
        for part, pairs in (
            ("morning", [p for p in by_day[day] if p[0].hour < 12]),
            ("afternoon", [p for p in by_day[day] if p[0].hour >= 12]),
        ):
            if not pairs:
                continue
            times = (_spoken_pair(pairs[0][1], pairs[1][1], tz_name) if len(pairs) > 1
                     else spoken_booked_time(pairs[0][1], tz_name))
            lines.append(f"[{label} {part}] How about {times}?")
    return "\n".join(lines)


def _first_offer_line(slots: dict, tz_name: str, now: Optional[datetime] = None) -> str:
    """The owner's offer line, written out with the day named ONCE:
    "How does Thursday at seven in the morning or two in the afternoon work for you?"

    The prompt used to carry two anchors (one per day, or one day named once) and
    the model picked between them — call 01a0a678 said "Thursday at seven in the
    morning or Thursday at two in the afternoon". Picking the pair here removes the
    choice: the first morning opening (before noon) and the first afternoon opening
    from the earliest day that has both; failing that, the earliest opening and the
    next one in the other half of the day, naming each day only when they differ.
    Blank when there are no slots (the prompt then builds the line itself).
    """
    tz = _prospect_tz(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)
    today = now_local.date()
    by_day = _preloaded_days(slots, tz_name, now_local)
    if not by_day:
        return ""

    def label(day) -> str:
        return _spoken_day_label(day, today)

    def spoken(iso: str) -> str:
        return spoken_booked_time(iso, tz_name)

    def pair(a_iso: str, b_iso: str) -> str:
        return _spoken_pair(a_iso, b_iso, tz_name)

    for day in sorted(by_day):
        mornings = [p for p in by_day[day] if p[0].hour < 12]
        afternoons = [p for p in by_day[day] if p[0].hour >= 12]
        if mornings and afternoons:
            return (f"How does {label(day)} at {spoken(mornings[0][1])} "
                    f"or {spoken(afternoons[0][1])} work for you?")
    first_day = min(by_day)
    first_local, first_iso = by_day[first_day][0]
    want_morning = first_local.hour >= 12
    for day in sorted(by_day):
        for local, iso in by_day[day]:
            if (local.hour < 12) == want_morning and iso != first_iso:
                if day == first_day:
                    return f"How does {label(day)} at {pair(first_iso, iso)} work for you?"
                return (f"How does {label(first_day)} at {spoken(first_iso)} "
                        f"or {label(day)} at {spoken(iso)} work for you?")
    # Nothing in the other half of the day anywhere: the first day's first two.
    if len(by_day[first_day]) > 1:
        return f"How does {label(first_day)} at {pair(first_iso, by_day[first_day][1][1])} work for you?"
    return f"How does {label(first_day)} at {spoken(first_iso)} work for you?"


async def _preload_rebooking_slots(lookup: dict) -> tuple[str, set, str, str, str]:
    """Jon's open times for the next week, fetched before dialling so Ashley can
    offer them without a mid-call calendar pull. Never raises: on any failure the
    call still goes out, the text is blank, and the prompt falls back to the tool."""
    tz_name = resolve_timezone(
        lookup.get("timezone", "") or "",
        lookup.get("city", "") or "",
        lookup.get("state", "") or "",
    )
    try:
        slots = await ghl.get_available_slots(
            days_ahead=7,
            timezone=tz_name,
            calendar_id=ghl.VIDEO_CALL_CALENDAR_ID,
            user_id=ghl.REBOOKING_ASSIGNED_USER_ID,
        )
    except Exception as e:  # noqa: BLE001 — degrade to the in-call tool, never block the call
        log.warning("rebooking preload: slot fetch failed: %s", e)
        return "", set(), "", "", ""
    text, offered = _format_preloaded_slots(slots, tz_name)
    return (text, offered, _first_offer_line(slots, tz_name),
            _day_offer_lines(slots, tz_name), _more_times_lines(slots, tz_name))


async def _seed_rebooking_call(call_id: str, lookup: dict, contact_id: str, number: str, offered: set) -> None:
    """Hand the preloaded contact and offered times to the call's cache, so the
    in-call tools book against the MAIN-CRM contact and accept the times she read out."""
    from .server import _cache_get  # imported here: server imports this module
    try:
        entry = await _cache_get(call_id)
        main_id = lookup.get("contact_id") or contact_id
        if main_id:
            entry["contact"] = {
                "id": main_id,
                "crm": "main",
                "first_name": lookup.get("first_name", "") or "",
                "business": lookup.get("business", "") or "",
                "city": lookup.get("city", "") or "",
                "state": lookup.get("state", "") or "",
                "timezone": lookup.get("timezone", "") or "",
            }
            entry["lookup_done"] = True
        entry["phone"] = number
        entry["call_type"] = "rebooking"
        entry["confirm_flow"] = True
        entry.setdefault("offered_slots", set()).update(offered)
    except Exception as e:  # noqa: BLE001 — the call is already ringing; tools fall back to metadata
        log.error("rebooking preload: cache seed failed call_id=%s err=%s", call_id, e)


def _starts_today_for_prospect(lookup: dict) -> bool:
    """True when the booking falls on today's date in the PROSPECT's timezone.

    Guards the day-of reminder, which says "later today". The lookup returns
    the soonest upcoming appointment whatever day it is on, so without this a
    prospect whose only booking is tomorrow gets told it is today. Compared in
    their timezone, not UTC — at 01:00 UTC the UTC date is already tomorrow
    across North America.
    """
    iso = lookup.get("start_iso") or ""
    if not iso:
        return False
    tz_name = resolve_timezone(
        lookup.get("timezone", "") or "",
        lookup.get("city", "") or "",
        lookup.get("state", "") or "",
    )
    try:
        tz = ZoneInfo(tz_name)
        start = datetime.fromisoformat(iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        return start.astimezone(tz).date() == datetime.now(tz).date()
    except Exception:  # noqa: BLE001 — an unparseable date is not "today"
        return False


@router.post("/ghl/outbound-call")
async def ghl_outbound_call(
    request: Request,
    x_ghl_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Dial a prospect with the assistant that matches `call_type`."""
    return await _place_call(request, x_ghl_secret, forced_call_type=None)


@router.post("/ghl/rebooking-call")
async def ghl_rebooking_call(
    request: Request,
    x_ghl_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Dial the Rebooking assistant. `call_type` in the body is ignored.

    Its own path, not a call_type on /ghl/outbound-call, because the CRM has a
    SEPARATE demo-rebooking TEXT drip at
    sales.c7a.io/api/systems/rebooking/entered. That endpoint takes almost any
    payload and returns 200, so a rebooking call aimed at it fails silently —
    which is exactly what happened on 2026-09-07. Two distinct paths, one per
    system, so the wrong URL cannot quietly succeed.

    The live-appointment-status guard does NOT apply here. That guard exists so
    a cancelled booking never gets asked "are we still on?". Offering a
    cancelled prospect a new time is this call's whole purpose, so blocking on
    status would defeat it. The status is logged, never enforced.
    """
    return await _place_call(request, x_ghl_secret, forced_call_type="rebooking")


def _ledger_target(lookup: dict, contact_id: Optional[str], appointment_id: Optional[str]) -> dict:
    """Who a call decision was about, for the call ledger. Same precedence as the
    call metadata below: the resolved booking wins over the workflow's hints."""
    return {
        "ghl_contact_id": lookup.get("contact_id") or contact_id,
        "ghl_appointment_id": lookup.get("appointment_id") or appointment_id,
        "business": lookup.get("business") or None,
    }


async def _place_call(
    request: Request,
    x_ghl_secret: Optional[str],
    forced_call_type: Optional[str] = None,
) -> JSONResponse:
    """Shared body for every outbound-call route.

    `forced_call_type` pins the flow for a dedicated path and makes the body's
    own call_type irrelevant, so a copy-pasted GHL webhook step cannot dial the
    wrong assistant.
    """
    expected = os.getenv("GHL_OUTBOUND_SECRET")
    if not expected:
        log.error("GHL_OUTBOUND_SECRET is not set — refusing to originate calls")
        return JSONResponse(status_code=500, content={"error": "server_not_configured"})
    if not x_ghl_secret or not hmac.compare_digest(x_ghl_secret, expected):
        log.warning("ghl/outbound-call rejected: bad or missing secret")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001 — any parse failure is the same 422
        return JSONResponse(status_code=422, content={"error": "invalid_json"})

    appointment_id_hint = _first(payload, "appointment_id", "appointmentId")
    call_type = (
        forced_call_type
        or (_first(payload, "call_type", "callType") or "confirmation")
    ).lower()
    assistant_env = ASSISTANT_ENV_BY_CALL_TYPE.get(call_type)
    if not assistant_env:
        log.warning("ghl/outbound-call unknown call_type=%r", call_type)
        return JSONResponse(
            status_code=422,
            content={"error": "unknown_call_type", "known": sorted(ASSISTANT_ENV_BY_CALL_TYPE)},
        )
    assistant_id = os.getenv(assistant_env) or FALLBACK_ASSISTANT_ID_BY_CALL_TYPE.get(call_type)
    if not assistant_id:
        log.error("ghl/outbound-call %s is not set", assistant_env)
        return JSONResponse(status_code=500, content={"error": "assistant_not_configured"})

    # The GHL "Appointment status" trigger fires on cancelled and no-show too,
    # not just booked. Calling someone who just cancelled to ask whether they
    # are still coming is the worst failure this endpoint could have, so the
    # status is checked here rather than trusting the GHL-side filter alone.
    # Absent status = allow (older workflows may not send it).
    status = (_first(payload, "appointment_status", "appointmentStatus", "status") or "").lower()
    if not status and appointment_id_hint:
        # The workflow does not always carry the status merge field, and the GHL
        # "Appointment Status" trigger fires on cancelled and no-show too. Ask
        # GHL rather than assume, so a workflow edit that drops the field cannot
        # quietly turn the guard off.
        status = await ghl.get_appointment_status(appointment_id_hint)
    if status and status not in LIVE_APPOINTMENT_STATUSES:
        # Rebooking is the one flow that SHOULD run on a dead appointment — it
        # exists to put the prospect back on the calendar.
        if call_type == "rebooking":
            log.info(
                "ghl/rebooking-call proceeding on appointment status=%r", status
            )
        else:
            log.info("ghl/outbound-call skipped: appointment status=%r", status)
            call_ledger.record(
                "skipped", call_type=call_type, reason=f"status_{status}",
                ghl_contact_id=_first(payload, "contact_id", "contactId"),
                ghl_appointment_id=appointment_id_hint,
            )
            return JSONResponse(
                status_code=200, content={"ok": True, "skipped": f"status_{status}"}
            )

    raw_phone = _first(payload, "phone", "phone_number", "contact_phone")
    number = normalize_phone(raw_phone)
    if not number:
        log.warning("ghl/outbound-call no usable phone; keys=%s", sorted(payload))
        return JSONResponse(status_code=422, content={"error": "no_phone"})

    appointment_id = _first(payload, "appointment_id", "appointmentId")
    contact_id = _first(payload, "contact_id", "contactId")
    first_name = _first(payload, "first_name", "firstName", "contact_first_name")

    # Anchor on the appointment when GHL sent one — a reschedule creates a NEW
    # appointment id, so a genuinely rebooked call is correctly allowed through
    # while a re-fire on the same booking is not.
    # Which kind of rebooking this is. Only meaningful for call_type=rebooking;
    # every other flow ignores it.
    reason_key = DEFAULT_REBOOK_REASON
    if call_type == "rebooking":
        raw_type = _first(payload, "type", "rebooking_type", "rebookingType", "reason")
        if raw_type:
            normalised = REBOOK_TYPE_ALIASES.get(raw_type.strip().lower().replace(" ", "-"))
            if not normalised:
                log.warning("ghl/rebooking-call unknown type=%r", raw_type)
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "unknown_rebooking_type",
                        "got": raw_type,
                        "known": sorted(set(REBOOK_TYPE_ALIASES)),
                    },
                )
            reason_key = normalised
    dedupe_key = f"{call_type}:{appointment_id or number}"
    if not _claim(dedupe_key):
        log.info("ghl/outbound-call deduped key=%s", dedupe_key)
        return JSONResponse(status_code=200, content={"ok": True, "deduped": True})

    # Resolve the booking BEFORE dialing and hand it over as variables.
    #
    # This used to be a mid-call tool the assistant fired as its first action —
    # a leftover from the GHL bridge, where the prospect's number arrived as
    # speech so nothing could be known until the call was live. Two live calls
    # (01a01c9f, 01a01cf9) showed the model simply not calling it: with
    # firstMessageMode=assistant-waits-for-user it gets one invocation after the
    # prospect says "hello" and reliably chooses to greet instead. It then
    # invented a booked time from a formatting example in its own prompt.
    #
    # We dial, so we already know who and when. Resolving here removes the tool
    # call from the critical path entirely — the assistant cannot skip a step
    # that no longer exists, and cannot invent a time it was handed.
    lookup = await _resolve_booking(number)

    # When the workflow sends the appointment it fired on, that appointment IS the
    # missed call. Read it directly: GHL drops a cancelled appointment from the
    # contact's list once it is deleted, so the list alone can name an older one.
    if call_type == "rebooking" and appointment_id:
        triggering = await ghl.get_appointment(appointment_id)
        triggering_start = ghl._event_start(triggering) if triggering else None
        if triggering_start:
            lookup = {**lookup, "missed_start_iso": triggering_start.isoformat()}

    # The day-of reminder says "later today". The lookup returns the SOONEST
    # upcoming appointment regardless of day, so a prospect whose only booking
    # is tomorrow would be told it is today. Drop the time rather than state a
    # wrong one — the prompt handles an empty bookedTime by omitting it.
    if call_type == "reminder" and lookup.get("start_iso"):
        if not _starts_today_for_prospect(lookup):
            log.info(
                "reminder: booking is not today (%s) — omitting the time",
                lookup.get("start_iso"),
            )
            lookup = {**lookup, "booked_time": ""}
    # What has the prospect been texting? Until 2026-09-07 Ashley knew nothing
    # about the thread she was calling into, so she could ring somebody who had
    # texted a minute earlier. Two short GHL reads, both degrading to nothing.
    thread_contact_id = lookup.get("contact_id") or contact_id
    recent_texts: list[dict] = []
    if thread_contact_id:
        # get_recent_texts swallows its own failures, but this sits on the dial
        # path and must not depend on that staying true. A thread we cannot read
        # is the state we were in before this existed: call, knowing nothing.
        try:
            recent_texts = await ghl.get_recent_texts(thread_contact_id)
        except Exception as e:  # noqa: BLE001 — never block the call
            log.warning("pre-dial text read failed for %s: %s", thread_contact_id, e)
            recent_texts = []

    # They told us to stop. Ashley writes `not-interested` mid-call via
    # mark_not_interested, and until 2026-09-10 nothing ever read it back — on
    # call 01a08929 the prospect said "can you please not call me back", she
    # tagged him correctly, and the system dialled him five more times over the
    # next half hour. The tag was write-only in both repos.
    #
    # Checked for EVERY call type, not just rebooking: someone who has opted out
    # should not get a confirmation or reminder call either.
    if thread_contact_id:
        try:
            if await ghl.has_opted_out(thread_contact_id):
                log.info(
                    "ghl/outbound-call skipped: opted out call_type=%s contact=%s",
                    call_type, thread_contact_id,
                )
                call_ledger.record(
                    "skipped", call_type=call_type, reason="opted_out",
                    **_ledger_target(lookup, contact_id, appointment_id),
                )
                return JSONResponse(
                    status_code=200,
                    content={"ok": True, "skipped": "opted_out"},
                )
        except Exception as e:  # noqa: BLE001 — a GHL blip must not stop all calls
            log.warning("opt-out check failed for %s: %s", thread_contact_id, e)

    # GHL Do Not Disturb. Vapi places the call, not GHL, so GHL's own DND
    # suppression never applied and nothing here read it until 2026-09-13.
    # Checked for every call type. Fails open, like the opt-out check above.
    if thread_contact_id:
        try:
            if await ghl.has_call_dnd(thread_contact_id):
                log.info(
                    "ghl/outbound-call skipped: DND call_type=%s contact=%s",
                    call_type, thread_contact_id,
                )
                call_ledger.record(
                    "skipped", call_type=call_type, reason="dnd",
                    **_ledger_target(lookup, contact_id, appointment_id),
                )
                return JSONResponse(
                    status_code=200,
                    content={"ok": True, "skipped": "dnd"},
                )
        except Exception as e:  # noqa: BLE001 — a GHL blip must not stop all calls
            log.warning("DND check failed for %s: %s", thread_contact_id, e)

    # They sent the last message, so a human owes them a reply and nothing
    # automated goes out until we have sent one. Same rule the drip already
    # applies to texts (weOweReply); owner extended it to calls 2026-09-08.
    if should_hold_for_reply(recent_texts):
        log.info(
            "ghl/outbound-call paused: prospect replied last call_type=%s contact=%s",
            call_type, thread_contact_id,
        )
        call_ledger.record(
            "skipped", call_type=call_type, reason="awaiting_our_reply",
            **_ledger_target(lookup, contact_id, appointment_id),
        )
        return JSONResponse(
            status_code=200,
            content={"ok": True, "skipped": "awaiting_our_reply"},
        )

    preload_offered: set = set()
    variable_values = {
        "firstName": lookup.get("first_name") or first_name or "there",
        "bookedTime": lookup.get("booked_time", ""),
        "greeting": lookup.get("greeting", ""),
    }
    # The reminder's opening line is delivered by Vapi as firstMessage, before the
    # model runs, so no prompt gate can catch an empty time there. Two live calls
    # said "the call at later today" (01a0b597, 01a0b4d8) from exactly that hole.
    # Pre-write the whole phrase instead — a line the webhook writes is spoken as
    # written, a line she assembles is not.
    if call_type == "reminder":
        _booked_time = lookup.get("booked_time", "")
        variable_values["reminderWhen"] = (
            f"later today at {_booked_time}" if _booked_time else "later today"
        )
    # Sanitised in text_digest before it gets here: the bodies are written by the
    # prospect and land in the same context window as Ashley's instructions.
    # Always set, even when empty — an unrendered {{recentTexts}} would reach the
    # model as literal braces and read as a broken instruction.
    variable_values["recentTexts"] = build_digest(recent_texts)

    # Only the rebooking prompt reads this. Never blank: an empty {{callReason}}
    # would leave that prompt saying "You are calling this prospect because ."
    if call_type == "rebooking":
        variable_values["callReason"] = (
            REBOOK_REASONS.get(reason_key) or REBOOK_REASONS[DEFAULT_REBOOK_REASON]
        )
        # Always set for rebooking, even blank, so {{missedDay}} never reaches the
        # model as literal braces.
        variable_values["missedDay"] = _missed_day_phrase(lookup)
        variable_values["whyCalling"] = _why_calling_line(variable_values["missedDay"])
        # Preloaded before dialling (owner, 2026-09-14). Always set, blank included.
        (preload_text, preload_offered, first_offer, day_offers,
         more_times) = await _preload_rebooking_slots(lookup)
        variable_values["availableSlots"] = preload_text
        # The first offer line, day named once (call 01a0a678 said the day twice).
        variable_values["firstOffer"] = first_offer
        # One offer line per day, for when they ask for a day (calls 01a0a796, 01a0a7e0).
        variable_values["dayOffers"] = day_offers
        # More times on a day, by half of the day, with the day left out (call 01a0ab63).
        variable_values["moreTimes"] = more_times
        # The prospect's own clock (owner, 2026-09-15): Vapi's currentDateTime is UTC.
        variable_values["currentDay"] = _current_day_line(resolve_timezone(
            lookup.get("timezone", "") or "",
            lookup.get("city", "") or "",
            lookup.get("state", "") or "",
        ))
    # Identity rides along on the metadata. We already paid for this lookup
    # above; without it a short call that fires no tool reaches the operator
    # chat as "(cold caller)" and a bare phone number, because nothing else in
    # the outbound chain ever learns who we dialled.
    metadata = {
        "source": "ghl-outbound-call",
        "call_type": call_type,
        "ghl_contact_id": lookup.get("contact_id") or contact_id,
        "ghl_appointment_id": lookup.get("appointment_id") or appointment_id,
        "ghl_first_name": lookup.get("first_name") or first_name or "",
        "ghl_business": lookup.get("business") or "",
        "ghl_city": lookup.get("city") or "",
        "ghl_state": lookup.get("state") or "",
        "ghl_timezone": lookup.get("timezone") or "",
    }

    # Clear any outcome tag from a previous attempt before dialing. Otherwise a
    # 2pm retry still carries this morning's `call-no-answer`, and the GHL
    # workflow reads a stale result as if it were this call's.
    if lookup.get("contact_id"):
        for stale in OUTCOME_TAGS:
            try:
                await ghl.remove_tag(lookup["contact_id"], "main", stale)
            except Exception:  # noqa: BLE001 — best effort; never block the call
                pass

    try:
        call = await create_outbound_call(
            assistant_id=assistant_id,
            customer_number=number,
            variable_values=variable_values,
            metadata=metadata,
        )
    except Exception as e:  # noqa: BLE001 — every failure returns the same 200
        # Release the dedupe claim ONLY when we know no call was created: Vapi
        # answered and refused (VapiError), or we never got as far as sending
        # (RuntimeError = missing config). A timeout or connection drop is
        # ambiguous — the call may well be ringing — so the claim stands and a
        # retry is suppressed. Not dialing beats dialing twice.
        definitively_no_call = isinstance(e, (VapiError, RuntimeError))
        if definitively_no_call:
            _recent.pop(dedupe_key, None)
        # 200 on purpose: a 5xx makes GHL retry, and a retry after a call that
        # actually went out is a second call to a real person.
        log.error(
            "ghl/outbound-call FAILED call_type=%s number=%s claim_released=%s err=%s",
            call_type, number, definitively_no_call, e,
        )
        call_ledger.record(
            "failed", call_type=call_type, reason=type(e).__name__,
            **_ledger_target(lookup, contact_id, appointment_id),
        )
        return JSONResponse(status_code=200, content={"ok": False, "error": "call_failed"})

    call_id = call.get("id")
    if call_type == "rebooking" and call_id:
        await _seed_rebooking_call(call_id, lookup, contact_id or "", number, preload_offered)
    call_ledger.record(
        "placed", call_type=call_type, vapi_call_id=call_id,
        **_ledger_target(lookup, contact_id, appointment_id),
    )
    log.info(
        "ghl/outbound-call placed call_type=%s reason=%s call_id=%s appointment=%s",
        call_type, reason_key if call_type == "rebooking" else "-",
        call_id, appointment_id,
    )
    return JSONResponse(status_code=200, content={"ok": True, "callId": call_id})
