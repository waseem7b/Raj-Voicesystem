#!/usr/bin/env python3
"""Extract prospect-only audio (everyone-but-Jon) from mono call recordings.

Sister script to extract-jon.py. Same Deepgram diarization, same heuristic to
identify Jon's speaker label — then takes ALL OTHER speaker labels and emits
those segments as the prospect's audio.

Pipeline per file:
  1. Send to Deepgram nova-3 with diarize=true → word-level speaker labels
  2. Identify Jon (same heuristic as extract-jon.py)
  3. Merge consecutive non-Jon words into segments [(start, end), ...]
  4. ffmpeg cuts those segments and concatenates them into recordings/prospect-only/<name>.wav

Cached: skips files where the output already exists.
Skips files in recordings/suspect/ (Jon diarization was unreliable).

Run from project root:
    .venv-extract/bin/python scripts/extract-prospect.py
"""

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
PROSPECT_DIR = ROOT / "recordings" / "prospect-only"
SUSPECT_DIR = ROOT / "recordings" / "suspect"

PROSPECT_DIR.mkdir(parents=True, exist_ok=True)


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


JON_NAMES = {"jon", "john"}


def _tok(w: dict) -> str:
    return (w.get("punctuated_word") or w.get("word") or "").lower().strip(".,!?;:'\"")


def identify_jon(words: List[dict]) -> int:
    """Identify Jon's speaker label. Same logic as extract-jon.py."""
    early = [w for w in words if w["start"] <= 90]
    tokens = [(w["speaker"], _tok(w)) for w in early]

    for i, (spk, tok) in enumerate(tokens):
        if tok not in JON_NAMES:
            continue
        prev1 = tokens[i - 1][1] if i >= 1 else ""
        prev2 = tokens[i - 2][1] if i >= 2 else ""
        if prev2 == "this" and prev1 == "is":
            return spk
        if prev1 in {"i'm", "im"}:
            return spk

    for spk, tok in tokens:
        if tok in JON_NAMES:
            return spk

    times: dict = {}
    for w in words:
        spk = w["speaker"]
        times[spk] = times.get(spk, 0.0) + (w["end"] - w["start"])
    return max(times, key=times.get)


def prospect_segments(words: List[dict], jon: int) -> List[Tuple[float, float]]:
    """Inverse of jon_segments — keep every speaker that is NOT Jon."""
    segs: List[Tuple[float, float]] = []
    cur_start = None
    cur_end = None
    for w in words:
        if w["speaker"] != jon:
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
    # Merge adjacent segments separated by < 0.5s
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
    out = PROSPECT_DIR / f.name
    if out.exists() and out.stat().st_size > 0:
        return f"  [{idx:2d}/{total}] {f.name} (cached)"
    try:
        resp = diarize(f)
        ch = resp["results"]["channels"][0]
        words = ch["alternatives"][0]["words"]
        if not words:
            return f"  [{idx:2d}/{total}] {f.name} — no speech, skipping"
        jon = identify_jon(words)
        segs = prospect_segments(words, jon)
        if not segs:
            return f"  [{idx:2d}/{total}] {f.name} — no prospect speech detected"
        cut_audio(f, segs, out)
        prospect_dur = sum(e - s for s, e in segs)
        call_total = words[-1]["end"]
        pct = 100 * prospect_dur / call_total if call_total > 0 else 0
        return (f"  [{idx:2d}/{total}] {f.name} — Jon=spkr{jon}  "
                f"prospect={prospect_dur:5.0f}s of {call_total:5.0f}s ({pct:.0f}%)")
    except Exception as e:
        return f"  [{idx:2d}/{total}] {f.name} — FAILED: {e}"


def main():
    suspect_names = {p.name for p in SUSPECT_DIR.glob("*.wav")} if SUSPECT_DIR.exists() else set()
    files = [f for f in sorted(RAW_DIR.glob("*.wav")) if f.name not in suspect_names]
    if not files:
        print(f"✗ No .wav files in {RAW_DIR}")
        sys.exit(1)
    print(f"Diarizing {len(files)} files via Deepgram nova-3 (8 concurrent)...")
    if suspect_names:
        print(f"Skipping {len(suspect_names)} suspect files: {sorted(suspect_names)}")
    print()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(process_one, f, i, len(files)): i
                   for i, f in enumerate(files, 1)}
        for fut in as_completed(futures):
            print(fut.result())

    out_files = sorted(PROSPECT_DIR.glob("*.wav"))
    if not out_files:
        print("\n✗ No output files.")
        sys.exit(1)
    # Total duration summary
    total_dur = 0.0
    for f in out_files:
        try:
            d = float(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(f)]
            ).strip())
            total_dur += d
        except Exception:
            pass
    print()
    print("=" * 60)
    print(f"  ✓ {len(out_files)} prospect-only files in {PROSPECT_DIR}")
    print(f"    Total duration: {total_dur/60:.1f} minutes ({int(total_dur)}s)")
    print("=" * 60)


if __name__ == "__main__":
    main()
