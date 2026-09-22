"""Deep edge-case tests for the timezone resolver.

Triple-check: every weird value GHL or the cache could ever hand us must
funnel cleanly to a non-Bogota IANA timezone. No exceptions, no Bogota,
no None returned.
"""
import logging
import random
import string

import pytest

from src.webhooks.tz_resolver import resolve_timezone, resolve_timezone_with_source


# --------------------------------------------------------------------------- #
# None / missing / whitespace inputs — every field can be None per JSON
# --------------------------------------------------------------------------- #
def test_none_stored_tz_falls_through_to_state():
    assert resolve_timezone(None, "", "CA") == "America/Los_Angeles"


def test_none_city_with_state_uses_state_default():
    assert resolve_timezone("", None, "CA") == "America/Los_Angeles"


def test_none_state_alone_returns_eastern_default():
    """City alone is not enough — we need state context to disambiguate."""
    assert resolve_timezone("", "Los Angeles", None) == "America/New_York"


def test_all_none_inputs_return_eastern():
    assert resolve_timezone(None, None, None) == "America/New_York"


def test_whitespace_only_stored_tz_falls_through():
    assert resolve_timezone("   ", "", "CA") == "America/Los_Angeles"
    assert resolve_timezone("\t\n", "", "NY") == "America/New_York"


def test_whitespace_only_state_returns_eastern():
    assert resolve_timezone("", "", "   ") == "America/New_York"


# --------------------------------------------------------------------------- #
# Whitespace tolerance — GHL data is never clean
# --------------------------------------------------------------------------- #
def test_iana_with_leading_trailing_whitespace_accepted():
    assert resolve_timezone("  America/Los_Angeles  ", "", "") == "America/Los_Angeles"
    assert resolve_timezone("America/New_York\n", "", "") == "America/New_York"


def test_state_with_whitespace_normalized():
    assert resolve_timezone("", "", "  CA  ") == "America/Los_Angeles"
    assert resolve_timezone("", "", " california ") == "America/Los_Angeles"


def test_city_with_whitespace_for_split_state():
    assert resolve_timezone("", "  El Paso  ", "TX") == "America/Denver"


# --------------------------------------------------------------------------- #
# Common malformed TZ strings — every one must drop and use state fallback
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_tz", [
    "PST", "EST", "CST", "MST",                          # 3-letter abbreviations (fixed-offset!)
    "PDT", "EDT", "CDT", "MDT",                          # DST abbreviations
    "HST", "AKST",                                       # other fixed aliases
    "Eastern Standard Time", "Pacific Standard Time",    # Windows zones
    "Eastern", "Pacific", "Central",                     # bare names
    "UTC-05:00", "GMT-5", "GMT+5", "+05:00", "-05:00",  # offset strings
    "America/Bogota_Lite",                               # made-up
    "asia/tokyo",                                        # case-sensitive
    "AMERICA/NEW_YORK",                                  # all-caps
    "UTC", "Z", "GMT",                                   # universal aliases
    "US/Eastern", "US/Pacific", "US/Central",            # legacy `US/` aliases
    "Etc/GMT-5", "Etc/UTC",                              # Etc namespace
])
def test_malformed_stored_tz_falls_through_to_state(bad_tz):
    """When stored TZ is anything but a geographic IANA zone (e.g. EST, US/Eastern,
    UTC, fixed-offset strings), drop it. Fixed-offset aliases like 'EST' don't
    observe DST and would silently mis-time a US prospect by one hour in summer."""
    result = resolve_timezone(bad_tz, "", "CA")
    assert result == "America/Los_Angeles", \
        f"bad_tz={bad_tz!r} should fall through to state CA → America/Los_Angeles, got {result!r}"


def test_legacy_us_eastern_alias_falls_through():
    """`US/Eastern` IS a working ZoneInfo alias but we deliberately reject it:
    legacy `US/` namespace is deprecated and we want all stored TZs to be in
    geographic form (`America/New_York`) for consistency in logs and telemetry."""
    assert resolve_timezone("US/Eastern", "", "NY") == "America/New_York"


# --------------------------------------------------------------------------- #
# Garbage in state slot — must never crash, must always default
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_state", [
    "ZZ", "XYZ", "123", "!!!", "California1", "N.Y.",
    "Cal", "Texass", "Floridaaaa", "ny ", " ny",
])
def test_unrecognized_state_returns_eastern_never_crashes(bad_state):
    result = resolve_timezone("", "", bad_state)
    assert result == "America/New_York"
    assert result != "America/Bogota"


def test_state_with_periods_full_name():
    """'N.Y.' isn't a recognized form — falls through to default. We don't
    try to be too clever about munging."""
    assert resolve_timezone("", "", "N.Y.") == "America/New_York"


