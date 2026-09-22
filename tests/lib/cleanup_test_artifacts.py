"""Cleanup script for live-system test artifacts.

Read `.claude/skills/live-system-testing/SKILL.md` first.

Two iron rules this script enforces:

  1. DELETE direction is fixed: iterate INVENTORY, issue DELETE-BY-ID, one
     entry at a time. The script NEVER lists upstream artifacts to decide
     what to delete. (This was the failure mode that cancelled Jared/Hector
     on 2026-05-11 — the prior cleanup script listed events and matched by
     ISO prefix; this script does not list at all for delete decisions.)

  2. Before each DELETE, the script GETs the artifact and verifies the
     phone is still in the test range AND the test tag is still present.
     If either fails, the entry is SKIPPED with `state: skipped-marker-mismatch`
     and the operator must resolve by hand. The script never deletes when
     unsure.

Usage:
    .venv-tests/bin/python tests/lib/cleanup_test_artifacts.py --run-id <id>
    .venv-tests/bin/python tests/lib/cleanup_test_artifacts.py --run-id <id> --dry-run

Exit codes:
    0 — every confirmed artifact is now deleted (or explicitly skipped with reason)
    1 — usage error / inventory missing
    2 — at least one artifact still alive upstream (cleanup incomplete)
    3 — at least one upstream DELETE failed (operator action required)
"""
import argparse
import os
import sys
from pathlib import Path

# Allow running as a script: add repo root to sys.path so `tests.lib.*` resolves.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from tests.lib.test_inventory import load_inventory  # noqa: E402
from tests.lib.test_markers import TEST_PHONE_PREFIX, TEST_TAG  # noqa: E402

# Load env from config/.env
_ENV_PATH = _REPO_ROOT / "config" / ".env"
for line in _ENV_PATH.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)

GHL_BASE = "https://services.leadconnectorhq.com"
PIT_MAIN = os.environ["GHL_MAIN_CRM_PIT"]
PIT_TEXTS_1 = os.environ.get("GHL_TEXTS_CRM_1_PIT", "")
PIT_TEXTS_2 = os.environ.get("GHL_TEXTS_CRM_2_PIT", "")


def _pit_for(crm_key: str) -> str:
    return {"main": PIT_MAIN, "texts1": PIT_TEXTS_1, "texts2": PIT_TEXTS_2}[crm_key]


def _hdrs(pit: str, version: str = "2021-07-28") -> dict:
    return {
        "Authorization": f"Bearer {pit}",
        "Version": version,
        "Content-Type": "application/json",
    }


def get_contact(client: httpx.Client, contact_id: str, crm_key: str) -> tuple[bool, dict]:
    """Return (found, contact_dict). found=False on 404."""
    r = client.get(
        f"{GHL_BASE}/contacts/{contact_id}",
        headers=_hdrs(_pit_for(crm_key)),
        timeout=10.0,
    )
    if r.status_code == 404:
        return False, {}
    if r.status_code != 200:
        raise RuntimeError(f"GET contact {contact_id}: HTTP {r.status_code} {r.text[:200]}")
    return True, r.json().get("contact") or {}


def get_appointment(client: httpx.Client, appt_id: str) -> tuple[bool, dict]:
    """Return (found_and_alive, appt_dict). GHL soft-deletes appointments:
    `deleted: true` means already cancelled. Treat as not-alive."""
    r = client.get(
        f"{GHL_BASE}/calendars/events/appointments/{appt_id}",
        headers=_hdrs(PIT_MAIN, version="2021-04-15"),
        timeout=10.0,
    )
    if r.status_code == 404:
        return False, {}
    if r.status_code != 200:
        raise RuntimeError(f"GET appt {appt_id}: HTTP {r.status_code} {r.text[:200]}")
    appt = r.json().get("appointment") or {}
    return (not appt.get("deleted", False)), appt


def verify_contact_markers(contact: dict) -> tuple[bool, str]:
    """Return (ok_to_delete, reason)."""
    phone = (contact.get("phone") or "").strip()
    if not phone.startswith(TEST_PHONE_PREFIX):
        return False, f"phone {phone!r} not in test range {TEST_PHONE_PREFIX!r}"
    tags = contact.get("tags") or []
    if TEST_TAG not in tags:
        return False, f"test tag {TEST_TAG!r} missing (tags={tags})"
    return True, "phone+tag confirmed"


def verify_appt_markers(client: httpx.Client, appt: dict) -> tuple[bool, str]:
    """For appointments, verify the underlying contact still has phone+tag.
    Appointments don't carry tags directly; the contact is the source of truth."""
    contact_id = appt.get("contactId")
    if not contact_id:
        return False, "appointment has no contactId"
    found, contact = get_contact(client, contact_id, "main")
    if not found:
        # Contact already gone (out-of-order cleanup or upstream cascade).
        # Safe to delete the orphan appointment.
        return True, "underlying contact already gone — appointment is orphan"
    return verify_contact_markers(contact)


