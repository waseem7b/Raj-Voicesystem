"""TDD tests for the timezone resolver.

Resolution chain:
  1. Stored IANA timezone wins when present and valid.
  2. Else derive from (city, state) — handles 10+ split states with city overrides.
  3. Else log error + return Eastern default. (Should ~never fire in production.)

Never returns America/Bogota — that's a wrong-time-to-the-prospect failure mode.
"""
import pytest

from src.webhooks.tz_resolver import resolve_timezone


# --------------------------------------------------------------------------- #
# Stored TZ wins
# --------------------------------------------------------------------------- #
def test_stored_iana_tz_wins_over_city_state():
    """When the contact has a valid IANA timezone, ignore city/state."""
    assert resolve_timezone(
        stored_tz="America/Los_Angeles",
        city="Miami",  # city says Eastern
        state="FL",
    ) == "America/Los_Angeles"


def test_stored_tz_blank_falls_through_to_state():
    assert resolve_timezone(stored_tz="", city="", state="CA") == "America/Los_Angeles"


def test_stored_tz_invalid_falls_through_to_state():
    """If stored TZ is garbage (e.g. 'PST', 'Eastern Standard Time'), drop it
    and use city/state instead — don't pass garbage to GHL."""
    assert resolve_timezone(stored_tz="PST", city="", state="CA") == "America/Los_Angeles"
    assert resolve_timezone(stored_tz="Eastern Standard Time", city="", state="NY") == "America/New_York"


# --------------------------------------------------------------------------- #
# State normalization
# --------------------------------------------------------------------------- #
def test_state_2letter_uppercase():
    assert resolve_timezone("", "", "CA") == "America/Los_Angeles"


def test_state_2letter_lowercase():
    assert resolve_timezone("", "", "ca") == "America/Los_Angeles"


def test_state_full_name():
    assert resolve_timezone("", "", "California") == "America/Los_Angeles"


def test_state_full_name_lowercase():
    assert resolve_timezone("", "", "california") == "America/Los_Angeles"


# --------------------------------------------------------------------------- #
# Single-TZ states — sample of each US zone
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("state,expected", [
    ("CA", "America/Los_Angeles"),
    ("WA", "America/Los_Angeles"),
    ("NV", "America/Los_Angeles"),
    ("AZ", "America/Phoenix"),       # no DST
    ("CO", "America/Denver"),
    ("UT", "America/Denver"),
    ("WY", "America/Denver"),
    ("MT", "America/Denver"),
    ("NM", "America/Denver"),
    ("IL", "America/Chicago"),
    ("WI", "America/Chicago"),
    ("MN", "America/Chicago"),
    ("IA", "America/Chicago"),
    ("MO", "America/Chicago"),
    ("AR", "America/Chicago"),
    ("LA", "America/Chicago"),
    ("MS", "America/Chicago"),
    ("AL", "America/Chicago"),
    ("OK", "America/Chicago"),
    ("NY", "America/New_York"),
    ("NJ", "America/New_York"),
    ("PA", "America/New_York"),
    ("CT", "America/New_York"),
    ("MA", "America/New_York"),
    ("OH", "America/New_York"),
    ("VA", "America/New_York"),
    ("NC", "America/New_York"),
    ("SC", "America/New_York"),
    ("GA", "America/New_York"),
    ("ME", "America/New_York"),
    ("HI", "Pacific/Honolulu"),
    ("AK", "America/Anchorage"),
])
def test_single_tz_states(state, expected):
    assert resolve_timezone("", "", state) == expected


# --------------------------------------------------------------------------- #
# Split-state states — city overrides
# --------------------------------------------------------------------------- #
def test_texas_default_central_el_paso_mountain():
    assert resolve_timezone("", "Houston", "TX") == "America/Chicago"
    assert resolve_timezone("", "Dallas", "TX") == "America/Chicago"
    assert resolve_timezone("", "El Paso", "TX") == "America/Denver"


def test_florida_default_eastern_panhandle_central():
    assert resolve_timezone("", "Miami", "FL") == "America/New_York"
    assert resolve_timezone("", "Orlando", "FL") == "America/New_York"
    assert resolve_timezone("", "Pensacola", "FL") == "America/Chicago"
    assert resolve_timezone("", "Panama City", "FL") == "America/Chicago"


