#!/usr/bin/env python3

"""
OpusClip Hybrid Watcher

Workflow:
1. Watch /videos/incoming
2. Treat each subfolder as one event
3. Wait until READY file exists or folder has been idle
4. Normalize all clips to Opus-friendly MP4
5. Merge clips into one long event video
6. Upload merged video to OpusClip API
7. Create OpusClip project
8. Archive original event folder

Recommended drop folder structure:

/videos/incoming/event-name-001/
  clip1.mp4
  clip2.mov
  clip3.mp4
  READY

The READY file can be empty. It tells the watcher, "all clips are copied, process now."
"""

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

load_dotenv()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}

OPUS_API_BASE = "https://api.opus.pro/api"

OPUS_API_KEY = os.getenv("OPUS_API_KEY", "").strip()
OPUS_NOTIFY_EMAIL = os.getenv("OPUS_NOTIFY_EMAIL", "").strip()
OPUS_BRAND_TEMPLATE_ID = os.getenv("OPUS_BRAND_TEMPLATE_ID", "").strip()
OPUS_TOPIC_KEYWORDS = [
    item.strip()
    for item in os.getenv("OPUS_TOPIC_KEYWORDS", "").split(",")
    if item.strip()
]
OPUS_SOURCE_LANG = os.getenv("OPUS_SOURCE_LANG", "auto").strip() or "auto"

