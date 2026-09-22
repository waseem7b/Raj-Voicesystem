# Reference point: 2026-06-18 — pre-copy-promotion

**Reason:** Backup of the deployed *original* inbound-squad prompt modules taken
immediately before promoting the user's `* copy.py` working drafts to become the
originals (the `__init__.py` registry imports the originals; the copies were
orphaned and never deployed). Restore these to undo the promotion.

## What this snapshot contains

The original prompt modules as they were before promotion:

- `shared_persona.py`
- `stage1_inbound_texts.py`
- `stage2_qualify.py`
- `stage3_book.py`

## To restore

```bash
cp docs/reference-points/2026-06-18-pre-copy-promotion/shared_persona.py src/agent/prompts/
cp docs/reference-points/2026-06-18-pre-copy-promotion/stage1_inbound_texts.py src/agent/prompts/
cp docs/reference-points/2026-06-18-pre-copy-promotion/stage2_qualify.py src/agent/prompts/
cp docs/reference-points/2026-06-18-pre-copy-promotion/stage3_book.py src/agent/prompts/
.venv-test/Scripts/python.exe scripts/push_squad.py
```

Verify by pulling the live Vapi assistant config and confirming the system
message contents match these files.
