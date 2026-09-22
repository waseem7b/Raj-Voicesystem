# Voicsito Webhooks (Vapi → GHL Bridge)

A small FastAPI service that receives Vapi tool-call requests and proxies them
to our existing GHL backend (`src/agent/ghl.py`). It is intentionally
stateless except for an in-memory per-call cache (1 h TTL) keyed by Vapi
`call.id`.

The service exposes a single tool endpoint:

| Method | Path        | Purpose                                         |
| ------ | ----------- | ----------------------------------------------- |
| POST   | /vapi/tool  | Receives ALL Vapi tool calls                    |
| GET    | /health     | Liveness probe                                  |

Vapi tools handled:

- `update_prospect_info` — updates the CRM contact (or caches locally if no
  contact resolved yet) with first/last name, business, email captured during
  the call.
- `get_available_slots` — fetches Video Call Calendar slots; returns a
  human-readable string with `[iso:...]` markers so the LLM can pass an ISO
  back to `book_video_call`.
- `book_video_call` — books an appointment, creating a Texts CRM contact on
  the fly when caller-ID lookup misses.
- `end_call` — compatibility stub. Vapi's built-in `endCall` system tool
  performs the actual hangup.

## Local development

```bash
# Install deps
python3 -m venv .venv-webhooks
source .venv-webhooks/bin/activate
pip install -r src/webhooks/requirements.txt

# Run with auto-reload
PYTHONPATH=. uvicorn src.webhooks.server:app --reload --port 8081

# Smoke test
curl http://127.0.0.1:8081/health
```

The service loads env vars from `/opt/webhooks/voicsito/.env` if present,
otherwise falls back to `config/.env`. The required GHL env vars (`GHL_*`) are
required for CRM lookups, contact creation, and calendar booking.

## Test with curl

A minimal Vapi-shaped tool-call request:

```bash
curl -sS -X POST http://127.0.0.1:8081/vapi/tool \
  -H 'Content-Type: application/json' \
  -d '{
    "message": {
      "type": "tool-calls",
      "call": {
        "id": "test-call-1",
        "customer": {"number": "+15551234567"}
      },
      "toolCallList": [
        {
          "id": "tc-abc",
          "type": "function",
          "function": {
            "name": "get_available_slots",
            "arguments": "{\"days_ahead\": 5}"
          }
        }
      ]
    }
  }'
```

Expected response shape (HTTP 200):

```json
{"results": [{"toolCallId": "tc-abc", "result": "Available slots ..."}]}
```

## VPS deploy

Run the deploy script from a workstation that has SSH access to the Voicsito
VPS:

```bash
./scripts/deploy-webhooks.sh
```

The script:

1. Validates Python syntax of `src/webhooks/*.py` and `src/agent/ghl.py`
2. Uploads both to `/opt/webhooks/voicsito/` and `/opt/webhooks/voicsito/agent/`
3. Installs `fastapi`, `uvicorn[standard]`, `httpx`, `python-dotenv` into the
   VPS Python env (idempotent)
4. Writes a systemd unit at `/etc/systemd/system/voicsito-webhooks.service`
5. `daemon-reload` + `restart` + verifies `curl http://localhost:8081/health`

Manual systemd unit equivalent:

```ini
[Unit]
Description=Voicsito Vapi Webhooks
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/webhooks/voicsito
EnvironmentFile=/opt/webhooks/voicsito/.env
ExecStart=/usr/bin/python3 -m uvicorn src.webhooks.server:app --host 127.0.0.1 --port 8081
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
```

## Vapi configuration

Configure each Vapi custom tool to call this Server URL:

```
https://<your-domain>/vapi/tool
```

Vapi requires HTTPS, so the service must be fronted by TLS termination — see
the next section. Inside Vapi's tool-config UI:

- Server URL: `https://<your-domain>/vapi/tool`
- Server Secret: any random value (set the same value as `VAPI_WEBHOOK_SECRET`
  on the VPS); leaving it blank disables verification (allowed for testing
  only).
- Per-tool function name must be one of: `update_prospect_info`,
  `get_available_slots`, `book_video_call`, `end_call`.

**TLS:** see [`docs/webhook-infrastructure.md`](../../docs/webhook-infrastructure.md). The shared `webhooks.c7a.io` infra (nginx + certbot) handles termination; you don't need to set up TLS per-project.

## Signature verification

The handler accepts either `x-vapi-signature` (HMAC-SHA256 hex digest of the
raw body) or `x-vapi-secret` (literal shared-secret value, Vapi's "Custom
Headers" feature). Set `VAPI_WEBHOOK_SECRET` in `/opt/webhooks/voicsito/.env` to enable
verification. When the env var is unset the service logs a warning at startup
and accepts all requests — fine for testing, never for production.

## Logs

- Local: stdout from uvicorn.
- VPS: `journalctl -u voicsito-webhooks -f`.
