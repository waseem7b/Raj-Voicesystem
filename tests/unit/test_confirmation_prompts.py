"""Guards the prompt-registry wiring for the outbound Ashley squad.

Rebuilt 2026-08-19 when the flow moved from Jon to Ashley and gained a second
call type; split three ways 2026-09-04 when rescheduling became its own agent.
Three flows share one persona:

  demo_confirmation / stage1  — day-before: confirm, tag locked-in, or hand off
  demo_reminder     / stage1  — day-of: logistics, tag locked-in, or hand off
  demo_rebooking    / stage1  — call-back: find + lock in the new time

"Hand off" is a tag (`needs-reschedule`) and a sign-off — never an in-call
transfer. GHL then fires the rebooking assistant as a separate call. Only that
assistant carries the calendar tools; there is still no stage2 anywhere.

confirm_persona.py (Jon) stays on disk and stays tested until the Ashley flow is
verified on real calls — it is the rollback target.
"""
import pathlib

import pytest

from src.agent.prompts import get_stage_prompt, PROMPT_BY_STAGE
from src.agent.prompts.ashley_persona import ASHLEY_PERSONA
from src.agent.prompts.confirm_persona import CONFIRM_PERSONA

# The two that hand off, and the one they hand off to.
HANDOFF_FLOWS = [("demo_confirmation", "stage1"), ("demo_reminder", "stage1")]
REBOOK_FLOW = [("demo_rebooking", "stage1")]
ASHLEY_FLOWS = HANDOFF_FLOWS + REBOOK_FLOW

# The greeting moved out of the prompt on 2026-09-01: it is now Vapi's
# firstMessage, rendered server-side and handed to ElevenLabs whole, with no
# model and no chunker in the path. That is what stopped it losing words.
# The guarantees the greeting tests were written for still matter — they just
# apply to that string now, so they read it from the push script.
_FLOW_KEY_BY_CALL_TYPE = {
    "demo_confirmation": "confirmation",
    "demo_reminder": "reminder",
    "demo_rebooking": "rebooking",
}


def first_message(call_type: str) -> str:
    """The firstMessage Vapi speaks for this flow, straight from the deploy script."""
    import re
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    key = _FLOW_KEY_BY_CALL_TYPE[call_type]
    block = src.split(f'"{key}": {{', 1)[1]
    m = re.search('"first_message": "(.*?)",', block, re.S)
    assert m, f"no first_message on flow {key}"
    return m.group(1).replace(chr(92) + chr(34), chr(34))



#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# personas
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
def test_ashley_persona_loads_and_is_ashley_not_jon():
    assert isinstance(ASHLEY_PERSONA, str) and ASHLEY_PERSONA.strip()
    low = ASHLEY_PERSONA.lower()
    assert "you are ashley from cloud 7" in low
    # Jon is a colleague she refers to, never the speaker.
    assert "you are jon" not in low
    # Jon's identity details must not have followed her over.
    assert "flooring company" not in low
    assert "bluetooth speaker" not in low
    assert "vancouver epoxy" not in low
    # Service mode, and no unpopulated inbound template vars.
    assert "courtesy call, not a pitch" in low
    assert "{{biz_type}}" not in ASHLEY_PERSONA


def test_jon_confirm_persona_kept_intact_as_rollback_target():
    assert isinstance(CONFIRM_PERSONA, str) and CONFIRM_PERSONA.strip()
    assert "you are jon" in CONFIRM_PERSONA.lower()


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# registry
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_flow_registered_and_composed(call_type, stage):
    assert (call_type, stage) in PROMPT_BY_STAGE
    prompt = get_stage_prompt(call_type, stage)
    assert isinstance(prompt, str) and prompt.strip()
    assert "You are Ashley from Cloud 7" in prompt


def test_rebooking_is_its_own_call_type_not_a_stage2():
    """Rescheduling is a separate assistant reached by a separate call, never
    a stage2 behind an in-call handoff (the path the 2026-08-19 design removed
    and the 2026-09-04 split kept removed)."""
    assert ("demo_rebooking", "stage1") in PROMPT_BY_STAGE
    for call_type, _ in ASHLEY_FLOWS:
        assert (call_type, "stage2") not in PROMPT_BY_STAGE


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# day-before confirmation — owner's script anchors, verbatim
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
def test_confirmation_carries_the_script_anchors():
    p = get_stage_prompt("demo_confirmation", "stage1")
    # The opener no longer says "I've been texting you" (owner, 2026-09-24), so
    # the text thread is now a reactive answer rather than part of the greeting.
    assert "you have been texting them" in p
    assert "still good for tomorrow at {time} with Jon" in p
    assert "Are we still on?" in p
    assert "The team is putting the breakdown of your market together" in p
    assert "just text me okay?" in p
    # Booking facts are handed over as variables before the call — there is no
    # in-call lookup to skip (two live calls proved the model skips it).
    assert "{{bookedTime}}" in p and "{{greeting}}" in p and "{{firstName}}" in p
    assert "get_prospect_appointment" not in p
    # Confirm branch tags the contact.
    assert "mark_locked_in" in p


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# day-of reminder — informational, no confirm ask, no tag
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
def test_reminder_carries_the_script_anchors():
    p = get_stage_prompt("demo_reminder", "stage1")
    # The reminder itself moved into the opener (owner, 2026-09-24), so the
    # prompt must NOT carry it too — that is the same sentence twice per call.
    assert "remind you about your call with John {{reminderWhen}}" in first_message("demo_reminder")
    assert "quick reminder for the call" not in p
    assert "market report all finished and ready to go" in p
    assert "meeting link an hour before the call" in p
    assert "It's going to be on Google Meet" in p
    assert "Alrighty we'll see you later! BYE" in p


