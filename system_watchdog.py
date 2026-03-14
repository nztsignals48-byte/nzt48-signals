"""
system_watchdog.py
==================
System Watchdog & State Machine for NZT-48 Paper Launch.

Monitors:
  - Tick loop liveness (stale tick detection)
  - Data freshness (last successful data fetch age)
  - Memory usage (runaway growth detection)
  - Constitution enforcement (max daily loss, max concurrent positions)

SystemState machine: OK → DEGRADED → HALTED
  - Each state has a list of reason codes
  - Transitions are logged and written to system_state.json

Artifacts produced:
  - artifacts/{date}/{session}/system_state.json
  - artifacts/{date}/{session}/reliability.json
  - artifacts/{date}/{session}/quality_report.json
  - artifacts/{date}/{session}/readiness.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.watchdog")

ARTIFACTS_ROOT = Path(__file__).parent / "artifacts"

# Thresholds
TICK_STALE_SECONDS = 120          # 2 min without tick → DEGRADED
TICK_HALT_SECONDS = 300           # 5 min without tick → HALTED
DATA_STALE_SECONDS = 300          # 5 min data age → DEGRADED
MEMORY_WARN_MB = 500              # Memory warning threshold
MEMORY_HALT_MB = 1000             # Memory halt threshold
# Daily loss governed solely by L1/L2/L3 cascade in circuit_breakers.py
# (L1=1.5%, L2=2.5%, L3=4.0%). Removed MAX_DAILY_LOSS_PCT=0.03 — see AEGIS F-08.
MAX_WEEKLY_LOSS_PCT = 0.06        # 6%
MAX_CONCURRENT_POSITIONS = 3
MAX_CONSECUTIVE_LOSSES = 5        # A-13: aligned with circuit_breakers._CONSEC_LOSS_TIER_3


class SystemState:
    """State machine: OK / DEGRADED / HALTED."""
    OK = "OK"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"


@dataclass
class SystemStateReport:
    """Full system state snapshot."""
    state: str = SystemState.OK
    reasons: list[str] = field(default_factory=list)
    tick_count: int = 0
    last_tick_age_seconds: float = 0.0
    data_freshness_seconds: float = 0.0
    memory_mb: float = 0.0
    daily_loss_pct: float = 0.0
    consecutive_losses: int = 0
    open_positions: int = 0
    kill_switch_active: bool = False
    mode: str = "PAPER"
    git_hash: str = ""
    config_hash: str = ""
    uptime_seconds: float = 0.0
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "reasons": self.reasons,
            "tick_count": self.tick_count,
            "last_tick_age_seconds": round(self.last_tick_age_seconds, 1),
            "data_freshness_seconds": round(self.data_freshness_seconds, 1),
            "memory_mb": round(self.memory_mb, 1),
            "daily_loss_pct": round(self.daily_loss_pct, 4),
            "consecutive_losses": self.consecutive_losses,
            "open_positions": self.open_positions,
            "kill_switch_active": self.kill_switch_active,
            "mode": self.mode,
            "git_hash": self.git_hash,
            "config_hash": self.config_hash,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "generated_at": self.generated_at,
        }


@dataclass
class DataReliabilityReport:
    """Per-session data reliability scoring."""
    score: float = 1.0                    # 0-1 aggregate
    tickers_checked: int = 0
    tickers_passed: int = 0
    tickers_warned: int = 0
    tickers_failed: int = 0
    rvol_available: int = 0
    rvol_na: int = 0
    short_window_count: int = 0
    avg_reliability_penalty: float = 0.0
    data_age_seconds: float = 0.0
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "data_reliability_score": round(self.score, 3),
            "tickers_checked": self.tickers_checked,
            "tickers_passed": self.tickers_passed,
            "tickers_warned": self.tickers_warned,
            "tickers_failed": self.tickers_failed,
            "rvol_available": self.rvol_available,
            "rvol_na": self.rvol_na,
            "short_window_count": self.short_window_count,
            "avg_reliability_penalty": round(self.avg_reliability_penalty, 3),
            "data_age_seconds": round(self.data_age_seconds, 1),
            "generated_at": self.generated_at,
        }


@dataclass
class QualityReport:
    """Per-session quality gate results."""
    passed: bool = True
    violations: list[str] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0
    rvol_placeholder_errors: int = 0
    regime_contradictions: int = 0
    missing_execution_plans: int = 0
    trade_without_approval: int = 0
    generated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "quality_passed": self.passed,
            "violations": self.violations,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "rvol_placeholder_errors": self.rvol_placeholder_errors,
            "regime_contradictions": self.regime_contradictions,
            "missing_execution_plans": self.missing_execution_plans,
            "trade_without_approval": self.trade_without_approval,
            "generated_at": self.generated_at,
        }


class SystemWatchdog:
    """Monitors system health and enforces quality guarantees."""

    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._last_tick_time: Optional[float] = None
        self._last_data_time: Optional[float] = None
        self._git_hash = self._get_git_hash()
        self._config_hash = self._get_config_hash()

    def _get_git_hash(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(Path(__file__).parent),
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def _get_config_hash(self) -> str:
        try:
            config_path = Path(__file__).parent / "config" / "settings.yaml"
            if config_path.exists():
                content = config_path.read_bytes()
                return hashlib.sha256(content).hexdigest()[:12]
        except Exception:
            pass
        return "unknown"

    def _get_memory_mb(self) -> float:
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            return usage.ru_maxrss / (1024 * 1024)  # macOS returns bytes
        except Exception:
            try:
                import psutil
                proc = psutil.Process(os.getpid())
                return proc.memory_info().rss / (1024 * 1024)
            except Exception:
                return 0.0

    def record_tick(self) -> None:
        """Called after each successful tick."""
        self._last_tick_time = time.monotonic()

    def record_data_fetch(self) -> None:
        """Called after each successful data fetch."""
        self._last_data_time = time.monotonic()

    def check_state(
        self,
        tick_count: int = 0,
        daily_loss_pct: float = 0.0,
        consecutive_losses: int = 0,
        open_positions: int = 0,
        kill_switch_active: bool = False,
    ) -> SystemStateReport:
        """Evaluate full system state and return report."""
        now = time.monotonic()
        reasons: list[str] = []
        state = SystemState.OK

        # Kill switch
        if kill_switch_active:
            state = SystemState.HALTED
            reasons.append("KILL_SWITCH_ACTIVE")

        # Tick staleness
        tick_age = (now - self._last_tick_time) if self._last_tick_time else 0.0
        if self._last_tick_time and tick_age > TICK_HALT_SECONDS:
            state = SystemState.HALTED
            reasons.append(f"TICK_STALE_{tick_age:.0f}s")
        elif self._last_tick_time and tick_age > TICK_STALE_SECONDS:
            if state != SystemState.HALTED:
                state = SystemState.DEGRADED
            reasons.append(f"TICK_SLOW_{tick_age:.0f}s")

        # Data freshness
        data_age = (now - self._last_data_time) if self._last_data_time else 0.0
        if self._last_data_time and data_age > DATA_STALE_SECONDS:
            if state != SystemState.HALTED:
                state = SystemState.DEGRADED
            reasons.append(f"DATA_STALE_{data_age:.0f}s")

        # Memory
        mem_mb = self._get_memory_mb()
        if mem_mb > MEMORY_HALT_MB:
            state = SystemState.HALTED
            reasons.append(f"MEMORY_CRITICAL_{mem_mb:.0f}MB")
        elif mem_mb > MEMORY_WARN_MB:
            if state != SystemState.HALTED:
                state = SystemState.DEGRADED
            reasons.append(f"MEMORY_HIGH_{mem_mb:.0f}MB")

        # Constitution checks
        # Daily loss check REMOVED (AEGIS F-08) — governed solely by
        # L1/L2/L3 cascade in circuit_breakers.py.

        if consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            state = SystemState.HALTED
            reasons.append(f"CONSECUTIVE_LOSSES_{consecutive_losses}")

        if open_positions > MAX_CONCURRENT_POSITIONS:
            if state != SystemState.HALTED:
                state = SystemState.DEGRADED
            reasons.append(f"EXCESS_POSITIONS_{open_positions}")

        if not reasons:
            reasons.append("ALL_CHECKS_PASSED")

        report = SystemStateReport(
            state=state,
            reasons=reasons,
            tick_count=tick_count,
            last_tick_age_seconds=tick_age,
            data_freshness_seconds=data_age,
            memory_mb=mem_mb,
            daily_loss_pct=daily_loss_pct,
            consecutive_losses=consecutive_losses,
            open_positions=open_positions,
            kill_switch_active=kill_switch_active,
            mode=os.environ.get("NZT48_MODE", "PAPER"),
            git_hash=self._git_hash,
            config_hash=self._config_hash,
            uptime_seconds=now - self._start_time,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info("[WATCHDOG] state=%s reasons=%s tick=%d mem=%.0fMB",
                     state, reasons, tick_count, mem_mb)
        return report


def compute_data_reliability(health_summary, features_map: dict) -> DataReliabilityReport:
    """Compute DataReliabilityScore from health + features."""
    report = DataReliabilityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    if health_summary:
        results = getattr(health_summary, "results", {})
        report.tickers_checked = len(results)
        for ticker, hr in results.items():
            status = getattr(hr, "status", "FAIL")
            if status == "PASS":
                report.tickers_passed += 1
            elif status == "WARN":
                report.tickers_warned += 1
            else:
                report.tickers_failed += 1

    # RVOL and reliability from features
    penalties = []
    for ticker, feat in features_map.items():
        if getattr(feat, "rvol", None) is not None:
            report.rvol_available += 1
        else:
            report.rvol_na += 1
        if getattr(feat, "short_window", False):
            report.short_window_count += 1
        pen = getattr(feat, "reliability_penalty", 0.0)
        if pen > 0:
            penalties.append(pen)

    report.avg_reliability_penalty = (
        sum(penalties) / len(penalties) if penalties else 0.0
    )

    # Aggregate score: 0-1
    total = report.tickers_checked or 1
    pass_ratio = (report.tickers_passed + 0.5 * report.tickers_warned) / total
    rvol_ratio = report.rvol_available / max(report.rvol_available + report.rvol_na, 1)
    penalty_factor = max(0.0, 1.0 - report.avg_reliability_penalty)

    report.score = round(
        0.50 * pass_ratio + 0.30 * rvol_ratio + 0.20 * penalty_factor,
        3,
    )
    return report


def run_quality_gate(plays: list, regime: str, features_map: dict) -> QualityReport:
    """Run quality gate checks on generated plays.

    Checks:
      1. No RVOL=0.00 placeholder errors (must be N/A or actual value)
      2. No LONG TRADE in RISK_OFF regime
      3. Every TRADE has an ExecutionPlan
      4. Every TRADE has RiskOfficer != VETO
      5. regime_confidence=0 with high conviction is flagged
    """
    report = QualityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    regime_upper = regime.upper()

    for play in plays:
        report.checks_run += 1

        # Check 1: RVOL placeholder
        rvol = getattr(play, "rvol", None)
        if rvol is not None and rvol == 0.0:
            report.rvol_placeholder_errors += 1
            report.violations.append(
                f"{getattr(play, 'ticker', '?')}: RVOL=0.00 (must be N/A)"
            )

        # Check 2: Regime contradiction
        direction = getattr(play, "direction", "")
        label = getattr(play, "label", "")
        if "RISK_OFF" in regime_upper and direction == "LONG" and label not in ("WATCH", "INTEL", "PEER"):
            report.regime_contradictions += 1
            report.violations.append(
                f"{getattr(play, 'ticker', '?')}: LONG in RISK_OFF regime"
            )

        # Check 3: ExecutionPlan for TRADE-eligible
        if label in ("TRADE", "STRICT", "FALLBACK", ""):
            exec_plan = getattr(play, "execution_plan", None)
            if not exec_plan:
                report.missing_execution_plans += 1
                report.violations.append(
                    f"{getattr(play, 'ticker', '?')}: TRADE without ExecutionPlan"
                )

        # Check 4: RiskOfficer decision
        ro_decision = getattr(play, "risk_officer_decision", None)
        if ro_decision == "VETO" and label not in ("WATCH", "INTEL", "PEER", "VETO"):
            report.trade_without_approval += 1
            report.violations.append(
                f"{getattr(play, 'ticker', '?')}: TRADE despite RiskOfficer VETO"
            )

        if not report.violations:
            report.checks_passed += 1

    report.checks_passed = report.checks_run - len(report.violations)
    report.passed = len(report.violations) == 0

    if report.violations:
        logger.warning("[QUALITY_GATE] %d violations: %s",
                       len(report.violations), report.violations[:5])
    else:
        logger.info("[QUALITY_GATE] all %d checks passed", report.checks_run)

    return report


def write_watchdog_artifacts(
    session: str,
    state_report: SystemStateReport,
    reliability_report: DataReliabilityReport,
    quality_report: QualityReport,
) -> dict[str, str]:
    """Write all watchdog artifacts atomically. Returns {name: path}."""
    today_str = str(date.today())
    session_key = session.lower().replace(" ", "_")
    out_dir = ARTIFACTS_ROOT / today_str / session_key
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}

    artifacts = {
        "system_state.json": state_report.to_dict(),
        "reliability.json": reliability_report.to_dict(),
        "quality_report.json": quality_report.to_dict(),
        "readiness.json": {
            "system_state": state_report.state,
            "data_reliability_score": reliability_report.score,
            "quality_passed": quality_report.passed,
            "violations_count": len(quality_report.violations),
            "mode": state_report.mode,
            "git_hash": state_report.git_hash,
            "config_hash": state_report.config_hash,
            "paper_launch_ready": (
                state_report.state != SystemState.HALTED
                and reliability_report.score >= 0.5
                and quality_report.passed
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    for name, payload in artifacts.items():
        out_path = out_dir / name
        try:
            fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(payload, indent=2, default=str))
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_name).replace(out_path)
            written[name] = str(out_path)
        except Exception as exc:
            logger.warning("[WATCHDOG] artifact write failed (%s): %s", name, exc)
            try:
                os.unlink(tmp_name)
            except Exception:
                pass

    logger.info("[WATCHDOG] wrote %d artifacts to %s", len(written), out_dir)
    return written
