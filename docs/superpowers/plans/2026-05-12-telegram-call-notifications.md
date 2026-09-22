# Telegram Call Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push Telegram notifications to the Voiscito Group on call start and call end, with the recording attached as audio and the transcript inline or as `.txt`. Save every call (>=30s) to `/opt/webhooks/voicsito/live-recordings/<ts>-<call_id>/` on the VPS.

**Architecture:** Three new pure-Python modules in `src/webhooks/` plus a wiring change in `server.py`'s existing `/vapi/server-events` handler. No new process, no DB. All Telegram errors are swallowed so the webhook never breaks a real call.

**Tech Stack:** FastAPI (existing), httpx (existing), Telegram Bot HTTP API (no SDK), pytest with pytest-asyncio (existing). Reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from env.

**Spec:** `docs/superpowers/specs/2026-05-12-telegram-call-notifications-design.md`

---

## File layout

| Path | Action | Responsibility |
|---|---|---|
| `src/webhooks/call_summary.py` | Create | Pure formatting: classify outcome icon, build "call started" message string, build "ended summary" message string. No I/O. |
| `src/webhooks/telegram_notify.py` | Create | Thin async wrapper around Telegram Bot HTTP API. Three functions: send_message, send_audio, send_document. No-op when env vars missing. Never raises. |
| `src/webhooks/call_archive.py` | Create | File-system archive: mkdir per-call folder, download recordingUrl → recording.mp3, write transcript.txt, write summary.json. Idempotent via `pushed.flag` sentinel. |
| `src/webhooks/server.py` | Modify | Wire `/vapi/server-events` to dispatch `status-update` (in-progress only) and `end-of-call-report` to new handlers `_handle_call_started` and `_handle_end_of_call`. |
| `tests/unit/test_call_summary.py` | Create | Pure-function tests for outcome classification + message formatting. |
| `tests/unit/test_telegram_notify.py` | Create | Mock httpx, verify request shape + disable-on-missing-env-vars. |
| `tests/unit/test_call_archive.py` | Create | tmp_path tests: folder creation, file contents, idempotency, under-30s skip. |
| `config/.env` | Modify (done) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` already added. |
| `/opt/webhooks/voicsito/.env` | Modify | Same two vars on VPS. |

---

### Task 1: `call_summary` module — outcome classifier

**Files:**
- Create: `src/webhooks/call_summary.py`
- Test: `tests/unit/test_call_summary.py`

- [ ] **Step 1: Write the failing tests for `classify_outcome`**

Create `tests/unit/test_call_summary.py`:

```python
"""Pure formatting + outcome classification — no I/O, no env."""
from src.webhooks.call_summary import classify_outcome


def test_classify_outcome_booked():
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": True, "summary": "Booked Tue 2pm"},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}
            ]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert icon == "✅"
    assert label == "BOOKED"
    assert slot_iso == "2026-05-14T10:00:00-04:00"


