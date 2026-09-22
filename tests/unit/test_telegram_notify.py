"""telegram_notify must (a) call the right Bot API endpoint with right args,
(b) be a no-op when env vars are missing, (c) never raise."""
from unittest.mock import AsyncMock, patch
import pytest

from src.webhooks import telegram_notify as tg


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    tg._token.cache_clear()
    tg._chat_id.cache_clear()


@pytest.mark.asyncio
async def test_send_message_success_returns_message_id():
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True, "result": {"message_id": 42}}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        mid = await tg.send_message("hello world", parse_mode="Markdown")
    assert mid == 42
    args, kwargs = mp.call_args
    assert "sendMessage" in args[0]
    assert kwargs["data"]["chat_id"] == "-100"
    assert kwargs["data"]["text"] == "hello world"
    assert kwargs["data"]["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_send_message_with_reply_and_silent():
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True, "result": {"message_id": 99}}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        mid = await tg.send_message("reply", reply_to_message_id=42,
                                     disable_notification=True)
    assert mid == 99
    _, kwargs = mp.call_args
    assert kwargs["data"]["reply_to_message_id"] == 42
    assert kwargs["data"]["disable_notification"] == "true"


@pytest.mark.asyncio
async def test_send_message_disabled_when_no_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    tg._token.cache_clear()
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as mp:
        mid = await tg.send_message("x")
    assert mid is None
    mp.assert_not_called()


@pytest.mark.asyncio
async def test_send_message_returns_none_on_http_error():
    fake_response = AsyncMock()
    fake_response.status_code = 400
    fake_response.text = "bad request"
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        mid = await tg.send_message("x")
    assert mid is None


@pytest.mark.asyncio
async def test_send_message_swallows_exceptions():
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=Exception("network"))):
        mid = await tg.send_message("x")
    assert mid is None  # never raises


@pytest.mark.asyncio
async def test_send_audio_with_reply_and_silent(tmp_path):
    mp3 = tmp_path / "rec.mp3"
    mp3.write_bytes(b"\xff\xfbfakebytes")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True, "result": {"message_id": 7}}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        mid = await tg.send_audio(mp3, caption="cap",
                                  reply_to_message_id=42,
                                  disable_notification=True)
    assert mid == 7
    args, kwargs = mp.call_args
    assert "sendAudio" in args[0]
    assert "files" in kwargs
    assert kwargs["data"]["reply_to_message_id"] == 42
    assert kwargs["data"]["disable_notification"] == "true"


@pytest.mark.asyncio
async def test_send_document_with_reply_and_silent(tmp_path):
    f = tmp_path / "transcript.txt"
    f.write_text("hello")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = lambda: {"ok": True, "result": {"message_id": 8}}
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)) as mp:
        mid = await tg.send_document(f, caption="transcript",
                                     reply_to_message_id=42,
                                     disable_notification=True)
    assert mid == 8
    args, kwargs = mp.call_args
    assert "sendDocument" in args[0]
    assert kwargs["data"]["reply_to_message_id"] == 42
