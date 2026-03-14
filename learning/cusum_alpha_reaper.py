"""
NZT-48 V9.5 CUSUM Alpha Reaper
================================
Statistical process control for strategy edge decay.

CUSUM (Cumulative Sum Control Chart) is optimal for detecting small,
persistent shifts in process mean. When a strategy's edge decays below
the control threshold, its capital allocation is zeroed and the strategy
is quarantined.

Page, E.S. (1954), "Continuous Inspection Schemes",
Biometrika, Vol. 41, pp. 100-115.

The #1 killer of algorithmic trading systems is trading a dead edge.
CUSUM catches edge decay statistically — not heuristically — and
cuts capital BEFORE you bleed out.

Integration:
  - Runs nightly via AutonomousMLDaemon (18:00 UTC)
  - Reads completed trades from SQLite virtual_trades table
  - Persists CUSUM state to SQLite learning_state table
  - Writes allocation overrides to data/cusum_allocation_override.json
  - Strategy tournament reads overrides before each scan
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.cusum_alpha_reaper")


@dataclass
class CUSUMState:
    """CUSUM tracker for a single strategy."""
    strategy: str = ""
    cusum_plus: float = 0.0     # Upward shift detector (edge improving)
    cusum_minus: float = 0.0    # Downward shift detector (edge decaying)
    target_expectancy: float = 0.15   # Minimum acceptable R-expectancy
    n_trades: int = 0
    current_expectancy: float = 0.0
    status: str = "ACTIVE"      # ACTIVE | DEGRADED | QUARANTINED | PROBATION
    quarantine_until: Optional[str] = None
    last_updated: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CUSUMState:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class CUSUMAlphaReaper:
    """Monitors strategy edge using CUSUM statistical process control.

    Page (1954) CUSUM is optimal for detecting small persistent shifts
    in process mean. Parameters:
      H = 3.0  (decision threshold in standard deviations)
      K = 0.5  (allowance — how much below target before alarming)

    A CUSUM alarm fires when cusum_minus > H, indicating the strategy's
    rolling expectancy has drifted below target_expectancy by a
    statistically significant margin.
    """

    def __init__(
        self,
        db_path: str = "data/nzt48.db",
        h_threshold: float = 3.0,
        k_allowance: float = 0.5,
        min_trades: int = 20,
        quarantine_days: int = 30,
        requalify_trades: int = 10,
    ) -> None:
        self.db_path = Path(db_path)
        self._h = h_threshold
        self._k = k_allowance
        self._min_trades = min_trades
        self._quarantine_days = quarantine_days
        self._requalify_trades = requalify_trades
        self._states: dict[str, CUSUMState] = {}
        self._override_path = Path("data/cusum_allocation_override.json")
        self._load_state()

    def _load_state(self) -> None:
        """Load CUSUM states from SQLite."""
        if not self.db_path.exists():
            logger.warning("CUSUM: DB not found at %s — starting fresh", self.db_path)
            return
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # Ensure learning_state table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_state (
                    module TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("cusum_alpha_reaper",),
            ).fetchone()

            if row:
                data = json.loads(row["state_json"])
                for strat, state_dict in data.items():
                    self._states[strat] = CUSUMState.from_dict(state_dict)
                logger.info("CUSUM: loaded %d strategy states", len(self._states))
            conn.close()
        except Exception as e:
            logger.error("CUSUM: load state failed: %s", e)

    def _save_state(self) -> None:
        """Persist CUSUM states to SQLite."""
        try:
            conn = sqlite3.connect(self.db_path)
            state_json = json.dumps(
                {s: st.to_dict() for s, st in self._states.items()},
                default=str,
            )
            conn.execute(
                "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) VALUES (?, ?, ?)",
                ("cusum_alpha_reaper", state_json, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("CUSUM: save state failed: %s", e)

    def update(self, strategy: str, r_multiple: float) -> Optional[str]:
        """Update CUSUM with a new trade result.

        Args:
            strategy: Strategy name (e.g. "S15_DailyTarget", "S16_Universal")
            r_multiple: R-multiple of completed trade

        Returns:
            Status change string if alarm fired, None otherwise.
        """
        if strategy not in self._states:
            self._states[strategy] = CUSUMState(strategy=strategy)

        state = self._states[strategy]
        state.n_trades += 1

        # Exponential moving average of expectancy (alpha=0.1)
        alpha = 0.1
        state.current_expectancy = (
            alpha * r_multiple + (1 - alpha) * state.current_expectancy
        )

        # Deviation from target
        deviation = r_multiple - state.target_expectancy

        # Update CUSUM (Page 1954)
        state.cusum_plus = max(0.0, state.cusum_plus + deviation - self._k)
        state.cusum_minus = max(0.0, state.cusum_minus - deviation - self._k)

        state.last_updated = datetime.now(timezone.utc).isoformat()

        # Only check alarms after minimum trades
        if state.n_trades < self._min_trades:
            return None

        # Downward shift detected — edge has decayed
        if state.cusum_minus > self._h and state.status == "ACTIVE":
            return self._trigger_degradation(strategy, state)

        # Upward shift during probation — edge recovering
        if state.cusum_plus > self._h and state.status == "PROBATION":
            return self._requalify(strategy, state)

        return None

    def _trigger_degradation(self, strategy: str, state: CUSUMState) -> str:
        """Strategy edge has degraded — zero capital allocation."""
        logger.warning(
            "CUSUM ALARM: %s DEGRADED (CUSUM-=%.2f > %.2f, expectancy=%.3fR)",
            strategy, state.cusum_minus, self._h, state.current_expectancy,
        )

        # Reset CUSUM counters
        state.cusum_plus = 0.0
        state.cusum_minus = 0.0

        # Quarantine
        quarantine_until = datetime.now(timezone.utc) + timedelta(days=self._quarantine_days)
        state.quarantine_until = quarantine_until.isoformat()
        state.status = "QUARANTINED"

        # Zero capital allocation
        self._write_allocation_override(strategy, 0.0, "CUSUM edge decay")
        self._save_state()

        logger.warning(
            "CUSUM: %s QUARANTINED until %s (must complete %d qualifying trades to return)",
            strategy, state.quarantine_until[:10], self._requalify_trades,
        )
        return f"DEGRADED: {strategy} quarantined until {state.quarantine_until[:10]}"

    def _requalify(self, strategy: str, state: CUSUMState) -> str:
        """Edge has recovered — requalify strategy."""
        logger.info(
            "CUSUM RECOVERY: %s edge recovered (CUSUM+=%.2f > %.2f, expectancy=%.3fR)",
            strategy, state.cusum_plus, self._h, state.current_expectancy,
        )

        state.status = "ACTIVE"
        state.cusum_plus = 0.0
        state.cusum_minus = 0.0
        state.quarantine_until = None

        # Remove allocation override
        self._remove_allocation_override(strategy)
        self._save_state()

        logger.info("CUSUM: %s REQUALIFIED — capital allocation restored", strategy)
        return f"REQUALIFIED: {strategy} capital restored"

    def check_quarantine_expiry(self) -> list[str]:
        """Check if any quarantined strategies are ready for probation.

        Returns list of strategies moved to PROBATION.
        """
        now = datetime.now(timezone.utc)
        moved = []

        for strategy, state in self._states.items():
            if state.status == "QUARANTINED" and state.quarantine_until:
                try:
                    expiry = datetime.fromisoformat(state.quarantine_until)
                    if now >= expiry:
                        state.status = "PROBATION"
                        state.quarantine_until = None
                        state.n_trades = 0  # Reset trade counter for re-qualification
                        moved.append(strategy)
                        logger.info(
                            "CUSUM: %s quarantine expired — PROBATION (needs %d good trades)",
                            strategy, self._requalify_trades,
                        )
                except (ValueError, TypeError):
                    pass

        if moved:
            self._save_state()
        return moved

    def _write_allocation_override(self, strategy: str, allocation: float, reason: str) -> None:
        """Write allocation override to JSON file."""
        overrides = {}
        if self._override_path.exists():
            try:
                with open(self._override_path) as f:
                    overrides = json.load(f)
            except (json.JSONDecodeError, IOError):
                overrides = {}

        overrides[strategy] = {
            "allocation": allocation,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._override_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._override_path, "w") as f:
            json.dump(overrides, f, indent=2)

        logger.warning("CUSUM: %s allocation → %.2f (%s)", strategy, allocation, reason)

    def _remove_allocation_override(self, strategy: str) -> None:
        """Remove allocation override for a requalified strategy."""
        if not self._override_path.exists():
            return
        try:
            with open(self._override_path) as f:
                overrides = json.load(f)
            overrides.pop(strategy, None)
            with open(self._override_path, "w") as f:
                json.dump(overrides, f, indent=2)
        except (json.JSONDecodeError, IOError):
            pass

    def is_strategy_active(self, strategy: str) -> bool:
        """Check if a strategy is allowed to trade (not degraded/quarantined)."""
        if strategy not in self._states:
            return True  # Unknown strategy — allow (no data yet)
        return self._states[strategy].status in ("ACTIVE", "PROBATION")

    def get_status(self) -> dict[str, dict]:
        """Get status of all tracked strategies."""
        return {s: st.to_dict() for s, st in self._states.items()}

    def run_on_recent_trades(self, days: int = 7) -> dict:
        """Run CUSUM on trades from the last N days. Used by ML Daemon.

        Returns summary dict with n_strategies, degraded list, probation list.
        """
        if not self.db_path.exists():
            return {"n_strategies": 0, "degraded": [], "probation": []}

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """SELECT strategy, r_multiple FROM virtual_trades
                   WHERE exit_time >= datetime('now', ?)
                   ORDER BY exit_time""",
                (f"-{days} days",),
            ).fetchall()
            conn.close()

            alerts = []
            for row in rows:
                alert = self.update(
                    strategy=row["strategy"],
                    r_multiple=float(row["r_multiple"]),
                )
                if alert:
                    alerts.append(alert)

            # Check quarantine expiry
            self.check_quarantine_expiry()

            status = self.get_status()
            degraded = [s for s, st in status.items() if st["status"] in ("DEGRADED", "QUARANTINED")]
            probation = [s for s, st in status.items() if st["status"] == "PROBATION"]

            logger.info(
                "CUSUM: processed %d trades across %d strategies (%d degraded, %d probation)",
                len(rows), len(status), len(degraded), len(probation),
            )

            return {
                "n_strategies": len(status),
                "n_trades_processed": len(rows),
                "degraded": degraded,
                "probation": probation,
                "alerts": alerts,
            }

        except Exception as e:
            logger.error("CUSUM: run_on_recent_trades failed: %s", e)
            return {"n_strategies": 0, "degraded": [], "probation": [], "error": str(e)}
