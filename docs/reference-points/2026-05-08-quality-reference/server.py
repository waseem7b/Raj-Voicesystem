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
from datetime import datetime
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
async def _handle_update_prospect_info(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    first_name = (args.get("first_name") or "").strip()
    last_name = (args.get("last_name") or "").strip()
    business = (args.get("business_name") or args.get("business") or "").strip()
    email = (args.get("email") or "").strip()

    entry = await _ensure_contact_loaded(call_id, phone)
    captured = entry["captured"]
    if first_name:
        captured["first_name"] = first_name
    if last_name:
        captured["last_name"] = last_name
    if business:
        captured["business"] = business
    if email:
        captured["email"] = email

    contact = entry.get("contact")
    if not contact or not contact.get("id"):
        log.info(
            "update_prospect_info cached only call_id=%s name=%r business=%r email=%r",
            call_id, first_name, business, email,
        )
        return "Info cached — will be saved when contact is created at booking time."

    try:
        result = await ghl.update_prospect_fields(
            contact_id=contact["id"],
            crm_key=contact["crm"],
            first_name=first_name,
            last_name=last_name,
            business=business,
            email=email,
        )
        if result.get("ok"):
            return "Updated."
        return f"Update failed: {result.get('error')}"
    except Exception as e:
        log.error("update_prospect_info error call_id=%s err=%s", call_id, e)
        return f"Update error: {str(e)[:120]}"


async def _handle_get_available_slots(args: dict) -> str:
    days_ahead = int(args.get("days_ahead") or 7)
    try:
        slots = await ghl.get_available_slots(days_ahead=days_ahead)
    except Exception as e:
        log.error("get_available_slots error err=%s", e)
        return "Error fetching calendar — try again or just collect their preferred time."

    lines: list[str] = []
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
                if iso_for_day is None:
                    iso_for_day = iso
            except Exception:
                pretty.append(iso)
        if iso_for_day:
            day_name = datetime.fromisoformat(iso_for_day).strftime("%A %b %-d")
            lines.append(f"{day_name}: " + ", ".join(pretty))

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


async def _handle_book_video_call(
    args: dict, call_id: str, phone: Optional[str]
) -> str:
    slot_iso = (args.get("slot_iso") or "").strip()
    email = (args.get("email") or "").strip()
    prospect_first_name = (args.get("prospect_first_name") or args.get("first_name") or "").strip()
    business_name = (args.get("business_name") or args.get("business") or "").strip()

    if not slot_iso:
        return "Booking error: slot_iso is required."
    if not email:
        return "Booking error: email is required."

    entry = await _ensure_contact_loaded(call_id, phone)
    captured = entry["captured"]
    prospect_first_name = prospect_first_name or captured.get("first_name", "")
    business_name = business_name or captured.get("business", "")
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
    main_contact = await ghl.wait_for_main_crm_contact(
        phone=resolved_phone, max_attempts=4, interval_seconds=12.0
    )
    if not main_contact or not main_contact.get("id"):
        log.error("main CRM contact missing after transfer call_id=%s phone=%s", call_id, resolved_phone)
        return "Booking failed: contact not in main CRM yet — please try again in 30 seconds."
    main_contact_id = main_contact["id"]

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
            result_str = await _handle_get_available_slots(args)
        elif name == "mark_for_transfer":
            result_str = await _handle_mark_for_transfer(args, call_id, phone)
        elif name == "book_video_call":
            result_str = await _handle_book_video_call(args, call_id, phone)
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

    # Run all tool calls in parallel (Vapi may batch multiple per request).
    results = await asyncio.gather(
        *[_dispatch_tool_call(tc, message, call_id, phone) for tc in tool_calls],
        return_exceptions=False,
    )
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


async def _handle_assistant_request(message: dict) -> dict:
    """Build the Vapi assistant-request response.
    Pre-populates {{biz_type}} and {{business}} variables from the caller's
    texts-CRM contact. Falls back to default assistant (no overrides) if
    lookup fails or contact not found — the call still goes through, just
    without industry adaptation.

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
        log.info("assistant-request no phone — using default assistant")
        return {
            "assistantId": _VOICSITO_ASSISTANT_ID,
            "assistantOverrides": {"variableValues": overrides_vars},
        }

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
            "assistant-request hit phone=%s biz_type=%r business=%r city=%r state=%r crm=%s",
            phone, overrides_vars["biz_type"], overrides_vars["business"],
            overrides_vars["city"], overrides_vars["state"], contact.get("crm"),
        )
    else:
        log.info("assistant-request miss phone=%s — default vars", phone)

    return {
        "assistantId": _VOICSITO_ASSISTANT_ID,
        "assistantOverrides": {"variableValues": overrides_vars},
    }


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

    # Acknowledge all other phone-number-level events (status-update,
    # end-of-call-report, speech-update, etc.). We don't act on them yet.
    log.info("server-events ack type=%s", msg_type)
    return JSONResponse(content={})