def test_reminder_informs_but_does_not_re_ask():
    """The day-of call must not re-open the question — the prospect already
    confirmed, and asking again invites a reversal. It DOES tag on
    acknowledgement though (owner 2026-08-21), which is a different thing:
    confirming what they said, not asking them to decide again."""
    p = get_stage_prompt("demo_reminder", "stage1")
    assert "Are we still on?" not in p
    assert "Ask nothing about whether they're still coming" in p
    assert "mark_locked_in" in p


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# the rebooking split (2026-09-04)
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
def test_rebooking_carries_the_owners_script():
    """Rebuilt 2026-09-08 from the owner's own rebooking script. Before that she
    improvised the sentence that says why she is calling — the first thing the
    prospect hears and judges."""
    p = get_stage_prompt("demo_rebooking", "stage1")
    # Owner, 2026-09-15: the reaction varies call to call, "along the lines of" his line.
    assert 'along the lines of "Love to hear it."' in p
    assert '**Anchor:** "Love to hear it."' not in p
    # The why-calling line arrives written out (calls 01a0a257, 01a0a26a).
    assert "**Anchor:** the whyCalling line in your call context." in p
    assert "Great, I will lock you in for {day} at {time}." in p
    assert "We did a bunch of research into your market and on your business." in p
    # The owner's updated script (2026-09-14) brings the reminders line back and
    # signs off on "Bye", with no Google Meet question.
    assert "I will send you a couple of text reminders before the call." in p
    assert "Ok enjoy the day and we'll talk soon. Bye" in p
    assert "Are you able to join on video" not in p
    # His objection answers, in her voice.
    assert "I'm a little confused though because when we talked the other day" in p
    assert "reposition local businesses like yours in front of people" in p
    assert "nine forty-seven a month to four thousand on average" in p
    assert "I don't have the answer on me right now" in p
    # And never his other document's voice.
    for masculine in ("brother,", "brother.", " man.", " man,", "dude"):
        assert masculine not in p, masculine
    # Call 01a0a678: the day said twice again. The first offer is now written by the
    # webhook ({{firstOffer}}); the day-once shape is the only primary anchor.
    assert "**Anchor:** the firstOffer line in your call context." in p
    # Owner, 2026-09-15: only ever two times per offer (call 01a0a746 read out six for Friday).
    assert "Never offer more than two times at once" in p
    # Owner, 2026-09-15 (call 01a0a796): "one in the afternoon, two in the afternoon, or three in the afternoon".
    assert 'say it once, after the second: "seven or eleven in the morning"' in p
    # Calls 01a0a796 / 01a0a7e0: her own offers ignored the rule; each day's line is now pre-written.
    assert "under dayOffers in your call context" in p
    # Call 01a0ab63: the last two offers she composed herself are pre-written too.
    assert "say the matching line from moreTimes in your call context" in p
    assert "never anything you say" in p
    assert "if they ask about a particular day, say that day's line exactly as written" in p
    assert "When those lines are blank (no open times were given to you)" in p
    # Calls 01a0a678 / 01a0a7ef: booked on "Okay. Sounds good." and on a garbled turn.
    assert "Nothing books until they have named a time." in p
    assert 'say "Sorry, say that again?" and wait' in p
    assert "A yes to a one-time offer is a pick of that time." in p
    assert '**Anchor:** "How does {day} at {time} or {time} work for you?"' in p
    assert '**Anchor:** "How does {day} at {time} or {day} at {time} work for you?"' not in p
    # Owner, 2026-09-14: not back to back — one morning time and one afternoon time.
    assert "the first morning opening (before noon) and the first afternoon opening (noon or later)" in p
    # Call 01a0a234: 8.4s of silence while booking; the phone system now says the hold line.
    assert '"Hold on a sec."' in p
    assert "get_available_slots" in p and "reschedule_video_call" in p
    assert "mark_not_interested" in p
    # A rebooked appointment re-enters the confirmation workflow, which is
    # where locked-in gets written — so this assistant never tags it.
    assert "mark_locked_in" not in p
    assert "mark_needs_reschedule" not in p