# --------------------------------------------------------------------------- #
# Mixed signal — partial data should still resolve
# --------------------------------------------------------------------------- #
def test_invalid_stored_tz_with_split_state_and_known_city():
    """Garbage TZ, but split state + known city → should pick the city's zone."""
    assert resolve_timezone("PST", "El Paso", "TX") == "America/Denver"


def test_invalid_stored_tz_with_split_state_unknown_city():
    """Garbage TZ + split state + unknown city → state default."""
    assert resolve_timezone("garbage", "Some Town", "TX") == "America/Chicago"


def test_valid_stored_tz_overrides_conflicting_state():
    """If stored TZ is valid, it wins even if state would suggest otherwise.
    This is a feature: trust the source of truth (the dedicated TZ field)."""
    assert resolve_timezone("America/New_York", "Los Angeles", "CA") == "America/New_York"


# --------------------------------------------------------------------------- #
# Fuzz sweep — random garbage strings must never produce Bogota or crash
# --------------------------------------------------------------------------- #
def _random_garbage(rng: random.Random) -> str:
    n = rng.randint(0, 30)
    chars = string.printable
    return "".join(rng.choice(chars) for _ in range(n))


def test_fuzz_resolver_never_returns_bogota_or_crashes():
    """500 random garbage tuples — must always return a sane IANA non-Bogota tz."""
    rng = random.Random(42)
    valid_states = ["CA", "TX", "NY", "FL", "WA", "IL", "AZ", "GA", "OH", "PA"]

    for _ in range(500):
        stored = _random_garbage(rng)
        city = _random_garbage(rng)
        state = rng.choice(valid_states + [_random_garbage(rng), ""])
        result = resolve_timezone(stored, city, state)

        assert result, f"empty result for inputs ({stored!r},{city!r},{state!r})"
        assert result != "America/Bogota", \
            f"resolved to Bogota for ({stored!r},{city!r},{state!r})"
        assert "/" in result, \
            f"non-IANA shape {result!r} for ({stored!r},{city!r},{state!r})"


def test_fuzz_split_state_resolver_consistent():
    """For each split state, every random city falls back to the state default
    when the city isn't a known override."""
    rng = random.Random(99)
    expected_state_default = {
        "TX": "America/Chicago", "FL": "America/New_York",
        "KY": "America/New_York", "TN": "America/Chicago",
        "IN": "America/New_York", "SD": "America/Chicago",
        "NE": "America/Chicago", "ID": "America/Denver",
        "OR": "America/Los_Angeles", "MI": "America/New_York",
        "ND": "America/Chicago", "KS": "America/Chicago",
    }
    for state, default_tz in expected_state_default.items():
        for _ in range(20):
            random_city = _random_garbage(rng)
            # Skip if our garbage happens to look like a real override key.
            from src.webhooks.tz_resolver import _CITY_OVERRIDES
            if (state, random_city.strip().lower()) in _CITY_OVERRIDES:
                continue
            assert resolve_timezone("", random_city, state) == default_tz


# --------------------------------------------------------------------------- #
# Determinism — same input always returns same output
# --------------------------------------------------------------------------- #
def test_resolver_is_deterministic():
    """100 calls with the same inputs must produce the same output."""
    inputs = [
        ("", "", "CA"),
        ("", "El Paso", "TX"),
        ("America/Phoenix", "Anywhere", "AZ"),
        (None, None, None),
        ("PST", "Brooklyn", "NY"),
    ]
    for inp in inputs:
        first = resolve_timezone(*inp)
        for _ in range(99):
            assert resolve_timezone(*inp) == first


# --------------------------------------------------------------------------- #
# Output shape — always an IANA tz, always non-empty, always usable
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Multi-slash IANA zones (Indiana sub-zones, Puerto Rico, etc.)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tz", [
    "America/Indiana/Indianapolis",
    "America/Indiana/Knox",
    "America/Indiana/Vincennes",
    "America/Kentucky/Louisville",
    "America/Kentucky/Monticello",
    "America/North_Dakota/Center",
    "America/North_Dakota/New_Salem",
    "America/Argentina/Buenos_Aires",
    "America/Puerto_Rico",
    "America/Detroit",
    "America/Boise",
])
def test_validator_accepts_multi_slash_iana_zones(tz):
    """IANA has multi-slash zone names (sub-regional). The validator must not
    reject these — they're proper IANA, not legacy aliases."""
    assert resolve_timezone(tz, "", "") == tz


# --------------------------------------------------------------------------- #
# Source signal — caller can tell which path fired
# --------------------------------------------------------------------------- #
def test_source_stored_when_valid_iana_present():
    _, src = resolve_timezone_with_source("America/Los_Angeles", "", "")
    assert src == "stored"


def test_source_city_for_split_state_override():
    _, src = resolve_timezone_with_source("", "El Paso", "TX")
    assert src == "city"


def test_source_state_for_state_default():
    _, src = resolve_timezone_with_source("", "", "CA")
    assert src == "state"


