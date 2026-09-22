"""Runs and payloads must outlive a restart.

A deploy restarts the service, and in-memory history disappears with it — which
already made a real GHL hit look like it never arrived. Anything worth showing
the user is appended to disk and reloaded on boot.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SECRET = "test-secret"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ASHLEY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("BLOOIO_API_KEY", "api_fake")
    monkeypatch.setenv("ASHLEY_STATE_DIR", str(tmp_path))
    return tmp_path


def load_server(monkeypatch):
    import webhook.server as server

    importlib.reload(server)
    monkeypatch.setattr(
        server,
        "setup_prospect_group",
        lambda *a, **kw: {"ok": True, "group_id": "grp_x", "steps": []},
    )
    return server


def auth():
    return {"X-Ashley-Secret": SECRET}


def test_a_run_survives_a_restart(env, monkeypatch):
    server = load_server(monkeypatch)
    client = TestClient(server.app)
    run_id = client.post(
        "/group/setup", json={"phone": "+1555010010"}, headers=auth()
    ).json()["run_id"]

    restarted = load_server(monkeypatch)
    runs = TestClient(restarted.app).get("/runs", headers=auth()).json()["runs"]
    assert [r["run_id"] for r in runs] == [run_id]


def test_a_payload_survives_a_restart(env, monkeypatch):
    server = load_server(monkeypatch)
    TestClient(server.app).post(
        "/group/setup", json={"phone": "+1555010010", "company_name": "Tree City"},
        headers=auth(),
    )

    restarted = load_server(monkeypatch)
    payloads = TestClient(restarted.app).get("/payloads", headers=auth()).json()["payloads"]
    assert payloads[0]["body"]["company_name"] == "Tree City"


def test_a_rejected_payload_also_survives(env, monkeypatch):
    server = load_server(monkeypatch)
    TestClient(server.app).post("/group/setup", json={"no": "phone"}, headers=auth())

    restarted = load_server(monkeypatch)
    payloads = TestClient(restarted.app).get("/payloads", headers=auth()).json()["payloads"]
    assert payloads[0]["body"] == {"no": "phone"}
    assert "phone" in payloads[0]["error"]


def test_a_corrupt_state_file_does_not_stop_the_service(env, monkeypatch):
    (env / "payloads.jsonl").write_text("{not json\n")
    server = load_server(monkeypatch)

    client = TestClient(server.app)
    assert client.get("/health").status_code == 200
    assert client.get("/payloads", headers=auth()).json()["payloads"] == []
