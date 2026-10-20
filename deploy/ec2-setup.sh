#!/bin/bash
# Run this once on the EC2 instance after SSH-ing in.
# Usage: bash ec2-setup.sh
set -e

APP_DIR="$HOME/migration-dashboard"
DOMAIN="delivery-migration.now3.amagi.tv"

echo "=== 1. System packages ==="
sudo apt-get update -q
sudo apt-get install -y python3.11 python3.11-venv python3-pip git curl

echo "=== 2. Python venv ==="
cd "$APP_DIR"
python3.11 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo "=== 3. Data directory ==="
mkdir -p data

echo "=== 4. Systemd service ==="
sudo tee /etc/systemd/system/migration-dashboard.service > /dev/null << EOF
[Unit]
Description=AN3 Migration Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python cli.py serve
Restart=always
RestartSec=5
StandardOutput=append:$APP_DIR/data/server.log
StandardError=append:$APP_DIR/data/server.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable migration-dashboard
sudo systemctl start migration-dashboard
echo "Dashboard service: $(sudo systemctl is-active migration-dashboard)"

echo "=== 5. Caddy (HTTPS reverse proxy) ==="
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update -q
sudo apt-get install -y caddy

sudo tee /etc/caddy/Caddyfile > /dev/null << EOF
$DOMAIN {
    reverse_proxy 127.0.0.1:8790
    encode gzip
}
EOF

sudo systemctl enable caddy
sudo systemctl restart caddy
echo "Caddy service: $(sudo systemctl is-active caddy)"

echo ""
echo "=== Done ==="
echo "Dashboard running at: https://$DOMAIN"
echo "Logs: tail -f $APP_DIR/data/server.log"
