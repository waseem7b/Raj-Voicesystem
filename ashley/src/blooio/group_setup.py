"""Brand a prospect's group chat and put Ashley's card in it.

Runs after GHL's automation has created the group and opened the conversation.

Rename and icon address the group (`grp_`); the contact card addresses its chat
(`chat_`). v4 keeps those namespaces separate — conflating them is what made
card sharing look impossible under v2.

Every step is attempted and reported independently: a failed icon upload must
not cost the prospect the contact card, and a failed card share must not hide
that the group was renamed on the device.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .group_lookup import wait_for_group_by_name

NAME_TEMPLATE = "Growth System - {business}"
# Shown for a second or two only when forcing a device rename; deliberately a
# plausible name rather than a placeholder, in case a member glimpses it.
TEMP_NAME = "Growth System"
DEFAULT_EMOJI = "\U0001f4c8"  # chart increasing

# The icon is identical for every group, so it ships pre-rendered. Regenerate
# with group_icon.render_group_icon after changing the emoji or gradient — that
# keeps headless Chrome a local build dependency instead of a runtime one.
DEFAULT_ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "group-icon.png"


def setup_prospect_group(
    client,
    business_name: str,
    group: dict[str, Any] | None = None,
    icon_path: Path | None = None,
    share_card: bool = True,
    wait_timeout: float = 240,
    sleep=None,
) -> dict[str, Any]:
    """Rename, icon, then share Ashley's card. Returns a per-step report.

    The group is located by the title GHL created it with, waiting for it to
    appear rather than falling back to an older group holding the same prospect.
    """
    name = NAME_TEMPLATE.format(business=business_name)

    if group is None:
        try:
            group = wait_for_group_by_name(
                client, name, timeout=wait_timeout, sleep=sleep
            )
        except TimeoutError as e:
            return {
                "ok": False,
                "group_id": None,
                "chat_id": None,
                "name": name,
                "error": "group_not_found",
                "detail": str(e),
                "steps": [],
            }

    group_id = group["id"]
    chat_id = group.get("chat_id")
    icon = icon_path or DEFAULT_ICON_PATH
    steps: list[dict[str, Any]] = []

    def record(step: str, action) -> None:
        try:
            steps.append({"step": step, "ok": True, "result": action()})
        except Exception as e:  # every step reports; none aborts the rest
            steps.append({"step": step, "ok": False, "error": str(e)})

    def skip(step: str, reason: str) -> None:
        steps.append({"step": step, "ok": False, "error": reason})

    # Renaming to the SAME title GHL created the group with is deliberate:
    # Blooio pushes a group name to GHL only at creation, so any other title
    # would desync the CRM. The rename exists to make the DEVICE apply it.
    #
    # But Blooio only pushes to the device when the value actually CHANGES, and
    # GHL has already written this exact string to Blooio's database — so a
    # straight PATCH is a silent no-op and the iMessage thread keeps its old
    # title. Bounce through a temporary name to force the push.
    # We always bounce, because we FOUND this group by that exact title — so the
    # stored name always already matches and a single PATCH would never push.
    def apply_name():
        temp = TEMP_NAME if name != TEMP_NAME else f"{TEMP_NAME} ."
        client.rename_group(group_id, temp)
        return client.rename_group(group_id, name)

    record("rename", apply_name)

    # v4 rejects an icon upload with 409 unless the chat is linked on a device.
    if group.get("chat_linked"):
        record("icon", lambda: client.set_group_icon(group_id, icon))
    else:
        skip("icon", "chat_not_linked_on_device")

    if share_card:
        if chat_id:
            record("card", lambda: client.share_contact_card(chat_id))
        else:
            skip("card", "no_chat_id")

    return {
        "ok": all(s["ok"] for s in steps),
        "group_id": group_id,
        "chat_id": chat_id,
        "name": name,
        "steps": steps,
    }
