#!/usr/bin/env python3
"""
Audio glitch detector for Voicsito (LiveKit + ElevenLabs voice agent QA).

Scans an audio file (or directory) for:
  1. Mid-word silence gaps  (the "wha-...-wha" stop-restart pattern)
  2. Doubled syllables / repeated audio (sliding cross-correlation)
  3. Clipping (amplitude saturation)
  4. Abnormal RMS variance within speech (uneven loudness / cuts)

Install (one-time):
    .venv-extract/bin/pip install librosa numpy scipy soundfile

Run:
    .venv-extract/bin/python scripts/audio-glitch-check.py audio/opener.mp3
    .venv-extract/bin/python scripts/audio-glitch-check.py recordings/
    .venv-extract/bin/python scripts/audio-glitch-check.py audio/opener.mp3 --json

Tuning:
    --threshold-gap-ms 50   Gaps shorter than this are ignored (default 50ms)
    --threshold-gap-ms-max 400  Gaps longer than this are likely real pauses (default 400ms)
    --threshold-corr 0.85   Cross-correlation above this flags repeats (default 0.85)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    import librosa
except ImportError:
    sys.stderr.write(
        "ERROR: librosa not installed. Run:\n"
        "  .venv-extract/bin/pip install librosa numpy scipy soundfile\n"
    )
    sys.exit(2)


AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}


# ----------------------------- data ---------------------------------------- #

@dataclass
class Issue:
    kind: str
    start_s: float | None = None
    end_s: float | None = None
    detail: str = ""

    def human(self) -> str:
        if self.start_s is not None and self.end_s is not None:
            return f"{self.kind}: {self.start_s:.2f}s -> {self.end_s:.2f}s ({self.detail})"
        return f"{self.kind}: {self.detail}"


@dataclass
class FileReport:
    path: str
    duration_s: float
    sample_rate: int
    channels: int
    issues: list[Issue] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)  # check_name -> passed
    rms_stddev_db: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issues"] = [asdict(i) for i in self.issues]
        return d


# --------------------------- helpers --------------------------------------- #

def _rms_db(x: np.ndarray) -> float:
    rms = np.sqrt(np.mean(x.astype(np.float64) ** 2) + 1e-12)
    return 20.0 * np.log10(rms + 1e-12)


def _load(path: Path) -> tuple[np.ndarray, int, int]:
    """Load audio as mono float32 in [-1, 1]. Returns (y, sr, channels)."""
    # First peek at native channel count for the report.
    try:
        import soundfile as sf
        info = sf.info(str(path))
        channels = info.channels
    except Exception:
        channels = 1
    y, sr = librosa.load(str(path), sr=None, mono=True)
    return y.astype(np.float32), int(sr), int(channels)


# --------------------------- detectors ------------------------------------- #

def detect_clipping(y: np.ndarray, threshold: float = 0.99,
                    pct_threshold: float = 0.001) -> tuple[bool, Issue | None, float]:
    """Flag if more than pct_threshold (default 0.1%) of samples saturate."""
    n_clip = int(np.sum(np.abs(y) >= threshold))
    pct = n_clip / max(len(y), 1)
    if pct > pct_threshold:
        return False, Issue(
            kind="Clipping",
            detail=f"{n_clip} samples clipped ({pct*100:.3f}% of audio)",
        ), pct
    return True, None, pct


def detect_silence_gaps(y: np.ndarray, sr: int,
                        gap_min_ms: float = 50.0,
                        gap_max_ms: float = 400.0,
                        speech_top_db: float = 35.0,
                        gap_floor_db: float = -50.0) -> list[Issue]:
    """
    Find non-silent intervals via librosa.effects.split, then look at GAPS
    between them. A gap is suspicious if:
      - duration is in [gap_min_ms, gap_max_ms]
      - it is bordered by speech on BOTH sides (not start/end of file)
      - the gap-region RMS is below gap_floor_db
    """
    intervals = librosa.effects.split(y, top_db=speech_top_db)
    if len(intervals) < 2:
        return []

    issues: list[Issue] = []
    gap_min = gap_min_ms / 1000.0
    gap_max = gap_max_ms / 1000.0

    for i in range(len(intervals) - 1):
        prev_end = intervals[i][1]
        next_start = intervals[i + 1][0]
        gap_samples = next_start - prev_end
        gap_s = gap_samples / sr
        if gap_s < gap_min or gap_s > gap_max:
            continue

        # Confirm prev/next are real speech (not a tiny blip).
        prev_dur = (intervals[i][1] - intervals[i][0]) / sr
        next_dur = (intervals[i + 1][1] - intervals[i + 1][0]) / sr
        if prev_dur < 0.08 or next_dur < 0.08:
            continue

        gap_audio = y[prev_end:next_start]
        if gap_audio.size == 0:
            continue
        gap_db = _rms_db(gap_audio)
        if gap_db > gap_floor_db:
            continue

        start_s = prev_end / sr
        end_s = next_start / sr
        ms = int(gap_s * 1000)
        issues.append(Issue(
            kind="Mid-word silence gap",
            start_s=start_s,
            end_s=end_s,
            detail=f"{ms}ms drop to {gap_db:.1f}dB amid speech",
        ))
    return issues


def detect_doubled_syllables(y: np.ndarray, sr: int,
                             win_ms: float = 200.0,
                             hop_ms: float = 100.0,
                             corr_threshold: float = 0.85,
                             min_energy_db: float = -45.0) -> list[Issue]:
    """
    Sliding-window normalized cross-correlation. For each window W_i, compare
    against the *next non-overlapping* window W_{i+1}. High zero-lag normalized
    correlation suggests the audio is repeating.
    """
    win = int(sr * win_ms / 1000.0)
    hop = int(sr * hop_ms / 1000.0)
    if win <= 0 or hop <= 0 or len(y) < 2 * win:
        return []

    issues: list[Issue] = []
    last_flag_end = -1.0  # to avoid spamming overlapping flags

    for start in range(0, len(y) - 2 * win, hop):
        a = y[start:start + win]
        b = y[start + win:start + 2 * win]
        # Skip silence — repetition there is uninteresting.
        if _rms_db(a) < min_energy_db or _rms_db(b) < min_energy_db:
            continue
        a0 = a - a.mean()
        b0 = b - b.mean()
        denom = (np.linalg.norm(a0) * np.linalg.norm(b0)) + 1e-12
        corr = float(np.dot(a0, b0) / denom)
        if corr >= corr_threshold:
            s = start / sr
            e = (start + 2 * win) / sr
            if s < last_flag_end:
                continue
            last_flag_end = e
            issues.append(Issue(
                kind="Doubled syllable / repeat",
                start_s=s,
                end_s=e,
                detail=f"corr={corr:.3f} between adjacent {int(win_ms)}ms windows",
            ))
    return issues


def detect_rms_variance(y: np.ndarray, sr: int,
                        frame_ms: float = 100.0,
                        speech_top_db: float = 35.0,
                        stddev_threshold_db: float = 12.0
                        ) -> tuple[bool, Issue | None, float]:
    """Compute RMS-in-dB across non-silent regions; flag high std-dev."""
    intervals = librosa.effects.split(y, top_db=speech_top_db)
    if len(intervals) == 0:
        return True, None, 0.0

    frame = int(sr * frame_ms / 1000.0)
    if frame <= 0:
        return True, None, 0.0

    db_values: list[float] = []
    for s, e in intervals:
        seg = y[s:e]
        for i in range(0, len(seg) - frame, frame):
            chunk = seg[i:i + frame]
            db_values.append(_rms_db(chunk))

    if len(db_values) < 3:
        return True, None, 0.0

    arr = np.array(db_values)
    # Use robust std via clipping outliers slightly so a single dip doesn't dominate.
    std = float(np.std(arr))
    if std > stddev_threshold_db:
        return False, Issue(
            kind="Abnormal RMS variance",
            detail=f"{std:.1f}dB stddev across speech (threshold {stddev_threshold_db:.0f}dB)",
        ), std
    return True, None, std


# --------------------------- runner ---------------------------------------- #

def analyse(path: Path, gap_min_ms: float, gap_max_ms: float,
            corr_threshold: float) -> FileReport:
    try:
        y, sr, channels = _load(path)
    except Exception as exc:
        return FileReport(
            path=str(path), duration_s=0.0, sample_rate=0, channels=0,
            error=f"load failed: {exc}",
        )

    duration = len(y) / sr if sr else 0.0
    report = FileReport(
        path=str(path),
        duration_s=duration,
        sample_rate=sr,
        channels=channels,
    )

    # 1. Clipping
    ok, issue, _pct = detect_clipping(y)
    report.checks["clipping"] = ok
    if issue:
        report.issues.append(issue)

    # 2. Mid-word silence gaps
    gap_issues = detect_silence_gaps(
        y, sr, gap_min_ms=gap_min_ms, gap_max_ms=gap_max_ms,
    )
    report.checks["silence_gaps"] = not gap_issues
    report.issues.extend(gap_issues)

    # 3. Doubled syllables
    dup_issues = detect_doubled_syllables(y, sr, corr_threshold=corr_threshold)
    report.checks["doubled_syllables"] = not dup_issues
    report.issues.extend(dup_issues)

    # 4. RMS variance
    ok, issue, std = detect_rms_variance(y, sr)
    report.checks["rms_variance"] = ok
    report.rms_stddev_db = round(std, 2)
    if issue:
        report.issues.append(issue)

    return report


def iter_audio_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if Path(name).suffix.lower() in AUDIO_EXTS:
                yield Path(dirpath) / name


def print_human(report: FileReport) -> None:
    if report.error:
        print(f"Audio: {report.path}")
        print(f"  ERROR: {report.error}")
        return

    print(f"Audio: {report.path}  "
          f"({report.duration_s:.1f}s, {report.sample_rate/1000:.1f}kHz "
          f"{'mono' if report.channels == 1 else f'{report.channels}ch'})")

    issue_kinds = {i.kind for i in report.issues}

    def line(passed: bool, ok_msg: str, issue_kind: str) -> None:
        if passed:
            print(f"  OK  {ok_msg}")
        else:
            for it in report.issues:
                if it.kind == issue_kind:
                    print(f"  FAIL  {it.human()}")

    line(report.checks.get("clipping", True), "No clipping", "Clipping")
    line(report.checks.get("silence_gaps", True),
         "No mid-word silence gaps", "Mid-word silence gap")
    line(report.checks.get("doubled_syllables", True),
         "No doubled syllables detected", "Doubled syllable / repeat")
    rms_msg = (f"RMS variance within bounds ({report.rms_stddev_db}dB stddev)"
               if report.rms_stddev_db is not None
               else "RMS variance within bounds")
    line(report.checks.get("rms_variance", True), rms_msg, "Abnormal RMS variance")

    n = len(report.issues)
    print(f"  RESULT: {n} issue{'s' if n != 1 else ''} found")
    print()


def main() -> int:
    p = argparse.ArgumentParser(
        description="Detect audio glitches in Voicsito output (LiveKit/ElevenLabs).",
    )
    p.add_argument("target", help="Audio file OR directory to scan recursively")
    p.add_argument("--json", action="store_true", help="Emit JSON report")
    p.add_argument("--threshold-gap-ms", type=float, default=50.0,
                   help="Min gap duration to consider suspicious (default 50)")
    p.add_argument("--threshold-gap-ms-max", type=float, default=400.0,
                   help="Max gap duration before treating as a normal pause (default 400)")
    p.add_argument("--threshold-corr", type=float, default=0.85,
                   help="Cross-correlation threshold for repeats (default 0.85)")
    p.add_argument("--all", action="store_true",
                   help="In directory mode, list every file (default: only those with issues)")
    args = p.parse_args()

    target = Path(args.target).expanduser()
    if not target.exists():
        sys.stderr.write(f"ERROR: path not found: {target}\n")
        return 2

    files = list(iter_audio_files(target))
    if not files:
        sys.stderr.write(f"ERROR: no audio files found under {target}\n")
        return 2

    reports: list[FileReport] = []
    for f in files:
        reports.append(analyse(
            f,
            gap_min_ms=args.threshold_gap_ms,
            gap_max_ms=args.threshold_gap_ms_max,
            corr_threshold=args.threshold_corr,
        ))

    if args.json:
        out = {
            "target": str(target),
            "files_scanned": len(reports),
            "files_with_issues": sum(1 for r in reports if r.issues),
            "reports": [r.to_dict() for r in reports],
        }
        print(json.dumps(out, indent=2))
    else:
        shown = 0
        for r in reports:
            if not args.all and target.is_dir() and not r.issues and not r.error:
                continue
            print_human(r)
            shown += 1
        total_issues = sum(len(r.issues) for r in reports)
        with_issues = sum(1 for r in reports if r.issues)
        print(f"Scanned {len(reports)} file(s); {with_issues} with issues; "
              f"{total_issues} total issue(s).")
        if target.is_dir() and not args.all and shown == 0:
            print("(no issues found in any file)")

    # Exit non-zero if any issues found, so this composes in CI.
    return 1 if any(r.issues for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
