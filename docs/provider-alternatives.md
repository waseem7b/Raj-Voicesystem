# Provider Alternatives — Vapi & ElevenLabs

**Date:** 2026-08-24 · **Status:** research, no decision made
**Driver (owner, 2026-08-24):** Vapi flakiness/latency + wanting better-sounding
voice and more natural communication. Also a general landscape map.

---

## 1. What we'd actually be replacing

These are two separate decisions and they are not equally urgent.

| Vendor | Job it does for us | Replaceable in isolation? |
|---|---|---|
| **Vapi** | Realtime media plane, 3-stage squad handoff, VAD/barge-in/endpointing, voicemail detection, background ambience, `assistant-request` caller-ID hook, tool dispatch | Yes, but it's the expensive migration |
| **ElevenLabs** | TTS only (Turbo v2.5 / `eleven_multilingual_v2`, stock voice `F2BOg7Ry3zTPV6Zd62Cd`) | Yes — a config-level swap, low risk |

Deepgram nova-3 (STT) and GPT-4.1 (LLM) are unaffected by either swap.

### Cost baseline (estimate, not measured)

| Line item | ~$/min |
|---|---|
| Vapi platform fee (Build tier) | 0.050 |
| Deepgram nova-3 streaming STT | 0.005 |
| GPT-4.1 w/ 24h prompt cache | 0.030–0.060 |
| ElevenLabs Turbo v2.5 | 0.020–0.040 |
| Telephony | 0.010–0.015 |
| **Total** | **~0.12–0.17** |

Vapi's own fee is roughly a third of the stack. Provider costs pass through at
cost ($0 if you BYO key), so switching orchestrators does not automatically save
much unless the orchestrator's own fee is the target.

---

## 2. Finding: part of the "communication" problem is our own config

`scripts/setup-vapi.py:212-220` ships:

```python
"startSpeakingPlan": {
    "waitSeconds": 0.2,
    "smartEndpointingEnabled": False,      # <-- ML turn detection is OFF
    "transcriptionEndpointingPlan": {
        "onPunctuationSeconds": 0.1,
        "onNoPunctuationSeconds": 0.4,
        "onNumberSeconds": 0.5,
    },
},
```

We disabled Vapi's ML-based turn detection and are running pure silence-timeout
endpointing. A flat 0.4s no-punctuation timeout is the classic recipe for an
agent that talks over people mid-thought — and we already know it, because
`scripts/push_squad.py:354-360` papers over it with a Stage-3-only
`customEndpointingRules` hack raising the timeout to 3s when Jon asks for an
email, specifically because "Jon barges in mid-email."

That is a symptom fix layered on top of a disabled feature. The 2026 state of the
art is semantic/model-based turn detection that predicts end-of-utterance from
*content*, not silence duration — LiveKit's open-weights Turn Detector v1 reports
a 9.9% false-cutoff rate at a 300ms latency budget. Vapi's smart endpointing is
its version of that.

**Implication:** re-enabling smart endpointing (and deleting the regex hack) is a
zero-migration test of the communication complaint. If it fixes the interruption
feel, the "Vapi is clunky" case weakens considerably. If it doesn't, we have real
evidence for a platform move rather than a hunch.

---

## 3. Vapi's reliability record

Per Vapi's own status page, 2026 incidents run roughly 1–2 per month:

- **Jun:** dashboard errors (~12h), call failures (~8h), elevated API errors
  (2h, twice), weekly-channel errors (~5h), call-log latency (~26h)
- **Jul:** daily-channel degradation, board/API issues (~1 day combined),
  API degradation, dashboard problems, US network performance (~15m)
- **Aug:** daily-channel degradation, docs outage, webhook issues for web calls,
  **Aug 19 call-failure spike (24h+)**

Most resolve in hours; the Aug 19 call-failure spike is the outlier and the one
that maps to our pain. Third-party trackers count 336 incidents since Aug 2024.

On latency: Vapi publishes p50 <500ms / p95 <800ms. An independent March 2026
study of 500 production calls per platform measured an *optimized* Vapi config at
**720ms median / 1050ms p95**, and found Retell lowest of the four tested.
Context: under ~300ms feels human, over 600ms degrades the call, over 1.5s and
callers hang up.

Structural note: Vapi owns no carrier network — it rides Twilio/Telnyx, so its
PSTN-side latency ceiling is somebody else's.

---

## 4. Orchestration alternatives

| Platform | Cost | Squad equivalent | Fit for us |
|---|---|---|---|
| **ElevenLabs Agents** | $0.08/min speech engine, BYO LLM | `transfer_to_agent` + workflow nodes | Collapses both decisions — drop Vapi, move *up* the ElevenLabs stack. Native Twilio + SIP, server tools, dynamic variables (maps to our `assistant-request`). Single-vendor lock. Voicemail detection + background ambience need verification. |
| **Retell AI** | $0.07–0.31/min ($0.055 infra + $0.015 telephony + LLM + TTS) | Multi-prompt agents | Closest 1:1 swap; lowest measured latency in the one independent benchmark found. More surrounding software included. Caveat: most "Vapi alternatives" content online is Retell's own SEO. |
| **LiveKit Agents** | $0.01/min agent + $0.01/min US telephony + providers at cost | Native multi-agent handoff | 5x cheaper fee than Vapi, best-in-class turn detection, native SIP, cloud or self-host. We write the Python pipeline — but our business logic already lives in our own FastAPI server, so the gap is smaller for us than for most. |
| **Pipecat / Pipecat Cloud** | $0.01–0.03/min agent, unlimited concurrency, PSTN $0.018/min | Pipecat Flows | Open-source, zero lock-in, cheapest hosting. Most DIY. |
| **Deepgram Voice Agent** | $0.056/min (→ $0.075 after 9/12) | Weak | Cheap, single vendor, consolidates with existing Deepgram STT. Gives up voice quality and stage flexibility. |
| **Telnyx** | Custom | Flow-based | Owns the carrier network — the one thing Vapi doesn't. Only compelling if PSTN-side latency is diagnosed as the cause. |
| **OpenAI Realtime** | ~$0.06–0.11/min w/ caching | N/A | Speech-to-speech, no cascade — eliminates cascade latency entirely, natively handles tone and interruption. **But no voice cloning**, so no Jon voice. Disqualified for a brand-voice sales agent. |

