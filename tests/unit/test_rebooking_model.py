"""Rebooking's model: gpt-4.1, with the per-flow override machinery unused.

Owner's model trials on 2026-09-15/16 (4.1-mini, 5.5, 5.4-mini, 5.4) all lost on
latency, cost or fidelity — see the comment on the rebooking FLOWS entry. This
test keeps the revert in place and keeps the override keys wired for next time.
"""
import pathlib


def _src() -> str:
    return (pathlib.Path(__file__).resolve().parents[2]
            / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")


def test_no_model_override_on_any_flow():
    src = _src()
    assert '"model": "gpt-5' not in src
    assert '"model": "gpt-4.1-mini"' not in src
    assert '"reasoning_effort":' not in src


def test_rebooking_falls_back_to_gpt_41():
    src = _src()
    rebooking = src.split('"rebooking": {', 1)[1].split('"test": {', 1)[0]
    assert '"model":' not in rebooking
    assert 'flow.get("model", "gpt-4.1")' in src
    # The override machinery stays wired up for the next trial.
    assert '"reasoningEffort": flow["reasoning_effort"]' in src
