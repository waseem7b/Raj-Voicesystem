#!/usr/bin/env python3
"""Extract Jon-only audio from mono call recordings via speaker diarization.

The recordings in recordings/raw/ are mono 8kHz WAV files with Jon and the
prospect mixed on the same channel. Channel-split won't work — we need
speaker diarization to identify which words came from Jon.

Pipeline per file:
  1. Send to Deepgram nova-3 with diarize=true → word-level speaker labels
  2. Identify which speaker is Jon (heuristic: first to say "Jon" within the
     first 30 seconds of the call; falls back to speaker with more total time)
  3. Merge consecutive Jon-words into time segments [(start, end), ...]
  4. ffmpeg cuts those segments and concatenates them into recordings/jon-only/<name>.wav
  5. After all files done, all jon-only/ WAVs are concatenated + loudness-normalized
     into recordings/jon-combined.wav, ready for ElevenLabs upload

Run from project root:
    .venv-extract/bin/python scripts/extract-jon.py
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

DEEPGRAM_KEY = os.environ["DEEPGRAM_API_KEY"]
RAW_DIR = ROOT / "recordings" / "raw"
JON_DIR = ROOT / "recordings" / "jon-only"
OUT = ROOT / "recordings" / "jon-combined.wav"

JON_DIR.mkdir(parents=True, exist_ok=True)


def diarize(path: Path) -> dict:
    with open(path, "rb") as f:
        audio = f.read()
    r = httpx.post(
        "https://api.deepgram.com/v1/listen",
        params={
            "model": "nova-3",
            "language": "en",
            "diarize": "true",
            "smart_format": "true",
            "punctuate": "true",
        },
        headers={
            "Authorization": f"Token {DEEPGRAM_KEY}",
            "Content-Type": "audio/wav",
        },
        content=audio,
        timeout=600.0,
    )
    r.raise_for_status()
    return r.json()


JON_NAMES = {"jon", "john"}  # Deepgram smart_format usually writes "John"


def _tok(w: dict) -> str:
    return (w.get("punctuated_word") or w.get("word") or "").lower().strip(".,!?;:'\"")


def identify_jon(words: List[dict]) -> int:
    """Identify Jon's speaker label.

    Priority:
      1. The speaker who introduces themselves as Jon: "this is jon/john" or "i'm jon".
         This is the gold-standard signal — the SELF-introduction.
      2. First speaker to UTTER "jon"/"john" within the first 90s. Less reliable
         (the prospect may say Jon's name back), but in practice Jon says his
         own name first because his opener leads with it.
      3. Fallback: speaker with more total speech time. Last-ditch — wrong
         when the prospect dominates the call.
    """
    early = [w for w in words if w["start"] <= 90]
    tokens = [(w["speaker"], _tok(w)) for w in early]

    # Pattern 1: "this is jon/john" or "i'm jon/john"
    for i, (spk, tok) in enumerate(tokens):
        if tok not in JON_NAMES:
            continue
        prev1 = tokens[i - 1][1] if i >= 1 else ""
        prev2 = tokens[i - 2][1] if i >= 2 else ""
        if prev2 == "this" and prev1 == "is":
            return spk
        if prev1 in {"i'm", "im"}:
            return spk

    # Pattern 2: first to say jon/john at all
    for spk, tok in tokens:
        if tok in JON_NAMES:
            return spk

    # Fallback: more total time
    times: dict = {}
    for w in words:
        spk = w["speaker"]
        times[spk] = times.get(spk, 0.0) + (w["end"] - w["start"])
    return max(times, key=times.get)


def jon_segments(words: List[dict], jon: int) -> List[Tuple[float, float]]:
    segs: List[Tuple[float, float]] = []
    cur_start = None
    cur_end = None
    for w in words:
        if w["speaker"] == jon:
            if cur_start is None:
                cur_start = w["start"]
            cur_end = w["end"]
        else:
            if cur_start is not None:
                segs.append((cur_start, cur_end))
                cur_start = None
                cur_end = None
    if cur_start is not None:
        segs.append((cur_start, cur_end))
    # Merge adjacent segments separated by < 0.5s (preserves natural cadence)
    merged: List[Tuple[float, float]] = []
    for s, e in segs:
        if merged and s - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # Drop tiny segments (<0.5s) — likely noise or backchannel mis-attribution
    return [(s, e) for s, e in merged if e - s >= 0.5]


def cut_audio(src: Path, segs: List[Tuple[float, float]], dst: Path) -> bool:
    if not segs:
        return False
    # Build atrim filter chain to extract each segment, then concat
    parts = [
        f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        for i, (s, e) in enumerate(segs)
    ]
    inputs = "".join(f"[a{i}]" for i in range(len(segs)))
    filt = ";".join(parts) + ";" + inputs + f"concat=n={len(segs)}:v=0:a=1[out]"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-filter_complex", filt,
            "-map", "[out]",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le",
            str(dst),
        ],
        check=True,
    )
    return True


def process_one(f: Path, idx: int, total: int) -> str:
    out = JON_DIR / f.name
    if out.exists() and out.stat().st_size > 0:
        return f"  [{idx:2d}/{total}] {f.name} (cached)"
    try:
        resp = diarize(f)
        ch = resp["results"]["channels"][0]
        words = ch["alternatives"][0]["words"]
        if not words:
            return f"  [{idx:2d}/{total}] {f.name} — no speech, skipping"
        jon = identify_jon(words)
        segs = jon_segments(words, jon)
        cut_audio(f, segs, out)
        jon_dur = sum(e - s for s, e in segs)
        call_total = words[-1]["end"]
        pct = 100 * jon_dur / call_total if call_total > 0 else 0
        return f"  [{idx:2d}/{total}] {f.name} — Jon=spkr{jon}  {jon_dur:5.0f}s of {call_total:5.0f}s ({pct:.0f}%)"
    except Exception as e:
        return f"  [{idx:2d}/{total}] {f.name} — FAILED: {e}"


def main():
    files = sorted(RAW_DIR.glob("*.wav"))
    if not files:
        print(f"✗ No .wav files in {RAW_DIR}")
        sys.exit(1)
    print(f"Diarizing {len(files)} files via Deepgram nova-3 (8 concurrent)...")
    print()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(process_one, f, i, len(files)): i
                   for i, f in enumerate(files, 1)}
        # Print results in submission order so output is readable
        results = [None] * len(files)
        for fut in as_completed(futures):
            results[futures[fut] - 1] = fut.result()
            print(fut.result())

    # Concatenate all per-call Jon WAVs into one master file
    out_files = sorted(JON_DIR.glob("*.wav"))
    if not out_files:
        print("\n✗ No output files. Aborting concat.")
        sys.exit(1)
    list_path = Path("/tmp/jon_concat.txt")
    list_path.write_text("\n".join(f"file '{f.absolute()}'" for f in out_files))
    print(f"\nConcatenating {len(out_files)} Jon-only files → {OUT.name} ...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-af", "loudnorm=I=-18:TP=-2:LRA=8",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le",
            str(OUT),
        ],
        check=True,
    )
    dur = float(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(OUT)]
        ).strip()
    )
    size_mb = OUT.stat().st_size / 1e6
    print()
    print("=" * 60)
    print(f"  ✓ {OUT}")
    print(f"    Duration: {dur/60:.1f} minutes ({int(dur)}s)   Size: {size_mb:.1f} MB")
    print("=" * 60)
    if dur < 60:
        print("  ⚠  Less than 1 min — too short even for IVC.")
    elif dur < 300:
        print("  → Enough for ElevenLabs Instant Voice Clone (IVC).")
    elif dur < 1800:
        print("  → IVC ready. PVC needs ≥30 min — keep going if you want PVC.")
    else:
        print("  → Enough for Professional Voice Clone (PVC).")


if __name__ == "__main__":
    main()
