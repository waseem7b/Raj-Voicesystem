#!/usr/bin/env python3
"""Simulate a Voicsito phone call against the LIVE 3-stage Vapi Squad prompts.

Loads `stage1_inbound_texts.PROMPT`, `stage2_qualify.PROMPT`, `stage3_book.PROMPT`
from src/agent/prompts/. When the Stage 1 / Stage 2 model fires the `handoff`
tool, the sim swaps the system prompt to the next stage's prompt (history is
preserved — mirrors Vapi's `contextEngineeringPlan: userAndAssistantMessages`).
When the model fires `endCall`, the call ends.

Two Claudes role-play:
  - "Jon" uses whichever stage prompt is currently active, with the same model
    + temperature + maxTokens as the live Vapi assistants.
  - "Prospect" uses the persona's persona_prompt as their system message.

Tool calls are mocked locally. Booking succeeds on book_video_call; the call
also ends on endCall or when the prospect signals hangup.

Usage:
    .venv-tests/bin/python scripts/sim-call.py --persona tests/personas/curious_first_timer.yaml
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from anthropic import Anthropic

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PROJECT_ROOT / "tests" / "transcripts"

sys.path.insert(0, str(PROJECT_ROOT))
from src.agent.prompts import get_stage_prompt  # noqa: E402

# Persona may override this via `prospect_tz: <IANA>` in YAML. When set, the
# get_available_slots mock generates slot ISOs in that zone and formats them
# like the real webhook (with [iso:...] tags), so the agent sees realistic
# data exactly as production would deliver it.
_PROSPECT_TZ: str = ""

# Vapi assistants use claude-sonnet-4-6 (or via OpenRouter alias).
JON_MODEL = os.environ.get("SIM_JON_MODEL", "anthropic/claude-sonnet-4.6")
JON_TEMPERATURE = 0.6
JON_MAX_TOKENS = 350

PROSPECT_MODEL = os.environ.get("SIM_PROSPECT_MODEL", "anthropic/claude-sonnet-4.6")
PROSPECT_TEMPERATURE = 0.8
PROSPECT_MAX_TOKENS = 200

# Vapi firstMessage (the line Vapi speaks before the LLM is invoked).
FIRST_MESSAGE = "Hey, Jon here — who is this calling?"

# ───────────────────────────── Tool definitions ─────────────────────────────
#
# Each stage exposes the same set of tools its live Vapi assistant has:
#   stage1: update_prospect_info, handoff(→stage2), endCall
#   stage2: update_prospect_info, handoff(→stage3), endCall
#   stage3: update_prospect_info, get_available_slots, mark_for_transfer,
#           book_video_call, endCall

TOOL_UPDATE_PROSPECT_INFO = {
    "name": "update_prospect_info",
    "description": (
        "Save the prospect's own info to CRM as you hear it. Fire-and-forget — "
        "do NOT mention it, do NOT pause, keep talking. Skip for your own name "
        "(Jon), partner (Waseem), or third parties."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "business_name": {"type": "string"},
            "email": {"type": "string"},
        },
        "required": [],
    },
}

TOOL_HANDOFF_STAGE2 = {
    "name": "handoff",
    "description": (
        "Fire AFTER the prospect verbally agrees to the marketing conversation "
        "at the end of the reveal anchor. Fire it silently. Do not fire on a "
        "hard disqualifier — call endCall instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "first_name": {"type": "string"},
            "biz_type": {"type": "string"},
            "reason": {"type": "string"},
            "raised_objections": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["first_name", "biz_type", "reason", "raised_objections"],
    },
}

TOOL_HANDOFF_STAGE3 = {
    "name": "handoff",
    "description": (
        "Fire AFTER the last discovery question has been answered, OR when the "
        "prospect explicitly asks to book / schedule now. Do not fire on a hard "
        "disqualifier — call endCall instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "first_name": {"type": "string"},
            "biz_type": {"type": "string"},
            "business": {"type": "string"},
            "city": {"type": "string"},
            "state": {"type": "string"},
            "crew_size": {"type": "string"},
            "lead_source": {"type": "string"},
            "job_volume": {"type": "string"},
            "capacity_for_more": {"type": "string"},
        },
        "required": [
            "first_name", "biz_type", "business", "city", "state",
            "crew_size", "lead_source", "job_volume", "capacity_for_more",
        ],
    },
}

TOOL_END_CALL = {
    "name": "endCall",
    "description": (
        "Hang up the call. Fire when the conversation is over — hostile prospect, "
        "hard disqualifier, or after the sign-off anchor in Stage 3."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

TOOL_GET_AVAILABLE_SLOTS = {
    "name": "get_available_slots",
    "description": "Return the next available video-call slots. Call when prospect mentions a day/time preference.",
    "input_schema": {
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
        },
        "required": [],
    },
}

TOOL_MARK_FOR_TRANSFER = {
    "name": "mark_for_transfer",
    "description": "Tag the prospect for transfer to the main CRM. Call the MOMENT the prospect agrees to a specific slot — BEFORE asking for their email.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

TOOL_BOOK_VIDEO_CALL = {
    "name": "book_video_call",
    "description": "Book the chosen ISO-8601 slot. Call AFTER mark_for_transfer + email captured + email confirmed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slot_iso": {"type": "string"},
            "email": {"type": "string"},
            "prospect_first_name": {"type": "string"},
            "business_name": {"type": "string"},
        },
        "required": ["slot_iso", "email", "prospect_first_name"],
    },
}


def tools_for_stage(stage: str) -> list[dict]:
    if stage == "stage1":
        return [TOOL_UPDATE_PROSPECT_INFO, TOOL_HANDOFF_STAGE2, TOOL_END_CALL]
    if stage == "stage2":
        return [TOOL_UPDATE_PROSPECT_INFO, TOOL_HANDOFF_STAGE3, TOOL_END_CALL]
    if stage == "stage3":
        return [
            TOOL_UPDATE_PROSPECT_INFO,
            TOOL_GET_AVAILABLE_SLOTS,
            TOOL_MARK_FOR_TRANSFER,
            TOOL_BOOK_VIDEO_CALL,
            TOOL_END_CALL,
        ]
    raise ValueError(f"unknown stage {stage}")


def render_stage_prompt(stage: str, session_vars: dict) -> str:
    """Pull the live Squad prompt for `stage` and substitute Vapi template vars."""
    raw = get_stage_prompt("inbound_texts", stage)
    return (
        raw
        .replace("{{biz_type}}", session_vars.get("biz_type", ""))
        .replace("{{business}}", session_vars.get("business", ""))
        .replace("{{city}}", session_vars.get("city", ""))
        .replace("{{state}}", session_vars.get("state", ""))
        .replace("{{first_name}}", session_vars.get("first_name", ""))
    )


def load_persona(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# ─────────────────────────── Tool mocks ───────────────────────────

def _capture_into(session_vars: dict, src: dict, *keys: str) -> None:
    for k in keys:
        v = src.get(k)
        if isinstance(v, str) and v.strip():
            session_vars[k] = v.strip()


def mock_tool_result(tool_name: str, tool_input: dict, session_vars: dict) -> str:
    """Return a deterministic fake tool response and mutate session_vars for any
    info the tool captured. Mirrors the real webhook's responses where reasonable.
    """
    if tool_name == "update_prospect_info":
        # business_name on this tool maps to {{business}} in the prompt
        if isinstance(tool_input.get("business_name"), str) and tool_input["business_name"].strip():
            session_vars["business"] = tool_input["business_name"].strip()
        _capture_into(session_vars, tool_input, "first_name", "last_name", "email")
        return "ok"

    if tool_name == "handoff":
        _capture_into(
            session_vars, tool_input,
            "first_name", "biz_type", "business", "city", "state",
            "crew_size", "lead_source", "job_volume", "capacity_for_more",
            "reason",
        )
        return "Handoff complete."

    if tool_name == "endCall":
        return "Call ended."

    if tool_name == "get_available_slots":
        tz_name = _PROSPECT_TZ or "America/Chicago"
        zi = ZoneInfo(tz_name)
        now_local = datetime.now(zi)
        lines: list[str] = []
        for d in (1, 2):
            day = (now_local + timedelta(days=d)).replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            slot_strs = []
            for h in (10, 14):
                t = day.replace(hour=h)
                stamp = f"{int(t.strftime('%I'))}:{t.strftime('%M%p')}".lower()
                slot_strs.append(f"{stamp} [iso:{t.isoformat()}]")
            lines.append(f"{day.strftime('%A %b')} {day.day}: " + ", ".join(slot_strs))
        return (
            "Available slots (use the [iso:...] value when calling book_video_call):\n"
            + "\n".join(lines)
        )

    if tool_name == "mark_for_transfer":
        return "Transfer initiated."

    if tool_name == "book_video_call":
        slot_iso = tool_input.get("slot_iso", "")
        return f"Booked successfully for {slot_iso}."

    return f"unknown tool {tool_name}"


MAX_TOOL_LOOPS = 8  # cap on tool_use iterations within a single Jon turn

# When a tool fires and the call should immediately leave the current stage
# (handoff) or terminate (endCall), break out of jon_turn's inner loop so the
# main loop can swap prompts / end the call. Mirrors Vapi's behavior:
# - handoff: Vapi intercepts the call, swaps to the next stage; model never
#   sees a tool result
# - endCall: Vapi hangs up after the call; no further model turns
_TERMINAL_TOOLS = ("handoff", "endCall")

# Mirrors the Vapi rejectionPlan on the endCall tool (see push_squad.endcall_tool).
# If the prospect's most recent message doesn't contain a goodbye / transactional-
# close / disqualifier signal, Vapi rejects the tool call and the model has to
# generate a different turn. Sim applies the same regex so behavior matches.
_END_CALL_ALLOWLIST = re.compile(
    r"(?i)\b("
    r"bye|goodbye|take care|talk soon|talk later|see ya|"
    r"have a good (one|day|night|rest)|sounds good|sounds great|perfect|"
    r"thanks|appreciate|cheers|cool|alright|got it|yep|all good|"
    r"stop calling|take me off|don'?t call|remove me|not interested|"
    r"retiring|no business|out of business|wrong number|hang up|"
    r"that'?s all|we'?re done|that'?s it"
    r")\b"
)


def _last_prospect_text(history: list[dict]) -> str:
    """Return the last user-role string message in history (skipping tool_result lists)."""
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
    return ""


def call_with_retry(fn, *args, **kwargs):
    """Wrap an Anthropic API call with 3-attempt exponential backoff on
    RateLimitError + 5xx APIStatusError. Other exceptions raise immediately.
    """
    import anthropic
    last_exc = None
    for attempt in range(3):
        try:
            return fn(*args, **kwargs)
        except (anthropic.RateLimitError,) as e:
            last_exc = e
        except anthropic.APIStatusError as e:
            if 500 <= getattr(e, "status_code", 0) < 600:
                last_exc = e
            else:
                raise
        if attempt < 2:
            time.sleep(2 ** attempt)
    raise last_exc


def jon_turn(
    client: Anthropic,
    system: str,
    tools: list[dict],
    history: list[dict],
    session_vars: dict,
) -> tuple[str, list[dict], str | None]:
    """Run Jon for one outer turn. MUTATES history in place.

    Returns (final_text, mocked_calls, terminal_signal).
    terminal_signal is one of: None, "handoff", "endCall".
    """
    mocked_calls: list[dict] = []
    text_parts: list[str] = []
    terminal: str | None = None
    for _ in range(MAX_TOOL_LOOPS):
        resp = call_with_retry(
            client.messages.create,
            model=JON_MODEL,
            max_tokens=JON_MAX_TOKENS,
            temperature=JON_TEMPERATURE,
            system=system,
            tools=tools,
            messages=history,
        )
        assistant_blocks = [
            (b.model_dump() if hasattr(b, "model_dump") else dict(b))
            for b in resp.content
        ]
        history.append({"role": "assistant", "content": assistant_blocks})
        for b in resp.content:
            if b.type == "text" and b.text.strip():
                text_parts.append(b.text)
        if resp.stop_reason != "tool_use":
            break
        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                result = mock_tool_result(block.name, block.input, session_vars)
                rejected = False
                if block.name == "endCall":
                    last = _last_prospect_text(history)
                    if last and not _END_CALL_ALLOWLIST.search(last):
                        result = (
                            "Tool call rejected by Vapi rejectionPlan — the prospect "
                            "has not signaled end of conversation in their last turn. "
                            "Continue the call instead of hanging up."
                        )
                        rejected = True
                mocked_calls.append({"name": block.name, "input": block.input,
                                      "result": result, "rejected": rejected})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result if isinstance(result, str) else json.dumps(result),
                })
                if block.name in _TERMINAL_TOOLS and not rejected:
                    terminal = block.name
        # Append tool_results so the conversation history stays consistent for
        # subsequent stages — even when we break out below, history must record
        # what happened.
        history.append({"role": "user", "content": tool_results})
        if terminal is not None:
            break
    return "".join(text_parts).strip(), mocked_calls, terminal


def prospect_turn(client: Anthropic, system: str, dialogue: list[dict]) -> str:
    """Run prospect for one turn. dialogue is a flat list of {role, text} for the prospect's POV.
    From the prospect's POV, Jon is 'user' and prospect is 'assistant'.
    """
    messages = [{"role": d["role"], "content": d["text"]} for d in dialogue]
    resp = call_with_retry(
        client.messages.create,
        model=PROSPECT_MODEL,
        max_tokens=PROSPECT_MAX_TOKENS,
        temperature=PROSPECT_TEMPERATURE,
        system=system,
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text


# Stage detection — used by --stop-after-step. Jon's verbatim phrases anchor
# each step. Step 8 is detected by tool_use signal instead of text.
STEP_DETECTORS = [
    (1, r"(?i)thanks for the call|just driving right now|might cut out"),
    (2, r"(?i)didn't catch your name"),
    (3, r"(?i)how can i help you"),
    (4, r"(?i)are you guys (mostly|only) (doing|handling)"),
    (5, r"(?i)zero to four million|custom seo system|even open to having that conversation"),
    (6, r"(?i)solely your company|how many guys on your crew|where('?s| is) the majority of your business|how many jobs are you doing|would you have to hire"),
    (7, r"(?i)run a report for your area|jump on a video call|morning or evening person"),
    (9, r"(?i)good number to keep texting|group chat with me and my partner"),
    (10, r"(?i)pleasure to meet you|enjoy the rest of your day|we'?ll speak soon"),
]


def detect_step(jon_text: str, mocked_calls: list[dict]) -> int | None:
    """Return the highest-numbered step detected in this turn, or None."""
    if any(c["name"] == "book_video_call" for c in mocked_calls):
        return 8
    if any(c["name"] in ("get_available_slots", "mark_for_transfer") for c in mocked_calls):
        return 8
    detected = None
    for step_n, pattern in STEP_DETECTORS:
        if re.search(pattern, jon_text):
            if detected is None or step_n > detected:
                detected = step_n
    return detected


def run_simulation(persona_path: Path, max_turns: int, quiet: bool, stop_after_step: int | None) -> dict:
    persona = load_persona(persona_path)
    prospect_system = persona["persona_prompt"]

    # Set the prospect timezone for the get_available_slots mock so the agent
    # sees slots in the prospect's local time, mirroring production behavior.
    global _PROSPECT_TZ
    _PROSPECT_TZ = (persona.get("prospect_tz") or "").strip()

    # Seed session_vars from the persona — biz_type + any CRM-hit fields. Note
    # we do NOT seed first_name from the persona; in the real flow first_name
    # is captured during the call (Context block doesn't include first_name).
    session_vars: dict = {
        "biz_type": persona.get("biz_type", "") or "",
        "business": persona.get("business_name", "") or "",
        "city": persona.get("city", "") or "",
        "state": persona.get("state", "") or "",
    }

    if stop_after_step is None and persona.get("stop_after_step") is not None:
        stop_after_step = int(persona["stop_after_step"])

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    client = Anthropic(base_url=base_url) if base_url else Anthropic()

    # Jon's view: full Claude messages list (mutated in place by jon_turn).
    jon_history: list[dict] = [
        {"role": "assistant", "content": FIRST_MESSAGE},
        {"role": "user", "content": persona["opening"]},
    ]

    # Prospect's view: their replies are 'assistant', Jon's lines are 'user'.
    prospect_dialogue: list[dict] = [
        {"role": "user", "text": FIRST_MESSAGE},
        {"role": "assistant", "text": persona["opening"]},
    ]

    transcript: list[dict] = [
        {"speaker": "system", "text": FIRST_MESSAGE, "turn": 0},
        {"speaker": "prospect", "text": persona["opening"], "turn": 0},
    ]

    current_stage = "stage1"
    end_reason = "max_turns_reached"
    stopped_at_step: int | None = None
    turn = 0
    stage_transitions: list[dict] = []

    for turn in range(1, max_turns + 1):
        jon_system = render_stage_prompt(current_stage, session_vars)
        jon_tools = tools_for_stage(current_stage)
        try:
            jon_text, mocked_calls, terminal = jon_turn(
                client, jon_system, jon_tools, jon_history, session_vars
            )
        except Exception as e:
            end_reason = f"jon_error: {e}"
            break

        # Record tool calls in transcript order
        for call in mocked_calls:
            transcript.append({
                "speaker": "tool",
                "tool": call["name"],
                "input": call["input"],
                "result": call["result"],
                "stage": current_stage,
                "turn": turn,
            })
            if not quiet:
                print(f"[tool/{current_stage}] {call['name']}({json.dumps(call['input'])}) -> {json.dumps(call['result'])[:120]}")

        if jon_text:
            prospect_dialogue.append({"role": "user", "text": jon_text})
            detected_step = detect_step(jon_text, mocked_calls)
            transcript.append({
                "speaker": "jon",
                "text": jon_text,
                "stage": current_stage,
                "turn": turn,
                "detected_step": detected_step,
            })
            if not quiet:
                step_tag = f" [step {detected_step}]" if detected_step else ""
                print(f"[jon/{current_stage}]{step_tag} {jon_text}")
        else:
            detected_step = None

        # End on booking
        def _is_booked(c: dict) -> bool:
            if c["name"] != "book_video_call":
                return False
            r = c.get("result")
            if isinstance(r, str):
                return r.lower().startswith("booked")
            return False

        if any(_is_booked(c) for c in mocked_calls):
            end_reason = "booked"
            stopped_at_step = 8
            break

        # End on assistant-fired endCall
        if terminal == "endCall":
            end_reason = "assistant_ended_call"
            break

        # Handoff: swap stage, record transition, immediately re-enter jon_turn
        # for the new stage without letting the prospect speak (mirrors Vapi
        # behavior — Vapi handoff is silent and the next stage speaks first).
        if terminal == "handoff":
            prior = current_stage
            current_stage = {"stage1": "stage2", "stage2": "stage3"}.get(current_stage, current_stage)
            transition_note = f"<<handoff: {prior} → {current_stage}>>"
            stage_transitions.append({"from": prior, "to": current_stage, "turn": turn,
                                      "vars_snapshot": dict(session_vars)})
            transcript.append({"speaker": "system", "text": transition_note,
                               "stage": current_stage, "turn": turn})
            if not quiet:
                print(f"[system]   {transition_note}  vars={json.dumps(session_vars)}")
            continue

        # End on stage-scope cutoff (after step content detection)
        if stop_after_step is not None and detected_step is not None and detected_step >= stop_after_step:
            end_reason = "stage_complete"
            stopped_at_step = detected_step
            break

        # Prospect speaks
        try:
            prospect_text = prospect_turn(client, prospect_system, list(prospect_dialogue))
        except Exception as e:
            end_reason = f"prospect_error: {e}"
            break

        if not prospect_text:
            end_reason = "prospect_empty_response"
            break

        prospect_dialogue.append({"role": "assistant", "text": prospect_text})
        jon_history.append({"role": "user", "content": prospect_text})
        transcript.append({
            "speaker": "prospect",
            "text": prospect_text,
            "stage": current_stage,
            "turn": turn,
        })
        if not quiet:
            print(f"[prospect] {prospect_text}")

        if re.search(r"\b(hang up|bye|gotta go|goodbye|take care|talk later)\b", prospect_text, re.I):
            end_reason = "prospect_hangup"
            break

    return {
        "persona": persona["name"],
        "biz_type": persona.get("biz_type"),
        "end_reason": end_reason,
        "stopped_at_step": stopped_at_step,
        "turns_used": turn,
        "transcript": transcript,
        "stage_transitions": stage_transitions,
        "final_stage": current_stage,
        "session_vars": session_vars,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def format_markdown(result: dict) -> str:
    """Render a sim result as a human-readable Markdown transcript with YAML frontmatter."""
    fm_keys = ["persona", "biz_type", "end_reason", "stopped_at_step",
               "turns_used", "elapsed_seconds", "final_stage", "started_at"]
    fm_lines = ["---"]
    for k in fm_keys:
        if k in result and result[k] is not None:
            v = result[k]
            if isinstance(v, str):
                fm_lines.append(f'{k}: "{v}"')
            else:
                fm_lines.append(f"{k}: {v}")
    if result.get("stage_transitions"):
        fm_lines.append("stage_transitions:")
        for st in result["stage_transitions"]:
            fm_lines.append(f"  - turn: {st['turn']}  ({st['from']} → {st['to']})")
    fm_lines.append("---")

    body = []
    body.append(f"# {result['persona']} ({result.get('biz_type', '?')})")
    body.append("")
    body.append(f"**End:** {result['end_reason']}  |  **Turns:** {result['turns_used']}"
                + (f"  |  **Elapsed:** {result.get('elapsed_seconds', '?')}s" if result.get('elapsed_seconds') else ""))
    body.append("")
    body.append("---")
    body.append("")
    for t in result["transcript"]:
        sp = t.get("speaker", "?")
        stage = t.get("stage")
        tag = f"/{stage}" if stage else ""
        if sp == "tool":
            inp = json.dumps(t.get("input", {}))
            res = json.dumps(t.get("result", {}))
            body.append(f"**tool{tag}:** `{t.get('tool')}({inp})` → `{res}`")
        else:
            text = t.get("text", "").strip().replace("\n", "\n> ")
            body.append(f"**{sp}{tag}:** {text}")
        body.append("")
    return "\n".join(fm_lines) + "\n\n" + "\n".join(body).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", required=True, type=Path, help="Path to persona YAML")
    parser.add_argument("--turns", type=int, default=30, help="Max outer turns (default 30)")
    parser.add_argument("--stop-after-step", type=int, default=None,
                        help="Halt the run when Jon completes step N (1-10).")
    parser.add_argument("--quiet", action="store_true", help="Suppress live conversation output")
    parser.add_argument("--out", type=Path, help="Output transcript Markdown path")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / "config" / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if os.environ.get("OPENROUTER_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        else:
            sys.exit("Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set in config/.env")

    if not args.persona.exists():
        sys.exit(f"persona file not found: {args.persona}")

    started = time.time()
    result = run_simulation(
        args.persona,
        max_turns=args.turns,
        quiet=args.quiet,
        stop_after_step=args.stop_after_step,
    )
    result["elapsed_seconds"] = round(time.time() - started, 2)

    out_path = args.out or (TRANSCRIPTS_DIR / f"{result['persona']}-{int(time.time())}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_markdown(result), encoding="utf-8")

    print()
    print(f"=== End: {result['end_reason']}  |  turns: {result['turns_used']}  |  elapsed: {result['elapsed_seconds']}s ===")
    print(f"final stage: {result['final_stage']}  |  transitions: {len(result['stage_transitions'])}")
    print(f"transcript saved to: {out_path}")


if __name__ == "__main__":
    main()
