"""Find the group by the title GHL gave it (Blooio v4).

GHL creates the chat already titled "Growth System - <Company>", which is a far
better handle than the prospect's phone number — that number also appears in
every older group, and branding one of those is what went wrong on 2026-08-06.

v4's group record carries `chat_id` and `chat_linked`, so one call yields both
the group (for rename/icon) and its chat (for the contact card). The lookup
returns the whole record rather than an id for that reason.

GHL's device-side create can lag the webhook by minutes, so arriving before the
group exists is normal and worth waiting out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blooio.group_lookup import find_group_by_name, wait_for_group_by_name  # noqa: E402

TITLE = "Growth System - Cloud 7 Agency"


class FakeClient:
    def __init__(self, groups):
        self._groups = list(groups)
        self.list_calls = 0

    def list_groups(self):
        self.list_calls += 1
        return list(self._groups)

    def add(self, group):
        self._groups.append(group)


def g(gid, name, created_at=0, chat_id=None, chat_linked=True):
    return {
        "id": gid,
        "name": name,
        "created_at": created_at,
        "chat_id": chat_id if chat_id is not None else f"chat_for_{gid}",
        "chat_linked": chat_linked,
    }


def test_returns_the_whole_group_record_not_just_an_id():
    """The card needs chat_id; rename and icon need the group id."""
    client = FakeClient([g("grp_target", TITLE, chat_id="chat_abc")])
    found = find_group_by_name(client, TITLE)

    assert found["id"] == "grp_target"
    assert found["chat_id"] == "chat_abc"
    assert found["chat_linked"] is True


def test_ignores_case_and_surrounding_whitespace():
    client = FakeClient([g("grp_target", "  growth system - cloud 7 agency ")])
    assert find_group_by_name(client, TITLE)["id"] == "grp_target"


def test_does_not_match_a_different_company():
    client = FakeClient([g("grp_other", "Growth System - Tree City")])
    assert find_group_by_name(client, TITLE) is None


def test_does_not_match_on_a_prefix():
    """An old group named just 'Growth System' must not be mistaken for this one."""
    client = FakeClient([g("grp_old", "Growth System")])
    assert find_group_by_name(client, TITLE) is None


def test_picks_the_newest_when_the_title_repeats():
    client = FakeClient([g("grp_old", TITLE, 100), g("grp_new", TITLE, 900)])
    assert find_group_by_name(client, TITLE)["id"] == "grp_new"


def test_tolerates_a_group_with_no_name():
    client = FakeClient([{"id": "grp_unnamed", "name": None}, g("grp_target", TITLE)])
    assert find_group_by_name(client, TITLE)["id"] == "grp_target"


def test_waits_for_a_group_ghl_has_not_finished_creating():
    client = FakeClient([g("grp_old", "Something Else")])
    ticks = {"n": 0}

    def sleeper(_seconds):
        ticks["n"] += 1
        if ticks["n"] == 3:
            client.add(g("grp_new", TITLE, 900))

    found = wait_for_group_by_name(client, TITLE, timeout=120, interval=10, sleep=sleeper)
    assert found["id"] == "grp_new"


def test_gives_up_with_a_clear_error_rather_than_branding_something_else():
    client = FakeClient([g("grp_old", "Something Else")])
    waited = {"t": 0}

    with pytest.raises(TimeoutError, match="Growth System - Cloud 7 Agency"):
        wait_for_group_by_name(
            client, TITLE, timeout=30, interval=10,
            sleep=lambda s: waited.__setitem__("t", waited["t"] + s),
        )
    assert waited["t"] >= 30


def test_returns_immediately_when_the_group_already_exists():
    client = FakeClient([g("grp_target", TITLE)])
    found = wait_for_group_by_name(
        client, TITLE, sleep=lambda s: pytest.fail("should not have slept")
    )
    assert found["id"] == "grp_target"
    assert client.list_calls == 1
