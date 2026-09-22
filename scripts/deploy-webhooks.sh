#!/bin/sh
# ────────────────────────────────────────────────────────────────────
# Deploy the Voicsito Vapi-tools webhook server to the shared
# webhooks.c7a.io infrastructure on the Hostinger VPS.
#
# Prerequisite: the VPS must already have webhooks.c7a.io provisioned
# via /opt/webhooks-infra/bin/bootstrap.sh (shared infra repo:
# github.com/waseem7b/webhooks-infra, clone at ~/CLAUDSITO/webhooks-infra).
#
# Layout on the VPS (Voicsito's slot in the shared infra):
#   /opt/webhooks/voicsito/                       ← project code root
#     ├── src/webhooks/server.py                  ← FastAPI app
#     ├── src/agent/ghl.py                        ← reused GHL client
#     ├── .env                                    ← project secrets
#     └── venv/                                   ← isolated Python venv
#   /etc/systemd/system/voicsito-webhooks.service
#   /etc/nginx/sites.d/webhooks/voicsito.conf     ← path-prefix route block
#
# Public URL:    https://webhooks.c7a.io/voicsito/vapi/tool
# Local port:    8081 (Voicsito's reserved slot)
# nginx's proxy_pass with trailing slash strips the /voicsito prefix,
# so the FastAPI app sees /vapi/tool not /voicsito/vapi/tool.
# ────────────────────────────────────────────────────────────────────

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_WEBHOOKS_DIR="$PROJECT_DIR/src/webhooks"
LOCAL_AGENT_DIR="$PROJECT_DIR/src/agent"
LOCAL_AUDIO_DIR="$PROJECT_DIR/audio"
LOCAL_ENV_FILE="$PROJECT_DIR/config/.env"
# voicsito-vps resolves to the Tailscale IP via ~/.ssh/config (durable path,
# survives ISP/Hostinger routing issues). voicsito-vps-direct is the public-IP
# fallback if Tailscale is ever down. Override at runtime via env var:
#   VPS_HOST=root@voicsito-vps-direct bash scripts/deploy-webhooks.sh
VPS_HOST="${VPS_HOST:-root@voicsito-vps-direct}"

# Project namespace within the shared infra
PROJECT_NAME="voicsito"
PROJECT_PORT=8081
VPS_BASE="/opt/webhooks/$PROJECT_NAME"
VPS_WEBHOOKS_DIR="$VPS_BASE/src/webhooks"
VPS_AGENT_DIR="$VPS_BASE/src/agent"
VPS_STATIC_DIR="$VPS_BASE/static"
VPS_VENV="$VPS_BASE/venv"
SYSTEMD_UNIT="voicsito-webhooks"
NGINX_SITE_FILE="/etc/nginx/sites.d/webhooks/$PROJECT_NAME.conf"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/voicsito_vps}"

echo "════════════════════════════════════════════════════════════"
echo "  Voicsito Webhooks Deploy"
echo "  → https://webhooks.c7a.io/$PROJECT_NAME/vapi/tool"
echo "════════════════════════════════════════════════════════════"

# 1. Validate Python syntax locally
echo ""
echo "[1/7] Validating Python syntax..."
for f in "$LOCAL_WEBHOOKS_DIR/server.py" "$LOCAL_WEBHOOKS_DIR/outbound.py" "$LOCAL_WEBHOOKS_DIR/tz_resolver.py" "$LOCAL_AGENT_DIR/ghl.py"; do
  # encoding is explicit: a bare open() uses the platform default (cp1252 on
  # Windows), so any non-ASCII in a source file failed here as a bogus
  # "syntax error". The sources are UTF-8 - read them as UTF-8.
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" "$f" \
    && echo "  ✓ $(basename "$f")" \
    || { echo "  ✗ $f — syntax error, aborting"; exit 1; }
done