@pytest.mark.parametrize("call_type,stage", HANDOFF_FLOWS)
def test_handoff_flows_tag_and_sign_off_with_no_calendar(call_type, stage):
    """The prompt bloat that made her lose the thread was the calendar branch
    sitting inside two 24k prompts. It is gone from both; what remains is the
    tag and the sign-off."""
    p = get_stage_prompt(call_type, stage)
    assert "mark_needs_reschedule" in p
    assert "Days and times are never offered on this call" in p
    assert "get_available_slots" not in p
    assert "reschedule_video_call" not in p
    assert "Would you be able to do {day} at {time}?" not in p
    assert "rescheduling section" not in p, "a branch still points at the removed section"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_in_call_handoff_anywhere(call_type, stage):
    """The split is a separate CALL, never an in-call transfer — the failure
    mode the 2026-08-19 design removed stays removed."""
    p = get_stage_prompt(call_type, stage)
    assert "handoff" not in p.lower() and "hand off" not in p.lower()


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_ashley_flows_say_nothing_about_zoom(call_type, stage):
    """Google Meet in this flow only. The inbound Jon squad keeps its Zoom
    wording, which is asserted by not touching it here."""
    assert "zoom" not in get_stage_prompt(call_type, stage).lower()


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# Regressions from live calls 01a01c9f and 01a01cf9 (2026-08-19)
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_goodbye_is_not_the_models_job(call_type, stage):
    """Four consecutive live calls (01a01c9f, 01a01cf9, 01a01d8d, 01a0202f)
    ended with end_call firing and no spoken sign-off, through three different
    wordings of the gate. The line moved to the assistant's endCallMessage,
    where Vapi guarantees it — so the prompt must NOT also carry one, or the two
    would talk over each other."""
    p = get_stage_prompt(call_type, stage)
    # She speaks the named part; endCallPhrases closes the call on "BYE".
    # so a skipped sign-off still leaves a goodbye behind.
    assert "BYE" in p, "sign-off must be in the script she speaks"