def test_kentucky_default_eastern_west_central():
    assert resolve_timezone("", "Louisville", "KY") == "America/New_York"
    assert resolve_timezone("", "Lexington", "KY") == "America/New_York"
    assert resolve_timezone("", "Paducah", "KY") == "America/Chicago"
    assert resolve_timezone("", "Bowling Green", "KY") == "America/Chicago"


def test_tennessee_default_central_east_eastern():
    assert resolve_timezone("", "Nashville", "TN") == "America/Chicago"
    assert resolve_timezone("", "Memphis", "TN") == "America/Chicago"
    assert resolve_timezone("", "Knoxville", "TN") == "America/New_York"
    assert resolve_timezone("", "Chattanooga", "TN") == "America/New_York"


def test_indiana_default_eastern_evansville_central():
    assert resolve_timezone("", "Indianapolis", "IN") == "America/New_York"
    assert resolve_timezone("", "Evansville", "IN") == "America/Chicago"


def test_south_dakota_default_central_rapid_city_mountain():
    assert resolve_timezone("", "Sioux Falls", "SD") == "America/Chicago"
    assert resolve_timezone("", "Rapid City", "SD") == "America/Denver"


def test_nebraska_default_central_scottsbluff_mountain():
    assert resolve_timezone("", "Omaha", "NE") == "America/Chicago"
    assert resolve_timezone("", "Lincoln", "NE") == "America/Chicago"
    assert resolve_timezone("", "Scottsbluff", "NE") == "America/Denver"


def test_idaho_default_mountain_north_pacific():
    assert resolve_timezone("", "Boise", "ID") == "America/Denver"
    assert resolve_timezone("", "Coeur d'Alene", "ID") == "America/Los_Angeles"


def test_oregon_default_pacific():
    assert resolve_timezone("", "Portland", "OR") == "America/Los_Angeles"


def test_michigan_default_eastern():
    assert resolve_timezone("", "Detroit", "MI") == "America/New_York"
    assert resolve_timezone("", "Grand Rapids", "MI") == "America/New_York"


def test_split_state_unknown_city_uses_state_default():
    """For a split state, an unrecognized city falls back to the state's
    dominant TZ — never crashes, never falls back to Bogota."""
    assert resolve_timezone("", "Some Tiny Town", "TX") == "America/Chicago"
    assert resolve_timezone("", "Unknown Place", "FL") == "America/New_York"


def test_city_lookup_case_insensitive():
    assert resolve_timezone("", "el paso", "tx") == "America/Denver"
    assert resolve_timezone("", "EL PASO", "TX") == "America/Denver"


# --------------------------------------------------------------------------- #
# Hard-default — never Bogota
# --------------------------------------------------------------------------- #
def test_empty_everything_returns_eastern_default(caplog):
    """If we know NOTHING about the prospect, default to Eastern (most US
    contractors) and log loudly. NEVER return America/Bogota."""
    import logging
    caplog.set_level(logging.WARNING)
    result = resolve_timezone("", "", "")
    assert result == "America/New_York"
    # Must NEVER return Bogota — that's a silent wrong-time bug.
    assert result != "America/Bogota"
    # Must log at WARNING or higher so this is visible in production.
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_unknown_state_returns_eastern_default():
    """A non-US state code (e.g. country only, or typo) defaults to Eastern."""
    assert resolve_timezone("", "", "ZZ") == "America/New_York"
    assert resolve_timezone("", "", "XYZ") == "America/New_York"


def test_never_returns_bogota_for_any_us_input():
    """Sweep — every US state code must resolve to a non-Bogota IANA zone."""
    us_states = [
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
        "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
        "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
        "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
        "WI", "WY",
    ]
    for s in us_states:
        tz = resolve_timezone("", "", s)
        assert tz != "America/Bogota", f"{s} resolved to Bogota — never acceptable"
        assert tz.startswith(("America/", "Pacific/")), f"{s} resolved to non-IANA-US: {tz}"
