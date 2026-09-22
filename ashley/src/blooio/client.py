"""Blooio v4 API client.

Spec: https://api.blooio.com/v4/openapi.json  (info.version 4.0.0-beta)

Why v4 rather than the more prominently documented v2: v4 exposes an
org-scoped `channels` resource that does not consult the per-number
ownership allowlist. With our number assigned to the GoHighLevel integration,
v2 refuses nearly everything (503 "no active devices", 403 "not accessible
with this API key") while v4's org-scoped endpoints — chats, groups, messages,
contacts, events — all work with the same key.

Three resolvers exist, and only one of them is open to us right now:

  org/binding-scoped   chats, groups, messages, contacts, events   WORKS
  number-ownership     /me/numbers/*  (contact card CONTENT)       403
  device-passthrough   chat background, Find My                    502/404

So Ashley's card can be SHARED (chat-scoped) but not EDITED (number-scoped)
while GHL owns the number. Editing it needs the number reassigned in the Blooio
dashboard — a one-time manual step, not a per-prospect concern.

Conventions (from the spec):
  lists  {"data": [...], "has_more": bool, "next_cursor": str|null}
  items  {"data": {...}}          sends return the message at the TOP level
  errors {"error": {"code", "message", "details"}}
  ids    ch_ (channel) ct_ (contact) cid_ (identity) chat_ msg_ grp_ wh_ evt_
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.blooio.com/v4"


class BlooioError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Blooio API error {status_code}: {body}")


@dataclass
class BlooioClient:
    api_key: str
    timeout: float = 30.0
    base_url: str = field(default=BASE_URL)

    # -- plumbing ------------------------------------------------------------

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
        files: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        headers = self._auth()
        if files is None:
            headers["Content-Type"] = "application/json"
        resp = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            json=json,
            params=params,
            files=files,
            timeout=timeout or self.timeout,
        )
        if resp.status_code >= 400:
            raise BlooioError(resp.status_code, resp.text)
        if not resp.content:
            return None
        payload = resp.json()
        # Unwrap the envelope; sends return the object at the top level.
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    @staticmethod
    def _enc(identifier: str) -> str:
        return urllib.parse.quote(identifier, safe="")

    # -- groups (org-scoped) -------------------------------------------------

    def list_groups(self) -> list[dict[str, Any]]:
        """Every group, each carrying `chat_id` and `chat_linked`.

        One call is enough to brand a group and share a card into its chat.
        """
        return self._request("GET", "/groups") or []

    def get_group(self, group_id: str) -> dict[str, Any]:
        return self._request("GET", f"/groups/{group_id}")

    def get_group_members(self, group_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/groups/{group_id}/members") or []

    def rename_group(self, group_id: str, name: str) -> dict[str, Any]:
        return self._request("PATCH", f"/groups/{group_id}", json={"name": name})

    def set_group_icon(self, group_id: str, image_path: str | Path) -> dict[str, Any]:
        """Upload a group icon. Multipart, field name `icon`, max 10 MB.

        409s unless the group's chat is linked on a device — check
        `chat_linked` on the group record first.
        """
        path = Path(image_path)
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return self._request(
            "POST",
            f"/groups/{group_id}/icon",
            files={"icon": (path.name, path.read_bytes(), mime)},
            timeout=90,
        )

    # -- chats (org-scoped) --------------------------------------------------

    def list_chats(self, contact_id: str | None = None) -> list[dict[str, Any]]:
        params = {"contact_id": contact_id} if contact_id else None
        return self._request("GET", "/chats", params=params) or []

    def share_contact_card(self, chat_id: str) -> dict[str, Any]:
        """Share Ashley's card into a chat; it rides the next outgoing message.

        Chat-scoped, so this works while the number belongs to GHL. v4 dropped
        v2's `share_contact: true` send flag — staging is the only route, which
        suits us: GHL sends the message that carries it.
        """
        return self._request(
            "POST", f"/chats/{self._enc(chat_id)}/contact-card", timeout=90
        )

    # -- contacts ------------------------------------------------------------

    def find_contacts(self, identifier: str) -> list[dict[str, Any]]:
        return self._request("GET", "/contacts", params={"identifier": identifier}) or []

    # -- messages ------------------------------------------------------------

    def send_message(self, to: str, text: str, from_channel: str | None = None) -> dict[str, Any]:
        """Send via the routed endpoint. `to` may be a phone, email, or grp_ id."""
        body: dict[str, Any] = {"to": to, "text": text}
        if from_channel:
            body["from"] = from_channel
        return self._request("POST", "/messages", json=body, timeout=90)

    def get_message_status(self, chat_id: str, message_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/chats/{self._enc(chat_id)}/messages/{message_id}/status"
        )

    def list_channels(self) -> list[dict[str, Any]]:
        return self._request("GET", "/channels") or []

    # -- account -------------------------------------------------------------

    def get_me(self) -> dict[str, Any]:
        return self._request("GET", "/me")

    # -- number-scoped: BLOCKED while GHL owns the number --------------------
    # Kept because the card content still has to be set once, from a session
    # where the number is assigned to this API key.

    def get_contact_card(self, number: str) -> dict[str, Any]:
        return self._request("GET", f"/me/numbers/{self._enc(number)}/contact-card")

    def update_contact_card(
        self,
        number: str,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar: str | None = None,
        sharing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update the card. `avatar` is a public image URL or a data URI.

        Send the whole card, not one field: partial writes persist in Blooio's
        database but never reach the device (observed 2026-08-05 on v2).
        """
        body: dict[str, Any] = {}
        if first_name is not None:
            body["first_name"] = first_name
        if last_name is not None:
            body["last_name"] = last_name
        if avatar is not None:
            body["avatar"] = avatar
        if sharing is not None:
            body["sharing"] = sharing
        return self._request(
            "PUT", f"/me/numbers/{self._enc(number)}/contact-card", json=body
        )

    def upload_contact_card_avatar(self, number: str, image_path: str | Path) -> dict[str, Any]:
        """Multipart avatar upload, field `avatar`, max 10 MB."""
        path = Path(image_path)
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return self._request(
            "POST",
            f"/me/numbers/{self._enc(number)}/contact-card/avatar",
            files={"avatar": (path.name, path.read_bytes(), mime)},
            timeout=90,
        )

    # -- webhooks ------------------------------------------------------------

    def list_webhooks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/webhooks") or []

    def create_webhook(self, url: str) -> dict[str, Any]:
        """Register an inbound webhook. The signing secret is returned ONCE.

        v4 retired per-event filtering — every subscription receives every
        event. v2's create endpoint now returns 410, so this is the only route.
        """
        return self._request("POST", "/webhooks", json={"url": url})