def test_endcall_message_carries_the_signoff():
    """Guards the platform half of the contract above."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert '"endCallPhrases": ["BYE", "Byeee", "Bye for now", "Bye"]' in src
    assert '"temperature": 0.2' in src, "script fidelity needs low temperature"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_in_call_lookup_to_skip(call_type, stage):
    """The booking is resolved before dialing, so there is no first-action tool
    the model can decline to fire."""
    p = get_stage_prompt(call_type, stage)
    assert "get_prospect_appointment" not in p
    assert "nothing to look up" in p


@pytest.mark.parametrize("call_type,stage", HANDOFF_FLOWS)
def test_time_and_greeting_are_variable_sourced(call_type, stage):
    """She invented "two in the afternoon" from a formatting sample, and said
    "good afternoon" at 10pm off the UTC clock."""
    p = get_stage_prompt(call_type, stage)
    assert "The only clock time you may ever say out loud is `{{bookedTime}}`" in p
    # The greeting words are no longer the model's to choose — Vapi renders the
    # opener. What must still hold is that anything per-prospect in it arrives
    # as a variable rather than being assembled.
    assert "{{firstName}}" in first_message(call_type)


def test_rebooking_times_come_only_from_the_calendar_result():
    """The rebooking call has no single booked time to anchor on — every time
    it may speak is one the calendar returned."""
    p = get_stage_prompt("demo_rebooking", "stage1")
    assert "The only days and times you may say out loud are ones in that list." in p
    # Open times are preloaded before dialling (owner, 2026-09-14).
    assert "under availableSlots in your call context" in p
    # And the prospect's own clock (owner, 2026-09-15; call 01a0a622 read today's slot as tomorrow's).
    assert "Today is what currentDay in your call context says." in p
    assert 'A day marked "no openings" has nothing to offer' in p
    assert "{{firstName}}" in first_message("demo_rebooking")


def test_punctuation_and_stutter_rules_present():
    p = get_stage_prompt("demo_confirmation", "stage1")
    assert "A question mark stays a question mark" in p     # "Are we still on" lost its "?"
    assert "Never say the same word twice in a row" in p    # "tomorrow. Tomorrow."


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# SSML break markers: removed entirely 2026-08-24 (owner call).
#
# eleven_turbo_v2_5 does parse them and enableSsmlParsing was on, but every
# marker splits the line into two ElevenLabs generations and the seam corrupts
# a word. Mid-line placement lost or doubled one ("It's Ashley. Ashley, how
# have you been", "from your phone don't have to download", call 01a0361f);
# moving them after the final punctuation just moved the damage to the last
# word of every sentence. Pauses now come from punctuation only, which stays
# inside a single generation.
#
# Reinstating them means re-solving the seam first — see docs/log.md.
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_break_markers_anywhere(call_type, stage):
    """Was: assert no <break> anywhere — written when SSML had been ruled out.
    Reversed 2026-09-01: <break time="0.5s" /> IS the pause mechanism in the
    anchors now, and it is the only one that survived testing (500ms silently
    ignored, 0.7s echoed 4 of 5, 2.5s echoed 2 of 5). What must NOT come back is
    a <speak> wrapper spanning two anchors — that spans two possible turns, so
    an improvised turn emits half a document and a stray </speak>."""
    p = get_stage_prompt(call_type, stage)
    assert "<break" in p, "the anchors lost their pause marks"
    assert "<speak>" not in p, "a <speak> wrapper is back in the script"
    assert "</speak>" not in p, "a stray closing tag is back in the script"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_surviving_instruction_to_emit_a_tag(call_type, stage):
    """The carve-outs are what taught her to emit markup at all. With the markers
    gone, an orphaned exception would leave her emitting a tag nothing renders."""
    p = get_stage_prompt(call_type, stage)
    for orphan in ("ONE exception", "The one thing you DO write out",
                   "The single exception", "character for character"):
        assert orphan not in p, f"orphaned tag carve-out survives: {orphan!r}"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_plain_spoken_english_is_stated_without_a_carve_out(call_type, stage):
    p = get_stage_prompt(call_type, stage)
    assert "No brackets, asterisks, markdown, emoji, tags, or stage directions" in p
    assert "Every pause you want comes from punctuation, never from a marker." in p


@pytest.mark.parametrize("call_type,stage", REBOOK_FLOW)
def test_calendar_pull_is_silent_without_a_marker(call_type, stage):
    """The 3s hold used to cover the lookup. The instruction has to carry it alone.
    Since the preload (2026-09-14) the in-call pull is only the fallback for an empty list."""
    p = get_stage_prompt(call_type, stage)
    assert "silently call `get_available_slots` with days_ahead=7 and say nothing until the result is back" in p

#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# Punctuation pauses (2026-08-24). With SSML gone, `...` and `—` are the only
# pause mechanism left, and unlike a tag they render inside a single generation
# so they cannot produce a seam. They live in the anchor text itself because a
# persona rule saying "you may add beats" never reliably produced one.
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_short_beats_survive_in_the_anchors(call_type, stage):
    assert "..." in get_stage_prompt(call_type, stage)


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_beats_are_locked_not_optional(call_type, stage):
    """She strips anything she reads as decoration unless it is named as locked."""
    p = get_stage_prompt(call_type, stage)
    assert "An `...` stays an `...` and a `—` stays a `—`" in p
    assert "dropping one runs two thoughts together" in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_goodbyes_stay_clean_so_the_end_call_phrase_matches(call_type, stage):
    """The sign-off is the endCallPhrase — a beat spliced into it would stop
    matching and the call would never hang up."""
    p = get_stage_prompt(call_type, stage)
    assert "... BYE" not in p
    assert "— BYE" not in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_greeting_runs_straight_through(call_type, stage):
    """Call 01a03697 opened "Hey. Ryan. Good evening. It's Ash" — every mark
    flattened to a full stop and the contraction expanded. Four short units back
    to back is already the choppiest line in the call, so it takes no added
    beats, and the marks it does have are named as load-bearing."""
    greeting = first_message(call_type)
    # confirm carries exactly one owner-requested beat; see the test below
    assert greeting.count("...") <= 1, f"extra beats in the open: {greeting.strip()}"
    assert "—" not in greeting
    # Vapi renders this string whole and these flows set chunkPlan
    # punctuationBoundaries to ";" only, so the opener reaches ElevenLabs as ONE
    # generation however many sentences it has. A ";" would reintroduce the
    # chunk seam that ate "It's Ashley." -> "It's Ash" (call 01a04fec).
    assert ";" not in greeting, f"chunk seam in the open: {greeting.strip()}"


def test_reminder_greeting_keeps_its_contraction():
    """Contractions are what keep the line from reading like a form letter."""
    assert "I'm just calling" in first_message("demo_reminder")


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_greeting_arrives_pre_filled(call_type, stage):
    """Vapi renders {{...}} into the system prompt before the model sees it; our
    own {Name} / {morning/afternoon} notation it leaves alone. On call 01a0369f
    the model therefore received a line with two blanks and ASSEMBLED it, which
    is where "Hey Ryan, good evening!" became "Hey. Ryan. Evening." — "good"
    dropped, both marks flattened. A pre-filled line has nothing to assemble."""
    greeting = first_message(call_type)
    assert "{{firstName}}" in greeting, greeting.strip()
    for slot in ("{Name}", "{morning/afternoon}", "[NAME]"):
        assert slot not in greeting, f"unrendered slot {slot}: {greeting.strip()}"
    # And the prompt must tell her it is already done, so she never says it again.
    assert "greeting is already done" in get_stage_prompt(call_type, stage)


def test_no_single_brace_name_slots_survive_anywhere():
    """[NAME] and {Name} are ours, not Vapi's — anything still carrying one gets
    rebuilt by the model instead of copied."""
    for call_type, stage in ASHLEY_FLOWS:
        for line in get_stage_prompt(call_type, stage).splitlines():
            if "**Anchor:**" not in line:
                continue
            for slot in ("[NAME]", "[Name]", "{Name}", "{morning/afternoon}"):
                assert slot not in line, f"{call_type}: {slot} in {line.strip()}"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_copy_never_rebuild_is_stated_at_the_top(call_type, stage):
    """Agentic Reminders are the one block re-read every turn."""
    head = get_stage_prompt(call_type, stage).split("# Who you are")[0]
    assert "copied, never rebuilt" in head


@pytest.mark.parametrize("call_type,anchor", [
    ("demo_confirmation", "Glad to hear!"),
    ("demo_reminder", "Love that!"),
])
def test_the_reaction_leads_the_turn(call_type, anchor):
    """Call 01a0369f came back "I'm good. Thank you. Love that. Just wanted to
    give you..." — she answered their "how about you?" before reacting to what
    they had just told her, which reads as waiting for a turn rather than
    listening. The reaction is now the first thing out of her mouth."""
    p = get_stage_prompt(call_type, "stage1")
    assert "react to that first" in p
    assert "**Gate:** When it does apply, nothing goes ahead of it." in p
    assert f'''"I'm good, thank you. {anchor}" is the wrong way round''' in p
    # and the ordering is spelled out, not left to inference
    assert "the reaction, then your answer, then the next step" in p
    # The openers ask for twenty seconds rather than how they are, so the
    # reaction is conditional now: "Glad to hear!" after a bare "sure" answers
    # a question nobody asked (owner, 2026-09-24).
    assert f'**Anchor (only when they tell you how they are):** "{anchor}"' in p
    assert "only when there is something to react to" in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_stretched_thank_you_survives(call_type, stage):
    """Vowel repetition is not in ElevenLabs' docs but it is the only lever for
    elongation on turbo v2 — phoneme tags change pronunciation, not duration, and
    would reintroduce the generation seam that killed the SSML breaks. GPT-4.1
    tidies unusual spellings back to correct ones unless told not to."""
    p = get_stage_prompt(call_type, stage)
    assert "THANK YOU" in p, "the capitals got normalised away"
    assert "A word in CAPITALS is deliberate" in p


def test_confirm_and_rebook_goodbyes_are_both_stretched():
    conf = get_stage_prompt("demo_confirmation", "stage1")
    assert '"Ok THANK YOU! Have a good day {{firstName}}! BYE"' in conf
    for ct in ("demo_confirmation", "demo_reminder", "demo_rebooking"):
        assert '"THANK YOU! Have a good day! BYE"' in get_stage_prompt(ct, "stage1")


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_stretching_did_not_break_the_end_call_phrase(call_type, stage):
    """The sign-off is what hangs up the call — it has to match the matcher."""
    p = get_stage_prompt(call_type, stage)
    for line in p.splitlines():
        if "**Anchor:**" in line and "BYE" in line:
            assert line.rstrip().endswith('BYE"'), f"end phrase altered: {line.strip()}"

@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_branch_still_ends_on_the_old_phrase(call_type, stage):
    """Every ending is the hang-up trigger, so a branch left on the retired
    wording does not just sound wrong — it never ends the call, and the line
    stays open to the 60s silence timeout."""
    p = get_stage_prompt(call_type, stage)
    assert "Bye for now" not in p, "a branch still carries the retired sign-off"
    endings = [l for l in p.splitlines() if "**Anchor:**" in l and "BYE" in l]
    assert len(endings) >= 3, f"only {len(endings)} sign-off anchors found"


def test_matcher_covers_the_range_the_model_might_shorten_to():
    """GPT-4.1 shortens stretched vowels. Every landing point from the full
    stretch down to a bare "Bye" has to hang up, or the call stays open."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    for phrase in ("BYE", "BYE", "Bye"):
        assert '"' + phrase + '"' in src, "matcher missing " + phrase


