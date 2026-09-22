"""Timezone resolution for prospect contacts.

Resolution chain (never falls back to Bogota — that's a silent wrong-time bug):
  1. Stored IANA TZ on the GHL contact (`contact.timezone`). 99.9% case.
  2. Derive from city + state. Handles the ~10 US states that span two zones
     by falling back to a per-state default for unrecognized cities.
  3. Hard default `America/New_York` + ERROR log. Should ~never fire.

US state coverage only — Voicsito's customer base is US contractors.

Returns the source of the resolution alongside the timezone via
`resolve_timezone_with_source()` so callers can log/alert when fallback fires.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("voicsito.tz")

HARD_DEFAULT_TZ = "America/New_York"

# --------------------------------------------------------------------------- #
# State → IANA TZ.  Single-zone states map directly. Split states map to their
# DOMINANT zone (where the population lives); city overrides handle the rest.
# --------------------------------------------------------------------------- #
_STATE_TZ: dict[str, str] = {
    # Pacific
    "CA": "America/Los_Angeles",
    "WA": "America/Los_Angeles",
    "NV": "America/Los_Angeles",
    "OR": "America/Los_Angeles",     # split: Malheur County is Mountain
    # Mountain (no DST)
    "AZ": "America/Phoenix",
    # Mountain
    "CO": "America/Denver",
    "UT": "America/Denver",
    "WY": "America/Denver",
    "MT": "America/Denver",
    "NM": "America/Denver",
    "ID": "America/Denver",          # split: northern panhandle is Pacific
    # Central
    "IL": "America/Chicago",
    "WI": "America/Chicago",
    "MN": "America/Chicago",
    "IA": "America/Chicago",
    "MO": "America/Chicago",
    "AR": "America/Chicago",
    "LA": "America/Chicago",
    "MS": "America/Chicago",
    "AL": "America/Chicago",
    "OK": "America/Chicago",
    "KS": "America/Chicago",         # split: 4 westernmost counties are Mountain
    "NE": "America/Chicago",         # split: panhandle is Mountain
    "ND": "America/Chicago",         # split: far southwest is Mountain
    "SD": "America/Chicago",         # split: western half is Mountain
    "TX": "America/Chicago",         # split: El Paso area is Mountain
    "TN": "America/Chicago",         # split: eastern half is Eastern
    # Eastern
    "NY": "America/New_York",
    "NJ": "America/New_York",
    "PA": "America/New_York",
    "CT": "America/New_York",
    "MA": "America/New_York",
    "RI": "America/New_York",
    "VT": "America/New_York",
    "NH": "America/New_York",
    "ME": "America/New_York",
    "OH": "America/New_York",
    "MD": "America/New_York",
    "DE": "America/New_York",
    "WV": "America/New_York",
    "VA": "America/New_York",
    "NC": "America/New_York",
    "SC": "America/New_York",
    "GA": "America/New_York",
    "FL": "America/New_York",        # split: panhandle is Central
    "MI": "America/New_York",        # split: 4 UP counties are Central
    "IN": "America/New_York",        # split: NW corner + Evansville are Central
    "KY": "America/New_York",        # split: western third is Central
    # Off-mainland
    "HI": "Pacific/Honolulu",
    "AK": "America/Anchorage",
    "DC": "America/New_York",
}

# Lowercase full-name → 2-letter code, so we accept either form on input.
_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN",
    "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

# City overrides for split-zone states. Key is (state_code, lower(city)).
# Only include cities whose TZ DIFFERS from the state default. Major metros first.
_CITY_OVERRIDES: dict[tuple[str, str], str] = {
    # Texas — default Central. El Paso area is Mountain.
    ("TX", "el paso"): "America/Denver",
    ("TX", "van horn"): "America/Denver",          # Culberson County
    ("TX", "sierra blanca"): "America/Denver",     # Hudspeth County seat
    # Florida — default Eastern. Panhandle is Central.
    ("FL", "pensacola"): "America/Chicago",
    ("FL", "panama city"): "America/Chicago",
    ("FL", "fort walton beach"): "America/Chicago",
    ("FL", "destin"): "America/Chicago",
    ("FL", "navarre"): "America/Chicago",
    ("FL", "crestview"): "America/Chicago",
    ("FL", "milton"): "America/Chicago",
    ("FL", "marianna"): "America/Chicago",
    ("FL", "defuniak springs"): "America/Chicago",
    ("FL", "niceville"): "America/Chicago",
    ("FL", "gulf breeze"): "America/Chicago",
    # Arizona — default Phoenix (no DST). Navajo Nation observes DST.
    ("AZ", "window rock"): "America/Denver",
    ("AZ", "kayenta"): "America/Denver",
    ("AZ", "tuba city"): "America/Denver",
    ("AZ", "chinle"): "America/Denver",
    ("AZ", "fort defiance"): "America/Denver",
    ("AZ", "shonto"): "America/Denver",
    # Kentucky — default Eastern. Western third is Central.
    ("KY", "paducah"): "America/Chicago",
    ("KY", "bowling green"): "America/Chicago",
    ("KY", "owensboro"): "America/Chicago",
    ("KY", "henderson"): "America/Chicago",
    ("KY", "hopkinsville"): "America/Chicago",
    # Tennessee — default Central. Eastern half is Eastern.
    ("TN", "knoxville"): "America/New_York",
    ("TN", "chattanooga"): "America/New_York",
    ("TN", "kingsport"): "America/New_York",
    ("TN", "johnson city"): "America/New_York",
    ("TN", "bristol"): "America/New_York",
    # Indiana — default Eastern. NW corner + Evansville are Central.
    ("IN", "evansville"): "America/Chicago",
    ("IN", "gary"): "America/Chicago",
    ("IN", "hammond"): "America/Chicago",
    ("IN", "jasper"): "America/Chicago",
    # South Dakota — default Central. Western half is Mountain.
    ("SD", "rapid city"): "America/Denver",
    ("SD", "spearfish"): "America/Denver",
    ("SD", "sturgis"): "America/Denver",
    # Nebraska — default Central. Panhandle is Mountain.
    ("NE", "scottsbluff"): "America/Denver",
    ("NE", "alliance"): "America/Denver",
    ("NE", "chadron"): "America/Denver",
    # Idaho — default Mountain. Northern panhandle is Pacific.
    ("ID", "coeur d'alene"): "America/Los_Angeles",
    ("ID", "coeur dalene"): "America/Los_Angeles",
    ("ID", "post falls"): "America/Los_Angeles",
    ("ID", "sandpoint"): "America/Los_Angeles",
    ("ID", "lewiston"): "America/Los_Angeles",
    ("ID", "moscow"): "America/Los_Angeles",
    # Michigan — default Eastern. 4 UP counties are Central.
    ("MI", "iron mountain"): "America/Chicago",
    ("MI", "menominee"): "America/Chicago",
    ("MI", "ironwood"): "America/Chicago",
    # North Dakota — default Central. Far SW is Mountain.
    ("ND", "dickinson"): "America/Denver",
    # Kansas — default Central. 4 western counties are Mountain.
    ("KS", "goodland"): "America/Denver",
    # Oregon — default Pacific. Eastern Malheur County is Mountain.
    ("OR", "ontario"): "America/Denver",
}


def _normalize_state(state: str) -> str:
    """Return uppercase 2-letter US state code, or '' if unrecognizable."""
    s = (state or "").strip()
    if not s:
        return ""
    if len(s) == 2:
        code = s.upper()
        return code if code in _STATE_TZ else ""
    code = _STATE_NAME_TO_CODE.get(s.lower())
    return code or ""


# Geographic region prefixes for proper IANA zones. The legacy fixed-offset
# aliases (EST, MST, UTC, GMT, US/Eastern, etc.) are intentionally REJECTED:
# zones like `EST` are fixed at UTC-5 year-round and would silently cost a
# DST-observing prospect one hour in summer. Forcing the geographic form
# means the timezone always observes DST correctly per the prospect's location.
_VALID_TZ_PREFIXES = (
    "America/", "Pacific/", "Atlantic/",
    "Asia/", "Europe/", "Africa/",
    "Australia/", "Indian/", "Antarctica/", "Arctic/",
)


def _is_valid_iana(tz: str) -> bool:
    if not tz:
        return False
    if not tz.startswith(_VALID_TZ_PREFIXES):
        return False
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def resolve_timezone_with_source(
    stored_tz: str, city: str, state: str
) -> tuple[str, str]:
    """Resolve an IANA timezone and return its source.

    Source is one of:
      - "stored": valid IANA on the contact's `timezone` field
      - "city":   split-state city override hit
      - "state":  state default
      - "default": no usable signal — hard default fired (ERROR-logged)

    Never returns America/Bogota.
    """
    stored = (stored_tz or "").strip() if isinstance(stored_tz, str) else ""
    if _is_valid_iana(stored):
        return stored, "stored"

    state_code = _normalize_state(state)
    city_key = (city or "").strip().lower() if isinstance(city, str) else ""

    if state_code:
        override = _CITY_OVERRIDES.get((state_code, city_key))
        if override:
            return override, "city"
        return _STATE_TZ[state_code], "state"

    # No usable signal at all.
    log.error(
        "tz_resolver: no usable signal for prospect — stored_tz=%r city=%r "
        "state=%r — defaulting to %s. CRM data is incomplete.",
        stored_tz, city, state, HARD_DEFAULT_TZ,
    )
    return HARD_DEFAULT_TZ, "default"


def resolve_timezone(stored_tz: str, city: str, state: str) -> str:
    """Resolve an IANA timezone for a prospect (source-stripped variant).

    Use `resolve_timezone_with_source` when you need to log/alert which path fired.
    """
    tz, _ = resolve_timezone_with_source(stored_tz, city, state)
    return tz
