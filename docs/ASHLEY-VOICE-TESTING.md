# Ashley voice + pause testing log

Append-only record of what has been tried on the outbound Ashley assistants
(confirmation `236bfb42`, reminder `1b4e0bb0`), so settled questions stay
settled. **Read the SETTLED section before proposing a pause or voice change.**

Evidence standard: a claim about what the model or TTS did needs the raw call
log, not the transcript. See "How to get real evidence" at the bottom — several
conclusions in this file reversed once the right artifact was read.

---

## SETTLED — do not retry without new information

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | SSML `<break time="Xs" />` for pauses | **Does not work. Removed.** | 46+ calls, both TTS models, 1 and 3 carve-outs, with and without competing rules. Model emits them correctly and Vapi delivers them to ElevenLabs intact; the audio comes back with the preceding word echoed or truncated. Calls `01a0361f`, `01a04ee1`. |
| 2 | Is the plain-English prohibition suppressing the break tags? | **No.** | Removed all three "no symbols / no markdown / no tags" rules, left the break instruction uncontested. Call `01a04ee1`: 12 tags in prompt, 0 artifacts avoided, same echo. |
| 3 | `...` and `—` beats inside anchors | **Removed — the chunker cuts through them.** | Call `01a04fec` chunk log: `".  He's got your market report"`, `"—. Umm I'll send you"`. Vapi keeps `"Jon."` and hands the leftover punctuation to the next chunk as an orphan fragment. No placement survives this. |
| 4 | Disable ElevenLabs text normalization to preserve stretched spellings | **Not possible via Vapi.** | `PATCH /assistant` → `400 voice.property applyTextNormalization should not exist`. Also tried `apply_text_normalization`, `textNormalization`. |
| 5 | `optimizeStreamingLatency` as the cause of dropped words | **Ruled out.** | Set to 0 (best quality, no optimisation). Call `01a03697` still lost "office"/"so" and truncated "Ashley"→"Ash". Now held at 1. |
| 6 | Persona restructure (improvise-vs-copy conflict) as the cause of punctuation flattening | **Not the cause.** | Restructure shipped and verified live; call `01a04a2c` still flattened `Glad to hear!` → `Glad to hear.` Kept anyway — the prompt is more coherent — but it did not fix the symptom. |
| 7 | Vowel repetition to elongate a word | **Undocumented; works only on medial vowels.** | ElevenLabs document capitals, dashes, apostrophes, phoneme tags and pronunciation dictionaries — never repeated letters. `thank yoooooou` lands; `Byeeeee` splits audibly, because "bye" already ends in "e" so there is no word boundary. Ruled out as a Vapi truncation: final chunk reached ElevenLabs 3.2s before `call.ended`. |

| 8 | Pause marker as a dash run | **Partly working. Placement and character both matter.** | `----` silent/swallowed (01a055a2). `————` jammed against the word gets PRONOUNCED — "with John J. P.", "know, Io.", "tomorrow, like," (01a055a9). ` ————` with a leading space is silent through long chunks but still voiced as "V" on a SHORT chunk (01a055bd), so the marker is now only on anchors longer than chunkPlan's 80-char minimum. Marker must sit before the terminal punctuation or the chunker orphans it. |

---

## IN PLACE — kept, with the reason

