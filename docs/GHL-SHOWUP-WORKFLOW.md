# GHL Show-Up Workflow — build spec

Owner-designed 2026-08-21.

**Built as THREE chained workflows, not one.** GHL workflow branches never
rejoin the trunk — there is no "go to step 3". A single workflow with jumps
cannot be built, and asking GHL's AI to try produces broken sequence links.
Each workflow below ends by adding the contact to the next one.

Calls are placed by `POST /ghl/outbound-call` (`src/webhooks/ghl_trigger.py`).
Branches read contact tags: two written by Ashley **during** the call, the rest
by `/vapi/outbound/events` when it **ends**.

## Tags

| Tag | Written by | Means |
|---|---|---|
| `locked-in` | Ashley, in-call | Confirmed attendance |
| `not-interested` | Ashley, in-call | Does not want the demo at all |
| `call-rescheduled` | end-of-call | Moved to a new time |
| `call-answered` | end-of-call | Picked up, gave no clear answer |
| `call-no-answer` | end-of-call | Nobody picked up |
| `call-voicemail` | end-of-call | Went to voicemail |

**The workflow adds none of these.** Ashley writes `locked-in` and
`not-interested` while still on the phone. `call-*` tags are cleared before each
redial; `locked-in` and `not-interested` are never cleared.

`locked-in` is also read by the CRM to stamp `calendar_events.locked_in_at`,
which drives show-rate reporting. Do not rename it.

## The webhook action (identical in all three workflows)

```
Method: POST
URL:    https://webhooks.c7a.io/voicsito/ghl/outbound-call
Header: X-GHL-Secret: [GHL_OUTBOUND_SECRET]
Body type: CUSTOM / JSON
{
  "call_type": "confirmation",
  "phone": "{{contact.phone}}",
  "contact_id": "{{contact.id}}",
  "appointment_id": "{{appointment.id}}",
  "appointment_status": "{{appointment.status}}",
  "first_name": "{{contact.first_name}}"
}
```
Only `call_type` changes: `"confirmation"` in workflow 1, `"reminder"` in 2 and 3.

---

```
=====================================================================
WORKFLOW 1 of 3:  "Show Up — 1. Confirmation"
=====================================================================
TRIGGER   Appointment Status
          Event type = Normal
          In calendar = [Video Call Calendar]
          Appointment status = booked OR confirmed
          Enrollment: Contact only
          Settings: allow re-entry = ON

 1. WAIT UNTIL   Appointment Time minus 1 day, at 9:00 AM
 2. WEBHOOK      call_type = "confirmation"
 3. WAIT         10 minutes
 4. IF/ELSE  (evaluate in this order)
      Branch A  Tags INCLUDE "call-rescheduled"
                -> END
      Branch B  Tags INCLUDE "not-interested"
                -> Move Opportunity: Not Interested W/o Demo
                -> END
      Branch C  Tags INCLUDE "locked-in"  OR  "call-answered"
                -> Add to Workflow: "Show Up — 2. Reminder"
                -> END
      Branch D  None of the above          (no answer / voicemail)
                -> WAIT UNTIL  2:00 PM same day
                -> WEBHOOK     call_type = "confirmation"
                -> WAIT        10 minutes
                -> IF/ELSE
                     B1  Tags INCLUDE "call-rescheduled"  -> END
                     B2  Tags INCLUDE "not-interested"
                         -> Move Opportunity: Not Interested W/o Demo -> END
                     B3  Tags INCLUDE "locked-in" OR "call-answered"
                         -> Add to Workflow: "Show Up — 2. Reminder" -> END
                     B4  None
                         -> SMS: [confirmation text]
                         -> Add to Workflow: "Show Up — 2. Reminder"
                         -> END

=====================================================================
WORKFLOW 2 of 3:  "Show Up — 2. Reminder"
=====================================================================
TRIGGER   Contact added to workflow (no trigger filter needed)
          Settings: allow re-entry = ON

 1. WAIT UNTIL   Appointment Time, at 9:00 AM same day
 2. WEBHOOK      call_type = "reminder"
 3. WAIT         10 minutes
 4. IF/ELSE  (evaluate in this order)
      Branch A  Tags INCLUDE "call-rescheduled"
                -> END
      Branch B  Tags INCLUDE "not-interested"
                -> Move Opportunity: Not Interested W/o Demo
                -> END
      Branch C  Tags INCLUDE "locked-in"  OR  "call-answered"
                -> Add to Workflow: "Show Up — 3. Final Reminder"
                -> END
      Branch D  None of the above          (no answer / voicemail)
                -> IF/ELSE
                     D1  Tags INCLUDE "locked-in"
                         -> SMS: [reminder text 1 — already confirmed]
                     D2  None
                         -> SMS: [reminder text 2 — never confirmed]
                -> Add to Workflow: "Show Up — 3. Final Reminder"
                -> END

=====================================================================
WORKFLOW 3 of 3:  "Show Up — 3. Final Reminder"
=====================================================================
TRIGGER   Contact added to workflow
          Settings: allow re-entry = ON

 1. WAIT UNTIL   Appointment Time minus 2 hours
 2. WEBHOOK      call_type = "reminder"
 3. WAIT         10 minutes
 4. IF/ELSE  (evaluate in this order)
      Branch A  Tags INCLUDE "call-rescheduled"        -> END
      Branch B  Tags INCLUDE "not-interested"
                -> Move Opportunity: Not Interested W/o Demo -> END
      Branch C  Tags INCLUDE "locked-in" OR "call-answered"  -> END
      Branch D  None of the above  -> Send voice note -> END
=====================================================================
```

## Rules that must not be changed

**Branch order.** Check `call-rescheduled` and `not-interested` FIRST, before
`locked-in`. Those two are never cleared, so a prospect confirmed on Monday who
declines on Tuesday still carries `locked-in` — testing it first would route a
declined prospect back into the reminder flow.

**The 10-minute waits.** Outcome tags are written when the call ENDS. Branch
immediately and every contact reads as untagged, sending everyone down the
no-answer path.

**Re-entry ON, all three.** A reschedule creates a new appointment, which
re-fires workflow 1 from the top. Without re-entry the second booking is
silently ignored.

**Every branch ends.** No branch continues past its own END, because GHL
branches cannot rejoin. Moving to the next stage is always an explicit
"Add to Workflow".

## Known gaps

- **Ashley leaves no voicemail.** `call-voicemail` tags correctly but the call
  is silent. Vapi's `VoicemailTool` would fix it; needs wording.
- **"I'll send you the meeting link an hour before"** is a promise nothing keeps
  until the GHL text steps are built.
