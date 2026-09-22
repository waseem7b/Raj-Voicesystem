"""Ashley — FastAPI webhook for prospect group setup.

GHL's automation creates the group chat and opens the conversation, then calls
here. We find the group from the prospect's number, brand it, and drop Ashley's
contact card in.

Device-backed calls (icon upload, card share) take tens of seconds — longer than
a GHL webhook will wait — so the endpoint acknowledges immediately and runs the
work in the background. Outcomes are kept in memory and served at /ashley/runs.

Run locally:
    uvicorn webhook.server:app --reload --port 8082 --app-dir ashley/src

Run on VPS:
    uvicorn webhook.server:app --host 0.0.0.0 --port 8082 --app-dir ashley/src
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from blooio.client import BlooioClient
from blooio.group_setup import setup_prospect_group
from webhook.fields import extract_fields

try:
    from dotenv import load_dotenv

    for env_path in ("/opt/webhooks/ashley/.env", "ashley/config/.env", "config/.env"):
        if os.path.exists(env_path):
            load_dotenv(env_path)
            break
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ashley")

# Used when GHL can only send the phone number.
DEFAULT_BUSINESS = "Growth"
RUN_HISTORY = 50

app = FastAPI(title="Ashley webhook")

# History is appended to disk because a deploy restarts the service, and losing
# a real hit to a restart already made one look like it never arrived.
STATE_DIR = Path(os.getenv("ASHLEY_STATE_DIR", "/opt/webhooks/ashley/state"))
RUNS_FILE = STATE_DIR / "runs.jsonl"
PAYLOADS_FILE = STATE_DIR / "payloads.jsonl"


def _load(path: Path) -> deque[dict[str, Any]]:
    """Newest-first history from a JSONL file. Corruption must not stop boot."""
    entries: list[dict[str, Any]] = []
    try:
        for line in path.read_text().splitlines():
            try:
                entries.append(json.loads(line))
            except Exception:
                continue
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("could not read %s", path)
    return deque(reversed(entries[-RUN_HISTORY:]), maxlen=RUN_HISTORY)


def _append(path: Path, entry: dict[str, Any]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        log.exception("could not persist to %s", path)


_runs: deque[dict[str, Any]] = _load(RUNS_FILE)
_payloads: deque[dict[str, Any]] = _load(PAYLOADS_FILE)


def reset_state() -> None:
    """Clear recorded runs and payloads. Used by tests."""
    _runs.clear()
    _payloads.clear()


def _record_payload(raw: bytes, body: Any, error: str | None = None) -> None:
    """Keep every authenticated request body, usable or not.

    A GHL test hook is often the only chance to see which fields actually
    arrive, so rejecting one must not discard it.
    """
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "raw": raw.decode("utf-8", "replace")[:4000],
        "body": body,
        "error": error,
    }
    _payloads.appendleft(entry)
    _append(PAYLOADS_FILE, entry)


class GroupSetupRequest(BaseModel):
    # Phone is not used to find the group (the title is) but identifies the run.
    phone: str = Field(min_length=7)
    business_name: str | None = None
    share_card: bool = True


def setup_kwargs(req: GroupSetupRequest) -> dict[str, Any]:
    """Optional arguments forwarded to setup_prospect_group."""
    return {"share_card": req.share_card}


def _require_secret(provided: str | None) -> None:
    expected = os.getenv("ASHLEY_WEBHOOK_SECRET")
    if not expected:
        raise HTTPException(status_code=500, detail="ASHLEY_WEBHOOK_SECRET not set")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="bad secret")


def _record(run_id: str, req: GroupSetupRequest, result: dict[str, Any]) -> None:
    entry = {
        "run_id": run_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "phone": req.phone,
        "result": result,
    }
    _runs.appendleft(entry)
    _append(RUNS_FILE, entry)


def _run_setup(run_id: str, req: GroupSetupRequest) -> None:
    business = req.business_name or DEFAULT_BUSINESS
    try:
        client = BlooioClient(api_key=os.environ["BLOOIO_API_KEY"], timeout=60)
        result = setup_prospect_group(client, business, **setup_kwargs(req))
    except Exception as e:
        log.exception("[%s] setup crashed", run_id)
        result = {"ok": False, "group_id": None, "error": str(e)}

    log.info("[%s] %s", run_id, result)
    _record(run_id, req, result)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "runs_recorded": len(_runs)}


@app.post("/group/setup", status_code=202)
async def group_setup(
    request: Request,
    background: BackgroundTasks,
    x_ashley_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_secret(x_ashley_secret)

    raw = await request.body()
    try:
        body = json.loads(raw)
    except Exception as e:
        _record_payload(raw, None, f"not json: {e}")
        log.warning("unparseable body: %s", raw[:400])
        raise HTTPException(status_code=422, detail="body is not JSON")

    try:
        fields = extract_fields(body)
        req = GroupSetupRequest(
            phone=fields["phone"],
            business_name=fields["business_name"],
            share_card=bool(body.get("share_card", True)),
        )
    except (ValueError, ValidationError) as e:
        _record_payload(raw, body, str(e))
        log.warning("unusable payload: %s", body)
        raise HTTPException(status_code=422, detail=str(e))

    _record_payload(raw, body)
    run_id = uuid.uuid4().hex[:12]
    log.info("[%s] queued setup for %s | payload=%s", run_id, req.phone, body)
    background.add_task(_run_setup, run_id, req)
    return {"accepted": True, "run_id": run_id}


@app.get("/payloads")
def payloads(x_ashley_secret: str | None = Header(default=None)) -> dict[str, Any]:
    """Every authenticated request body received, newest first."""
    _require_secret(x_ashley_secret)
    return {"payloads": list(_payloads)}


@app.get("/runs")
def runs(x_ashley_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _require_secret(x_ashley_secret)
    return {"runs": list(_runs)}
