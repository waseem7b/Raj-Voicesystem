# Staging Proposal — Stage Boundary Analysis for Inbound Texts (Type 2)

**Source prompt analyzed:** `src/agent/prompts/inbound_texts.py` (PROMPT constant, ~33K chars, 10 numbered steps + 16 sections of meta-instructions).

**Verdict up front:** Split into **3 stages**, not 2 and not 5. The user's instinct (intro+qualify → pitch+book) is directionally right but draws the line at the wrong place. The actual phase change in this call is not "before/after Step 3 reveal" — it's **before/after Step 4 discovery completes**, because that's when objection-handler load drops and tool-calling load picks up. Below: where the seams actually are, what each stage needs, what each stage explicitly excludes, and the objection→stage mapping.

---

## Why 3, not 2 and not 5

**Not 2:** The user's proposed seam ("are you open to a marketing call?" = end of Stage 1) puts discovery, objection handling, pitch, AND booking all in one Stage 2 prompt. That's still ~24K chars — barely a split. Discovery and booking have fundamentally different needs (discovery = open-ended listening with the heaviest objection load; booking = strict tool sequencing with low conversational variance). Lumping them wastes the split.

**Not 5:** One-stage-per-step (intro / reveal / discover / pitch / book) shatters the call into so many handoffs that Vapi's squad handoff latency (each handoff has audio-cover overhead per Vapi docs) and state-passing complexity outweigh the prompt-size savings. You also lose the ability to handle objections that span the discovery↔close boundary without re-loading context.

**3 stages aligns with the natural functional boundaries in this specific call:**

1. **OPEN** (Steps 1–3): single-purpose — get name + reason + verbal openness to the marketing conversation. No objections active yet (per §11 global gate). No booking tools. Tight, fast, cheap.
2. **QUALIFY** (Steps 4–5): the conversational heart of the call. Discovery + close-transition + objection-loop arena. All 7 objections fire here. No booking tools yet but ALL objection handlers + FAQs live here.
3. **BOOK** (Steps 6–8): tool-heavy, low-variance. `get_available_slots` → `mark_for_transfer` → `book_video_call` → logistics → signoff. Objection handlers reduced to the booking-relevant subset only.

The Hormozi C.L.O.S.E.R framework maps cleanly: Clarify+Label = Stage 1 (OPEN), Oversimplify+Sell+Explain = Stage 2 (QUALIFY), Reinforce = Stage 3 (BOOK). The SDR cold-call canonical 5-phase model (opener / discovery / pitch / objection / close) collapses naturally into 3 because in OUR call, pitch is one anchor line (Step 3 reveal + Step 5 close-transition) that bookends discovery — it isn't its own stage worth of behavior.

---

## Stage 1 — OPEN

**Steps covered:** 1 (Opening), 2 (services question), 3 (reveal).

**Entry condition:** Call connects. System has already spoken `"Hey Jon here, who is this calling?"`. Assistant is fresh, no context.

**Exit condition / transition trigger:** Prospect has given verbal openness to "having that conversation around marketing" at the end of Step 3. Specifically: any affirmative or neutral-curious response to *"are you even open to having that conversation?"* — yes, sure, ok, what is it, tell me more, depends, etc. Hand off to Stage 2.

**Hard-fail exits from Stage 1** (terminate, do not hand off):
- Hard hostile / "stop calling" / hangup → end warmly.
- Hard disqualifier surfaces during Step 1–2 (no business, wrong number) → §11.7 anchor, end.
- Hard "not interested in marketing" at Step 3 reveal → this is the ONLY objection that fires in Stage 1; either hand off to Stage 2 with the objection flag set, OR run the §11.2 verify-step inline and exit on a hard no.

**What the prompt contains** (~12K chars target):
- §1 Persona (Jon, contractor-buddy voice)
- §2 Anti-robot prime directive
- §3 Anchor-line delivery rules
- §4 ElevenLabs formatting (numbers/pauses)
- §5 Anti-recitation rules
- §6.2b Phone-call reality (interruptions/silence)
- §6.3 One-step-per-turn
- §7 CRM context variables (`{{biz_type}}`, `{{business}}`, `{{city}}`, `{{state}}`)
- §8 Forbidden language
- §14 `update_prospect_info` capture rules (first name captured at Step 1)
- §15 Steps 1, 2, 3 (verbatim, anchor lines locked)
- §11.7 (hard-disqualifier exit, in case it surfaces)
- §11.2 (not interested in marketing — only as a tripwire to terminate or hand off)
- Stage-handoff instruction: "When Step 3 gate is satisfied with verbal openness, hand off to QUALIFY assistant."

