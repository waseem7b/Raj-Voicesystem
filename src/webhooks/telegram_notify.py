"""Telegram Bot HTTP API wrapper. All functions never raise — they log and
return False on failure. No-op when env vars are missing.

Read .claude/skills/live-system-testing/SKILL.md if you're considering
running these against the live bot from a test. Don't post spam to the
operator group.
"""
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 10.0


@lru_cache(maxsize=1)
def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


@lru_cache(maxsize=1)
def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def _base_url() -> Optional[str]:
    tok = _token()
    if not tok or not _chat_id():
        return None
    return f"https://api.telegram.org/bot{tok}"


def _parse_message_id(r) -> Optional[int]:
    """Extract message_id from a Telegram Bot API response, or None on failure."""
    try:
        body = r.json()
    except Exception:
        return None
    if not body.get("ok"):
        return None
    result = body.get("result") or {}
    if isinstance(result, list) and result:
        # sendMediaGroup returns a list — use the first message_id
        result = result[0]
    mid = result.get("message_id") if isinstance(result, dict) else None
    return mid if isinstance(mid, int) else None


async def send_message(
    text: str,
    *,
    parse_mode: Optional[str] = "Markdown",
    reply_to_message_id: Optional[int] = None,
    disable_notification: bool = False,
) -> Optional[int]:
    """Send a text message. Returns the new message_id on success, None on
    failure. (Truthy on success — callers using `if await send_message(...)` keep
    working.)"""
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return None
    data = {"chat_id": _chat_id(), "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if disable_notification:
        data["disable_notification"] = "true"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{base}/sendMessage", data=data)
    except Exception as e:
        log.error("telegram sendMessage crashed: %s", e)
        return None
    if r.status_code != 200:
        log.error("telegram sendMessage HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return None
    return _parse_message_id(r)


async def send_audio(
    path: Path,
    *,
    caption: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    disable_notification: bool = False,
    filename: Optional[str] = None,
) -> Optional[int]:
    """Send an MP3 audio file. `filename` overrides the displayed name in
    Telegram (defaults to the local path's basename)."""
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return None
    if not path.exists():
        log.error("send_audio: file does not exist: %s", path)
        return None
    data = {"chat_id": _chat_id()}
    if caption:
        data["caption"] = caption
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if disable_notification:
        data["disable_notification"] = "true"
    display_name = filename or path.name
    try:
        with path.open("rb") as fh:
            files = {"audio": (display_name, fh, "audio/mpeg")}
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{base}/sendAudio", data=data, files=files)
    except Exception as e:
        log.error("telegram sendAudio crashed: %s", e)
        return None
    if r.status_code != 200:
        log.error("telegram sendAudio HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return None
    return _parse_message_id(r)


async def send_document(
    path: Path,
    *,
    caption: Optional[str] = None,
    reply_to_message_id: Optional[int] = None,
    disable_notification: bool = False,
) -> Optional[int]:
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return None
    if not path.exists():
        log.error("send_document: file does not exist: %s", path)
        return None
    data = {"chat_id": _chat_id()}
    if caption:
        data["caption"] = caption
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if disable_notification:
        data["disable_notification"] = "true"
    try:
        with path.open("rb") as fh:
            files = {"document": (path.name, fh, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{base}/sendDocument", data=data, files=files)
    except Exception as e:
        log.error("telegram sendDocument crashed: %s", e)
        return None
    if r.status_code != 200:
        log.error("telegram sendDocument HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return None
    return _parse_message_id(r)
