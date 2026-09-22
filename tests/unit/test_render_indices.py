"""Tests for voicsito/call-review/render.py — INDEX.md generation."""
from pathlib import Path

import yaml


def _write_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def test_render_checklist_index_groups_by_category(tmp_path, monkeypatch):
    base = tmp_path / "voicsito" / "call-review"
    cl = base / "checklist"

    _write_yaml(cl / "CL-001.yaml", {
        "id": "CL-001", "category": "voice_tts", "severity": "medium",
        "status": "confirmed",
        "question": "No audio glitches in Jon's speech?",
    })
    _write_yaml(cl / "CL-007.yaml", {
        "id": "CL-007", "category": "discovery", "severity": "high",
        "status": "confirmed",
        "question": "Did Jon recap pain before the booking ask?",
    })
    _write_yaml(cl / "CL-008.yaml", {
        "id": "CL-008", "category": "discovery", "severity": "medium",
        "status": "probationary",
        "question": "Did Jon ask one Pull-Teeth follow-up on a vague answer?",
    })

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "render", Path(__file__).resolve().parent.parent.parent / "voicsito" / "call-review" / "render.py"
    )
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)

    render.render_checklist(base / "checklist")

    rendered = (cl / "INDEX.md").read_text()
    # Header
    assert "# Checklist" in rendered
    # Both categories present, in alpha order
    assert rendered.index("## discovery") < rendered.index("## voice_tts")
    # All items appear
    assert "CL-001" in rendered
    assert "CL-007" in rendered
    assert "CL-008" in rendered
    # Severity + status surfaced
    assert "high" in rendered
    assert "probationary" in rendered


def test_render_changes_index_sorts_newest_first(tmp_path):
    base = tmp_path / "voicsito" / "call-review"
    chg = base / "changes"

    _write_yaml(chg / "CH-2026-05-20-001.yaml", {
        "id": "CH-2026-05-20-001", "created": "2026-05-20T14:32",
        "prompt_file": "src/agent/prompts/stage1_inbound_texts.py",
        "stage": "stage1",
        "section": "opener",
        "hypothesis": "Tighten opener to one breath",
    })
    _write_yaml(chg / "CH-2026-05-22-001.yaml", {
        "id": "CH-2026-05-22-001", "created": "2026-05-22T11:05",
        "prompt_file": "src/agent/prompts/stage2_qualify.py",
        "stage": "stage2",
        "section": "discovery — recap step",
        "hypothesis": "Add recap step before booking ask",
    })

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "render", Path(__file__).resolve().parent.parent.parent / "voicsito" / "call-review" / "render.py"
    )
    render = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(render)

    render.render_changes(chg)

    rendered = (chg / "INDEX.md").read_text()
    # Newest first
    assert rendered.index("CH-2026-05-22-001") < rendered.index("CH-2026-05-20-001")
    assert "recap step" in rendered
    assert "opener" in rendered
