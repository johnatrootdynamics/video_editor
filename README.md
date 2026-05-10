# OpusClip Hybrid Folder Watcher

This watches a mounted video share, combines all clips from an event into one long video, uploads that merged video to OpusClip, and creates an OpusClip clipping project.

## Folder layout

```text
/videos/
  incoming/    # Drop event folders here
  work/        # Temporary normalized clips
  merged/      # Final merged long videos sent to OpusClip
  archive/     # Original event folders after successful upload
  logs/        # App logs
```

Recommended event folder format:

```text
/videos/incoming/drift-event-2026-05-10/
  clip001.mp4
  clip002.mov
  clip003.mp4
  READY
```

The `READY` file can be empty. It tells the watcher that all files have finished copying and the event can be processed immediately.

If you do not create a `READY` file, the watcher waits until the event folder has had no changes for `EVENT_IDLE_SECONDS`.

## Install

```bash
cd opus-hybrid-watcher
chmod +x install.sh
./install.sh
```

Edit config:

```bash
sudo nano /opt/opus-hybrid-watcher/.env
```

Required:

```bash
OPUS_API_KEY=your_api_key_here
```

Start it:

```bash
sudo systemctl start opus-hybrid-watcher
sudo systemctl status opus-hybrid-watcher
```

Watch logs:

```bash
sudo journalctl -u opus-hybrid-watcher -f
```

App log:

```bash
tail -f /videos/logs/opus_hybrid_watcher.log
```

## What it does

1. Watches `/videos/incoming`.
2. Treats each subfolder as one event.
3. Waits for a `READY` file or folder inactivity.
4. Normalizes all clips to MP4/H.264/AAC at 30fps.
5. Merges them into one long video.
6. Uploads that video to OpusClip using the OpusClip API.
7. Creates an OpusClip project.
8. Archives the original event folder.

## Why normalize first?

OpusClip local uploads have duration and file-size limits, and their docs note that non-integer frame rates are not currently supported. This script normalizes footage to a predictable format before upload.

## Config

See `.env.example`.

Common settings:

```bash
EVENT_IDLE_SECONDS=300
OPUS_TOPIC_KEYWORDS=drifting,racing,event highlights
OPUS_NOTIFY_EMAIL=you@example.com
OPUS_BRAND_TEMPLATE_ID=
```

## Manually test

```bash
cd /opt/opus-hybrid-watcher
source venv/bin/activate
python opus_hybrid_watcher.py
```

Then create an event folder:

```bash
mkdir -p /videos/incoming/test-event
cp /path/to/clips/*.mp4 /videos/incoming/test-event/
touch /videos/incoming/test-event/READY
```

## Notes

- Keep one event per subfolder.
- Do not dump unrelated events directly into `/videos/incoming`.
- The merged video remains in `/videos/merged`.
- The original event folder moves to `/videos/archive` after successful upload.
- Failed folders stay in `/videos/incoming` with a `.FAILED` file.
