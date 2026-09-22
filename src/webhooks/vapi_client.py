"""Vapi REST client — outbound call origination.

Engineer-facing note: this is the ONLY place Voicsito originates a call. Every
other Vapi interaction in this repo is Vapi calling us (tool + event webhooks).

Caller-ID caveat, recorded because it is a real cost of this design: the number
the prospect sees is the Vapi number we dial from, NOT the GHL LC number Ashley
has been texting them on. Her opener leans on "I'm the one who's been texting
you", so those two identities being different is a known trade-off of API
origination over the GHL bridge (owner decision 2026-08-19). Importing the LC
number into Vapi, if GHL ever exposes Twilio credentials for it, would close the
gap without touching this module.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

VAPI_BASE = "https://api.vapi.ai"

# Vapi rejects a create-call in well under this; the ceiling exists so a hung
# connection can't hold a GHL webhook open past its retry timeout.
CREATE_CALL_TIMEOUT_S = 20.0


class VapiError(Exception):
    """Non-2xx from the Vapi API. Carries status + raw body for the log."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Vapi API error {status}: {body[:400]}")
        self.status = status
        self.body = body


def normalize_phone(raw: Any) -> Optional[str]:
    """Normalize to E.164 (US default). None when there aren't enough digits.

    Also rejects unrendered GHL merge fields — a workflow with no contact in
    scope ships the literal "{{contact.phone}}", and dialing that is a hard
    Vapi error rather than a silent no-op.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or (s.startswith("{{") and s.endswith("}}")):
        return None
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


async def create_outbound_call(
    *,
    assistant_id: str,
    customer_number: str,
    phone_number_id: Optional[str] = None,
    variable_values: Optional[dict[str, str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """Place an outbound call. Raises VapiError on a non-2xx.

    `variable_values` fills {{placeholders}} in the assistant's prompt — Vapi
    does not substitute them unless they arrive through this call.
    """
    api_key = os.getenv("VAPI_API_KEY")
    if not api_key:
        raise RuntimeError("VAPI_API_KEY is not set")
    number_id = phone_number_id or os.getenv("VAPI_PHONE_NUMBER_ID")
    if not number_id:
        raise RuntimeError("VAPI_PHONE_NUMBER_ID is not set")

    body: dict[str, Any] = {
        "assistantId": assistant_id,
        "phoneNumberId": number_id,
        "customer": {"number": customer_number},
    }
    if variable_values:
        body["assistantOverrides"] = {"variableValues": variable_values}
    if metadata:
        body["metadata"] = metadata

    async with httpx.AsyncClient(timeout=CREATE_CALL_TIMEOUT_S) as client:
        res = await client.post(
            f"{VAPI_BASE}/call",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if res.status_code >= 300:
        raise VapiError(res.status_code, res.text)
    return res.json()