def test_ashley_is_in_an_office_not_a_truck():
    """The base config is cloned from Jon's monolithic assistant, which plays a
    hosted car-engine loop because he takes calls from his truck. Ashley calls
    from a desk, so the clone must override it rather than inherit."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert '"backgroundSound": "office"' in src
    # Call 01a0a234: the booking is spoken over by Vapi itself, every other tool stays silent.
    assert 'BOOKING_HOLD_MESSAGES = [{"type": "request-start", "content": "Hold on a sec."}]' in src
    assert src.split("def reschedule_video_call_tool", 1)[1].split("def ", 1)[0].count('"messages": BOOKING_HOLD_MESSAGES') == 1
    assert src.count('"messages": BOOKING_HOLD_MESSAGES') == 1
    assert '"backgroundSound": mono.get("backgroundSound")' not in src

@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_comma_is_followed_by_a_beat(call_type, stage):
    """A comma immediately followed by an ellipsis is doubled pause punctuation.
    Call 01a0493e collapsed every one of them into a full stop — "Google Meet.
    Okay?", "before the call. And you can join" — the opposite of the beat it was
    meant to be. The comma is already the small pause; ellipses now mark sentence
    boundaries only, and the em-dash marks section ends."""
    for line in get_stage_prompt(call_type, stage).splitlines():
        if "**Anchor:**" in line and "It's Ashley from Cloud 7" not in line:
            assert ", ..." not in line, "comma-beat survives: " + line.strip()


GREETING_BEAT = "Cloud 7, ... I've been texting you"


def test_reminder_greeting_still_takes_none():
    greeting = first_message("demo_reminder")
    assert "..." not in greeting, greeting.strip()

#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# Rebook call 01a04a15 (2026-08-28). The branch worked end to end — slots
# pulled, real slot offered, reschedule_video_call booked it — with two defects
# in the raw model output.
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", REBOOK_FLOW)
def test_the_slot_offer_follows_a_silent_lookup_turn(call_type, stage):
    """She emitted "Oh, no problem. Let me get you rescheduled then." twice in a
    single turn — once with the calendar lookup, again when the slots came back.
    Nothing told her the second turn resumes rather than restarts."""
    p = get_stage_prompt(call_type, stage)
    # The lookup turn carries no text at all: GPT-4.1 returns EITHER content OR a
    # tool call, so "say the line while the tool runs" describes a turn that cannot
    # exist — the line is owed, and it came out twice when it finally landed.
    # Preload (2026-09-14): the offer follows their reply, and the fallback pull stays silent.
    assert "say nothing until the result is back" in p
    assert "It is the first thing out of your mouth once they have replied open to finding a new time." in p
    assert "in the same turn as the calendar lookup" not in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_clock_time_can_be_lifted_out_of_the_instructions(call_type, stage):
    """With bookedTime EMPTY she told the prospect "two in the afternoon" — the
    exact string the voice-formatting table used as its sample. Three separate
    gates warned her not to treat samples as bookings, one of them quoting the
    sample as it warned. Not supplying a liftable time beats warning about it."""
    p = get_stage_prompt(call_type, stage)
    for sample in ("two in the afternoon", "ten in the morning", "nine thirty"):
        assert sample not in p.lower(), f"liftable clock time in prompt: {sample!r}"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_time_formatting_is_described_not_exemplified(call_type, stage):
    """The table still has to teach the transformation, just without a value she
    can speak straight out of it."""
    p = get_stage_prompt(call_type, stage)
    assert 'the hour as a word, then "in the morning"' in p

#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# Persona restructure (2026-08-28). Revert point: tag
# ashley-pre-persona-restructure.
#
# The prompt had been arguing with itself. Four separate lines licensed the
# model to rewrite an anchor — "Even when you are aiming at an anchor line,
# deliver it like you are thinking it up on the spot", "Reaches for different
# phrasing each time", "Insert beats ... for cadence", and a mental model that
# said "not karaoke-reading the lyrics off a screen" — against an anchor
# section saying the words and punctuation were locked. The model resolved the
# conflict by improvising every time: exclamation marks became periods,
# "How've" became "How have", "THANK YOU" became "Thank you", every beat was
# dropped. Each new gate added weight to the losing side of that argument.
#
# Everything she says is now partitioned up front, and the natural-speech rules
# are scoped to the half she authors.
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_speech_is_partitioned_before_either_rule_argues(call_type, stage):
    p = get_stage_prompt(call_type, stage)
    assert "# Two kinds of line" in p
    assert "They are read, not composed." in p
    assert "**Everything else is yours**" in p
    assert "none of it licenses editing an anchor" in p
    # the partition has to land BEFORE the anti-robot rules it scopes
    assert p.index("# Two kinds of line") < p.index("prime directive")


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_surviving_licence_to_rewrite_an_anchor(call_type, stage):
    p = get_stage_prompt(call_type, stage)
    for licence in (
        "Even when you are aiming at an anchor line",   # said it outright
        "karaoke",                                      # "not karaoke-reading the lyrics"
        "Insert beats with",                            # SHOULD add punctuation
        "You may add more for cadence",                 # to an anchor
    ):
        assert licence not in p, f"still licenses rewriting an anchor: {licence!r}"


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_spontaneity_is_scoped_to_her_own_words(call_type, stage):
    p = get_stage_prompt(call_type, stage)
    assert "for your own words" in p
    assert "anti-robot" in p
    assert "never the words an anchor chose for you" in p
    assert "among their own words" in p
    assert "not in swapping the words out" in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_adding_punctuation_to_an_anchor_is_now_an_edit(call_type, stage):
    """The old text told her she SHOULD insert beats and contractions. That is
    indistinguishable from permission to re-punctuate."""
    p = get_stage_prompt(call_type, stage)
    assert "What is yours is how it sounds, not what it says." in p
    assert "or add punctuation of your own" in p


@pytest.mark.parametrize("call_type,stage", REBOOK_FLOW)
def test_claims_about_availability_are_gated_too(call_type, stage):
    """Call 01a04a2c: she summarised across days — "on Tuesday and Wednesday the
    latest we have is also five in the afternoon" — carrying Tuesday's 5pm onto
    Wednesday, whose last slot is 4pm. The prospect took it, reschedule fired for
    2026-09-02T17:00, and only the server-side offered-slots guard stopped it.
    The old gate covered offering a time, not characterising the calendar."""
    p = get_stage_prompt(call_type, stage)
    assert "This covers what you say ABOUT the calendar too" in p
    assert "Read each day's list on its own." in p
    assert "Never carry one day's times, or one day's latest slot, across to another day." in p


def test_tts_model_is_pinned_not_inherited():
    """The Ashley config is built by cloning Jon's assistant, and the TTS model
    came along with it unchosen — same route the car-engine background sound took.
    Multilingual v2 is ElevenLabs' most emotionally rich model, which is the axis
    this flow is tuned along, and unlike Turbo it is on their supported list for
    break tags."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert "ASHLEY_TTS_MODEL = " in src
    assert '"model": ASHLEY_TTS_MODEL,' in src


