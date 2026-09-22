# Reference point: 2026-05-20 — call-review-launch

**Reason:** Snapshot at call-review system launch (Task 7 of plan).

## What this snapshot contains

Prompt files copied at the moment this snapshot was created:

- `shared_persona.py` (from `src/agent/prompts/shared_persona.py`)
- `stage1_inbound_texts.py` (from `src/agent/prompts/stage1_inbound_texts.py`)
- `stage2_qualify.py` (from `src/agent/prompts/stage2_qualify.py`)
- `stage3_book.py` (from `src/agent/prompts/stage3_book.py`)


## To restore

```bash
cp docs/reference-points/2026-05-20-call-review-launch/shared_persona.py src/agent/prompts/
cp docs/reference-points/2026-05-20-call-review-launch/stage1_inbound_texts.py src/agent/prompts/
cp docs/reference-points/2026-05-20-call-review-launch/stage2_qualify.py src/agent/prompts/
cp docs/reference-points/2026-05-20-call-review-launch/stage3_book.py src/agent/prompts/
.venv-extract/bin/python scripts/push_squad.py
```

Verify by pulling the live Vapi assistant config and confirming the system message contents match these files.
