"""An outbound call must reach the operator chat NAMED.

Regression for the 2026-09-04 call that arrived as:

    Caller: +1555010014 / (cold caller)
    ID: 04-09-26 10-07 - +1555010014

despite GHL having the contact. Two causes: the metadata seed carried only a
contact id (no name), and it ran only from the tool-call handler — so a short
call where the model fired no tool never seeded anything at all.
"""
import asyncio

from src.webhooks.call_summary import _caller_line
from src.webhooks.outbound import _seed_contact_from_metadata
from src.webhooks.server import _resolve_business_label

PHONE = "+1555010014"


def _message(**meta):
    base = {"ghl_contact_id": "abc123", "call_type": "confirmation"}
    base.update(meta)
    return {"call": {"id": "call-1", "customer": {"number": PHONE},
                     "metadata": base}}


def test_seed_carries_the_name_not_just_the_id():
    entry: dict = {}
    asyncio.run(_seed_contact_from_metadata(
        entry, _message(ghl_first_name="Cordero", ghl_business="M Cutz Lawn Service",
                        ghl_city="Columbia")))
    c = entry["contact"]
    assert c["id"] == "abc123"
    assert c["first_name"] == "Cordero"
    assert c["business"] == "M Cutz Lawn Service"
    assert c["city"] == "Columbia"


def test_caller_line_names_the_prospect():
    entry: dict = {}
    asyncio.run(_seed_contact_from_metadata(
        entry, _message(ghl_first_name="Cordero", ghl_business="M Cutz Lawn Service",
                        ghl_city="Columbia")))
    line = _caller_line(PHONE, entry["contact"])
    assert "(cold caller)" not in line
    assert "M Cutz Lawn Service" in line


def test_folder_label_uses_the_name_not_the_phone():
    entry: dict = {}
    asyncio.run(_seed_contact_from_metadata(
        entry, _message(ghl_first_name="Cordero", ghl_business="M Cutz Lawn Service")))
    assert _resolve_business_label(entry, PHONE) == "M Cutz Lawn Service"


def test_first_name_only_still_beats_the_phone():
    """No business on the contact — the greeting name is still better than digits."""
    entry: dict = {}
    asyncio.run(_seed_contact_from_metadata(entry, _message(ghl_first_name="Cordero")))
    assert _resolve_business_label(entry, PHONE) == "Cordero"
    assert "(cold caller)" not in _caller_line(PHONE, entry["contact"])


def test_genuinely_unknown_caller_still_reads_as_cold():
    """No metadata identity at all -> the placeholder is still correct."""
    entry: dict = {}
    asyncio.run(_seed_contact_from_metadata(entry, {"call": {"metadata": {}}}))
    assert entry.get("contact") is None
    assert "(cold caller)" in _caller_line(PHONE, None)
