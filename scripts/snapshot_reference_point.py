#!/usr/bin/env python3
"""Snapshot the current prompt files into docs/reference-points/<date>-<reason>/.

Matches the existing pattern (see docs/reference-points/2026-05-11-pre-stage-split/).
Use before a "ship it" deploy so we can roll back if the change goes badly.

CLI:
    python scripts/snapshot_reference_point.py \\
        --reason recap-fix \\
        --note "Added recap step before booking ask per CL-010"
"""
import argparse
import shutil
import sys
from datetime import date
from pathlib import Path

PROMPT_FILES = [
    "src/agent/prompts/inbound_texts.py",
    "src/agent/prompts/shared_persona.py",
    "src/agent/prompts/stage1_inbound_texts.py",
    "src/agent/prompts/stage2_qualify.py",
    "src/agent/prompts/stage3_book.py",
]


def create_snapshot(repo_root: Path, date: str, short_reason: str, long_reason: str) -> Path:
    repo_root = Path(repo_root)
    snap = repo_root / "docs" / "reference-points" / f"{date}-{short_reason}"
    snap.mkdir(parents=True, exist_ok=False)

    for rel in PROMPT_FILES:
        src = repo_root / rel
        if src.exists():
            dst = snap / Path(rel).name
            shutil.copy2(src, dst)

    restore_md = f"""# Reference point: {date} — {short_reason}

**Reason:** {long_reason}

## What this snapshot contains

Prompt files copied at the moment this snapshot was created:

"""
    for rel in PROMPT_FILES:
        if (repo_root / rel).exists():
            restore_md += f"- `{Path(rel).name}` (from `{rel}`)\n"
    restore_md += f"""

## To restore

```bash
cp docs/reference-points/{date}-{short_reason}/shared_persona.py src/agent/prompts/
cp docs/reference-points/{date}-{short_reason}/stage1_inbound_texts.py src/agent/prompts/
cp docs/reference-points/{date}-{short_reason}/stage2_qualify.py src/agent/prompts/
cp docs/reference-points/{date}-{short_reason}/stage3_book.py src/agent/prompts/
.venv-extract/bin/python scripts/push_squad.py
```

Verify by pulling the live Vapi assistant config and confirming the system message contents match these files.
"""
    (snap / "RESTORE.md").write_text(restore_md)
    return snap


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reason", required=True,
                    help="short-kebab tag for the snapshot directory (e.g., recap-fix)")
    ap.add_argument("--note", required=True,
                    help="one-sentence description for RESTORE.md")
    ap.add_argument("--date", default=str(date.today()),
                    help="YYYY-MM-DD (defaults to today)")
    ap.add_argument("--repo-root", type=Path,
                    default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()

    snap = create_snapshot(args.repo_root, args.date, args.reason, args.note)
    print(f"✓ snapshot created at {snap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
