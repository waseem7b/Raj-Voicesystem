"""The webhook GHL calls once it has created a prospect's group.

Device calls take tens of seconds, well past a GHL webhook timeout, so the
endpoint acknowledges immediately and brands the group in the background. The
run is recorded so the outcome can be inspected afterward.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SECRET = "test-secret"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ASHLEY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("BLOOIO_API_KEY", "api_fake")

    import webhook.server as server

    server.reset_state()
    recorded = []

    def fake_setup(client, business_name, **kw):
        recorded.append({"business_name": business_name, **kw})
        return {"ok": True, "group_id": "grp_x", "chat_id": "chat_x", "steps": []}

    monkeypatch.setattr(server, "setup_prospect_group", fake_setup)
    test_client = TestClient(server.app)
    test_client.recorded = recorded
    test_client.server = server
    return test_client


def post(client, body, secret=SECRET):
    headers = {"X-Ashley-Secret": secret} if secret else {}
    return client.post("/group/setup", json=body, headers=headers)


def test_health_needs_no_secret(client):
    assert client.get("/health").status_code == 200


def test_rejects_a_request_with_no_secret(client):
    r = post(client, {"phone": "+1555010010", "business_name": "Tree City"}, secret=None)
    assert r.status_code == 401
    assert client.recorded == []


def test_rejects_a_request_with_the_wrong_secret(client):
    r = post(client, {"phone": "+1555010010", "business_name": "X"}, secret="nope")
    assert r.status_code == 401
    assert client.recorded == []


def test_rejects_a_request_with_no_phone(client):
    r = post(client, {"business_name": "Tree City"})
    assert r.status_code == 422
    assert client.recorded == []


def test_acknowledges_immediately_and_runs_the_setup(client):
    r = post(client, {"phone": "+1555010010", "business_name": "Tree City"})

    assert r.status_code == 202
    assert r.json()["accepted"] is True
    assert client.recorded[0]["business_name"] == "Tree City"


def test_shares_the_card_by_default(client):
    """The card works on v4, so it is no longer opt-in."""
    post(client, {"phone": "+1555010010", "business_name": "Tree City"})

    assert client.recorded[0]["share_card"] is True


def test_the_card_can_still_be_turned_off(client):
    post(client, {"phone": "+1555010010", "business_name": "Tree City",
                  "share_card": False})

    assert client.recorded[0]["share_card"] is False


def test_server_calls_setup_with_arguments_the_real_function_accepts():
    """Guards against signature drift the fake in `client` would hide."""
    import inspect

    from blooio.group_setup import setup_prospect_group
    import webhook.server as server

    req = server.GroupSetupRequest(phone="+1555010010", business_name="Tree City")
    inspect.signature(setup_prospect_group).bind(
        object(), "Tree City", **server.setup_kwargs(req)
    )


def test_falls_back_to_a_default_name_when_business_is_missing(client):
    """GHL may only be able to send the phone; the run must still proceed."""
    r = post(client, {"phone": "+1555010010"})

    assert r.status_code == 202
    assert client.recorded[0]["business_name"] == "Growth"


def test_records_the_run_so_the_outcome_can_be_inspected(client):
    r = post(client, {"phone": "+1555010010", "business_name": "Tree City"})
    run_id = r.json()["run_id"]

    runs = client.get("/runs", headers={"X-Ashley-Secret": SECRET}).json()
    assert runs["runs"][0]["run_id"] == run_id
    assert runs["runs"][0]["result"]["group_id"] == "grp_x"


def test_a_crashing_setup_is_recorded_rather_than_lost(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("device unreachable")

    monkeypatch.setattr(client.server, "setup_prospect_group", boom)
    r = post(client, {"phone": "+1555010010", "business_name": "Tree City"})

    assert r.status_code == 202
    runs = client.get("/runs", headers={"X-Ashley-Secret": SECRET}).json()
    assert runs["runs"][0]["result"]["ok"] is False
    assert "device unreachable" in runs["runs"][0]["result"]["error"]