# 2. Verify the shared infra is provisioned on the VPS
echo ""
echo "[2/7] Verifying shared webhook infra is up..."
INFRA_OK=$(ssh -i "$SSH_KEY" "$VPS_HOST" "test -d /etc/nginx/sites.d/webhooks && systemctl is-active --quiet nginx && echo yes || echo no")
if [ "$INFRA_OK" != "yes" ]; then
  echo "  ✗ Shared infra not ready. See ~/CLAUDSITO/webhooks-infra/README.md → 'Fresh-VPS bootstrap'."
  exit 1
fi
echo "  ✓ nginx + /etc/nginx/sites.d/webhooks/ exist"

# 3. Ensure remote dirs exist
echo ""
echo "[3/7] Preparing remote directories..."
ssh -i "$SSH_KEY" "$VPS_HOST" "mkdir -p $VPS_WEBHOOKS_DIR $VPS_AGENT_DIR $VPS_STATIC_DIR"
ssh -i "$SSH_KEY" "$VPS_HOST" "touch $VPS_BASE/src/__init__.py $VPS_AGENT_DIR/__init__.py"

# 4. Upload code + .env (the latter only on first deploy; never overwrite if VPS already has one)
echo ""
echo "[4/7] Uploading source..."
# One list, used for BOTH the upload and the confirmation line. The echo below
# used to be hardcoded and had drifted from reality: call_archive.py,
# call_summary.py and telegram_notify.py were never uploaded at all. They sit on
# the VPS from an early manual copy, so every later edit to them silently never
# shipped (found 2026-09-03 — a recording-url fix looked deployed and was not).
WEBHOOK_FILES=(
  server.py
  outbound.py
  ghl_trigger.py
  vapi_client.py
  tz_resolver.py
  text_digest.py
  call_archive.py
  call_summary.py
  stats.py
  telegram_notify.py
  __init__.py
  requirements.txt
)
scp -i "$SSH_KEY" -q   "${WEBHOOK_FILES[@]/#/$LOCAL_WEBHOOKS_DIR/}"   "$VPS_HOST:$VPS_WEBHOOKS_DIR/"
scp -i "$SSH_KEY" -q \
  "$LOCAL_AGENT_DIR/ghl.py" \
  "$VPS_HOST:$VPS_AGENT_DIR/"
