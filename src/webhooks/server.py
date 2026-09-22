"""Voicsito — FastAPI webhook server for Vapi tool calls.

Receives Vapi tool-call requests at POST /vapi/tool, dispatches to handlers
that proxy to our existing GHL backend (src.agent.ghl), and returns results
in the Vapi-required format:

    {"results": [{"toolCallId": "...", "result": "<string>"}]}

Run locally:
    uvicorn src.webhooks.server:app --reload --port 8081

Run on VPS (production):
    uvicorn src.webhooks.server:app --host 0.0.0.0 --port 8081
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

# Load env from VPS path first, then local fallback. Both are gitignored.
try:
    from dotenv import load_dotenv

    for env_path in ("/opt/webhooks/voicsito/.env", "config/.env"):
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break
except Exception:
    pass

# Import the existing GHL client. Kept stateless and free of heavy media deps.
import sys

# Make `src.agent.ghl` importable regardless of how uvicorn is launched.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.agent import ghl  # noqa: E402
from src.webhooks.tz_resolver import resolve_timezone_with_source  # noqa: E402
from src.webhooks.telegram_notify import send_message as tg_send_message  # noqa: E402
from src.webhooks.telegram_notify import send_audio as tg_send_audio  # noqa: E402
from src.webhooks.telegram_notify import send_document as tg_send_document  # noqa: E402
from src.webhooks.call_archive import (  # noqa: E402
    archive_call, already_pushed, mark_pushed, title_slash, title_dash, slugify,
    build_transcript, transcript_filename,
)
from src.webhooks.call_summary import (  # noqa: E402
    format_started_message,
    format_ended_summary,
    duration_label,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("voicsito.webhooks")


# ---------------------------------------------------------------------------
# Per-call cache (in-memory, TTL-bound)
# ---------------------------------------------------------------------------
# Keyed by Vapi call.id. Holds the resolved CRM contact + locally captured
# fields so subsequent tool calls in the same call don't re-hit GHL search.
_CACHE_TTL_SECONDS = 60 * 60  # 1h — calls rarely run longer
_call_cache: dict[str, dict[str, Any]] = {}
_cache_lock = asyncio.Lock()

# Track call_ids we've already pushed "📞 Call started" for, to avoid duplicate
# pushes if Vapi sends multiple `status-update` events with status=in-progress.
# In-memory only — resets on restart, which is fine.
_started_pushed: set[str] = set()


async def _cache_get(call_id: str) -> dict[str, Any]:
    """Return the cache entry for a call, creating an empty one if missing.

    Each entry shape:
        {
            "created": float (monotonic),
            "phone": str | None,
            "contact": dict | None,        # GHL lookup result
            "lookup_done": bool,
            "captured": {                  # locally captured during this call
                "first_name": str,
                "last_name": str,
                "business": str,
                "email": str,
            },
        }
    """
    async with _cache_lock:
        _evict_expired_locked()
        entry = _call_cache.get(call_id)
        if entry is None:
            entry = {
                "created": time.monotonic(),
                "phone": None,
                "contact": None,
                "lookup_done": False,
                "captured": {
                    "first_name": "",
                    "last_name": "",
                    "business": "",
                    "email": "",
                },
                # Slot ISOs surfaced to the LLM via get_available_slots; used by
                # book_video_call to reject hallucinated slots.
                "offered_slots": set(),
            }
            _call_cache[call_id] = entry
        return entry


def _evict_expired_locked() -> None:
    """Drop cache entries older than the TTL. Caller holds _cache_lock."""
    now = time.monotonic()
    expired = [k for k, v in _call_cache.items() if now - v["created"] > _CACHE_TTL_SECONDS]
    for k in expired:
        _call_cache.pop(k, None)


# ---------------------------------------------------------------------------
# Signature verification (stub — only enforces if env var set)
# ---------------------------------------------------------------------------
_WEBHOOK_SECRET = os.environ.get("VAPI_WEBHOOK_SECRET", "").strip()
if not _WEBHOOK_SECRET:
    log.warning(
        "VAPI_WEBHOOK_SECRET not set — webhook signature verification is DISABLED. "
        "Set this env var in production to enforce HMAC-SHA256 verification."
    )


def _verify_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    """Verify HMAC-SHA256 signature header against raw body.

    Vapi sends the shared secret either as a literal value in `x-vapi-secret`
    or as an HMAC-SHA256 hex digest in `x-vapi-signature`. If the supplied
    header matches the literal secret, accept (Vapi's "Custom Headers"
    flow). Otherwise compute HMAC and compare in constant time.
    """
    if not _WEBHOOK_SECRET:
        return True  # verification disabled by configuration
    if not signature:
        return False
    # Literal match (Vapi "Custom Headers" feature)
    if hmac.compare_digest(signature.strip(), _WEBHOOK_SECRET):
        return True
    # HMAC match
    expected = hmac.new(
        _WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature.strip().lower(), expected.lower())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_phone(message: dict) -> Optional[str]:
    """Pull the caller's phone number from a Vapi message envelope.

    Vapi places the inbound caller's number under several possible paths
    depending on telephony provider; check each.
    """
    if not isinstance(message, dict):
        return None
    call = message.get("call") or {}
    customer = (call.get("customer") or message.get("customer") or {})
    for path in (
        customer.get("number"),
        customer.get("phoneNumber"),
        (call.get("phoneNumber") or {}).get("number") if isinstance(call.get("phoneNumber"), dict) else None,
        call.get("from"),
        message.get("phoneNumber"),
    ):
        if isinstance(path, str) and path.strip():
            return path.strip()
    return None


def _extract_call_id(message: dict) -> str:
    """Return the Vapi call.id, or a stable fallback if missing."""
    if not isinstance(message, dict):
        return "unknown"
    call = message.get("call") or {}
    cid = call.get("id") or message.get("callId") or message.get("id")
    return cid if isinstance(cid, str) and cid else "unknown"


def _arg_str(value: Any) -> str:
    """Coerce a tool-call argument to a stripped string.

    GPT-4.1 occasionally emits non-string types (int, bool, None, list) for
    fields the schema says are strings. The handlers used to do
    `args.get(k, "").strip()` which crashes when `args.get(k)` returns a
    non-string. Returns "" for None / missing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_args(raw: Any) -> dict:
    """Vapi sends function.arguments as either a dict or a JSON string."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _ensure_contact_loaded(call_id: str, phone: Optional[str]) -> dict[str, Any]:
    """Look up the CRM contact for this call (once) and cache it.

    Returns the cache entry. Lookup is best-effort; failures are logged and
    leave `contact` as None.
    """
    entry = await _cache_get(call_id)
    if entry["lookup_done"]:
        return entry
    if phone:
        entry["phone"] = phone
        try:
            contact = await asyncio.wait_for(
                ghl.lookup_contact_in_texts_crms(phone), timeout=4.0
            )
            entry["contact"] = contact
            if contact:
                log.info(
                    "lookup hit call_id=%s phone=%s name=%r business=%r biz_type=%r crm=%s",
                    call_id, phone, contact.get("name"), contact.get("business"),
                    contact.get("biz_type"), contact.get("crm"),
                )
            else:
                log.info("lookup miss call_id=%s phone=%s", call_id, phone)
        except Exception as e:
            log.error("lookup error call_id=%s phone=%s err=%s", call_id, phone, e)
    entry["lookup_done"] = True
    return entry


# ---------------------------------------------------------------------------
# Tool handlers — each returns a stringified result for Vapi.
# ---------------------------------------------------------------------------
# Strong references to in-flight background tasks. Without this set, asyncio
# can GC tasks that nobody awaits, cancelling the GHL write before it lands.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _persist_captured_to_ghl(call_id: str, phone: Optional[str]) -> None:
    """Background task — does the actual GHL write off the critical path.
    Reads the latest captured fields from the cache (in case multiple
    update_prospect_info calls fired in quick succession, only the freshest
    write hits GHL). Errors are logged, not raised."""
    try:
        entry = await _ensure_contact_loaded(call_id, phone)
        contact = entry.get("contact")
        if not contact or not contact.get("id"):
            log.info("background persist: no contact for call_id=%s yet — cached only", call_id)
            return
        captured = entry["captured"]
        await ghl.update_prospect_fields(
            contact_id=contact["id"],
            crm_key=contact["crm"],
            first_name=captured.get("first_name", ""),
            last_name=captured.get("last_name", ""),
            business=captured.get("business", ""),
            email=captured.get("email", ""),
        )
    except Exception as e:
        log.warning("background persist failed call_id=%s err=%s", call_id, e)


async def _handle_update_prospect_info(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Fire-and-forget: stash captured fields synchronously, schedule the GHL
    write in the background, return in milliseconds. The LLM's natural
    acknowledgement of the prospect's name plays without any audible delay."""
    first_name = _arg_str(args.get("first_name"))
    last_name = _arg_str(args.get("last_name"))
    business = _arg_str(args.get("business_name")) or _arg_str(args.get("business"))
    email = _arg_str(args.get("email"))

    # Synchronous part — must complete in <5ms. No GHL round-trip here.
    entry = await _cache_get(call_id)
    if phone and not entry.get("phone"):
        entry["phone"] = phone
    captured = entry["captured"]
    if first_name:
        captured["first_name"] = first_name
    if last_name:
        captured["last_name"] = last_name
    if business:
        captured["business"] = business
    if email:
        captured["email"] = email

    log.info(
        "update_prospect_info captured call_id=%s first=%r business=%r email=%r",
        call_id, first_name, business, email,
    )

    # Background — actual GHL write happens off the critical path.
    task = asyncio.create_task(_persist_captured_to_ghl(call_id, phone))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)

    return "ok"


