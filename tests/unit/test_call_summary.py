"""Pure formatting + outcome classification — no I/O, no env."""
from src.webhooks.call_summary import (
    classify_outcome,
    format_started_message,
    format_ended_summary,
    duration_label,
)


# ─────────── classify_outcome ───────────

def test_classify_outcome_booked():
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": True, "summary": "Booked Tue 2pm"},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}
            ]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert icon == "✅"
    assert label == "BOOKED"
    assert slot_iso == "2026-05-14T10:00:00-04:00"


def test_classify_outcome_booked_even_when_success_false():
    """A confirmed book_video_call result is ground truth — BOOKED even if the
    LLM successEvaluation disagrees."""
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": False, "summary": "unclear"},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}
            ]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert (icon, label) == ("✅", "BOOKED")
    assert slot_iso == "2026-05-14T10:00:00-04:00"


def test_classify_outcome_agreed_not_booked():
    """Regression (call 019e816d): prospect agreed — mark_for_transfer fired —
    but book_video_call never completed and the assistant ended the call. Must
    NOT read as NOT INTERESTED."""
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": "true",
                     "summary": "Prospect open to marketing; follow-up discussed."},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "mark_for_transfer", "arguments": "{}"}}]},
            {"role": "tool_call_result", "name": "mark_for_transfer",
             "result": "Transfer initiated."},
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "endCall", "arguments": "{}"}}]},
            {"role": "tool_call_result", "name": "endCall", "result": "Success."},
        ],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert (icon, label) == ("🟡", "AGREED – NOT BOOKED")
    assert slot_iso is None


def test_classify_outcome_booked_takes_precedence_over_transfer():
    """A fully booked call has BOTH mark_for_transfer and book_video_call — it
    must classify BOOKED, not AGREED – NOT BOOKED."""
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": True, "summary": "Booked."},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "mark_for_transfer", "arguments": "{}"}}]},
            {"role": "tool_call_result", "name": "mark_for_transfer",
             "result": "Transfer initiated."},
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
    }
    icon, label, _ = classify_outcome(payload)
    assert (icon, label) == ("✅", "BOOKED")


def test_classify_outcome_disqualified_out_of_area():
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": False, "summary": "Out of area, can't serve."},
        "messages": [],
    }
    icon, label, _ = classify_outcome(payload)
    assert icon == "❌"
    assert label == "DISQUALIFIED"


def test_classify_outcome_disqualified_retiring():
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": False, "summary": "Owner is retiring next month."},
        "messages": [],
    }
    icon, label, _ = classify_outcome(payload)
    assert icon == "❌"
    assert label == "DISQUALIFIED"


def test_classify_outcome_not_interested_default():
    """Assistant ended, no booking, no disqualifier keyword -> not interested."""
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": False,
                      "summary": "Prospect declined the demo invitation."},
        "messages": [],
    }
    icon, label, _ = classify_outcome(payload)
    assert icon == "🚫"
    assert label == "NOT INTERESTED"


def test_classify_outcome_customer_hangup():
    payload = {
        "endedReason": "customer-ended-call",
        "analysis": {"successEvaluation": False, "summary": "Customer hung up."},
        "messages": [],
    }
    icon, label, _ = classify_outcome(payload)
    assert icon == "☎️"
    assert label == "HUNG UP"


def test_classify_outcome_unknown_falls_through():
    payload = {"endedReason": "silence-timeout", "analysis": {}, "messages": []}
    icon, label, _ = classify_outcome(payload)
    assert icon == "⚠️"
    assert label == "OTHER"


# ─────────── format_started_message ───────────

def test_format_started_with_crm_hit():
    text = format_started_message(
        phone="+15555550100",
        contact={"first_name": "Mike", "business": "Rock Epoxy",
                 "biz_type": "epoxy flooring", "city": "San Jose", "state": "CA"},
    )
    assert text.startswith("📲 NEW PHONE CALL")
    assert "Caller: +15555550100 / Rock Epoxy / San Jose" in text


def test_format_started_business_empty_falls_back_to_first_name():
    text = format_started_message(
        phone="+1555010010",
        contact={"first_name": "Lucie", "business": "", "city": "Vancouver"},
    )
    assert "Caller: +1555010010 / Lucie / Vancouver" in text


def test_format_started_no_crm():
    text = format_started_message(phone="+15555550199", contact=None)
    assert text.startswith("📲 NEW PHONE CALL")
    assert "Caller: +15555550199 / (cold caller)" in text


def test_format_started_missing_phone():
    text = format_started_message(phone="", contact=None)
    assert "(unknown)" in text


# ─────────── format_ended_summary ───────────

def test_format_ended_booked_full():
    payload = {
        "endedReason": "assistant-ended-call",
        "startedAt": "2026-05-12T14:30:00Z",
        "endedAt": "2026-05-12T14:34:22Z",
        "cost": 0.43,
        "analysis": {"successEvaluation": True, "summary": "Booked Tue 2pm"},
        "transcript": "AI: hi.\nUser: hi.",
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}
            ]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
        "call": {"customer": {"number": "+15555550100"}},
    }
    text = format_ended_summary(
        payload=payload,
        contact={"first_name": "Mike", "business": "Rock Epoxy", "city": "San Jose"},
        folder_name="20260512-143000-019e1a0a",
    )
    # outcome + duration are in the header line — no "CALL COMPLETED" prefix
    assert text.startswith("✅ BOOKED · 4m 22s")
    assert "CALL COMPLETED" not in text
    assert "Caller: +15555550100 / Rock Epoxy / San Jose" in text
    # Booked slot is on its own line
    assert "Booked:" in text and ("May 14" in text or "10:00am" in text)
    assert "Cost: $0.43" in text
    assert "ID: 20260512-143000-019e1a0a" in text
    assert text.rstrip().endswith("Transcript:")
    # Decorator emojis must be stripped from line prefixes
    assert "👤" not in text and "🤌" not in text and "💰" not in text and "🆔" not in text


def test_format_ended_no_transcript_omits_label():
    payload = {
        "startedAt": "2026-05-12T14:30:00Z", "endedAt": "2026-05-12T14:30:42Z",
        "endedReason": "customer-ended-call",
        "analysis": {"successEvaluation": False},
        "transcript": "",
        "messages": [], "cost": 0.02,
        "call": {"customer": {"number": "+15555550100"}},
    }
    text = format_ended_summary(payload=payload, contact=None,
                                folder_name="20260512-143000-zzz")
    assert "Transcript:" not in text
    assert "(cold caller)" in text
    assert "Cost: $0.02" in text


def test_format_ended_missing_cost_shows_dash():
    payload = {
        "startedAt": "2026-05-12T14:30:00Z", "endedAt": "2026-05-12T14:35:00Z",
        "endedReason": "customer-ended-call",
        "analysis": {"successEvaluation": False},
        "transcript": "x", "messages": [],
        "call": {"customer": {"number": "+15555550100"}},
        # no cost key
    }
    text = format_ended_summary(payload=payload, contact=None,
                                folder_name="20260512-143000-zzz")
    assert "Cost: —" in text


# ─────────── duration_label ───────────

def test_duration_label_minutes_and_seconds():
    assert duration_label("2026-05-12T14:30:00Z", "2026-05-12T14:34:22Z") == "4m 22s"


def test_duration_label_seconds_only():
    assert duration_label("2026-05-12T14:30:00Z", "2026-05-12T14:30:42Z") == "42s"


def test_duration_label_malformed():
    assert duration_label("not-a-date", "also-not") == ""
