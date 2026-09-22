"""Tests for scripts/snapshot_reference_point.py."""
from pathlib import Path


def test_snapshot_creates_directory_with_prompts_and_restore(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "src" / "agent" / "prompts").mkdir(parents=True)
    (repo / "src" / "agent" / "prompts" / "stage1_inbound_texts.py").write_text("PROMPT = 'v1'\n")
    (repo / "src" / "agent" / "prompts" / "stage2_qualify.py").write_text("PROMPT = 'v1'\n")
    (repo / "src" / "agent" / "prompts" / "stage3_book.py").write_text("PROMPT = 'v1'\n")
    (repo / "src" / "agent" / "prompts" / "shared_persona.py").write_text("PERSONA = 'v1'\n")
    (repo / "docs").mkdir()

    from scripts.snapshot_reference_point import create_snapshot

    snap_dir = create_snapshot(
        repo_root=repo,
        date="2026-05-22",
        short_reason="recap-fix",
        long_reason="Added recap step before booking ask, per CL-010.",
    )

    assert snap_dir == repo / "docs" / "reference-points" / "2026-05-22-recap-fix"
    assert snap_dir.is_dir()
    assert (snap_dir / "stage1_inbound_texts.py").read_text() == "PROMPT = 'v1'\n"
    assert (snap_dir / "stage2_qualify.py").read_text() == "PROMPT = 'v1'\n"
    assert (snap_dir / "stage3_book.py").read_text() == "PROMPT = 'v1'\n"
    assert (snap_dir / "shared_persona.py").read_text() == "PERSONA = 'v1'\n"
    restore = (snap_dir / "RESTORE.md").read_text()
    assert "2026-05-22" in restore
    assert "recap-fix" in restore
    assert "Added recap step before booking ask" in restore
