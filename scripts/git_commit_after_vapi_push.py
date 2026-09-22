#!/usr/bin/env python3
"""
Safety-net PostToolUse hook: auto-commits after push_squad.py runs
when called outside the call-review ship-it flow.

The call-review SKILL.md ship-it flow writes a meaningful commit message
at step 7 (after render.py). When that step runs, `git status` is usually
clean or has only deploy-metadata files. This script handles the case where
push_squad.py is invoked directly (bypassing the SKILL.md flow) so changes
are never silently left uncommitted.

Registered in: .claude/settings.json → hooks.PostToolUse (matcher: Bash)
"""
import datetime
import json
import subprocess
import sys


def main() -> None:
    data = json.load(sys.stdin)
    command = data.get("tool_input", {}).get("command", "")

    if "push_squad.py" not in command:
        return

    # Check whether there is anything to commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if not status.stdout.strip():
        return  # Nothing to commit — call-review flow already committed

    today = datetime.date.today().isoformat()
    msg = (
        f"Voicsito: Vapi prompt push {today} (auto-committed)\n\n"
        "Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
    )

    subprocess.run(["git", "add", "-A"], check=False)
    subprocess.run(["git", "commit", "-m", msg], check=False)
    subprocess.run(["git", "push"], check=False)


if __name__ == "__main__":
    main()
