"""Master supervisor extension — registers newly-wired services built this session.

Runs alongside v5_master_supervisor.py. Services:
  - gmm_regime_daemon         — publishes regime.gmm every 60s
  - sector_regime_daemon       — publishes regime.sector.{ETF} per-sector
  - super_institutional_gate   — applies all Phase 2-4 gates
  - venue_slippage_tracker     — measures IS per venue/order-type

Use this + master supervisor together for full coverage.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V5_ROOT", "/Users/rr/aegis-v5"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from v5_master_supervisor import Service

PID_FILE = Path("/tmp/v5_master_supervisor_ext.pid")
LOG = logging.getLogger("master-sup-ext")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_services() -> list[Service]:
    py = sys.executable
    return [
        Service("gmm_regime_daemon",
                cmd=[py, "-u", "python_brain/quant/gmm_regime_daemon.py"],
                tier=2),
        Service("sector_regime_daemon",
                cmd=[py, "-u", "python_brain/quant/sector_regime_daemon.py"],
                tier=2),
        Service("super_institutional_gate",
                cmd=[py, "-u", "python_brain/engine/super_institutional_gate.py"],
                tier=2),
        Service("venue_slippage_tracker",
                cmd=[py, "-u", "python_brain/execution/venue_slippage_tracker.py"],
                tier=2),
    ]


def acquire_lock():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            try:
                os.kill(pid, 0)
                LOG.error("another master-ext alive (pid=%d)", pid)
                sys.exit(1)
            except OSError:
                pass
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()))


def main():
    acquire_lock()
    LOG.info("master supervisor ext starting (pid=%d)", os.getpid())
    running = True

    def _sigterm(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services = build_services()
    valid = []
    for s in services:
        ok, reason = s.valid()
        if ok:
            valid.append(s)
            s.start()
            time.sleep(1)
        else:
            LOG.error("SKIP %s: %s", s.name, reason)

    try:
        while running:
            time.sleep(10)
            for s in valid:
                if not s.is_alive():
                    LOG.warning("%s dead (attempts=%d), restarting", s.name, s.attempts)
                    time.sleep(s.restart_backoff_s)
                    s.start()
    finally:
        for s in valid:
            s.stop()
        try: PID_FILE.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