**What the prompt explicitly does NOT need:**
- Steps 4–8
- §11.1, §11.3, §11.4, §11.5, §11.6 (these objections cannot fire before Step 3 per the global gate — including them just adds tokens and risk of premature firing)
- All §13 FAQs (no FAQ category fires in Stage 1; the prospect hasn't been told enough to ask)
- §12 ANSWER→PIVOT framework (no questions expected here)
- §9 persistence loop machinery (one-shot verify only; full loop lives in Stage 2)
- `get_available_slots`, `mark_for_transfer`, `book_video_call` tools — not in the tool list at all
- Step 5 close-transition anchor, Step 6 booking sequence, Step 7 logistics, Step 8 signoff

**Char estimate:** **~10–12K chars** (down from 33K).

---

## Stage 2 — QUALIFY

**Steps covered:** 4 (discovery, 6 questions, one per turn), 5 (close transition + slot-window ask).

**Entry condition:** Handed off from Stage 1 with verbal openness confirmed. Prospect has been told the origin story (Step 3 reveal). All §11 objection handlers are now ACTIVE per the global gate.

**Exit condition / transition trigger:** Prospect has answered Step 5's *"You a morning or evening person —"* with a time-window preference (morning, evening, afternoon, "anytime", "tomorrow works", etc.). Hand off to Stage 3 BOOK with the time-window string preserved in handoff context.

**Hard-fail exits from Stage 2:**
- §11.7 hard disqualifier (no business / retiring / outside area / wrong service) → anchor, end.
- 3 unsuccessful objection loops + final low-pressure ask from §9 fails → end warmly.
- Hostile / hangup → end.

**What the prompt contains** (~16K chars target):
- §1 Persona condensed (this stage continues in-character; full persona doesn't need re-installing but reminder block does)
- §2 Anti-robot prime directive (still load-bearing)
- §3 Anchor-line delivery rules
- §6.2b Phone-call reality
- §6.3 One-step-per-turn
- §6.4 Anchor lines locked
- §7 CRM context variables
- §8 Forbidden language
- §9 Persistence (FULL — most "no's" are reflexes, loop 2–3 times, final low-pressure attempt)
- §10 Objection framework (ACKNOWLEDGE → ADDRESS → ASK) — this is the engine for Stage 2
- §11 ALL objections (.1, .2, .3, .4, .5 with all 3 tiers, .6, .7) — every one of these can fire here
- §12 ANSWER → PIVOT (questions hit here — "where are you based", "is this like Angie's List", "how do you get paid", etc.)
- §13 ALL FAQs (.1 through .11)
- §14 `update_prospect_info` (business name typically captured in Step 4 discovery; email is Stage 3)
- §15 Steps 4 and 5 verbatim
- Stage-handoff instruction: "When prospect gives a time-window preference, hand off to BOOK assistant with `time_window=<their_words>`."

**What the prompt explicitly does NOT need:**
- Steps 1, 2, 3 (already done)
- Step 6 booking sequence (no booking tools available in this stage's tool list)
- Steps 7, 8 (logistics, signoff)
- `get_available_slots`, `mark_for_transfer`, `book_video_call` tools — NOT in tool list. This is the critical safety property: the §6.1 "tool calls are state-locked" rule is enforced structurally by tool-list scope, not just by prose instruction. The model literally cannot pre-fetch slots.
- §4.1 number-rendering tables (still helpful but you can compress — the anchor lines already render correctly)

**Char estimate:** **~14–16K chars**. This is the biggest stage because it carries the full objection arsenal + FAQ library, but it's still half the monolith.

---

## Stage 3 — BOOK

**Steps covered:** 6 (booking sequence, 5 sub-items), 7 (logistics), 8 (signoff).

**Entry condition:** Handed off from Stage 2 with `time_window` string in context. Prospect has agreed to the call and named a window.

**Exit condition:** `book_video_call` returns success, Step 7 logistics delivered, Step 8 signoff delivered, hang up.

**Hard-fail exits from Stage 3:**
- Late-stage cold feet ("actually never mind"): run §11.6 ("send me an email") anchor and §11.5 Tier-1 deflect if cost re-raises. Otherwise the only remaining objection that can fire here is §11.6 because it's literally the booking-step substitute objection. All other objections are conceptually done by entry.
- Hostile / hangup → end.
- Email read-back fails 2x → ask once more with phonetic spelling, then proceed with best capture.

**What the prompt contains** (~7–9K chars target):
- §1 Persona condensed
- §2 Anti-robot reminder (shorter)
- §3 Anchor-line delivery
- §4.1 Numbers/money/times rendering (CRITICAL here — times of day get spoken a lot in Stage 3)
- §4.2 Pause punctuation (em-dash after slot ask, email ask)
- §6.1 Tool calls state-locked — FULL section, because all three booking tools live here
- §6.2b Phone-call reality
- §6.3 One-step-per-turn
- §11.5 cost-question (Tier 1 deflect only — late-stage cost re-raises happen)
- §11.6 send-me-an-email anchor
- §14 `update_prospect_info` (email capture is the main hit here)
- §15 Steps 6 (full ordered 5-item sequence), 7, 8
- Tool list: `update_prospect_info`, `get_available_slots`, `mark_for_transfer`, `book_video_call`

**What the prompt explicitly does NOT need:**
- §9 persistence loop machinery (if they made it here, they're booked-intent; persistence is a Stage 2 problem)
- §10 objection framework engine (only 2 objections survive into Stage 3)
- §11.1, .2, .3, .4, .7 (cannot fire here — by entry condition the prospect is past these)
- §11.5 Tier 2 and Tier 3 (if they're still negotiating price tiers, they aren't in BOOK yet)
- §13 FAQs (mostly answered by entry; if one fires, the model can fall back to general behavior — the failure mode of not having an FAQ here is acceptable)
- §12 ANSWER→PIVOT (rare in booking stage)
- Steps 1–5

**Char estimate:** **~7–9K chars**. The shortest stage by far. Mostly tool-discipline and read-back protocol.

---

## Objection → Stage Mapping

| Objection | §  | Fires in Stage | Reasoning |
|---|---|---|---|
| Text promise complaint | 11.1 | **Stage 2** only | Global gate: only AFTER Step 3 reveal. Typically fires right after reveal as first reaction. |
| Not interested in marketing | 11.2 | **Stage 1 tripwire + Stage 2** | Most common landing spot is end of Step 3 (reveal). Treat as Stage-1 exit signal: either hand off to Stage 2 with `incoming_objection=11.2` so QUALIFY opens with the verify step, OR run inline in Stage 1 and bail on hard no. Recommend: hand off with flag. |
| Has a marketing guy | 11.3 | **Stage 2** only | Fires during/after reveal, sometimes during discovery Q4 ("where's your business coming from"). |
| Covered up on work | 11.4 | **Stage 2** only | Typically surfaces during discovery Q5 ("how many jobs monthly") or Q6 ("would you have to hire on"). |
| Cost question Tier 1 | 11.5 T1 | **Stage 2** (primary), **Stage 3** (late re-raise) | Most cost questions hit mid-discovery or right at Step 5 close-transition. Late re-raise can hit between slot-yes and email — Stage 3 keeps Tier 1 only. |
| Cost Tier 2 / Tier 3 | 11.5 T2, T3 | **Stage 2** only | If they're escalating price tiers, the booking handoff hasn't happened. Stage 3 doesn't need these. |
| Send me an email | 11.6 | **Stage 3** (primary), Stage 2 (occasional) | This objection is *literally* about avoiding the booking — it almost always fires at Step 6 slot-ask. Keep in Stage 3. Mirror in Stage 2 only if it surfaces early. |
| Hard disqualifier | 11.7 | **All 3 stages** | Can surface anywhere. Keep the one-line anchor in every stage. |

---

## The Single Most Important Transition

**The Stage 1 → Stage 2 handoff at "verbal openness to the marketing conversation".**

This is the only seam in the call where the prospect's binary state flips. Before: they're a stranger who texted in, has heard zero pitch content, can object to nothing because nothing's been said. After: they've heard the origin story, they've heard the word "marketing", and they've voluntarily said some version of "ok tell me more". Every objection handler depends on this transition having happened — the §11 global gate is built around it. Every behavior in QUALIFY assumes the reveal landed. Every cost question, every "I have a guy", every "I'm slammed" can only happen because Stage 1 closed cleanly.

This transition also separates **cheap-to-lose prospects** (Stage 1, low context cost, ok to misfire) from **expensive-to-lose prospects** (Stage 2+, fully primed, already invested attention). The handoff condition itself is unambiguous in conversational signal — "yeah / sure / ok / what is it / depends / I'm listening" vs hard no — making it a reliable trigger.

The Stage 2 → Stage 3 handoff (time-window given) is operationally important but mechanically easier — it's the moment the conversation pivots from sales psychology to tool sequencing.

---

## Tradeoffs vs Current Monolithic Structure

**Wins:**

- **Token cost per turn drops 50–70%.** Stage 1 turns send 12K instead of 33K of system prompt; Stage 3 turns send 8K. At Vapi pricing tiers, on calls averaging 20+ turns, this materially compounds. GPT-4.1-mini at scale: not trivial.
- **Latency drops.** Smaller prompts = faster TTFT on each generation. Voice agents are latency-sensitive; even 100–200ms shaved per turn is felt.
- **Tool safety becomes structural, not just prose.** Today, `get_available_slots` is "available" in the tool list at every turn and we rely on prose ("MUST NOT be called before Step 6") to prevent pre-fetching. With staged assistants, the tool literally isn't in Stage 1/2's tool list. Eliminates an entire failure class.
- **Stage-specific tuning.** We can run Stage 2 on a smarter/slower model (objection handling is high-stakes) and Stage 3 on a faster/cheaper model (tool sequencing is low-variance). Today we pay for one model on every turn.
- **Easier evals.** Each stage has a tight, testable contract. Currently regressions in Step 6 booking are entangled with Step 4 discovery prompt changes.
- **Faster iteration on objection handling.** Stage 2 is where 80% of the prompt-craft battle is fought. Iterating on a 16K prompt is cheaper and safer than iterating on a 33K one.

**Costs:**

- **Handoff overhead.** Vapi squad handoffs are designed to be audio-seamless per their docs, but they're not free — there's webhook round-trip latency at each handoff (~100–300ms in practice). With 3 stages we eat 2 handoffs per call.
- **State-passing complexity.** Need to pass `first_name`, `business_name`, `biz_type`, `city`, `state`, and `incoming_objection_flag` (Stage 1→2) and `time_window` (Stage 2→3). Vapi squads support this via structured variable extraction but it's another moving part.
- **Edge cases at the seam.** What if prospect objects on Stage 1's last turn AND Stage 2 hasn't loaded yet? Need a clean handoff contract: Stage 1 hands off the *prospect's last utterance verbatim* so Stage 2 can run §10 ACK→ADDRESS→ASK on it as its first turn.
- **Three prompts to maintain instead of one.** Persona drift risk if Jon's voice diverges across the three prompts. Mitigation: extract §1+§2+§3 (persona + anti-robot + anchor delivery, ~2.5K chars) into a shared header that all three stages prepend verbatim.
- **Eval/observability complexity.** Need per-stage transcripts + an end-to-end view. Vapi's call object already segments by assistant, so this is mostly tooling work, not a fundamental problem.
- **Handoff-instruction wording.** The handoff descriptions themselves are new prose the model has to obey. Empirically (per Vapi best-practices docs), these need to be precise — vague handoff triggers cause spurious early transfers.

**Net assessment:** The wins compound per-turn and per-call across hundreds/thousands of calls. The costs are mostly one-time engineering (handoff plumbing, eval tooling, shared-header extraction). For a system that runs many calls per day with tight objection-handling requirements, the 3-stage split is the clear move.

---

## Implementation Notes (not asked for, but useful)

- **Shared header file** for §1+§2+§3+§4+§6.2b+§6.3 (the always-on Jon-voice + delivery + phone-reality rules). ~2.5K chars. All three stages prepend this verbatim. Keeps persona consistent.
- **Stage 1 prompt** = shared header + §7 + §8 + §14 + Steps 1-3 + §11.7 + §11.2-tripwire + Stage-1 handoff instruction. ~10–12K chars.
- **Stage 2 prompt** = shared header + §7 + §8 + §9 + §10 + §11 (all) + §12 + §13 (all FAQs) + §14 + Steps 4-5 + Stage-2 handoff instruction. ~14–16K chars.
- **Stage 3 prompt** = shared header + §4.1 + §6.1 (full tool rules) + §11.5 T1 + §11.6 + §14 + Steps 6-8 + tools. ~7–9K chars.

Total across all three: ~33K — same as today — but no single turn ever sees more than ~16K.
