#!/usr/bin/env python3
"""Compare two audio files (e.g., reference Jon vs. agent output) on voice-clone metrics.

Computes side-by-side metrics so we can quantify how Jon-like the agent sounds:
  - Cadence: duration, words-per-minute, pause distribution
  - Pitch profile (F0): mean, stddev (via librosa.pyin)
  - Energy / dynamics: RMS mean (dBFS), RMS stddev
  - Vocabulary: top-20 words, Jaccard similarity, filler-word frequency
  - Speech-rate consistency: onset-based syllable-rate stddev

Transcripts come from Deepgram nova-3 (same model used for diarization), cached
under recordings/.transcripts/ keyed by file mtime+size to keep reruns cheap.

Usage:
    .venv-extract/bin/python scripts/compare-jon.py \
        --reference recordings/jon-only/Jeremiah.wav \
        --test recordings/jon-only/Aaron\\ Landscaping.wav

    # JSON output:
    .venv-extract/bin/python scripts/compare-jon.py \
        --reference <ref> --test <test> --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import httpx
import librosa
import numpy as np
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")

DEEPGRAM_KEY = os.environ.get("DEEPGRAM_API_KEY")
TRANSCRIPT_CACHE = ROOT / "recordings" / ".transcripts"
TRANSCRIPT_CACHE.mkdir(parents=True, exist_ok=True)

# Common English stopwords (kept tight — we want vocabulary signature, not lit theory)
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "by",
    "for", "with", "from", "as", "is", "am", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "what", "which", "who", "whom",
    "where", "when", "why", "how", "not", "no", "yes", "so", "just", "now", "then",
    "there", "here", "out", "up", "down", "off", "on", "over", "under", "again",
    "into", "than", "too", "very", "s", "t", "d", "ll", "m", "re", "ve", "y",
    "any", "all", "some", "more", "most", "other", "such", "only", "own", "same",
    "about", "because", "while", "during", "before", "after", "above", "below",
    "between", "through", "against", "without", "within",
}

# Filler tokens we count separately (not removed from totals)
FILLERS_SINGLE = {"uh", "um", "uhh", "umm", "er", "ah", "hmm", "yeah", "right", "so",
                  "like", "okay", "ok"}
FILLERS_PHRASES = ["you know", "i mean", "kind of", "sort of"]


def _cache_key(path: Path) -> str:
    st = path.stat()
    h = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()
    return h[:16]


def transcribe(path: Path) -> dict:
    """Deepgram nova-3 transcript with word timings. Cached by file size+mtime."""
    cache_path = TRANSCRIPT_CACHE / f"{_cache_key(path)}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    if not DEEPGRAM_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not set in config/.env")
    with open(path, "rb") as f:
        audio = f.read()
    r = httpx.post(
        "https://api.deepgram.com/v1/listen",
        params={
            "model": "nova-3",
            "language": "en",
            "smart_format": "true",
            "punctuate": "true",
            "utterances": "true",
        },
        headers={
            "Authorization": f"Token {DEEPGRAM_KEY}",
            "Content-Type": "audio/wav",
        },
        content=audio,
        timeout=600.0,
    )
    r.raise_for_status()
    data = r.json()
    cache_path.write_text(json.dumps(data))
    return data


def cadence_metrics(words: List[dict], duration_s: float) -> dict:
    """Speaking time, WPM, pause distribution.

    A "pause" is a gap >= 0.25s between consecutive word boundaries. We don't
    count leading/trailing silence — only inter-word gaps within the file.
    """
    if not words:
        return {
            "duration_s": duration_s,
            "speaking_time_s": 0.0,
            "wpm": 0.0,
            "silent_pct": 100.0,
            "pause_count": 0,
            "avg_pause_s": 0.0,
            "median_pause_s": 0.0,
        }
    speaking = sum(w["end"] - w["start"] for w in words)
    pauses = []
    for a, b in zip(words[:-1], words[1:]):
        gap = b["start"] - a["end"]
        if gap >= 0.25:
            pauses.append(gap)
    silent = max(0.0, duration_s - speaking)
    wpm = len(words) / (duration_s / 60.0) if duration_s > 0 else 0.0
    return {
        "duration_s": duration_s,
        "speaking_time_s": speaking,
        "wpm": wpm,
        "silent_pct": 100.0 * silent / duration_s if duration_s > 0 else 0.0,
        "pause_count": len(pauses),
        "avg_pause_s": float(np.mean(pauses)) if pauses else 0.0,
        "median_pause_s": float(np.median(pauses)) if pauses else 0.0,
    }


def pitch_metrics(y: np.ndarray, sr: int) -> dict:
    """Mean/stddev F0 via librosa.pyin. Voiced frames only."""
    # pyin: typical voice range 70–400 Hz covers male & female
    f0, voiced_flag, _ = librosa.pyin(
        y,
        fmin=float(librosa.note_to_hz("C2")),  # ~65 Hz
        fmax=float(librosa.note_to_hz("C6")),  # ~1047 Hz
        sr=sr,
        frame_length=2048,
    )
    voiced = f0[~np.isnan(f0)]
    if voiced.size == 0:
        return {"f0_mean_hz": 0.0, "f0_std_hz": 0.0, "voiced_frames": 0}
    return {
        "f0_mean_hz": float(np.mean(voiced)),
        "f0_std_hz": float(np.std(voiced)),
        "voiced_frames": int(voiced.size),
    }


def energy_metrics(y: np.ndarray, sr: int) -> dict:
    """RMS mean (dBFS) and stddev (dB). Computed on 25ms frames, 10ms hop."""
    frame_length = int(0.025 * sr)
    hop_length = int(0.010 * sr)
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    # Filter out near-silent frames (<-60 dBFS) so they don't drag the mean
    rms = rms[rms > 1e-5]
    if rms.size == 0:
        return {"rms_mean_dbfs": -120.0, "rms_std_db": 0.0}
    db = 20 * np.log10(rms)
    return {
        "rms_mean_dbfs": float(np.mean(db)),
        "rms_std_db": float(np.std(db)),
    }


def speech_rate_consistency(y: np.ndarray, sr: int) -> dict:
    """Approximate syllable rate via onset detection.

    Slice the file into 4-second windows, count onsets per window → rate
    distribution → stddev tells us how steady the cadence is.
    """
    onsets = librosa.onset.onset_detect(y=y, sr=sr, units="time", backtrack=False)
    if onsets.size < 4:
        return {"onsets_per_sec_mean": 0.0, "onsets_per_sec_std": 0.0, "windows": 0}
    duration = librosa.get_duration(y=y, sr=sr)
    if duration < 4.0:
        rate = onsets.size / duration if duration > 0 else 0.0
        return {"onsets_per_sec_mean": float(rate), "onsets_per_sec_std": 0.0, "windows": 1}
    win = 4.0
    rates = []
    t = 0.0
    while t + win <= duration:
        n = int(np.sum((onsets >= t) & (onsets < t + win)))
        rates.append(n / win)
        t += win
    if not rates:
        return {"onsets_per_sec_mean": 0.0, "onsets_per_sec_std": 0.0, "windows": 0}
    return {
        "onsets_per_sec_mean": float(np.mean(rates)),
        "onsets_per_sec_std": float(np.std(rates)),
        "windows": len(rates),
    }


_WORD_RE = re.compile(r"[a-z']+")


def vocabulary_metrics(transcript: str) -> dict:
    """Top-20 content words, filler frequency."""
    text = transcript.lower()
    # Phrase fillers first (count then strip so they don't double-count via tokens)
    phrase_filler_count = 0
    for ph in FILLERS_PHRASES:
        phrase_filler_count += len(re.findall(r"\b" + re.escape(ph) + r"\b", text))
    tokens = _WORD_RE.findall(text)
    total = len(tokens)
    single_filler_count = sum(1 for t in tokens if t in FILLERS_SINGLE)
    filler_total = single_filler_count + phrase_filler_count
    content = [t for t in tokens if t not in STOPWORDS and t not in FILLERS_SINGLE and len(t) > 1]
    top20 = Counter(content).most_common(20)
    return {
        "total_words": total,
        "filler_count": filler_total,
        "filler_pct": (100.0 * filler_total / total) if total else 0.0,
        "top20": top20,
        "content_word_set": set(w for w, _ in Counter(content).most_common(200)),
    }


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def analyze(path: Path) -> dict:
    """Run every metric on a single file."""
    y, sr = librosa.load(str(path), sr=None, mono=True)
    duration = librosa.get_duration(y=y, sr=sr)
    dg = transcribe(path)
    ch = dg["results"]["channels"][0]["alternatives"][0]
    transcript = ch.get("transcript", "")
    words = ch.get("words", [])

    cad = cadence_metrics(words, duration)
    pit = pitch_metrics(y, sr)
    eng = energy_metrics(y, sr)
    rate = speech_rate_consistency(y, sr)
    vocab = vocabulary_metrics(transcript)

    return {
        "path": str(path),
        "sr": sr,
        "transcript": transcript,
        **cad,
        **pit,
        **eng,
        **rate,
        "total_words": vocab["total_words"],
        "filler_count": vocab["filler_count"],
        "filler_pct": vocab["filler_pct"],
        "top20": vocab["top20"],
        "content_word_set": vocab["content_word_set"],
    }


def fmt_dur(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(s, 60)
    return f"{int(m)}m {int(sec):02d}s"


def fmt_delta(ref: float, test: float, unit: str = "", note_fn=None) -> str:
    d = test - ref
    sign = "+" if d >= 0 else ""
    note = note_fn(d) if note_fn else ""
    return f"{sign}{d:.1f}{unit}{(' ' + note) if note else ''}"


def render_table(ref: dict, test: dict, jac: float) -> str:
    rows: List[Tuple[str, str, str, str]] = []

    rows.append(("duration", fmt_dur(ref["duration_s"]), fmt_dur(test["duration_s"]), ""))
    rows.append((
        "words per minute",
        f"{ref['wpm']:.0f}",
        f"{test['wpm']:.0f}",
        fmt_delta(ref["wpm"], test["wpm"]),
    ))
    rows.append((
        "speaking time %",
        f"{100 - ref['silent_pct']:.1f}%",
        f"{100 - test['silent_pct']:.1f}%",
        fmt_delta(100 - ref["silent_pct"], 100 - test["silent_pct"], "%"),
    ))
    rows.append((
        "avg pause (s)",
        f"{ref['avg_pause_s']:.2f}",
        f"{test['avg_pause_s']:.2f}",
        fmt_delta(ref["avg_pause_s"], test["avg_pause_s"], "s"),
    ))
    rows.append((
        "median pause (s)",
        f"{ref['median_pause_s']:.2f}",
        f"{test['median_pause_s']:.2f}",
        fmt_delta(ref["median_pause_s"], test["median_pause_s"], "s"),
    ))
    rows.append((
        "mean F0 (Hz)",
        f"{ref['f0_mean_hz']:.0f}",
        f"{test['f0_mean_hz']:.0f}",
        fmt_delta(ref["f0_mean_hz"], test["f0_mean_hz"], "Hz"),
    ))
    rows.append((
        "F0 stddev (Hz)",
        f"{ref['f0_std_hz']:.0f}",
        f"{test['f0_std_hz']:.0f}",
        fmt_delta(
            ref["f0_std_hz"], test["f0_std_hz"], "Hz",
            note_fn=lambda d: "(less dynamic)" if d < -3 else ("(more dynamic)" if d > 3 else ""),
        ),
    ))
    rows.append((
        "RMS mean (dBFS)",
        f"{ref['rms_mean_dbfs']:.1f}",
        f"{test['rms_mean_dbfs']:.1f}",
        fmt_delta(
            ref["rms_mean_dbfs"], test["rms_mean_dbfs"], "dB",
            note_fn=lambda d: "(louder)" if d > 1 else ("(quieter)" if d < -1 else ""),
        ),
    ))
    rows.append((
        "RMS stddev (dB)",
        f"{ref['rms_std_db']:.1f}",
        f"{test['rms_std_db']:.1f}",
        fmt_delta(
            ref["rms_std_db"], test["rms_std_db"], "dB",
            note_fn=lambda d: "(less variation)" if d < -0.5 else ("(more variation)" if d > 0.5 else ""),
        ),
    ))
    rows.append((
        "filler %",
        f"{ref['filler_pct']:.1f}%",
        f"{test['filler_pct']:.1f}%",
        fmt_delta(
            ref["filler_pct"], test["filler_pct"], "%",
            note_fn=lambda d: "(sounds AI)" if d < -1 else "",
        ),
    ))
    rows.append((
        "onsets/sec mean",
        f"{ref['onsets_per_sec_mean']:.2f}",
        f"{test['onsets_per_sec_mean']:.2f}",
        fmt_delta(ref["onsets_per_sec_mean"], test["onsets_per_sec_mean"]),
    ))
    rows.append((
        "onsets/sec stddev",
        f"{ref['onsets_per_sec_std']:.2f}",
        f"{test['onsets_per_sec_std']:.2f}",
        fmt_delta(ref["onsets_per_sec_std"], test["onsets_per_sec_std"]),
    ))
    rows.append((
        "top word overlap (Jaccard)",
        "—",
        f"{jac:.2f}",
        "(0=none, 1=identical)",
    ))

    # Render
    lab_w = max(len(r[0]) for r in rows)
    out = []
    out.append("=" * 78)
    out.append("=== Jon Comparison ===")
    out.append(f"  Reference: {ref['path']}")
    out.append(f"  Test:      {test['path']}")
    out.append("=" * 78)
    out.append(f"  {'metric'.ljust(lab_w)}   {'Reference'.ljust(20)} {'Test'.ljust(20)} Δ")
    out.append("  " + "-" * (lab_w + 50))
    for label, r, t, d in rows:
        out.append(f"  {label.ljust(lab_w)}   {r.ljust(20)} {t.ljust(20)} {d}")
    out.append("")
    out.append(f"  Top 20 (Reference): {', '.join(w for w, _ in ref['top20'][:20])}")
    out.append(f"  Top 20 (Test):      {', '.join(w for w, _ in test['top20'][:20])}")
    out.append("=" * 78)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Compare two audio files on Jon-likeness metrics.")
    ap.add_argument("--reference", required=True, type=Path, help="Reference Jon audio file")
    ap.add_argument("--test", required=True, type=Path, help="Test (e.g. agent output) audio file")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human table")
    args = ap.parse_args()

    if not args.reference.exists():
        print(f"✗ Reference not found: {args.reference}", file=sys.stderr)
        sys.exit(1)
    if not args.test.exists():
        print(f"✗ Test not found: {args.test}", file=sys.stderr)
        sys.exit(1)

    ref = analyze(args.reference)
    test = analyze(args.test)
    jac = jaccard(ref["content_word_set"], test["content_word_set"])

    if args.json:
        # Strip non-serializable bits
        def clean(d: Dict) -> Dict:
            out = {k: v for k, v in d.items() if k != "content_word_set"}
            out["top20"] = list(out["top20"])
            return out
        print(json.dumps({
            "reference": clean(ref),
            "test": clean(test),
            "jaccard": jac,
        }, indent=2))
    else:
        print(render_table(ref, test, jac))


if __name__ == "__main__":
    main()
