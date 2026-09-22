# Reference point: 2026-08-19 — pre-ashley-confirm

**Reason:** Last known-good state of the Jon-voiced demo-confirmation squad (Type 4),
captured before the rebuild onto the Ashley persona.

## What changed after this snapshot

The confirmation flow moved from **Jon** (contractor, trade-buddy voice, flooring
backstory, truck/Bluetooth AI-challenge line) to **Ashley** (the person who has been
texting the prospect; Jon becomes the third party she refers to). Two calls now run
off the same Stage 0 router, split by the spoken bot-ID:

- **bot 1** — day-before confirmation (confirm + same-call rebook)
- **bot 2** — day-of reminder (logistics; rebook handoff if they raise a conflict)

Meeting platform moved to Google Meet **inside this flow only** — the inbound Type 2
squad (`inbound_texts.py`, `stage2_qualify.py`, `stage3_book.py`) keeps its Zoom
wording untouched per owner instruction 2026-08-19.

## Files captured

| File | Role at snapshot time |
|---|---|
| `confirm_persona.py` | Jon persona shared by both confirm stages |
| `stage0_router.py` | Silent bot-ID + phone-number router (single destination in use) |
| `stage1_confirm.py` | Day-of confirmation, Zoom link anchor |
| `stage2_rebook.py` | Rebook stage, Zoom link anchor |

## Restore

```bash
cd /path/to/Voicsito
cp docs/reference-points/2026-08-19-pre-ashley-confirm/*.py src/agent/prompts/
# then re-deploy the confirm squad
python scripts/push_confirm_squad.py
```

Restoring the prompt files alone does NOT revert Vapi-side config (voice ID, router
destinations). Check `scripts/push_confirm_squad.py` for any voice/destination changes
made alongside the Ashley rebuild and revert those in the same pass.

## Note on tooling

`scripts/snapshot_reference_point.py` still lists only the **inbound** prompt files in
its `PROMPT_FILES` constant — it predates the confirm squad and would have snapshotted
the wrong files here. This snapshot was taken by hand. Extend that list before the next
confirm-flow change.
