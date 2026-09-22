"""Per-call filesystem archive under /opt/webhooks/voicsito/live-recordings/.

Layout:

    live-recordings/
      MM-YYYY/                              ← month folder
        DD-MM-YY HH-MM - <business>/        ← per-call folder
          recording.mp3
          transcript.txt
          summary.json
          pushed.flag

Date/time strings are in the display timezone (env var `DISPLAY_TZ`, default
`America/New_York` since the Vapi calendar operates Eastern). On same-minute
same-business collision, an SS suffix is appended for uniqueness. Vapi
end-of-call-report resends for the SAME call.id reuse the existing folder
(idempotency via call_id match in summary.json).
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx

from .call_summary import classify_outcome, duration_label

log = logging.getLogger(__name__)

DEFAULT_BASE_DIR = Path(
    os.environ.get("LIVE_RECORDINGS_DIR", "/opt/webhooks/voicsito/live-recordings")
)
DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "America/New_York"))

# Recordings are ALSO published to a flat directory that nginx already serves
# straight off disk (the `location /voicsito/static/` block in
# scripts/deploy-webhooks.sh, which is matched before the app proxy). That gives
# a GHL internal comment a link that plays in any browser, needs no login, and
# never expires.
#
# Vapi's own urls cannot do any of that, verified 2026-09-09: artifact.recordingUrl
# is the bare R2 object and a ranged GET returns 400 InvalidArgument/Authorization,
# and artifact.presignedMonoUrl works but is time-limited — dead long before anyone
# opens the contact. Re-fetching GET /call/{id} does not reliably mint a fresh one.
#
# The file is named by the Vapi call id, so a link can be built from the id alone
# without knowing the archive folder name. That id is a UUID and is the ONLY thing
# protecting the file — anyone holding the link can play the call.
PUBLIC_REC_DIR = Path(
    os.environ.get("PUBLIC_RECORDINGS_DIR", "/opt/webhooks/voicsito/static/rec")
)
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://webhooks.c7a.io/voicsito"
).rstrip("/")


def _duration_sec(payload: dict) -> int:
    try:
        s = datetime.fromisoformat(payload["startedAt"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(payload["endedAt"].replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds()))
    except Exception:
        return 0


def slugify(name: str, max_len: int = 60) -> str:
    """Make a label safe for use as a directory or file name.
    Strips characters that break common filesystems / shells. Keeps spaces
    and most punctuation. Truncates to max_len.
    """
    if not name:
        return "Unknown"
    s = re.sub(r'[/\\:*?"<>|]+', " ", name)  # FS-unsafe -> space
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or "Unknown")[:max_len]


def transcript_filename(transcript_label: str = "") -> str:
    """Return the transcript filename for a given label.
    'Mike - Rock Epoxy' → 'Mike - Rock Epoxy transcript.txt'."""
    if transcript_label:
        return f"{slugify(transcript_label)} transcript.txt"
    return "transcript.txt"


def _ts_prefix(ts) -> str:
    """Format a Vapi message `time` as a 'Mon D h:mm:sspm ' prefix in the
    display timezone, e.g. 'May 31 5:00:00pm '.

    Vapi sends `time` as epoch MILLISECONDS (~1.7e12). datetime.fromtimestamp()
    expects seconds, so we scale ms → s by magnitude: any value >= 1e11 (≈ year
    5138 in seconds, but only ≈ 1973 in ms) is milliseconds. Payloads that send
    seconds still work. Returns '' for a missing/unusable value so a single bad
    turn degrades to an unprefixed line instead of raising and crashing the
    whole transcript — and, via archive_call, the entire end-of-call handler.

    Hour/day are computed directly rather than via %-I/%-d — those are glibc-only
    strftime codes that silently broke _fmt_slot_human on other platforms.
    """
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ""
    try:
        secs = ts / 1000 if ts >= 1e11 else ts
        dt = datetime.fromtimestamp(secs, tz=timezone.utc).astimezone(DISPLAY_TZ)
    except (OverflowError, ValueError, OSError):
        return ""
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    return f"{dt.strftime('%b')} {dt.day} {hour12}:{dt.minute:02d}:{dt.second:02d}{ampm} "


def build_transcript(payload: dict) -> str:
    """Build a timestamped transcript from the messages array.

    Each bot/user turn is prefixed with its local wall-clock time, e.g.:
      May 31 5:00:00pm AI: Hey, this is Jon...
      May 31 5:00:05pm User: Hi.

    Falls back to the plain payload["transcript"] string if the messages array
    has no time-stamped speech turns (e.g. test fixtures or older Vapi versions).
    """
    lines = []
    for m in (payload.get("messages") or []):
        role = m.get("role", "")
        if role not in ("bot", "user"):
            continue
        text = (m.get("message") or "").strip()
        if not text:
            continue
        speaker = "AI" if role == "bot" else "User"
        lines.append(f"{_ts_prefix(m.get('time'))}{speaker}: {text}")
    if lines:
        return "\n".join(lines)
    return (payload.get("transcript") or "").strip()


def _started_local(payload: dict) -> datetime:
    """Started time converted to the display timezone."""
    try:
        utc_dt = datetime.fromisoformat(payload["startedAt"].replace("Z", "+00:00"))
        return utc_dt.astimezone(DISPLAY_TZ)
    except Exception:
        return datetime.now(DISPLAY_TZ)


def title_slash(payload: dict, business_label: str) -> str:
    """`DD/MM/YY HH:MM - Business Name` — used in Telegram caption."""
    dt = _started_local(payload)
    return f"{dt.strftime('%d/%m/%y %H:%M')} - {business_label}"


def title_dash(payload: dict, business_label: str) -> str:
    """`DD-MM-YY HH-MM - Business Name` — used in folder + filename."""
    dt = _started_local(payload)
    return f"{dt.strftime('%d-%m-%y %H-%M')} - {slugify(business_label)}"


def month_folder(payload: dict) -> str:
    """`MM-YYYY` — month bucket directory."""
    return _started_local(payload).strftime("%m-%Y")


def _resolve_folder(base: Path, payload: dict, business_label: str) -> Path:
    """Compute the per-call folder, nested under the month subfolder.
    Returns an existing folder iff it belongs to the same call.id (idempotent
    resend). Otherwise creates a new unique path, disambiguating via SS suffix."""
    month_dir = base / month_folder(payload)
    desired_name = title_dash(payload, business_label)
    desired = month_dir / desired_name
    call_id = (payload.get("call") or {}).get("id") or ""

    if desired.exists():
        prior_summary = desired / "summary.json"
        if prior_summary.exists():
            try:
                prior = json.loads(prior_summary.read_text())
                if prior.get("call_id") == call_id:
                    return desired  # idempotent resend
            except Exception:
                pass
        # Collision with a different call same minute — disambiguate with SS
        ss = _started_local(payload).strftime("%S")
        return month_dir / f"{desired_name} {ss}s"

    return desired


# Recordings for our call lengths sit in the 1-3 MB range. Telegram's upload
# cap is 50 MB. Cap at 60 MB to leave headroom but bound memory/disk damage if
# someone (Vapi bug, attacker with the secret) feeds a multi-GB URL.
_MAX_RECORDING_BYTES = 60 * 1024 * 1024


async def _download_recording(url: str, dest: Path) -> bool:
    """Stream the recording to disk with a hard size cap. Returns True on
    success. On failure (HTTP non-2xx, oversize, network error), logs and
    returns False — the caller continues without an audio attachment."""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("GET", url) as r:
                if r.status_code != 200:
                    log.error("recording download HTTP %s for %s", r.status_code, url)
                    return False
                total = 0
                tmp = dest.with_suffix(dest.suffix + ".part")
                with tmp.open("wb") as fh:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        total += len(chunk)
                        if total > _MAX_RECORDING_BYTES:
                            log.error(
                                "recording download aborted — exceeded %d bytes for %s",
                                _MAX_RECORDING_BYTES, url,
                            )
                            try:
                                tmp.unlink()
                            except Exception:
                                pass
                            return False
                        fh.write(chunk)
                tmp.replace(dest)
                return True
    except Exception as e:
        log.error("recording download crashed for %s: %s", url, e)
        return False


def _publish_recording(src: Path, call_id: str) -> None:
    """Expose the archived mp3 at a stable public path. Never raises.

    Hard-links rather than copies so a second full byte-copy of every call does
    not accumulate on the VPS; falls back to a copy when the archive and the
    static dir are on different filesystems.
    """
    if not call_id or not src.exists():
        return
    try:
        PUBLIC_REC_DIR.mkdir(parents=True, exist_ok=True)
        dest = PUBLIC_REC_DIR / f"{call_id}.mp3"
        if dest.exists():
            return
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
    except Exception as e:  # noqa: BLE001 — a missing link must never fail a call
        log.warning("could not publish recording for call %s: %s", call_id, e)


def public_recording_url(call_id: str) -> Optional[str]:
    """The permanent link to a call's recording, or None when there isn't one.

    Checks the file rather than assuming it: calls under 30s are never archived,
    and a download can fail, so the caller must be able to tell the difference
    between "here is the audio" and "there is no audio" without plumbing a
    return value out of the archive step.
    """
    if not call_id:
        return None
    if not (PUBLIC_REC_DIR / f"{call_id}.mp3").exists():
        return None
    return f"{PUBLIC_BASE_URL}/static/rec/{call_id}.mp3"


async def archive_call(
    payload: dict,
    *,
    business_label: str = "Unknown",
    transcript_label: str = "",
    base_dir: Optional[Path] = None,
    outcome_override: Optional[tuple[str, str]] = None,
    call_type: str = "",
) -> Optional[Path]:
    """Write recording / transcript / summary for one ended call.
    Returns the folder path on success, or None if duration < 30s (skip).

    business_label: human-readable name for the prospect's business; goes in
    the folder name. Caller computes this from CRM/captured fields.
    transcript_label: label used for the transcript filename, e.g.
    'Mike - Rock Epoxy'. Falls back to 'transcript.txt' if empty.
    """
    duration = _duration_sec(payload)
    if duration < 30:
        log.info("archive_call skipped — duration %ds under 30s", duration)
        return None

    base = base_dir or DEFAULT_BASE_DIR
    base.mkdir(parents=True, exist_ok=True)
    folder = _resolve_folder(base, payload, business_label)
    folder.mkdir(parents=True, exist_ok=True)

    (folder / transcript_filename(transcript_label)).write_text(
        build_transcript(payload)
    )

    # Prefer a PRESIGNED url. payload.recordingUrl is the bare R2 object url and
    # needs auth we do not send — it returns HTTP 400, which is why Ashley's
    # first Telegram push (call 01a0682f, 2026-09-03) arrived with no audio.
    # Vapi puts working, time-limited urls on the artifact; take the mono one,
    # then stereo, and only fall back to the bare url if neither is present.
    _art = payload.get("artifact") or {}
    rec_url = (
        _art.get("presignedMonoUrl")
        or _art.get("presignedStereoUrl")
        or _art.get("recordingUrl")
        or payload.get("recordingUrl")
    )
    rec_path = folder / "recording.mp3"
    if rec_url and not rec_path.exists():
        await _download_recording(rec_url, rec_path)
    _publish_recording(rec_path, (payload.get("call") or {}).get("id") or "")

    icon, label, slot_iso = classify_outcome(payload)
    # classify_outcome only speaks the INBOUND vocabulary, so it buckets every
    # one of Ashley's outbound calls as OTHER. The outbound chain already knows
    # the real result (confirmed / rescheduled / no answer / voicemail) and
    # passes it here, so the daily stats can count what actually happened
    # instead of a wall of OTHER.
    if outcome_override:
        icon, label = outcome_override
    summary = {
        "call_type": call_type,
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


# The GHL-comment sentinel is keyed by CALL ID, not by folder, because the
# comment goes out for every call while a folder only exists for calls over 30s.
# Same job as pushed.flag: absorb Vapi's end-of-call-report resends.
COMMENT_FLAG_DIR = DEFAULT_BASE_DIR / ".commented"


def already_commented(call_id: str) -> bool:
    """True iff a GHL internal comment was already posted for this call."""
    if not call_id:
        return False
    return (COMMENT_FLAG_DIR / f"{call_id}.flag").exists()


def mark_commented(call_id: str) -> None:
    """Never raises — losing the sentinel costs a duplicate comment on a resend,
    which is better than the comment not going out at all."""
    if not call_id:
        return
    try:
        COMMENT_FLAG_DIR.mkdir(parents=True, exist_ok=True)
        (COMMENT_FLAG_DIR / f"{call_id}.flag").write_text(
            datetime.utcnow().isoformat() + "Z"
        )
    except Exception as e:  # noqa: BLE001
        log.warning("could not mark call %s commented: %s", call_id, e)
