"""Watchdog entrypoint — Hetzner CX22. Phase 10.1 fills with real health-poll + KILL authority."""
from __future__ import annotations

import time


def main() -> int:
    print("watchdog scaffold — Phase 10.1 will wire NATS mirror + Telegram")
    while True:
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
