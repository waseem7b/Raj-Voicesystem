"""The sequence run once GHL has created a prospect's group.

Rename and icon address the group (`grp_`); the contact card addresses the chat
(`chat_`). Conflating those two namespaces is what made card sharing look
impossible under v2.

Each step reports independently — a later failure must not erase what already
succeeded on the device.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blooio.group_setup import setup_prospect_group  # noqa: E402

GROUP = "grp_x"
CHAT = "chat_x"


class FakeClient:
    def __init__(self, groups=None, fail_on=None):
        self.groups = groups if groups is not None else [
            {"id": GROUP, "name": "Growth System - Tree City", "created_at": 10,
             "chat_id": CHAT, "chat_linked": True}
        ]
        self.fail_on = fail_on or set()
        self.calls = []

    def list_groups(self):
        return self.groups

    def _guard(self, step):
        if step in self.fail_on:
            raise RuntimeError(f"{step} exploded")

    def rename_group(self, group_id, name):
        self._guard("rename")
        self.calls.append(("rename", group_id, name))
        return {"id": group_id, "name": name}

    def set_group_icon(self, group_id, image_path):
        self._guard("icon")
        self.calls.append(("icon", group_id, str(image_path)))
        return {"id": group_id, "icon_url": "https://bucket/icon.png"}

    def share_contact_card(self, chat_id):
        self._guard("card")
        self.calls.append(("card", chat_id))
        return {"chat_id": chat_id, "shared": True}


def run(client, **kw):
    kw.setdefault("sleep", lambda s: None)
    return setup_prospect_group(client, "Tree City", **kw)


def test_addresses_the_group_for_branding_and_the_chat_for_the_card():
    client = FakeClient()
    result = run(client)

    assert client.calls == [
        ("rename", GROUP, "Growth System"),           # temp, forces the device push
        ("rename", GROUP, "Growth System - Tree City"),
        ("icon", GROUP, str(Path(__file__).resolve().parents[1] / "assets" / "group-icon.png")),
        ("card", CHAT),
    ]
    assert result["group_id"] == GROUP
    assert result["chat_id"] == CHAT
    assert result["ok"] is True


def test_finds_the_group_by_the_title_ghl_created_it_with():
    client = FakeClient(groups=[
        {"id": "grp_stale", "name": "Growth System - Old Co", "created_at": 900,
         "chat_id": "chat_stale", "chat_linked": True},
        {"id": "grp_right", "name": "Growth System - Tree City", "created_at": 10,
         "chat_id": "chat_right", "chat_linked": True},
    ])
    assert run(client)["group_id"] == "grp_right"


def test_refuses_to_brand_anything_when_the_titled_group_never_appears():
    client = FakeClient(groups=[{"id": "grp_other", "name": "Unrelated"}])
    result = run(client, wait_timeout=0)

    assert result["ok"] is False
    assert result["error"] == "group_not_found"
    assert client.calls == []


def test_skips_the_icon_when_the_group_is_not_linked_on_a_device():
    """v4 rejects an icon upload with 409 unless the chat is device-linked;
    reporting that up front beats a bare API error."""
    client = FakeClient(groups=[
        {"id": GROUP, "name": "Growth System - Tree City", "chat_id": CHAT,
         "chat_linked": False}
    ])
    result = run(client)

    by_step = {s["step"]: s for s in result["steps"]}
    assert by_step["icon"]["ok"] is False
    assert by_step["icon"]["error"] == "chat_not_linked_on_device"
    assert [c[0] for c in client.calls] == ["rename", "rename", "card"]


def test_skips_the_card_when_the_group_has_no_chat_yet():
    client = FakeClient(groups=[
        {"id": GROUP, "name": "Growth System - Tree City", "chat_id": None,
         "chat_linked": False}
    ])
    result = run(client)

    by_step = {s["step"]: s for s in result["steps"]}
    assert by_step["card"]["ok"] is False
    assert by_step["card"]["error"] == "no_chat_id"
    assert [c[0] for c in client.calls] == ["rename", "rename"]


def test_names_the_group_from_the_business_name():
    client = FakeClient()
    run(client)

    renames = [c for c in client.calls if c[0] == "rename"]
    assert renames[-1][2] == "Growth System - Tree City"


def test_renames_to_the_same_title_ghl_used_so_ghl_stays_in_sync():
    """Blooio pushes a group name to GHL only at creation, so renaming to
    anything else would leave GHL showing a stale title. The rename still
    matters: it forces the device to apply the title."""
    client = FakeClient()
    run(client)

    assert client.calls[1] == ("rename", GROUP, "Growth System - Tree City")


def test_reports_every_step_as_ok():
    client = FakeClient()
    result = run(client)

    assert [s["step"] for s in result["steps"]] == ["rename", "icon", "card"]
    assert all(s["ok"] for s in result["steps"])


def test_a_failed_step_does_not_erase_earlier_successes():
    client = FakeClient(fail_on={"card"})
    result = run(client)

    by_step = {s["step"]: s for s in result["steps"]}
    assert by_step["rename"]["ok"] is True
    assert by_step["icon"]["ok"] is True
    assert by_step["card"]["ok"] is False
    assert "exploded" in by_step["card"]["error"]
    assert result["ok"] is False


def test_keeps_going_after_a_mid_sequence_failure():
    """An icon failure must not cost the prospect Ashley's contact card."""
    client = FakeClient(fail_on={"icon"})
    result = run(client)

    assert [c[0] for c in client.calls] == ["rename", "rename", "card"]
    assert {s["step"] for s in result["steps"]} == {"rename", "icon", "card"}


def test_can_skip_the_card_on_request():
    client = FakeClient()
    run(client, share_card=False)

    assert [c[0] for c in client.calls] == ["rename", "rename", "icon"]


def test_accepts_an_override_icon(tmp_path):
    custom = tmp_path / "custom.png"
    custom.write_bytes(b"png")
    client = FakeClient()
    run(client, icon_path=custom)

    icon = next(c for c in client.calls if c[0] == "icon")
    assert icon[2] == str(custom)


def test_forces_a_device_push_when_the_name_is_already_correct():
    """GHL writes the title to Blooio's database, but Blooio only pushes to the
    device on an actual change — so an identical PATCH is a silent no-op and the
    iMessage thread keeps its old title. Bounce through a temp name."""
    client = FakeClient(groups=[
        {"id": GROUP, "name": "Growth System - Tree City", "chat_id": CHAT,
         "chat_linked": True}
    ])
    setup_prospect_group(client, "Tree City", sleep=lambda s: None)

    renames = [r[2] for r in client.calls if r[0] == "rename"]
    assert len(renames) == 2
    assert renames[0] != "Growth System - Tree City"
    assert renames[1] == "Growth System - Tree City"


def test_the_temp_name_never_collides_with_the_target():
    """A bounce only pushes if the two names differ."""
    from blooio.group_setup import NAME_TEMPLATE, TEMP_NAME

    # Contrive a business whose full title equals the temp name.
    business = TEMP_NAME.replace(NAME_TEMPLATE.format(business=""), "")
    title = NAME_TEMPLATE.format(business=business)
    client = FakeClient(groups=[
        {"id": GROUP, "name": title, "chat_id": CHAT, "chat_linked": True}
    ])
    setup_prospect_group(client, business, sleep=lambda s: None)

    renames = [r[2] for r in client.calls if r[0] == "rename"]
    assert renames[0] != renames[-1]
    assert renames[-1] == title
