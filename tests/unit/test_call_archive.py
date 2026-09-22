"""Filesystem archive: creates per-call folder, writes recording.mp3 / transcript.txt /
summary.json. Idempotent. Skips when call duration is under 30s."""
import json
import pytest
from unittest.mock import AsyncMock, patch

from src.webhooks import call_archive


def _payload(duration_sec=120, with_recording=True, transcript="User: hi\nAI: hey"):
    # Build a valid endedAt by adding duration_sec minutes/seconds to 14:30:00
    m, s = divmod(duration_sec, 60)
    end_h = 14 + (m + 30) // 60
    end_m = (m + 30) % 60
    return {
        "call": {"id": "019e1a0a-1acd-71-test-aaaaaaaa",
                 "customer": {"number": "+15555550100"}},
        "startedAt": "2026-05-12T14:30:00Z",
        "endedAt": f"2026-05-12T{end_h:02d}:{end_m:02d}:{s:02d}Z",
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


def _mock_download(audio_bytes: bytes = b"\xff\xfbFAKEMP3"):
    """Return an AsyncMock that emulates _download_recording: writes bytes
    to dest and returns True. Use with `patch.object(call_archive,
    '_download_recording', new=_mock_download())`."""
    async def fake(url: str, dest):
        dest.write_bytes(audio_bytes)
        return True
    m = AsyncMock(side_effect=fake)
    return m


@pytest.mark.asyncio
async def test_archive_creates_month_nested_folder(tmp_path):
    """Folder is nested under MM-YYYY and named `DD-MM-YY HH-MM - <biz>`."""
    audio_bytes = b"\xff\xfbFAKEMP3"
    with patch.object(call_archive, "_download_recording", new=_mock_download(audio_bytes)):
        folder = await call_archive.archive_call(
            _payload(duration_sec=120),
            business_label="Rock Epoxy",
            base_dir=tmp_path,
        )
    assert folder is not None
    assert folder.exists()
    # Month subfolder (Eastern: May 12 2026 still in May → 05-2026)
    assert folder.parent.name == "05-2026"
    # Folder name format DD-MM-YY HH-MM - Business (Eastern conversion).
    # startedAt=14:30 UTC = 10:30 EDT on 2026-05-12.
    assert folder.name == "12-05-26 10-30 - Rock Epoxy"
    assert (folder / "recording.mp3").read_bytes() == audio_bytes
    assert "User: hi" in (folder / "transcript.txt").read_text()
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["phone"] == "+15555550100"
    assert summary["duration_sec"] == 120
    assert summary["outcome_label"] in ("BOOKED", "NOT INTERESTED", "OTHER")
    assert summary["cost_usd"] == 0.42


@pytest.mark.asyncio
async def test_archive_default_business_label_is_unknown(tmp_path):
    with patch.object(call_archive, "_download_recording", new=_mock_download()):
        folder = await call_archive.archive_call(_payload(120), base_dir=tmp_path)
    assert folder.name.endswith(" - Unknown")


@pytest.mark.asyncio
async def test_archive_idempotent_same_call(tmp_path):
    """Resending the same call.id reuses the folder and does NOT re-download."""
    mg = _mock_download()
    with patch.object(call_archive, "_download_recording", new=mg):
        folder1 = await call_archive.archive_call(
            _payload(120), business_label="Rock Epoxy", base_dir=tmp_path,
        )
        (folder1 / "pushed.flag").write_text("ok")
        folder2 = await call_archive.archive_call(
            _payload(120), business_label="Rock Epoxy", base_dir=tmp_path,
        )
    assert folder1 == folder2
    assert mg.call_count == 1  # recording not re-downloaded


@pytest.mark.asyncio
async def test_archive_collision_different_call_gets_ss_suffix(tmp_path):
    """Two different calls in the same minute under same business name get
    distinct folders — the second gets an SS suffix appended."""
    p1 = _payload(120)
    p2 = _payload(120)
    p2["call"]["id"] = "different-call-id-bbbbbbbb"

    with patch.object(call_archive, "_download_recording", new=_mock_download()):
        folder1 = await call_archive.archive_call(
            p1, business_label="Rock Epoxy", base_dir=tmp_path,
        )
        folder2 = await call_archive.archive_call(
            p2, business_label="Rock Epoxy", base_dir=tmp_path,
        )
    assert folder1 != folder2
    assert folder1.name == "12-05-26 10-30 - Rock Epoxy"
    # Second call adds the seconds suffix
    assert folder2.name.startswith("12-05-26 10-30 - Rock Epoxy ")
    assert folder2.name.endswith("s")  # e.g. "00s"


@pytest.mark.asyncio
async def test_archive_skips_recording_download_when_url_missing(tmp_path):
    mg = _mock_download()
    with patch.object(call_archive, "_download_recording", new=mg):
        folder = await call_archive.archive_call(
            _payload(120, with_recording=False),
            business_label="Test Biz",
            base_dir=tmp_path,
        )
    assert folder is not None
    assert not (folder / "recording.mp3").exists()
    mg.assert_not_called()


# ─────────── build_transcript / _ts_prefix ───────────

# Real Vapi `time` value from a captured call: epoch MILLISECONDS.
# 1778272773774 ms → 2026-05-08 20:39:33 UTC → 16:39:33 EDT → "May 8 4:39:33pm".
_VAPI_MS = 1778272773774


def test_build_transcript_ms_time_produces_local_timestamps():
    """Regression: Vapi sends `time` as epoch ms. The old code passed it to
    datetime.fromtimestamp() (which wants seconds) and raised, killing the
    whole transcript. Each turn must now carry a 'Mon D h:mm:sspm' prefix in
    the display timezone (Eastern), labeled AI / User."""
    payload = {
        "messages": [
            {"role": "system", "time": _VAPI_MS, "message": "you are Jon"},
            {"role": "bot", "time": _VAPI_MS, "message": "Hey, this is Jon."},
            {"role": "user", "time": _VAPI_MS + 4000, "message": "Hi there."},
        ],
        "transcript": "FALLBACK SHOULD NOT BE USED",
    }
    out = call_archive.build_transcript(payload)
    assert out == ("May 8 4:39:33pm AI: Hey, this is Jon.\n"
                   "May 8 4:39:37pm User: Hi there.")
    # system turns are excluded; fallback string is untouched
    assert "you are Jon" not in out
    assert "FALLBACK" not in out


def test_build_transcript_missing_time_degrades_to_unprefixed_line():
    """A turn with no usable `time` degrades to a bare 'Speaker: text' line
    rather than crashing the transcript."""
    payload = {"messages": [
        {"role": "bot", "message": "Hello."},
        {"role": "user", "time": "not-a-number", "message": "Hi."},
    ]}
    assert call_archive.build_transcript(payload) == "AI: Hello.\nUser: Hi."


def test_build_transcript_falls_back_to_plain_transcript_when_no_turns():
    """Empty messages[] (test fixtures / older payloads) → plain transcript."""
    payload = {"messages": [], "transcript": "User: hi\nAI: hey"}
    assert call_archive.build_transcript(payload) == "User: hi\nAI: hey"


def test_ts_prefix_accepts_both_ms_and_seconds():
    assert call_archive._ts_prefix(_VAPI_MS) == "May 8 4:39:33pm "
    assert call_archive._ts_prefix(_VAPI_MS / 1000) == "May 8 4:39:33pm "  # seconds


def test_ts_prefix_returns_empty_for_unusable_values():
    assert call_archive._ts_prefix(None) == ""
    assert call_archive._ts_prefix("1778272773774") == ""
    assert call_archive._ts_prefix(True) == ""  # bool is an int subclass — guard it


# ─────────── helpers (title_slash / title_dash / slugify) ───────────

def test_title_slash_format():
    p = {"startedAt": "2026-05-12T14:30:00Z", "endedAt": "2026-05-12T14:32:00Z",
         "call": {"id": "x", "customer": {"number": ""}}}
    assert call_archive.title_slash(p, "Rock Epoxy") == "12/05/26 10:30 - Rock Epoxy"


def test_title_dash_format():
    p = {"startedAt": "2026-05-12T14:30:00Z", "endedAt": "2026-05-12T14:32:00Z",
         "call": {"id": "x", "customer": {"number": ""}}}
    assert call_archive.title_dash(p, "Rock Epoxy") == "12-05-26 10-30 - Rock Epoxy"


def test_slugify_strips_unsafe_chars():
    assert call_archive.slugify("Joe's Plumbing/Heating") == "Joe's Plumbing Heating"
    assert call_archive.slugify("") == "Unknown"
    assert call_archive.slugify(None) == "Unknown"
    assert call_archive.slugify("  spaces  ") == "spaces"


def test_month_folder_format():
    p = {"startedAt": "2026-05-12T14:30:00Z"}
    assert call_archive.month_folder(p) == "05-2026"


# --------------------------------------------------------------------------- #
# Publishing the recording to a permanent, linkable path (2026-09-09)
#
# The GHL internal comment carries a link to the audio. Vapi's own urls cannot
# serve that purpose — the bare R2 object returns 400 without auth and the
# presigned one expires — so the archived mp3 is published under the Vapi call id
# into the directory nginx already serves at /voicsito/static/.
# --------------------------------------------------------------------------- #
@pytest.fixture
def public_dir(tmp_path, monkeypatch):
    d = tmp_path / "static-rec"
    monkeypatch.setattr(call_archive, "PUBLIC_REC_DIR", d)
    monkeypatch.setattr(call_archive, "PUBLIC_BASE_URL", "https://example.test/voicsito")
    return d


def test_no_url_until_the_file_is_published(public_dir):
    assert call_archive.public_recording_url("call-1") is None


def test_url_is_built_from_the_call_id(public_dir):
    public_dir.mkdir(parents=True)
    (public_dir / "call-1.mp3").write_bytes(b"audio")
    assert call_archive.public_recording_url("call-1") == (
        "https://example.test/voicsito/static/rec/call-1.mp3"
    )


def test_blank_call_id_has_no_url(public_dir):
    assert call_archive.public_recording_url("") is None


def test_publish_exposes_the_archived_file(tmp_path, public_dir):
    src = tmp_path / "recording.mp3"
    src.write_bytes(b"audio")
    call_archive._publish_recording(src, "call-1")
    assert (public_dir / "call-1.mp3").read_bytes() == b"audio"


def test_publishing_a_missing_file_is_a_no_op(tmp_path, public_dir):
    call_archive._publish_recording(tmp_path / "nope.mp3", "call-1")
    assert call_archive.public_recording_url("call-1") is None


def test_publish_is_idempotent(tmp_path, public_dir):
    src = tmp_path / "recording.mp3"
    src.write_bytes(b"audio")
    call_archive._publish_recording(src, "call-1")
    call_archive._publish_recording(src, "call-1")  # a Vapi resend
    assert len(list(public_dir.iterdir())) == 1


@pytest.mark.asyncio
async def test_archive_publishes_the_recording_it_downloaded(tmp_path, public_dir):
    """End to end: the download writes the file, the archive publishes it, and a
    link exists for the comment to carry."""
    async def fake_download(url, dest):
        dest.write_bytes(b"audio")
        return True

    with patch.object(call_archive, "_download_recording", new=AsyncMock(
            side_effect=fake_download)):
        await call_archive.archive_call(
            _payload(120), business_label="Rock Epoxy", base_dir=tmp_path,
        )

    call_id = _payload()["call"]["id"]
    assert call_archive.public_recording_url(call_id) == (
        f"https://example.test/voicsito/static/rec/{call_id}.mp3"
    )


# The comment sentinel is keyed by call id, not folder — calls under 30s never
# get a folder but still get a comment.
def test_comment_sentinel_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(call_archive, "COMMENT_FLAG_DIR", tmp_path / ".commented")
    assert call_archive.already_commented("call-1") is False
    call_archive.mark_commented("call-1")
    assert call_archive.already_commented("call-1") is True


def test_comment_sentinel_ignores_a_blank_call_id(tmp_path, monkeypatch):
    monkeypatch.setattr(call_archive, "COMMENT_FLAG_DIR", tmp_path / ".commented")
    call_archive.mark_commented("")
    assert call_archive.already_commented("") is False
