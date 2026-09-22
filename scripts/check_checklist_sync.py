#!/usr/bin/env python3
"""Verify every prompt file's current sha256 matches the most-recent
`voicsito/call-review/changes/CH-*.yaml` entry that references it.

Exits 0 if clean; exits 1 with a drift report if any prompt file changed
without a matching log entry (or the latest matching entry doesn't have
an `escape_reason` justifying the unmatched sha).

CLI:
    python scripts/check_checklist_sync.py
    python scripts/check_checklist_sync.py --repo-root /path/to/Voicsito
"""
import argparse
import hashlib
import sys
from pathlib import Path

import yaml

PROMPT_GLOB = "src/agent/prompts/*.py"
CHANGES_GLOB = "voicsito/call-review/changes/CH-*.yaml"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_change(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        return {"_parse_error": str(e), "_path": str(path)}


def scan(repo_root: Path) -> list:
    """Return a list of drift records. Empty list = clean."""
    repo_root = Path(repo_root)
    prompts = sorted(repo_root.glob(PROMPT_GLOB))
    prompts = [p for p in prompts if p.name != "__init__.py"]

    changes = sorted(repo_root.glob(CHANGES_GLOB))
    changes_by_prompt: dict[str, list] = {}
    for c in changes:
        data = _load_change(c)
        target = data.get("prompt_file")
        if not target:
            continue
        changes_by_prompt.setdefault(target, []).append((c, data))

    drifts = []
    for prompt in prompts:
        rel = prompt.relative_to(repo_root).as_posix()
        current_sha = _sha256_of(prompt)
        history = changes_by_prompt.get(rel, [])

        if not history:
            drifts.append({
                "prompt_file": rel,
                "current_sha256": current_sha,
                "reason": "no change entries reference this prompt file",
            })
            continue

        # Latest entry by filename sort (CH IDs are date-prefixed).
        latest_path, latest = history[-1]
        if latest.get("prompt_file_sha256_after") == current_sha:
            continue  # clean

        if (latest.get("escape_reason") or "").strip():
            continue  # latest entry justifies the mismatch

        drifts.append({
            "prompt_file": rel,
            "current_sha256": current_sha,
            "latest_change": latest.get("id") or latest_path.name,
            "latest_sha_after": latest.get("prompt_file_sha256_after"),
            "reason": "sha mismatch and no escape_reason on latest change",
        })
    return drifts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()

    drifts = scan(args.repo_root)
    if not drifts:
        print("✓ all prompts in sync with change log")
        return 0

    print("✗ drift detected:")
    for d in drifts:
        print(f"  - {d['prompt_file']}: {d['reason']}")
        if d.get("latest_change"):
            print(f"      latest change: {d['latest_change']}")
            print(f"      logged sha: {d.get('latest_sha_after')}")
            print(f"      current sha: {d['current_sha256']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
