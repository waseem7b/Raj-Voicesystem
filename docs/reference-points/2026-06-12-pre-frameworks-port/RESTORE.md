# Restore point — 2026-06-12, before the frameworks-refactor push

Snapshot of the deployed 3-stage Squad taken immediately BEFORE pushing the
2026-06-12 batch (frameworks.py extraction, shared-persona dedup/conflict
fixes, stage-1 handoff consolidation + 11.1 verify-question close).

Verified at capture time: these deployed prompts were byte-identical to the
prompts composed from git HEAD `3f7d512` — so `git checkout 3f7d512` of
`src/agent/prompts/` reproduces this state exactly.

## Contents

- `assistant-stage{1,2,3}.json` — full Vapi assistant configs (voice,
  transcriber, hooks, tools, prompt)
- `{stage1,stage2,stage3}-system-prompt.md` — extracted system prompts
- `squad.json` — squad config (member order)

## IDs

| Resource | ID |
|---|---|
| Stage 1 — Inbound Texts | 46e47e95-674a-4624-9029-69bb39c4daf0 |
| Stage 2 — Qualify | 7b2666e0-154d-4c85-82f0-07b4c51ad264 |
| Stage 3 — Book | 7600c2b6-d669-4f2e-8387-e371b0c193d4 |
| Squad | bea22dc5-24eb-42ed-ba81-f52cf1986ebf |

## Restore procedure

Fast path (prompts only — what the 06-12 push changed):

```bash
git checkout 3f7d512 -- src/agent/prompts/shared_persona.py \
    src/agent/prompts/stage1_inbound_texts.py \
    src/agent/prompts/stage2_qualify.py src/agent/prompts/stage3_book.py
PYTHONIOENCODING=utf-8 python scripts/push_squad.py
```

(`frameworks.py` can stay — restored stage files don't import it.)

Full config restore (if voice/hooks/tools also drifted): PATCH each
assistant with the relevant fields from its `assistant-*.json` here.

Routing note: the phone number (+1555010011) has no direct squad binding;
the VPS webhook (`/opt/webhooks/voicsito/.env` → `VAPI_SQUAD_ID`) returns
this squad on assistant-request. Squad ID is stable across pushes — no VPS
change needed for prompt rollbacks.
