"""Tests for scripts/check_checklist_sync.py — atomic-rule enforcement."""
import hashlib
from pathlib import Path

import pytest
import yaml


def _write_yaml(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_clean_state_returns_zero_drift(tmp_path):
    from scripts.check_checklist_sync import scan

    prompts_dir = tmp_path / "src" / "agent" / "prompts"
    prompts_dir.mkdir(parents=True)
    p = prompts_dir / "stage1_inbound_texts.py"
    p.write_text("PROMPT = 'hello'\n")

    changes_dir = tmp_path / "voicsito" / "call-review" / "changes"
    changes_dir.mkdir(parents=True)
    _write_yaml(changes_dir / "CH-2026-05-22-001.yaml", {
        "id": "CH-2026-05-22-001",
        "prompt_file": "src/agent/prompts/stage1_inbound_texts.py",
        "prompt_file_sha256_before": "ignored",
        "prompt_file_sha256_after": _sha256("PROMPT = 'hello'\n"),
        "escape_reason": "",
    })

    drifts = scan(repo_root=tmp_path)
    assert drifts == []


def test_drift_detected_when_prompt_changed_without_log_entry(tmp_path):
    from scripts.check_checklist_sync import scan

    prompts_dir = tmp_path / "src" / "agent" / "prompts"
    prompts_dir.mkdir(parents=True)
    p = prompts_dir / "stage1_inbound_texts.py"
    p.write_text("PROMPT = 'NEW VERSION'\n")  # current

    changes_dir = tmp_path / "voicsito" / "call-review" / "changes"
    changes_dir.mkdir(parents=True)
    _write_yaml(changes_dir / "CH-2026-05-22-001.yaml", {
        "id": "CH-2026-05-22-001",
        "prompt_file": "src/agent/prompts/stage1_inbound_texts.py",
        "prompt_file_sha256_before": "ignored",
        "prompt_file_sha256_after": _sha256("PROMPT = 'OLD VERSION'\n"),  # stale
        "escape_reason": "",
    })

    drifts = scan(repo_root=tmp_path)
    assert len(drifts) == 1
    assert drifts[0]["prompt_file"].endswith("stage1_inbound_texts.py")
    assert "no logged change" in drifts[0]["reason"] or "sha mismatch" in drifts[0]["reason"]


def test_prompt_with_no_change_entry_at_all_is_drift(tmp_path):
    from scripts.check_checklist_sync import scan

    prompts_dir = tmp_path / "src" / "agent" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "stage1_inbound_texts.py").write_text("PROMPT = 'untracked'\n")

    (tmp_path / "voicsito" / "call-review" / "changes").mkdir(parents=True)

    drifts = scan(repo_root=tmp_path)
    assert len(drifts) == 1
    assert "no change entries" in drifts[0]["reason"]


def test_escape_reason_clears_drift(tmp_path):
    from scripts.check_checklist_sync import scan

    prompts_dir = tmp_path / "src" / "agent" / "prompts"
    prompts_dir.mkdir(parents=True)
    p = prompts_dir / "stage1_inbound_texts.py"
    p.write_text("PROMPT = 'NEW'\n")

    changes_dir = tmp_path / "voicsito" / "call-review" / "changes"
    changes_dir.mkdir(parents=True)
    _write_yaml(changes_dir / "CH-2026-05-22-001.yaml", {
        "id": "CH-2026-05-22-001",
        "prompt_file": "src/agent/prompts/stage1_inbound_texts.py",
        "prompt_file_sha256_before": "ignored",
        "prompt_file_sha256_after": _sha256("PROMPT = 'NEW'\n"),
        "escape_reason": "",
    })
    # second change with mismatched sha but with escape_reason — should clear drift
    _write_yaml(changes_dir / "CH-2026-05-22-002.yaml", {
        "id": "CH-2026-05-22-002",
        "prompt_file": "src/agent/prompts/stage1_inbound_texts.py",
        "prompt_file_sha256_before": _sha256("PROMPT = 'NEW'\n"),
        "prompt_file_sha256_after": "deadbeef",  # intentionally wrong
        "escape_reason": "comment-only edit; no behavior change",
    })

    drifts = scan(repo_root=tmp_path)
    assert drifts == []  # escape reason on the latest change clears the drift


def test_init_files_are_ignored(tmp_path):
    from scripts.check_checklist_sync import scan

    prompts_dir = tmp_path / "src" / "agent" / "prompts"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "__init__.py").write_text("")
    (tmp_path / "voicsito" / "call-review" / "changes").mkdir(parents=True)

    drifts = scan(repo_root=tmp_path)
    assert drifts == []