**What a migration actually costs us:** rebuilding `assistant-request` caller-ID
routing across 3 CRMs, squad handoff, tool auth, voicemail detection, background
ambience, the idle-message hook ladder, and months of endpointing tuning. That is
the real price tag, not the per-minute delta.

---

## 5. TTS alternatives

Requirement is narrow: clonable, streaming, low TTFB, must survive an 8kHz μ-law
phone codec.

| Option | Price | Latency (TTFA) | Cloning |
|---|---|---|---|
| **Rime** (Mist v3) | $0.03/1k chars | 37ms p50 / 56ms p90 | Custom clones; unlimited on enterprise |
| **Cartesia** (Sonic-3) | ~$0.039/1k | ~40ms | Instant from 3s; professional (225 credits) |
| **Inworld** (TTS-2 Flash) | $15/1M on-demand, $8–10 paid tiers | sub-200ms | Instant + professional add-on |
| **Fish Audio** (S2) | $15/1M | streaming | 15s reference; open weights; #1 TTS-Arena |
| ElevenLabs Flash/Turbo (today) | $0.05/1k | ~75ms | PVC (~60 min audio) |
| MiniMax Speech 2.8 HD | ~$100/1M | — | Yes — quality near EL v3, no cost win |
| Deepgram Aura-2 | $0.030/1k | — | **No real cloning — disqualified** |

**Rime first.** Purpose-built for phone, ~40% cheaper than Turbo, lowest measured
TTFA, and its voices are recorded from ordinary people rather than voice actors —
which is exactly the register `shared_persona.py` is written in ("a buddy in the
trade"). **Fish Audio** is the cheapest top-tier quality and Vapi supports it
natively, so it's a near-free bake-off entry.

Skip open-weight self-hosted options (Chatterbox, Qwen3-TTS, Kokoro) — the
architecture doc's "API-first, no GPU" constraint rules them out regardless of
blind-test scores.

**Caveat on all quality rankings:** these are studio-audio leaderboards. Our
channel is narrowband telephony, which compresses most of that difference away.
A blind A/B over real PSTN is the only test that counts.

---

## 6. The voice complaint is mostly not a vendor problem

We are on a **stock** voice because the Jon PVC (`LJ3VmFT7KIfEw9jSLImM`) was
retired after prospect-voice contamination. `docs/voice-cloning.md` says the
re-clone from clean diarized audio is unblocked but never attempted:

- `recordings/jon-combined.wav` — 295 min (4.9 hr) of Jon-only audio
- `recordings/jon-3min.wav` — ready for IVC
- `recordings/jon-30min.wav` — ready for PVC

Three known risks already documented there:

1. Source recordings are **8kHz phone quality** — that is the ceiling.
2. PVC requires a verification recording on the same mic; with existing call
   recordings, **PVC verification may fail**. Test IVC first.
3. Documented fallback: Jon re-records ~3 min on a condenser mic in a quiet room.

Since Rime, Cartesia, Inworld and Fish all clone too, the re-clone and the
provider bake-off are the same experiment — do them together, not sequentially.

---

## 7. Recommended sequence

Cheapest-and-most-informative first:

1. **Re-enable `smartEndpointingEnabled`** and delete the Stage-3 email regex
   hack. Zero migration cost. Directly targets the "communication" complaint.
2. **Have Jon record ~3 clean minutes on a real mic.** Removes the 8kHz ceiling
   and the PVC-verification risk in one move.
3. **Bake off 4 clones on that same audio** — ElevenLabs IVC/PVC, Rime, Cartesia,
   Fish — and blind-A/B them over real PSTN, not in the browser.
4. **Only then** evaluate an orchestration move, with real before/after latency
   numbers instead of a hunch. If steps 1-3 fix the feel, Vapi's remaining problem
   is uptime alone, and the question becomes "is ~1-2 incidents/month tolerable"
   rather than "which platform sounds better."

If the answer to step 4 is a move: **ElevenLabs Agents** if we want fewer vendors
and a managed plane; **LiveKit** if we want the media plane and the turn detector
under our own control.

---

## Sources

Vapi pricing + status page; Retell pricing; Cartesia pricing; Rime pricing;
LiveKit Cloud pricing + turn-detection blog; Pipecat Cloud pricing; Deepgram
pricing; Inworld pricing; ElevenLabs API pricing + Agents docs; independent
latency benchmarks (Tested Media 500-call study, Openbenchmarks); Hamming AI
interruption-handling analysis.
