"""Prompt registry — monolithic + staged.

Two prompt systems coexist here during the staged-rollout window:

1) **Monolithic** (legacy, rollback target)
   - `inbound_texts`  — Type 2: single 33K-char prompt with everything.

2) **Staged** (new, 3-stage Vapi Squad — see /Stages.md)
   Shared module used by every stage:
     - `shared_persona`        — persona, voice, anti-robot, ElevenLabs formatting,
                                 anti-recitation, phone-call reality, context (CRM vars),
                                 forbidden language

   Stage 1 — INTEREST (per call type; opener -> reveal -> handoff):
     - `stage1_inbound_texts`  — Type 2 inbound text-lead opener -> reveal

   Stage 2 — QUALIFY (shared across all call types):
     - `stage2_qualify`        — discovery + FAQs + mid-call objections -> handoff

   Stage 3 — BOOK (shared across all call types):
     - `stage3_book`           — close transition -> book slot -> sign off

Call types (see /docs/call-types.md):
  - inbound_texts        Type 2  — warm SMS lead calls in
  - inbound_main         Type 6  — booked-demo prospect calls in (service mode)
  - outbound_discovery   Type 1  — scheduled outbound discovery
  - callback             Type 3  — prospect calling back after missed outbound
  - demo_confirmation    Type 4  — day-of-demo confirmation outbound
  - rebooking            Type 5  — no-show/cancel recovery outbound

When new call types come online, each gets its own `stage1_<call_type>` module
and reuses `stage2_qualify` + `stage3_book` as-is.
"""

from . import inbound_texts
from . import stage1_inbound_texts
from . import stage2_qualify
from . import stage3_book
from . import stage0_router
from . import stage1_confirm
from . import stage2_rebook

# Monolithic registry (legacy rollback target)
PROMPT_BY_TYPE = {
    "inbound_texts": inbound_texts.PROMPT,
}

# Staged registry: (call_type, stage) -> prompt
PROMPT_BY_STAGE = {
    ("inbound_texts", "stage1"): stage1_inbound_texts.PROMPT,
    ("inbound_texts", "stage2"): stage2_qualify.PROMPT,
    ("inbound_texts", "stage3"): stage3_book.PROMPT,
    # Outbound demo-confirmation (Type 4). stage0 = silent router; stage1 =
    # confirm (locked-in tag on confirm); stage2 = rebook (handoff from stage1's
    # can't-make-it branch — offers slots + reschedules the existing appointment).
    ("demo_confirmation", "stage0"): stage0_router.PROMPT,
    ("demo_confirmation", "stage1"): stage1_confirm.PROMPT,
    ("demo_confirmation", "stage2"): stage2_rebook.PROMPT,
}


def get_prompt(call_type: str) -> str:
    """Return the monolithic system prompt for a given call type (legacy)."""
    return PROMPT_BY_TYPE[call_type]


def get_stage_prompt(call_type: str, stage: str) -> str:
    """Return the staged system prompt for (call_type, stage).

    stage is one of: 'stage1', 'stage2', 'stage3'
    """
    return PROMPT_BY_STAGE[(call_type, stage)]
