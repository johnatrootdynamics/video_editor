# Video Folder Merger

This watches `/videos/incoming` on an Ubuntu machine and merges each event folder into one long MP4.

No AI upload. No Opus API. No browser automation.

## Folder layout

```text
/videos/
  incoming/
    event-name-1/
      clip1.mp4
      clip2.mov
      READY
  output/
    event-name-1_merged.mp4
  archive/
    event-name-1/
  logs/
    video-folder-merger.log
```

## How it works

Each subfolder inside `/videos/incoming` is treated as one event.

The script processes an event when either:

1. A file named `READY` exists in the event folder, or
2. No files have changed in that event folder for the configured inactive time.

Default inactive time is 10 minutes.

## Install

```bash
sudo apt update
sudo apt install -y python3 python3-venv ffmpeg

sudo mkdir -p /opt/video-folder-merger
sudo cp -r . /opt/video-folder-merger/

cd /opt/video-folder-merger
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

sudo mkdir -p /videos/incoming /videos/output /videos/archive /videos/logs
sudo chmod -R 777 /videos
```

## Configure

```bash
cp config.example.json config.json
nano config.json
```

Most likely you can leave the defaults.

## Run manually

```bash
cd /opt/video-folder-merger
./venv/bin/python video_folder_merger.py
```

## Install as a service

```bash
sudo cp video-folder-merger.service /etc/systemd/system/video-folder-merger.service
sudo systemctl daemon-reload
sudo systemctl enable video-folder-merger
sudo systemctl start video-folder-merger
```

## Check logs

```bash
sudo journalctl -u video-folder-merger -f
tail -f /videos/logs/video-folder-merger.log
```

## Usage

Create one folder per event:

```bash
mkdir -p /videos/incoming/event-2026-05-10
cp *.mp4 /videos/incoming/event-2026-05-10/
touch /videos/incoming/event-2026-05-10/READY
```

The finished file will be placed in:

```text
/videos/output/event-2026-05-10_merged.mp4
```

The original event folder will be moved to:

```text
/videos/archive/event-2026-05-10
```
