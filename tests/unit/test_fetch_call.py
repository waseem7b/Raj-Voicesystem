"""Tests for scripts/fetch_call.py — Vapi call data normalization."""
from unittest.mock import MagicMock, patch

import pytest


def _fake_vapi_call_response():
    return {
        "id": "019e462b-7bff-711f-aae4-1780cb3da813",
        "startedAt": "2026-05-20T18:42:11Z",
        "endedAt": "2026-05-20T18:46:43Z",
        "endedReason": "customer-ended-call",
        "assistantId": "46e47e95-674a-4624-9029-69bb39c4daf0",
        "assistant": {"name": "Voicsito - Stage 1 - Inbound Texts"},
        "customer": {"number": "+14255550199", "firstName": "Test"},
        "phoneNumber": {"number": "+1555010011"},
        "summary": "Prospect was on the road, asked for callback.",
        "transcript": "AI: Hey Jon here, who's this?\nUser: This is Mike.\n",
        "recordingUrl": "https://storage.vapi.ai/abc.mp3",
        "metadata": {"biz_type": "concrete"},
    }


def test_fetch_call_returns_normalized_dict_for_voicsito_call():
    from scripts.fetch_call import fetch_one

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _fake_vapi_call_response()
        mock_response.raise_for_status.return_value = None
        mock_httpx.get.return_value = mock_response

        result = fetch_one("019e462b-7bff-711f-aae4-1780cb3da813", api_key="test")

    assert result["call_id"] == "019e462b-7bff-711f-aae4-1780cb3da813"
    assert result["started_at"] == "2026-05-20T18:42:11Z"
    assert result["duration_s"] == 272  # 18:46:43 - 18:42:11
    assert result["ended_reason"] == "customer-ended-call"
    assert result["assistant_name"] == "Voicsito - Stage 1 - Inbound Texts"
    assert result["stage_reached"] == "stage1"
    assert result["customer_number"] == "+14255550199"
    assert result["customer_first_name"] == "Test"
    assert result["biz_type"] == "concrete"
    assert result["summary"].startswith("Prospect was on the road")
    assert "Hey Jon here" in result["transcript"]
    assert result["recording_url"] == "https://storage.vapi.ai/abc.mp3"


def test_fetch_call_rejects_non_voicsito_call():
    from scripts.fetch_call import fetch_one

    payload = _fake_vapi_call_response()
    payload["assistantId"] = "0ffb1d95-6bfa-40d7-923e-66e504a4c58f"  # Cloud 7 Inbound
    payload["assistant"]["name"] = "Cloud 7 Inbound"
    payload["squadId"] = None  # ensure squad path doesn't accidentally pass

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None
        mock_httpx.get.return_value = mock_response

        with pytest.raises(ValueError, match="not a Voicsito"):
            fetch_one("some-id", api_key="test")


def _fake_squad_call_response():
    """A squad-routed call (the production shape)."""
    return {
        "id": "019e-squad-id",
        "startedAt": "2026-05-20T18:42:11Z",
        "endedAt": "2026-05-20T18:46:43Z",
        "endedReason": "customer-ended-call",
        "assistantId": None,
        "assistant": None,
        "squadId": "bea22dc5-24eb-42ed-ba81-f52cf1986ebf",
        "customer": {"number": "+14255550199"},
        "summary": "Squad call summary.",
        "transcript": None,  # top-level transcript may be null; falls back to artifact
        "recordingUrl": None,
        "artifact": {
            "transcript": "AI: Hey Jon here.\nUser: Hi.\n",
            "recordingUrl": "https://storage.vapi.ai/squad.wav",
            "assistantActivations": [
                {
                    "assistantName": "Voicsito - Stage 1 - Inbound Texts",
                    "assistantId": "46e47e95-674a-4624-9029-69bb39c4daf0",
                },
                {
                    "assistantName": "Voicsito - Stage 2 - Qualify",
                    "assistantId": "7b2666e0-154d-4c85-82f0-07b4c51ad264",
                },
            ],
        },
        "metadata": {},
    }


