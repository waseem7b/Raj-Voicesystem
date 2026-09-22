"""Pull what we need out of a GoHighLevel workflow POST.

GHL sends its whole standard contact payload alongside whatever Custom Data the
action defines, so the fields we want are usually already there under their
standard names. Preferring those means the Custom Data section is optional and a
mistyped custom key degrades instead of breaking.
"""

from __future__ import annotations

from typing import Any

# Custom Data keys seen in the wild, including the mistyped one GHL sent.
BUSINESS_KEYS = ("business_name", "business Name", "businessName", "business name")
PHONE_KEYS = ("phone", "phone_number", "contact_phone")


def _first_nonblank(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _from_keys(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    return _first_nonblank(*(source.get(k) for k in keys))


def extract_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Return {phone, business_name, closer_phone, contact_id, first_name}.

    Raises ValueError when no phone is present anywhere — without it there is no
    way to find the prospect's group.
    """
    custom = body.get("customData") or {}

    phone = _first_nonblank(
        body.get("phone"),
        _from_keys(custom, PHONE_KEYS),
        *(body.get(k) for k in PHONE_KEYS),
    )
    if not phone:
        raise ValueError("no phone in payload (checked top level and customData)")

    business_name = _first_nonblank(
        body.get("business_name"),
        _from_keys(custom, BUSINESS_KEYS),
        body.get("company_name"),
    )

    user = body.get("user") or {}
    return {
        "phone": phone,
        "business_name": business_name,
        "closer_phone": _first_nonblank(user.get("phone")),
        "contact_id": _first_nonblank(body.get("contact_id")),
        "first_name": _first_nonblank(body.get("first_name")),
    }
