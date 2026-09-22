#!/bin/sh
# ────────────────────────────────────────────────────────────────────
# Deploy the Ashley webhook to the shared webhooks.c7a.io infra.
#
# Layout on the VPS:
#   /opt/webhooks/ashley/
#     ├── src/blooio/*.py       ← Blooio client + group logic
#     ├── src/webhook/server.py ← FastAPI app
#     ├── assets/group-icon.png ← pre-rendered icon (no browser needed here)
#     ├── .env                  ← BLOOIO_API_KEY + ASHLEY_WEBHOOK_SECRET
#     └── venv/
#
# Public URL:  https://webhooks.c7a.io/ashley/group/setup
# Local port:  8082
# nginx's proxy_pass trailing slash strips the /ashley prefix, so the app
# serves /group/setup, not /ashley/group/setup.
# ────────────────────────────────────────────────────────────────────

set -e

ASHLEY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VPS_HOST="${VPS_HOST:-root@voicsito-vps}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/voicsito_vps}"

PROJECT_NAME="ashley"
PROJECT_PORT=8082
VPS_BASE="/opt/webhooks/$PROJECT_NAME"
VPS_VENV="$VPS_BASE/venv"
SYSTEMD_UNIT="ashley-webhooks"
NGINX_SITE_FILE="/etc/nginx/sites.d/webhooks/$PROJECT_NAME.conf"

echo "════════════════════════════════════════════════════════════"
echo "  Ashley Webhook Deploy"
echo "  → https://webhooks.c7a.io/$PROJECT_NAME/group/setup"
echo "════════════════════════════════════════════════════════════"

echo ""
echo "[1/7] Tests + syntax..."
if [ -x "$ASHLEY_DIR/../.venv-tests/bin/python" ]; then
  (cd "$ASHLEY_DIR/.." && .venv-tests/bin/python -m pytest ashley/tests -q) \
    || { echo "  ✗ tests failed, aborting"; exit 1; }
fi
for f in "$ASHLEY_DIR"/src/blooio/*.py "$ASHLEY_DIR"/src/webhook/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" \
    || { echo "  ✗ $f — syntax error"; exit 1; }
done
echo "  ✓ tests + syntax OK"

echo ""
echo "[2/7] Verifying shared infra..."
INFRA_OK=$(ssh -i "$SSH_KEY" "$VPS_HOST" "test -d /etc/nginx/sites.d/webhooks && systemctl is-active --quiet nginx && echo yes || echo no")
[ "$INFRA_OK" = "yes" ] || { echo "  ✗ shared infra not ready"; exit 1; }
echo "  ✓ nginx + sites.d ready"

echo ""
echo "[3/7] Preparing remote directories..."
ssh -i "$SSH_KEY" "$VPS_HOST" "mkdir -p $VPS_BASE/src/blooio $VPS_BASE/src/webhook $VPS_BASE/assets"

echo ""
echo "[4/7] Uploading source + assets..."
scp -i "$SSH_KEY" -q "$ASHLEY_DIR"/src/blooio/*.py "$VPS_HOST:$VPS_BASE/src/blooio/"
scp -i "$SSH_KEY" -q "$ASHLEY_DIR"/src/webhook/*.py "$VPS_HOST:$VPS_BASE/src/webhook/"
scp -i "$SSH_KEY" -q "$ASHLEY_DIR"/assets/group-icon.png "$VPS_HOST:$VPS_BASE/assets/"
scp -i "$SSH_KEY" -q "$ASHLEY_DIR"/requirements.txt "$VPS_HOST:$VPS_BASE/"
echo "  ✓ uploaded"

# .env on first deploy only — never clobber VPS-side edits
ssh -i "$SSH_KEY" "$VPS_HOST" "test -f $VPS_BASE/.env" || {
  if [ -f "$ASHLEY_DIR/config/.env" ]; then
    scp -i "$SSH_KEY" -q "$ASHLEY_DIR/config/.env" "$VPS_HOST:$VPS_BASE/.env"
    ssh -i "$SSH_KEY" "$VPS_HOST" "chmod 600 $VPS_BASE/.env"
    echo "  ✓ initial .env uploaded (chmod 600)"
  else
    echo "  ⚠  no local ashley/config/.env — VPS needs one at $VPS_BASE/.env"
  fi
}

echo ""
echo "[5/7] Python venv + deps..."
ssh -i "$SSH_KEY" "$VPS_HOST" "
  [ -d $VPS_VENV ] || python3 -m venv $VPS_VENV
  $VPS_VENV/bin/pip install --quiet --upgrade pip
  $VPS_VENV/bin/pip install --quiet -r $VPS_BASE/requirements.txt
"
echo "  ✓ deps installed"

echo ""
echo "[6/7] systemd unit + nginx site..."
ssh -i "$SSH_KEY" "$VPS_HOST" "cat > /etc/systemd/system/$SYSTEMD_UNIT.service" <<EOF
[Unit]
Description=Ashley Blooio Webhook (FastAPI on shared webhooks.c7a.io infra)
After=network.target

[Service]
Type=simple
WorkingDirectory=$VPS_BASE
EnvironmentFile=$VPS_BASE/.env
ExecStart=$VPS_VENV/bin/uvicorn webhook.server:app --host 127.0.0.1 --port $PROJECT_PORT --app-dir $VPS_BASE/src
Restart=always
RestartSec=3
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

ssh -i "$SSH_KEY" "$VPS_HOST" "cat > $NGINX_SITE_FILE" <<EOF
# Ashley Blooio webhook (managed by ashley/scripts/deploy.sh)
# Public path: /$PROJECT_NAME/* → http://127.0.0.1:$PROJECT_PORT/*

location /$PROJECT_NAME/ {
    proxy_pass http://127.0.0.1:$PROJECT_PORT/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix /$PROJECT_NAME;
    proxy_read_timeout 120s;
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

echo ""
echo "[7/7] Health check..."
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
  echo "  ⚠  public health not ok yet (local was OK, so the app is up)"
fi

echo ""
echo "Webhook URL for the GHL automation:"
echo "  POST https://webhooks.c7a.io/$PROJECT_NAME/group/setup"
echo "  Header: X-Ashley-Secret: <ASHLEY_WEBHOOK_SECRET from .env>"
echo "  Body:   {\"phone\": \"{{contact.phone}}\", \"business_name\": \"{{contact.company_name}}\"}"
