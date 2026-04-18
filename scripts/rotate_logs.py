"""Log rotation helper — trims /tmp/v5_*.log files.

Run periodically (cron / supervisor Tier 3). For any v5_*.log larger than
MAX_MB, rotate to v5_*.log.1 and truncate active log to last KEEP_LINES.

Keeps disk usage bounded without stopping services.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


LOG_DIR = Path("/tmp")
MAX_MB = 10  # rotate if larger than this
KEEP_LINES = 500  # lines to preserve in new active log
MAX_ROTATIONS = 3  # keep .log, .log.1, .log.2, .log.3


def rotate(log_path: Path):
    if not log_path.exists():
        return 0
    if log_path.stat().st_size <= MAX_MB * 1024 * 1024:
        return 0

    # Preserve last KEEP_LINES
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # Read last ~2MB max
            f.seek(max(0, size - 2 * 1024 * 1024))
            tail = f.read()
        # Last KEEP_LINES
        lines = tail.decode(errors="replace").split("\n")
        last = "\n".join(lines[-KEEP_LINES:])
    except Exception:
        last = ""

    # Shift rotations
    for i in range(MAX_ROTATIONS - 1, 0, -1):
        src = log_path.with_suffix(log_path.suffix + f".{i}")
        dst = log_path.with_suffix(log_path.suffix + f".{i + 1}")
        if src.exists():
            try:
                shutil.move(str(src), str(dst))
            except Exception:
                pass

    # Active → .1
    rot1 = log_path.with_suffix(log_path.suffix + ".1")
    try:
        shutil.move(str(log_path), str(rot1))
    except Exception:
        pass

    # Write last KEEP_LINES back to active log
    log_path.write_text(last + "\n[--- rotated ---]\n")

    return 1


def main():
    rotated = 0
    for log in LOG_DIR.glob("v5_*.log"):
        n = rotate(log)
        rotated += n
        if n:
            print(f"rotated {log}")
    print(f"done: {rotated} files rotated")


if __name__ == "__main__":
    main()
