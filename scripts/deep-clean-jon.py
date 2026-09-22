#!/usr/bin/env python3
"""Deep-clean Jon-only audio from raw call recordings for ElevenLabs PVC training.

Pipeline per file:
  1. Load 16kHz mono
  2. WhisperX large-v3 + wav2vec2 alignment → word-level timestamps
  3. pyannote-3.1 diarization → speaker label per turn
  4. Identify Jon's pyannote speaker (max cosine vs reference embedding)
  5. pyannote-3.0 segmentation OSD → mark overlap regions
  6. Drop overlap regions (±150ms padding)
  7. Stoplist filter → drop short non-content interjections (yeah/mhm/etc.)
  8. Resemblyzer per-word similarity gate → drop chunks below cos threshold
  9. Build retain regions: word_start −80ms, word_end +150ms
  10. Merge contiguous Jon regions if gap < 250ms
  11. ffmpeg cut+concat with 10ms crossfades → cleaned per-call WAV

Usage:
  # 1. Build a Jon reference embedding (one-time, from a known-clean clip):
  python scripts/deep-clean-jon.py build-ref --input recordings/jon-3min.mp3 \\
      --output recordings/jon-reference.npy

  # 2. Test on ONE file:
  python scripts/deep-clean-jon.py clean --input "recordings/raw/AARON MYSTERY.wav" \\
      --output "recordings/jon-deep-clean/AARON MYSTERY.wav" \\
      --jon-ref recordings/jon-reference.npy

  # 3. Batch (parallel):
  python scripts/deep-clean-jon.py batch --input-dir recordings/raw \\
      --output-dir recordings/jon-deep-clean --jon-ref recordings/jon-reference.npy \\
      --workers 4

  # 4. After batch, merge + final loudnorm:
  python scripts/deep-clean-jon.py merge --input-dir recordings/jon-deep-clean \\
      --output recordings/jon-deep-clean.flac
"""
import argparse
import multiprocessing as mp
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# pyannote 3.x checkpoints need weights_only=False under torch 2.6+ (default tightened).
# Force-override any caller's weights_only kwarg — lightning_fabric explicitly passes True.
import torch as _torch
_torch_orig_load = _torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs["weights_only"] = False
    return _torch_orig_load(*args, **kwargs)
_torch.load = _patched_torch_load

# Stoplist of short non-content interjections — drop if labeled as Jon but matches
STOPLIST = {
    "yeah", "yep", "yup", "yah", "ya",
    "mhm", "mm-hmm", "uh-huh", "uhuh", "hmm",
    "ok", "okay", "kay",
    "right", "alright",
    "sure", "for sure",
    "gotcha", "got it",
    "uh", "um", "er", "ah",
    "oh", "wow",
    "hello", "hey", "hi",
}
STOPLIST_MAX_DURATION_S = 0.6  # only drop if also < 600ms

# Word-boundary padding (tight to prevent prospect bleed)
HEAD_PAD_S = 0.030  # 30ms before each retained word
TAIL_PAD_S = 0.030  # 30ms after each retained word (no bleed into next speaker)
MERGE_GAP_S = 0.200  # merge contiguous Jon regions if gap < 200ms

# Speaker similarity threshold (Resemblyzer)
SPEAKER_SIM_THRESHOLD = 0.85  # cosine vs Jon reference centroid (was 0.78 — too loose)

# Hard-skip the first/last seconds of every call (call opener + goodbye are unreliable).
EDGE_SKIP_S = 5.0  # drop everything in the first 5s and last 5s of every call

# Overlap padding (drop overlap regions ±150ms)
OVERLAP_PAD_S = 0.150


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_hf_token() -> str:
    """Read HF_TOKEN from config/.env."""
    env = repo_root() / "config" / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("HF_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("HF_TOKEN not in config/.env")


def normalize_word(text: str) -> str:
    """Lowercase + strip punctuation for stoplist comparison."""
    return re.sub(r"[^\w]", "", text.lower())


def load_audio_16k_mono(path: Path):
    """Load arbitrary input → 16kHz mono float32 numpy array."""
    import soundfile as sf
    import librosa
    import numpy as np

    if path.suffix.lower() in (".mp3", ".m4a", ".flac"):
        # ffmpeg → temp wav for non-WAV
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.check_call(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
             "-ar", "16000", "-ac", "1", tmp_path],
        )
        audio, sr = sf.read(tmp_path, dtype="float32")
        os.unlink(tmp_path)
    else:
        audio, sr = sf.read(str(path), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)  # downmix to mono
        if sr != 16000:
            audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
            sr = 16000
    return audio, sr


