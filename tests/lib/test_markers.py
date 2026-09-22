"""Marker generation for live-system tests against production third-party APIs.

Read `.claude/skills/live-system-testing/SKILL.md` before using this module.

Only two markers are LOAD-BEARING:

  - `phone` in the +15550199XXXX range. Immutable in GHL (it's the keying
    field). Substring `+15550199` is the cleanup script's primary gate.
  - `tag` value `voicsito-test-artifact`. Tags are append-only in GHL — they
    survive `update_prospect_fields` writes from the booking codepath. Tests
    MUST `add_tag` immediately after contact create.

`email` and `first_name` are SOFT markers — visible in the GHL UI for human
recognition, but the booking codepath overwrites them at server.py:515-522.
NEVER use them as deletion gates.

The phone suffix is deterministic from `test_run_id` so two parallel test runs
cannot collide on the same number. Collision would cause GHL contact dedup to
return the OTHER run's contactId, silently merging the runs' artifacts and
corrupting both inventories.
"""
import hashlib
import uuid
from datetime import datetime, timezone


# --- LOAD-BEARING CONSTANTS ---
# These are the only values cleanup_test_artifacts.py and the webhook's
# _test_dry_run gate look at. If you change them, update both places in the
# same commit or production calls will start being dry-run'd.

TEST_PHONE_PREFIX = "+15550199"  # 4 free digits -> 10000 slots/run
TEST_TAG = "voicsito-test-artifact"  # append-only marker, survives prod codepath
TEST_EMAIL_DOMAIN = "voicsito-tests.invalid"  # RFC 2606 .invalid TLD


def new_run() -> str:
    """Return a fresh test run id. Format: vt-<utc-date>-<8-hex>."""
    return f"vt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _phone_suffix_for_run(run_id: str) -> str:
    """Deterministic 4-digit suffix from run_id. Two different run_ids never
    collide on the same number (within the 0000-9999 space; collision rate
    becomes meaningful only at ~100 concurrent runs)."""
    h = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return f"{int(h[:8], 16) % 10000:04d}"


def new_artifact_markers(run_id: str, purpose: str = "main") -> dict:
    """Build the marker bundle for a single test artifact.

    Args:
        run_id: result of new_run()
        purpose: short label like 'replay' or 'batch'. Embedded in soft
                 markers for human recognition; never used as a deletion gate.

    Returns dict with: phone, email, first_name, tag.
    """
    short = run_id.split("-")[-1]  # last 8 hex chars
    suffix = _phone_suffix_for_run(run_id)
    return {
        "phone": f"{TEST_PHONE_PREFIX}{suffix}",
        "email": f"vt-{short}-{purpose}@{TEST_EMAIL_DOMAIN}",
        "first_name": f"VTEST_{short}",
        "tag": TEST_TAG,
    }


def is_test_phone(phone: str) -> bool:
    """True iff phone is in the reserved test range. Used by the webhook's
    _test_dry_run gate and by cleanup verification."""
    if not phone:
        return False
    return phone.startswith(TEST_PHONE_PREFIX)


def is_test_email(email: str) -> bool:
    """True iff email is in the test domain. SOFT signal — do not gate
    deletions on this. The booking codepath overwrites email."""
    if not email:
        return False
    return email.lower().endswith(f"@{TEST_EMAIL_DOMAIN}")
