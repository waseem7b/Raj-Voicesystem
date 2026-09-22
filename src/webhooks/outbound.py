"""Outbound demo-confirmation (Type 4) webhook — SEPARATE from the inbound path.

GHL originates the call, bridges it into a Vapi entry number, and GHL's bot
speaks a bot-ID + the prospect's phone number. Stage 0 routes to the
confirmation flow; Stage 1's first action looks the booking up by that spoken
number (`get_prospect_appointment`), and Stage 2 reschedules if needed.

Isolation: these routes (`/vapi/outbound/tool`, `/vapi/outbound/events`) and the
Vapi tool resources that point at them are DISTINCT from the inbound `/vapi/tool`
path, so a bug here can't reach inbound. Low-level helpers are imported from
`server` (which mounts this router at its very bottom, after every helper is
defined — so there is no import cycle as long as `server` is imported first,
which it always is: uvicorn targets `server:app` and the tests import it first).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from src.agent import ghl

from .tz_resolver import resolve_timezone, resolve_timezone_with_source
from .server import (
    log,
    _verify_signature,
    _extract_call_id,
    _extract_phone,
    _parse_args,
    _arg_str,
    _cache_get,
    _slot_iso_matches_offered,
    _slot_is_in_past,
    _handle_get_available_slots,
    _handle_end_call,
    _ensure_contact_loaded,
    _resolve_business_label,
    _resolve_transcript_label,
)
from .call_archive import (
    archive_call,
    already_pushed,
    mark_pushed,
    already_commented,
    mark_commented,
    public_recording_url,
    build_transcript,
    transcript_filename,
    title_slash,
    title_dash,
)
from .call_summary import format_ended_summary, duration_label
from . import call_ledger
from .telegram_notify import send_message as tg_send_message
from .telegram_notify import send_audio as tg_send_audio
from .telegram_notify import send_document as tg_send_document

router = APIRouter()


# --------------------------------------------------------------------------- #
# Spoken-number normalization
# --------------------------------------------------------------------------- #
def _normalize_spoken_phone(raw: Any) -> Optional[str]:
    """Normalize a spoken/typed phone number to E.164 (US default).

    GHL speaks the prospect's number on the bridged leg, so it arrives as a
    tool argument that may carry separators / a country code / a leading '+'.
    Returns None when fewer than 10 digits are present (caller treats that as a
    bad_number and falls back gracefully).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    has_plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if has_plus:
        return "+" + digits if len(digits) >= 10 else None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) >= 10:
        return "+" + digits
    return None


# --------------------------------------------------------------------------- #
# Spoken-time normalization — hand the model the booked time already in word
# form ("nine thirty", "two in the afternoon") so it never has to read a raw ISO
# (which it renders as "oh nine thirty"). Mirrors the persona's time table.
# --------------------------------------------------------------------------- #
_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    base = _TENS[tens * 10]
    return base if ones == 0 else f"{base}-{_ONES[ones]}"


def _prospect_tz(out: dict) -> str:
    """IANA timezone for the prospect. Always returns something usable."""
    return resolve_timezone(
        out.get("timezone", "") or "", out.get("city", "") or "", out.get("state", "") or ""
    )


def _prospect_tz_if_known(out: dict) -> str:
    """The prospect's timezone ONLY when their record actually says where they
    are — "" when the resolver had to fall back to its hard default.

    Used for the SPOKEN booked time, where a guess is worse than no conversion.
    A contact with no timezone, city or state resolves to America/New_York, and
    converting the account clock into it silently shifts every stated time by an
    hour: a 6:30pm booking was announced as "seven thirty" (live call 01a0309e).
    With no location on file the honest answer is the time as booked — which is
    also the time on the calendar the rep is looking at.

    The greeting still uses the fallback via `_prospect_tz`: morning/afternoon
    only has to be roughly right, and being an hour out rarely changes the word.
    """
    tz_name, source = resolve_timezone_with_source(
        out.get("timezone", "") or "", out.get("city", "") or "", out.get("state", "") or ""
    )
    return "" if source == "default" else tz_name


def _local_greeting(out: dict) -> str:
    """"morning" / "afternoon" / "evening" where the PROSPECT is, right now.

    Vapi's `{{now}}` is UTC, which broke this twice on the first live call: the
    model said "good afternoon" at 8:43pm the prospect's time, and — worse —
    between 00:00 and 05:00 UTC the UTC date is already tomorrow in North
    America, so a day-before call would say "tomorrow" about the wrong day.
    Resolving from the contact's own timezone removes both.
    """
    tz_name = _prospect_tz(out)
    try:
        hour = datetime.now(ZoneInfo(tz_name)).hour
    except Exception:  # noqa: BLE001 — an unusable tz must not break the lookup
        return ""
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


def _spoken_booked_time(iso: str, tz_name: str = "") -> str:
    """ISO start time -> spoken clock time IN THE PROSPECT'S TIMEZONE.

    GHL returns naive times in the ACCOUNT timezone (Bogota, UTC-5), so reading
    the hour straight off the string tells a prospect the time on the agency's
    clock, not their own — an appointment at 21:00 Bogota is 22:00 for a New
    York prospect and 19:00 in California. Live-observed 2026-08-20 (call
    01a02200): a New York prospect was told "nine at night" about a booking
    their own calendar shows at ten.

    Naive input is interpreted in the account tz, then converted to `tz_name`
    before the hour is read. Without a tz_name the account tz is used, which is
    the old behaviour and correct only for Bogota-local prospects.
    """
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ghl.GHL_ACCOUNT_TZ)
    if tz_name:
        try:
            dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:  # noqa: BLE001 — unusable tz: fall back to as-parsed
            pass
    hour_word = _ONES[dt.hour % 12 or 12]
    if dt.minute == 0:
        if dt.hour < 12:
            part = "in the morning"
        elif dt.hour < 17:
            part = "in the afternoon"
        elif dt.hour < 21:
            part = "in the evening"
        else:
            part = "at night"
        return f"{hour_word} {part}"
    if dt.minute < 10:
        return f"{hour_word} oh {_ONES[dt.minute]}"
    return f"{hour_word} {_two_digit_words(dt.minute)}"


