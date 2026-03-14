"""
NZT-48 V9.5 Autonomous ML Daemon
=================================
Daily self-improvement loop: retrain → recalibrate → reap → reload.

The Ouroboros: the system eats its own tail to grow stronger.
Runs daily at 18:00 UTC (after US close, before nightly intelligence).

Pipeline:
  1. Warm-start retrain LightGBM on enriched outcomes (telemetry features)
  2. Recalibrate GPD tail risk (refit Generalized Pareto on recent losses)
  3. Run CUSUM Alpha Reaper — quarantine dead edges
  4. Publish hot_reload → tick_loop swaps weights atomically

References:
  De Prado (2018): meta-labelling + purged k-fold
  Balkema & de Haan (1974): EVT/GPD for tail risk
  Page (1954): CUSUM for edge decay detection
  Silver et al. (2016): self-play improvement loops

Integration:
  - Scheduled via APScheduler at 18:00 UTC daily in main.py
  - Reads outcomes from data/outcomes.jsonl
  - Reads/writes ml_model.pkl (with .backup safety)
  - Publishes nzt:system:hot_reload via Redis PUBSUB
  - All steps gated by v95_autonomous_ml_daemon feature flag
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nzt48.autonomous_ml_daemon")


@dataclass
class DaemonRunResult:
    """Result of a single daemon cycle."""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ml_retrained: bool = False
    ml_cv_auc: float = 0.0
    ml_n_trades: int = 0
    gpd_tickers_refitted: int = 0
    cusum_degraded: list[str] = field(default_factory=list)
    cusum_probation: list[str] = field(default_factory=list)
    cusum_alerts: list[str] = field(default_factory=list)
    hot_reload_published: bool = False
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "ml_retrained": self.ml_retrained,
            "ml_cv_auc": self.ml_cv_auc,
            "ml_n_trades": self.ml_n_trades,
            "gpd_tickers_refitted": self.gpd_tickers_refitted,
            "cusum_degraded": self.cusum_degraded,
            "cusum_probation": self.cusum_probation,
            "cusum_alerts": self.cusum_alerts,
            "hot_reload_published": self.hot_reload_published,
            "errors": self.errors,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class AutonomousMLDaemon:
    """Daily self-improvement daemon.

    Orchestrates: retrain → recalibrate → reap → reload.
    Each step is independent and fail-safe — one failure does not block the others.
    """

    def __init__(
        self,
        ml_model=None,
        cusum_reaper=None,
        state_manager=None,
        data_dir: str = "data",
        model_path: str = "data/ml_model.pkl",
        outcomes_path: str = "data/outcomes.jsonl",
        tickers: Optional[list[str]] = None,
    ) -> None:
        self._ml_model = ml_model
        self._cusum = cusum_reaper
        self._state_manager = state_manager
        self._data_dir = Path(data_dir)
        self._model_path = Path(model_path)
        self._outcomes_path = Path(outcomes_path)
        self._tickers = tickers or []
        self._last_run: Optional[DaemonRunResult] = None
        self._run_log_path = self._data_dir / "ml_daemon_log.jsonl"

    async def run(self) -> DaemonRunResult:
        """Execute full Ouroboros cycle.

        Returns DaemonRunResult with per-step outcomes.
        """
        t0 = time.monotonic()
        result = DaemonRunResult()

        logger.info("=" * 60)
        logger.info("[V9.5] AUTONOMOUS ML DAEMON — Ouroboros cycle starting")
        logger.info("=" * 60)

        # Step 1: Retrain LightGBM
        self._step_retrain_ml(result)

        # Step 2: Recalibrate GPD tail risk
        self._step_recalibrate_gpd(result)

        # Step 3: CUSUM Alpha Reaper
        self._step_cusum_reaper(result)

        # Step 4: Hot-reload broadcast
        await self._step_hot_reload(result)

        result.duration_seconds = time.monotonic() - t0

        # Persist run log
        self._persist_run_log(result)

        logger.info(
            "[V9.5] DAEMON COMPLETE in %.1fs — ML:%s (AUC=%.3f, n=%d) | "
            "GPD:%d tickers | CUSUM: %d degraded, %d probation | reload:%s",
            result.duration_seconds,
            "RETRAINED" if result.ml_retrained else "skipped",
            result.ml_cv_auc,
            result.ml_n_trades,
            result.gpd_tickers_refitted,
            len(result.cusum_degraded),
            len(result.cusum_probation),
            "YES" if result.hot_reload_published else "no",
        )
        if result.errors:
            logger.warning("[V9.5] DAEMON ERRORS: %s", result.errors)

        self._last_run = result
        return result

    # ── Step 1: ML Retrain ─────────────────────────────────────────────

    def _step_retrain_ml(self, result: DaemonRunResult) -> None:
        """Warm-start retrain LightGBM on enriched outcomes."""
        if not self._ml_model:
            logger.info("[DAEMON] Step 1: ML model not available — skipping")
            return

        try:
            # Check if retrain is warranted
            if not hasattr(self._ml_model, "should_retrain"):
                logger.info("[DAEMON] Step 1: ML model has no should_retrain — skipping")
                return

            # should_retrain() may require last_trained_at argument
            import inspect
            sig = inspect.signature(self._ml_model.should_retrain)
            if sig.parameters:
                # Extract trained_at from loaded model payload
                _trained_at = getattr(self._ml_model, "_trained_at", None)
                if _trained_at is None and hasattr(self._ml_model, "_model_payload"):
                    _trained_at = (self._ml_model._model_payload or {}).get("trained_at")
                if _trained_at is None:
                    # No model trained yet — always retrain
                    _trained_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
                needs_retrain = self._ml_model.should_retrain(_trained_at)
            else:
                needs_retrain = self._ml_model.should_retrain()

            if not needs_retrain:
                logger.info("[DAEMON] Step 1: ML retrain not needed (insufficient new trades)")
                return

            # Safety: backup current model before retrain
            self._backup_model()

            # Retrain
            logger.info("[DAEMON] Step 1: Retraining LightGBM meta-model...")
            train_result = self._ml_model.train()

            if train_result and isinstance(train_result, dict):
                result.ml_retrained = True
                result.ml_cv_auc = train_result.get("cv_auc", 0.0)
                result.ml_n_trades = train_result.get("n_trades", 0)
                logger.info(
                    "[DAEMON] Step 1: ML retrained — AUC=%.3f, n_trades=%d",
                    result.ml_cv_auc, result.ml_n_trades,
                )
            else:
                result.ml_retrained = True
                logger.info("[DAEMON] Step 1: ML retrained (no metrics returned)")

        except Exception as e:
            msg = f"Step 1 ML retrain failed: {e}"
            logger.error("[DAEMON] %s", msg)
            result.errors.append(msg)

    def _backup_model(self) -> None:
        """Backup ml_model.pkl → ml_model.pkl.backup before retrain."""
        if self._model_path.exists():
            backup_path = self._model_path.with_suffix(".pkl.backup")
            try:
                shutil.copy2(self._model_path, backup_path)
                logger.info("[DAEMON] Model backed up: %s", backup_path)
            except Exception as e:
                logger.warning("[DAEMON] Model backup failed: %s", e)

    # ── Step 2: GPD Recalibration ──────────────────────────────────────

    def _step_recalibrate_gpd(self, result: DaemonRunResult) -> None:
        """Recalibrate GPD tail risk fits for all active tickers.

        Invalidates the TailRiskMonitor cache so next signal evaluation
        triggers a fresh GPD fit with the latest loss data. The actual
        fitting happens on-demand (cache miss) during the next tick loop.
        """
        if not self._tickers:
            logger.info("[DAEMON] Step 2: No tickers configured — skipping GPD recalibration")
            return

        try:
            # We don't import TailRiskMonitor at module level to avoid circular deps.
            # The actual recalibration is a cache invalidation — the monitor refits
            # on next access with fresh data. This is correct because:
            #   1. GPD fits are cached with 1-hour TTL
            #   2. Invalidating forces refit on next signal
            #   3. The refit uses the latest loss distribution
            from core.evt import TailRiskMonitor

            monitor = TailRiskMonitor()
            monitor.invalidate()  # Clear all cached GPD fits
            result.gpd_tickers_refitted = len(self._tickers)
            logger.info(
                "[DAEMON] Step 2: GPD cache invalidated — %d tickers will refit on next signal",
                len(self._tickers),
            )

        except ImportError:
            logger.info("[DAEMON] Step 2: TailRiskMonitor not available — skipping")
        except Exception as e:
            msg = f"Step 2 GPD recalibration failed: {e}"
            logger.error("[DAEMON] %s", msg)
            result.errors.append(msg)

    # ── Step 3: CUSUM Alpha Reaper ─────────────────────────────────────

    def _step_cusum_reaper(self, result: DaemonRunResult) -> None:
        """Run CUSUM on recent trades to detect edge decay."""
        if not self._cusum:
            logger.info("[DAEMON] Step 3: CUSUM Alpha Reaper not available — skipping")
            return

        try:
            logger.info("[DAEMON] Step 3: Running CUSUM Alpha Reaper on last 7 days...")
            cusum_result = self._cusum.run_on_recent_trades(days=7)

            result.cusum_degraded = cusum_result.get("degraded", [])
            result.cusum_probation = cusum_result.get("probation", [])
            result.cusum_alerts = cusum_result.get("alerts", [])

            logger.info(
                "[DAEMON] Step 3: CUSUM — %d strategies, %d trades | degraded=%s, probation=%s",
                cusum_result.get("n_strategies", 0),
                cusum_result.get("n_trades_processed", 0),
                result.cusum_degraded or "none",
                result.cusum_probation or "none",
            )

            if result.cusum_alerts:
                for alert in result.cusum_alerts:
                    logger.warning("[DAEMON] CUSUM ALERT: %s", alert)

        except Exception as e:
            msg = f"Step 3 CUSUM failed: {e}"
            logger.error("[DAEMON] %s", msg)
            result.errors.append(msg)

    # ── Step 4: Hot-Reload Broadcast ───────────────────────────────────

    async def _step_hot_reload(self, result: DaemonRunResult) -> None:
        """Publish hot_reload event so tick_loop swaps weights atomically."""
        if not self._state_manager:
            logger.info("[DAEMON] Step 4: StateManager not available — skipping hot-reload")
            return

        # Only reload if something actually changed
        if not result.ml_retrained and not result.cusum_alerts:
            logger.info("[DAEMON] Step 4: Nothing changed — skipping hot-reload")
            return

        try:
            if hasattr(self._state_manager, "publish_hot_reload"):
                message = {
                    "source": "autonomous_ml_daemon",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "ml_retrained": result.ml_retrained,
                    "cusum_alerts": result.cusum_alerts,
                }
                await self._state_manager.publish_hot_reload(message)
                result.hot_reload_published = True
                logger.info("[DAEMON] Step 4: Hot-reload published → tick_loop will swap weights")
            else:
                logger.info("[DAEMON] Step 4: StateManager has no publish_hot_reload — skipping")

        except Exception as e:
            msg = f"Step 4 hot-reload failed: {e}"
            logger.error("[DAEMON] %s", msg)
            result.errors.append(msg)

    # ── Persistence ────────────────────────────────────────────────────

    def _persist_run_log(self, result: DaemonRunResult) -> None:
        """Append daemon run to JSONL log for auditing."""
        try:
            self._run_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._run_log_path, "a") as f:
                f.write(json.dumps(result.to_dict(), default=str) + "\n")
        except Exception as e:
            logger.warning("[DAEMON] Run log write failed: %s", e)

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get daemon status for dashboard / health checks."""
        return {
            "last_run": self._last_run.to_dict() if self._last_run else None,
            "ml_model_available": self._ml_model is not None,
            "cusum_available": self._cusum is not None,
            "state_manager_available": self._state_manager is not None,
            "tickers_count": len(self._tickers),
        }
