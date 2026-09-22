---
name: live-system-testing
description: |
  Invoke BEFORE running ANY test that hits live infrastructure that can mutate
  state — webhook integration tests, GHL/Cal.com/CRM bookings, Vapi assistant
  calls, Twilio writes, anything that produces artifacts a real human/system
  might see, AND any cleanup of test artifacts. Triggers: "test the booking",
  "test the webhook live", "real test", "deep test against prod", "integration
  test", "e2e test", "smoke test", "exercise the live tool", "actually book a
  slot to verify", "run it for real", "hit the live X", "verify the booking
  landed", "cleanup", "clean up the test", "delete the test artifacts", "sync
  to GHL/Cal/Twilio/whatever", or any test that will POST/PUT/DELETE against a
  third-party API used by production. Does NOT apply to text-only LLM-vs-LLM
  persona sims — those go through `simulation-test`. When user is asking to
  VERIFY something downstream that requires a write, this skill takes priority
  over `verification-before-completion`.
---

# live-system-testing

## The rule (read this first)

| You MUST | You MUST NOT |
|---|---|
| Cleanup iterates the **inventory** and issues DELETE BY ID, one entry at a time. | List upstream artifacts and filter that list to decide what to delete. **Ever.** |
| Use phone in `+15550199XXXX` AND tag `voicsito-test-artifact` as the **only** load-bearing markers. | Trust `firstName` or `email` as markers on anything the booking codepath touches — the webhook itself overwrites them at `server.py:515-522`. |
| Generate the phone suffix deterministically from `test_run_id` so two runs can never collide on the same number. | Pick test phones by hand or sequentially. |
| `tests/lib/test_markers.py`, `tests/lib/test_inventory.py`, `tests/lib/cleanup_test_artifacts.py`, `tests/lib/verify_clean_state.py` must exist before any live-system test runs. | Invent your own marker scheme. Skip the helpers. Run a one-off cleanup script with ad-hoc match logic. |
| Open a per-run inventory file at `tests/runs/<run_id>.json` (one file per test run). Flush atomically (temp + rename) after every create. | Share a single inventory file across runs. Use plain `json.dump` over the live file. |
| Add `voicsito-test-artifact` tag to every test contact **immediately** after create, BEFORE any other webhook call. | Rely on `firstName`/`email` to survive — `update_prospect_fields` overwrites them. |
| GET the artifact back post-create. Confirm phone is unchanged AND tag is present. | Assume the upstream system preserved what you sent. |
| Default to dry-run (`_test_dry_run: true` in `book_video_call` args). Real writes require a one-sentence justification at the top of the test file AND a fresh `GO LIVE TEST` token from the user **in this session**. | Carry forward yesterday's approval. Real-write a booking on "they said go before." |
| End every session with `verify_clean_state.py --run-id <id>` exiting zero. | Claim the session is done without that exit-zero proof. |

## Steps

### 1. Pre-flight

Run all five before any test code touches an HTTP method other than GET:

```bash
# (a) Helpers must exist.
ls tests/lib/test_markers.py tests/lib/test_inventory.py \
   tests/lib/cleanup_test_artifacts.py tests/lib/verify_clean_state.py
# Any missing -> STOP. Do not invent. Tell the user.

# (b) Prior runs must be clean.
.venv-tests/bin/python tests/lib/verify_clean_state.py --all-runs
# Any run with state != done -> resolve it first (cleanup or human review).

# (c) Snapshot the upstream BEFORE the test starts.
.venv-tests/bin/python tests/lib/verify_clean_state.py --snapshot-before $RUN_ID

# (d) Decide explicitly: dry-run or real-write?
# Default is dry-run. Real-write requires the next two.

# (e) Get a fresh GO LIVE TEST token from the user IN THIS SESSION,
#     for real-writes only. The user types it; do not assume it.
```

### 2. Generate markers + open inventory

```python
from tests.lib.test_markers import new_run, new_artifact_markers
from tests.lib.test_inventory import open_inventory

run_id = new_run()  # uuid + iso timestamp
inv = open_inventory(run_id)  # writes tests/runs/<run_id>.json
markers = new_artifact_markers(run_id)
# {
#   "phone": "+155501994821",     # deterministic from run_id, immutable in GHL
#   "email": "vt-<run_id_short>@voicsito-tests.invalid",  # soft (overwritten)
#   "first_name": "VTEST_<short>",                         # soft (overwritten)
#   "tag": "voicsito-test-artifact",                       # immutable
# }
```

### 3. Execute the test

For each create call:

1. Generate the markers (step 2 helper). Per-run hash → fixed phone suffix.
2. Send the create (e.g. webhook POST). Inventory entry goes in **state `pending`** BEFORE the request fires (so a crash mid-create leaves a recoverable breadcrumb).
3. On 2xx, parse the artifact id. Upgrade inventory entry to **state `confirmed`** with the id.
4. **Immediately** call `add_tag(contact_id, crm_key, "voicsito-test-artifact")`. This is the only marker that survives the booking codepath.
5. GET the artifact. Assert phone matches AND tag is present. If either fails, mark `state: pending-degraded`, run cleanup, and ABORT the session.
6. Print one breadcrumb to stdout: `[VTEST <run_id>] created <type> id=<id> tag=ok`.

If a request returns 2xx with no parseable id, do NOT continue. Run `GET /contacts/?phone=<marker_phone>` to find what was created, inventory it, then run cleanup.

### 4. Cleanup

