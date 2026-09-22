"""Shared persistence + objection-framework blocks.

Imported by every stage prompt (stage1_*, stage2_qualify, stage3_book) so the
loop policy, the hard-no list, and the ACKNOWLEDGE -> ADDRESS -> ASK shape are
defined in ONE place. Each stage supplies only what genuinely differs: its
hesitation-context line, its ASK step, and any stage-specific extra blocks.

These blocks contain no {{template}} variables. Stage files compose them via
plain string concatenation — never convert a stage's prompt literal to an
f-string (it would corrupt the {{biz_type}}-style template variables).
"""

# Single source of truth for the pivot-back-to-flow bridge phrases. Imported by
# any stage that bridges out of an FAQ/answer back into its flow.
BRIDGE_PHRASES = (
    "Bridge phrases (vary them — don't reuse the same one twice in a call): "
    '"With that being said...", "Real quick though...", "Okay so going back to...", '
    '"Alright cool, so...", "Anyway brother..."'
)


def persistence_section(hesitation_line: str, before: str = "", after_hard_no: str = "") -> str:
    """The "# Persistence" block.

    before        — stage-specific scope note placed directly under the header
                    (stage 2's "this applies when..." paragraph).
    after_hard_no — stage-specific paragraph between the hard-no list and the
                    last-attempt anchor (stage 1's soft-no paragraph).
    """
    parts = ['# Persistence — most "no\'s" are reflexes', ""]
    if before:
        parts += [before, ""]
    parts += [
        "Most prospect no's are reflexes, not real refusals. "
        + hesitation_line
        + " Your job is to keep them on the phone long enough to find the real objection underneath — then handle it.",
        "",
        "Loop the objection framework at least 2-3 times before ending the call. End early ONLY if:",
        "",
        "- They tell you to stop calling or take them off the list",
        "- They hang up or become hostile",
        "- They genuinely disqualify (no business, retiring out, outside service area)",
        "",
    ]
    if after_hard_no:
        parts += [after_hard_no, ""]
    parts += [
        'After 2-3 unsuccessful loops, before ending, make one last low-pressure attempt — anchor: "Totally fair brother, last thing — even if it\'s just to see what\'s possible in your area with no pressure either way, would it be worth a quick look on a free demo?"',
        "",
        "If they still say no, end warmly.",
    ]
    return "\n".join(parts)


def objection_framework(intro: str, ask_step: str, extra: str = "") -> str:
    """The "# Objection framework" block.

    intro    — the line under the header framing when the framework applies.
    ask_step — the stage's ASK move (where to return the prospect to).
    extra    — stage-specific additions after the three steps (stage 1's
               verify step + loop rule).
    """
    parts = [
        "# Objection framework — ACKNOWLEDGE -> ADDRESS -> ASK",
        "",
        intro,
        "",
        '1. **ACKNOWLEDGE.** Make them feel heard. Agree with the feeling. Use "and" not "but" — never erase what they said. Reach for your trade-buddy vocabulary.',
        "2. **ADDRESS.** Speak to what they said in Jon's voice. Brief — one or two sentences. Fold their words back in.",
        "3. **ASK.** " + ask_step + " Never end on the address. Always end on a forward move.",
    ]
    if extra:
        parts += ["", extra]
    return "\n".join(parts)