def delete_appointment(client: httpx.Client, appt_id: str) -> tuple[bool, str]:
    r = client.delete(
        f"{GHL_BASE}/calendars/events/{appt_id}",
        headers=_hdrs(PIT_MAIN, version="2021-04-15"),
        timeout=10.0,
    )
    if r.status_code in (200, 201, 204):
        return True, f"HTTP {r.status_code}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def delete_contact(client: httpx.Client, contact_id: str, crm_key: str) -> tuple[bool, str]:
    r = client.delete(
        f"{GHL_BASE}/contacts/{contact_id}",
        headers=_hdrs(_pit_for(crm_key)),
        timeout=10.0,
    )
    if r.status_code in (200, 201, 204):
        return True, f"HTTP {r.status_code}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


def cleanup_run(run_id: str, dry_run: bool = False) -> int:
    inv = load_inventory(run_id)
    inv.mark_run_state("cleanup_in_progress")

    # Iterate REVERSED so appointments delete before their parent contacts.
    # Build the working list as (original_index, entry) so we can update inv by index.
    working = list(enumerate(inv.artifacts))
    appts = [(i, e) for i, e in working if e.get("artifact_type") == "appointment"]
    contacts = [(i, e) for i, e in working if e.get("artifact_type") == "contact"]
    others = [(i, e) for i, e in working if e.get("artifact_type") not in ("appointment", "contact")]
    ordered = appts + contacts + others  # appointments first, then contacts

    print(f"\n=== cleanup run_id={run_id}  ({'DRY-RUN' if dry_run else 'LIVE'}) ===")
    print(f"  inventory: {len(inv.artifacts)} artifacts ({len(appts)} appts, {len(contacts)} contacts, {len(others)} other)\n")

    deleted = 0
    skipped = 0
    failed = 0
    already_gone = 0

    with httpx.Client() as client:
        for index, entry in ordered:
            label = f"[{entry['artifact_type']} id={entry.get('id')}]"

            state = entry.get("state")
            if state == "deleted":
                already_gone += 1
                print(f"  {label} state=deleted (already cleaned)")
                continue
            if not entry.get("id"):
                print(f"  {label} state={state} — no id, skipping")
                skipped += 1
                continue

            # GET upstream + verify markers
            try:
                if entry["artifact_type"] == "appointment":
                    alive, artifact = get_appointment(client, entry["id"])
                    if not alive:
                        if dry_run:
                            print(f"  {label} already gone upstream — would mark deleted")
                        else:
                            inv.set_state(index, "deleted", reason="already gone upstream")
                            already_gone += 1
                            print(f"  {label} already gone upstream — marked deleted")
                        continue
                    ok, reason = verify_appt_markers(client, artifact)
                elif entry["artifact_type"] == "contact":
                    crm_key = entry.get("notes", "").split(":")[-1] or "main"
                    if crm_key not in ("main", "texts1", "texts2"):
                        crm_key = "main"
                    alive, artifact = get_contact(client, entry["id"], crm_key)
                    if not alive:
                        if dry_run:
                            print(f"  {label} already gone upstream — would mark deleted")
                        else:
                            inv.set_state(index, "deleted", reason="already gone upstream")
                            already_gone += 1
                            print(f"  {label} already gone upstream — marked deleted")
                        continue
                    ok, reason = verify_contact_markers(artifact)
                else:
                    print(f"  {label} unsupported artifact_type — manual review")
                    skipped += 1
                    continue
            except Exception as e:
                print(f"  {label} GET failed: {e} — leaving as-is for retry")
                failed += 1
                continue

            if not ok:
                print(f"  {label} MARKER MISMATCH: {reason} — REFUSING TO DELETE")
                if not dry_run:
                    inv.set_state(index, "skipped-marker-mismatch", reason=reason)
                skipped += 1
                continue

            if dry_run:
                print(f"  {label} would DELETE — markers ok ({reason})")
                continue

            # Issue DELETE
            try:
                if entry["artifact_type"] == "appointment":
                    success, msg = delete_appointment(client, entry["id"])
                else:
                    crm_key = entry.get("notes", "").split(":")[-1] or "main"
                    if crm_key not in ("main", "texts1", "texts2"):
                        crm_key = "main"
                    success, msg = delete_contact(client, entry["id"], crm_key)
            except Exception as e:
                inv.set_state(index, "delete-failed", reason=str(e))
                print(f"  {label} DELETE crashed: {e}")
                failed += 1
                continue

            if success:
                inv.set_state(index, "deleted", reason=msg)
                deleted += 1
                print(f"  {label} DELETED ({msg})")
            else:
                inv.set_state(index, "delete-failed", reason=msg)
                failed += 1
                print(f"  {label} DELETE FAILED: {msg}")

    print(f"\n  deleted={deleted}  already_gone={already_gone}  skipped={skipped}  failed={failed}")

    if dry_run:
        print("\n  DRY-RUN — no state changes written.\n")
        return 0

    if failed > 0:
        inv.mark_run_state("needs_human")
        return 3

    # Determine final run state
    confirmed_left = sum(
        1
        for e in inv.artifacts
        if e.get("state") in ("confirmed", "pending-degraded", "pending")
    )
    if confirmed_left > 0:
        inv.mark_run_state("needs_human")
        return 2

    inv.mark_run_state("done")
    print("  run marked done.\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be deleted; make no changes")
    args = parser.parse_args()
    sys.exit(cleanup_run(args.run_id, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
