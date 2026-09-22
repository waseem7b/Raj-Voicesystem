"""GHL's standard payload already carries what we need.

A real workflow POST (2026-08-06) sent top-level `phone`, `company_name`,
`first_name`, `contact_id`, plus the assigned `user` and a `customData` object.
Reading the standard fields means the Custom Data section is optional, and a
mistyped custom key can't break the run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from webhook.fields import extract_fields  # noqa: E402

# Trimmed from the real GHL payload.
GHL_PAYLOAD = {
    "contact_id": "0Zd2QMpmBgwSs2ZMii8g",
    "first_name": "Waseem",
    "phone": "+1555010015",
    "company_name": "Cloud 7 Agency",
    "user": {"firstName": "Jon", "phone": "+1555010019"},
    "customData": {"phone": "+1555010015", "business Name": "Cloud 7 Agency"},
}


def test_reads_phone_and_business_from_the_standard_payload():
    fields = extract_fields(GHL_PAYLOAD)
    assert fields["phone"] == "+1555010015"
    assert fields["business_name"] == "Cloud 7 Agency"


def test_an_explicit_business_name_wins_over_company_name():
    fields = extract_fields({**GHL_PAYLOAD, "business_name": "Explicit Co"})
    assert fields["business_name"] == "Explicit Co"


def test_falls_back_to_custom_data_when_the_standard_field_is_missing():
    body = {"customData": {"phone": "+1555010010", "business_name": "Tree City"}}
    fields = extract_fields(body)
    assert fields["phone"] == "+1555010010"
    assert fields["business_name"] == "Tree City"


def test_tolerates_a_mistyped_custom_data_key():
    """'business Name' is what the GHL action actually sent."""
    body = {"phone": "+1555010010", "customData": {"business Name": "Tree City"}}
    assert extract_fields(body)["business_name"] == "Tree City"


def test_ignores_blank_values_that_ghl_sends_for_empty_fields():
    body = {"phone": "+1555010010", "company_name": "", "customData": {}}
    assert extract_fields(body)["business_name"] is None


def test_raises_when_no_phone_can_be_found_anywhere():
    with pytest.raises(ValueError, match="phone"):
        extract_fields({"first_name": "Waseem", "customData": {}})


def test_surfaces_the_assigned_user_as_the_closer():
    assert extract_fields(GHL_PAYLOAD)["closer_phone"] == "+1555010019"
