"""Per-run inventory for live-system tests.

Read `.claude/skills/live-system-testing/SKILL.md` first.

Design points:
- One JSON file per test run (no shared global file → no concurrent-write
  corruption, no lock contention).
- Atomic write via temp-then-rename so a crash mid-flush never truncates
  the file.
- Per-artifact tri-state (`pending` / `confirmed` / `pending-degraded` /
  `deleted` / `skipped-marker-mismatch` / `delete-failed`) — boolean was
  insufficient when a cleanup partially succeeded.
- Only cleanup_test_artifacts.py and verify_clean_state.py are allowed to
  flip an entry to `deleted`. Tests flip to `confirmed` / `pending-degraded`.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = _REPO_ROOT / "tests" / "runs"


ARTIFACT_STATES = (
    "pending",                  # request fired, no ID yet
    "confirmed",                # ID returned, markers verified
    "pending-degraded",         # ID returned but markers didn't round-trip
    "deleted",                  # cleanup confirmed gone upstream
    "skipped-marker-mismatch",  # cleanup refused to delete (markers changed)
    "delete-failed",            # upstream returned non-2xx on DELETE
)

RUN_STATES = (
    "in_progress",
    "cleanup_in_progress",
    "done",
    "needs_human",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.json"


def open_inventory(run_id: str, label: str = "") -> "Inventory":
    """Create a new inventory file. Fails if one already exists for this run_id."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = _run_path(run_id)
    if path.exists():
        raise FileExistsError(
            f"Inventory for run {run_id} already exists at {path}. "
            f"Use load_inventory() if you intend to resume."
        )
    inv = Inventory(run_id=run_id, label=label, path=path)
    inv._data = {
        "version": 1,
        "run_id": run_id,
        "label": label,
        "started_at": _now_iso(),
        "completed_at": None,
        "run_state": "in_progress",
        "artifacts": [],
    }
    inv._flush()
    return inv


def load_inventory(run_id: str) -> "Inventory":
    """Load an existing inventory. Fails if not found."""
    path = _run_path(run_id)
    if not path.exists():
        raise FileNotFoundError(f"No inventory at {path}")
    inv = Inventory(run_id=run_id, label="", path=path)
    inv._data = json.loads(path.read_text())
    inv.label = inv._data.get("label", "")
    return inv


def list_runs() -> list[dict]:
    """Return one summary dict per inventory file under tests/runs/.
    Each: {run_id, label, run_state, started_at, completed_at, artifact_count}."""
    if not RUNS_DIR.exists():
        return []
    out = []
    for p in sorted(RUNS_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text())
            out.append({
                "run_id": d.get("run_id", p.stem),
                "label": d.get("label", ""),
                "run_state": d.get("run_state", "?"),
                "started_at": d.get("started_at", ""),
                "completed_at": d.get("completed_at"),
                "artifact_count": len(d.get("artifacts", [])),
                "path": str(p),
            })
        except Exception as e:
            out.append({"run_id": p.stem, "error": str(e), "path": str(p)})
    return out


class Inventory:
    """One inventory file. Mutations flush atomically to disk."""

    def __init__(self, run_id: str, label: str, path: Path):
        self.run_id = run_id
        self.label = label
        self.path = path
        self._data: dict = {}

    # ─────────── lifecycle ───────────

    def add_pending(
        self,
        *,
        artifact_type: str,
        markers: dict,
        slot_iso: Optional[str] = None,
        parent_artifact_id: Optional[str] = None,
        notes: str = "",
    ) -> int:
        """Record a planned create. Call BEFORE firing the request so a crash
        mid-create leaves a recoverable breadcrumb. Returns the index of the
        new entry (use it with set_confirmed / set_state)."""
        entry = {
            "artifact_type": artifact_type,
            "state": "pending",
            "id": None,
            "markers": dict(markers),
            "slot_iso": slot_iso,
            "parent_artifact_id": parent_artifact_id,
            "notes": notes,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._data["artifacts"].append(entry)
        self._flush()
        return len(self._data["artifacts"]) - 1

    def set_confirmed(self, index: int, artifact_id: str) -> None:
        e = self._data["artifacts"][index]
        e["id"] = artifact_id
        e["state"] = "confirmed"
        e["updated_at"] = _now_iso()
        self._flush()

    def set_state(self, index: int, state: str, reason: str = "") -> None:
        if state not in ARTIFACT_STATES:
            raise ValueError(f"unknown artifact state: {state}")
        e = self._data["artifacts"][index]
        e["state"] = state
        e["updated_at"] = _now_iso()
        if reason:
            e.setdefault("history", []).append({"at": _now_iso(), "state": state, "reason": reason})
        self._flush()

    def mark_run_state(self, state: str) -> None:
        if state not in RUN_STATES:
            raise ValueError(f"unknown run state: {state}")
        self._data["run_state"] = state
        if state == "done":
            self._data["completed_at"] = _now_iso()
        self._flush()

    # ─────────── reads ───────────

    @property
    def artifacts(self) -> list[dict]:
        return list(self._data.get("artifacts", []))

    @property
    def run_state(self) -> str:
        return self._data.get("run_state", "?")

    def confirmed_artifacts(self) -> list[dict]:
        return [
            {**a, "_index": i}
            for i, a in enumerate(self._data["artifacts"])
            if a.get("state") in ("confirmed", "pending-degraded", "delete-failed")
            and a.get("id")
        ]

    # ─────────── atomic write ───────────

    def _flush(self) -> None:
        """Write inventory to disk atomically. Temp file in same dir → rename
        is atomic on POSIX (same filesystem). Avoids truncated-JSON corruption
        if process dies mid-write."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".tmp.{os.getpid()}.{int(time.time() * 1000)}")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)
