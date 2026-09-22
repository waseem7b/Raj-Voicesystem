"""Confirmation and Reminder carry Rebooking's latency/audio settings.

Owner, 2026-09-16: "update confirmation and reminder to all the settings
rebooking has except for model". The model stays gpt-4.1 on all three, and
optimizeStreamingLatency stays at the base 1 on these two because Rebooking's
3 is what dropped two words from the audio on call 01a0ab4a.
"""
import pathlib

ROOM_TONE = "https://webhooks.c7a.io/voicsito/static/room-tone-quiet.mp3"


def _src() -> str:
    return (pathlib.Path(__file__).resolve().parents[2]
            / "scripts" / "push_confirm_squad.py").read_text(encoding="utf-8")


def _entry(src: str, name: str, nxt: str) -> str:
    return src.split(f'"{name}": {{', 1)[1].split(f'"{nxt}": {{', 1)[0]


def test_confirmation_and_reminder_match_rebooking_latency_settings():
    src = _src()
    for name, nxt in (("confirmation", "reminder"), ("reminder", "rebooking")):
        e = _entry(src, name, nxt)
        assert '"smart_endpointing": False,' in e, name
        assert '"wait_seconds": 0.0,' in e, name
        assert '"stability": 0.6,' in e, name
        assert '"style": 0.5,' in e, name
        assert f'"background_sound": "{ROOM_TONE}",' in e, name


def test_streaming_latency_is_not_copied_from_rebooking():
    """3 dropped words on 01a0ab4a; only Rebooking carries it, pending revert."""
    src = _src()
    for name, nxt in (("confirmation", "reminder"), ("reminder", "rebooking")):
        assert '"optimizeStreamingLatency":' not in _entry(src, name, nxt), name


def test_model_is_not_overridden_on_the_two_flows():
    src = _src()
    for name, nxt in (("confirmation", "reminder"), ("reminder", "rebooking")):
        e = _entry(src, name, nxt)
        assert '"model":' not in e, name
        assert '"reasoning_effort"' not in e, name
