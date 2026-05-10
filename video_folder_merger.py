\
#!/usr/bin/env python3

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "watch_dir": "/videos/incoming",
    "output_dir": "/videos/output",
    "archive_dir": "/videos/archive",
    "logs_dir": "/videos/logs",
    "ready_file_name": "READY",
    "auto_process_after_inactive_minutes": 10,
    "delete_work_files": True,
}

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mkv",
    ".avi",
    ".webm",
}


def load_config() -> Dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        config = {**DEFAULT_CONFIG, **user_config}
    else:
        config = DEFAULT_CONFIG.copy()
    return config


CONFIG = load_config()

WATCH_DIR = Path(CONFIG["watch_dir"])
OUTPUT_DIR = Path(CONFIG["output_dir"])
ARCHIVE_DIR = Path(CONFIG["archive_dir"])
LOGS_DIR = Path(CONFIG["logs_dir"])
READY_FILE_NAME = CONFIG["ready_file_name"]
AUTO_PROCESS_AFTER_SECONDS = int(CONFIG["auto_process_after_inactive_minutes"]) * 60
DELETE_WORK_FILES = bool(CONFIG["delete_work_files"])

for folder in [WATCH_DIR, OUTPUT_DIR, ARCHIVE_DIR, LOGS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=LOGS_DIR / "video-folder-merger.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger("").addHandler(console)


event_last_change: Dict[str, float] = {}


def safe_name(name: str) -> str:
    name = name.strip().replace(" ", "_")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def run_cmd(cmd: List[str]) -> subprocess.CompletedProcess:
    logging.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        logging.error("Command failed with rc=%s", result.returncode)
        logging.error("STDOUT: %s", result.stdout)
        logging.error("STDERR: %s", result.stderr)
        raise RuntimeError(result.stderr)
    return result


def wait_until_file_stable(path: Path, stable_seconds: int = 8, interval: int = 2) -> None:
    last_size = -1
    stable_for = 0

    while stable_for < stable_seconds:
        if not path.exists():
            raise FileNotFoundError(path)

        current_size = path.stat().st_size

        if current_size == last_size:
            stable_for += interval
        else:
            stable_for = 0
            last_size = current_size

        time.sleep(interval)


def event_folder_has_ready_file(event_dir: Path) -> bool:
    return (event_dir / READY_FILE_NAME).exists()


def collect_video_files(event_dir: Path) -> List[Path]:
    videos = [p for p in event_dir.iterdir() if is_video(p)]
    videos.sort(key=lambda p: p.name.lower())
    return videos


def normalize_clip(input_file: Path, output_file: Path) -> None:
    """
    Normalize every clip before concat so mixed camera formats do not break the merge.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
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
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_file),
    ]
    run_cmd(cmd)


def concat_clips(normalized_files: List[Path], output_file: Path) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
        concat_list = Path(f.name)
        for clip in normalized_files:
            f.write(f"file '{clip.resolve()}'\n")

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_file),
        ]
        run_cmd(cmd)
    finally:
        concat_list.unlink(missing_ok=True)


def archive_event_folder(event_dir: Path) -> None:
    destination = ARCHIVE_DIR / event_dir.name

    if destination.exists():
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        destination = ARCHIVE_DIR / f"{event_dir.name}_{timestamp}"

    shutil.move(str(event_dir), str(destination))
    logging.info("Archived event folder to: %s", destination)


def process_event_folder(event_dir: Path) -> None:
    if not event_dir.exists() or not event_dir.is_dir():
        return

    lock_file = event_dir / ".processing"
    if lock_file.exists():
        return

    lock_file.touch()

    try:
        logging.info("Processing event folder: %s", event_dir)

        videos = collect_video_files(event_dir)
        if not videos:
            logging.warning("No videos found in %s", event_dir)
            return

        for video in videos:
            logging.info("Waiting for stable file: %s", video)
            wait_until_file_stable(video)

        output_name = f"{safe_name(event_dir.name)}_merged.mp4"
        final_output = OUTPUT_DIR / output_name

        if final_output.exists():
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            final_output = OUTPUT_DIR / f"{safe_name(event_dir.name)}_merged_{timestamp}.mp4"

        work_dir = event_dir / ".work_normalized"
        work_dir.mkdir(exist_ok=True)

        normalized_files = []

        for index, video in enumerate(videos, start=1):
            normalized_file = work_dir / f"{index:04d}_{safe_name(video.stem)}.mp4"
            logging.info("Normalizing %s -> %s", video, normalized_file)
            normalize_clip(video, normalized_file)
            normalized_files.append(normalized_file)

        logging.info("Merging %s clips into %s", len(normalized_files), final_output)
        concat_clips(normalized_files, final_output)

        logging.info("Merged output created: %s", final_output)

        if DELETE_WORK_FILES:
            shutil.rmtree(work_dir, ignore_errors=True)

        archive_event_folder(event_dir)

    except Exception as exc:
        logging.exception("Failed to process %s: %s", event_dir, exc)
    finally:
        lock_file.unlink(missing_ok=True)


def scan_existing_event_folders() -> None:
    for event_dir in WATCH_DIR.iterdir():
        if event_dir.is_dir():
            event_last_change[str(event_dir)] = time.time()


def should_process_due_to_inactivity(event_dir: Path) -> bool:
    if AUTO_PROCESS_AFTER_SECONDS <= 0:
        return False

    last_change = event_last_change.get(str(event_dir))
    if last_change is None:
        event_last_change[str(event_dir)] = time.time()
        return False

    return (time.time() - last_change) >= AUTO_PROCESS_AFTER_SECONDS


class IncomingHandler(FileSystemEventHandler):
    def mark_event_folder_changed(self, path: Path) -> None:
        try:
            relative = path.relative_to(WATCH_DIR)
        except ValueError:
            return

        parts = relative.parts
        if not parts:
            return

        event_dir = WATCH_DIR / parts[0]
        if event_dir.is_dir():
            event_last_change[str(event_dir)] = time.time()
            logging.info("Change detected in event folder: %s", event_dir)

    def on_created(self, event):
        self.mark_event_folder_changed(Path(event.src_path))

    def on_modified(self, event):
        self.mark_event_folder_changed(Path(event.src_path))

    def on_moved(self, event):
        self.mark_event_folder_changed(Path(event.dest_path))


def monitor_loop() -> None:
    logging.info("Watching folder: %s", WATCH_DIR)
    logging.info("Output folder: %s", OUTPUT_DIR)

    scan_existing_event_folders()

    observer = Observer()
    observer.schedule(IncomingHandler(), str(WATCH_DIR), recursive=True)
    observer.start()

    try:
        while True:
            for event_dir in list(WATCH_DIR.iterdir()):
                if not event_dir.is_dir():
                    continue

                if (event_dir / ".processing").exists():
                    continue

                if event_folder_has_ready_file(event_dir):
                    logging.info("READY file found for %s", event_dir)
                    process_event_folder(event_dir)
                    event_last_change.pop(str(event_dir), None)
                    continue

                if should_process_due_to_inactivity(event_dir):
                    logging.info("Inactive timeout reached for %s", event_dir)
                    process_event_folder(event_dir)
                    event_last_change.pop(str(event_dir), None)

            time.sleep(10)

    except KeyboardInterrupt:
        logging.info("Stopping watcher.")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    monitor_loop()