def test_fetch_call_accepts_voicsito_squad_call_and_uses_last_activation_as_stage():
    from scripts.fetch_call import fetch_one

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _fake_squad_call_response()
        mock_response.raise_for_status.return_value = None
        mock_httpx.get.return_value = mock_response

        result = fetch_one("019e-squad-id", api_key="test")

    assert result["call_id"] == "019e-squad-id"
    # Last activation = stage 2
    assert result["stage_reached"] == "stage2"
    assert result["assistant_id"] == "7b2666e0-154d-4c85-82f0-07b4c51ad264"
    assert result["assistant_name"] == "Voicsito - Stage 2 - Qualify"
    # Transcript + recording url fell back to artifact.*
    assert "Hey Jon here" in result["transcript"]
    assert result["recording_url"] == "https://storage.vapi.ai/squad.wav"


def test_fetch_call_squad_with_empty_activations_returns_unknown_stage():
    from scripts.fetch_call import fetch_one

    payload = _fake_squad_call_response()
    payload["artifact"]["assistantActivations"] = []

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None
        mock_httpx.get.return_value = mock_response

        result = fetch_one("019e-squad-id", api_key="test")

    assert result["stage_reached"] == "unknown"
    assert result["assistant_id"] is None
    assert result["assistant_name"] == "Voicsito Squad"


def test_fetch_call_rejects_call_with_neither_voicsito_assistant_nor_squad():
    from scripts.fetch_call import fetch_one

    payload = _fake_squad_call_response()
    payload["squadId"] = "some-other-squad"

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = payload
        mock_response.raise_for_status.return_value = None
        mock_httpx.get.return_value = mock_response

        with pytest.raises(ValueError, match="not a Voicsito"):
            fetch_one("some-id", api_key="test")


def _fake_list_response(calls):
    """Build a Vapi /call list response wrapping the given call dicts."""
    return MagicMock(
        status_code=200,
        json=MagicMock(return_value=calls),
        raise_for_status=MagicMock(return_value=None),
    )


def test_fetch_list_filters_to_voicsito_only():
    from scripts.fetch_call import fetch_list

    voicsito = _fake_vapi_call_response()
    other = _fake_vapi_call_response()
    other["id"] = "other-id"
    other["assistantId"] = "0ffb1d95-6bfa-40d7-923e-66e504a4c58f"
    other["assistant"]["name"] = "Cloud 7 Inbound"

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_httpx.get.return_value = _fake_list_response([voicsito, other])
        results = fetch_list(api_key="test", limit=10)

    assert len(results) == 1
    assert results[0]["call_id"] == voicsito["id"]


def test_fetch_list_applies_biz_type_filter():
    from scripts.fetch_call import fetch_list

    cleaning = _fake_vapi_call_response()
    cleaning["id"] = "c1"
    cleaning["metadata"] = {"biz_type": "cleaning"}

    concrete = _fake_vapi_call_response()
    concrete["id"] = "c2"
    concrete["metadata"] = {"biz_type": "concrete"}

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_httpx.get.return_value = _fake_list_response([cleaning, concrete])
        results = fetch_list(
            api_key="test", limit=10, filters={"biz_type": "cleaning"},
        )

    assert len(results) == 1
    assert results[0]["call_id"] == "c1"


def test_fetch_list_applies_stage_reached_filter():
    from scripts.fetch_call import fetch_list

    s1 = _fake_vapi_call_response()
    s1["id"] = "s1"
    # already stage1 by default fixture

    s2 = _fake_vapi_call_response()
    s2["id"] = "s2"
    s2["assistantId"] = "7b2666e0-154d-4c85-82f0-07b4c51ad264"
    s2["assistant"]["name"] = "Voicsito - Stage 2 - Qualify"

    with patch("scripts.fetch_call.httpx") as mock_httpx:
        mock_httpx.get.return_value = _fake_list_response([s1, s2])
        results = fetch_list(
            api_key="test", limit=10, filters={"stage_reached": "stage2"},
        )

    assert len(results) == 1
    assert results[0]["call_id"] == "s2"


def test_fetch_list_parses_filter_strings():
    from scripts.fetch_call import parse_filters

    parsed = parse_filters(["biz_type=cleaning", "ended_reason=customer-ended-call"])
    assert parsed == {
        "biz_type": "cleaning",
        "ended_reason": "customer-ended-call",
    }


def test_fetch_list_rejects_unknown_filter_key():
    from scripts.fetch_call import parse_filters

    with pytest.raises(ValueError, match="unknown filter key"):
        parse_filters(["something_else=foo"])
