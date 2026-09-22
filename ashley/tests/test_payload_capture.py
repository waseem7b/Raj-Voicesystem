"""Whatever GHL sends must be inspectable, even when it is unusable.

A test hook from GHL may carry sample fields, different field names, or no
phone at all. Rejecting it without recording the body loses the one thing the
test was for: seeing which fields actually arrive.
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
    monkeypatch.setattr(
        server,
        "setup_prospect_group",
        lambda *a, **kw: {"ok": True, "group_id": "grp_x", "steps": []},
    )
    return TestClient(server.app)


def auth():
    return {"X-Ashley-Secret": SECRET}


def test_records_the_raw_body_of_a_valid_request(client):
    client.post("/group/setup", json={"phone": "+1555010010", "extra": "kept"}, headers=auth())

    payloads = client.get("/payloads", headers=auth()).json()["payloads"]
    assert payloads[0]["body"]["extra"] == "kept"


def test_records_the_raw_body_even_when_the_phone_is_missing(client):
    r = client.post("/group/setup", json={"contact": {"name": "Sample"}}, headers=auth())

    assert r.status_code == 422
    payloads = client.get("/payloads", headers=auth()).json()["payloads"]
    assert payloads[0]["body"] == {"contact": {"name": "Sample"}}
    assert "phone" in payloads[0]["error"]


def test_records_a_body_that_is_not_even_json(client):
    r = client.post(
        "/group/setup",
        content=b"phone=%2B+1555010010",
        headers={**auth(), "Content-Type": "application/x-www-form-urlencoded"},
    )

    assert r.status_code == 422
    payloads = client.get("/payloads", headers=auth()).json()["payloads"]
    assert "phone=" in payloads[0]["raw"]


def test_payloads_need_the_secret(client):
    assert client.get("/payloads").status_code == 401


def test_an_unauthenticated_post_is_not_recorded(client):
    client.post("/group/setup", json={"phone": "+1555010010"})

    assert client.get("/payloads", headers=auth()).json()["payloads"] == []
