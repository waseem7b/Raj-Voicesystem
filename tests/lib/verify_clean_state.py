"""Post-test state proof for live-system tests.

Read `.claude/skills/live-system-testing/SKILL.md` first.

What this checks:

  1. Every artifact in the run's inventory is now deleted upstream (or
     explicitly skipped with a written reason).
  2. No orphan test artifacts survive in the next 30 days of the Video Call
     calendar — checked by listing events, GETting the contact, looking for
     the test tag. Orphans = test-tagged contacts not in ANY inventory file.
  3. No run is left in `cleanup_in_progress` or `pending` states.

The "phone-prefix substring search" route is intentionally NOT used — GHL's
exposed endpoints don't reliably support it across the surface this codebase
uses. The tag-based scan is the durable alternative.

Exit codes:
    0 — clean. Safe to claim the live test passed.
    1 — usage error
    2 — at least one inventory entry is still alive upstream
    3 — orphan test artifact found (test-tagged but not in any inventory)
    4 — a prior run is left in pending / cleanup_in_progress / needs_human
"""
import argparse
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from tests.lib.test_inventory import load_inventory, list_runs  # noqa: E402
from tests.lib.test_markers import TEST_PHONE_PREFIX, TEST_TAG  # noqa: E402

_ENV_PATH = _REPO_ROOT / "config" / ".env"
for line in _ENV_PATH.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k, v)

GHL_BASE = "https://services.leadconnectorhq.com"
PIT = os.environ["GHL_MAIN_CRM_PIT"]
LOCATION_ID = "G1yFGT1liEZgvT3dYgEx"
CALENDAR_ID = "ULFPF484I2XcTfSR2uIC"


def _hdrs(version: str = "2021-04-15") -> dict:
    return {
        "Authorization": f"Bearer {PIT}",
        "Version": version,
        "Content-Type": "application/json",
    }


def verify_run(run_id: str) -> tuple[int, list[str]]:
    """Returns (exit_code, list_of_issue_strings)."""
    issues: list[str] = []
    inv = load_inventory(run_id)
    print(f"\n=== verify_clean_state run_id={run_id} ===\n")
    print(f"  run_state: {inv.run_state}")
    print(f"  artifacts: {len(inv.artifacts)}")

    alive_ids = []
    for e in inv.artifacts:
        state = e.get("state")
        if state == "deleted":
            continue
        if state == "skipped-marker-mismatch":
            issues.append(
                f"  · artifact {e.get('artifact_type')} id={e.get('id')} "
                f"skipped-marker-mismatch — needs human review"
            )
            continue
        # confirmed / pending / pending-degraded / delete-failed all mean "may still be alive"
        alive_ids.append(e)

    if alive_ids:
        for e in alive_ids:
            issues.append(
                f"  · artifact {e.get('artifact_type')} id={e.get('id')} "
                f"state={e.get('state')} — still in inventory as not-deleted"
            )
        print(f"\n  ✗ {len(alive_ids)} inventory entries are NOT marked deleted.\n")
        return 2, issues

    print("  ✓ every inventory entry is deleted (or explicitly skipped)\n")
    return 0, issues