| Setting | Value | Why |
|---|---|---|
| `model` (TTS) | `eleven_turbo_v2_5` | Reverted from multilingual_v2 on 2026-08-30 at the owner's call. Multilingual was preferred on one listen and is the more emotionally rich model, but it normalises harder — it is what killed the stretched spellings. Turbo is what the voice was tuned against all week. Flip is one line: `ASHLEY_TTS_MODEL`. |
| `chunkPlan` | `minCharacters: 80`, boundaries `. ! ?` | Default boundary set includes the **comma**, which cut the 38-word joining line into three generations. **23 chunks → 10** on the next call, joining line intact as one 165-char chunk. |
| `speed` | 0.93 | Pacing lever, since break tags are ruled out. 0.92 read as flat; 0.97 was the long-standing value. |
| `style` | 0.65 | Emotion. Raised 0.45 → 0.60 → 0.65. |
| `stability` | 0.45 | Counterweight to style. ElevenLabs default 0.50. |
| `similarityBoost` | 1.00 | 0.75 was A/B'd and judged "alright" — not an improvement worth the drift. |
| `useSpeakerBoost` | false | Inherited from Jon's clone. **Never tested.** ElevenLabs default it ON. |
| `backgroundSound` | `office` | Was inheriting Jon's car-engine loop; he takes calls from his truck. |
| `smartEndpointingPlan` | `livekit` | The deprecated boolean `smartEndpointingEnabled` does nothing. |
| `endCallPhrases` | `Byeee, Byeeeee, Bye for now, Bye` | Wide on purpose: a missed match does not degrade, the call just never hangs up. |
| Greeting anchors | `{{firstName}}` / `{{greeting}}` | Vapi renders `{{...}}` into the prompt; our own `{Name}` it does not, so the model was *assembling* the greeting each call and re-punctuating it. |

---

## OPEN — real, unfixed

1. **Word loss at chunk boundaries.** Reduced by `chunkPlan`, not eliminated. Call `01a04fec`: `"...It's Ashley."` / `"How've you been?"` sent as two chunks, audio returned "It's Ash How have you been?". No tag, no beat — the boundary alone did it.
2. ~~**Empty `bookedTime` makes her invent a time.**~~ **Cause found and fixed 2026-08-31:** the lookup read `CONFIRM_CALENDAR_ID` (a test calendar) while bookings went to `VIDEO_CALL_CALENDAR_ID`, so no call ever found a booking. Every prompt gate written against this symptom failed — invented times ("two in the afternoon", "seven in the evening", "your time") and the broken "for the call at later today". The GO-LIVE flip is taken; both now point at the same calendar. **Requires a webhook-server deploy to take effect.**
3. **Whole-turn repeats.** Call `01a04fec`: greeting delivered twice, then the entire Step 3 block twice. Prompt-side, not chunking.
4. **Reminder script length.** The 38-word joining line is the biggest chunk generator left. Owner has flagged trimming; wording is his to cut.

---

## TIMELINE OF MODEL SWAPS

| Date | TTS model | Why |
|---|---|---|
| through 2026-08-28 | `eleven_turbo_v2_5` | Inherited from Jon's clone, never chosen. Vowel-repetition stretches worked on it. |
| 2026-08-29 | `eleven_multilingual_v2` | Swapped for emotional richness, and on a (mistaken) belief that it would fix break tags. Owner preferred the sound. Stretches stopped working. |
| 2026-08-30 | `eleven_turbo_v2_5` | Reverted at owner's request. |

---

## NEXT UNTESTED LEVERS

- `useSpeakerBoost: true` — inherited-false, never tested, one field.
- Put the `...` beats back **with** `chunkPlan` in place. That combination has never run: the beats failed when commas were split points and the chunker cut through them. Sentence-only boundaries may leave them intact.
- Trim the reminder's joining line (needs owner wording).

---

## How to get real evidence

`messagesOpenAIFormatted` and `artifact.transcript` are **sanitised** — Vapi
strips SSML from them. Two conclusions in this file were wrong until the raw
log was read. The authoritative source is the gzipped call log:

```python
art = httpx.get(f"https://api.vapi.ai/call/{cid}", headers=H).json()["artifact"]
txt = gzip.decompress(httpx.get(art["presignedLogUrl"]).content).decode("utf-8")
# body == "Model output"                  -> raw LLM token stream
# body == "ElevenLabs text message sent"  -> the exact chunk sent to TTS
```

The artifact is empty while a call is `in-progress`; wait for `status: ended`.
