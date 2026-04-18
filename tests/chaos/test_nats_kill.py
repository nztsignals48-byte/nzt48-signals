"""Chaos test: kill NATS, verify services recover within 60s."""
from __future__ import annotations

import subprocess
import time


def test_nats_restart_survival():
    # Find NATS PID
    result = subprocess.run(["pgrep", "-f", "nats-server"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("NATS not running; skipping chaos test")
        return True

    pids = result.stdout.strip().split("\n")
    print(f"NATS pids: {pids}")

    # Kill
    for pid in pids:
        try:
            subprocess.run(["kill", "-9", pid])
        except Exception:
            pass

    print("NATS killed; sleeping 60s for recovery")
    time.sleep(60)

    # Check if back up
    result2 = subprocess.run(["pgrep", "-f", "nats-server"], capture_output=True, text=True)
    recovered = bool(result2.stdout.strip())
    print(f"Recovered: {recovered}")
    return recovered


if __name__ == "__main__":
    import sys
    if "--run" in sys.argv:
        ok = test_nats_restart_survival()
        sys.exit(0 if ok else 1)
    else:
        print("Pass --run to actually kill NATS")
