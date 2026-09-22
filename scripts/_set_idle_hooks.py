"""One-off: re-attach the idle/silence hooks to the 3 live stage assistants.

Surgical hooks-only PATCH (only the `hooks` field changes). Does NOT touch
prompts/tools/voice — intentionally avoids push_squad.py so uncommitted local
prompt edits are not deployed. The hooks are imported from push_squad.IDLE_HOOKS
so this stays in sync with the source of truth.

Usage: python scripts/_set_idle_hooks.py [--dry-run]
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "config" / ".env")
sys.path.insert(0, str(ROOT / "scripts"))

from push_squad import IDLE_HOOKS  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv[1:]

k = os.environ["VAPI_API_KEY"]
h = {"Authorization": f"Bearer {k}", "Content-Type": "application/json"}
ids = {
    "Stage1": "46e47e95-674a-4624-9029-69bb39c4daf0",
    "Stage2": "7b2666e0-154d-4c85-82f0-07b4c51ad264",
    "Stage3": "7600c2b6-d669-4f2e-8387-e371b0c193d4",
}


def summarize(hooks: list) -> str:
    parts = []
    for hook in hooks:
        opts = hook.get("options", {})
        parts.append(
            f"{hook.get('name')}@{opts.get('timeoutSeconds')}s"
            f"x{opts.get('triggerMaxCount')}/{opts.get('triggerResetMode')}"
        )
    return "; ".join(parts) if parts else "(none)"


for name, i in ids.items():
    if DRY_RUN:
        print(f"{name}: [dry-run] would PATCH hooks -> {summarize(IDLE_HOOKS)}")
        continue
    r = httpx.patch(
        f"https://api.vapi.ai/assistant/{i}", headers=h, json={"hooks": IDLE_HOOKS}, timeout=30
    )
    if r.status_code >= 400:
        print(f"{name}: PATCH FAILED {r.status_code}: {r.text[:300]}")
        continue
    back = httpx.get(f"https://api.vapi.ai/assistant/{i}", headers=h, timeout=20).json().get("hooks") or []
    print(f"{name}: {len(back)} hooks -> {summarize(back)}")