def test_ashley_voice_inherits_nothing_from_jon():
    """Jon's voice block used to be spread into Ashley's config, so any field not
    explicitly overridden silently became his. That is how she ended up on his TTS
    model, his similarityBoost of 1.00, and a car-engine background loop. Every
    voice field is now declared."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    voice = src.split('"voice": {', 1)[1].split("},", 1)[0]
    assert '**mono["voice"]' not in voice, "Jon's voice block is being inherited again"
    for field in ("provider", "voiceId", "model", "speed", "stability", "style",
                  "similarityBoost", "useSpeakerBoost", "optimizeStreamingLatency",
                  "enableSsmlParsing"):
        assert f'"{field}"' in voice, f"voice field not declared: {field}"

#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# Chunk boundaries (2026-08-30). Revert: tag ashley-pre-chunkplan.
#
# Vapi splits the model's text and sends each chunk to ElevenLabs as its own
# generation. Boundaries are where words die. Call 01a04fec sent
#   "Hey Ryan, good evening! It's Ashley."  /  "How've you been?"
# as two chunks and the audio came back "It's Ash How have you been?".
#
# The default punctuationBoundaries include the comma, which cut the 38-word
# joining line into three generations. And the chunker splits INSIDE a "..." —
# it kept "Jon." and handed the leftover ".  " to the next chunk, so the beats
# were arriving at ElevenLabs as orphaned fragments (".  He's got", "-. Umm").
# That is why the beats are gone rather than retuned: no placement survives a
# splitter that cuts through them. Pacing is speed 0.92 instead.
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_no_beat_punctuation_survives_in_anchors(call_type, stage):
    for line in get_stage_prompt(call_type, stage).splitlines():
        if "**Anchor:**" in line:
            assert " ... " not in line, f"ellipsis beat fragments at chunking: {line.strip()}"
            assert ' \u2014"' not in line, f"em-dash beat fragments at chunking: {line.strip()}"


def test_chunk_plan_pins_boundaries_and_a_minimum():
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert '"chunkPlan"' in src
    assert '"minCharacters": 80' in src
    assert '"punctuationBoundaries": [".", "!", "?"]' in src
    assert '","' not in src.split('"punctuationBoundaries"')[1].split("]")[0], \
        "comma must not be a split boundary — it cut the joining line into three"


# HANDOFF_FLOWS only: every rebooking anchor is a single sentence, so there is
# no sentence end inside one to mark.
@pytest.mark.parametrize("call_type,stage", HANDOFF_FLOWS)
def test_sentence_dashes_are_present_and_named(call_type, stage):
    """Owner asked for a pause mark at every sentence end (2026-08-30). GPT-4.1
    tidies unusual punctuation away the same way it tidied unusual spelling and
    casing, so the marker has to be named as deliberate in the persona.

    The literal mark has moved with testing — em dashes -> ` -- --` (2026-08-31,
    the form ElevenLabs documents). This asserts the INTENT (a mark exists in the
    anchors and the persona names it), not one spelling of it, so the next tuning
    round does not turn a deliberate change into a red gate."""
    p = get_stage_prompt(call_type, stage)
    anchors = [l for l in p.splitlines() if "**Anchor:**" in l]
    marks = sum(a.count(" -- --") + a.count(" ———") + a.count("<break time=") for a in anchors)
    assert marks >= 2, f"no pause marks found in {len(anchors)} anchors"
    assert "deliberate, not typo" in p.replace("not typos", "not typo")
    assert "never add, move or remove them" in p


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_marker_only_ever_sits_before_a_comma(call_type, stage):
    """Simulating chunkPlan against the live script: with the marker before
    terminal punctuation, 9 of 11 landed at a chunk END, and a chunk-final marker
    gets VOICED — "with Jon ———." came back "with John e", "later ———!" came back
    "later, V". Commas are not split boundaries, so a marker there is always
    mid-chunk. Same simulation with comma placement: 0 at a chunk end."""
    M = " " + "—" * 3
    for line in get_stage_prompt(call_type, stage).splitlines():
        if "**Anchor:**" not in line or M not in line:
            continue
        for i, ch in enumerate(line):
            if line.startswith(M, i):
                after = line[i + len(M):i + len(M) + 1]
                assert after == ",", (
                    f"marker not before a comma (found {after!r}): {line.strip()}")


@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_the_greeting_carries_no_pause_marker_of_any_kind(call_type, stage):
    """Call 01a055a2 proved it: dashes reached ElevenLabs on 5 of 10 chunks but
    the greeting arrived bare, because its gate said "carries no ... and no —"
    while the anchor carried  ———. A contradiction gets silently resolved, never
    flagged. Owner's own finding was that stops in this line are what make the
    open sound robotic, so the gate wins and the anchor is bare."""
    greeting = first_message(call_type)
    for mark in (" ———", "...", "—"):
        assert mark not in greeting, f"pause marker in the open: {greeting.strip()}"
    # The leading <break> is the ONE exception and is deliberate: it is the only
    # break ever measured working (1.1s of real silence, greeting intact) because
    # at position zero there is no preceding word for ElevenLabs to echo.
    assert greeting.startswith("<break time="), greeting.strip()
    assert greeting.count("<break") == 1, f"extra breaks in the open: {greeting.strip()}"


def _push_src() -> str:
    return (pathlib.Path(__file__).resolve().parents[2]
            / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")


def _call_flow_entries(src: str):
    """The three flows that call prospects, in file order. The TEST twins come
    after them and deliberately keep the base settings."""
    return [src.split(f'"{name}": {{', 1)[1].split(f'"{nxt}": {{', 1)[0]
            for name, nxt in (("confirmation", "reminder"), ("reminder", "rebooking"),
                              ("rebooking", "test"))]


def test_steadier_voice_is_on_every_call_flow_not_the_test_twins():
    """Started Rebooking-only for the owner's choppy-line A/B (2026-09-14). He put
    it on all three on 2026-09-16: "all the settings rebooking has except for
    model". The TEST twins keep the base 0.45 / 0.75."""
    src = _push_src()
    for entry in _call_flow_entries(src):
        assert '"stability": 0.6,' in entry and '"style": 0.5,' in entry
    assert src.count('"stability": 0.6,') == 3 and src.count('"style": 0.5,') == 3


