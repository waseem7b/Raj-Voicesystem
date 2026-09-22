"""Find a prospect's group chat by the title GHL created it with.

v4's group record carries both `chat_id` and `chat_linked`, so a single
`GET /groups` yields everything the branding sequence needs: the group id for
rename and icon, and the chat id for the contact card. Those are separate
namespaces in v4 — passing a `grp_` id to a chat endpoint is what made card
sharing look impossible under v2.
"""

from __future__ import annotations

from typing import Any


def _newest_first(groups: list[dict]) -> list[dict]:
    """Sort by whichever is later: last message or the group's own creation.

    The webhook can arrive before the first message registers, so creation time
    has to count.
    """
    return sorted(
        groups,
        key=lambda g: max(g.get("last_message_at") or 0, g.get("created_at") or 0),
        reverse=True,
    )


def find_group_by_name(client, name: str) -> dict[str, Any] | None:
    """Return the newest group titled exactly `name`, or None.

    Returns the whole record — the caller needs `id` for the group endpoints and
    `chat_id` for the contact card.
    """
    target = (name or "").strip().casefold()
    if not target:
        raise ValueError("group name is empty")

    for group in _newest_first(client.list_groups()):
        if ((group.get("name") or "").strip().casefold()) == target:
            return group
    return None


def wait_for_group_by_name(
    client,
    name: str,
    timeout: float = 240,
    interval: float = 10,
    sleep=None,
) -> dict[str, Any]:
    """Poll until the group GHL is creating shows up.

    Blooio's device-side creation can lag the webhook by minutes, so arriving
    early is normal rather than an error. Raises TimeoutError instead of
    falling back to some other group.
    """
    if sleep is None:
        import time as _time

        sleep = _time.sleep

    waited = 0.0
    while True:
        found = find_group_by_name(client, name)
        if found:
            return found
        if waited >= timeout:
            raise TimeoutError(
                f"no group titled {name!r} appeared within {timeout:.0f}s"
            )
        sleep(interval)
        waited += interval
