#!/usr/bin/env python3
"""V5 Supervisor — the one process that must never die.

Keeps every V5 service alive. If one dies, restart it. If IB Gateway goes
down, re-launch it. Never claim a service is up without verifying with ps/lsof.

Run this with launchd so it starts on boot and auto-restarts itself.
"""
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("supervisor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler("/tmp/v5_supervisor.log"),
        logging.StreamHandler(),
    ],
)

ROOT = Path("/Users/rr/aegis-v5")
HEARTBEAT = Path("/tmp/v5_supervisor_heartbeat.json")
STATE_FILE = Path("/tmp/v5_supervisor_state.json")
IBGATEWAY_APP = Path("/Users/rr/Applications/IB Gateway 10.37/IB Gateway 10.37.app")


@dataclass
class Service:
    name: str
    cmd: list[str]
    cwd: Path = ROOT
    log_path: Path = field(default_factory=lambda: Path("/tmp"))
    needs_ibkr: bool = True        # service requires IBKR to be up
    needs_nats: bool = True        # service requires NATS to be up
    startup_delay_s: float = 1.0   # how long to wait after launch before checking
    restart_backoff_max_s: float = 60.0
    env_extra: dict[str, str] = field(default_factory=dict)

    # runtime
    proc: Optional[subprocess.Popen] = None
    last_start_ts: float = 0.0
    restart_count: int = 0
    backoff_s: float = 2.0

    @property
    def log_file(self) -> Path:
        return Path(f"/tmp/{self.name}.log")

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.is_alive():
            return
        env = {**os.environ, **self.env_extra}
        # Append to log instead of overwrite so we can trace restarts.
        with open(self.log_file, "a") as lf:
            lf.write(f"\n===== SUPERVISOR RESTART #{self.restart_count + 1} "
                     f"@ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            lf.flush()
            self.proc = subprocess.Popen(
                self.cmd,
                cwd=str(self.cwd),
                env=env,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        self.last_start_ts = time.time()
        self.restart_count += 1
        LOG.info("started %s pid=%d (attempt %d)",
                 self.name, self.proc.pid, self.restart_count)

    def stop(self) -> None:
        if not self.is_alive():
            return
        try:
            self.proc.terminate()
            time.sleep(1.5)
            if self.is_alive():
                self.proc.kill()
        except Exception as e:
            LOG.warning("stop %s: %s", self.name, e)


# ---------- Dependencies check
def nats_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8222/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ibkr_up() -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("127.0.0.1", 4002))
        s.close()
        return True
    except Exception:
        return False


def launch_nats() -> None:
    """Native NATS via homebrew binary."""
    if nats_up():
        return
    LOG.warning("NATS down — launching native")
    subprocess.Popen(
        ["/opt/homebrew/bin/nats-server", "--jetstream",
         "--http_port", "8222"],
        stdout=open("/tmp/nats.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def launch_ibgateway() -> None:
    """Open IB Gateway via macOS `open -a`."""
    if ibkr_up():
        return
    LOG.warning("IB Gateway down — launching via `open`")
    try:
        subprocess.Popen(
            ["open", "-a", str(IBGATEWAY_APP)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        LOG.info("IB Gateway launch issued. User may need to enter 2FA.")
    except Exception as e:
        LOG.error("failed to launch IB Gateway: %s", e)


# ---------- Service registry
def build_services() -> list[Service]:
    py = sys.executable
    return [
        # Data plane
        Service("rust_bridge",
                cmd=[str(ROOT / "rust_core/target/debug/aegis-engine")],
                needs_ibkr=True, needs_nats=True, startup_delay_s=8.0),

        # Scanner + rotator
        Service("ibkr_scanner_v2",
                cmd=[py, "-u", "python_brain/scanner/ibkr_scanner_v2.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        Service("universe_rotator_v2",
                cmd=[py, "-m", "python_brain.scanner.universe_rotator_v2"],
                needs_ibkr=False, needs_nats=True),

        # Order router
        Service("paper_executor",
                cmd=[py, "-u", "python_brain/engine/paper_executor.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        Service("signal_to_order",
                cmd=[py, "-m", "python_brain.engine.signal_to_order_bridge"],
                needs_ibkr=False, needs_nats=True),

        Service("exit_to_order",
                cmd=[py, "-m", "python_brain.engine.exit_to_order_bridge"],
                needs_ibkr=False, needs_nats=True),

        Service("broker_chandelier",
                cmd=[py, "-m", "python_brain.engine.broker_chandelier"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        # Signal/news pipeline
        Service("scanner_momentum_fast",
                cmd=[py, "-u", "python_brain/strategies/scanner_momentum_fast.py"],
                needs_ibkr=False, needs_nats=True),

        Service("news_reactor",
                cmd=[py, "-u", "python_brain/intelligence/ibkr_news_reactor.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        Service("llm_news_analyzer",
                cmd=[py, "-u", "python_brain/intelligence/llm_news_analyzer.py"],
                needs_ibkr=False, needs_nats=True,
                env_extra={"ANTHROPIC_API_KEY_FILE": str(ROOT / ".env.anthropic")}),

        Service("agent_swarm",
                cmd=[py, "-u", "python_brain/intelligence/agent_swarm.py"],
                needs_ibkr=False, needs_nats=True,
                env_extra={"ANTHROPIC_API_KEY_FILE": str(ROOT / ".env.anthropic")}),

        Service("news_to_intel",
                cmd=[py, "-u", "python_brain/intelligence/news_to_intel.py"],
                needs_ibkr=False, needs_nats=True),

        # Data maintenance
        Service("adaptive_intel_seeder",
                cmd=[py, "-u", "python_brain/scanner/adaptive_intel_seeder.py"],
                needs_ibkr=False, needs_nats=True),

        Service("nats_archiver",
                cmd=[py, "-u", "python_brain/infra/nats_archiver.py"],
                needs_ibkr=False, needs_nats=True),

        Service("arcticdb_ingester",
                cmd=[py, "-u", "python_brain/infra/arcticdb_ingester.py"],
                needs_ibkr=False, needs_nats=True),

        # Monitoring
        Service("compounding_tracker",
                cmd=[py, "-u", "python_brain/infra/compounding_tracker.py"],
                needs_ibkr=False, needs_nats=True),

        Service("metrics_feeder",
                cmd=[py, "-u", "python_brain/infra/metrics_feeder.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        Service("stuck_order_watchdog",
                cmd=[py, "-u", "python_brain/engine/stuck_order_watchdog.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0),

        Service("regime_amplifier",
                cmd=[py, "-u", "python_brain/quant/regime_amplifier.py"],
                needs_ibkr=False, needs_nats=True),

        Service("reentry_manager",
                cmd=[py, "-u", "python_brain/engine/reentry_manager.py"],
                needs_ibkr=False, needs_nats=True),

        Service("vpin_daemon",
                cmd=[py, "-u", "python_brain/quant/vpin_daemon.py"],
                needs_ibkr=False, needs_nats=True),

        # Engine runner
        Service("run_live_ibkr",
                cmd=[py, "-u", "scripts/run_live_ibkr.py"],
                needs_ibkr=False, needs_nats=True, startup_delay_s=5.0),
    ]


# ---------- Main loop
def main():
    LOG.info("V5 supervisor starting (pid=%d)", os.getpid())

    # Handle signals gracefully
    running = True
    def _sigterm(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services = build_services()
    svc_by_name = {s.name: s for s in services}

    # First, ensure dependencies
    launch_nats()
    time.sleep(2)
    launch_ibgateway()
    # Wait for IBKR to come up (max 120s)
    for i in range(60):
        if ibkr_up():
            LOG.info("IB Gateway up after %ds", i * 2)
            break
        time.sleep(2)
    else:
        LOG.warning("IB Gateway still not responding after 120s — services needing it will keep retrying")

    while running:
        # Dependency monitor
        if not nats_up():
            launch_nats()
            time.sleep(3)

        if not ibkr_up():
            LOG.warning("IB Gateway not reachable — attempting relaunch")
            launch_ibgateway()
            # Give Gateway 60s before hammering dependent services
            time.sleep(60)

        # Service supervisor
        state = {"ts": time.time(), "services": {}, "ibkr_up": ibkr_up(), "nats_up": nats_up()}
        for s in services:
            if not s.is_alive():
                if s.needs_nats and not nats_up():
                    continue
                if s.needs_ibkr and not ibkr_up():
                    continue
                # Exponential backoff
                since = time.time() - s.last_start_ts
                if since < s.backoff_s:
                    continue
                s.start()
                s.backoff_s = min(s.backoff_s * 1.5, s.restart_backoff_max_s)
                time.sleep(s.startup_delay_s)
            else:
                # Alive — reset backoff slowly
                s.backoff_s = max(2.0, s.backoff_s * 0.95)

            state["services"][s.name] = {
                "alive": s.is_alive(),
                "pid": s.proc.pid if s.proc else None,
                "restarts": s.restart_count,
                "backoff_s": round(s.backoff_s, 1),
            }

        # Heartbeat
        try:
            HEARTBEAT.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

        time.sleep(5)

    LOG.info("supervisor shutting down — stopping all services")
    for s in services:
        s.stop()


if __name__ == "__main__":
    main()