def test_smart_endpointing_is_off_on_every_call_flow():
    """Owner's first latency test (2026-09-15): call 01a0a653 waited 12s for the
    endpointer, and dropping the LiveKit plan took endpointing to a flat ~101ms.
    Rebooking first, all three from 2026-09-16. The trade-off is cutting a prospect
    off mid-thought (call 01a0a7fe), which now reaches the live flows too."""
    src = _push_src()
    for entry in _call_flow_entries(src):
        assert '"smart_endpointing": False,' in entry
        assert '"wait_seconds": 0.0,' in entry
    assert src.count('"smart_endpointing": False,') == 3
    assert src.count('"wait_seconds": 0.0,') == 3
    assert 'if k != "smartEndpointingPlan"' in src
    # optimizeStreamingLatency 3 stays Rebooking-only: it is what dropped two words
    # from the audio on call 01a0ab4a, and the revert is still pending.
    rebooking = src.split('"rebooking": {', 1)[1].split('"test": {', 1)[0]
    assert '"optimizeStreamingLatency": 3,' in rebooking
    assert src.count('"optimizeStreamingLatency": 3,') == 1


def test_every_flow_runs_gpt_41():
    """gpt-4.1-mini was tried on Rebooking for one call (2026-09-15, 01a0a7a6) and
    ignored the day the prospect asked for. Every flow is back on gpt-4.1; the
    per-flow "model" key stays available for a trial on TEST."""
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert '"model": "gpt-4.1-mini",' not in src
    assert 'flow.get("model", "gpt-4.1")' in src