def test_classify_outcome_disqualified():
    payload = {
        "endedReason": "assistant-ended-call",
        "analysis": {"successEvaluation": False, "summary": "Out of area"},
        "messages": [],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert icon == "🚫"
    assert label == "DISQUALIFIED"
    assert slot_iso is None


def test_classify_outcome_customer_hangup_no_booking():
    payload = {
        "endedReason": "customer-ended-call",
        "analysis": {"successEvaluation": False},
        "messages": [],
    }
    icon, label, slot_iso = classify_outcome(payload)
    assert icon == "⚠️"
    assert label == "NO BOOKING (customer hung up)"


def test_classify_outcome_unknown_falls_through():
    payload = {"endedReason": "silence-timeout", "analysis": {}, "messages": []}
    icon, label, slot_iso = classify_outcome(payload)
    assert icon == "⚠️"
    assert label == "OTHER"
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py -v
```
Expected: `ModuleNotFoundError: No module named 'src.webhooks.call_summary'`

- [ ] **Step 3: Implement `classify_outcome` to pass**

Create `src/webhooks/call_summary.py`:

```python
"""Pure formatting for Telegram messages. No I/O, no env access."""
import json
from typing import Optional


def _booked_slot_iso(payload: dict) -> Optional[str]:
    """If book_video_call was invoked AND returned success, extract the slot ISO."""
    messages = payload.get("messages") or []
    booked_slot = None
    for m in messages:
        if m.get("role") == "tool_calls":
            for tc in m.get("toolCalls") or []:
                fn = tc.get("function") or {}
                if fn.get("name") == "book_video_call":
                    raw = fn.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                        if isinstance(args, dict) and args.get("slot_iso"):
                            booked_slot = args["slot_iso"]
                    except Exception:
                        pass
        elif m.get("role") == "tool_call_result" and m.get("name") == "book_video_call":
            result = m.get("result") or ""
            if "Booked successfully" in result and booked_slot:
                return booked_slot
    return None


def classify_outcome(payload: dict) -> tuple[str, str, Optional[str]]:
    """Return (icon, label, booked_slot_iso_or_None)."""
    analysis = payload.get("analysis") or {}
    success = analysis.get("successEvaluation")
    if isinstance(success, str):
        success = success.lower() == "true"
    ended_reason = payload.get("endedReason", "")

    slot_iso = _booked_slot_iso(payload)
    if success is True and slot_iso:
        return ("✅", "BOOKED", slot_iso)
    if ended_reason == "assistant-ended-call" and success is False:
        return ("🚫", "DISQUALIFIED", None)
    if ended_reason == "customer-ended-call" and success is False:
        return ("⚠️", "NO BOOKING (customer hung up)", None)
    return ("⚠️", "OTHER", None)
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Write failing tests for `format_started_message`**

Append to `tests/unit/test_call_summary.py`:

```python
from src.webhooks.call_summary import format_started_message


def test_format_started_with_crm_hit():
    text = format_started_message(
        phone="+15555550100",
        contact={"first_name": "Mike", "business": "Rock Epoxy",
                 "biz_type": "epoxy flooring", "city": "San Jose", "state": "CA"},
    )
    assert "📞" in text
    assert "+15555550100" in text
    assert "Mike" in text and "Rock Epoxy" in text
    assert "San Jose" in text and "CA" in text


def test_format_started_no_crm():
    text = format_started_message(phone="+15555550199", contact=None)
    assert "+15555550199" in text
    assert "no CRM match" in text.lower() or "cold caller" in text.lower()


def test_format_started_missing_phone():
    text = format_started_message(phone="", contact=None)
    assert "unknown" in text.lower() or "no phone" in text.lower()
```

- [ ] **Step 6: Run the new tests to confirm they fail**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py -v -k started
```
Expected: ImportError on `format_started_message`.

- [ ] **Step 7: Implement `format_started_message`**

Append to `src/webhooks/call_summary.py`:

```python
def format_started_message(phone: str, contact: Optional[dict]) -> str:
    """Build the '📞 Call started' Telegram message text. Markdown."""
    phone_line = phone or "(unknown number)"
    lines = ["📞 *Call started*", phone_line]
    if not contact or not isinstance(contact, dict):
        lines.append("_(no CRM match — cold caller)_")
        return "\n".join(lines)
    first = (contact.get("first_name") or "").strip()
    biz = (contact.get("business") or "").strip()
    biz_type = (contact.get("biz_type") or "").strip()
    city = (contact.get("city") or "").strip()
    state = (contact.get("state") or "").strip()
    name_line = " — ".join(p for p in [first, biz] if p) or "(unnamed contact)"
    lines.append(name_line)
    detail = ", ".join(p for p in [biz_type, ", ".join(x for x in [city, state] if x)] if p)
    if detail:
        lines.append(f"_{detail}_")
    return "\n".join(lines)
```

- [ ] **Step 8: Run all call_summary tests to confirm they pass**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py -v
```
Expected: 7 passed.

- [ ] **Step 9: Write failing tests for `format_ended_summary`**

Append:

```python
from src.webhooks.call_summary import format_ended_summary


def test_format_ended_booked_full():
    payload = {
        "endedReason": "assistant-ended-call",
        "startedAt": "2026-05-12T14:30:00Z",
        "endedAt": "2026-05-12T14:34:22Z",
        "cost": 0.43,
        "analysis": {"successEvaluation": True, "summary": "Booked Tue 2pm"},
        "messages": [
            {"role": "tool_calls", "toolCalls": [
                {"function": {"name": "book_video_call",
                              "arguments": '{"slot_iso":"2026-05-14T10:00:00-04:00"}'}}
            ]},
            {"role": "tool_call_result", "name": "book_video_call",
             "result": "Booked successfully for 2026-05-14T10:00:00-04:00."},
        ],
        "call": {"customer": {"number": "+15555550100"}},
    }
    text = format_ended_summary(
        payload=payload,
        contact={"first_name": "Mike", "business": "Rock Epoxy"},
        folder_name="20260512-143000-019e1a0a",
    )
    assert "✅" in text and "BOOKED" in text
    assert "4m 22s" in text
    assert "+15555550100" in text
    assert "Mike" in text and "Rock Epoxy" in text
    assert "$0.43" in text
    assert "20260512-143000-019e1a0a" in text


def test_format_ended_duration_seconds_only():
    payload = {
        "startedAt": "2026-05-12T14:30:00Z", "endedAt": "2026-05-12T14:30:42Z",
        "endedReason": "customer-ended-call",
        "analysis": {"successEvaluation": False},
        "messages": [], "cost": 0.02,
        "call": {"customer": {"number": "+15555550100"}},
    }
    text = format_ended_summary(payload=payload, contact=None,
                                folder_name="20260512-143000-zzz")
    assert "42s" in text and "m " not in text.split("·")[1]
```

- [ ] **Step 10: Run to confirm they fail**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py::test_format_ended_booked_full -v
```
Expected: ImportError on `format_ended_summary`.

- [ ] **Step 11: Implement `format_ended_summary` + `duration_label`**

Append:

```python
from datetime import datetime


def duration_label(started_at: str, ended_at: str) -> str:
    """ '4m 22s' or '42s'. Empty string if either timestamp is malformed."""
    try:
        s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
        secs = max(0, int((e - s).total_seconds()))
    except Exception:
        return ""
    if secs < 60:
        return f"{secs}s"
    return f"{secs // 60}m {secs % 60}s"


def _fmt_slot_human(slot_iso: str) -> str:
    """ '2026-05-14T10:00:00-04:00' -> 'Wed May 14 · 10:00am'."""
    try:
        dt = datetime.fromisoformat(slot_iso)
        hour = dt.strftime("%-I:%M%p").lower()
        return dt.strftime(f"%a %b %-d · {hour}")
    except Exception:
        return slot_iso


def format_ended_summary(payload: dict, contact: Optional[dict],
                         folder_name: str) -> str:
    icon, label, slot_iso = classify_outcome(payload)
    dur = duration_label(payload.get("startedAt", ""), payload.get("endedAt", ""))
    phone = (payload.get("call") or {}).get("customer", {}).get("number", "") or "(unknown)"

    head_bits = [f"{icon} *{label}*"]
    if dur:
        head_bits.append(dur)
    head_bits.append(phone)
    head = " · ".join(head_bits)

    lines = [head]
    if contact:
        first = (contact.get("first_name") or "").strip()
        biz = (contact.get("business") or "").strip()
        nm = " — ".join(p for p in [first, biz] if p)
        if nm:
            lines.append(nm)
    if slot_iso:
        lines.append(f"🗓 {_fmt_slot_human(slot_iso)}")
    cost = payload.get("cost")
    if isinstance(cost, (int, float)):
        lines.append(f"💰 ${cost:.2f}")
    lines.append(f"🆔 `{folder_name}`")
    summary_text = (payload.get("analysis") or {}).get("summary") or ""
    if summary_text and label == "OTHER":
        lines.append(f"\n_{summary_text}_")
    return "\n".join(lines)
```

- [ ] **Step 12: Run all call_summary tests**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_summary.py -v
```
Expected: 9 passed.

- [ ] **Step 13: Commit**

```bash
git add src/webhooks/call_summary.py tests/unit/test_call_summary.py
git commit -m "feat(webhooks): add call_summary module for Telegram message formatting"
```

(If repo isn't a git repo per CLAUDE.md — skip this commit step. Otherwise commit.)

---

### Task 2: `telegram_notify` module

**Files:**
- Create: `src/webhooks/telegram_notify.py`
- Test: `tests/unit/test_telegram_notify.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_telegram_notify.py`:

```python
"""telegram_notify must (a) call the right Bot API endpoint with right args,
(b) be a no-op when env vars are missing, (c) never raise."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from src.webhooks import telegram_notify as tg


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    # Reset cached env reads
    tg._token.cache_clear()
    tg._chat_id.cache_clear()


@pytest.mark.asyncio
async def test_send_message_success():
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True, "result": {"message_id": 1}}
    fake_response.raise_for_status = lambda: None
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        ok = await tg.send_message("hello world", parse_mode="Markdown")
    assert ok is True
    args, kwargs = mp.call_args
    assert "sendMessage" in args[0]
    assert kwargs["data"]["chat_id"] == "-100"
    assert kwargs["data"]["text"] == "hello world"
    assert kwargs["data"]["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_send_message_disabled_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    tg._token.cache_clear()
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as mp:
        ok = await tg.send_message("x")
    assert ok is False
    mp.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_returns_false_on_http_error():
    fake_response = AsyncMock()
    fake_response.status_code = 400
    fake_response.text = "bad request"
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        ok = await tg.send_message("x")
    assert ok is False


@pytest.mark.asyncio
async def test_send_message_swallows_exceptions():
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("network"))):
        ok = await tg.send_message("x")
    assert ok is False  # never raises


@pytest.mark.asyncio
async def test_send_audio_uses_multipart(tmp_path):
    mp3 = tmp_path / "rec.mp3"
    mp3.write_bytes(b"\xff\xfbfakebytes")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        ok = await tg.send_audio(mp3, caption="+15555550100 · 4m 22s")
    assert ok is True
    args, kwargs = mp.call_args
    assert "sendAudio" in args[0]
    assert "files" in kwargs


@pytest.mark.asyncio
async def test_send_document_uses_multipart(tmp_path):
    f = tmp_path / "transcript.txt"
    f.write_text("hello")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        ok = await tg.send_document(f, caption="transcript")
    assert ok is True
    args, kwargs = mp.call_args
    assert "sendDocument" in args[0]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_telegram_notify.py -v
```
Expected: ImportError on `src.webhooks.telegram_notify`.

- [ ] **Step 3: Implement the module**

Create `src/webhooks/telegram_notify.py`:

```python
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


async def send_message(text: str, *, parse_mode: Optional[str] = "Markdown") -> bool:
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return False
    data = {"chat_id": _chat_id(), "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{base}/sendMessage", data=data)
    except Exception as e:
        log.error("telegram sendMessage crashed: %s", e)
        return False
    if r.status_code != 200:
        log.error("telegram sendMessage HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return False
    return True


async def send_audio(path: Path, *, caption: Optional[str] = None) -> bool:
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return False
    if not path.exists():
        log.error("send_audio: file does not exist: %s", path)
        return False
    data = {"chat_id": _chat_id()}
    if caption:
        data["caption"] = caption
    try:
        with path.open("rb") as fh:
            files = {"audio": (path.name, fh, "audio/mpeg")}
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(f"{base}/sendAudio", data=data, files=files)
    except Exception as e:
        log.error("telegram sendAudio crashed: %s", e)
        return False
    if r.status_code != 200:
        log.error("telegram sendAudio HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return False
    return True


async def send_document(path: Path, *, caption: Optional[str] = None) -> bool:
    base = _base_url()
    if not base:
        log.warning("telegram disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return False
    if not path.exists():
        log.error("send_document: file does not exist: %s", path)
        return False
    data = {"chat_id": _chat_id()}
    if caption:
        data["caption"] = caption
    try:
        with path.open("rb") as fh:
            files = {"document": (path.name, fh, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{base}/sendDocument", data=data, files=files)
    except Exception as e:
        log.error("telegram sendDocument crashed: %s", e)
        return False
    if r.status_code != 200:
        log.error("telegram sendDocument HTTP %s: %s", r.status_code, getattr(r, "text", "")[:200])
        return False
    return True
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_telegram_notify.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/webhooks/telegram_notify.py tests/unit/test_telegram_notify.py
git commit -m "feat(webhooks): add telegram_notify wrapper with disable-on-missing-env"
```

---

### Task 3: `call_archive` module

**Files:**
- Create: `src/webhooks/call_archive.py`
- Test: `tests/unit/test_call_archive.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_call_archive.py`:

```python
"""Filesystem archive: creates per-call folder, writes recording.mp3 / transcript.txt /
summary.json. Idempotent. Skips when call duration is under 30s."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.webhooks import call_archive


def _payload(duration_sec=120, with_recording=True, transcript="User: hi\nAI: hey"):
    return {
        "call": {"id": "019e1a0a-1acd-71-test-aaaaaaaa",
                 "customer": {"number": "+15555550100"}},
        "startedAt": "2026-05-12T14:30:00Z",
        "endedAt": f"2026-05-12T14:{30 + duration_sec // 60:02d}:{duration_sec % 60:02d}Z",
        "endedReason": "assistant-ended-call",
        "recordingUrl": "https://storage.vapi.ai/fake/recording.mp3" if with_recording else None,
        "transcript": transcript,
        "analysis": {"summary": "Test", "successEvaluation": True},
        "cost": 0.42,
        "messages": [],
    }


@pytest.mark.asyncio
async def test_archive_under_30s_returns_none(tmp_path):
    res = await call_archive.archive_call(_payload(duration_sec=15), base_dir=tmp_path)
    assert res is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_archive_creates_folder_and_files(tmp_path):
    audio_bytes = b"\xff\xfbFAKEMP3"
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.content = audio_bytes
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)):
        folder = await call_archive.archive_call(_payload(duration_sec=120), base_dir=tmp_path)
    assert folder is not None
    assert folder.exists()
    assert folder.name.startswith("20260512-143000-")
    assert (folder / "recording.mp3").read_bytes() == audio_bytes
    assert "User: hi" in (folder / "transcript.txt").read_text()
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["phone"] == "+15555550100"
    assert summary["duration_sec"] == 120
    assert summary["outcome_label"] == "BOOKED" or summary["outcome_label"] == "OTHER"
    assert summary["cost_usd"] == 0.42


@pytest.mark.asyncio
async def test_archive_idempotent(tmp_path):
    audio_bytes = b"\xff\xfbFAKEMP3"
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.content = audio_bytes
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=fake_response)) as mg:
        folder1 = await call_archive.archive_call(_payload(120), base_dir=tmp_path)
        # Simulate prior push completed
        (folder1 / "pushed.flag").write_text("ok")
        folder2 = await call_archive.archive_call(_payload(120), base_dir=tmp_path)
    assert folder1 == folder2
    # Second call must NOT re-download recording
    assert mg.call_count == 1


@pytest.mark.asyncio
async def test_archive_skips_recording_download_when_url_missing(tmp_path):
    with patch("httpx.AsyncClient.get", new=AsyncMock()) as mg:
        folder = await call_archive.archive_call(_payload(120, with_recording=False),
                                                  base_dir=tmp_path)
    assert folder is not None
    assert not (folder / "recording.mp3").exists()
    mg.assert_not_called()
```

- [ ] **Step 2: Run to confirm failing**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_archive.py -v
```
Expected: ImportError on `call_archive`.

- [ ] **Step 3: Implement `archive_call`**

Create `src/webhooks/call_archive.py`:

```python
"""Per-call filesystem archive under /opt/webhooks/voicsito/live-recordings/.

The folder name is <UTC YYYYMMDD-HHMMSS>-<call_id[:8]> using the call's
startedAt timestamp (so chronological sort works even if end-of-call events
arrive out of order).
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from .call_summary import classify_outcome, duration_label

log = logging.getLogger(__name__)

DEFAULT_BASE_DIR = Path(
    os.environ.get("LIVE_RECORDINGS_DIR", "/opt/webhooks/voicsito/live-recordings")
)


def _duration_sec(payload: dict) -> int:
    try:
        s = datetime.fromisoformat(payload["startedAt"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(payload["endedAt"].replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds()))
    except Exception:
        return 0


def _folder_name(payload: dict) -> str:
    try:
        s = datetime.fromisoformat(payload["startedAt"].replace("Z", "+00:00"))
        ts = s.strftime("%Y%m%d-%H%M%S")
    except Exception:
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    cid = ((payload.get("call") or {}).get("id") or "unknown")[:8]
    return f"{ts}-{cid}"


async def _download_recording(url: str, dest: Path) -> bool:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(url)
        if r.status_code != 200:
            log.error("recording download HTTP %s for %s", r.status_code, url)
            return False
        dest.write_bytes(r.content)
        return True
    except Exception as e:
        log.error("recording download crashed for %s: %s", url, e)
        return False


async def archive_call(payload: dict, *, base_dir: Optional[Path] = None) -> Optional[Path]:
    """Write recording / transcript / summary for one ended call.
    Returns the folder path on success, or None if duration < 30s (skip)."""
    duration = _duration_sec(payload)
    if duration < 30:
        log.info("archive_call skipped — duration %ds under 30s", duration)
        return None

    base = base_dir or DEFAULT_BASE_DIR
    folder = base / _folder_name(payload)
    folder.mkdir(parents=True, exist_ok=True)

    # Transcript (overwrite ok — Vapi sends the same content)
    transcript = payload.get("transcript") or ""
    (folder / "transcript.txt").write_text(transcript)

    # Recording — only download if missing (idempotency)
    rec_url = payload.get("recordingUrl")
    rec_path = folder / "recording.mp3"
    if rec_url and not rec_path.exists():
        await _download_recording(rec_url, rec_path)

    # Summary
    icon, label, slot_iso = classify_outcome(payload)
    summary = {
        "call_id": (payload.get("call") or {}).get("id"),
        "phone": ((payload.get("call") or {}).get("customer", {}) or {}).get("number", ""),
        "started_at": payload.get("startedAt"),
        "ended_at": payload.get("endedAt"),
        "duration_sec": duration,
        "duration_label": duration_label(
            payload.get("startedAt", ""), payload.get("endedAt", "")
        ),
        "ended_reason": payload.get("endedReason"),
        "outcome_icon": icon,
        "outcome_label": label,
        "booked_slot_iso": slot_iso,
        "summary_text": (payload.get("analysis") or {}).get("summary", ""),
        "success_evaluation": (payload.get("analysis") or {}).get("successEvaluation"),
        "cost_usd": payload.get("cost"),
        "recording_url": rec_url,
        "assistant_id": payload.get("assistantId") or
                        (payload.get("call") or {}).get("assistantId"),
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return folder


def already_pushed(folder: Path) -> bool:
    """True iff a previous run already pushed the Telegram notification for
    this folder. Used to avoid duplicate messages on Vapi event resends."""
    return (folder / "pushed.flag").exists()


def mark_pushed(folder: Path) -> None:
    (folder / "pushed.flag").write_text(datetime.utcnow().isoformat() + "Z")
```

- [ ] **Step 4: Run tests, confirm pass**

```bash
.venv-tests/bin/python -m pytest tests/unit/test_call_archive.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/webhooks/call_archive.py tests/unit/test_call_archive.py
git commit -m "feat(webhooks): add call_archive for per-call VPS folder + summary.json"
```

---

### Task 4: Wire handlers into `server.py`

**Files:**
- Modify: `src/webhooks/server.py` — the `/vapi/server-events` endpoint (around line 851-907)

- [ ] **Step 1: Read the current `vapi_server_events` handler**

```bash
sed -n '851,910p' src/webhooks/server.py
```

Confirm the structure: `if msg_type == "assistant-request"` then a fall-through that just logs `server-events ack type=...`.

- [ ] **Step 2: Add module imports + dedupe set near top of server.py**

In `src/webhooks/server.py`, find the existing imports block (around line 1-30). Add after the existing `from .` imports:

```python
from .telegram_notify import send_message as tg_send_message
from .telegram_notify import send_audio as tg_send_audio
from .telegram_notify import send_document as tg_send_document
from .call_archive import archive_call, already_pushed, mark_pushed
from .call_summary import format_started_message, format_ended_summary
```

Find the existing `_call_cache` declaration (around line 68-69). Below it, add:

```python
# Track call_ids we've already pushed "📞 Call started" for, to avoid duplicate
# pushes if Vapi sends multiple `status-update` events with status=in-progress.
# In-memory only — resets on restart, which is fine (calls last minutes; the
# webhook restart while a call is mid-flight is rare and a duplicate Telegram
# ping is better than missing a notification).
_started_pushed: set[str] = set()
```

- [ ] **Step 3: Add the two handlers**

Below `_handle_assistant_request` (around line 849), add:

```python
async def _handle_call_started(message: dict) -> None:
    """Fired on the first status-update where status == 'in-progress' for a
    given call.id. Idempotent within process lifetime."""
    call = message.get("call") or {}
    call_id = call.get("id") or ""
    if not call_id or call_id in _started_pushed:
        return
    _started_pushed.add(call_id)

    phone = _extract_phone(message)
    contact = None
    if phone:
        try:
            contact = await asyncio.wait_for(
                ghl.lookup_contact_in_texts_crms(phone), timeout=2.5
            )
        except Exception as e:
            log.warning("call-started CRM lookup failed phone=%s err=%s", phone, e)
    text = format_started_message(phone=phone or "", contact=contact)
    await tg_send_message(text, parse_mode="Markdown")


async def _handle_end_of_call(message: dict) -> None:
    """Fired on end-of-call-report. Archives to VPS folder (if >= 30s) and
    pushes Telegram summary + audio + transcript. Idempotent via the
    pushed.flag sentinel in the folder."""
    folder = await archive_call(message)
    if folder is None:
        # Under-30s skip.
        return

    if already_pushed(folder):
        log.info("end-of-call: telegram already pushed for %s — skipping resend",
                 folder.name)
        return

    phone = ((message.get("call") or {}).get("customer", {}) or {}).get("number", "")
    contact = None
    if phone:
        try:
            contact = await asyncio.wait_for(
                ghl.lookup_contact_in_texts_crms(phone), timeout=2.5
            )
        except Exception as e:
            log.warning("end-of-call CRM lookup failed phone=%s err=%s", phone, e)

    summary_text = format_ended_summary(payload=message, contact=contact,
                                        folder_name=folder.name)
    await tg_send_message(summary_text, parse_mode="Markdown")

    rec_path = folder / "recording.mp3"
    if rec_path.exists():
        from .call_summary import duration_label as _dl
        cap = f"{phone} · {_dl(message.get('startedAt',''), message.get('endedAt',''))}"
        await tg_send_audio(rec_path, caption=cap.strip(" ·"))
    else:
        log.warning("end-of-call: recording.mp3 missing for %s", folder.name)

    transcript = message.get("transcript") or ""
    if transcript:
        if len(transcript) <= 3500:
            # Plain text (no code fence) — wraps cleanly on iPhone Telegram.
            await tg_send_message(transcript, parse_mode=None)
        else:
            await tg_send_document(folder / "transcript.txt", caption="transcript")

    mark_pushed(folder)
```

- [ ] **Step 4: Dispatch from the existing endpoint**

In `vapi_server_events` (around line 875-887), replace the `if msg_type == "assistant-request":` block with:

```python
    if msg_type == "assistant-request":
        try:
            response = await _handle_assistant_request(message)
            return JSONResponse(content=response)
        except Exception as e:
            log.exception("assistant-request handler crash err=%s", e)
            return JSONResponse(content={"assistantId": _VOICSITO_ASSISTANT_ID})

    if msg_type == "status-update":
        status = (message.get("status") or "").lower()
        if status == "in-progress":
            try:
                await _handle_call_started(message)
            except Exception:
                log.exception("call-started handler crashed")
        return JSONResponse(content={})

    if msg_type == "end-of-call-report":
        try:
            await _handle_end_of_call(message)
        except Exception:
            log.exception("end-of-call handler crashed")
        return JSONResponse(content={})

    log.info("server-events ack type=%s", msg_type)
    return JSONResponse(content={})
```

(Remove the bottom-of-function ack — it's now after the explicit handlers.)

- [ ] **Step 5: Local sanity check — import the modified server**

```bash
.venv-tests/bin/python -c "from src.webhooks.server import app; print('import ok')"
```
Expected: `import ok` and no exception.

- [ ] **Step 6: Run all unit tests to confirm nothing regressed**

```bash
.venv-tests/bin/python -m pytest tests/unit/ -v
```
Expected: all pass (call_summary, telegram_notify, call_archive — total ~19 tests, plus any pre-existing unit tests).

- [ ] **Step 7: Commit**

```bash
git add src/webhooks/server.py
git commit -m "feat(webhooks): wire call_started + end_of_call handlers to /vapi/server-events"
```

---

### Task 5: Deploy + smoke test against the live VPS

**Files:**
- Modify on VPS: `/opt/webhooks/voicsito/.env`, `/opt/webhooks/voicsito/src/webhooks/*.py`

- [ ] **Step 1: Add the two env vars to the VPS `.env`**

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "grep -q TELEGRAM_BOT_TOKEN /opt/webhooks/voicsito/.env || printf '\nTELEGRAM_BOT_TOKEN=<TELEGRAM_BOT_TOKEN>\nTELEGRAM_CHAT_ID=-1000000000\n' >> /opt/webhooks/voicsito/.env"
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "grep -E '^TELEGRAM_' /opt/webhooks/voicsito/.env | sed 's/=.*/=SET/'"
```
Expected: `TELEGRAM_BOT_TOKEN=SET` and `TELEGRAM_CHAT_ID=SET`.

- [ ] **Step 2: SCP the four code files**

```bash
scp -i ~/.ssh/voicsito_vps \
  src/webhooks/server.py \
  src/webhooks/telegram_notify.py \
  src/webhooks/call_summary.py \
  src/webhooks/call_archive.py \
  root@voicsito-vps-direct:/opt/webhooks/voicsito/src/webhooks/
```

- [ ] **Step 3: Restart the service + verify import + verify active**

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "systemctl restart voicsito-webhooks && sleep 2 && systemctl is-active voicsito-webhooks && curl -s http://127.0.0.1:8081/health"
```
Expected: `active` and `{"ok":true,"service":"voicsito-webhooks"}`.

If service is `failed`, run `journalctl -u voicsito-webhooks -n 50 --no-pager` to see the import error.

- [ ] **Step 4: Synthetic call-started smoke test**

Send a synthetic `status-update` payload to the server-events endpoint:

```bash
curl -s -X POST "https://webhooks.c7a.io/voicsito/vapi/server-events" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "status-update",
      "status": "in-progress",
      "call": {"id": "smoke-test-started-0001", "customer": {"number": "+15550199000"}}
    }
  }' && echo
```
Expected: empty `{}` response (HTTP 200). Within ~2s, Voiscito Group receives:
```
📞 Call started
+15550199000
(no CRM match — cold caller)
```

- [ ] **Step 5: Synthetic end-of-call smoke test — short (under 30s, should NOT push)**

```bash
curl -s -X POST "https://webhooks.c7a.io/voicsito/vapi/server-events" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "call": {"id": "smoke-test-short-0001", "customer": {"number": "+15550199001"}},
      "startedAt": "2026-05-12T14:30:00Z",
      "endedAt": "2026-05-12T14:30:12Z",
      "endedReason": "customer-ended-call",
      "transcript": "AI: hi\nUser: wrong number",
      "analysis": {"successEvaluation": false, "summary": "Short call"},
      "cost": 0.01
    }
  }' && echo
```
Expected: HTTP 200 empty response. **NO Telegram message**. Verify nothing arrived in the group.

- [ ] **Step 6: Synthetic end-of-call smoke test — full (over 30s, should push)**

```bash
curl -s -X POST "https://webhooks.c7a.io/voicsito/vapi/server-events" \
  -H "Content-Type: application/json" \
  -d '{
    "message": {
      "type": "end-of-call-report",
      "call": {"id": "smoke-test-full-0001", "customer": {"number": "+15550199002"}},
      "startedAt": "2026-05-12T14:30:00Z",
      "endedAt": "2026-05-12T14:34:22Z",
      "endedReason": "customer-ended-call",
      "transcript": "AI: Hey Jon here.\nUser: Hi.\nAI: Thanks for the call. How can I help?",
      "analysis": {"successEvaluation": false, "summary": "Test call, no booking"},
      "cost": 0.42
    }
  }' && echo
```
Expected: Telegram group receives the summary message (no audio attachment because there's no recordingUrl), then the transcript inline. VPS folder created.

- [ ] **Step 7: Verify VPS folder for the over-30s test**

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "ls -la /opt/webhooks/voicsito/live-recordings/ | tail -5"
```
Expected: a `20260512-143000-smoke-te/` folder containing `transcript.txt`, `summary.json`, `pushed.flag`. No `recording.mp3` (no URL in payload).

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "cat /opt/webhooks/voicsito/live-recordings/20260512-143000-smoke-te/summary.json"
```
Expected: JSON with phone=`+15550199002`, duration_sec=262, outcome_label, etc.

- [ ] **Step 8: Verify idempotency by replaying the same payload**

Run the same curl from Step 6 again. Expected: HTTP 200, but **NO** new Telegram message (pushed.flag prevents resend).

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "grep -c 'telegram already pushed' /var/log/syslog 2>/dev/null || journalctl -u voicsito-webhooks -n 30 --no-pager | grep 'already pushed'"
```
Expected: at least one matching log line.

- [ ] **Step 9: Clean up the smoke-test folders**

```bash
ssh -i ~/.ssh/voicsito_vps root@voicsito-vps-direct "rm -rf /opt/webhooks/voicsito/live-recordings/20260512-143000-smoke-te"
```

- [ ] **Step 10: Tell the user smoke tests passed and stand by for a real call**

Report: "Telegram notifications wired and live. Smoke tests passed: call-started fires on `status-update:in-progress`, end-of-call with under-30s skips silently, over-30s pushes the summary + transcript + creates the VPS folder, replays are de-duped by `pushed.flag`. Place a real call to the Type-2 number to verify the full path including the actual Vapi recordingUrl download + audio attachment."

---

## Notes for the implementer

- This project's CLAUDE.md treats `config/.env` as gitignored. The `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` lines were appended to it earlier in the brainstorming session — don't re-add them locally.
- Repo state on this branch may not be a git repo (no `.git` per project context). If `git commit` errors out with "not a git repository", skip the commits — the steps still produce valid code.
- Vapi's actual end-of-call-report payload may put `transcript` under a different key (`messages` array vs `transcript` string). The code defensively reads `payload.get("transcript")` — if real calls show empty transcripts, the implementer should run a real call, grab the actual payload from the VPS logs, and update the field extraction. Don't over-engineer this in advance.
- The Telegram Bot API silently rejects messages with unbalanced Markdown. If real-call payloads cause Markdown parse errors (look for `parse_mode` errors in webhook logs), fall back to `parse_mode=None` for the summary line.
- The smoke tests use phone numbers in the `+15550199` test range so they integrate cleanly with the live-system-testing skill's marker convention.
