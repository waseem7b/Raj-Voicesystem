# Reference point: 2026-06-09 — opener-business-advance-driving-harden

**Reason:** Opener: business name/trade advances to Step 2 (CL-030 reversed); driving caveat one-time rule hardened (CL-028); ships bundle incl. stage2 6->5 discovery + stage3 stall fix + shared_persona name rule

## What this snapshot contains

Prompt files copied at the moment this snapshot was created:

- `inbound_texts.py` (from `src/agent/prompts/inbound_texts.py`)
- `shared_persona.py` (from `src/agent/prompts/shared_persona.py`)
- `stage1_inbound_texts.py` (from `src/agent/prompts/stage1_inbound_texts.py`)
- `stage2_qualify.py` (from `src/agent/prompts/stage2_qualify.py`)
- `stage3_book.py` (from `src/agent/prompts/stage3_book.py`)


## To restore

```bash
cp docs/reference-points/2026-06-09-opener-business-advance-driving-harden/shared_persona.py src/agent/prompts/
cp docs/reference-points/2026-06-09-opener-business-advance-driving-harden/stage1_inbound_texts.py src/agent/prompts/
cp docs/reference-points/2026-06-09-opener-business-advance-driving-harden/stage2_qualify.py src/agent/prompts/
cp docs/reference-points/2026-06-09-opener-business-advance-driving-harden/stage3_book.py src/agent/prompts/
.venv-extract/bin/python scripts/push_squad.py
```

Verify by pulling the live Vapi assistant config and confirming the system message contents match these files.