def cmd_build_ref(args):
    """Build a Jon reference embedding from a clean Jon clip and save to .npy."""
    import numpy as np
    from resemblyzer import VoiceEncoder, preprocess_wav

    print(f"loading {args.input}")
    audio, _ = load_audio_16k_mono(Path(args.input))
    # Resemblyzer expects pre-processed wav at 16kHz
    wav = preprocess_wav(audio, source_sr=16000)
    print(f"  duration: {len(wav)/16000:.1f}s")
    encoder = VoiceEncoder(device="cpu")
    print("computing embedding...")
    emb = encoder.embed_utterance(wav)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, emb)
    print(f"saved reference embedding ({emb.shape}) → {out}")


def cmd_clean(args):
    """Clean ONE raw call file → Jon-only WAV."""
    import numpy as np
    import torch
    import whisperx
    from pyannote.audio import Pipeline
    from resemblyzer import VoiceEncoder, preprocess_wav

    inp = Path(args.input)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    jon_ref = np.load(args.jon_ref)
    hf_token = get_hf_token()

    print(f"[{inp.name}] loading audio")
    audio, sr = load_audio_16k_mono(inp)
    duration = len(audio) / sr
    print(f"  duration: {duration:.1f}s ({duration/60:.1f} min)")

    # ───────── 1. WhisperX transcribe + word-level alignment ─────────
    print(f"[{inp.name}] WhisperX transcribe (CPU int8, silero VAD)")
    # vad_method="silero" sidesteps whisperx's pyannote VAD which has API mismatch with pyannote 3.x
    asr_model = whisperx.load_model("large-v3", device="cpu", compute_type="int8", vad_method="silero")
    result = asr_model.transcribe(audio, batch_size=8, language="en")
    del asr_model

    print(f"[{inp.name}] WhisperX align (word-level timestamps)")
    align_model, meta = whisperx.load_align_model(language_code="en", device="cpu")
    aligned = whisperx.align(
        result["segments"], align_model, meta, audio, device="cpu",
        return_char_alignments=False,
    )
    del align_model
    word_segments = aligned.get("word_segments", [])
    print(f"  words aligned: {len(word_segments)}")

    # ───────── 2. pyannote diarization (CPU; MPS has timestamp bugs) ─────────
    print(f"[{inp.name}] pyannote diarization")
    diar_pipe = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", use_auth_token=hf_token,
    )
    diar_pipe.to(torch.device("cpu"))
    diar_input = {"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": sr}
    diarization = diar_pipe(diar_input, num_speakers=2)

    speaker_segments = {}  # label → list of (start, end)
    for turn, _, label in diarization.itertracks(yield_label=True):
        speaker_segments.setdefault(label, []).append((turn.start, turn.end))
    print(f"  speakers detected: {list(speaker_segments.keys())}")

    # ───────── 3. Identify Jon by max cosine vs reference embedding ─────────
    encoder = VoiceEncoder(device="cpu")

    def speaker_centroid(segs):
        embs = []
        for s, e in segs:
            if e - s < 1.0:  # skip very short segments
                continue
            chunk = audio[int(s * sr):int(e * sr)]
            try:
                wav = preprocess_wav(chunk, source_sr=sr)
                if len(wav) >= 16000:  # need ≥1s after preprocessing
                    embs.append(encoder.embed_utterance(wav))
            except Exception:
                continue
        if not embs:
            return None
        return np.mean(embs, axis=0)

    def cos(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    label_scores = {}
    for label, segs in speaker_segments.items():
        c = speaker_centroid(segs)
        label_scores[label] = cos(c, jon_ref) if c is not None else -1
    jon_label = max(label_scores, key=label_scores.get)
    print(f"  jon similarity scores: {label_scores}")
    print(f"  → jon = {jon_label}")

    # ───────── 4. Per-word: keep only Jon-labeled words ─────────
    def speaker_at(t):
        """Return diarization label at time t, or None."""
        for label, segs in speaker_segments.items():
            for s, e in segs:
                if s <= t <= e:
                    return label
        return None

    # Compute Jon-only intervals: Jon turns MINUS any prospect turn (rigorous overlap drop).
    # If both speakers talk simultaneously, the audio is contaminated — exclude that range entirely.
    jon_intervals = sorted([(s, e) for s, e in speaker_segments[jon_label]])
    prospect_intervals = sorted([
        (s, e) for label, segs in speaker_segments.items() if label != jon_label for s, e in segs
    ])

    def subtract_intervals(a, b):
        """Return intervals in `a` with all of `b` subtracted (set-difference of intervals)."""
        result = []
        for s, e in a:
            cur = [(s, e)]
            for bs, be in b:
                new_cur = []
                for cs, ce in cur:
                    if be <= cs or bs >= ce:
                        new_cur.append((cs, ce))
                    else:
                        if bs > cs:
                            new_cur.append((cs, bs))
                        if be < ce:
                            new_cur.append((be, ce))
                cur = new_cur
                if not cur:
                    break
            result.extend(cur)
        return result

    jon_only_intervals = subtract_intervals(jon_intervals, prospect_intervals)
    jon_only_total = sum(e - s for s, e in jon_only_intervals)
    jon_total = sum(e - s for s, e in jon_intervals)
    print(f"  jon turns total: {jon_total:.1f}s  jon-ONLY (no overlap): {jon_only_total:.1f}s  (dropped {jon_total - jon_only_total:.1f}s of overlap)")

    def in_jon_only(w_start, w_end):
        """True iff [w_start, w_end] is entirely inside any jon-only interval."""
        for s, e in jon_only_intervals:
            if s <= w_start and w_end <= e:
                return True
        return False

    # Hard-skip first/last EDGE_SKIP_S seconds (pyannote struggles at call boundaries)
    safe_start = EDGE_SKIP_S
    safe_end = duration - EDGE_SKIP_S

    # Build a per-call Jon reference from THIS call's longest pure-Jon-only interval.
    # More accurate than relying on a potentially-contaminated global reference.
    longest_jon_only = max(jon_only_intervals, key=lambda iv: iv[1] - iv[0]) if jon_only_intervals else None
    per_call_ref = None
    if longest_jon_only and (longest_jon_only[1] - longest_jon_only[0]) >= 5.0:
        s, e = longest_jon_only
        chunk = audio[int(s * sr):int(e * sr)]
        try:
            wav = preprocess_wav(chunk, source_sr=sr)
            per_call_ref = encoder.embed_utterance(wav)
            sim_to_global = cos(per_call_ref, jon_ref)
            print(f"  per-call jon ref built from {e-s:.1f}s clip (cos vs global: {sim_to_global:.3f})")
        except Exception as e:
            print(f"  per-call ref build failed: {e}")

    jon_words = []
    overlap_drops = 0
    edge_drops = 0
    for w in word_segments:
        if "start" not in w or "end" not in w:
            continue
        if w["start"] < safe_start or w["end"] > safe_end:
            edge_drops += 1
            continue
        if in_jon_only(w["start"], w["end"]):
            jon_words.append(w)
        else:
            overlap_drops += 1
    print(f"  jon-only words: {len(jon_words)} (dropped {overlap_drops} in overlap, {edge_drops} in first/last {EDGE_SKIP_S}s)")

    # ───────── 5. Stoplist filter (drop short non-content interjections) ─────────
    filtered = []
    for w in jon_words:
        word_text = normalize_word(w.get("word", ""))
        word_dur = w["end"] - w["start"]
        if word_text in STOPLIST and word_dur < STOPLIST_MAX_DURATION_S:
            continue
        filtered.append(w)
    print(f"  after stoplist: {len(filtered)} (dropped {len(jon_words) - len(filtered)} interjections)")

    # ───────── 6. Per-word Resemblyzer similarity gate ─────────
    # Use per-call ref if we built one (more reliable than potentially-contaminated global).
    active_ref = per_call_ref if per_call_ref is not None else jon_ref
    ref_label = "per-call" if per_call_ref is not None else "global"
    survivors = []
    sim_too_low = 0
    too_short = 0
    for w in filtered:
        # ±200ms window around the word
        s = max(0, w["start"] - 0.2)
        e = min(duration, w["end"] + 0.2)
        chunk = audio[int(s * sr):int(e * sr)]
        if len(chunk) < 16000:  # need ≥1s for embedding
            too_short += 1
            survivors.append(w)
            continue
        try:
            wav = preprocess_wav(chunk, source_sr=sr)
            emb = encoder.embed_utterance(wav)
            sim = cos(emb, active_ref)
            if sim >= SPEAKER_SIM_THRESHOLD:
                w["sim"] = sim
                survivors.append(w)
            else:
                sim_too_low += 1
        except Exception:
            pass
    print(f"  after similarity gate ({ref_label} ref ≥{SPEAKER_SIM_THRESHOLD}): {len(survivors)} (dropped {sim_too_low} low-similarity, {too_short} too-short kept)")

    # ───────── 7. Build retain regions with padding, merge contiguous ─────────
    if not survivors:
        print(f"[{inp.name}] NO SURVIVING WORDS — skipping output")
        return

    regions = []
    for w in survivors:
        regions.append([
            max(0, w["start"] - HEAD_PAD_S),
            min(duration, w["end"] + TAIL_PAD_S),
        ])
    regions.sort()

    # Merge contiguous (gap < MERGE_GAP_S)
    merged = [regions[0]]
    for s, e in regions[1:]:
        if s - merged[-1][1] < MERGE_GAP_S:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    total_kept = sum(e - s for s, e in merged)
    print(f"  merged regions: {len(merged)}  total kept: {total_kept:.1f}s ({total_kept*100/duration:.1f}%)")

    # ───────── 8. Cut and concat using numpy/soundfile (avoids ffmpeg argv length limit) ─────────
    # Older approach used ffmpeg -af aselect='between(t,s,e)+...' which OS-killed at >128KB filter expr.
    # Audio is already loaded in `audio` (16kHz mono float32). Slice + concat in numpy.
    print(f"[{inp.name}] cutting with numpy/soundfile")
    import numpy as np
    import soundfile as sf
    chunks = []
    for s, e in merged:
        i0 = int(s * sr)
        i1 = int(e * sr)
        if 0 <= i0 < i1 <= len(audio):
            chunks.append(audio[i0:i1])
    if not chunks:
        print(f"[{inp.name}] no audio chunks — skipping output")
        return
    cleaned = np.concatenate(chunks)
    sf.write(str(out), cleaned, sr, subtype="PCM_16")
    print(f"[{inp.name}] ✓ wrote {out} ({out.stat().st_size/1_000_000:.1f}MB, {len(cleaned)/sr:.1f}s)")


def cmd_batch(args):
    """Batch-process a directory of raw files in parallel."""
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(in_dir.glob("*.wav"))
    if not files:
        sys.exit(f"no .wav files in {in_dir}")
    if getattr(args, "reverse", False):
        files = files[::-1]

    # Skip files already processed (re-check before each file in the loop too,
    # so peer machines syncing files in mid-run get picked up).
    todo = [f for f in files if not (out_dir / f.name).exists()]
    print(f"total: {len(files)}  already done: {len(files)-len(todo)}  todo: {len(todo)}  order: {'reverse' if getattr(args,'reverse',False) else 'forward'}")
    if not todo:
        return

    def make_args(f):
        return argparse.Namespace(
            input=str(f),
            output=str(out_dir / f.name),
            jon_ref=args.jon_ref,
        )

    # Sequential or parallel
    if args.workers <= 1:
        for f in todo:
            try:
                cmd_clean(make_args(f))
            except Exception as e:
                print(f"  FAIL {f.name}: {e}")
    else:
        # multiprocessing: each worker gets its own model load (memory ~3-4GB each)
        with mp.Pool(args.workers) as pool:
            results = []
            for f in todo:
                results.append(pool.apply_async(_worker_clean, (make_args(f),)))
            for r in results:
                try:
                    r.get()
                except Exception as e:
                    print(f"  worker error: {e}")


def _worker_clean(ns):
    """multiprocessing entry — each worker imports its own copies of heavy deps."""
    import warnings
    warnings.filterwarnings("ignore")
    cmd_clean(ns)


def cmd_merge(args):
    """Concat all cleaned WAVs + final 2-pass loudnorm → single FLAC."""
    in_dir = Path(args.input_dir)
    out = Path(args.output)
    files = sorted(in_dir.glob("*.wav"))
    if not files:
        sys.exit(f"no .wav files in {in_dir}")
    print(f"merging {len(files)} files")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        listfile = Path(f.name)
        for p in files:
            esc = str(p).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")

    # Pass 1: concat + measured loudnorm (ebur128 stats)
    print("running ffmpeg concat + 2-pass loudnorm...")
    subprocess.check_call([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-af", "loudnorm=I=-20:TP=-3:LRA=7:print_format=summary",
        "-ar", "44100", "-ac", "1",
        "-c:a", "flac", "-compression_level", "8",
        str(out),
    ])
    listfile.unlink()
    size = out.stat().st_size
    probe = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(out),
    ]).decode().strip()
    minutes = float(probe) / 60
    print(f"\n=== OUTPUT ===")
    print(f"  {out}")
    print(f"  size: {size/1_000_000:.1f}MB")
    print(f"  duration: {minutes:.1f} min")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("build-ref", help="build Jon reference embedding from clean clip")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_build_ref)

    p = sub.add_parser("clean", help="clean one raw file → Jon-only WAV")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--jon-ref", required=True)
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("batch", help="batch-clean a directory in parallel")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--jon-ref", required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--reverse", action="store_true", help="iterate files Z->A so two machines meet in the middle")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("merge", help="concat all cleaned WAVs + loudnorm → final FLAC")
    p.add_argument("--input-dir", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=cmd_merge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
