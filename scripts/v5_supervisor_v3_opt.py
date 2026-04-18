"""V5 supervisor extension: options_flow_signal service.

Consumes ticks.live.* → publishes signals.options_flow.
Integrates with adaptive gate chain (feeds into ensemble_entry via NATS).
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from v5_supervisor_v3 import Service

PID_FILE = Path("/tmp/v5_supervisor_v3_opt.pid")
LOG = logging.getLogger("supervisor-v3-opt")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_services() -> list[Service]:
    py = sys.executable
    return [
        Service("options_flow_signal",
                cmd=[py, "-u", "python_brain/quant/options_flow_signal.py"]),
    ]


def main():
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            try:
                os.kill(existing, 0)
                LOG.error("another v3-opt alive (pid=%d)", existing)
                sys.exit(1)
            except OSError:
                pass
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()))
    LOG.info("v5 supervisor v3-opt starting (pid=%d)", os.getpid())
    running = True

    def _sigterm(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services = build_services()
    for s in services:
        ok, reason = s.valid()
        if ok:
            s.start()
            time.sleep(1)
        else:
            LOG.error("SKIP %s: %s", s.name, reason)

    try:
        while running:
            time.sleep(10)
            for s in services:
                if not s.is_alive():
                    LOG.warning("%s dead, restarting", s.name)
                    time.sleep(s.restart_backoff_s)
                    s.start()
    finally:
        for s in services:
            if s.proc:
                try: s.proc.terminate()
                except Exception: pass
        try: PID_FILE.unlink()
        except Exception: pass


if __name__ == "__main__":
    main()
