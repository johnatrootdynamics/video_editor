#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/opus-hybrid-watcher"
SERVICE_FILE="/etc/systemd/system/opus-hybrid-watcher.service"

sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip

sudo mkdir -p "$APP_DIR"
sudo cp opus_hybrid_watcher.py requirements.txt .env.example "$APP_DIR/"

if [ ! -f "$APP_DIR/.env" ]; then
  sudo cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env. Edit it and add your OPUS_API_KEY."
fi

sudo python3 -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

sudo mkdir -p /videos/incoming /videos/work /videos/merged /videos/archive /videos/logs
sudo chmod -R 777 /videos

sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=OpusClip Hybrid Video Watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/opus_hybrid_watcher.py
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable opus-hybrid-watcher

echo "Installed. Before starting, edit: $APP_DIR/.env"
echo "Then run: sudo systemctl start opus-hybrid-watcher"
