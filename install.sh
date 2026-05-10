#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/video-folder-merger"

sudo apt update
sudo apt install -y python3 python3-venv ffmpeg

sudo mkdir -p "$APP_DIR"
sudo cp -r . "$APP_DIR/"

cd "$APP_DIR"

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f "$APP_DIR/config.json" ]; then
  sudo cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
fi

sudo mkdir -p /videos/incoming /videos/output /videos/archive /videos/logs
sudo chmod -R 777 /videos

sudo cp "$APP_DIR/video-folder-merger.service" /etc/systemd/system/video-folder-merger.service
sudo systemctl daemon-reload
sudo systemctl enable video-folder-merger
sudo systemctl restart video-folder-merger

echo "Installed and started video-folder-merger."
echo "Logs: sudo journalctl -u video-folder-merger -f"