```bash
.venv-tests/bin/python tests/lib/cleanup_test_artifacts.py --run-id $RUN_ID --dry-run
# Read the printed to-delete list. EVERY entry must be from this run's inventory.
# If anything is unexpected, STOP.
.venv-tests/bin/python tests/lib/cleanup_test_artifacts.py --run-id $RUN_ID
```

The cleanup script:

- Reads `tests/runs/<run_id>.json`.
- Iterates inventory entries **in reverse creation order** (appointments before contacts, so we never orphan an event when its contact is gone).
- For each entry: GET the artifact, verify (a) phone still starts with `+15550199`, (b) tag `voicsito-test-artifact` still present, (c) inventory id matches the upstream id.
- If all three confirm → DELETE BY ID. Record `state: deleted` with timestamp.
- If any fail → SKIP. Record `state: skipped-marker-mismatch` with the reason. Do NOT delete.
- Cleanup NEVER lists upstream artifacts to decide what to delete. The inventory is the only source of truth for the to-delete set.

### 5. State proof

```bash
.venv-tests/bin/python tests/lib/verify_clean_state.py --run-id $RUN_ID
```

Exits zero only when:

- Every inventory entry has `state == deleted` (or an explicitly justified `skipped-marker-mismatch`).
- A scan of the next 30 days of the Video Call calendar finds zero events whose contactId has the test tag (orphan check via tag, since phone-prefix substring search is not reliable across GHL endpoints).
- The pre-test upstream snapshot's set of appointment IDs is a subset of the current set (no real appointments disappeared during the session).

Until this exits zero, the test session is `NOT YET VERIFIED`. Do not say "passed", "done", "works", or "verified" in chat.

## Hard prohibitions

- **NEVER** decide what to delete by listing upstream artifacts. The inventory file drives every DELETE. (This was the exact failure mode that cancelled Jared/Hector.)
- **NEVER** match on ISO timestamp prefix, title pattern, or time window. Even with timezone offsets correct, prefix matching is banned for delete decisions.
- **NEVER** assume GHL/Twilio/Cal.com will reject obviously-fake input. GHL accepts `+15555550100` and creates real records. Treat every write as if it WILL succeed.
- **NEVER** start a real-write live-system test without a fresh `GO LIVE TEST` token from the user in the current session. Cache nothing across sessions. Yesterday's approval does not carry forward.
- **NEVER** run two live-system tests in parallel against the same upstream tenant. Acquire `tests/.live-test.lock` (flock) for the run's duration. Fail fast if already held.
- **NEVER** flip an inventory entry from `pending` or `pending-degraded` to `deleted` by hand. Only `cleanup_test_artifacts.py` and `verify_clean_state.py` are allowed to write `deleted`.
- **NEVER** use `sim-call.py` to claim a live booking works. `sim-call.py` mocks tools locally — it would have passed on the broken `get_available_slots` config that caused the 2026-05-11 incident. Live behavior requires hitting the live webhook with real Vapi-shaped payloads.
- **NEVER** trust `firstName` or `email` as a deletion-gating marker. The booking codepath overwrites both at `server.py:515-522`. Phone + tag are the only durable markers.

## Real tests vs fake tests

A **real test** exercises the actual production code path end-to-end:
- Hits the deployed webhook URL (not a mock).
- Sends a Vapi-shaped payload (`tool-calls` envelope shape that Vapi sends).
- Verifies the real upstream API responded as expected.
- For mutating tools: either actually mutates (with markers + inventory + cleanup) OR uses the production webhook's `_test_dry_run` path.

A **fake test** is any of:
- In-process mock of the tool response (sim-call.py default behavior).
- Skipping signature verification when production enforces it.
- Asserting on a string the test itself constructed.
- Hitting a stubbed handler rather than the deployed FastAPI app.

Fake tests are fine for prompt iteration. They are NEVER sufficient to claim "booking works" or "the tool is fixed" — say `NOT YET VERIFIED` until a real test passes.

## Why this skill exists — the 2026-05-11 Jared/Hector incident

While writing "deep tests" for `get_available_slots`, the test harness booked 3 real appointments on Jon's GHL calendar against phantom contacts (GHL accepted phone numbers thought to be obviously fake). The follow-up cleanup script matched bookings by ISO time **prefix** with no timezone awareness: `14:00-04:00` (Eastern, 18:00 UTC) was treated as identical to `14:00-05:00` (Central, 19:00 UTC). Two real prospect appointments were cancelled — **Jared JDK Builders** (Tue May 12 2pm Central) and **Hector West Texas Quality Construction** (Wed May 13 11am Central).

Recovery worked: GHL soft-deletes (`deleted: true` on the record), so the originals were retrieved via GET and recreated via POST with the same contact, slot, calendar, and Zoom link — `toNotify: false` to avoid duplicate invite emails. Calendar state was confirmed clean post-recovery.

Two root patterns drove the failure:

1. **Heuristic matching for deletion.** Time-window / prefix / title matching can never be made safe. The rule must be ID-from-inventory, period.
2. **Marker overwrite.** The booking codepath itself overwrites `firstName` and `email` via `update_prospect_fields`. Any marker scheme that depends on those two fields surviving the booking will fail. Phone (immutable keying field in GHL) and tags (append-only) are the only durable markers.

This skill encodes both lessons as enforceable rules. If you read this skill and want to bypass any of its prohibitions for a "quick" test — don't. The 2026-05-11 incident took ~2 hours to recover from, and only because GHL happens to soft-delete. A different upstream system with hard deletes would have lost the data.