WATCH_DIR = Path(os.getenv("WATCH_DIR", "/videos/incoming"))
WORK_DIR = Path(os.getenv("WORK_DIR", "/videos/work"))
MERGED_DIR = Path(os.getenv("MERGED_DIR", "/videos/merged"))
ARCHIVE_DIR = Path(os.getenv("ARCHIVE_DIR", "/videos/archive"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/videos/logs"))

EVENT_IDLE_SECONDS = int(os.getenv("EVENT_IDLE_SECONDS", "300"))
SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
MAX_VIDEO_HOURS = float(os.getenv("MAX_VIDEO_HOURS", "10"))
MAX_VIDEO_GB = float(os.getenv("MAX_VIDEO_GB", "30"))

PROCESSING_MARKER = ".PROCESSING"
DONE_MARKER = ".DONE"
FAILED_MARKER = ".FAILED"
READY_MARKER = "READY"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_DIR / "opus_hybrid_watcher.log",
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(console)


def ensure_dirs() -> None:
    for directory in [WATCH_DIR, WORK_DIR, MERGED_DIR, ARCHIVE_DIR, LOG_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    logging.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode != 0:
        logging.error("Command failed. STDOUT=%s STDERR=%s", result.stdout, result.stderr)
        raise RuntimeError(result.stderr)
    return result


def safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in name.strip())
    return cleaned.strip("._") or "event"


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def get_event_dirs() -> List[Path]:
    return sorted([p for p in WATCH_DIR.iterdir() if p.is_dir()])


def event_has_marker(event_dir: Path, marker: str) -> bool:
    return (event_dir / marker).exists()


def touch_marker(event_dir: Path, marker: str, content: str = "") -> None:
    (event_dir / marker).write_text(content)


def get_video_files(event_dir: Path) -> List[Path]:
    return sorted([p for p in event_dir.rglob("*") if is_video(p)])


def folder_latest_mtime(event_dir: Path) -> float:
    latest = event_dir.stat().st_mtime
    for path in event_dir.rglob("*"):
        try:
            latest = max(latest, path.stat().st_mtime)
        except FileNotFoundError:
            continue
    return latest


def should_process_event(event_dir: Path) -> Tuple[bool, str]:
    if event_has_marker(event_dir, DONE_MARKER):
        return False, "already done"
    if event_has_marker(event_dir, FAILED_MARKER):
        return False, "previously failed"
    if event_has_marker(event_dir, PROCESSING_MARKER):
        return False, "already processing"

    videos = get_video_files(event_dir)
    if not videos:
        return False, "no video files"

    if event_has_marker(event_dir, READY_MARKER):
        return True, "READY marker found"

    idle_for = time.time() - folder_latest_mtime(event_dir)
    if idle_for >= EVENT_IDLE_SECONDS:
        return True, f"idle for {int(idle_for)} seconds"

    return False, f"not idle long enough: {int(idle_for)} seconds"


def get_duration_seconds(path: Path) -> float:
    result = run_cmd([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ])
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def normalize_clip(input_path: Path, output_path: Path) -> None:
    """
    Normalize clips to an Opus-friendly format:
    - MP4 container
    - H.264 video
    - AAC audio
    - integer 30 fps to avoid non-integer frame-rate issues
    - yuv420p pixel format for compatibility
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_cmd([
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        "scale='min(1920,iw)':-2,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output_path),
    ])


def merge_clips(normalized_files: List[Path], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    concat_file = output_file.with_suffix(".concat.txt")

    with concat_file.open("w") as f:
        for clip in normalized_files:
            f.write(f"file '{clip.resolve()}'\n")

    run_cmd([
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_file),
    ])

    concat_file.unlink(missing_ok=True)


def validate_video_for_opus(path: Path) -> None:
    duration_hours = get_duration_seconds(path) / 3600
    size_gb = path.stat().st_size / (1024 ** 3)

    if duration_hours > MAX_VIDEO_HOURS:
        raise ValueError(f"Merged video is {duration_hours:.2f} hours, over limit of {MAX_VIDEO_HOURS} hours")
    if size_gb > MAX_VIDEO_GB:
        raise ValueError(f"Merged video is {size_gb:.2f} GB, over limit of {MAX_VIDEO_GB} GB")


def opus_headers() -> Dict[str, str]:
    if not OPUS_API_KEY:
        raise RuntimeError("Missing OPUS_API_KEY. Put it in /opt/opus-hybrid-watcher/.env")
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPUS_API_KEY}",
    }


def opus_generate_upload_link() -> Dict:
    response = requests.post(
        f"{OPUS_API_BASE}/upload-links",
        headers=opus_headers(),
        json={"video": {"usecase": "LocalUpload"}},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def opus_start_resumable_upload(upload_url: str) -> str:
    response = requests.post(
        upload_url,
        headers={"x-goog-resumable": "start", "Content-Length": "0"},
        timeout=60,
    )
    response.raise_for_status()
    location = response.headers.get("location")
    if not location:
        raise RuntimeError("Opus/GCS did not return resumable upload location header")
    return location


def opus_upload_file(upload_location: str, video_file: Path) -> None:
    with video_file.open("rb") as f:
        response = requests.put(
            upload_location,
            headers={"Content-Type": "application/octet-stream"},
            data=f,
            timeout=None,
        )
    response.raise_for_status()


def opus_create_clip_project(upload_id: str, event_name: str) -> Dict:
    payload = {
        "videoUrl": upload_id,
        "curationPref": {
            "clipDurations": [[0, 90]],
            "genre": "Auto",
            "skipCurate": False,
        },
        "importPref": {
            "sourceLang": OPUS_SOURCE_LANG,
        },
    }

    if OPUS_TOPIC_KEYWORDS:
        payload["curationPref"]["topicKeywords"] = OPUS_TOPIC_KEYWORDS

    if OPUS_NOTIFY_EMAIL:
        payload["conclusionActions"] = [
            {
                "type": "EMAIL",
                "notifyFailure": True,
                "email": OPUS_NOTIFY_EMAIL,
            }
        ]

    if OPUS_BRAND_TEMPLATE_ID:
        payload["brandTemplateId"] = OPUS_BRAND_TEMPLATE_ID

    response = requests.post(
        f"{OPUS_API_BASE}/clip-projects",
        headers=opus_headers(),
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json() if response.text.strip() else {}
    data["eventName"] = event_name
    data["uploadId"] = upload_id
    return data


def upload_merged_video_to_opus(video_file: Path, event_name: str) -> Dict:
    logging.info("Generating Opus upload link")
    link_data = opus_generate_upload_link()
    upload_url = link_data["url"]
    upload_id = link_data["uploadId"]

    logging.info("Starting resumable upload session")
    upload_location = opus_start_resumable_upload(upload_url)

    logging.info("Uploading merged video to Opus: %s", video_file)
    opus_upload_file(upload_location, video_file)

    logging.info("Creating Opus clip project")
    return opus_create_clip_project(upload_id, event_name)


def archive_event(event_dir: Path, job_name: str) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / job_name
    counter = 1
    while archive_path.exists():
        archive_path = ARCHIVE_DIR / f"{job_name}_{counter}"
        counter += 1
    shutil.move(str(event_dir), str(archive_path))
    return archive_path


def process_event(event_dir: Path) -> None:
    event_name = safe_name(event_dir.name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    job_name = f"{timestamp}_{event_name}"
    job_work_dir = WORK_DIR / job_name
    normalized_dir = job_work_dir / "normalized"
    result_file = job_work_dir / "opus_project.json"

    logging.info("Starting event: %s", event_dir)
    touch_marker(event_dir, PROCESSING_MARKER, datetime.now().isoformat())

    try:
        clips = get_video_files(event_dir)
        if not clips:
            raise RuntimeError("No video clips found")

        normalized_files: List[Path] = []
        for index, clip in enumerate(clips, start=1):
            output = normalized_dir / f"{index:04d}_{safe_name(clip.stem)}.mp4"
            logging.info("Normalizing clip %s/%s: %s", index, len(clips), clip)
            normalize_clip(clip, output)
            normalized_files.append(output)

        merged_file = MERGED_DIR / f"{job_name}_merged_for_opus.mp4"
        logging.info("Merging %s clips into %s", len(normalized_files), merged_file)
        merge_clips(normalized_files, merged_file)

        validate_video_for_opus(merged_file)

        opus_result = upload_merged_video_to_opus(merged_file, event_name)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(json.dumps(opus_result, indent=2))

        touch_marker(event_dir, DONE_MARKER, json.dumps({
            "completedAt": datetime.now().isoformat(),
            "mergedFile": str(merged_file),
            "opusResult": opus_result,
        }, indent=2))

        archived = archive_event(event_dir, job_name)
        logging.info("Event complete. Archived to %s", archived)

    except Exception as exc:
        logging.exception("Event failed: %s", event_dir)
        touch_marker(event_dir, FAILED_MARKER, str(exc))
        processing = event_dir / PROCESSING_MARKER
        processing.unlink(missing_ok=True)


def scan_once() -> None:
    for event_dir in get_event_dirs():
        process, reason = should_process_event(event_dir)
        logging.info("Event check: %s | process=%s | reason=%s", event_dir.name, process, reason)
        if process:
            process_event(event_dir)


class ChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        # We intentionally do not process immediately. The main loop handles
        # debounce/idleness so partially copied files do not get processed.
        logging.debug("Filesystem event: %s", event)


def main() -> None:
    setup_logging()
    ensure_dirs()

    logging.info("Opus Hybrid Watcher starting")
    logging.info("Watching %s", WATCH_DIR)

    observer = Observer()
    observer.schedule(ChangeHandler(), str(WATCH_DIR), recursive=True)
    observer.start()

    try:
        while True:
            scan_once()
            time.sleep(SCAN_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Stopping watcher")
        observer.stop()
    finally:
        observer.join()


if __name__ == "__main__":
    main()