async def _handle_get_available_slots(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    try:
        days_ahead = int(args.get("days_ahead") or 7)
    except (TypeError, ValueError):
        days_ahead = 7
    entry = await _ensure_contact_loaded(call_id, phone)
    contact = entry.get("contact") or {}

    # Resolution chain: stored IANA tz → derive from city/state → Eastern default.
    # Never falls back to the calendar's configured zone (Bogota) — that would
    # silently mis-time a US prospect's slots.
    prospect_tz, tz_source = resolve_timezone_with_source(
        stored_tz=contact.get("timezone", ""),
        city=contact.get("city", ""),
        state=contact.get("state", ""),
    )
    # WARN when fallback fires (CRM data gap), INFO when stored/state/city worked.
    log_fn = log.warning if tz_source == "default" else log.info
    log_fn(
        "tz_resolved call_id=%s tz=%s source=%s stored=%r city=%r state=%r",
        call_id, prospect_tz, tz_source, contact.get("timezone", ""),
        contact.get("city", ""), contact.get("state", ""),
    )
    # Availability always reads the Video Call Calendar — for inbound booking AND
    # confirm rebooking (the rebook destination). The confirm flow looks up the
    # prospect's existing appointment separately on CONFIRM_CALENDAR_ID and moves
    # it here, so open slots must come from the calendar we book onto.
    cal_id = ghl.VIDEO_CALL_CALENDAR_ID

    # Rebooking books with Jon only (owner, 2026-09-10). The Video Call Calendar
    # is round-robin across Waseem and Jon, and Jon's availability is a strict
    # subset — measured 2026-09-10, 12 of the pool's 26 slots over 7 days. Left
    # unfiltered, more than half of what Ashley reads out is time only Waseem
    # can take, and she would be offering slots that cannot be honoured.
    #
    # Scoped to the rebooking flow on purpose: an inbound booking should still
    # see the whole team's availability.
    slot_user_id = ""
    if (entry.get("call_type") or "").strip().lower() == "rebooking":
        slot_user_id = ghl.REBOOKING_ASSIGNED_USER_ID
    try:
        slots = await ghl.get_available_slots(
            days_ahead=days_ahead,
            timezone=prospect_tz,
            calendar_id=cal_id,
            user_id=slot_user_id,
        )
    except Exception as e:
        log.error("get_available_slots error err=%s", e)
        return "Error fetching calendar — try again or just collect their preferred time."

    lines: list[str] = []
    offered_now: set[str] = set()
    for date_key, info in slots.items():
        if not isinstance(info, dict) or "slots" not in info:
            continue
        slot_list = info["slots"] or []
        if not slot_list:
            continue
        pretty: list[str] = []
        iso_for_day: Optional[str] = None
        for iso in slot_list[:6]:
            try:
                dt = datetime.fromisoformat(iso)
                pretty.append(dt.strftime("%-I:%M%p").lower() + f" [iso:{iso}]")
                offered_now.add(iso)
                if iso_for_day is None:
                    iso_for_day = iso
            except Exception:
                pretty.append(iso)
        if iso_for_day:
            day_name = datetime.fromisoformat(iso_for_day).strftime("%A %b %-d")
            lines.append(f"{day_name}: " + ", ".join(pretty))

    # Stash every ISO the LLM saw — book_video_call validates against this set
    # to catch hallucinated slots. Accumulate across multiple slot fetches in
    # the same call (so re-pick after re-fetch still works).
    entry.setdefault("offered_slots", set()).update(offered_now)

    if not lines:
        return "No open slots in the next week."
    return (
        "Available slots (use the [iso:...] value when calling book_video_call):\n"
        + "\n".join(lines)
    )


async def _handle_mark_for_transfer(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    """Tag the texts-CRM contact `transfer` so the GHL automation copies it to main CRM.
    Fires the moment the prospect agrees to book — main-CRM contact is then ready by
    the time book_video_call runs (after natural email-confirmation dialogue)."""
    entry = await _ensure_contact_loaded(call_id, phone)
    contact = entry.get("contact")

    # If lookup missed earlier, create a texts-CRM contact now so we have something to tag.
    if not contact or not contact.get("id"):
        captured = entry["captured"]
        try:
            created = await ghl.create_contact_in_texts_crm(
                phone=phone or entry.get("phone") or "",
                first_name=captured.get("first_name", ""),
                last_name=captured.get("last_name", ""),
                email=captured.get("email", ""),
                business=captured.get("business", ""),
            )
            if not created.get("ok"):
                return f"Transfer prep failed: {created.get('error')}"
            entry["contact"] = {
                "id": created["contact_id"],
                "crm": "texts1",
                "first_name": captured.get("first_name", ""),
                "business": captured.get("business", ""),
                "email": captured.get("email", ""),
            }
            contact = entry["contact"]
        except Exception as e:
            log.error("transfer create error call_id=%s err=%s", call_id, e)
            return f"Transfer error: {str(e)[:120]}"

    try:
        ok = await ghl.add_tag(contact["id"], contact["crm"], "transfer")
        if ok:
            log.info("tagged transfer call_id=%s contact=%s crm=%s", call_id, contact["id"], contact["crm"])
            return "Transfer initiated."
        return "Transfer tag failed — continue conversation, will retry at booking."
    except Exception as e:
        log.error("transfer tag error call_id=%s err=%s", call_id, e)
        return f"Transfer error: {str(e)[:120]}"


def _canonical_iso(iso: str) -> Optional[str]:
    """Return a canonical isoformat() of `iso` if parseable, else None.
    Used to compare slot ISOs by the absolute moment they represent rather
    than by the exact textual spelling the LLM passed back."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.isoformat()


def _slot_iso_matches_offered(slot_iso: str, offered: set) -> bool:
    """True if `slot_iso` represents the same absolute moment as any ISO in
    `offered`. Tolerates textual reformatting by the LLM."""
    target = _canonical_iso(slot_iso)
    if target is None:
        return False
    return any(_canonical_iso(o) == target for o in offered)


def _wall_key(iso: str) -> Optional[tuple]:
    """(month, day, hour, minute) of an ISO's wall-clock components, ignoring
    year and UTC offset. Used to recover from an LLM that kept the right
    month/day/time but stamped the wrong year (or mangled the offset) instead
    of copying the [iso:...] value verbatim."""
    try:
        dt = datetime.fromisoformat(iso.strip())
    except (ValueError, TypeError, AttributeError):
        return None
    return (dt.month, dt.day, dt.hour, dt.minute)


def _snap_to_offered(slot_iso: str, offered: set) -> Optional[str]:
    """If `slot_iso` doesn't match any offered ISO as an absolute moment but its
    wall-clock month/day/hour/minute UNIQUELY matches exactly one offered slot,
    return that offered ISO (the authoritative year + offset). Returns None when
    there is no wall-clock match or the match is ambiguous (>1) — the caller then
    rejects rather than guessing at a time the prospect didn't agree to."""
    key = _wall_key(slot_iso)
    if key is None:
        return None
    matches = [o for o in offered if _wall_key(o) == key]
    return matches[0] if len(matches) == 1 else None


def _slot_is_in_past(slot_iso: str) -> bool:
    """True if `slot_iso` parses to a tz-aware moment before now. Degraded-mode
    sanity check: with no offered set to validate against, a past-dated slot
    (e.g. an LLM that stamped the wrong year) can never be a real open time, so
    we reject it rather than let GHL bounce it with 'slot no longer available'.
    Unparseable / tz-naive returns False — that's a separate concern GHL handles."""
    canon = _canonical_iso(slot_iso)
    if canon is None:
        return False
    return datetime.fromisoformat(canon) < datetime.now(timezone.utc)


async def _handle_book_video_call(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    slot_iso = _arg_str(args.get("slot_iso"))
    email = _arg_str(args.get("email"))
    prospect_first_name = _arg_str(args.get("prospect_first_name")) or _arg_str(args.get("first_name"))
    business_name = _arg_str(args.get("business_name")) or _arg_str(args.get("business"))

    if not slot_iso:
        return "Booking error: slot_iso is required."
    if not email:
        return "Booking error: email is required."

    # Dry-run gate for live-system tests.
    # See .claude/skills/live-system-testing/SKILL.md.
    # Triggers ONLY when BOTH conditions are met:
    #   (a) caller passed _test_dry_run=true in tool args (LLM won't — it's
    #       not in the tool schema; only the test framework sends it).
    #   (b) the call's customer phone is in the reserved test range +15550199.
    # Production calls have neither, so this can't ever affect a real customer.
    # If marker prefix changes in tests/lib/test_markers.py, update here too.
    _TEST_PHONE_PREFIX = "+15550199"
    if args.get("_test_dry_run") is True:
        if not (phone or "").startswith(_TEST_PHONE_PREFIX):
            log.error(
                "dry_run rejected: phone %r not in test range %s call_id=%s",
                phone, _TEST_PHONE_PREFIX, call_id,
            )
            return "Booking error: dry-run requires a test-range phone number."
        log.info(
            "DRY-RUN book_video_call call_id=%s phone=%s slot=%s — skipping GHL write",
            call_id, phone, slot_iso,
        )
        return f"[DRY-RUN] Would book {slot_iso} for {phone} ({email})."

    entry = await _ensure_contact_loaded(call_id, phone)

    # Reject hallucinated slots: if the LLM ever saw a slot list this call,
    # the slot_iso must match one of those ISOs as the same absolute moment.
    # Compare canonical (parsed) form so a same-instant ISO with a different
    # textual spelling (e.g. trailing whitespace, dropped seconds, +00:00 vs Z)
    # still matches. Empty set = state-loss degraded mode → allow + log.
    offered = entry.get("offered_slots") or set()
    if offered:
        if not _slot_iso_matches_offered(slot_iso, offered):
            # Recovery before rejection: GPT-4.1 sometimes rebuilds the timestamp
            # from its training prior — right month/day/time, wrong year/offset
            # (observed stamping 2024 on live 2026 calls). If the wall-clock
            # uniquely matches one offered slot, snap to that authoritative ISO.
            snapped = _snap_to_offered(slot_iso, offered)
            if snapped:
                log.warning(
                    "book_video_call snapped wrong-year/offset slot call_id=%s "
                    "sent=%s -> offered=%s", call_id, slot_iso, snapped,
                )
                slot_iso = snapped
            else:
                log.warning(
                    "book_video_call rejected hallucinated slot call_id=%s slot_iso=%s "
                    "offered=%d slots", call_id, slot_iso, len(offered),
                )
                return (
                    "That time wasn't in the list I offered — please call "
                    "get_available_slots to see current open times, then pick one."
                )
    else:
        # Degraded mode: no offered set to validate against (cache lost, or the
        # model skipped get_available_slots and booked from short-term memory).
        # We can't confirm the slot is a real open time, but a past-dated slot
        # (e.g. the LLM stamped the wrong year) is definitively invalid — reject
        # it before GHL bounces it with "slot no longer available". A genuine
        # degraded-mode booking is always a future time, so this never blocks a
        # real one.
        if _slot_is_in_past(slot_iso):
            log.warning(
                "book_video_call rejected past slot in degraded mode "
                "call_id=%s slot_iso=%s", call_id, slot_iso,
            )
            return (
                "That time has already passed — please call get_available_slots "
                "to see current open times, then pick one."
            )
        log.warning(
            "book_video_call: offered_slots empty for call_id=%s — proceeding "
            "in degraded mode (cache may have been lost). slot_iso=%s",
            call_id, slot_iso,
        )

    # Cache is the authoritative source — captured at the moment the prospect
    # said it via update_prospect_info. The LLM's args (passed minutes later
    # from short-term memory) are a fallback only.
    captured = entry["captured"]
    prospect_first_name = captured.get("first_name", "") or prospect_first_name
    business_name = captured.get("business", "") or business_name
    # Email is special: the booking-time args are the freshest source (the
    # prospect just confirmed it on the call), so args win for email.
    email = email or captured.get("email", "")
    resolved_phone = phone or entry.get("phone") or ""

    # Make sure the texts-CRM contact has the captured info + transfer tag.
    # If mark_for_transfer ran earlier this call, the tag is already there (idempotent).
    texts_contact = entry.get("contact")
    if not texts_contact or not texts_contact.get("id"):
        try:
            created = await ghl.create_contact_in_texts_crm(
                phone=resolved_phone,
                first_name=prospect_first_name,
                last_name="",
                email=email,
                business=business_name,
            )
            if not created.get("ok"):
                return f"Could not create contact: {created.get('error')}"
            texts_contact = {
                "id": created["contact_id"],
                "crm": "texts1",
                "first_name": prospect_first_name,
                "business": business_name,
                "email": email,
            }
            entry["contact"] = texts_contact
        except Exception as e:
            log.error("texts contact create error call_id=%s err=%s", call_id, e)
            return f"Contact create error: {str(e)[:120]}"

    # Best-effort field update + transfer tag (idempotent if already tagged).
    try:
        await ghl.update_prospect_fields(
            contact_id=texts_contact["id"],
            crm_key=texts_contact["crm"],
            first_name=prospect_first_name,
            last_name="",
            business=business_name,
            email=email,
        )
    except Exception as e:
        log.warning("pre-book update failed (non-fatal) call_id=%s err=%s", call_id, e)
    try:
        await ghl.add_tag(texts_contact["id"], texts_contact["crm"], "transfer")
    except Exception as e:
        log.warning("pre-book transfer tag failed (non-fatal) call_id=%s err=%s", call_id, e)

    # Poll main CRM for the freshly-transferred contact.
    if not resolved_phone:
        return "Booking error: no phone to look up in main CRM."
    # 5 × 3s = 15s nominal. Wrap with asyncio.wait_for to enforce a hard 15s
    # wall-clock ceiling — the inner httpx timeouts (4s each) could otherwise
    # push real worst-case past 30s and breach Vapi's 60s tool budget when
    # combined with the pre-book GHL ops (~5–10s).
    try:
        main_contact = await asyncio.wait_for(
            ghl.wait_for_main_crm_contact(
                phone=resolved_phone, max_attempts=5, interval_seconds=3.0
            ),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        log.error("main CRM polling exceeded 15s ceiling call_id=%s phone=%s",
                  call_id, resolved_phone)
        return "Booking failed: contact not in main CRM yet — please try again in 30 seconds."
    if not main_contact or not main_contact.get("id"):
        log.error("main CRM contact missing after transfer call_id=%s phone=%s", call_id, resolved_phone)
        return "Booking failed: contact not in main CRM yet — please try again in 30 seconds."
    main_contact_id = main_contact["id"]

    # Write the captured name + business + email onto the MAIN CRM contact
    # before booking. The texts→main automation creates main from a snapshot
    # of texts at transfer time — usually scraped/empty data. The pre-book
    # texts update doesn't propagate (automation already fired), so we have
    # to update main directly here. Best-effort: never blocks the booking.
    # Includes email in the guard because the snapshot's email is often blank
    # while we always have a fresh one at booking time.
    if prospect_first_name or business_name or email:
        try:
            await ghl.update_prospect_fields(
                contact_id=main_contact_id,
                crm_key="main",
                first_name=prospect_first_name,
                last_name="",
                business=business_name,
                email=email,
            )
        except Exception as e:
            log.warning(
                "main contact update failed (non-fatal) call_id=%s main_contact_id=%s err=%s",
                call_id, main_contact_id, e,
            )

    try:
        result = await ghl.book_video_call(
            slot_iso=slot_iso,
            contact_id=main_contact_id,
            email=email,
        )
        if not result.get("ok"):
            return f"Booking failed: {result.get('error', 'unknown error')}"
        appt = result.get("appointment", {}) or {}
        log.info(
            "booked call_id=%s appt_id=%s slot=%s main_contact=%s",
            call_id, appt.get("id", "?"), slot_iso, main_contact_id,
        )
        try:
            await ghl.add_tag(main_contact_id, "main", "voicsito-booked")
        except Exception as e:
            log.warning("post-book tag failed (non-fatal) call_id=%s err=%s", call_id, e)
        return f"Booked successfully for {slot_iso}."
    except Exception as e:
        log.error("book_video_call error call_id=%s err=%s", call_id, e)
        return f"Booking error: {str(e)[:120]}"


async def _handle_end_call(args: dict, call_id: str) -> str:
    # Vapi's `endCall` system tool actually hangs up. This is just a
    # compatibility stub so a custom-named tool returns success.
    log.info("end_call invoked call_id=%s", call_id)
    return "Call ended."


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
async def _dispatch_tool_call(
    tool_call: dict, message: dict, call_id: str, phone: Optional[str]
) -> dict:
    """Run one tool call and return a Vapi-shaped result entry."""
    tool_call_id = tool_call.get("id") or "unknown"
    function = tool_call.get("function") or {}
    name = function.get("name") or "unknown"
    args = _parse_args(function.get("arguments"))

    started = time.monotonic()
    try:
        if name == "update_prospect_info":
            result_str = await _handle_update_prospect_info(args, call_id, phone)
        elif name == "get_available_slots":
            result_str = await _handle_get_available_slots(args, call_id, phone)
        elif name == "mark_for_transfer":
            result_str = await _handle_mark_for_transfer(args, call_id, phone)
        elif name == "book_video_call":
            # Hard 45s ceiling — Vapi's tool budget is 60s, and the handler's
            # worst-case is ~53s (create + update + tag + 15s poll + update +
            # appointment write + tag). If we breach Vapi's budget the LLM
            # apologizes mid-call AFTER the appointment may have actually
            # landed in GHL, leaving a confused prospect. Fail clearly first.
            try:
                result_str = await asyncio.wait_for(
                    _handle_book_video_call(args, call_id, phone),
                    timeout=45.0,
                )
            except asyncio.TimeoutError:
                log.error(
                    "book_video_call hit 45s ceiling call_id=%s — Vapi tool "
                    "budget would have breached. Booking state in GHL is "
                    "ambiguous (may have completed). Operator must verify.",
                    call_id,
                )
                result_str = (
                    "Booking taking longer than expected — please try again "
                    "in 30 seconds or pick a different slot."
                )
        elif name == "end_call":
            result_str = await _handle_end_call(args, call_id)
        else:
            log.warning("unknown tool call_id=%s tool=%s", call_id, name)
            result_str = f"Unknown tool: {name}"
    except Exception as e:
        # Never let a handler exception bubble — Vapi needs a result string.
        log.exception("tool handler crash call_id=%s tool=%s", call_id, name)
        result_str = f"Internal error in {name}: {str(e)[:160]}"

    latency_ms = (time.monotonic() - started) * 1000
    log.info(
        "tool call_id=%s tool=%s latency_ms=%.0f result=%r",
        call_id, name, latency_ms, result_str[:120],
    )
    return {"toolCallId": tool_call_id, "result": result_str}


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Voicsito Vapi Webhooks", version="1.0.0")


@app.on_event("shutdown")
async def _drain_background_tasks() -> None:
    """On graceful shutdown, give in-flight fire-and-forget tasks a brief
    window to land their GHL writes. Without this, a redeploy mid-call drops
    captured prospect data on the floor."""
    if not _BACKGROUND_TASKS:
        return
    log.info("draining %d in-flight background tasks (5s grace)…", len(_BACKGROUND_TASKS))
    try:
        await asyncio.wait_for(
            asyncio.gather(*_BACKGROUND_TASKS, return_exceptions=True),
            timeout=5.0,
        )
        log.info("background tasks drained cleanly")
    except asyncio.TimeoutError:
        log.warning("background-task drain hit 5s timeout; %d still pending",
                    len(_BACKGROUND_TASKS))


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "voicsito-webhooks"}


@app.post("/vapi/tool")
async def vapi_tool(
    request: Request,
    x_vapi_signature: Optional[str] = Header(default=None),
    x_vapi_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Single endpoint for ALL Vapi tool calls.

    Returns 200 with `{"results": [...]}` on every code path so Vapi can
    surface errors back to the LLM rather than retrying or breaking the call.
    """
    raw = await request.body()
    sig = x_vapi_signature or x_vapi_secret
    if not _verify_signature(raw, sig):
        log.warning("rejected unsigned/invalid signature request len=%d", len(raw))
        return JSONResponse(
            status_code=401, content={"error": "invalid signature"}
        )

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        log.error("malformed JSON body")
        return JSONResponse(status_code=200, content={"results": []})

    message = body.get("message") or {}
    msg_type = message.get("type")
    tool_calls = message.get("toolCallList") or message.get("toolCalls") or []
    call_id = _extract_call_id(message)
    phone = _extract_phone(message)

    if msg_type and msg_type != "tool-calls":
        # Vapi sends other event types (status-update, end-of-call-report, etc.)
        # to the same server URL. Acknowledge them with an empty results list
        # so Vapi doesn't retry.
        log.info("non-tool message ignored type=%s call_id=%s", msg_type, call_id)
        return JSONResponse(content={"results": []})

    if not tool_calls:
        log.info("empty toolCallList call_id=%s", call_id)
        return JSONResponse(content={"results": []})

    # Vapi may batch multiple tool calls per request. Two tools MUST complete
    # their cache writes before later-running tools read those caches, or the
    # later tools see stale/empty data:
    #   - `update_prospect_info` writes captured name/business/email
    #   - `get_available_slots` writes the offered-slots set
    # `book_video_call` reads BOTH. Run the cache-priming tools first
    # sequentially, then everything else in parallel. Result order is
    # preserved so Vapi's toolCallList ordering is honored.
    PRIMING_TOOLS = ("update_prospect_info", "get_available_slots")
    priming: list[tuple[int, dict]] = []
    other: list[tuple[int, dict]] = []
    for i, tc in enumerate(tool_calls):
        name = ((tc.get("function") or {}).get("name")) or ""
        if name in PRIMING_TOOLS:
            priming.append((i, tc))
        else:
            other.append((i, tc))

    indexed_results: list[tuple[int, dict]] = []
    if priming:
        # Priming tools run concurrently — update_prospect_info is sync-fast
        # (~5ms), get_available_slots takes seconds for the GHL round-trip.
        # Running in parallel means slow slots don't block fast updates.
        # They write disjoint cache fields (captured vs offered_slots) so
        # there's no shared-state conflict.
        priming_results = await asyncio.gather(
            *[_dispatch_tool_call(tc, message, call_id, phone) for _, tc in priming],
            return_exceptions=False,
        )
        for (i, _), r in zip(priming, priming_results):
            indexed_results.append((i, r))
    if other:
        other_results = await asyncio.gather(
            *[_dispatch_tool_call(tc, message, call_id, phone) for _, tc in other],
            return_exceptions=False,
        )
        for (i, _), r in zip(other, other_results):
            indexed_results.append((i, r))
    indexed_results.sort(key=lambda pair: pair[0])
    results = [r for _, r in indexed_results]
    return JSONResponse(content={"results": results})


# ---------------------------------------------------------------------------
# Phone-number-level server events (assistant-request, end-of-call-report, etc.)
# ---------------------------------------------------------------------------
# Vapi POSTs `assistant-request` to a phone number's `serverUrl` BEFORE the LLM
# loads — that's our window to pre-populate prompt variables (biz_type, etc.)
# from the caller's CRM contact.

_VOICSITO_ASSISTANT_ID = os.environ.get(
    "VAPI_ASSISTANT_ID", "261bf1a0-3527-4fcb-956c-b6e227d0bae1"
)
# Optional: when set, the webhook returns a Squad config (3-stage flow) instead
# of the monolithic assistant. Unset this env var to roll back to the single
# monolithic assistant — no code change needed.
_VOICSITO_SQUAD_ID = os.environ.get("VAPI_SQUAD_ID", "").strip() or None


def _build_routing_response(overrides_vars: dict[str, str]) -> dict:
    """Return either a Squad config (preferred when VAPI_SQUAD_ID is set) or a
    single-assistant config (monolithic fallback). In both shapes the CRM
    variables propagate to every assistant the call touches.
    """
    if _VOICSITO_SQUAD_ID:
        return {
            "squadId": _VOICSITO_SQUAD_ID,
            "assistantOverrides": {"variableValues": overrides_vars},
        }
    return {
        "assistantId": _VOICSITO_ASSISTANT_ID,
        "assistantOverrides": {"variableValues": overrides_vars},
    }


async def _handle_assistant_request(message: dict) -> dict:
    """Build the Vapi assistant-request response.
    Pre-populates {{biz_type}} and {{business}} variables from the caller's
    texts-CRM contact. Falls back to default routing (no overrides) if
    lookup fails or contact not found — the call still goes through, just
    without industry adaptation.

    Routing target depends on VAPI_SQUAD_ID env var:
      - Set     -> returns squadId (3-stage Vapi Squad flow)
      - Unset   -> returns assistantId (legacy monolithic assistant)

    Note on first_name: intentionally NOT injected. The CRM's firstName
    field is scraped data and frequently contains a fragment of the business
    name (e.g. "Precision" from "Precision Powder Coating LLC"). The agent
    asks the prospect their name in the opener — that's the trustworthy source.
    """
    phone = _extract_phone(message)
    overrides_vars: dict[str, str] = {
        "biz_type": "",
        "business": "",
        "city": "",
        "state": "",
    }

    if not phone:
        log.info("assistant-request no phone — using default routing")
        return _build_routing_response(overrides_vars)

    # Tight timeout: GHL is usually <800ms; if slower, fail fast with empty vars
    # rather than hanging the call connect. Prompt handles missing vars gracefully.
    try:
        contact = await asyncio.wait_for(
            ghl.lookup_contact_in_texts_crms(phone), timeout=2.5
        )
    except asyncio.TimeoutError:
        log.warning("assistant-request lookup timeout phone=%s", phone)
        contact = None
    except Exception as e:
        log.error("assistant-request lookup error phone=%s err=%s", phone, e)
        contact = None

    if contact:
        overrides_vars["biz_type"] = (contact.get("biz_type") or "").strip()
        overrides_vars["business"] = (contact.get("business") or "").strip()
        overrides_vars["city"] = (contact.get("city") or "").strip()
        overrides_vars["state"] = (contact.get("state") or "").strip()
        log.info(
            "assistant-request hit phone=%s biz_type=%r business=%r city=%r state=%r crm=%s squad=%s",
            phone, overrides_vars["biz_type"], overrides_vars["business"],
            overrides_vars["city"], overrides_vars["state"], contact.get("crm"),
            bool(_VOICSITO_SQUAD_ID),
        )
    else:
        log.info("assistant-request miss phone=%s — default vars", phone)

    # Seed the per-call cache so the call-started / end-of-call Telegram handlers
    # don't re-hit GHL (which can rate-limit / timeout on the second concurrent
    # request seconds later). We already paid the cost of this lookup; share it.
    call_id = _extract_call_id(message)
    if call_id and call_id != "unknown":
        try:
            entry = await _cache_get(call_id)
            entry["phone"] = phone
            entry["contact"] = contact
            entry["lookup_done"] = True
        except Exception as e:
            log.warning("assistant-request: failed to seed cache call_id=%s err=%s",
                        call_id, e)

    return _build_routing_response(overrides_vars)


async def _handle_call_started(message: dict) -> None:
    """Fired on the first status-update where status == 'in-progress' for a
    given call.id. Idempotent within process lifetime.

    Reads the contact from the per-call cache — `_handle_assistant_request`
    has already run for this call and seeded the cache with the GHL lookup
    result. If that seeding failed or there's no cache entry, falls back to
    `_ensure_contact_loaded` which does a fresh lookup with a 4s timeout.
    """
    call = message.get("call") or {}
    call_id = call.get("id") or ""
    if not call_id or call_id in _started_pushed:
        return
    _started_pushed.add(call_id)

    phone = _extract_phone(message)
    contact = None
    try:
        entry = await _ensure_contact_loaded(call_id, phone)
        contact = entry.get("contact")
    except Exception as e:
        log.warning("call-started cache/lookup failed phone=%s err=%s", phone, e)

    text = format_started_message(phone=phone or "", contact=contact)
    log.info("call-started push call_id=%s phone=%s crm_hit=%s",
             call_id, phone, bool(contact))
    await tg_send_message(text, parse_mode=None)


def _resolve_business_label(entry: dict, phone: str) -> str:
    """Pick the best human-readable label for the prospect's business.
    Priority: captured-during-call > CRM business > CRM first_name > phone."""
    captured = (entry or {}).get("captured", {}) or {}
    contact = (entry or {}).get("contact") or {}
    biz = (captured.get("business") or "").strip()
    if biz:
        return biz
    biz = (contact.get("business") or "").strip()
    if biz:
        return biz
    first = (contact.get("first_name") or "").strip()
    if first:
        return first
    if phone:
        return phone
    return "Unknown"


def _resolve_transcript_label(entry: dict) -> str:
    """Build a 'First Name - Business' label for the transcript filename.
    Falls back gracefully when either part is missing."""
    captured = (entry or {}).get("captured", {}) or {}
    contact = (entry or {}).get("contact") or {}
    first = (contact.get("first_name") or "").strip()
    biz = (captured.get("business") or contact.get("business") or "").strip()
    parts = [p for p in [first, biz] if p]
    return " - ".join(parts)


async def _handle_end_of_call(message: dict) -> None:
    """Fired on end-of-call-report. Archives to VPS folder (if >= 30s) and
    pushes Telegram summary + audio + transcript. Idempotent via the
    pushed.flag sentinel in the folder."""
    phone = ((message.get("call") or {}).get("customer", {}) or {}).get("number", "")
    call_id = ((message.get("call") or {}).get("id")) or ""
    entry: dict = {}
    contact = None
    try:
        entry = await _ensure_contact_loaded(call_id, phone)
        contact = entry.get("contact")
    except Exception as e:
        log.warning("end-of-call cache/lookup failed phone=%s err=%s", phone, e)

    business_label = _resolve_business_label(entry, phone)
    t_label = _resolve_transcript_label(entry)
    folder = await archive_call(message, business_label=business_label,
                                transcript_label=t_label)
    if folder is None:
        # Under-30s skip — already logged inside archive_call.
        return

    if already_pushed(folder):
        log.info("end-of-call: telegram already pushed for %s — skipping resend",
                 folder.name)
        return

    summary_text = format_ended_summary(payload=message, contact=contact,
                                        folder_name=folder.name)
    summary_mid = await tg_send_message(summary_text, parse_mode=None)

    # Commit the pushed-flag the moment the SUMMARY message lands. The
    # summary is the operator-critical signal — if anything fails after this,
    # we accept a missing audio/transcript over a duplicate summary on the
    # next Vapi resend. (Real risk before this fix: a 60s audio upload timeout
    # left the flag unwritten → resend re-sent summary + transcript + audio.)
    if summary_mid:
        mark_pushed(folder)

    # Order: summary → transcript → audio. Three separate Telegram messages,
    # each pings. If parallel-call interleave becomes a problem, re-enable
    # reply_to_message_id + disable_notification threading.
    transcript = build_transcript(message)
    if transcript:
        if len(transcript) <= 3500:
            await tg_send_message(transcript, parse_mode=None)
        else:
            await tg_send_document(
                folder / transcript_filename(t_label), caption="transcript"
            )

    rec_path = folder / "recording.mp3"
    if rec_path.exists():
        await tg_send_audio(
            rec_path,
            caption=title_slash(message, business_label),
            filename=f"{title_dash(message, business_label)}.mp3",
        )
    else:
        log.warning("end-of-call: recording.mp3 missing for %s", folder.name)

    log.info("end-of-call: pushed Telegram for %s biz=%r", folder.name, business_label)


@app.post("/vapi/server-events")
async def vapi_server_events(
    request: Request,
    x_vapi_signature: Optional[str] = Header(default=None),
    x_vapi_secret: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Phone-number-level Vapi events: assistant-request, status-update,
    end-of-call-report, speech-update. Configure the phone number's
    `serverUrl` to point here. Always returns 200; never breaks the call."""
    raw = await request.body()
    sig = x_vapi_signature or x_vapi_secret
    if not _verify_signature(raw, sig):
        log.warning("server-events: rejected unsigned request len=%d", len(raw))
        return JSONResponse(status_code=401, content={"error": "invalid signature"})

    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        log.error("server-events: malformed JSON body")
        return JSONResponse(status_code=200, content={})

    message = body.get("message") or {}
    msg_type = message.get("type")

    if msg_type == "assistant-request":
        try:
            response = await _handle_assistant_request(message)
            return JSONResponse(content=response)
        except Exception as e:
            # NEVER block the call — return the default assistant on any failure.
            log.exception("assistant-request handler crash err=%s", e)
            return JSONResponse(content={"assistantId": _VOICSITO_ASSISTANT_ID})

    if msg_type == "status-update":
        status = (message.get("status") or "").lower()
        if status == "in-progress":
            try:
                await _handle_call_started(message)
            except Exception:
                log.exception("call-started handler crashed")
        return JSONResponse(content={})

    if msg_type == "end-of-call-report":
        try:
            await _handle_end_of_call(message)
        except Exception:
            log.exception("end-of-call handler crashed")
        return JSONResponse(content={})

    log.info("server-events ack type=%s", msg_type)
    return JSONResponse(content={})


# ---------------------------------------------------------------------------
# Outbound demo-confirmation (Type 4) — SEPARATE routes, mounted last so all
# shared helpers above are defined before outbound.py imports them.
#
# FAILSAFE: a missing or broken outbound.py must NEVER stop this app from
# starting — the inbound bot depends on it, and the deploy script has a history
# of silently omitting files. If the mount fails for ANY reason, log loudly and
# continue; the inbound routes stay fully operational.
# ---------------------------------------------------------------------------
try:
    from . import outbound as _outbound  # noqa: E402

    app.include_router(_outbound.router)
    log.info("outbound demo-confirmation routes mounted")
except Exception as _outbound_mount_err:  # noqa: BLE001 — inbound must survive
    log.error(
        "outbound routes NOT mounted — inbound is UNAFFECTED and running. err=%s",
        _outbound_mount_err,
    )

# ---------------------------------------------------------------------------
# GHL-facing trigger (POST /ghl/outbound-call). Separate module, separate
# secret: it is the only route here that is not Vapi calling us. Same failsafe
# as above — a broken trigger must never take the inbound bot down with it.
# ---------------------------------------------------------------------------
try:
    from . import ghl_trigger as _ghl_trigger  # noqa: E402

    app.include_router(_ghl_trigger.router)
    log.info("ghl outbound-call trigger route mounted")
except Exception as _ghl_trigger_mount_err:  # noqa: BLE001 — inbound must survive
    log.error(
        "ghl trigger route NOT mounted — inbound is UNAFFECTED and running. err=%s",
        _ghl_trigger_mount_err,
    )
