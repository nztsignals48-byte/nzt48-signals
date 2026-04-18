"""V5 Master Supervisor — unified replacement for v2/v3/v3_ext/v3_opt/v3_fixes.

Single process, single PID file, comprehensive service registry. Prior
supervisors fragmented the service set across 5 files, each with its own
PID lock and restart loop. This one supervises everything from one place.

Service registry is organized by tier:
  Tier 0: Infrastructure (NATS depends)
  Tier 1: IBKR-bound (need Gateway)
  Tier 2: Analysis (need NATS but not Gateway)
  Tier 3: Reporting (run periodically)

Startup order respects tier dependencies. Per-tier serialized IBKR spawns
keep Gateway stable.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("V5_ROOT", "/Users/rr/aegis-v5"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PID_FILE = Path("/tmp/v5_master_supervisor.pid")
HEARTBEAT = Path("/tmp/v5_master_heartbeat.json")
LOG = logging.getLogger("master-sup")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# Load Anthropic env if present
_ANTHROPIC_ENV = {}
env_path = ROOT / ".env.anthropic"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _ANTHROPIC_ENV[k.strip()] = v.strip().strip('"').strip("'")


@dataclass
class Service:
    name: str
    cmd: list
    tier: int = 2
    needs_ibkr: bool = False
    verify_file: str = ""
    env_extra: dict = field(default_factory=dict)
    restart_backoff_s: float = 10.0
    restart_backoff_max_s: float = 300.0
    startup_delay_s: float = 0.5
    proc: subprocess.Popen | None = None
    attempts: int = 0
    last_spawn_ts: float = 0.0
    _current_backoff: float = 5.0

    def valid(self) -> tuple[bool, str]:
        if self.verify_file and not (ROOT / self.verify_file).exists():
            return False, f"missing: {self.verify_file}"
        return True, "ok"

    def start(self):
        env = os.environ.copy()
        env.update(self.env_extra)
        env.setdefault("PYTHONPATH", str(ROOT))
        env.setdefault("V5_ROOT", str(ROOT))
        log_path = Path(f"/tmp/v5_{self.name}.log")
        try:
            f = open(log_path, "a")
            f.write(f"\n===== MASTER SUPERVISOR SPAWN #{self.attempts + 1} @ {time.ctime()} =====\n")
            f.flush()
            self.proc = subprocess.Popen(
                self.cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            self.attempts += 1
            self.last_spawn_ts = time.time()
            LOG.info("started %s (pid=%d, tier=%d) log=%s",
                     self.name, self.proc.pid, self.tier, log_path)
        except Exception as e:
            LOG.error("failed to start %s: %s", self.name, e)

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass


def build_registry() -> list[Service]:
    """Complete V5 service registry."""
    py = sys.executable
    services = []

    # Tier 0: infrastructure
    # (NATS + Gateway are managed externally — supervisor doesn't spawn them)

    # Tier 1: IBKR-bound
    services.extend([
        Service("rust_bridge",
                cmd=[str(ROOT / "rust_core/target/debug/aegis-engine")],
                verify_file="rust_core/target/debug/aegis-engine",
                tier=1, needs_ibkr=True, startup_delay_s=8.0,
                restart_backoff_s=30.0, restart_backoff_max_s=300.0),
        Service("ibkr_scanner_v2",
                cmd=[py, "-u", "python_brain/scanner/ibkr_scanner_v2.py"],
                tier=1, needs_ibkr=True, startup_delay_s=3.0,
                restart_backoff_s=30.0),
        Service("paper_executor",
                cmd=[py, "-u", "python_brain/engine/paper_executor.py"],
                tier=1, needs_ibkr=True, startup_delay_s=3.0),
        Service("portfolio_streamer",
                cmd=[py, "-u", "python_brain/engine/portfolio_streamer.py"],
                tier=1, needs_ibkr=True, startup_delay_s=5.0),
        Service("news_reactor",
                cmd=[py, "-u", "python_brain/news/ibkr_news_reactor.py"],
                tier=1, needs_ibkr=True, startup_delay_s=3.0),
        Service("ibkr_fundamentals_puller",
                cmd=[py, "-u", "python_brain/news/ibkr_fundamentals_puller.py"],
                tier=1, needs_ibkr=True, startup_delay_s=5.0),
        Service("delayed_universe_streamer",
                cmd=[py, "-u", "python_brain/scanner/delayed_universe_streamer.py"],
                tier=1, needs_ibkr=True, startup_delay_s=5.0),
    ])

    # Tier 2: analysis / NATS consumers
    services.extend([
        Service("signal_to_order",
                cmd=[py, "-m", "python_brain.engine.signal_to_order_bridge"],
                tier=2),
        Service("exit_to_order",
                cmd=[py, "-m", "python_brain.engine.exit_to_order_bridge"],
                tier=2),
        Service("universe_rotator_v2",
                cmd=[py, "-m", "python_brain.scanner.universe_rotator_v2"],
                tier=2),
        Service("indicator_framer",
                cmd=[py, "-u", "python_brain/engine/indicator_framer.py"],
                tier=2),
        Service("adaptive_gate_chain",
                cmd=[py, "-u", "python_brain/engine/adaptive_gate_chain.py"],
                tier=2),
        Service("signals_gated_forwarder",
                cmd=[py, "-u", "python_brain/engine/signals_gated_forwarder.py"],
                tier=2),
        Service("scanner_momentum_fast",
                cmd=[py, "-u", "python_brain/strategies/scanner_momentum_fast.py"],
                tier=2),
        Service("news_alpha_trader",
                cmd=[py, "-u", "python_brain/strategies/news_alpha_trader.py"],
                tier=2),
        Service("options_flow_signal",
                cmd=[py, "-u", "python_brain/quant/options_flow_signal.py"],
                tier=2),
        Service("reentry_manager",
                cmd=[py, "-u", "python_brain/engine/reentry_manager.py"],
                tier=2),
        Service("regime_amplifier",
                cmd=[py, "-u", "python_brain/engine/regime_amplifier.py"],
                tier=2),
        Service("compounding_tracker",
                cmd=[py, "-u", "python_brain/engine/compounding_tracker.py"],
                tier=2),
        Service("hedge_executor",
                cmd=[py, "-u", "python_brain/risk/hedge_executor.py"],
                tier=2),
        Service("cross_portfolio_halt",
                cmd=[py, "-u", "python_brain/risk/cross_portfolio_halt.py"],
                tier=2),
        Service("llm_news_analyzer",
                cmd=[py, "-u", "python_brain/news/llm_news_analyzer.py"],
                tier=2, env_extra=dict(_ANTHROPIC_ENV)),
        Service("agent_swarm",
                cmd=[py, "-u", "python_brain/agents/agent_swarm.py"],
                tier=2, env_extra=dict(_ANTHROPIC_ENV)),
        Service("llm_uplift_tracker",
                cmd=[py, "-u", "python_brain/intelligence/llm_uplift_tracker.py"],
                tier=2, env_extra=dict(_ANTHROPIC_ENV)),
        Service("llm_decision_audit",
                cmd=[py, "-u", "python_brain/compliance/llm_decision_audit.py"],
                tier=2),
        Service("best_execution_logger",
                cmd=[py, "-u", "python_brain/compliance/best_execution_logger.py"],
                tier=2),
        Service("news_to_intel",
                cmd=[py, "-u", "python_brain/news/news_to_intel.py"],
                tier=2),
        Service("adaptive_intel_seeder",
                cmd=[py, "-u", "python_brain/scanner/adaptive_intel_seeder.py"],
                tier=2),
        Service("external_api_puller",
                cmd=[py, "-u", "python_brain/news/external_api_puller.py"],
                tier=2),
        Service("intelligence_runner",
                cmd=[py, "-u", "python_brain/agents/intelligence_runner.py"],
                tier=2),
        Service("nats_archiver",
                cmd=[py, "-u", "python_brain/core/nats_archiver.py"],
                tier=2),
        Service("data_flow_monitor",
                cmd=[py, "-u", "python_brain/infra/data_flow_monitor.py"],
                tier=2),
        Service("benchmark_streamer",
                cmd=[py, "-u", "python_brain/quant/benchmark_streamer.py"],
                tier=2),
        Service("marginal_var_live",
                cmd=[py, "-u", "python_brain/reporting/marginal_var_live.py"],
                tier=2),
        Service("regime_persistence_publisher",
                cmd=[py, "-u", "python_brain/reporting/regime_persistence_publisher.py"],
                tier=2),
        Service("capital_bandit_daemon",
                cmd=[py, "-u", "scripts/capital_bandit_daemon_launcher.py"],
                tier=2),
    ])

    # Tier 3: periodic
    services.extend([
        Service("gateway_healer",
                cmd=[py, "-u", "python_brain/infra/gateway_healer.py"],
                tier=3),
        Service("vpin_daemon",
                cmd=[py, "-u", "python_brain/quant/vpin_daemon.py"],
                tier=3),
        Service("bar_builder_5s",
                cmd=[py, "-u", "python_brain/quant/bar_builder_5s.py"],
                tier=3),
        Service("pit_snapshot",
                cmd=[py, "-u", "python_brain/scanner/pit_snapshot.py"],
                tier=3),
        Service("ouroboros_daemon",
                cmd=[py, "-u", "scripts/ouroboros_daemon.py"],
                tier=3),
        Service("run_live_ibkr",
                cmd=[py, "-u", "scripts/run_live_ibkr.py"],
                tier=3, startup_delay_s=5.0),
    ])

    # Filter to those whose files exist
    valid = []
    for s in services:
        ok, reason = s.valid()
        if ok:
            valid.append(s)
        else:
            LOG.warning("SKIP %s: %s", s.name, reason)
    return valid


def acquire_lock():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            try:
                os.kill(pid, 0)
                LOG.error("another master supervisor alive (pid=%d)", pid)
                sys.exit(1)
            except OSError:
                pass
        except Exception:
            pass
    PID_FILE.write_text(str(os.getpid()))


def release_lock():
    try:
        PID_FILE.unlink()
    except Exception:
        pass


def write_heartbeat(services: list[Service]):
    snap = {
        "ts": time.time(),
        "pid": os.getpid(),
        "total": len(services),
        "alive": sum(1 for s in services if s.is_alive()),
        "services": {
            s.name: {
                "alive": s.is_alive(),
                "tier": s.tier,
                "attempts": s.attempts,
                "pid": s.proc.pid if s.proc else None,
            } for s in services
        },
    }
    try:
        HEARTBEAT.write_text(json.dumps(snap))
    except Exception:
        pass


def main():
    acquire_lock()
    LOG.info("V5 master supervisor starting (pid=%d, ROOT=%s)", os.getpid(), ROOT)

    running = True

    def _sigterm(*_):
        nonlocal running
        LOG.info("SIGTERM received, shutting down")
        running = False

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    services = build_registry()
    LOG.info("registered %d services across tiers", len(services))

    # Staggered startup by tier
    by_tier: dict[int, list[Service]] = {}
    for s in services:
        by_tier.setdefault(s.tier, []).append(s)

    last_ibkr_spawn = 0.0
    for tier in sorted(by_tier.keys()):
        LOG.info("=== starting tier %d (%d services) ===", tier, len(by_tier[tier]))
        for s in by_tier[tier]:
            if s.needs_ibkr:
                delta = time.time() - last_ibkr_spawn
                if delta < 10:
                    time.sleep(10 - delta)
                last_ibkr_spawn = time.time()
            s.start()
            time.sleep(s.startup_delay_s)
        time.sleep(2)  # tier break

    # Main supervision loop
    last_heartbeat = 0.0
    try:
        while running:
            time.sleep(5)
            now = time.time()

            for s in services:
                if not s.is_alive():
                    # Exponential backoff per-service
                    delta = now - s.last_spawn_ts
                    if delta < s._current_backoff:
                        continue
                    # IBKR spawn serialization
                    if s.needs_ibkr:
                        if now - last_ibkr_spawn < 10:
                            continue
                        last_ibkr_spawn = now
                    LOG.warning("respawning %s (attempts=%d)", s.name, s.attempts)
                    s.start()
                    s._current_backoff = min(
                        s._current_backoff * 1.5, s.restart_backoff_max_s
                    )
                else:
                    # Reset backoff if alive > 60s
                    if now - s.last_spawn_ts > 60:
                        s._current_backoff = s.restart_backoff_s

            if now - last_heartbeat >= 5:
                write_heartbeat(services)
                last_heartbeat = now
    finally:
        LOG.info("stopping all services")
        for s in services:
            s.stop()
        release_lock()


if __name__ == "__main__":
    main()
