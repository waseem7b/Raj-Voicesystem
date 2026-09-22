#!/usr/bin/env python3
"""SessionStart hook — surface deferred known issues at the top of every session.

Reads docs/OPEN-ISSUES.md and injects it into the model's context so outstanding
defects get raised even when nobody remembers to ask. Delete an entry from that
file when it is fixed; an empty file means nothing outstanding.

Silent no-op if the file is missing, so this can never block a session start.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

PREAMBLE = (
    "OPEN ISSUES for this project, from docs/OPEN-ISSUES.md. These are known, "
    "deliberately deferred defects. Near the start of this session, tell the "
    "user what is outstanding and ask whether they want any of it fixed now — "
    "do this even if they never bring it up. Keep it to a short list; the "
    "detail is in the file. If the user declines, drop it and do not raise it "
    "again this session.\n\n"
)


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    path = pathlib.Path(root) / "docs" / "OPEN-ISSUES.md"
    try:
        body = path.read_text()
    except OSError:
        return 0

    if not body.strip():
        return 0

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": PREAMBLE + body,
        },
        "systemMessage": "Open issues loaded — see docs/OPEN-ISSUES.md",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
