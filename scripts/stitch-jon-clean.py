#!/usr/bin/env python3
"""Stitch Jon-only recordings into one ElevenLabs-ready FLAC.

Pipeline:
1. Concat all input files (ffmpeg decodes each — works across .wav/.m4a/.mp3/.flac)
2. Silence-remove (drop dead air > stop_duration, threshold stop_threshold)
3. Loudness-normalize (target -20 LUFS, true peak -3 dB) — kills call-to-call level variance
4. Encode FLAC (lossless, 44.1kHz mono)

Usage:
  # default: recordings/jon-only/*.wav → recordings/jon-clean.flac
  python scripts/stitch-jon-clean.py

  # custom (recursive, all common audio extensions):
  python scripts/stitch-jon-clean.py \\
      --input-dir "recordings/May 20 - recordings upload" \\
      --output recordings/jon-clean-may-20.flac
"""
import argparse
import subprocess
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "recordings" / "jon-only"
DEFAULT_OUT = ROOT / "recordings" / "jon-clean.flac"
AUDIO_EXTS = {".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg"}


def collect_files(src_dir: Path) -> list[Path]:
    """Recursively gather audio files, sorted by path."""
    return sorted(
        p for p in src_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--silence-threshold", default="-45dB",
                    help="silenceremove stop_threshold (default -45dB)")
    ap.add_argument("--silence-duration", default="1.0",
                    help="silenceremove stop_duration seconds (default 1.0)")
    args = ap.parse_args()

    SRC_DIR = args.input_dir
    OUT = args.output

    files = collect_files(SRC_DIR)
    if not files:
        sys.exit(f"no audio files in {SRC_DIR}")
    print(f"input:  {len(files)} files in {SRC_DIR}")
    total_bytes = sum(f.stat().st_size for f in files)
    print(f"total:  {total_bytes / 1_000_000_000:.2f} GB raw WAV")
    print()

    # Build concat list (ffmpeg demands single-quoted absolute paths, escape inner ')
    # Paths in the list are resolved relative to the listfile, so we must write absolute paths.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        listfile = Path(f.name)
        for p in files:
            esc = str(p.resolve()).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")

    print(f"concat list: {listfile}")
    print(f"output:      {OUT}")
    print()

    # silenceremove: stop_periods=-1 drops ALL silences ≥ stop_duration at threshold
    # loudnorm: single-pass dynamic — ElevenLabs cares about consistency, not perfect LUFS
    af = (
        f"silenceremove="
        f"stop_periods=-1:stop_duration={args.silence_duration}:stop_threshold={args.silence_threshold},"
        f"loudnorm=I=-20:TP=-3:LRA=7"
    )

    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-af", af,
        "-ar", "44100",      # downsample 48kHz → 44.1kHz (ElevenLabs accepts; smaller file)
        "-ac", "1",
        "-c:a", "flac",
        "-compression_level", "8",
        str(OUT),
    ]
    print("running:", " ".join(cmd[:6]), "...")
    print()
    rc = subprocess.call(cmd)
    listfile.unlink()
    if rc != 0:
        sys.exit(f"ffmpeg failed rc={rc}")

    # Stats
    out_size = OUT.stat().st_size
    probe = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(OUT),
    ]).decode().strip()
    duration_s = float(probe)
    minutes = duration_s / 60

    print()
    print("=" * 60)
    print(f"OUTPUT: {OUT}")
    print(f"  size:     {out_size / 1_000_000:.1f} MB")
    print(f"  duration: {minutes:.1f} min ({duration_s:.0f}s)")
    print(f"  format:   FLAC, 44.1 kHz, mono, lossless")
    print()
    print("ElevenLabs PVC wants 30 min min, 2+ hr ideal.")
    print(f"  → {'✓' if minutes >= 30 else '✗'} 30-min minimum {'met' if minutes >= 30 else 'NOT MET'}")
    print(f"  → {'✓' if minutes >= 120 else '·'} 2-hour ideal {'met' if minutes >= 120 else 'not met (acceptable)'}")


if __name__ == "__main__":
    main()
