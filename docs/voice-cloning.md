# Voice Cloning Pipeline — Jon

Goal: replace the ElevenLabs library voice with a clone of Jon's actual voice so the agent sounds *exactly* like him on every call.

Jon already has 64 recorded sales calls. They turned out to be **mono 8kHz** (not stereo) — Jon and prospect mixed on a single channel — so the pipeline does **speaker diarization via Deepgram nova-3** to separate the voices, then keeps only Jon's segments.

## Current state (extraction complete)

- Source: `recordings/raw/` — 64 files, 575 MB
- 61 successfully diarized; 3 quarantined to `recordings/suspect/` (Deepgram split Jon's voice across multiple speaker labels — pure diarization failure, not heuristic bug)
- **Output:** `recordings/jon-combined.wav` — **295 min (4.9 hr) of Jon-only audio**, 48kHz/24-bit mono PCM
- Pre-trimmed slices ready for upload:
  - `recordings/jon-3min.wav` (25 MB) — for ElevenLabs **IVC**
  - `recordings/jon-30min.wav` (247 MB) — for ElevenLabs **PVC**

Source recordings are 8kHz phone-quality — that's the quality ceiling. Once the cloned voice plays back over the same 8kHz μ-law phone codec, much of the gap closes. If clone quality on phone disappoints, the fallback is for Jon to re-record ~3 min on a real condenser mic in a quiet room.

---

## How extraction worked (already done — for reference)

```bash
# 0. One-time install
brew install ffmpeg
python3 -m venv .venv-extract
.venv-extract/bin/pip install httpx python-dotenv

# 1. Drop call recordings into recordings/raw/

# 2. Run the diarization pipeline
.venv-extract/bin/python scripts/extract-jon.py
# Sends each file to Deepgram nova-3 with diarize=true (8 concurrent),
# identifies Jon by the "this is Jon/John" self-introduction pattern,
# cuts only Jon's word-timed segments via ffmpeg, concatenates everything.
# Result: recordings/jon-combined.wav

# 3. Trim slices for cloning
ffmpeg -ss 60 -t 180  -i recordings/jon-combined.wav -c:a copy recordings/jon-3min.wav
ffmpeg -ss 60 -t 1800 -i recordings/jon-combined.wav -c:a copy recordings/jon-30min.wav
```


---

## What the extraction script does

For each file in `recordings/raw/`:

1. **Channel split.** Stereo → take only the chosen channel (Jon's).
2. **High-pass at 80Hz.** Kills HVAC rumble and low-end mud.
3. **Silence removal.** Drops anything below -38dB for >0.4s — so we keep only Jon talking, no dead air, no faint prospect bleed-through.
4. **Loudness normalization.** EBU R128 to -18 LUFS — every clip lands at the same perceived volume regardless of the original recording level.
5. **Resample to 48kHz / 24-bit mono PCM.** ElevenLabs' preferred upload format.

Then concatenates all per-call WAVs into `recordings/jon-combined.wav`.

---

## Cloning at ElevenLabs

### Option A — Instant Voice Clone (IVC, 1-2 minutes audio, instant)

1. Trim the combined file to ~3 minutes: `ffmpeg -ss 60 -t 180 -i recordings/jon-combined.wav -c:a copy recordings/jon-3min.wav`
2. ElevenLabs dashboard → **Voices → Add Voice → Instant Voice Clone**
3. Upload `recordings/jon-3min.wav`
4. Name it `jon-ivc-v1`, accept consent, save
5. Voice ID is callable via API immediately
6. Update the voice in the Vapi assistant config (use `scripts/sync-vapi-prompt.py` to sync, or edit `voice.voiceId` on the assistant via Vapi dashboard / API).

**Recommended starting settings for clones** (different from premade-voice defaults):
- `stability=0.45` (slightly lower — clones need range)
- `similarity_boost=0.80` (higher — locks identity)
- `style=0.0`
- `speed=1.0`
- `use_speaker_boost=True`

### Option B — Professional Voice Clone (PVC, 30+ min audio, ~24h training)

PVC requires ≥30 minutes of training audio. The combined file will tell you whether you have enough — the script's final summary line says exactly which tier you're in. PVC also requires a **verification recording** (Jon reads a specific verification phrase on the same mic he was recorded on — but since these are existing call recordings, PVC verification may fail. Test IVC first.

If PVC fails verification, the IVC route is the fallback and is generally good enough once audio downsamples to 8kHz μ-law for telephony.

### Option C — Re-record on a real mic

If both IVC and PVC quality from phone recordings are unacceptable:
- Jon records ~3 min on a USB condenser (AT2020, Rode NT1, even Blue Yeti) in a quiet room
- 48kHz/24-bit WAV, 6-8 inches from mouth, conversational sales energy
- Read the locked script lines + 60s of spontaneous shop talk
- Upload that file as IVC instead — quality ceiling is significantly higher

---

## Quality expectations

Phone recordings cap at 8kHz/16kHz mono. IVC trained on phone audio:
- ✓ Captures Jon's cadence, accent, vocabulary
- ✗ Limited dynamic range (training data already compressed)
- ✗ May struggle on emotional registers Jon didn't show in calls
- ✗ Sibilance and "s" sounds can be lossy

Once the phone-trained clone is itself played back through 8kHz μ-law, much of the quality gap closes. Phone-in / phone-out is forgiving.

---

## Mono recording fallback (if needed)

The extraction script assumes stereo with one speaker per channel. If recordings turn out to be mono (Jon mixed with the prospect on one channel), we need speaker diarization:

```bash
pip install pyannote.audio
# Requires HuggingFace token + accept license at:
#   https://huggingface.co/pyannote/speaker-diarization-3.1
```

Then we'd write a separate script that segments by speaker and keeps only Jon's segments. We'll wire that path up only if needed — verify your recordings are stereo first via `discover`.

---

## What's gitignored

`.gitignore` excludes everything in `recordings/` except `raw/.gitkeep`. Audio is large and not source-of-truth — recordings live on your Mac, the cloned voice ID lives in ElevenLabs, neither belongs in the repo.

---

## Status checklist

- [x] Mono diarization pipeline (`scripts/extract-jon.py`)
- [x] Jon's 64 call recordings processed through diarization
- [x] 295 min of Jon-only audio generated (`recordings/jon-combined.wav`)
- [x] IVC slice ready (`recordings/jon-3min.wav`)
- [x] PVC slice ready (`recordings/jon-30min.wav`)
- [ ] **NEXT: upload `recordings/jon-3min.wav` to ElevenLabs as IVC** → get voice ID
- [ ] Update voice ID in the Vapi assistant config (Vapi dashboard or via API)
- [ ] Test call (+1555010011)
- [ ] If quality is acceptable: also try `jon-30min.wav` as PVC for higher fidelity
- [ ] (Optional) Pre-record locked phrases in Jon's actual voice for hybrid playback
