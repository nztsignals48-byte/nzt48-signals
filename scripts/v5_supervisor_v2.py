#!/usr/bin/env python3
"""V5 Supervisor v2 — clean build with all consolidated services + Gateway healer.

Improvements over v1:
- All file paths validated to exist on disk before registration
- PID-file lock so only one supervisor runs at a time
- Orphan cleanup at startup (kills children from crashed prior runs)
- 30s+ backoff on IBKR-dependent services (prevents Gateway thrash)
- Serialized IBKR spawns (one per 10s window)
- Liveness probe via log-file mtime
- Anthropic env file auto-loaded into LLM service envs
- Gateway healer auto-relaunches Gateway if port dies

Consolidated services (11 IBKR clients → 7):
- portfolio_streamer replaces broker_chandelier + account_streamer + metrics_feeder
- paper_executor now handles kill_switch + stuck_order_watchdog inline
- VPIN + bar_builder_5s + gateway_healer added
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
PIDFILE = Path("/tmp/v5_supervisor.pid")
IBGATEWAY_APP = Path("/Users/rr/Applications/IB Gateway 10.37/IB Gateway 10.37.app")


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


_ANTHROPIC_ENV = load_env_file(ROOT / ".env.anthropic")


def acquire_pid_lock() -> None:
    if PIDFILE.exists():
        try:
            other = int(PIDFILE.read_text().strip())
            os.kill(other, 0)
            print(f"ERROR: supervisor already running pid={other}", file=sys.stderr)
            sys.exit(1)
        except (ValueError, ProcessLookupError):
            pass
        except PermissionError:
            print(f"ERROR: pid {other} exists (perm denied)", file=sys.stderr)
            sys.exit(1)
    PIDFILE.write_text(str(os.getpid()))


def release_pid_lock() -> None:
    try:
        if PIDFILE.exists() and PIDFILE.read_text().strip() == str(os.getpid()):
            PIDFILE.unlink()
    except Exception:
        pass


@dataclass
class Service:
    name: str
    cmd: list[str]
    verify_file: Optional[str] = None
    cwd: Path = ROOT
    needs_ibkr: bool = True
    needs_nats: bool = True
    startup_delay_s: float = 1.0
    restart_backoff_initial_s: float = 2.0
    restart_backoff_max_s: float = 60.0
    env_extra: dict[str, str] = field(default_factory=dict)
    log_stale_max_s: Optional[float] = 600.0
    liveness_grace_s: float = 60.0

    proc: Optional[subprocess.Popen] = None
    last_start_ts: float = 0.0
    restart_count: int = 0
    backoff_s: float = 2.0
    last_stale_kill_ts: float = 0.0

    def __post_init__(self):
        if self.backoff_s == 2.0 and self.restart_backoff_initial_s != 2.0:
            self.backoff_s = self.restart_backoff_initial_s

    def valid(self) -> tuple[bool, str]:
        if self.verify_file:
            p = ROOT / self.verify_file
            if not p.exists():
                return False, f"file not found: {p}"
        else:
            for a in self.cmd[1:]:
                if a.startswith("-"):
                    continue
                if a.endswith(".py"):
                    p = (ROOT / a) if not os.path.isabs(a) else Path(a)
                    if not p.exists():
                        return False, f"file not found: {p}"
                    break
        return True, "ok"

    @property
    def log_file(self) -> Path:
        return Path(f"/tmp/{self.name}.log")

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def is_stuck(self) -> tuple[bool, str]:
        if not self.is_alive() or self.log_stale_max_s is None:
            return False, ""
        now = time.time()
        if now - self.last_start_ts < self.liveness_grace_s:
            return False, ""
        if not self.log_file.exists():
            return False, ""
        age = now - self.log_file.stat().st_mtime
        if age > self.log_stale_max_s:
            return True, f"log silent {age:.0f}s (>{self.log_stale_max_s:.0f}s)"
        return False, ""

    def start(self) -> None:
        if self.is_alive():
            return
        env = {**os.environ, **self.env_extra, "PYTHONPATH": str(ROOT)}
        with open(self.log_file, "a") as lf:
            lf.write(f"\n===== SUPERVISOR RESTART #{self.restart_count + 1} @ "
                     f"{time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
            lf.flush()
            self.proc = subprocess.Popen(
                self.cmd, cwd=str(self.cwd), env=env,
                stdout=lf, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
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
    if nats_up():
        return
    LOG.warning("NATS down — launching native")
    subprocess.Popen(
        ["/opt/homebrew/bin/nats-server", "--jetstream", "--http_port", "8222"],
        stdout=open("/tmp/nats.log", "a"),
        stderr=subprocess.STDOUT, start_new_session=True,
    )


def launch_ibgateway() -> None:
    if ibkr_up():
        return
    LOG.warning("IB Gateway down — issuing `open -a`")
    try:
        subprocess.Popen(
            ["open", "-a", str(IBGATEWAY_APP)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        LOG.error("Gateway relaunch failed: %s", e)


def kill_orphans(services: list["Service"]) -> None:
    """Kill children matching our services from a previous supervisor run."""
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,command"], text=True,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        return
    me = os.getpid()
    identifiers = []
    for s in services:
        for a in reversed(s.cmd):
            if a.endswith(".py") or a.endswith("aegis-engine"):
                identifiers.append((s.name, a))
                break
            if "." in a and "/" not in a and not a.startswith("-"):
                identifiers.append((s.name, a))
                break
    killed = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        if pid == me:
            continue
        for svc_name, ident in identifiers:
            if ident in cmd and "grep" not in cmd and "v5_supervisor" not in cmd:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed.append((pid, svc_name))
                except Exception:
                    pass
                break
    if killed:
        LOG.info("killed %d orphans: %s", len(killed), killed[:10])
        time.sleep(3)


def build_services() -> list[Service]:
    py = sys.executable
    return [
        # ---- Data plane ---------------------------------------------------
        Service("rust_bridge",
                cmd=[str(ROOT / "rust_core/target/debug/aegis-engine")],
                verify_file="rust_core/target/debug/aegis-engine",
                needs_ibkr=True, needs_nats=True, startup_delay_s=8.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0),

        Service("ibkr_scanner_v2",
                cmd=[py, "-u", "python_brain/scanner/ibkr_scanner_v2.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0),

        # Delayed-data streamer — rotates through full universe snapshots
        Service("delayed_universe_streamer",
                cmd=[py, "-u", "python_brain/scanner/delayed_universe_streamer.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=5.0,
                log_stale_max_s=900.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0),

        Service("universe_rotator_v2",
                cmd=[py, "-m", "python_brain.scanner.universe_rotator_v2"],
                verify_file="python_brain/scanner/universe_rotator_v2.py",
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=1200.0),

        # ---- Order router -------------------------------------------------
        # paper_executor now includes kill_switch + stuck_order_watchdog inline
        Service("paper_executor",
                cmd=[py, "-u", "python_brain/engine/paper_executor.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0),

        Service("signal_to_order",
                cmd=[py, "-m", "python_brain.engine.signal_to_order_bridge"],
                verify_file="python_brain/engine/signal_to_order_bridge.py",
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        Service("exit_to_order",
                cmd=[py, "-m", "python_brain.engine.exit_to_order_bridge"],
                verify_file="python_brain/engine/exit_to_order_bridge.py",
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        # ---- CONSOLIDATED portfolio_streamer (cid=118) -------------------
        # Replaces broker_chandelier + account_streamer + metrics_feeder.
        Service("portfolio_streamer",
                cmd=[py, "-u", "python_brain/engine/portfolio_streamer.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=5.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0,
                log_stale_max_s=900.0),

        # ---- Indicators ---------------------------------------------------
        Service("indicator_framer",
                cmd=[py, "-u", "python_brain/engine/indicator_framer.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=900.0),

        Service("scanner_momentum_fast",
                cmd=[py, "-u", "python_brain/strategies/scanner_momentum_fast.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        # news_alpha_trader — closes LLM→trade loop. LLM scores QQQ/SPY/major
        # names, this service converts strong deltas into tradeable signals.
        Service("news_alpha_trader",
                cmd=[py, "-u", "python_brain/strategies/news_alpha_trader.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        # ---- News / LLM ---------------------------------------------------
        Service("news_reactor",
                cmd=[py, "-u", "python_brain/news/ibkr_news_reactor.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=3.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0),

        Service("llm_news_analyzer",
                cmd=[py, "-u", "python_brain/news/llm_news_analyzer.py"],
                needs_ibkr=False, needs_nats=True,
                env_extra=dict(_ANTHROPIC_ENV),
                log_stale_max_s=None),

        Service("agent_swarm",
                cmd=[py, "-u", "python_brain/agents/agent_swarm.py"],
                needs_ibkr=False, needs_nats=True,
                env_extra=dict(_ANTHROPIC_ENV),
                log_stale_max_s=None),

        Service("news_to_intel",
                cmd=[py, "-u", "python_brain/news/news_to_intel.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        # ---- Data maintenance --------------------------------------------
        Service("adaptive_intel_seeder",
                cmd=[py, "-u", "python_brain/scanner/adaptive_intel_seeder.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=1500.0),

        Service("external_api_puller",
                cmd=[py, "-u", "python_brain/news/external_api_puller.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=3600.0),

        Service("ibkr_fundamentals_puller",
                cmd=[py, "-u", "python_brain/news/ibkr_fundamentals_puller.py"],
                needs_ibkr=True, needs_nats=True, startup_delay_s=5.0,
                restart_backoff_initial_s=30.0, restart_backoff_max_s=300.0,
                log_stale_max_s=3600.0),

        Service("intelligence_runner",
                cmd=[py, "-u", "python_brain/agents/intelligence_runner.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=1800.0),

        # ---- Infra --------------------------------------------------------
        Service("nats_archiver",
                cmd=[py, "-u", "python_brain/core/nats_archiver.py"],
                needs_ibkr=False, needs_nats=True),

        Service("compounding_tracker",
                cmd=[py, "-u", "python_brain/engine/compounding_tracker.py"],
                needs_ibkr=False, needs_nats=True),

        Service("regime_amplifier",
                cmd=[py, "-u", "python_brain/engine/regime_amplifier.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        Service("reentry_manager",
                cmd=[py, "-u", "python_brain/engine/reentry_manager.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        Service("data_flow_monitor",
                cmd=[py, "-u", "python_brain/infra/data_flow_monitor.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=None),

        # ---- NEW quant daemons (built this session) ----------------------
        Service("vpin_daemon",
                cmd=[py, "-u", "python_brain/quant/vpin_daemon.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=300.0),

        Service("bar_builder_5s",
                cmd=[py, "-u", "python_brain/quant/bar_builder_5s.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=300.0),

        # Gateway healer — auto-relaunches Gateway if port dies, raises
        # gateway.api_wedged alert if handshakes hang. Prevents silent
        # multi-hour Gateway outages.
        Service("gateway_healer",
                cmd=[py, "-u", "python_brain/infra/gateway_healer.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=120.0),

        Service("ouroboros_daemon",
                cmd=[py, "-u", "scripts/ouroboros_daemon.py"],
                needs_ibkr=False, needs_nats=False,
                log_stale_max_s=None),

        # ---- Super-institutional-plus gate (Phase 2/3/4) ------------------
        # Adds Almgren-Chriss cost, L2 book imbalance boost, VaR/CVaR monitor,
        # correlation guard, tail-hedge overlay, CVaR-aware stops.
        # Publishes to signals.post_super + risk.var_cvar + hedge.recommendation
        Service("super_institutional_gate",
                cmd=[py, "-u", "python_brain/engine/super_institutional_gate.py"],
                needs_ibkr=False, needs_nats=True,
                log_stale_max_s=300.0),

        Service("run_live_ibkr",
                cmd=[py, "-u", "scripts/run_live_ibkr.py"],
                needs_ibkr=False, needs_nats=True, startup_delay_s=5.0,
                log_stale_max_s=None),
    ]


def main():
    acquire_pid_lock()
    LOG.info("V5 supervisor v2 starting (pid=%d)", os.getpid())
    running = True

    def _sigterm(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services_raw = build_services()
    services: list[Service] = []
    skipped: list[tuple[str, str]] = []
    for s in services_raw:
        ok, reason = s.valid()
        if ok:
            services.append(s)
        else:
            skipped.append((s.name, reason))
            LOG.error("SKIP %s — %s", s.name, reason)

    LOG.info("registered %d services (%d skipped)", len(services), len(skipped))

    kill_orphans(services)
    launch_nats()
    time.sleep(2)
    launch_ibgateway()
    for i in range(60):
        if ibkr_up():
            LOG.info("IB Gateway up after %ds", i * 2)
            break
        time.sleep(2)

    while running:
        if not nats_up():
            launch_nats()
            time.sleep(3)
        if not ibkr_up():
            LOG.warning("IB Gateway unreachable — re-launching")
            launch_ibgateway()
            time.sleep(60)

        state = {
            "ts": time.time(),
            "ibkr_up": ibkr_up(),
            "nats_up": nats_up(),
            "services": {},
            "skipped": dict(skipped),
        }

        # Serialize IBKR spawns — one per 10s window
        last_ibkr_start = max(
            (s.last_start_ts for s in services if s.needs_ibkr), default=0.0
        )
        ibkr_spawn_ok = (time.time() - last_ibkr_start) > 10.0

        for s in services:
            stuck, reason = s.is_stuck()
            if stuck and (time.time() - s.last_stale_kill_ts) > 120:
                LOG.warning("STUCK %s (%s) — killing", s.name, reason)
                s.stop()
                s.last_stale_kill_ts = time.time()

            if not s.is_alive():
                if s.needs_nats and not nats_up():
                    continue
                if s.needs_ibkr and not ibkr_up():
                    continue
                if time.time() - s.last_start_ts < s.backoff_s:
                    continue
                if s.needs_ibkr and not ibkr_spawn_ok:
                    continue
                s.start()
                s.backoff_s = min(s.backoff_s * 1.5, s.restart_backoff_max_s)
                if s.needs_ibkr:
                    ibkr_spawn_ok = False
                time.sleep(s.startup_delay_s)
            else:
                s.backoff_s = max(2.0, s.backoff_s * 0.95)

            state["services"][s.name] = {
                "alive": s.is_alive(),
                "pid": s.proc.pid if s.proc else None,
                "restarts": s.restart_count,
                "backoff_s": round(s.backoff_s, 1),
            }

        try:
            HEARTBEAT.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

        time.sleep(5)

    LOG.info("shutting down")
    for s in services:
        s.stop()
    release_pid_lock()


if __name__ == "__main__":
    main()
