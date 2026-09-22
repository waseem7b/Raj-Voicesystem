#!/usr/bin/env python3
"""Lightweight call judge: grade a sim transcript against per-persona expectations.

Usage:
    .venv-tests/bin/python scripts/judge-call.py \\
        --transcript tests/transcripts/stress-baseline/just_says_name.json \\
        --persona tests/personas/just_says_name.yaml

The persona YAML's `expectations:` list is a set of plain-English checks.
Claude reads them + the transcript and returns PASS/FAIL with one-sentence
reasoning per check.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "anthropic/claude-sonnet-4.6")
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 2500


def load_transcript(path: Path) -> str:
    """Load a transcript file as plain text for the judge.

    Markdown (.md) — strips YAML frontmatter, returns body verbatim.
    JSON (.json) — legacy fallback; reformats into the same line-based shape.
    """
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".md" or raw.lstrip().startswith("---"):
        body = raw.lstrip()
        if body.startswith("---"):
            # Strip frontmatter: find the closing ``---``.
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + 4:]
        return body.strip()
    # Legacy JSON path.
    data = json.loads(raw)
    lines = []
    for t in data["transcript"]:
        sp = t["speaker"]
        if sp == "tool":
            lines.append(f"[turn {t['turn']}] [TOOL] {t['tool']}({json.dumps(t.get('input', {}))})")
        else:
            lines.append(f"[turn {t['turn']}] [{sp}] {t['text']}")
    return "\n".join(lines)


def build_prompt(expectations: list[str], transcript_block: str) -> str:
    expectations_block = "\n".join(f"{i+1}. {e}" for i, e in enumerate(expectations))
    return f"""You are a sales call quality auditor. Review a transcript between Jon (an AI sales agent for a contractor-marketing service) and a prospect, and grade Jon against the expectations below.

For each expectation:
- Verdict: PASS or FAIL.
- Reason: ONE short sentence. Quote turn numbers when relevant (e.g., "turn 4").
- Be strict. If an expectation is even partially missed, FAIL it.
- Don't grade the prospect — only Jon.

Output ONLY a JSON array, no preamble, no markdown fences:
[
  {{"id": 1, "verdict": "PASS", "reason": "..."}},
  {{"id": 2, "verdict": "FAIL", "reason": "..."}}
]

EXPECTATIONS:
{expectations_block}

TRANSCRIPT:
{transcript_block}
"""


def parse_verdicts(raw: str) -> list[dict]:
    """Strip optional markdown fences, parse JSON array."""
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json ... ``` or ``` ... ```
        m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
        if m:
            raw = m.group(1).strip()
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--persona", required=True, type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / "config" / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if os.environ.get("OPENROUTER_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        else:
            sys.exit("Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set in config/.env")

    persona = yaml.safe_load(args.persona.read_text(encoding="utf-8"))
    expectations = persona.get("expectations") or []
    if not expectations:
        sys.exit(f"persona {persona.get('name', args.persona)} has no 'expectations:' field — add a list of plain-English checks to grade against")

    transcript_block = load_transcript(args.transcript)
    prompt = build_prompt(expectations, transcript_block)

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client = Anthropic(base_url=base_url) if base_url else Anthropic()

    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        temperature=JUDGE_TEMPERATURE,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    try:
        verdicts = parse_verdicts(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse failed: {e}", file=sys.stderr)
        print("---raw output---", file=sys.stderr)
        print(raw, file=sys.stderr)
        sys.exit(1)

    pass_count = sum(1 for v in verdicts if v.get("verdict") == "PASS")
    total = len(verdicts)

    print(f"\n=== Judge: {persona['name']}  |  {pass_count}/{total} pass ===")
    for v in verdicts:
        i = v.get("id", 0)
        verdict = v.get("verdict", "?")
        reason = v.get("reason", "")
        text = expectations[i - 1] if 1 <= i <= len(expectations) else "(unknown expectation)"
        marker = "✓" if verdict == "PASS" else "✗"
        print(f"  {marker} [{verdict}] {text}")
        print(f"        → {reason}")


if __name__ == "__main__":
    main()
