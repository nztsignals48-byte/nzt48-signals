"""
N10c — Log Rotation Policy for AEGIS V2

Rotates all /var/log/*.log files inside the container.
Keeps last 7 days of logs, truncates current files after backup.
Runs daily at 04:45 UTC (before nightly learning at 04:50).

Usage:
    python3 -m python_brain.ouroboros.log_rotate
"""
import os
import glob
import shutil
import time
from datetime import datetime, timedelta, timezone

LOG_DIR = "/var/log"
ARCHIVE_DIR = "/var/log/archive"
MAX_AGE_DAYS = 7
MAX_FILE_SIZE_MB = 50  # Truncate immediately if a log exceeds this


def rotate_logs():
    """Rotate all .log files in LOG_DIR."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    rotated = 0
    truncated = 0

    for log_path in sorted(glob.glob(os.path.join(LOG_DIR, "*.log"))):
        basename = os.path.basename(log_path)
        size_mb = os.path.getsize(log_path) / (1024 * 1024)

        if size_mb < 0.001:
            continue  # Skip empty files

        # Archive: copy current log to archive/basename.YYYYMMDD
        archive_path = os.path.join(ARCHIVE_DIR, f"{basename}.{today}")
        if not os.path.exists(archive_path):
            try:
                shutil.copy2(log_path, archive_path)
                rotated += 1
            except OSError as e:
                print(f"ERROR: Failed to archive {basename}: {e}")
                continue

        # Truncate the active log file (don't delete — cron appends to it)
        try:
            with open(log_path, "w") as f:
                f.write(f"# Log rotated at {datetime.now(timezone.utc).isoformat()} UTC\n")
            truncated += 1
        except OSError as e:
            print(f"ERROR: Failed to truncate {basename}: {e}")

    # Purge old archives
    purged = purge_old_archives()

    print(
        f"N10c LOG ROTATE: {rotated} archived, {truncated} truncated, "
        f"{purged} old archives purged (max_age={MAX_AGE_DAYS}d)"
    )


def purge_old_archives():
    """Remove archive files older than MAX_AGE_DAYS."""
    cutoff = time.time() - (MAX_AGE_DAYS * 86400)
    purged = 0
    for path in glob.glob(os.path.join(ARCHIVE_DIR, "*.log.*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                purged += 1
        except OSError:
            pass
    return purged


if __name__ == "__main__":
    rotate_logs()