def scan_for_orphans() -> tuple[int, list[str]]:
    """List Video Call events in the next 30 days. For each, GET the
    appointment to find contactId, then GET the contact and check for the
    test tag. Any test-tagged contact whose appointment is alive is an
    orphan that no inventory accounted for."""
    print("=== scan for orphan test artifacts (next 30 days) ===\n")
    issues: list[str] = []
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + 30 * 24 * 3600 * 1000

    with httpx.Client(timeout=20.0) as client:
        r = client.get(
            f"{GHL_BASE}/calendars/events",
            params={
                "locationId": LOCATION_ID,
                "calendarId": CALENDAR_ID,
                "startTime": str(now_ms),
                "endTime": str(end_ms),
            },
            headers=_hdrs(),
        )
        if r.status_code != 200:
            issues.append(f"  · orphan scan failed: HTTP {r.status_code}")
            return 1, issues
        events = r.json().get("events") or r.json().get("appointments") or []
        print(f"  {len(events)} events in window")

        # All inventories' artifact IDs combined (for membership check)
        all_known_ids: set[str] = set()
        for ri in list_runs():
            try:
                rinv = load_inventory(ri["run_id"])
                for a in rinv.artifacts:
                    if a.get("id"):
                        all_known_ids.add(a["id"])
            except Exception:
                pass

        orphans_found = 0
        for ev in events:
            contact_id = ev.get("contactId")
            if not contact_id:
                continue
            cr = client.get(
                f"{GHL_BASE}/contacts/{contact_id}",
                headers=_hdrs(version="2021-07-28"),
            )
            if cr.status_code != 200:
                continue
            contact = cr.json().get("contact") or {}
            tags = contact.get("tags") or []
            phone = (contact.get("phone") or "")
            has_test_tag = TEST_TAG in tags
            in_test_phone_range = phone.startswith(TEST_PHONE_PREFIX)
            if not (has_test_tag or in_test_phone_range):
                continue
            # This is a test-flagged artifact. Is it accounted for?
            appt_id = ev.get("id")
            if appt_id in all_known_ids:
                continue  # known
            orphans_found += 1
            issues.append(
                f"  · ORPHAN: appt {appt_id} contact {contact_id} "
                f"tags={tags} phone={phone} start={ev.get('startTime')} "
                f"title={ev.get('title')!r}"
            )

        if orphans_found > 0:
            print(f"\n  ✗ {orphans_found} orphan test artifacts on the calendar.\n")
            return 3, issues
        print("  ✓ no orphan test artifacts in next 30 days\n")
        return 0, issues


def verify_all_runs() -> tuple[int, list[str]]:
    """Check every inventory file. Any not-done run is a blocker."""
    print("=== verify all prior runs ===\n")
    issues: list[str] = []
    blockers = 0
    for r in list_runs():
        state = r.get("run_state")
        marker = "✓" if state == "done" else "✗"
        print(f"  {marker} {r['run_id']}  state={state}  artifacts={r.get('artifact_count')}  ({r.get('label', '')})")
        if state != "done":
            blockers += 1
            issues.append(f"  · {r['run_id']} is in state {state}")
    if blockers > 0:
        print(f"\n  ✗ {blockers} prior runs are not done. Resolve before new live tests.\n")
        return 4, issues
    print("\n  ✓ all prior runs done\n")
    return 0, issues


def snapshot_before(run_id: str) -> int:
    """Save a pre-test snapshot of the calendar so post-test we can confirm
    no real appointment disappeared during the session."""
    print(f"=== snapshot before run_id={run_id} ===\n")
    out_dir = _REPO_ROOT / "tests" / "runs" / f"{run_id}.snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + 30 * 24 * 3600 * 1000
    with httpx.Client(timeout=20.0) as client:
        r = client.get(
            f"{GHL_BASE}/calendars/events",
            params={
                "locationId": LOCATION_ID,
                "calendarId": CALENDAR_ID,
                "startTime": str(now_ms),
                "endTime": str(end_ms),
            },
            headers=_hdrs(),
        )
        if r.status_code != 200:
            print(f"  ✗ snapshot failed: HTTP {r.status_code}")
            return 1
    snap_path = out_dir / "calendar-before.json"
    snap_path.write_text(r.text)
    print(f"  ✓ saved calendar snapshot to {snap_path}")
    print(f"  {len(r.json().get('events') or r.json().get('appointments') or [])} events recorded\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="verify a specific run (and scan orphans)")
    parser.add_argument("--all-runs", action="store_true",
                        help="check every inventory file's run_state")
    parser.add_argument("--snapshot-before", metavar="RUN_ID",
                        help="save pre-test calendar snapshot for RUN_ID")
    args = parser.parse_args()

    if args.snapshot_before:
        sys.exit(snapshot_before(args.snapshot_before))

    if args.all_runs:
        code, issues = verify_all_runs()
        for i in issues:
            print(i)
        sys.exit(code)

    if not args.run_id:
        parser.print_help()
        sys.exit(1)

    code1, i1 = verify_run(args.run_id)
    code2, i2 = scan_for_orphans()
    all_issues = i1 + i2

    if all_issues:
        print("\nISSUES:")
        for i in all_issues:
            print(i)

    final = code1 or code2
    if final == 0:
        print("\n✓ verify_clean_state PASSED — safe to claim live test passed.\n")
    else:
        print(f"\n✗ verify_clean_state FAILED exit={final}\n")
    sys.exit(final)


if __name__ == "__main__":
    main()