def test_source_state_for_split_state_unknown_city():
    _, src = resolve_timezone_with_source("", "Nowheresville", "TX")
    assert src == "state"


def test_source_default_when_nothing():
    tz, src = resolve_timezone_with_source("", "", "")
    assert src == "default"
    assert tz == "America/New_York"


def test_source_default_when_invalid_state_only():
    _, src = resolve_timezone_with_source("", "", "ZZ")
    assert src == "default"


# --------------------------------------------------------------------------- #
# Arizona Navajo Nation — observes DST while rest of AZ doesn't
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("city", [
    "Window Rock", "window rock", "WINDOW ROCK",
    "Kayenta", "Tuba City", "Chinle", "Fort Defiance", "Shonto",
])
def test_arizona_navajo_nation_observes_dst(city):
    """Navajo Nation cities in NE Arizona observe DST. They must resolve to
    America/Denver (Mountain), not America/Phoenix (no DST)."""
    assert resolve_timezone("", city, "AZ") == "America/Denver"


def test_arizona_phoenix_default():
    """Phoenix and the rest of AZ stay on Phoenix (no DST)."""
    assert resolve_timezone("", "Phoenix", "AZ") == "America/Phoenix"
    assert resolve_timezone("", "Tucson", "AZ") == "America/Phoenix"
    assert resolve_timezone("", "Flagstaff", "AZ") == "America/Phoenix"


# --------------------------------------------------------------------------- #
# Florida panhandle — added cities
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("city,expected", [
    ("Crestview", "America/Chicago"),
    ("Milton", "America/Chicago"),
    ("Marianna", "America/Chicago"),
    ("DeFuniak Springs", "America/Chicago"),
    ("Niceville", "America/Chicago"),
    ("Gulf Breeze", "America/Chicago"),
    # Eastern Florida defaults stay Eastern.
    ("Tallahassee", "America/New_York"),
    ("Jacksonville", "America/New_York"),
    ("Tampa", "America/New_York"),
])
def test_florida_panhandle_central_rest_eastern(city, expected):
    assert resolve_timezone("", city, "FL") == expected


# --------------------------------------------------------------------------- #
# Apostrophe-handling — Coeur d'Alene and the no-apostrophe variant
# --------------------------------------------------------------------------- #
def test_coeur_dalene_apostrophe_and_without():
    assert resolve_timezone("", "Coeur d'Alene", "ID") == "America/Los_Angeles"
    assert resolve_timezone("", "coeur d'alene", "ID") == "America/Los_Angeles"
    assert resolve_timezone("", "COEUR D'ALENE", "ID") == "America/Los_Angeles"
    # No-apostrophe variant — humans type it both ways.
    assert resolve_timezone("", "Coeur dAlene", "ID") == "America/Los_Angeles"
    assert resolve_timezone("", "coeur dalene", "ID") == "America/Los_Angeles"


# --------------------------------------------------------------------------- #
# Fuzz with None substitution — production failure mode (JSON null)
# --------------------------------------------------------------------------- #
def test_fuzz_with_none_substitution_never_crashes_or_returns_bogota():
    """Mix random garbage + None values in any of the 3 inputs.
    Must never crash, never return Bogota, always return a /-shaped IANA tz."""
    rng = random.Random(123)
    valid_states = ["CA", "TX", "NY", "FL", "WA", "IL", "AZ", "GA", "OH", "PA", ""]

    def maybe_none(rng, value):
        return None if rng.random() < 0.3 else value

    for _ in range(500):
        stored = maybe_none(rng, _random_garbage(rng))
        city = maybe_none(rng, _random_garbage(rng))
        state = maybe_none(rng, rng.choice(valid_states))

        result = resolve_timezone(stored, city, state)
        assert result, f"empty result for ({stored!r},{city!r},{state!r})"
        assert result != "America/Bogota"
        assert "/" in result


# --------------------------------------------------------------------------- #
# Hard-default ERROR-level log
# --------------------------------------------------------------------------- #
def test_default_path_logs_at_error_level(caplog):
    """The hard-default branch must log at ERROR (not just WARNING) so it
    surfaces in any production alerting tied to ERROR-level events."""
    caplog.set_level(logging.ERROR)
    resolve_timezone("", "", "")
    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "hard-default branch must emit an ERROR-level log"


def test_output_is_always_a_loadable_iana_zone():
    """Every output must round-trip through ZoneInfo without raising."""
    from zoneinfo import ZoneInfo

    inputs = [
        ("America/Los_Angeles", "", ""),
        ("", "", "CA"),
        ("", "El Paso", "TX"),
        ("PST", "", "NY"),
        ("", "", ""),
        (None, None, None),
        ("", "", "ZZ"),
    ]
    for inp in inputs:
        result = resolve_timezone(*inp)
        # Must not raise.
        ZoneInfo(result)
