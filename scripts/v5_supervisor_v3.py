"""V5 supervisor v3 — extends v2 registry with all Phase A-Q services.

Adds to existing v2 service list:
- adaptive_gate_chain (Phase G/H/I wiring)
- benchmark_streamer (Phase B)
- hedge_executor (Phase D)
- pit_snapshot (Phase E)
- venue_slippage_tracker (Phase K)
- cross_portfolio_halt (Phase L)
- llm_uplift_tracker (Phase F)
- llm_decision_audit (Phase O)
- best_execution_logger (Phase O)

Does NOT duplicate v2 services (no need — they continue to run).
Designed to launch alongside v2; or replace via env var.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
LOG = logging.getLogger("supervisor-v3")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PID_FILE = Path("/tmp/v5_supervisor_v3.pid")
_ANTHROPIC_ENV = {}
env_path = ROOT / ".env.anthropic"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _ANTHROPIC_ENV[k.strip()] = v.strip().strip('"')


@dataclass
class Service:
    name: str
    cmd: list
    verify_file: str = ""
    needs_nats: bool = True
    env_extra: dict = field(default_factory=dict)
    log_stale_max_s: float | None = 300.0
    restart_backoff_s: float = 10.0
    proc: subprocess.Popen | None = None

    def valid(self) -> tuple[bool, str]:
        if self.verify_file:
            p = ROOT / self.verify_file
            if not p.exists():
                return False, f"missing: {self.verify_file}"
        return True, "ok"

    def start(self):
        env = os.environ.copy()
        env.update(self.env_extra)
        env.setdefault("PYTHONPATH", str(ROOT))
        log_path = Path(f"/tmp/v5_{self.name}.log")
        try:
            f = open(log_path, "a")
            self.proc = subprocess.Popen(
                self.cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            LOG.info("started %s (pid=%d) log=%s", self.name, self.proc.pid, log_path)
        except Exception as e:
            LOG.error("failed to start %s: %s", self.name, e)

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None


def build_v3_additions() -> list[Service]:
    py = sys.executable
    return [
        Service("benchmark_streamer",
                cmd=[py, "-u", "python_brain/quant/benchmark_streamer.py"]),
        Service("adaptive_gate_chain",
                cmd=[py, "-u", "python_brain/engine/adaptive_gate_chain.py"]),
        Service("hedge_executor",
                cmd=[py, "-u", "python_brain/risk/hedge_executor.py"]),
        Service("pit_snapshot",
                cmd=[py, "-u", "python_brain/scanner/pit_snapshot.py"]),
        Service("venue_slippage_tracker",
                cmd=[py, "-u", "python_brain/execution/venue_slippage_tracker.py"]),
        Service("cross_portfolio_halt",
                cmd=[py, "-u", "python_brain/risk/cross_portfolio_halt.py"]),
        Service("llm_uplift_tracker",
                cmd=[py, "-u", "python_brain/intelligence/llm_uplift_tracker.py"],
                env_extra=dict(_ANTHROPIC_ENV)),
        Service("llm_decision_audit",
                cmd=[py, "-u", "python_brain/compliance/llm_decision_audit.py"]),
        Service("best_execution_logger",
                cmd=[py, "-u", "python_brain/compliance/best_execution_logger.py"]),
    ]


def acquire_pid_lock():
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            # Check if process alive
            try:
                os.kill(existing, 0)
                LOG.error("another supervisor-v3 alive (pid=%d)", existing)
                sys.exit(1)
            except OSError:
                pass
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()))


def release_pid_lock():
    try:
        PID_FILE.unlink()
    except Exception:
        pass


def main():
    acquire_pid_lock()
    LOG.info("V5 supervisor-v3 starting (pid=%d)", os.getpid())
    running = True

    def _sigterm(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services = build_v3_additions()
    valid_services = []
    for s in services:
        ok, reason = s.valid()
        if ok:
            valid_services.append(s)
        else:
            LOG.error("SKIP %s: %s", s.name, reason)

    LOG.info("registered %d services", len(valid_services))
    for s in valid_services:
        s.start()
        time.sleep(1.5)  # stagger startups

    try:
        while running:
            time.sleep(10)
            for s in valid_services:
                if not s.is_alive():
                    LOG.warning("service %s dead, restarting", s.name)
                    time.sleep(s.restart_backoff_s)
                    s.start()
    finally:
        for s in valid_services:
            if s.proc:
                try:
                    s.proc.terminate()
                except Exception:
                    pass
        release_pid_lock()


if __name__ == "__main__":
    main()