# --------------------------------------------------------------------------- #
# Tool handlers — each returns a STRUCTURED, factual result string. The spoken
# phrasing (what Jon says) lives in the prompt, never here.
# --------------------------------------------------------------------------- #
async def _handle_get_prospect_appointment(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Stage 1 first action: look the prospect's upcoming demo up, seed the call
    cache (so reschedule + get_available_slots reuse the Main-CRM contact without
    a texts lookup), and return structured facts.

    `phone_number` is OPTIONAL. Under the old GHL bridge the number arrived as
    speech and had to be passed in; now that we originate the call ourselves it
    is simply the call's own customer number, which `phone` already carries. The
    model has no reliable way to know the number, so requiring the argument made
    it skip the tool entirely and invent a booked time (live-observed
    2026-08-19, call 01a01c9f). Argument first, call's number as the fallback.
    """
    raw = args.get("phone_number") or args.get("number") or args.get("phone") or phone
    number = _normalize_spoken_phone(raw)
    entry = await _cache_get(call_id)
    entry["confirm_flow"] = True

    if not number:
        entry["confirm_outcome"] = "bad_number"
        log.warning("get_prospect_appointment unparseable number call_id=%s raw=%r", call_id, raw)
        return "appointment_found=false; reason=bad_number"

    try:
        out = await ghl.get_prospect_appointment(number)
    except Exception as e:
        log.error("get_prospect_appointment error call_id=%s err=%s", call_id, e)
        return "appointment_found=false; reason=lookup_error"

    if not out.get("ok"):
        reason = out.get("reason") or "unknown"
        # No phone match at all -> treat as a bad/mis-transcribed number.
        entry["confirm_outcome"] = "bad_number" if reason == "no_contact" else reason
        log.info("get_prospect_appointment miss call_id=%s number=%s reason=%s",
                 call_id, number, reason)

        # `no_appointment` means we DO know who this is, they just have nothing
        # booked. Seed the contact so the rebooking branch can offer real slots
        # and create an appointment for them. `no_contact` deliberately seeds
        # nothing — booking for someone we cannot identify would put a stranger
        # on Jon's calendar.
        if reason == "no_appointment" and out.get("contact_id"):
            entry["contact"] = {
                "id": out.get("contact_id"),
                "crm": "main",
                "timezone": out.get("timezone", ""),
                "city": out.get("city", ""),
                "state": out.get("state", ""),
                "first_name": out.get("first_name", ""),
                "business": out.get("business", ""),
            }
            entry["lookup_done"] = True
            return (
                "appointment_found=false; reason=no_appointment; "
                f"first_name={out.get('first_name','')}; "
                f"greeting={_local_greeting(out)}"
            )

        return f"appointment_found=false; reason={reason}"

    # Seed the cache: subsequent get_available_slots short-circuits the texts
    # lookup (lookup_done=True) and uses this Main-CRM contact's timezone.
    entry["contact"] = {
        "id": out.get("contact_id"),
        "crm": "main",
        "timezone": out.get("timezone", ""),
        "city": out.get("city", ""),
        "state": out.get("state", ""),
        "first_name": out.get("first_name", ""),
        "business": out.get("business", ""),
    }
    entry["lookup_done"] = True
    entry["appointment"] = {
        "appointment_id": out.get("appointment_id", ""),
        "start_iso": out.get("start_iso", ""),
    }
    entry.setdefault(
        "captured",
        {"first_name": out.get("first_name", ""), "last_name": "",
         "business": out.get("business", ""), "email": ""},
    )

    return (
        "appointment_found=true; "
        f"first_name={out.get('first_name','')}; "
        f"booked_time={_spoken_booked_time(out.get('start_iso',''), _prospect_tz_if_known(out))}; "
        f"start_iso={out.get('start_iso','')}; "
        f"greeting={_local_greeting(out)}; "
        f"status={out.get('status','')}"
    )


async def _handle_reschedule_video_call(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Move the existing appointment to a new offered slot (rebook path)."""
    entry = await _cache_get(call_id)
    appt = entry.get("appointment") or {}
    appointment_id = _arg_str(args.get("appointment_id")) or appt.get("appointment_id")

    slot_iso = _arg_str(args.get("slot_iso"))
    if not slot_iso:
        return "reschedule_failed; reason=missing_slot"

    # Same anti-hallucination guard as booking: slot must be one we offered,
    # else (degraded, no offered set) at least not in the past.
    offered = entry.get("offered_slots") or set()
    if offered:
        if not _slot_iso_matches_offered(slot_iso, offered):
            return ("That time wasn't in the list I offered — call "
                    "get_available_slots to see current open times, then pick one.")
    elif _slot_is_in_past(slot_iso):
        return ("That time has already passed — call get_available_slots to see "
                "current open times, then pick one.")

    # Move an existing booking, or create one when they had none. The model is
    # never asked to choose between two tools — it just locks in the agreed
    # time, and which GHL call that means is decided here.
    contact_id = (entry.get("contact") or {}).get("id")
    # Filtering the offered slots is only half of it: the calendar is
    # round-robin, so without an explicit assignee GHL can still hand a
    # Jon-only slot to Waseem, and a move keeps whoever it was assigned to.
    assignee = ""
    if (entry.get("call_type") or "").strip().lower() == "rebooking":
        assignee = ghl.REBOOKING_ASSIGNED_USER_ID
    log.info(
        "reschedule: call_id=%s path=%s appointment_id=%s contact_id=%s slot=%s "
        "lookup_calendar=%s write_calendar=%s assignee=%s",
        call_id,
        "move" if appointment_id else ("create" if contact_id else "abort"),
        appointment_id or "-", contact_id or "-", slot_iso,
        ghl.CONFIRM_CALENDAR_ID, ghl.VIDEO_CALL_CALENDAR_ID,
        assignee or "(round-robin)",
    )
    try:
        if appointment_id:
            res = await ghl.reschedule_appointment(
                appointment_id, slot_iso, assigned_user_id=assignee
            )
            action = "rescheduled"
        elif contact_id:
            res = await ghl.book_video_call(
                slot_iso, contact_id, assigned_user_id=assignee
            )
            action = "booked"
        else:
            # No appointment AND no known contact = we cannot identify who this
            # is, so putting them on the calendar would be a guess.
            return "reschedule_failed; reason=no_contact_on_file"
    except Exception as e:
        log.error("reschedule_video_call error call_id=%s err=%s", call_id, e)
        return "reschedule_failed; reason=error"

    if not res.get("ok"):
        log.error("reschedule: call_id=%s action=%s GHL rejected: %s",
                  call_id, action, str(res.get("error"))[:300])
        return f"reschedule_failed; reason={str(res.get('error', 'unknown'))[:80]}"

    log.info("reschedule: call_id=%s action=%s OK appointment_id=%s start=%s calendar=%s",
             call_id, action, res.get("appointment_id") or appointment_id or "-",
             slot_iso, ghl.VIDEO_CALL_CALENDAR_ID)

    entry["confirm_outcome"] = action
    entry.setdefault("appointment", {})["start_iso"] = slot_iso
    if action == "booked" and res.get("appointment_id"):
        entry["appointment"]["appointment_id"] = res["appointment_id"]
    return f"{action}=true; new_start_iso={slot_iso}"


async def _seed_contact_from_metadata(entry: dict, message: dict) -> None:
    """Populate the call cache from the metadata we set when placing the call.

    Since the pre-call lookup moved into ghl_trigger, no in-call tool seeds the
    cache any more — so mark_locked_in and the rebooking tools would find an
    empty contact and fail with `no_contact_on_file` (live-observed on calls
    01a01c9f and 01a01cf9). The contact id is already on the call's metadata;
    this lifts it across. No-op when the cache is already populated.
    """
    if (entry.get("contact") or {}).get("id"):
        return
    meta = ((message.get("call") or {}).get("metadata")) or {}
    contact_id = meta.get("ghl_contact_id")
    if not contact_id:
        return
    # Carry the name across too, not just the id. The operator summary builds
    # its "Caller:" line from this contact and the archive folder name from
    # _resolve_business_label; with an id alone every outbound call reaches
    # Telegram as a bare phone number.
    entry["contact"] = {
        "id": contact_id,
        "crm": "main",
        "first_name": meta.get("ghl_first_name") or "",
        "business": meta.get("ghl_business") or "",
        "city": meta.get("ghl_city") or "",
        "state": meta.get("ghl_state") or "",
        "timezone": meta.get("ghl_timezone") or "",
    }
    # The metadata names the MAIN-CRM contact we dialled, so the lookup is done.
    # Without this, get_available_slots ran the texts-CRM phone lookup, found the
    # same number in Texts CRM #1 and replaced this contact with that one — the
    # booking then sent a texts-CRM id to the main calendar and GHL answered
    # HTTP 422 "The contact id is invalid." (call 01a0a1fd, 2026-09-14).
    entry["lookup_done"] = True
    entry["confirm_flow"] = True
    if meta.get("call_type"):
        entry["call_type"] = meta["call_type"]
    appointment_id = meta.get("ghl_appointment_id")
    if appointment_id:
        entry.setdefault("appointment", {})["appointment_id"] = appointment_id


async def _handle_mark_locked_in(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Tag the prospect's contact `locked-in` once they confirm the appointment.
    Contact id comes from the call metadata (or a lookup, if one ran)."""
    entry = await _cache_get(call_id)
    contact_id = (entry.get("contact") or {}).get("id")
    if not contact_id:
        return "locked_in=false; reason=no_contact_on_file"

    # TWO tags, deliberately.
    #
    # `locked-in` is the one the CRM reads: applyLockInFromTags normalises tags
    # by stripping non-alphanumerics and matching "lockedin" exactly, then
    # stamps calendar_events.locked_in_at — which is what show-rate reporting
    # runs on. A stage-suffixed tag alone would normalise to "lockedinconfirmed"
    # and silently never match.
    #
    # The stage tag is for the GHL show-up workflow, which needs to tell a
    # day-before confirmation from a same-day re-confirmation.
    stage_tag = {
        "confirmation": "locked-in-confirmed",
        "reminder": "locked-in-reminder",
    }.get(entry.get("call_type") or "")
    try:
        ok = await ghl.add_tag(contact_id, "main", "locked-in")
        if stage_tag:
            await ghl.add_tag(contact_id, "main", stage_tag)
    except Exception as e:
        log.error("mark_locked_in error call_id=%s err=%s", call_id, e)
        return "locked_in=false; reason=error"
    if ok:
        entry["confirm_outcome"] = "confirmed"
        log.info("mark_locked_in: %s tagged locked-in + %s", contact_id, stage_tag or "(no stage)")
        return "locked_in=true"
    return "locked_in=false; reason=tag_failed"


# --------------------------------------------------------------------------- #
# Dispatch + routes
# --------------------------------------------------------------------------- #
async def _handle_mark_not_interested(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Tag the prospect `not-interested` when they say they no longer want the
    demo. Mirrors mark_locked_in: Ashley writes it during the call, so the GHL
    workflow only has to branch on it."""
    entry = await _cache_get(call_id)
    contact_id = (entry.get("contact") or {}).get("id")
    if not contact_id:
        return "not_interested=false; reason=no_contact_on_file"
    try:
        ok = await ghl.add_tag(contact_id, "main", "not-interested")
    except Exception as e:  # noqa: BLE001
        log.error("mark_not_interested error call_id=%s err=%s", call_id, e)
        return "not_interested=false; reason=error"
    if ok:
        entry["confirm_outcome"] = "not_interested"
        return "not_interested=true"
    return "not_interested=false; reason=tag_failed"


async def _handle_mark_needs_reschedule(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Tag the prospect `needs-reschedule` when they can't make their time.

    Rescheduling is its own assistant (owner ruling 2026-09-04): the
    confirmation and reminder calls only hand off. This tag is what the GHL
    workflow branches on to fire /ghl/outbound-call with call_type=rebooking.
    Written in-call, like locked-in, so a 10-minute workflow wait reads it.
    """
    entry = await _cache_get(call_id)
    contact_id = (entry.get("contact") or {}).get("id")
    if not contact_id:
        return "needs_reschedule=false; reason=no_contact_on_file"
    try:
        ok = await ghl.add_tag(contact_id, "main", NEEDS_RESCHEDULE_TAG)
    except Exception as e:  # noqa: BLE001
        log.error("mark_needs_reschedule error call_id=%s err=%s", call_id, e)
        return "needs_reschedule=false; reason=error"
    if ok:
        entry["confirm_outcome"] = "reschedule_requested"
        log.info("mark_needs_reschedule: %s tagged %s", contact_id, NEEDS_RESCHEDULE_TAG)
        return "needs_reschedule=true"
    return "needs_reschedule=false; reason=tag_failed"


async def _dispatch_outbound_tool(tool_call: dict, call_id: str, phone: Optional[str]) -> dict:
    tool_call_id = tool_call.get("id") or "unknown"
    function = tool_call.get("function") or {}
    name = function.get("name") or "unknown"
    args = _parse_args(function.get("arguments"))
    log.info("TOOL -> call_id=%s tool=%s args=%s", call_id, name, args)
    try:
        if name == "get_prospect_appointment":
            result = await _handle_get_prospect_appointment(args, call_id, phone)
        elif name == "get_available_slots":
            # Reused from the inbound handler; uses the Main-CRM contact/timezone
            # seeded by get_prospect_appointment, so no texts lookup happens.
            result = await _handle_get_available_slots(args, call_id, phone)
        elif name == "reschedule_video_call":
            result = await _handle_reschedule_video_call(args, call_id, phone)
        elif name == "mark_locked_in":
            result = await _handle_mark_locked_in(args, call_id, phone)
        elif name == "mark_not_interested":
            result = await _handle_mark_not_interested(args, call_id, phone)
        elif name == "mark_needs_reschedule":
            result = await _handle_mark_needs_reschedule(args, call_id, phone)
        elif name == "end_call":
            result = await _handle_end_call(args, call_id)
        else:
            log.warning("unknown outbound tool call_id=%s tool=%s", call_id, name)
            result = f"Unknown tool: {name}"
    except Exception as e:
        log.exception("outbound tool crash call_id=%s tool=%s", call_id, name)
        result = f"Internal error in {name}: {str(e)[:160]}"
    log.info("TOOL <- call_id=%s tool=%s result=%s", call_id, name, str(result)[:400])
    return {"toolCallId": tool_call_id, "result": result}


@router.post("/vapi/outbound/tool")
async def outbound_tool(
    request: Request,
    x_vapi_signature: Optional[str] = Header(default=None),
    x_vapi_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Tool endpoint for the outbound demo-confirmation squad ONLY."""
    raw = await request.body()
    if not _verify_signature(raw, x_vapi_signature or x_vapi_secret):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        return JSONResponse(status_code=200, content={"results": []})

    message = body.get("message") or {}
    msg_type = message.get("type")
    if msg_type and msg_type != "tool-calls":
        return JSONResponse(content={"results": []})

    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []
    call_id = _extract_call_id(message)
    phone = _extract_phone(message)
    if not tool_calls:
        return JSONResponse(content={"results": []})

    # Lift contact/appointment off the call metadata before dispatching, so the
    # tools have someone to act on without an in-call lookup having run.
    await _seed_contact_from_metadata(await _cache_get(call_id), message)

    # Sequential: the confirmation flow never batches slots+book the way inbound
    # does, and order preservation keeps get_prospect_appointment before others.
    results = [await _dispatch_outbound_tool(tc, call_id, phone) for tc in tool_calls]
    return JSONResponse(content={"results": results})


# Vapi endedReason -> outcome, for the reasons that mean nobody was reached.
# Anything not listed here means a human was on the line.
_NO_ANSWER_REASONS = {
    "customer-did-not-answer", "no-answer", "customer-busy", "twilio-failed-to-connect-call",
    "call.start.error-vapi-number-outbound-daily-limit",
    "call.start.error-vapi-number-international",
    "call.in-progress.error-providerfault-transport-never-connected",
}
_VOICEMAIL_REASONS = {"voicemail", "customer-voicemail", "assistant-forwarded-call-voicemail"}

# In-call outcome (set by the tool handlers) -> the tag GHL branches on.
# `confirmed` is absent on purpose: mark_locked_in already writes `locked-in`
# during the call, and writing it twice would be noise.
_TAG_BY_OUTCOME = {
    "rescheduled": "call-rescheduled",
    "booked": "call-rescheduled",
    # Every number in this flow comes from a main-CRM appointment, so a
    # no-contact lookup is a data problem, not a call outcome (owner ruling
    # 2026-08-21). Tagging it as no-answer keeps the GHL workflow's branches
    # exhaustive; the distinct reason is preserved in the log for diagnosis.
    "bad_number": "call-no-answer",
}

# Written in-call by mark_needs_reschedule; the GHL workflow branches on it to
# fire the rebooking call-back. Cleared before every dial like the outcome
# tags, so the rebooking call consumes it and a later attempt starts clean.
NEEDS_RESCHEDULE_TAG = "needs-reschedule"

OUTCOME_TAGS = {
    "call-answered", "call-voicemail", "call-no-answer", "call-rescheduled",
    NEEDS_RESCHEDULE_TAG,
}


# Phrases that only ever come out of an answering machine. Deepgram transcribes
# a voicemail greeting as role=user, so without this an outgoing message counts
# as the prospect speaking — and a missed call gets tagged `call-answered`,
# which skips the retry, the text and the voice note. Live-observed on calls
# 01a03094 and 01a03248, both endedReason=customer-did-not-answer.
_MACHINE_PHRASES = (
    "leave your name", "leave a message", "leave your message", "after the tone",
    "at the tone", "record your message", "voice messaging system", "voicemail",
    "is not available", "can't come to the phone", "cant come to the phone",
    "can't make it to the phone", "cant make it to the phone",
    "has been forwarded", "please record", "press 5", "press one",
    "get back to you as soon as",
    # Observed on calls 01a06797 and 01a067cb (Cordero, 2026-09-03), both
    # endedReason=customer-did-not-answer. Deepgram mangles the greeting —
    # "record your message" came through as "Cord your message" — so match the
    # tail of the sentence too, which survives transcription more reliably.
    "you may hang up", "finished recording", "when you have finished",
    "your message", "at the sound of",
    # The carrier "person not available" system, 2026-09-07. This one never says
    # the word voicemail: it opens "Thanks. Please stay on the line." (which
    # matched nothing here, so it read as the prospect talking) and only then
    # plays "I'm sorry. This person is not available. If you would like to leave
    # an additional message, please reply after the tone." Seven live calls went
    # out tagged call-answered on the strength of that opener alone —
    # 01a07d08, 01a0781c, 01a06e6f, 01a06cbf, 01a068a9, 01a06797, 01a058e6.
    "stay on the line", "person is not available", "additional message",
    "reply after the tone",
    # The "nothing has been recorded / for delivery options, press the pound
    # sign" system — 01a06cbf, 01a0697e, 01a0686b, 01a06dcb.
    "nothing has been recorded", "delivery options", "press the pound",
    "pound sign",
    # "...can't take your call now. At the tone..." — 01a07db3, 01a07ca0.
    # "To leave a callback number, press 5." — 01a07924.
    "take your call", "leave a callback number",
    # Canonical greeting language, not yet seen in this window. Listed because
    # missing a machine costs a whole retry chain; matching one costs one call.
    "at the beep", "after the beep",
)


# How much real speech it takes to conclude a human picked up on a call where a
# machine was also heard. Fragments below this are the greeting, not the prospect.
# Four, not higher: "Sorry, hello? This is Ryan." is five words and has to keep
# counting as a pickup (test_a_real_conversation_after_a_machine_line_still_counts).
# Every fragment that caused the misreads was one or two words, so four clears
# them with margin. 4 and 6 give identical tags across the 180-call sample.
_HUMAN_WORDS_OVER_MACHINE = 4


def _sounds_like_a_machine(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _MACHINE_PHRASES)


def _prospect_spoke(messages: Optional[list]) -> bool:
    """True if the prospect said anything at all during the call.

    This is the load-bearing signal for answered-vs-missed, in preference to
    string-matching Vapi's endedReason. Every reason string is a guess until it
    turns up in a real call, and an unrecognised one would fall through to
    "answered" — silently killing the retry, the text and the voice note in the
    GHL show-up workflow, with nothing in the log to say why.

    Somebody speaking is direct evidence a human picked up. No user turns means
    nobody was reached, whatever the reason string happens to say.

    Machine evidence anywhere in the call outweighs a stray fragment. The old
    rule was "first user turn that is not a known machine phrase wins", and a
    greeting only has to produce ONE innocuous turn to defeat it: "Thanks."
    "Available." "But a." Seven calls in the 2026-09-07 window were tagged
    call-answered off exactly that, killing the retry, the text and the voice
    note. So once a machine has been heard, the prospect has to actually say
    something — a real sentence, not a fragment — to take the call back.
    """
    machine_heard = False
    human_words = 0
    for m in messages or []:
        if m.get("role") != "user":
            continue
        text = str(m.get("message") or m.get("content") or "").strip()
        if not text:
            continue
        if _sounds_like_a_machine(text):
            machine_heard = True
            continue  # an answering machine talking is not the prospect answering
        human_words += len(text.split())
    if machine_heard:
        # A human CAN pick up over their own greeting, so this stays possible —
        # it just takes a sentence rather than a fragment.
        return human_words >= _HUMAN_WORDS_OVER_MACHINE
    return human_words > 0


def _classify_outcome(
    ended_reason: str, confirm_outcome: str, messages: Optional[list] = None
) -> Optional[str]:
    """Pick the single tag describing how this call went.

    Order of authority:
      1. What she DID in the call (tools fired) — definitive.
      2. Voicemail detection — a machine answered, which is neither.
      3. Whether the prospect spoke — the answered/missed signal.
      4. The ended reason — only as a fallback when there is no transcript.
    """
    if confirm_outcome == "confirmed":
        return None  # mark_locked_in already tagged them `locked-in`
    if confirm_outcome == "not_interested":
        return None  # mark_not_interested already tagged them `not-interested`
    if confirm_outcome == "reschedule_requested":
        return None  # mark_needs_reschedule already tagged them `needs-reschedule`
    if confirm_outcome in _TAG_BY_OUTCOME:
        return _TAG_BY_OUTCOME[confirm_outcome]

    reason = (ended_reason or "").lower()
    if any(r in reason for r in _VOICEMAIL_REASONS):
        return "call-voicemail"

    # A RECOGNISED no-answer reason is a carrier-level fact: the line was never
    # picked up by a person. Any transcript on such a call is a machine talking,
    # so it must not promote the call to "answered".
    #
    # This is the Cordero incident (2026-09-03): two voicemails came back
    # endedReason=customer-did-not-answer, but Deepgram rendered the greeting as
    # role=user text the phrase list did not match ("Cord your message...",
    # "Recording, you may hang up."), so _prospect_spoke said a human had
    # answered and both were tagged call-answered.
    #
    # Only KNOWN reasons get this authority. An unrecognised reason still falls
    # through to the transcript, which is the case _prospect_spoke was written
    # for — an unknown string must never silently mean "answered".
    if reason in _NO_ANSWER_REASONS:
        if messages:
            for m in messages:
                if m.get("role") == "user" and str(
                    m.get("message") or m.get("content") or ""
                ).strip():
                    return "call-voicemail"
        return "call-no-answer"

    if messages is not None:
        if _prospect_spoke(messages):
            return "call-answered"
        # Nobody real spoke. If a machine did, that is voicemail; otherwise the
        # line was never picked up at all.
        for m in messages:
            if m.get("role") == "user" and _sounds_like_a_machine(
                str(m.get("message") or m.get("content") or "")
            ):
                return "call-voicemail"
        return "call-no-answer"

    # No transcript to judge by — fall back to the reason string, and default to
    # no-answer rather than answered. A missed call wrongly marked answered stops
    # the retry chain dead; a connected call wrongly marked missed costs one
    # extra call. The cheaper mistake wins.
    if reason in _NO_ANSWER_REASONS or "did-not-answer" in reason or "no-answer" in reason:
        return "call-no-answer"
    if reason.startswith("call.start.error") or reason.startswith("pipeline-error"):
        return "call-no-answer"
    if reason in {"assistant-ended-call", "customer-ended-call",
                  "assistant-said-end-call-phrase", "assistant-forwarded-call"}:
        return "call-answered"
    log.warning("unrecognised endedReason %r and no transcript — assuming no answer", ended_reason)
    return "call-no-answer"


async def _tag_call_outcome(body: dict) -> tuple[dict, Optional[str]]:
    """Write the outcome of a finished call back to GHL as a contact tag.

    This is what makes the "if no answer" branches in the GHL show-up workflow
    possible: without it GHL has no idea whether anyone picked up, because our
    trigger returns 200 the moment the call is handed to Vapi.

    Never raises — a tagging failure must not make Vapi retry the report.
    """
    message = body.get("message") or {}
    call = message.get("call") or {}
    meta = call.get("metadata") or {}
    contact_id = meta.get("ghl_contact_id")
    call_id = call.get("id") or _extract_call_id(message)

    entry = await _cache_get(call_id) if call_id else {}
    # Seed here as well as in the tool handler: a call where the model fires no
    # tool at all never reaches that path, and this entry is what the operator
    # summary and the archive folder name are built from.
    if call_id:
        await _seed_contact_from_metadata(entry, message)
    if not contact_id:
        contact_id = (entry.get("contact") or {}).get("id")
    if not contact_id:
        log.info("outbound-events: no contact on call %s — nothing to tag", call_id)
        return entry, None

    ended_reason = message.get("endedReason") or call.get("endedReason") or ""
    messages = message.get("messages") or (message.get("artifact") or {}).get("messages")
    tag = _classify_outcome(ended_reason, entry.get("confirm_outcome") or "", messages)
    if not tag:
        log.info("outbound-events: call %s already tagged in-call — no outcome tag", call_id)
        return entry, None

    try:
        ok = await ghl.add_tag(contact_id, "main", tag)
    except Exception as e:  # noqa: BLE001 — never fail the webhook on a tag write
        log.error("outbound-events: tag %s failed for %s: %s", tag, contact_id, e)
        return entry, tag
    log.info(
        "outbound-events: tagged %s -> %s (ok=%s, endedReason=%s, call=%s)",
        contact_id, tag, ok, ended_reason, call_id,
    )
    return entry, tag


# --------------------------------------------------------------------------- #
# Telegram push (outbound)
# --------------------------------------------------------------------------- #

# Header labels for an OUTBOUND call. call_summary.classify_outcome only knows
# Jon's two endings (customer-ended-call, assistant-ended-call); Ashley signs off
# by SPEAKING "BYE" via endCallPhrases, so every clean call comes back
# endedReason=assistant-said-end-call-phrase and classifies as OTHER. The better
# the call went, the more certain that was. We already know the real outcome from
# the tools and the GHL tag, so use it - layout unchanged, header only.
_OUTBOUND_HEADER_BY_CONFIRM = {
    "confirmed": ("✅", "CONFIRMED"),
    "not_interested": ("🚫", "NOT INTERESTED"),
    "reschedule_requested": ("📅", "NEEDS RESCHEDULE"),
    "rescheduled": ("📅", "RESCHEDULED"),
    "booked": ("📅", "RESCHEDULED"),
}
_OUTBOUND_HEADER_BY_TAG = {
    "call-answered": ("☎️", "ANSWERED"),
    "call-voicemail": ("📼", "VOICEMAIL"),
    "call-no-answer": ("❌", "NO ANSWER"),
    "call-rescheduled": ("📅", "RESCHEDULED"),
}


# Which of Ashley's flows a call was, in words. `rebooking` was missing until
# 2026-09-09 and fell through to the ungrammatical "rebooking call"; it is a
# first-class flow and reads as one now. Shared by the Telegram summary and the
# GHL internal comment so the two can never disagree about what a call was.
_CALL_TYPE_LABEL = {
    "confirmation": "Confirmation call (day before)",
    "reminder": "Reminder call (day of)",
    "rebooking": "Rebooking call-back",
    "test": "TEST twin",
    "test2": "TEST V2 twin",
}


def _call_type_label(entry: dict) -> str:
    """Human label for the flow, or "" when the call carried no call_type."""
    ctype = (entry.get("call_type") or "").strip().lower()
    if not ctype:
        return ""
    return _CALL_TYPE_LABEL.get(ctype, ctype + " call")


def _outbound_header(entry: dict, tag: Optional[str]) -> Optional[tuple[str, str]]:
    """The (icon, LABEL) for an outbound call, or None when we cannot tell.

    Single source of truth for the outbound outcome: the Telegram header and
    the archived summary.json both read it, so the daily digest can never
    report something different from what the operator saw in the chat.

    The in-call outcome wins over the end-of-call tag — "they confirmed" is a
    stronger fact than "they answered".
    """
    head = _OUTBOUND_HEADER_BY_CONFIRM.get((entry.get("confirm_outcome") or "").strip())
    if head is None:
        head = _OUTBOUND_HEADER_BY_TAG.get(tag or "")
    return head


def _retitle_for_outbound(summary_text: str, entry: dict, tag: Optional[str]) -> str:
    """Swap the header icon+label for the outbound truth, keeping the duration.

    First line is "<icon> <LABEL>" or "<icon> <LABEL> · <duration>". Only the first
    segment is replaced; if that shape ever changes the text comes back untouched
    rather than mangled."""
    head = _outbound_header(entry, tag)
    if head is None:
        return summary_text
    lines = summary_text.splitlines()
    if not lines:
        return summary_text
    sep = " · "
    parts = lines[0].split(sep)
    parts[0] = head[0] + " " + head[1]
    lines[0] = sep.join(parts)
    return chr(10).join(lines)


def _outbound_telegram_text(summary_text: str, entry: dict, tag: Optional[str]) -> str:
    """The shared end-of-call summary, adjusted for an Ashley call (owner, 2026-09-14).

    Header reads "<icon> <LABEL> · <duration> - <which call>", e.g.
    "✅ CONFIRMED · 44s - Reminder call (day of)". The call type rides on the
    header instead of its own "Ashley:" line, and the "CRM:" line is dropped:
    every Ashley call is Main CRM, so it said nothing. Done here, not in the
    formatter, because the inbound chain shares that formatter and keeps both.
    """
    summary_text = _retitle_for_outbound(summary_text, entry, tag)
    lines = [ln for ln in summary_text.splitlines() if not ln.startswith("CRM: ")]
    which = _call_type_label(entry)
    if which and lines:
        lines[0] = lines[0] + " - " + which
    return chr(10).join(lines)


def _build_comment_text(
    message: dict, entry: dict, tag: Optional[str], mention: dict | None
) -> str:
    """The internal-comment body: what happened, which call it was, the audio.

    Deliberately short. This lands inline in the conversation next to the texts,
    and anything longer than a few lines buries the thread it is annotating —
    the transcript already has a home in Telegram and the archive.
    """
    head = _outbound_header(entry, tag)
    icon, label = head if head else ("⚠️", "CALL ENDED")
    which = _call_type_label(entry) or "Outbound call"
    dur = duration_label(message.get("startedAt", "") or "",
                         message.get("endedAt", "") or "")

    first = icon + " " + label + " — Ashley, " + which
    if dur:
        first += " · " + dur
    lines = [first]

    # Mention markup and the bare id in `mentions` are BOTH required by GHL —
    # the markup renders the @name, the array is what actually notifies.
    if mention:
        lines.append("@" + mention["name"] + "<userId>" + mention["id"] + "</userId>")

    call_id = (message.get("call") or {}).get("id") or ""
    rec = public_recording_url(call_id)
    if rec:
        lines.append("Recording: " + rec)

    return chr(10).join(lines)


async def _post_call_comment(body: dict, entry: dict, tag: Optional[str]) -> None:
    """Post the call result into the prospect's GHL conversation. Never raises.

    Runs as its own step rather than inside _push_outbound_telegram, because that
    function returns early for calls under 30s and for already-pushed resends —
    and every call gets a comment, no-answers included. The recording link is
    resolved from the call id via the published file, so no folder has to be
    passed between the two steps.
    """
    message = body.get("message") or {}
    call = message.get("call") or {}
    call_id = call.get("id") or _extract_call_id(message)
    contact_id = (call.get("metadata") or {}).get("ghl_contact_id")         or (entry.get("contact") or {}).get("id")
    if not contact_id:
        log.info("outbound-events: no contact on call %s — no GHL comment", call_id)
        return
    if already_commented(call_id):
        log.info("outbound-events: GHL comment already posted for %s", call_id)
        return

    mention = await ghl.get_assigned_user(contact_id, "main")
    text = _build_comment_text(message, entry, tag, mention)
    ok = await ghl.post_internal_comment(
        contact_id, "main", text,
        mentions=[mention["id"]] if mention else None,
    )
    if ok:
        mark_commented(call_id)
    log.info("outbound-events: GHL comment for %s ok=%s mention=%s",
             contact_id, ok, (mention or {}).get("id"))


async def _push_outbound_telegram(body: dict, entry: dict, tag: Optional[str]) -> None:
    """Archive the call and push summary + transcript + audio to the operator bot.

    Mirrors the inbound chain in server._handle_end_of_call, including its
    idempotency: archive_call returns None under 30s, and the pushed.flag
    sentinel absorbs Vapi's event resends. The flag is committed as soon as the
    SUMMARY lands — the summary is the operator-critical signal, so a failed
    audio upload afterwards must not cause a duplicate summary on resend.

    Never raises: a Telegram outage must not make Vapi retry the report.
    """
    message = body.get("message") or {}
    phone = ((message.get("call") or {}).get("customer", {}) or {}).get("number", "")

    business_label = _resolve_business_label(entry, phone)
    t_label = _resolve_transcript_label(entry)

    folder = await archive_call(
        message,
        business_label=business_label,
        transcript_label=t_label,
        # Persist the OUTBOUND outcome, not classify_outcome's inbound guess —
        # otherwise every one of Ashley's calls archives as OTHER and the daily
        # stats cannot tell a confirmation from a no-answer.
        outcome_override=_outbound_header(entry, tag),
        call_type=(entry.get("call_type") or "").strip().lower(),
    )
    if folder is None:
        return  # under 30s — archive_call already logged the skip
    if already_pushed(folder):
        log.info("outbound-events: telegram already pushed for %s", folder.name)
        return

    # Same formatter, same layout as the inbound chain in server._handle_end_of_call,
    # so an Ashley call reads identically to one of Jon's in the operator chat.
    summary_text = format_ended_summary(payload=message, contact=entry.get("contact"),
                                        folder_name=folder.name)

    summary_text = _outbound_telegram_text(summary_text, entry, tag)
    summary_mid = await tg_send_message(summary_text, parse_mode=None)
    if summary_mid:
        mark_pushed(folder)

    transcript = build_transcript(message)
    if transcript:
        if len(transcript) <= 3500:
            await tg_send_message(transcript, parse_mode=None)
        else:
            await tg_send_document(folder / transcript_filename(t_label),
                                   caption="transcript")

    rec = folder / "recording.mp3"
    if rec.exists():
        await tg_send_audio(rec, caption=title_slash(message, business_label),
                            filename=f"{title_dash(message, business_label)}.mp3")
    else:
        log.warning("outbound-events: recording.mp3 missing for %s", folder.name)

    log.info("outbound-events: pushed Telegram for %s biz=%r tag=%s",
             folder.name, business_label, tag)


def _record_call_ended(body: dict, entry: dict, tag: Optional[str]) -> None:
    """Write the call's `ended` line to the ledger the CRM reports from.

    Every call gets one, however short: the Telegram push skips calls under 30s,
    and the CRM needs the voicemails and no-answers too. The outcome comes from
    _outbound_header, the same source as the Telegram header and GHL comment.
    """
    message = body.get("message") or {}
    call = message.get("call") or {}
    meta = call.get("metadata") or {}
    cost = message.get("cost", call.get("cost"))
    call_ledger.record(
        "ended",
        call_type=meta.get("call_type") or entry.get("call_type") or "",
        vapi_call_id=call.get("id") or _extract_call_id(message),
        ghl_contact_id=meta.get("ghl_contact_id") or (entry.get("contact") or {}).get("id"),
        ghl_appointment_id=meta.get("ghl_appointment_id"),
        outcome=call_ledger.outcome_key(_outbound_header(entry, tag)),
        outcome_tag=tag,
        ended_reason=message.get("endedReason") or call.get("endedReason"),
        duration_sec=call_ledger.seconds_between(message.get("startedAt"), message.get("endedAt")),
        cost=cost if isinstance(cost, (int, float)) else None,
        business=meta.get("ghl_business"),
    )


@router.post("/vapi/outbound/events")
async def outbound_events(
    request: Request,
    x_vapi_signature: Optional[str] = Header(default=None),
    x_vapi_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Phone-level events for the outbound Ashley assistants ONLY.

    On end-of-call we classify how the call went and write a tag back to GHL so
    the show-up workflow can branch on it. Kept separate from the inbound
    /vapi/server-events handler so inbound logic never runs for an outbound call.
    """
    raw = await request.body()
    if not _verify_signature(raw, x_vapi_signature or x_vapi_secret):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        return JSONResponse(content={})

    msg_type = (body.get("message") or {}).get("type")
    if msg_type == "end-of-call-report":
        entry: dict = {}
        tag: Optional[str] = None
        try:
            entry, tag = await _tag_call_outcome(body)
        except Exception:  # noqa: BLE001 — always 200 so Vapi stops retrying
            log.exception("outbound-events: outcome tagging crashed")
        # Its own try, straight after tagging: the CRM's call tracking reads this
        # line, so it must not wait on — or be lost to — Telegram or GHL below.
        try:
            _record_call_ended(body, entry, tag)
        except Exception:  # noqa: BLE001
            log.exception("outbound-events: call ledger write crashed")
        # Separate try: a Telegram outage must not cost us the GHL tag, and a
        # tagging failure must not cost the operator their call record.
        try:
            await _push_outbound_telegram(body, entry, tag)
        except Exception:  # noqa: BLE001
            log.exception("outbound-events: telegram push crashed")
        # Third separate try, same reasoning as the second: a GHL outage must not
        # cost the operator their call record, and it must not make Vapi retry.
        # Runs last because the recording link only exists once the archive step
        # above has downloaded and published the audio.
        try:
            await _post_call_comment(body, entry, tag)
        except Exception:  # noqa: BLE001
            log.exception("outbound-events: GHL internal comment crashed")
    else:
        log.info("outbound-events ack type=%s", msg_type)
    return JSONResponse(content={})