def test_quiet_room_tone_is_on_every_call_flow():
    """Owner (2026-09-15): the office loop was too loud. Vapi has no volume field,
    so the flows point at a hosted quiet room tone — Rebooking first, all three from
    2026-09-16. The TEST twins keep the built-in "office" loop."""
    src = _push_src()
    url = "https://webhooks.c7a.io/voicsito/static/room-tone-quiet.mp3"
    for entry in _call_flow_entries(src):
        assert f'"background_sound": "{url}",' in entry
    assert src.count('"background_sound":') == 3
    assert 'cfg["backgroundSound"] = flow["background_sound"]' in src
    assert '"backgroundSound": "office",' in src


# ── two messages: static prompt + per-call context (2026-09-15) ─────────────

REBOOKING_VARS = ("currentDay", "firstName", "greeting", "callReason", "missedDay",
                  "whyCalling", "firstOffer", "availableSlots", "dayOffers", "moreTimes",
                  "recentTexts")


def test_rebooking_static_prompt_has_no_variables():
    """A {{variable}} anywhere in the static prompt ends the cacheable prefix there."""
    assert "{{" not in get_stage_prompt("demo_rebooking", "stage1")


def test_rebooking_context_carries_every_variable_the_webhook_sends():
    from src.agent.prompts import get_stage_context
    ctx = get_stage_context("demo_rebooking", "stage1")
    for name in REBOOKING_VARS:
        assert "{{" + name + "}}" in ctx, name


def test_rebooking_persona_date_line_is_swapped_for_the_context_clock():
    from src.agent.prompts.ashley_persona import PERSONA_DATE_LINE
    p = get_stage_prompt("demo_rebooking", "stage1")
    assert PERSONA_DATE_LINE not in p
    assert "currentDay in your call context" in p
    # The other flows keep the persona's own date line until they get a context too.
    assert PERSONA_DATE_LINE in get_stage_prompt("demo_confirmation", "stage1")


def test_push_sends_the_context_as_a_second_system_message():
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")
    assert 'get_stage_context(*flow["prompt"])' in src
    assert '{"role": "system", "content": get_stage_context(*flow["prompt"])}' in src


#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
# The prompt's account of the greeting must match the greeting (owner, 2026-09-24)
#  ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ——— ———--- #
@pytest.mark.parametrize("call_type,stage", ASHLEY_FLOWS)
def test_step_two_quotes_the_line_actually_spoken(call_type, stage):
    """Step 2 tells her what the prospect just heard, and Step 3 reacts to it.
    When the openers changed and this quote did not, she was told the prospect
    had been asked "How is everything?" when they had been asked for twenty
    seconds — so a bare "sure" read as an answer to a question nobody asked.
    Braces are normalised because the rebook prompt writes {firstName} in prose
    where the opener carries {{firstName}}."""
    import re

    prompt = get_stage_prompt(call_type, stage)
    m = re.search(r'The first thing they heard was "(.*?)"', prompt, re.S)
    assert m, f"{call_type}: Step 2 no longer quotes the greeting"

    def norm(s: str) -> str:
        s = re.sub(r"<break[^>]*/>", "", s)
        return re.sub(r"[{}]", "", s).strip()

    assert norm(m.group(1)) in norm(first_message(call_type)), (
        f"{call_type}: Step 2 quotes a line she never speaks.\n"
        f"  prompt says: {m.group(1)}\n"
        f"  she says   : {first_message(call_type)}"
    )