# Static assets (background sound, etc.) — served by nginx, not the FastAPI app
if ls "$LOCAL_AUDIO_DIR"/*.mp3 "$LOCAL_AUDIO_DIR"/*.wav 2>/dev/null | head -1 >/dev/null; then
  scp -i "$SSH_KEY" -q "$LOCAL_AUDIO_DIR"/*.mp3 "$VPS_HOST:$VPS_STATIC_DIR/" 2>/dev/null || true
  scp -i "$SSH_KEY" -q "$LOCAL_AUDIO_DIR"/*.wav "$VPS_HOST:$VPS_STATIC_DIR/" 2>/dev/null || true
  echo "  ✓ uploaded static audio assets to $VPS_STATIC_DIR"
fi
echo "  ✓ uploaded ${WEBHOOK_FILES[*]} + ghl.py"

# Sync .env on first deploy only — preserves any VPS-side changes after that
ssh -i "$SSH_KEY" "$VPS_HOST" "test -f $VPS_BASE/.env" || {
  if [ -f "$LOCAL_ENV_FILE" ]; then
    scp -i "$SSH_KEY" -q "$LOCAL_ENV_FILE" "$VPS_HOST:$VPS_BASE/.env"
    ssh -i "$SSH_KEY" "$VPS_HOST" "chmod 600 $VPS_BASE/.env"
    echo "  ✓ initial .env uploaded (chmod 600)"
  else
    echo "  ⚠  no local config/.env — VPS will need one at $VPS_BASE/.env"
  fi
}

# 5. Project venv + deps (idempotent)
echo ""
echo "[5/7] Setting up Python venv + deps on VPS..."
ssh -i "$SSH_KEY" "$VPS_HOST" "
  if [ ! -d $VPS_VENV ]; then
    python3 -m venv $VPS_VENV
  fi
  $VPS_VENV/bin/pip install --quiet --upgrade pip
  $VPS_VENV/bin/pip install --quiet -r $VPS_WEBHOOKS_DIR/requirements.txt
"
echo "  ✓ deps installed in $VPS_VENV"

# 6. Systemd unit + nginx include file (write or update)
echo ""
echo "[6/7] Writing systemd unit + nginx site config..."
ssh -i "$SSH_KEY" "$VPS_HOST" "cat > /etc/systemd/system/$SYSTEMD_UNIT.service" <<EOF
[Unit]
Description=Voicsito Vapi Webhooks (FastAPI on shared webhooks.c7a.io infra)
After=network.target

[Service]
Type=simple
WorkingDirectory=$VPS_BASE
EnvironmentFile=$VPS_BASE/.env
ExecStart=$VPS_VENV/bin/uvicorn src.webhooks.server:app --host 127.0.0.1 --port $PROJECT_PORT
Restart=always
RestartSec=3
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# nginx include — proxy_pass with trailing slash strips the /voicsito prefix
ssh -i "$SSH_KEY" "$VPS_HOST" "cat > $NGINX_SITE_FILE" <<EOF
# Voicsito Vapi-tools webhook (managed by scripts/deploy-webhooks.sh)
# Public path: /$PROJECT_NAME/* → http://127.0.0.1:$PROJECT_PORT/*

# Static assets (background sound, voicemail audio, etc.) served directly from disk.
# This block must be BEFORE /$PROJECT_NAME/ so it matches first.
location /$PROJECT_NAME/static/ {
    alias $VPS_STATIC_DIR/;
    expires 7d;
    add_header Cache-Control "public, immutable";
    access_log off;
}

location /$PROJECT_NAME/ {
    proxy_pass http://127.0.0.1:$PROJECT_PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix /$PROJECT_NAME;
    proxy_read_timeout 90s;
    client_max_body_size 10M;
}
EOF

ssh -i "$SSH_KEY" "$VPS_HOST" "
  systemctl daemon-reload
  systemctl enable $SYSTEMD_UNIT >/dev/null 2>&1
  systemctl restart $SYSTEMD_UNIT
  nginx -t >/dev/null 2>&1 || { echo 'nginx config invalid'; exit 1; }
  systemctl reload nginx
"
echo "  ✓ $SYSTEMD_UNIT restarted; nginx reloaded"

sleep 3

# 7. Verify health locally and through nginx
echo ""
echo "[7/7] Verifying health..."
LOCAL_HEALTH=$(ssh -i "$SSH_KEY" "$VPS_HOST" "curl -fsS http://127.0.0.1:$PROJECT_PORT/health 2>/dev/null || true")
if echo "$LOCAL_HEALTH" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
  echo "  ✓ local /health OK"
else
  echo "  ✗ local /health failed — recent journal:"
  ssh -i "$SSH_KEY" "$VPS_HOST" "journalctl -u $SYSTEMD_UNIT -n 40 --no-pager"
  exit 1
fi

PUBLIC_HEALTH=$(curl -fsS "https://webhooks.c7a.io/$PROJECT_NAME/health" 2>/dev/null || true)
if echo "$PUBLIC_HEALTH" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'; then
  echo "  ✓ public https://webhooks.c7a.io/$PROJECT_NAME/health OK"
else
  echo "  ⚠  public health didn't return ok yet — may be DNS/cert propagation. Local was OK so the app is up."
  echo "     Test from your machine:  curl -v https://webhooks.c7a.io/$PROJECT_NAME/health"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✓ Webhooks deployed."
echo ""
echo "  Vapi tool Server URL → https://webhooks.c7a.io/$PROJECT_NAME/vapi/tool"
echo "  Logs (app):     ssh voicsito-vps 'journalctl -u $SYSTEMD_UNIT -f'"
echo "  Logs (nginx):   ssh voicsito-vps 'tail -f /var/log/nginx/webhooks.c7a.io.access.log'"
echo "════════════════════════════════════════════════════════════"
