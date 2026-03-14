"""
NZT-48 Trading System — Main Orchestrator
The central coordinator that ties everything together.

Runs on APScheduler with automated scan schedule (Section IV).
Coordinates: data feeds → indicators → regime → strategies →
qualification → delivery. Runs continuously across all active bot instances.

The cognitive loop (Section 3):
INGEST → PERCEIVE → CLASSIFY → DECIDE → QUALIFY → SIZE → EXECUTE → LEARN
"""

from __future__ import annotations

import asyncio
import copy
import heapq
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Optional
from zoneinfo import ZoneInfo

# Add project root to path — must be first to avoid collision with config/ directory
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env file (safe in Docker where env vars are already set)
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env", override=False)
except ImportError:
    pass  # dotenv not installed — env vars must be set externally (Docker, etc.)

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

import config as cfg
from models import (
    Signal, Direction, Bot, BotInstance, RegimeState,
    MarketContext, IndicatorSnapshot, SectorFlow, NarrativeContext,
    SignalStatus,
)
from delivery.database import init_db, get_connection, transaction
from delivery.telegram_bot import TelegramDelivery, KillSwitch
from delivery.telegram_notifier import TelegramNotifier, get_notifier, P0, P1, P2, P3
from delivery.sheets_logger import SheetsLogger
from feeds.regime_classifier import RegimeClassifier, TimeOfDayEngine
from qualification.qualifier import QualificationPipeline
from qualification.risk_sizer import (
    ImmutableRiskRules, EmotionalFirewall, SessionProtection, DrawdownRecovery,
)
from qualification.profit_ladder import ProfitLadder, ETPProfitLadder
from execution.virtual_trader import VirtualTrader
from bots.specialist_bots import BotRouter
from bots.portfolio_overseer import PortfolioOverseer
from learning.learning_engine import LearningEngine
from learning.trade_autopsy import TradeAutopsyEngine
from learning.missed_trade_journal import MissedTradeJournal
from learning.adaptive_intelligence import AdaptiveIntelligenceEngine
from qualification.go_nogo import GoNoGoTracker
from learning.strategy_tournament import StrategyTournament
from bots.kelly_sizer import KellySizer
from qualification.dynamic_sizer import DynamicSizer
from qualification.circuit_breakers import CircuitBreakerSystem
from learning.edge_decay_engine import EdgeDecayEngine
from qualification.confluence_scorer import ConfluenceScorer
from execution.smart_routing import SmartRouter
from qualification.portfolio_risk import PortfolioRiskManager
from exceptions import HardGateError, SoftGateError
from feeds.correlation_matrix import RealTimeCorrelationMatrix
from feeds.data_validator import DataFeedValidator
from execution.session_manager import SessionBoundaryManager
from learning.performance_attribution import PerformanceAttributionEngine
from learning.expectancy_model import ExpectancyModel
from learning.execution_quality_model import ExecutionQualityModel
from learning.adaptive_engine import AdaptiveLearningEngine
from execution.exit_engine import ExitEngine
from feeds.attention_detector import AttentionDetector
from bots.sector_meta_bot import SectorRotationMetaBot
from learning.signal_logger import SignalLogger
from delivery.database import (
    get_daily_pnl, get_weekly_pnl, get_consecutive_losses,
    get_daily_trade_count, get_weekly_trade_count,
)
from delivery.pdf_intelligence import PDFIntelligenceReport
from signal_engine.pipeline_runner import run_pipeline, generate_preview_pdf, generate_scheduled_pdf
from core.sanity_gates import run_signal_sanity_gates
from core.scan_health import ScanHealthTracker
from core.stale_data_monitor import StaleDataMonitor
from core.universe_governance import UniverseGovernance
from core.trading_discipline import TradingDisciplineEngine
from core.scoped_query import ScopedQuery
from learning.guardrails import check_guardrails
from config.change_log import ChangeLogger
from delivery.dst_anchor import log_dst_state
from execution.planner import ExecutionPlanner
from core.provenance import ProvenanceRegistry, FreshnessChecker
from feeds.volume_profile import VolumeProfileEngine
from uk_isa.isa_eligibility import is_isa_eligible

# Universe Refresh Integration
try:
    from core.universe_refresh_integration import setup_universe_refresh_integration
    from core.universe_refresh_scheduler import UniverseRefreshScheduler, RefreshSchedule, UniverseSnapshot
    _UNIVERSE_REFRESH_AVAILABLE = True
except ImportError as _e:
    _UNIVERSE_REFRESH_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("UniverseRefresh not available: %s", _e)

# Tier-Based Trading System
try:
    from core.tier_based_entry_logic import TierBasedEntryDetector, EntryType, EntrySignal
    from core.tier_exit_enforcer import SessionExitEnforcer, ExitReason
    _TIER_BASED_AVAILABLE = True
except ImportError as _e:
    _TIER_BASED_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("Tier-based entry logic not available: %s", _e)

# H-01: Startup Readiness Gate — 8 pre-flight checks
try:
    from core.startup_gate import enforce_startup_gate
    _STARTUP_GATE_AVAILABLE = True
except ImportError as _e:
    _STARTUP_GATE_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("StartupGate not available: %s", _e)

# H-02: Invariant Enforcer — 12 runtime invariants
try:
    from core.invariant_enforcer import InvariantEnforcer
    _INVARIANT_ENFORCER_AVAILABLE = True
except ImportError as _e:
    _INVARIANT_ENFORCER_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("InvariantEnforcer not available: %s", _e)

# V8.0 State Manager — Redis SSOT with Lua atomicity
try:
    from core.state_manager import StateManager, GhostLedger
    _STATE_MANAGER_AVAILABLE = True
except ImportError as _e:
    _STATE_MANAGER_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("StateManager not available: %s", _e)

# Q1-Q10 Master Orchestrator Integration
try:
    from core.master_orchestrator import MasterOrchestrator, get_orchestrator
    from core.orchestrator_adapter import OrchestratorAdapter
    _MASTER_ORCHESTRATOR_AVAILABLE = True
except ImportError as _e:
    _MASTER_ORCHESTRATOR_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("Master Orchestrator not available: %s", _e)

# ─────────────────────────────────────────────────────────────────────────────
# V3.0 Microstructure + ML modules — imported with graceful fallback
# ─────────────────────────────────────────────────────────────────────────────
try:
    from core.earnings_fade_gate import EarningsFadeGate
    _EARNINGS_FADE_AVAILABLE = True
except ImportError as _e:
    _EARNINGS_FADE_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("EarningsFadeGate not available: %s", _e)

try:
    from core.portfolio_heat import PortfolioHeatMonitor
    _PORTFOLIO_HEAT_AVAILABLE = True
except ImportError as _e:
    _PORTFOLIO_HEAT_AVAILABLE = False

try:
    from core.iv_crush_monitor import IVCrushMonitor
    _IV_CRUSH_AVAILABLE = True
except ImportError as _e:
    _IV_CRUSH_AVAILABLE = False

try:
    from core.short_squeeze_monitor import ShortSqueezeMonitor
    _SHORT_SQUEEZE_AVAILABLE = True
except ImportError as _e:
    _SHORT_SQUEEZE_AVAILABLE = False

try:
    from core.chandelier_exit import ChandelierExit
    _CHANDELIER_AVAILABLE = True
except ImportError as _e:
    _CHANDELIER_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("ChandelierExit not available: %s", _e)

try:
    from core.realtime_data import SpreadHistoryTracker
    _SPREAD_TRACKER_AVAILABLE = True
except ImportError as _e:
    _SPREAD_TRACKER_AVAILABLE = False

try:
    from core.data_retention import DataRetentionManager
    _DATA_RETENTION_AVAILABLE = True
except ImportError as _e:
    _DATA_RETENTION_AVAILABLE = False

try:
    from core.expiry_pinning import ExpiryPinningMonitor
    _EXPIRY_PINNING_AVAILABLE = True
except ImportError as _e:
    _EXPIRY_PINNING_AVAILABLE = False

try:
    from core.window_dressing import WindowDressingMonitor
    _WINDOW_DRESSING_AVAILABLE = True
except ImportError as _e:
    _WINDOW_DRESSING_AVAILABLE = False

try:
    from core.gap_analytics import GapAnalytics
    _GAP_ANALYTICS_AVAILABLE = True
except ImportError as _e:
    _GAP_ANALYTICS_AVAILABLE = False

try:
    from core.day_of_week_filter import DayOfWeekFilter
    _DOW_FILTER_AVAILABLE = True
except ImportError as _e:
    _DOW_FILTER_AVAILABLE = False

try:
    from core.ml_meta_model import MLMetaModel
    _ML_META_AVAILABLE = True
except ImportError as _e:
    _ML_META_AVAILABLE = False

try:
    from core.earnings_sentiment import EarningsSentimentScorer
    _EARNINGS_SENTIMENT_AVAILABLE = True
except ImportError as _e:
    _EARNINGS_SENTIMENT_AVAILABLE = False

try:
    from core.realtime_data import RealtimeDataFeed
    _REALTIME_DATA_AVAILABLE = True
except ImportError as _e:
    _REALTIME_DATA_AVAILABLE = False

try:
    from core.profit_ladder import ProfitLadder as V3ProfitLadder
    _V3_LADDER_AVAILABLE = True
except ImportError as _e:
    _V3_LADDER_AVAILABLE = False

try:
    from core.indicator_ranking_report import IndicatorRankingReport
    _INDICATOR_RANKING_AVAILABLE = True
except ImportError as _e:
    _INDICATOR_RANKING_AVAILABLE = False

try:
    from learning.move_attribution import MoveAttribution
    _MOVE_ATTRIBUTION_AVAILABLE = True
except ImportError as _e:
    _MOVE_ATTRIBUTION_AVAILABLE = False

try:
    from core.intraday_momentum import IntradayMomentumEngine
    _INTRADAY_MOMENTUM_AVAILABLE = True
except ImportError as _e:
    _INTRADAY_MOMENTUM_AVAILABLE = False

try:
    from core.vwap_signal import VWAPSignalEngine
    _VWAP_SIGNAL_AVAILABLE = True
except ImportError as _e:
    _VWAP_SIGNAL_AVAILABLE = False

try:
    from core.liquidity_monitor import LiquidityMonitor
    _LIQUIDITY_MONITOR_AVAILABLE = True
except ImportError as _e:
    _LIQUIDITY_MONITOR_AVAILABLE = False

try:
    from core.sue_pead_scorer import SUEPEADScorer
    _SUE_PEAD_AVAILABLE = True
except ImportError as _e:
    _SUE_PEAD_AVAILABLE = False

try:
    from core.sector_momentum import SectorMomentumEngine
    _SECTOR_MOMENTUM_AVAILABLE = True
except ImportError as _e:
    _SECTOR_MOMENTUM_AVAILABLE = False

# ── V3.2 NEW MODULES ──────────────────────────────────────────────────────────
try:
    from core.performance_relegation import PerformanceRelegation
    _PERFORMANCE_RELEGATION_AVAILABLE = True
except ImportError as _e:
    _PERFORMANCE_RELEGATION_AVAILABLE = False

try:
    from core.order_flow_imbalance import OrderFlowImbalance
    _OFI_AVAILABLE = True
except ImportError as _e:
    _OFI_AVAILABLE = False

try:
    from core.overnight_gap_persistence import OvernightGapPersistence
    _OVERNIGHT_GAP_AVAILABLE = True
except ImportError as _e:
    _OVERNIGHT_GAP_AVAILABLE = False

try:
    from core.analyst_revision_tracker import AnalystRevisionTracker
    _ANALYST_REVISION_AVAILABLE = True
except ImportError as _e:
    _ANALYST_REVISION_AVAILABLE = False

try:
    from core.cross_asset_macro import CrossAssetMacro
    _CROSS_ASSET_AVAILABLE = True
except ImportError as _e:
    _CROSS_ASSET_AVAILABLE = False

try:
    from core.accruals_quality_veto import AccrualsQualityVeto
    _ACCRUALS_VETO_AVAILABLE = True
except ImportError as _e:
    _ACCRUALS_VETO_AVAILABLE = False

try:
    from core.earnings_calendar import EarningsCalendar
    _EARNINGS_CALENDAR_AVAILABLE = True
except ImportError as _e:
    _EARNINGS_CALENDAR_AVAILABLE = False

try:
    from core.net_expectancy import NetExpectancyEngine
    _NET_EXPECTANCY_AVAILABLE = True
except ImportError as _e:
    _NET_EXPECTANCY_AVAILABLE = False

try:
    from core.tail_loss_monitor import TailLossMonitor
    _TAIL_LOSS_AVAILABLE = True
except ImportError as _e:
    _TAIL_LOSS_AVAILABLE = False

try:
    from core.cost_drag_calculator import CostDragCalculator
    _COST_DRAG_AVAILABLE = True
except ImportError as _e:
    _COST_DRAG_AVAILABLE = False

try:
    from core.regime_stability_scorer import RegimeStabilityScorer
    _REGIME_STABILITY_AVAILABLE = True
except ImportError as _e:
    _REGIME_STABILITY_AVAILABLE = False

try:
    from core.capacity_monitor import CapacityConstraintMonitor
    _CAPACITY_MONITOR_AVAILABLE = True
except ImportError as _e:
    _CAPACITY_MONITOR_AVAILABLE = False

try:
    from learning.incremental_learner import IncrementalLearner
    _INCREMENTAL_LEARNER_AVAILABLE = True
except ImportError as _e:
    _INCREMENTAL_LEARNER_AVAILABLE = False

try:
    from learning.drift_detector import DriftDetector as V32DriftDetector
    _DRIFT_DETECTOR_AVAILABLE = True
except ImportError as _e:
    _DRIFT_DETECTOR_AVAILABLE = False

try:
    from learning.bayesian_win_rate import BayesianWinRate
    _BAYESIAN_WIN_RATE_AVAILABLE = True
except ImportError as _e:
    _BAYESIAN_WIN_RATE_AVAILABLE = False

try:
    from learning.ensemble_diversity import EnsembleDiversitySystem
    _ENSEMBLE_DIVERSITY_AVAILABLE = True
except ImportError as _e:
    _ENSEMBLE_DIVERSITY_AVAILABLE = False

try:
    from learning.active_learning_weighter import ActiveLearningWeighter
    _ACTIVE_LEARNING_AVAILABLE = True
except ImportError as _e:
    _ACTIVE_LEARNING_AVAILABLE = False

try:
    from learning.ai_research_engine import AIResearchEngine
    _AI_RESEARCH_AVAILABLE = True
except ImportError as _e:
    _AI_RESEARCH_AVAILABLE = False

# Wiring Validator — auto-detects dead code modules on every startup
try:
    from core.wiring_validator import validate_wiring, get_telegram_summary as _wiring_telegram_summary
    _WIRING_VALIDATOR_AVAILABLE = True
except ImportError:
    _WIRING_VALIDATOR_AVAILABLE = False
# ── END V3.2 NEW MODULES ──────────────────────────────────────────────────────

# V8.0 — Apex Predator Engine modules
try:
    try:
        from delivery.pdf_v2_momentum import MomentumPDFReport as _MomentumPDFReport
    except ImportError:
        from delivery.pdf_v2_momentum import PDFMomentumReport as _MomentumPDFReport  # type: ignore[assignment]
    _PDF_V2_MOMENTUM_AVAILABLE = True
except ImportError:
    _PDF_V2_MOMENTUM_AVAILABLE = False

try:
    from delivery.pdf_v2_risk import RiskPDFReport as _RiskPDFReport
    _PDF_V2_RISK_AVAILABLE = True
except ImportError as _e_risk:
    _PDF_V2_RISK_AVAILABLE = False
    # Log immediately so startup diagnostics show the problem
    logging.getLogger("nzt48.main").error(
        "pdf_v2_risk import FAILED at startup: %s — PDF2 (Pre-NYSE) will be degraded", _e_risk
    )

try:
    from delivery.pdf_v2_daily_review import DailyReviewPDFReport as _DailyReviewPDFReport
    _PDF_V2_DAILY_REVIEW_AVAILABLE = True
except ImportError as _e_review:
    _PDF_V2_DAILY_REVIEW_AVAILABLE = False
    logging.getLogger("nzt48.main").error(
        "pdf_v2_daily_review import FAILED at startup: %s — PDF3 (EOD Review) will be degraded", _e_review
    )

# V9.5 — CUSUM Alpha Reaper + Autonomous ML Daemon
try:
    from learning.cusum_alpha_reaper import CUSUMAlphaReaper
    _CUSUM_AVAILABLE = True
except ImportError as _e:
    _CUSUM_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("CUSUMAlphaReaper not available: %s", _e)

try:
    from learning.autonomous_ml_daemon import AutonomousMLDaemon
    _ML_DAEMON_AVAILABLE = True
except ImportError as _e:
    _ML_DAEMON_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("AutonomousMLDaemon not available: %s", _e)

try:
    from core.telemetry import TelemetryBuffer
    _TELEMETRY_AVAILABLE = True
except ImportError as _e:
    _TELEMETRY_AVAILABLE = False

# Wave 2 — Gaussian HMM Regime Classifier (Nystrup et al. 2017)
try:
    from core.regime_hmm import GaussianHMMRegimeClassifier
    _HMM_REGIME_AVAILABLE = True
except ImportError as _e:
    _HMM_REGIME_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("GaussianHMMRegimeClassifier not available: %s", _e)

# Wave 2 — ERC Portfolio Optimizer (Maillard et al. 2010)
try:
    from core.portfolio_optimizer import ERCPortfolioOptimizer
    _ERC_AVAILABLE = True
except ImportError as _e:
    _ERC_AVAILABLE = False
    logging.getLogger("nzt48.main").warning("ERCPortfolioOptimizer not available: %s", _e)

# Wave 2 — Correlation Engine for ERC covariance input
try:
    from uk_isa.correlation_engine import CorrelationEngine
    _CORR_ENGINE_AVAILABLE = True
except ImportError as _e:
    _CORR_ENGINE_AVAILABLE = False

# Configure logging
_LOG_DIR = _PROJECT_ROOT / "data"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)

# Rotating file handler — 10MB max, keep 5 backups
_log_file = _LOG_DIR / "nzt48.log"
_file_handler = RotatingFileHandler(
    str(_log_file),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
))
logging.getLogger("nzt48").addHandler(_file_handler)

logger = logging.getLogger("nzt48.main")


class _RowProxy:
    """Wraps a sqlite3.Row to support both dict-style and attribute access.

    Fix #11: Risk rules and firewall use attribute access (pos.ticker, pos.bot_instance)
    but sqlite3.Row only supports dict-style (row["ticker"]). This proxy bridges the gap.
    """

    __slots__ = ("_row",)

    def __init__(self, row):
        object.__setattr__(self, "_row", row)

    def __getattr__(self, name: str):
        try:
            return self._row[name]
        except (KeyError, IndexError):
            raise AttributeError(f"Row has no column '{name}'")

    def __getitem__(self, key):
        return self._row[key]

    def keys(self):
        return self._row.keys()


# ---------------------------------------------------------------------------
# V5.0 Ulysses Lock — market-hours protection (Phase 7)
# ---------------------------------------------------------------------------

async def enforce_read_only_market_hours(request: "Request", call_next):
    """Read-only API middleware during market hours (UK local, handles BST)."""
    from core.clock import is_market_hours_frozen
    if request.method in ("POST", "PUT", "DELETE"):
        if is_market_hours_frozen():
            if "emergency_halt" not in request.url.path:
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="MARKET_HOURS_FREEZE")
    return await call_next(request)


class FrozenConfig:
    """Freeze config at market open so live parameters cannot drift mid-session.

    During market hours (08:00-16:30 UK local time), returns the snapshot taken at
    freeze-time.  Outside market hours, returns the live (mutable) config.
    """

    def __init__(self):
        self._frozen_config = None
        self._frozen_at = None
        self._live_config: dict = {}

    def freeze(self):
        import yaml
        with open("config/settings.yaml") as f:
            self._frozen_config = yaml.safe_load(f)
        self._frozen_at = datetime.now(timezone.utc)

    def get(self, key, default=None):
        from core.clock import is_market_hours_frozen
        if is_market_hours_frozen() and self._frozen_config:
            return self._frozen_config.get(key, default)
        return self._live_config.get(key, default)


STRATEGY_KILL_RULES = {
    "max_consecutive_losses": 10,
    "max_daily_loss_pct": 0.02,
    "max_weekly_loss_pct": 0.05,
    "max_total_drawdown_pct": 0.15,
}


# ---------------------------------------------------------------------------
# APIPusher — engine → unified API IPC via HTTP POST
# ---------------------------------------------------------------------------

class _APIPusher:
    """Push engine state to the unified API server for WebSocket broadcast."""

    def __init__(self, base_url: str | None = None):
        self._base_url = base_url or os.environ.get("NZT48_API_URL", "http://localhost:8000")
        self._client: httpx.Client | None = None
        self._failures = 0

    def _get_client(self) -> httpx.Client | None:
        if httpx is None:
            return None
        if self._client is None:
            self._client = httpx.Client(timeout=5.0)
        return self._client

    def push_state(self, event_type: str, data: dict) -> bool:
        """Push an event (signal, position update, regime change) to the API."""
        client = self._get_client()
        if not client:
            return False
        try:
            resp = client.post(
                f"{self._base_url}/_internal/push_state",
                json={"event_type": event_type, "data": data},
            )
            self._failures = 0
            return resp.status_code == 200
        except Exception as exc:
            self._failures += 1
            if self._failures <= 3:
                logger.warning("[APIPusher] push_state failed: %s", exc)
            return False

    def push_cc_snapshot(self, snapshot: dict) -> bool:
        """Push full Command Center state snapshot to the API."""
        client = self._get_client()
        if not client:
            return False
        try:
            resp = client.post(
                f"{self._base_url}/_internal/push_cc_state",
                json=snapshot,
            )
            self._failures = 0
            return resp.status_code == 200
        except Exception as exc:
            self._failures += 1
            if self._failures <= 3:
                logger.warning("[APIPusher] push_cc_snapshot failed: %s", exc)
            return False

    def heartbeat(self) -> bool:
        """Send heartbeat to the API so dashboard knows engine is alive."""
        client = self._get_client()
        if not client:
            return False
        try:
            resp = client.post(f"{self._base_url}/_internal/heartbeat")
            return resp.status_code == 200
        except Exception:
            return False

    def push_signal(self, signal) -> bool:
        """Push a new signal event to the API for dashboard broadcast."""
        try:
            data = {
                "ticker": signal.ticker,
                "direction": signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction),
                "strategy": signal.strategy,
                "confidence": signal.confidence,
                "entry": signal.entry,
                "stop": signal.stop,
                "target_1r": signal.target_1r,
                "timestamp": signal.timestamp.isoformat() if hasattr(signal.timestamp, "isoformat") else str(signal.timestamp),
            }
            return self.push_state("signal", data)
        except Exception as exc:
            if self._failures <= 3:
                logger.warning("[APIPusher] push_signal failed: %s", exc)
            return False

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class _MegaFallbackCompleted(Exception):
    """Sentinel exception used to break out of the mega PDF try-block
    after the fallback (individual V2 PDFs) has already executed.
    Never propagated — caught immediately in the except clause."""
    pass


# ---------------------------------------------------------------------------
# V5.0 Heartbeat API endpoint (Phase 17)
# ---------------------------------------------------------------------------
# Standalone async handler — register on any FastAPI app via:
#   app.get("/api/heartbeat")(get_heartbeat)

async def get_heartbeat():
    """Return engine heartbeat status from /tmp/nzt48_heartbeat.json.

    The tick loop writes this file every cycle.  If the file is stale
    (>90 s) or missing, the engine is considered DEAD.
    """
    import json as _json
    import time as _time
    try:
        hb = _json.loads(Path("/tmp/nzt48_heartbeat.json").read_text())
        age_seconds = _time.time() - hb["epoch"]
        hb["age_seconds"] = round(age_seconds, 1)
        hb["healthy"] = age_seconds < 90
        return hb
    except Exception:
        return {"status": "DEAD", "healthy": False}


# ─────────────────────────────────────────────────────────────────────────────
# C-12: Priority Signal Queue — heapq sorted by -composite_score
# ─────────────────────────────────────────────────────────────────────────────
class PrioritySignalQueue:
    """Thread-safe priority queue for signals, sorted by -composite_score (highest first).

    Drop-in replacement for queue.Queue with put_nowait() / get_nowait() API.
    Uses a min-heap with negated scores so highest-priority signals are dequeued first.
    """

    def __init__(self, maxsize: int = 50):
        self._maxsize = maxsize
        self._heap: list[tuple[float, int, dict]] = []  # (-score, seq, payload)
        self._seq = 0  # tie-breaker for equal scores
        self._lock = threading.Lock()

    def put_nowait(self, item: dict) -> None:
        """Add a signal dict to the priority queue. Raises Full if at capacity."""
        with self._lock:
            if len(self._heap) >= self._maxsize:
                raise Full(f"PrioritySignalQueue at capacity ({self._maxsize})")
            signal_obj = item.get("signal")
            score = getattr(signal_obj, "confidence", 0) if signal_obj else 0
            heapq.heappush(self._heap, (-score, self._seq, item))
            self._seq += 1

    def get_nowait(self) -> dict:
        """Remove and return the highest-priority signal. Raises Empty if empty."""
        with self._lock:
            if not self._heap:
                raise Empty("PrioritySignalQueue is empty")
            _, _, item = heapq.heappop(self._heap)
            return item

    def qsize(self) -> int:
        with self._lock:
            return len(self._heap)

    def empty(self) -> bool:
        with self._lock:
            return len(self._heap) == 0

    def full(self) -> bool:
        with self._lock:
            return len(self._heap) >= self._maxsize


class NZT48Orchestrator:
    """Main orchestrator for the NZT-48 trading system.

    Coordinates all system components through the cognitive loop.
    Manages the APScheduler for automated scan scheduling.
    """

    def __init__(self) -> None:
        logger.info("Initializing NZT-48 Trading System...")

        # Load configuration
        self.config = cfg.load_config()
        self._version = cfg.get("system.version", "8.0")
        self._rvol_scan_cache: dict[str, datetime] = {}
        self._gate_failure_counts: dict[str, int] = {}
        self._safety_failure_counts: dict[str, int] = {}  # 0-08: consecutive failure tracker per safety subsystem
        self._safety_halted: bool = False  # 0-08: set True after 3 consecutive failures in any safety subsystem
        self._background_tasks: set[asyncio.Task] = set()  # C-17: prevent GC of fire-and-forget tasks
        logger.info("System version: v%s", self._version)
        self.mode = cfg.get("system.mode", "PAPER")
        self.equity = float(cfg.get("system.starting_equity", 10_000))  # From config, default £10k ISA

        # Core components
        self.regime_classifier = RegimeClassifier()
        self.time_engine = TimeOfDayEngine()
        self.qualifier = QualificationPipeline(equity=self.equity)
        self.risk_rules = ImmutableRiskRules()
        self.firewall = EmotionalFirewall()
        self.session_protection = SessionProtection()
        self.drawdown_recovery = DrawdownRecovery()
        self.profit_ladder = ProfitLadder()
        self.etp_ladder = ETPProfitLadder()
        self.kill_switch = KillSwitch()

        # Delivery
        self.telegram = TelegramDelivery()
        self.sheets = SheetsLogger(starting_equity=self.equity)

        # Tiered notification bus (Hyman et al. 2019 — alert fatigue prevention)
        # P0: always send | P1: max 3/day | P2: max 5/day | P3: nightly digest
        try:
            from core.telegram_event_bus import get_event_bus as _get_bus
            self._tg_bus = _get_bus()
            self._tg_bus.set_sender(self.telegram)
            logger.info("TelegramEventBus initialised — P1 cap=%d P2 cap=%d", 3, 5)
        except Exception as _tg_bus_err:
            self._tg_bus = None
            logger.warning("TelegramEventBus init failed (non-critical): %s", _tg_bus_err)

        # H-04/H-05/H-11: Tiered notification architecture with fallback defence-in-depth
        try:
            self._notifier = get_notifier()
            self._notifier.set_telegram_sender(self.telegram)
            logger.info(
                "TelegramNotifier (AEGIS H-04/H-05/H-11) initialised — "
                "P0=instant+sound, P1=silent, P2=30min batch, P3=2x daily digest"
            )
        except Exception as _notifier_err:
            self._notifier = None
            logger.warning("TelegramNotifier init failed (non-critical): %s", _notifier_err)

        # Virtual execution engine
        self.virtual_trader = VirtualTrader()

        # Connect VirtualTrader to SQLite for trade persistence
        try:
            _vt_db = get_connection()
            self.virtual_trader.set_db(_vt_db)
            logger.info("VirtualTrader: database connected for trade persistence")
        except Exception as _vt_db_err:
            logger.error("VirtualTrader: database connection FAILED — trades will NOT persist: %s", _vt_db_err)

        # Multi-bot architecture
        self.bot_router = BotRouter()
        self.overseer = PortfolioOverseer()

        # Learning engine (10 modules)
        self.learning = LearningEngine()
        self.kelly = KellySizer()
        self.learning.set_kelly_callback(self.kelly.add_trade)

        # Trade Autopsy — 5-grade post-trade analysis feeds learning
        self.trade_autopsy = TradeAutopsyEngine()

        # Missed Trade Journal — tracks rejected signals to evaluate filter effectiveness
        self.missed_trade_journal = MissedTradeJournal()

        # Go/No-Go Scorecard — tracks 10 launch criteria for paper→live transition
        self.go_nogo = GoNoGoTracker()

        # Strategy Tournament — Darwinian capital allocation
        self.tournament = StrategyTournament()

        # Adaptive Intelligence Engine — AI-powered nightly learning cycle
        ai_key = os.environ.get("GEMINI_API_KEY", "")
        self.adaptive_intel = AdaptiveIntelligenceEngine(
            ai_api_key=ai_key, ai_model="gemini-2.5-flash",
        )

        # Adaptive Learning Engine — outcome-driven trade selection improvement
        self.adaptive_engine = AdaptiveLearningEngine()
        self.adaptive_engine.load_playbook()

        # Load auto-approved parameter improvements (written by auto_apply_improvements)
        # This is the "today's excellence is tomorrow's average" feedback loop:
        # improved thresholds from last night's analysis are applied at startup today.
        self._approved_params = self.adaptive_engine.load_approved_params()
        if self._approved_params:
            logger.info("STARTUP: Applied %d auto-tuned parameter(s): %s",
                       len(self._approved_params), self._approved_params)

        # === INSTITUTIONAL-GRADE MODULES (Section 37-42) ===

        # Dynamic Position Sizer — 8-factor Kelly + vol + regime + streak sizing
        self.dynamic_sizer = DynamicSizer(
            starting_equity=self.equity,
            max_portfolio_heat=float(cfg.get("dynamic_sizer.max_portfolio_heat", 0.06)),
        )

        # Circuit Breaker System — 5 independent breakers (drawdown, VIX, correlation, streaks, black swan)
        # A-06: Pass sync Redis client so CB state survives Docker/IBC restarts
        _cb_redis = None
        try:
            import redis as _cb_redis_mod
            _cb_redis = _cb_redis_mod.Redis(
                host='redis', port=6379,
                password='nzt48redis', decode_responses=True,
                socket_connect_timeout=5, socket_timeout=5,
            )
            _cb_redis.ping()
            logger.info("CircuitBreakers: sync Redis client connected")
        except Exception as _cb_redis_err:
            logger.warning("CircuitBreakers: Redis unavailable, in-memory mode: %s", _cb_redis_err)
            _cb_redis = None
        self.circuit_breakers = CircuitBreakerSystem(
            equity=self.equity, redis_client=_cb_redis,
        )

        # Edge Decay Engine — intraday alpha curve tracking per strategy × regime × 30min bucket
        self.edge_decay = EdgeDecayEngine()

        # Confluence Scorer — multi-timeframe agreement (5min, 15min, 1h, daily, weekly)
        self.confluence_scorer = ConfluenceScorer()

        # Smart Order Router — liquidity scoring, slippage prediction, execution timing
        self.smart_router = SmartRouter()

        # Portfolio Risk Manager — 8-dimension risk decomposition + trade gate
        self.portfolio_risk = PortfolioRiskManager(
            equity=self.equity,
            daily_risk_budget_pct=float(cfg.get("portfolio_risk.daily_budget_pct", 0.03)),
        )

        # === TIER 2 INSTITUTIONAL MODULES ===

        # Real-Time Correlation Matrix — Welford's algorithm for live pairwise correlations
        self.correlation_matrix = RealTimeCorrelationMatrix()

        # Data Feed Validator — bar integrity, staleness detection, quality scoring
        self.data_validator = DataFeedValidator()

        # Session Boundary Manager — 8-phase session awareness + fatigue tracking
        self.session_manager = SessionBoundaryManager()

        # Performance Attribution Engine — 6-factor trade decomposition
        self.perf_attribution = PerformanceAttributionEngine()

        # Expectancy Model — signal gate using Edge Ledger data
        self.expectancy_model = ExpectancyModel()

        # Execution Quality Model — fill risk + slippage prediction
        self.execution_quality_model = ExecutionQualityModel()

        # Exit Engine — track-aware exit scoring for open positions
        self.exit_engine = ExitEngine()

        # B-Team Manager — Promotion/Relegation system for ticker universe rotation
        try:
            from strategies.b_team_manager import BTeamManager
            self.b_team = BTeamManager(state_path="data/b_team_state.json")
            self.b_team.initialize()
            logger.info("B-Team Manager initialized: A=%d B=%d",
                       len(self.b_team.get_a_team()), len(self.b_team.get_b_team()))
        except Exception as e:
            self.b_team = None
            logger.warning("B-Team Manager failed to initialize: %s", e)

        # Scan Health Tracker — heartbeat monitoring for scan loop
        self.scan_health = ScanHealthTracker.instance()

        # F-14: Stale Data Tick-Change Counter — detects frozen feeds
        self.stale_data_monitor = StaleDataMonitor(
            stale_threshold_sec=float(cfg.get("stale_data_monitor.stale_threshold_sec", 300)),
            halt_fraction=float(cfg.get("stale_data_monitor.halt_fraction", 0.50)),
            min_tickers_for_halt=int(cfg.get("stale_data_monitor.min_tickers_for_halt", 4)),
        )

        # Universe Governance — auto-delist dead tickers, auto-promote clean ones
        self.universe_governance = UniverseGovernance()

        # Config change detection — log startup changes
        try:
            change_log = ChangeLogger()
            change = change_log.check_for_changes()
            if change:
                logger.warning("CONFIG CHANGED since last run: %s", change)
        except Exception as e:
            logger.warning("Config change log check failed: %s", e)

        # DST anchor — log DST state for scheduling awareness
        try:
            log_dst_state()
        except Exception as e:
            logger.warning("DST state logging failed: %s", e)

        # Attention Detector — tracks attention exhaustion lifecycle per ticker
        self.attention_detector = AttentionDetector()

        # Sector Rotation Meta-Bot — adjusts confidence based on sector flows
        self.sector_meta_bot = SectorRotationMetaBot()

        # Execution Planner — cost-aware execution plans with spread gate + DNT checks
        self.execution_planner = ExecutionPlanner()

        # Provenance Registry — tracks data freshness with per-field TTLs
        # Reads provenance_tracking feature flag from settings.yaml
        try:
            _prov_enabled = bool(cfg.get("feature_flags.provenance_tracking", False))
        except Exception:
            _prov_enabled = False
        self.provenance_registry = ProvenanceRegistry(enabled=_prov_enabled)

        # Volume Profile Engine — structural level analysis (POC, VA, HVN/LVN)
        self.volume_profile_engine = VolumeProfileEngine()

        # Tier-Based Entry Logic & Exit Enforcement
        self.tier_entry_detector = None
        self.tier_exit_enforcer = None
        if _TIER_BASED_AVAILABLE:
            try:
                self.tier_entry_detector = TierBasedEntryDetector()
                self.tier_exit_enforcer = SessionExitEnforcer()
                logger.info("Tier-based trading system initialized (Type A/B/C entry patterns, position sizing, Tier 3 exit discipline)")
            except Exception as _tier_err:
                logger.warning("Tier-based trading system init failed: %s", _tier_err)

        # Signal Logger — logs every signal to data/signal_log.jsonl for future analysis
        try:
            self.signal_logger = SignalLogger()
        except Exception as e:
            logger.warning("SignalLogger init failed (non-critical): %s", e)
            self.signal_logger = None

        # H-10: CloudWatch Metrics Emitter — system health metrics every 60s
        self.cloudwatch_emitter = None
        try:
            from core.cloudwatch_metrics import CloudWatchMetricsEmitter
            self.cloudwatch_emitter = CloudWatchMetricsEmitter()
            if self.cloudwatch_emitter.available:
                logger.info("CloudWatch metrics emitter initialized (namespace=NZT48)")
            else:
                logger.info("CloudWatch metrics emitter loaded but unavailable (boto3/creds missing)")
        except Exception as _cw_err:
            logger.warning("CloudWatch metrics emitter init failed (non-critical): %s", _cw_err)

        # Trading Discipline Engine — absolute veto power over every trade
        # "Today's excellence is tomorrow's average. No trade > bad trade."
        self.discipline = TradingDisciplineEngine()

        # ─────────────────────────────────────────────────────────────────────
        # V3.0 MICROSTRUCTURE + ML MODULES
        # ─────────────────────────────────────────────────────────────────────

        # RC-07b: Earnings Fade Gate — Buy-the-Rumour / Sell-the-News (Kim & Verrecchia 1991)
        self.earnings_fade_gate = EarningsFadeGate() if _EARNINGS_FADE_AVAILABLE else None

        # V3.2 Data Retention Manager — ring buffer, outcomes rotation, model backup, WAL mode
        self.data_retention = DataRetentionManager() if _DATA_RETENTION_AVAILABLE else None

        # RC-02: Portfolio Heat Monitor — aggregate daily P&L guard
        self.portfolio_heat = PortfolioHeatMonitor() if _PORTFOLIO_HEAT_AVAILABLE else None

        # IV Crush Monitor — pre-event IV inflation / post-announcement crush detection
        self.iv_crush = IVCrushMonitor() if _IV_CRUSH_AVAILABLE else None

        # Short Squeeze Monitor — FINRA short interest, squeeze confidence boost
        self.short_squeeze = ShortSqueezeMonitor() if _SHORT_SQUEEZE_AVAILABLE else None

        # Mandate 5+9: Chandelier Exit with Redis persistence
        self.chandelier = None
        if _CHANDELIER_AVAILABLE:
            try:
                import os as _os
                _redis_client = None
                _redis_url = _os.getenv("REDIS_URL")
                if _redis_url:
                    try:
                        import redis as _redis_mod
                        _redis_client = _redis_mod.from_url(_redis_url)
                        _redis_client.ping()
                        logger.info("ChandelierExit: Redis connected at %s", _redis_url)
                    except Exception as _redis_err:
                        logger.warning("ChandelierExit: Redis unavailable, in-memory mode: %s", _redis_err)
                        _redis_client = None
                self.chandelier = ChandelierExit(redis_client=_redis_client)
            except Exception as _ce:
                logger.warning("ChandelierExit init failed: %s", _ce)

        # V8.0 State Manager — Redis SSOT for positions, equity, P&L, kill switch
        self.state_manager = None
        self.ghost_ledger = None
        self._frozen_config_hash: str | None = None
        if _STATE_MANAGER_AVAILABLE:
            try:
                _sm_db = get_connection()
                self.state_manager = StateManager(
                    redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
                    redis_password=os.environ.get("REDIS_PASSWORD", "nzt48redis"),
                    initial_equity=self.equity,
                    db=_sm_db,
                )
                self.ghost_ledger = GhostLedger(self.state_manager)
                logger.info("StateManager + GhostLedger initialized (equity=%.2f)", self.equity)
            except Exception as _sm_err:
                logger.warning("StateManager init failed (non-critical, in-memory fallback): %s", _sm_err)

        # Mandate 10: Dynamic P90 Fallback Spread Tracker
        self.spread_tracker = SpreadHistoryTracker() if _SPREAD_TRACKER_AVAILABLE else None

        # Options Expiry Pinning — 3rd Friday / weekly expiry calendar
        self.expiry_pinning = ExpiryPinningMonitor() if _EXPIRY_PINNING_AVAILABLE else None

        # Window Dressing Monitor — quarter-end fund buying / new-Q unwind
        self.window_dressing = WindowDressingMonitor() if _WINDOW_DRESSING_AVAILABLE else None

        # Gap Analytics — gap-and-go vs gap-fade classification
        self.gap_analytics = GapAnalytics() if _GAP_ANALYTICS_AVAILABLE else None

        # Day-of-Week Filter — Monday penalty, Friday bonus
        self.dow_filter = DayOfWeekFilter() if _DOW_FILTER_AVAILABLE else None

        # LightGBM Meta-Model — Stage 2 ML confidence blending (70% rule + 30% ML)
        self.ml_model = MLMetaModel() if _ML_META_AVAILABLE else None

        # V9.5 CUSUM Alpha Reaper — Page (1954) edge decay detection
        self.cusum_reaper = CUSUMAlphaReaper() if _CUSUM_AVAILABLE else None

        # V9.5 Telemetry Buffer — per-trade feature snapshots in Redis
        self._telemetry_buffer = None
        if _TELEMETRY_AVAILABLE and self.state_manager:
            try:
                self._telemetry_buffer = TelemetryBuffer(self.state_manager)
                if hasattr(self, 'virtual_trader'):
                    self.virtual_trader.set_telemetry_buffer(self._telemetry_buffer)
                logger.info("[V9.5] TelemetryBuffer initialized")
            except Exception as _tb_err:
                logger.warning("[V9.5] TelemetryBuffer init failed: %s", _tb_err)

        # V9.5 Autonomous ML Daemon — daily Ouroboros loop
        self.ml_daemon = None
        if _ML_DAEMON_AVAILABLE:
            _daemon_tickers = cfg.get_isa_tickers() if cfg.get_primary_mode() == "UK_ISA" else cfg.get_tickers()
            self.ml_daemon = AutonomousMLDaemon(
                ml_model=self.ml_model,
                cusum_reaper=self.cusum_reaper,
                state_manager=self.state_manager,
                tickers=_daemon_tickers,
            )
            logger.info("[V9.5] AutonomousMLDaemon initialized (tickers=%d)", len(_daemon_tickers))

        # Wave 2 — Gaussian HMM Regime Classifier (Nystrup et al. 2017)
        self.hmm_regime: object | None = None
        if _HMM_REGIME_AVAILABLE and cfg.get("v95_hmm_regime_enabled", True):
            self.hmm_regime = GaussianHMMRegimeClassifier()
            logger.info("[Wave2] GaussianHMMRegimeClassifier initialized")

        # Wave 2 — ERC Portfolio Optimizer (Maillard et al. 2010)
        self.erc_optimizer: object | None = None
        self._correlation_engine: object | None = None
        if _ERC_AVAILABLE and cfg.get("v95_erc_allocation_enabled", True):
            self.erc_optimizer = ERCPortfolioOptimizer()
            if _CORR_ENGINE_AVAILABLE:
                self._correlation_engine = CorrelationEngine()
            # Attach ERC optimizer to DynamicSizer for per-ticker weight scaling
            self.dynamic_sizer.set_erc_optimizer(self.erc_optimizer)
            logger.info("[Wave2] ERCPortfolioOptimizer initialized + attached to DynamicSizer")

        # Earnings Sentiment Scorer — Gemini NLP on earnings headlines
        self.earnings_sentiment = EarningsSentimentScorer() if _EARNINGS_SENTIMENT_AVAILABLE else None

        # Real-Time Data Feed — Polygon (US) + TwelveData (LSE) priority chain
        self.realtime_feed = RealtimeDataFeed() if _REALTIME_DATA_AVAILABLE else None

        # Indicator Ranking Report — Friday weekly Telegram report
        self.indicator_ranking = IndicatorRankingReport() if _INDICATOR_RANKING_AVAILABLE else None

        # Move Attribution — catalyst → ETP chain reaction tracker
        self.move_attribution = MoveAttribution() if _MOVE_ATTRIBUTION_AVAILABLE else None

        logger.info(
            "V3.0 modules loaded: fade_gate=%s heat=%s iv=%s squeeze=%s pinning=%s "
            "wd=%s gap=%s dow=%s ml=%s sentiment=%s rt_data=%s ladder=%s attribution=%s",
            _EARNINGS_FADE_AVAILABLE, _PORTFOLIO_HEAT_AVAILABLE, _IV_CRUSH_AVAILABLE,
            _SHORT_SQUEEZE_AVAILABLE, _EXPIRY_PINNING_AVAILABLE, _WINDOW_DRESSING_AVAILABLE,
            _GAP_ANALYTICS_AVAILABLE, _DOW_FILTER_AVAILABLE, _ML_META_AVAILABLE,
            _EARNINGS_SENTIMENT_AVAILABLE, _REALTIME_DATA_AVAILABLE, _V3_LADDER_AVAILABLE,
            _MOVE_ATTRIBUTION_AVAILABLE,
        )

        # ─────────────────────────────────────────────────────────────────────
        # V3.1 ACADEMIC RESEARCH MODULES (5 new signals from institutional research)
        # ─────────────────────────────────────────────────────────────────────

        # Intraday Momentum — Gao, Han, Li & Zhou (2018): first-half-hour predicts last
        self.intraday_momentum = IntradayMomentumEngine() if _INTRADAY_MOMENTUM_AVAILABLE else None

        # VWAP Signal Engine — Madhavan, Richardson & Roomans (1997): VWAP as institutional anchor
        self.vwap_engine = VWAPSignalEngine() if _VWAP_SIGNAL_AVAILABLE else None

        # Liquidity Black Hole Monitor — Brunnermeier & Pedersen (2009): spiral detection
        self.liquidity_monitor = LiquidityMonitor() if _LIQUIDITY_MONITOR_AVAILABLE else None

        # SUE / PEAD Scorer — Bernard & Thomas (1989): post-earnings drift quantification
        self.sue_pead = SUEPEADScorer() if _SUE_PEAD_AVAILABLE else None

        # Sector Momentum Engine — Moskowitz & Grinblatt (1999): sector explains individual momentum
        self.sector_momentum_engine = SectorMomentumEngine() if _SECTOR_MOMENTUM_AVAILABLE else None

        logger.info(
            "V3.1 academic modules: intraday_momentum=%s vwap=%s liquidity=%s sue_pead=%s sector_mom=%s",
            _INTRADAY_MOMENTUM_AVAILABLE, _VWAP_SIGNAL_AVAILABLE, _LIQUIDITY_MONITOR_AVAILABLE,
            _SUE_PEAD_AVAILABLE, _SECTOR_MOMENTUM_AVAILABLE,
        )

        # Q1-Q10 Master Orchestrator — unified pipeline for all signal generation
        try:
            _mo_config = {
                'use_postgresql': False,
                'use_fpga': False,
                'use_quantum': False,
                'sqlite_path': cfg.get('database.path', 'data/nzt48.db'),
                'universe': cfg.get('system.universe', ['QQQ3.L', '3LUS.L', 'TSL3.L']),
            }
            self.master_orchestrator = MasterOrchestrator(_mo_config) if _MASTER_ORCHESTRATOR_AVAILABLE else None
            if self.master_orchestrator:
                logger.info("✅ Q1-Q10 Master Orchestrator initialized — all 10 phases ready")
            else:
                logger.warning("⚠️  Master Orchestrator unavailable — falling back to legacy pipeline")
        except Exception as _mo_err:
            logger.error("Master Orchestrator initialization failed: %s", _mo_err)
            self.master_orchestrator = None


        # ─────────────────────────────────────────────────────────────────────
        # V3.2 NEW ACADEMIC SIGNALS + INSTITUTIONAL RISK + SELF-LEARNING
        # ─────────────────────────────────────────────────────────────────────

        # W2 — Performance Relegation (A-team/B-team tier management)
        self.performance_relegation = PerformanceRelegation() if _PERFORMANCE_RELEGATION_AVAILABLE else None

        # W4 — Order Flow Imbalance (Chordia & Subrahmanyam 2004)
        self.order_flow_imbalance = OrderFlowImbalance() if _OFI_AVAILABLE else None

        # W4 — Overnight Gap Persistence (Lou, Polk & Sornette 2013)
        self.overnight_gap = OvernightGapPersistence() if _OVERNIGHT_GAP_AVAILABLE else None

        # W4 — Analyst Revision Tracker (Boni 2004, Womack 1996)
        self.analyst_revision = AnalystRevisionTracker() if _ANALYST_REVISION_AVAILABLE else None

        # W4 — Cross-Asset Macro (Erb & Harvey 2006: VIX term structure, DXY, credit spread)
        self.cross_asset_macro = CrossAssetMacro() if _CROSS_ASSET_AVAILABLE else None

        # W4 — Accruals Quality Veto (Sloan 1996: high accruals = lower quality earnings)
        self.accruals_veto = AccrualsQualityVeto() if _ACCRUALS_VETO_AVAILABLE else None

        # W4/W6 — Earnings Calendar (auto-fetches upcoming earnings, feeds SUE/PEAD)
        self.earnings_calendar = EarningsCalendar() if _EARNINGS_CALENDAR_AVAILABLE else None

        # W9 — Net Expectancy Engine (Thorp 1997, Vince 1992)
        self.net_expectancy = NetExpectancyEngine() if _NET_EXPECTANCY_AVAILABLE else None

        # W9 — Tail Loss Monitor (Taleb 2007, Bali et al. 2011)
        self.tail_loss = TailLossMonitor() if _TAIL_LOSS_AVAILABLE else None

        # W9 — Cost Drag Calculator (Frazzini, Israel & Moskowitz 2015)
        self.cost_drag = CostDragCalculator() if _COST_DRAG_AVAILABLE else None

        # W9 — Regime Stability Scorer (Guidolin & Timmermann 2007)
        self.regime_stability = RegimeStabilityScorer() if _REGIME_STABILITY_AVAILABLE else None

        # W9 — Capacity Constraint Monitor (Bouchaud et al. 2009, Zhu 2014)
        self.capacity_monitor = CapacityConstraintMonitor() if _CAPACITY_MONITOR_AVAILABLE else None

        # W12 — Incremental Passive-Aggressive Learner (Crammer et al. 2006)
        self.incremental_learner = IncrementalLearner() if _INCREMENTAL_LEARNER_AVAILABLE else None

        # W12 — Concept Drift Detector (Page-Hinkley + exponential forgetting)
        self.v32_drift_detector = V32DriftDetector() if _DRIFT_DETECTOR_AVAILABLE else None

        # W12 — Bayesian Win Rate Estimator (Gelman et al. 2013, beta-binomial)
        self.bayesian_win_rate = BayesianWinRate() if _BAYESIAN_WIN_RATE_AVAILABLE else None

        # W12 — Ensemble Diversity System (Dietterich 2000, Kuncheva & Whitaker 2003)
        self.ensemble_diversity = EnsembleDiversitySystem() if _ENSEMBLE_DIVERSITY_AVAILABLE else None

        # W12 — Active Learning Weighter (Settles 2009, uncertainty sampling)
        self.active_learning = ActiveLearningWeighter() if _ACTIVE_LEARNING_AVAILABLE else None

        # W12+ — AI Research Engine (Gemini 2.5 Flash / OpenAI academic reasoning layer)
        self.ai_research = AIResearchEngine() if _AI_RESEARCH_AVAILABLE else None

        logger.info(
            "V3.2 modules loaded: relegation=%s ofi=%s gap_persist=%s analyst=%s "
            "macro=%s accruals=%s earnings_cal=%s net_e=%s tail=%s cost=%s "
            "regime_stab=%s capacity=%s incr_learn=%s drift=%s bayes=%s "
            "ensemble=%s active=%s ai_research=%s",
            _PERFORMANCE_RELEGATION_AVAILABLE, _OFI_AVAILABLE, _OVERNIGHT_GAP_AVAILABLE,
            _ANALYST_REVISION_AVAILABLE, _CROSS_ASSET_AVAILABLE, _ACCRUALS_VETO_AVAILABLE,
            _EARNINGS_CALENDAR_AVAILABLE, _NET_EXPECTANCY_AVAILABLE, _TAIL_LOSS_AVAILABLE,
            _COST_DRAG_AVAILABLE, _REGIME_STABILITY_AVAILABLE, _CAPACITY_MONITOR_AVAILABLE,
            _INCREMENTAL_LEARNER_AVAILABLE, _DRIFT_DETECTOR_AVAILABLE,
            _BAYESIAN_WIN_RATE_AVAILABLE, _ENSEMBLE_DIVERSITY_AVAILABLE,
            _ACTIVE_LEARNING_AVAILABLE, _AI_RESEARCH_AVAILABLE,
        )

        # Wire learning engine into virtual trader — closed trades auto-feed learning
        self.virtual_trader.register_trade_callback(self._on_trade_closed)

        # Load edge decay and dynamic sizer state from database (if available)
        try:
            with transaction() as conn:
                self.edge_decay.load_state(conn)
                # Load historical R-multiples for dynamic sizer warm-up
                rows = conn.execute(
                    "SELECT r_multiple, ticker FROM virtual_trades ORDER BY exit_time ASC"
                ).fetchall()
                if rows:
                    r_multiples = [row["r_multiple"] for row in rows if row["r_multiple"] is not None]
                    tickers = [row["ticker"] or "" for row in rows if row["r_multiple"] is not None]
                    self.dynamic_sizer.load_history(r_multiples, tickers=tickers)
                    logger.info("Dynamic sizer loaded %d historical trades", len(r_multiples))
        except Exception as e:
            logger.warning("State loading for new modules failed: %s", e)

        # Data feeds (lazy-loaded)
        self._latest_premarket_brief = None
        self._data_feeds = None
        self._market_structure = None
        self._indicator_engine = None
        self._pattern_detector = None
        self._calendar_feed = None
        self._news_feed = None
        self._screener = None

        # Pre-market intelligence (lazy-loaded)
        self._holdings = None
        self._premarket_engine = None

        # Strategies (lazy-loaded)
        self._strategies: list = []

        # State tracking
        self._current_market_ctx: Optional[MarketContext] = None
        self._daily_pnl_pct: float = 0.0
        self._weekly_pnl_pct: float = 0.0
        self._consecutive_losses: int = 0
        self._last_stopout_time: Optional[datetime] = None

        # API Pusher (engine → dashboard IPC)
        self._api_pusher = _APIPusher()

        # Signal queue — decouples signal generation from execution (V5.0)
        # C-12: heapq priority queue sorted by -composite_score (highest first)
        self._signal_queue: PrioritySignalQueue = PrioritySignalQueue(maxsize=50)

        # ─────────────────────────────────────────────────────────────────────
        # H-02: Invariant Enforcer — 12 runtime invariants
        # ─────────────────────────────────────────────────────────────────────
        self.invariant_enforcer = None
        if _INVARIANT_ENFORCER_AVAILABLE:
            try:
                self.invariant_enforcer = InvariantEnforcer(
                    redis_client=_cb_redis,
                    circuit_breakers=self.circuit_breakers,
                    virtual_trader=self.virtual_trader,
                    risk_rules=self.risk_rules,
                    state_manager=self.state_manager,
                    equity=self.equity,
                    flatten_callback=self._invariant_flatten,
                    alert_callback=self._invariant_alert,
                )
                logger.info("InvariantEnforcer initialized with 12 invariants")
            except Exception as _ie_err:
                logger.warning("InvariantEnforcer init failed (non-critical): %s", _ie_err)

        logger.info("NZT-48 initialized in %s mode", self.mode)

    # ── H-02: Invariant Enforcer Callbacks ─────────────────────────────────
    def _invariant_flatten(self) -> None:
        """Emergency flatten all positions triggered by invariant violation."""
        try:
            open_positions = list(self.virtual_trader.open_positions.values())
            for pos in open_positions:
                if getattr(pos, "status", "OPEN") == "OPEN":
                    exit_price = self._derive_exit_price_simple(pos)
                    self.virtual_trader.close_position(
                        pos.id, exit_price, reason="INVARIANT_VIOLATION_FLATTEN"
                    )
            logger.critical(
                "INVARIANT_FLATTEN: Closed %d positions", len(open_positions),
            )
        except Exception as e:
            logger.critical("INVARIANT_FLATTEN failed: %s", e)

    def _invariant_alert(self, message: str) -> None:
        """Send Telegram alert for invariant violation (sync wrapper)."""
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.telegram.send_alert(message))
            else:
                loop.run_until_complete(self.telegram.send_alert(message))
        except Exception as e:
            logger.critical("INVARIANT_ALERT failed: %s", e)

    async def _run_invariant_runtime_check(self) -> None:
        """Scheduled job: run invariant enforcer every 60s during market hours."""
        if self.invariant_enforcer is None:
            return
        try:
            # Update equity from latest state
            self.invariant_enforcer.update_equity(self.equity)
            results = self.invariant_enforcer.enforce_runtime()
            failures = [r for r in results if not r.passed]
            if failures:
                logger.warning(
                    "INVARIANT_RUNTIME: %d failure(s) detected", len(failures),
                )
        except Exception as e:
            logger.error("INVARIANT_RUNTIME check error: %s", e)

    @staticmethod
    def _derive_exit_price_simple(pos) -> float:
        """Derive current market price from a VirtualPosition's unrealised P&L.

        Used by circuit breaker and any code outside _handle_regime_transition
        that needs to compute a realistic exit price instead of using entry_price
        (which would record zero P&L on every emergency close).

        Note: unrealised_pnl is calculated on remaining_shares (after partials),
        so we divide by remaining_shares, not original pos.shares.
        """
        remaining_shares = int(pos.shares * getattr(pos, 'remaining_pct', 1.0))
        if remaining_shares > 0 and pos.unrealised_pnl:
            if pos.direction == "LONG":
                return pos.entry_price + (pos.unrealised_pnl / remaining_shares)
            else:
                return pos.entry_price - (pos.unrealised_pnl / remaining_shares)
        return pos.entry_price  # fallback if no P&L data yet

    def _update_state_from_db(self, conn) -> None:
        """Update running state (PnL, consecutive losses, equity, stopout time) from database.

        Called at the start of each scan cycle so risk rules and firewalls
        operate on real data instead of stale zeros.
        """
        # Fix #3: Update daily/weekly PnL from database
        daily_pnl_dollars = get_daily_pnl(conn)
        weekly_pnl_dollars = get_weekly_pnl(conn)
        if self.equity > 0:
            self._daily_pnl_pct = daily_pnl_dollars / self.equity
            self._weekly_pnl_pct = weekly_pnl_dollars / self.equity

        # B-06 (SK-02): Update consecutive losses from most recent trades.
        # Uses ScopedQuery to scope to current session (06:00 UK today).
        # Old code used datetime('now', '-12 hours') which caused zombie halts:
        # losses from months ago could trigger permanent deadlock.
        try:
            sq = ScopedQuery(conn)
            # Paper mode uses virtual_trades; also check trades table for live mode
            rows = sq.execute(
                """SELECT net_pnl FROM virtual_trades
                   WHERE 1=1
                   ORDER BY exit_time DESC LIMIT 10""",
                time_column="exit_time",
            ).fetchall()
            if not rows:
                # Fallback to trades table (live mode)
                rows = sq.execute(
                    """SELECT pnl_dollars as net_pnl FROM trades
                       WHERE 1=1
                       ORDER BY time_exited DESC LIMIT 10""",
                    time_column="time_exited",
                ).fetchall()
            count = 0
            for row in rows:
                if (row["net_pnl"] or 0) < 0:
                    count += 1
                else:
                    break
            self._consecutive_losses = count
        except Exception as e:
            logger.warning("Failed to query consecutive losses: %s", e)

        # B-06 (SK-02): Update last stopout time — scoped to current session.
        # Old code had no date bound, scanning entire history — ancient stopouts
        # from months ago could trigger false cooldowns.
        try:
            sq = ScopedQuery(conn)
            stopout_row = sq.execute(
                """SELECT exit_time FROM virtual_trades
                   WHERE exit_reason = 'STOP_HIT'
                   ORDER BY exit_time DESC LIMIT 1""",
                time_column="exit_time",
            ).fetchone()
            if stopout_row and stopout_row["exit_time"]:
                self._last_stopout_time = datetime.fromisoformat(stopout_row["exit_time"])
        except Exception as e:
            logger.warning("Failed to query last stopout time: %s", e)

        # Fix #9: Update equity from virtual trader
        self.equity = self.virtual_trader.equity
        self.qualifier.equity = self.equity
        self.portfolio_risk.equity = self.equity
        self.circuit_breakers._equity = self.equity
        self.dynamic_sizer._equity = self.equity

        logger.debug(
            "State updated: equity=%.2f daily_pnl=%.4f weekly_pnl=%.4f "
            "consec_losses=%d last_stopout=%s",
            self.equity, self._daily_pnl_pct, self._weekly_pnl_pct,
            self._consecutive_losses, self._last_stopout_time,
        )

    def _init_feeds(self) -> None:
        """Lazy-initialize data feed modules. Retries failed feeds on each call."""
        if self._data_feeds is None:
            try:
                from data_hub.hub import DataHub
                self._data_feeds = DataHub()
                logger.info("DataHub initialized (IBKR-first, yfinance fallback)")
            except Exception as hub_err:
                logger.warning("DataHub init failed (%s), falling back to DataFeedManager", hub_err)
                try:
                    from feeds.data_feeds import DataFeedManager
                    self._data_feeds = DataFeedManager()
                except ImportError as e:
                    logger.error("Failed to import any data feed module: %s", e)

        if self._market_structure is None:
            try:
                from feeds.market_structure import MarketStructure
                self._market_structure = MarketStructure(primary_mode=cfg.get_primary_mode())
            except ImportError as e:
                logger.error("Failed to import MarketStructure: %s", e)

        if self._indicator_engine is None:
            try:
                from feeds.indicators import IndicatorEngine
                self._indicator_engine = IndicatorEngine()
            except ImportError as e:
                logger.error("Failed to import IndicatorEngine: %s", e)

        if self._pattern_detector is None:
            try:
                from feeds.pattern_detector import PatternDetector
                self._pattern_detector = PatternDetector()
            except ImportError as e:
                logger.error("Failed to import PatternDetector: %s", e)

        if self._calendar_feed is None:
            try:
                from feeds.calendar_feed import CalendarFeed
                self._calendar_feed = CalendarFeed()
            except ImportError as e:
                logger.error("Failed to import CalendarFeed: %s", e)

        if self._news_feed is None:
            try:
                from feeds.news_feed import NewsFeed
                self._news_feed = NewsFeed()
            except ImportError as e:
                logger.error("Failed to import NewsFeed: %s", e)

        if self._screener is None:
            try:
                from feeds.screener import FinvizScreener
                self._screener = FinvizScreener()
            except ImportError as e:
                logger.error("Failed to import FinvizScreener: %s", e)

        if self._holdings is None:
            try:
                from feeds.holdings_decomposition import HoldingsDecomposer
                self._holdings = HoldingsDecomposer()
            except ImportError as e:
                logger.error("Failed to import HoldingsDecomposer: %s", e)

        if self._premarket_engine is None and self._data_feeds and self._holdings:
            try:
                from feeds.premarket_intelligence import PreMarketIntelligenceEngine
                self._premarket_engine = PreMarketIntelligenceEngine(
                    data_feeds=self._data_feeds,
                    news_feed=self._news_feed,
                    screener=self._screener,
                    holdings=self._holdings,
                )
            except ImportError as e:
                logger.error("Failed to import PreMarketIntelligenceEngine: %s", e)

    def _init_strategies(self) -> None:
        """Lazy-initialize all 16 strategy modules."""
        if self._strategies:
            return

        strategy_imports = [
            ("strategies.regime_trend", "RegimeTrendStrategy"),
            ("strategies.momentum_breakout", "MomentumBreakoutStrategy"),
            ("strategies.mean_reversion", "MeanReversionStrategy"),
            ("strategies.catalyst_narrative", "CatalystNarrativeStrategy"),
            ("strategies.pead_earnings", "PEADEarningsDrift"),
            ("strategies.macro_regime", "MacroRegimeShift"),
            ("strategies.sector_rotation", "SectorRotation"),
            ("strategies.vol_crush", "VolatilityCrush"),
            ("strategies.pairs_trade", "PairsTrade"),
            ("strategies.ai_thematic", "AIThematicStrategy"),
            ("strategies.hot_scanner", "HotScannerStrategy"),
            ("strategies.rebalance_flow", "RebalanceFlowStrategy"),
            ("strategies.trend_compound", "TrendCompoundStrategy"),
            ("strategies.gamma_squeeze", "GammaSqueezeStrategy"),
            ("strategies.daily_target", "DailyTargetStrategy"),
            ("strategies.universal_scanner", "UniversalScannerStrategy"),  # S16
        ]

        for module_path, class_name in strategy_imports:
            try:
                module = __import__(module_path, fromlist=[class_name])
                strategy_class = getattr(module, class_name)
                # S15: inject dynamic SpreadHistoryTracker + sync Redis for GPD cache
                if class_name == "DailyTargetStrategy":
                    _s15_kwargs = {}
                    if hasattr(self, "spread_tracker") and self.spread_tracker:
                        _s15_kwargs["spread_tracker"] = self.spread_tracker
                    # T-04: Synchronous Redis client for GPD cache lookups
                    # Must be sync (not async) because scan() is synchronous
                    try:
                        import redis as sync_redis_lib
                        _s15_redis = sync_redis_lib.Redis(
                            host='redis', port=6379,
                            password='nzt48redis', decode_responses=True,
                            socket_connect_timeout=3,
                        )
                        _s15_redis.ping()
                        _s15_kwargs["redis_client"] = _s15_redis
                        logger.info("S15: sync Redis client connected for GPD cache")
                    except Exception as _redis_err:
                        logger.warning("S15: sync Redis unavailable — GPD cache disabled: %s", _redis_err)
                    self._strategies.append(strategy_class(**_s15_kwargs))
                else:
                    self._strategies.append(strategy_class())
                logger.info("Strategy loaded: %s", class_name)
            except (ImportError, AttributeError) as e:
                logger.warning("Failed to load strategy %s: %s", class_name, e)

        logger.info("Loaded %d / 16 strategies", len(self._strategies))

    def _check_price_anomalies(self, tickers: list[str]) -> list[str]:
        """T-03: Detect price anomalies for priority scanning.

        Checks if any CORE ticker moved > 1% from session open or > 0.5%
        in the last 5 minutes. Anomaly tickers get prepended to the scan
        list so they're evaluated first within the same pipeline pass.

        Uses Redis for 5-min price snapshots (TTL 300s) and 30s debounce.
        """
        anomaly_tickers: list[str] = []
        try:
            if not hasattr(self, '_anomaly_redis') or self._anomaly_redis is None:
                import redis as _sync_redis_anomaly
                self._anomaly_redis = _sync_redis_anomaly.Redis(
                    host='redis', port=6379,
                    password='nzt48redis', decode_responses=True,
                    socket_connect_timeout=2,
                )
                self._anomaly_redis.ping()

            import time
            now_ts = time.time()

            for ticker in tickers:
                try:
                    # Debounce: skip if scanned within last 30s
                    last_scan_key = f"nzt:last_scan:{ticker}"
                    last_scan_ts = self._anomaly_redis.get(last_scan_key)
                    if last_scan_ts and (now_ts - float(last_scan_ts)) < 30:
                        continue

                    # Get current price from latest indicator snapshot
                    if not hasattr(self, '_latest_indicators'):
                        continue
                    snap = self._latest_indicators.get(ticker)
                    if snap is None or snap.price <= 0:
                        continue

                    current_price = snap.price
                    is_anomaly = False

                    # Check 1: > 1% from session open
                    open_key = f"nzt:session_open:{ticker}"
                    open_price_str = self._anomaly_redis.get(open_key)
                    if open_price_str:
                        open_price = float(open_price_str)
                        if open_price > 0:
                            move_from_open = abs(current_price - open_price) / open_price
                            if move_from_open > 0.01:
                                is_anomaly = True
                    else:
                        # Cache session open (first time today)
                        self._anomaly_redis.setex(open_key, 43200, str(current_price))  # 12h TTL

                    # Check 2: > 0.5% in last 5 min
                    price_5m_key = f"nzt:price_5m:{ticker}"
                    price_5m_str = self._anomaly_redis.get(price_5m_key)
                    if price_5m_str:
                        price_5m = float(price_5m_str)
                        if price_5m > 0:
                            move_5m = abs(current_price - price_5m) / price_5m
                            if move_5m > 0.005:
                                is_anomaly = True

                    # Update 5-min price snapshot
                    self._anomaly_redis.setex(price_5m_key, 300, str(current_price))

                    if is_anomaly:
                        anomaly_tickers.append(ticker)
                        # Set debounce
                        self._anomaly_redis.setex(last_scan_key, 30, str(now_ts))
                        logger.info("T-03 ANOMALY: %s price=%.4f — priority scan triggered", ticker, current_price)

                except Exception:
                    pass  # Non-fatal per ticker

        except Exception as e:
            logger.warning("T-03 anomaly check failed (non-fatal): %s", e)
            self._anomaly_redis = None  # Reset on failure

        return anomaly_tickers

    def _record_safety_failure(self, subsystem: str, exc: Exception) -> None:
        """0-08: Track consecutive safety subsystem failures.

        After 3 consecutive failures in any safety subsystem, log CRITICAL
        and set _safety_halted to halt trading. This prevents silent
        degradation of safety checks from going unnoticed.
        """
        self._safety_failure_counts[subsystem] = (
            self._safety_failure_counts.get(subsystem, 0) + 1
        )
        count = self._safety_failure_counts[subsystem]
        if count >= 3:
            logger.critical(
                "SAFETY_DEGRADATION: %s has failed %d consecutive times — "
                "HALTING TRADING. Last error: %s",
                subsystem, count, exc,
            )
            self._safety_halted = True
        else:
            logger.error(
                "SAFETY_CHECK_FAILED: %s (attempt %d/3): %s",
                subsystem, count, exc,
            )

    def _reset_safety_failure(self, subsystem: str) -> None:
        """0-08: Reset consecutive failure counter on successful check."""
        if subsystem in self._safety_failure_counts:
            self._safety_failure_counts[subsystem] = 0

    async def run_scan(
        self,
        strategy_ids: list[str] | None = None,
        tickers: list[str] | None = None,
    ) -> list[Signal]:
        """Run a complete scan cycle through the cognitive loop.

        INGEST → PERCEIVE → CLASSIFY → DECIDE → QUALIFY → SIZE → DELIVER

        Args:
            strategy_ids: Optional list of strategy IDs to run (e.g., ["S1", "S3"]).
                          If None, runs all active strategies.
            tickers: Optional list of tickers to scan (e.g., ["AAPL"]).
                     If None, scans all tickers from config.

        Returns:
            List of qualified Signal objects ready for delivery.
        """
        # F-18: Halt scan outside market hours to prevent stale indicator pollution
        # AR-05: indicators must pause ingestion, not just skip scanning
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            _now_uk = datetime.now(ZoneInfo("Europe/London"))
        except Exception:
            import pytz
            _now_uk = datetime.now(pytz.timezone("Europe/London"))
        _h = _now_uk.hour
        # Allow scanning only 06:00-22:00 UK on trading days
        if _h < 6 or _h >= 22:
            # Allow exit monitoring to continue but skip signal generation
            if hasattr(self, '_exit_monitor_only'):
                await self._exit_monitor_only()
            return []
        # Also skip weekends
        if _now_uk.weekday() >= 5:  # Saturday=5, Sunday=6
            return []

        # Kill switch check
        if self.kill_switch.is_killed():
            logger.critical("KILL SWITCH ACTIVE — scan aborted")
            return []

        # 0-08: Safety degradation halt — triggered after 3 consecutive failures
        if self._safety_halted:
            logger.critical("SAFETY_HALTED — scan aborted due to repeated safety subsystem failures: %s",
                            {k: v for k, v in self._safety_failure_counts.items() if v >= 3})
            return []

        # V8.0 StateManager: Redis kill switch check (survives container restarts)
        if self.state_manager:
            try:
                if await self.state_manager.is_killed():
                    kill_info = await self.state_manager.get_kill_info()
                    logger.critical("STATE_MANAGER KILL SWITCH ACTIVE — scan aborted: %s", kill_info)
                    return []
                self._reset_safety_failure("state_manager_kill")
            except Exception as _sm_kill_err:
                self._record_safety_failure("state_manager_kill", _sm_kill_err)

        # V8.0 Ulysses Lock: verify config hasn't mutated mid-session
        if self.state_manager and self._frozen_config_hash:
            try:
                match = await self.state_manager.verify_config_hash(self._frozen_config_hash)
                if not match:
                    logger.critical("ULYSSES_LOCK_VIOLATION — config mutated mid-session, halting")
                    self.kill_switch.set_process_killed()
                    await self.state_manager.set_kill("ULYSSES_LOCK_VIOLATION")
                    return []
                self._reset_safety_failure("ulysses_lock")
            except Exception as _ul_err:
                self._record_safety_failure("ulysses_lock", _ul_err)

        # V8.0 StateManager: Redis health check (fail-closed on 3 consecutive failures)
        if self.state_manager:
            try:
                healthy = await self.state_manager.health_check()
                if not healthy:
                    logger.critical("STATE_MANAGER: Redis health check failed — FAIL_CLOSED")
                    self.kill_switch.set_process_killed()
                    return []
                self._reset_safety_failure("redis_health")
            except Exception as _hc_err:
                self._record_safety_failure("redis_health", _hc_err)

        # RC-02: Portfolio Heat Check — halt trading if daily loss ≥ -10%
        if self.portfolio_heat:
            try:
                if self.portfolio_heat.is_halted():
                    status = self.portfolio_heat.get_status()
                    logger.critical(
                        "RC-02 PORTFOLIO HEAT: HALTED — daily PnL=%.2f%%. No new positions.",
                        status.get("daily_pnl_pct", 0) * 100,
                    )
                    return []
                self._reset_safety_failure("portfolio_heat")
            except Exception as _heat_err:
                self._record_safety_failure("portfolio_heat", _heat_err)

        # Brunnermeier & Pedersen (2009): Liquidity Black Hole kill switch
        if self.liquidity_monitor:
            try:
                if self.liquidity_monitor.should_halt_leveraged_etps():
                    alert = self.liquidity_monitor.get_telegram_alert()
                    logger.critical("LIQUIDITY CRISIS: 3/3 triggers — halting all leveraged ETP entries. %s", alert)
                    await self.telegram.send_message(alert)
                    return []
                self._reset_safety_failure("liquidity_monitor")
            except Exception as _liq_err:
                self._record_safety_failure("liquidity_monitor", _liq_err)

        # Update VIX in liquidity monitor each scan cycle
        if self.liquidity_monitor and self._current_market_ctx:
            try:
                vix = getattr(self._current_market_ctx, "vix", None)
                if vix:
                    self.liquidity_monitor.update_vix(vix)
            except Exception:
                pass

        self._init_feeds()
        self._init_strategies()

        if tickers is not None:
            pass  # Use provided tickers
        elif cfg.get_primary_mode() == "UK_ISA":
            tickers = cfg.get_isa_tickers()
            logger.info("ISA MODE: Scanning %d ISA tickers", len(tickers))
        else:
            tickers = cfg.get_tickers()
        qualified_signals: list[Signal] = []

        # T-03: Anomaly-triggered priority scanning
        # Prepend anomaly tickers to scan first (within same pipeline pass)
        try:
            anomaly_tickers = self._check_price_anomalies(tickers)
            if anomaly_tickers:
                anomaly_set = set(anomaly_tickers)
                tickers = anomaly_tickers + [t for t in tickers if t not in anomaly_set]
                logger.info("T-03: %d anomaly ticker(s) prepended: %s", len(anomaly_tickers), anomaly_tickers)
        except Exception as _anom_err:
            logger.warning("T-03 anomaly prepend failed (non-fatal): %s", _anom_err)

        # === INGEST: Pull raw data ===
        logger.info("=== SCAN CYCLE START ===")
        indicators: dict[str, IndicatorSnapshot] = {}
        sector_flows: dict[str, SectorFlow] = {}
        narratives: dict[str, NarrativeContext] = {}

        # MoveAttribution: chain boosts are accumulated per-ticker inside the
        # indicator loop below (check_move called with correct signature per ticker).
        # _pending_chain_boosts carries forward boosts from previous cycle, decayed
        # by Bernard & Thomas (1990) day+1 residual factor (0.30 per cycle).
        if not hasattr(self, "_pending_chain_boosts"):
            self._pending_chain_boosts: dict[str, int] = {}
        # Decay existing boosts by 30% per cycle (PEAD day+1 residual)
        self._pending_chain_boosts = {
            t: int(v * 0.70) for t, v in self._pending_chain_boosts.items()
            if int(v * 0.70) >= 1
        }

        # === DATA VALIDATOR: Check system health before processing ===
        data_health = self.data_validator.get_system_health()
        if data_health.get("status") == "SYSTEM_DOWN":
            logger.critical("DATA QUALITY: SYSTEM_DOWN — all feeds degraded, halting scan")
            return []

        # === F-14: Stale Data Tick-Change Counter — detect frozen feeds ===
        if self.stale_data_monitor.should_halt():
            logger.critical("F-14 STALE DATA HALT: >50%% of universe has unchanged prices for >5min — halting scan")
            return []

        # AEGIS 0-03: Pre-fetch underlying instrument data
        _underlying_data_cache = {}
        try:
            from uk_isa.isa_universe import get_underlying_yahoo_ticker
            if self._data_feeds:
                _underlyings_needed = set()
                for _t in tickers:
                    _u = get_underlying_yahoo_ticker(_t)
                    if _u:
                        _underlyings_needed.add(_u)
                for _u_ticker in _underlyings_needed:
                    try:
                        _u_df = self._data_feeds.get_intraday_bars(_u_ticker)
                        if _u_df is not None and not _u_df.empty:
                            _underlying_data_cache[_u_ticker] = _u_df
                    except Exception:
                        pass
                if _underlying_data_cache:
                    logger.info(
                        "AEGIS 0-03: fetched %d/%d underlying instruments",
                        len(_underlying_data_cache), len(_underlyings_needed),
                    )
        except Exception:
            pass

        for ticker in tickers:
            try:
                # Get price bars
                if self._data_feeds:
                    df = self._data_feeds.get_intraday_bars(ticker)
                    df_daily = self._data_feeds.get_daily_bars(ticker)

                    # === DATA VALIDATION: Validate latest bar before indicator computation ===
                    if not df.empty:
                        # Use the bar's actual timestamp, not datetime.now().
                        # Using now() masks stale data — a Friday bar fetched Saturday
                        # would appear fresh and pollute indicators with yesterday's prices.
                        _raw_ts = df.index[-1]
                        try:
                            if hasattr(_raw_ts, "to_pydatetime"):
                                _bar_ts = _raw_ts.to_pydatetime()
                                if _bar_ts.tzinfo is None:
                                    _bar_ts = _bar_ts.replace(tzinfo=timezone.utc)
                            else:
                                _bar_ts = datetime.now(timezone.utc)
                        except Exception:
                            _bar_ts = datetime.now(timezone.utc)

                        latest_bar = {
                            "open": float(df.iloc[-1].get("Open", 0)),
                            "high": float(df.iloc[-1].get("High", 0)),
                            "low": float(df.iloc[-1].get("Low", 0)),
                            "close": float(df.iloc[-1].get("Close", 0)),
                            "volume": float(df.iloc[-1].get("Volume", 0)),
                            "timestamp": _bar_ts,
                        }
                        # ISA .L tickers have different validation rules
                        is_lse_ticker = ticker.endswith('.L')
                        if is_lse_ticker:
                            # LSE leveraged ETPs have lower volume and different price ranges
                            # Override validation: don't reject on low volume during LSE hours
                            from datetime import time as dtime
                            from core.clock import now_uk
                            uk_now = now_uk()
                            uk_time = uk_now.time()
                            lse_open = dtime(8, 0) <= uk_time <= dtime(16, 30)

                            if latest_bar["volume"] == 0 and not lse_open:
                                # Zero volume outside LSE hours is NORMAL for .L tickers
                                bar_ok = True
                                bar_issues = []
                            elif latest_bar["volume"] < 100 and lse_open:
                                # Very low volume during market hours — warn but don't reject
                                logger.info("LOW_VOL_LSE %s: volume=%.0f during LSE hours (allowing)",
                                           ticker, latest_bar["volume"])
                                bar_ok = True
                                bar_issues = []
                            else:
                                bar_ok, bar_issues = self.data_validator.validate_bar(ticker, latest_bar)
                        else:
                            bar_ok, bar_issues = self.data_validator.validate_bar(ticker, latest_bar)
                        if bar_ok:
                            # Validate OHLC integrity before using
                            o, h, l, c = latest_bar["open"], latest_bar["high"], latest_bar["low"], latest_bar["close"]
                            if h >= l and h >= 0 and l >= 0 and c >= 0 and o >= 0 and latest_bar["volume"] >= 0:
                                self.data_validator.register_price(
                                    ticker, latest_bar["close"], latest_bar["timestamp"]
                                )
                                # F-14: Feed stale data monitor for tick-change detection
                                self.stale_data_monitor.update(
                                    ticker, latest_bar["close"], latest_bar["timestamp"]
                                )
                                # Feed correlation matrix ONLY after validation passes
                                self.correlation_matrix.update(
                                    ticker, latest_bar["close"], latest_bar["timestamp"]
                                )
                                # Register price provenance for staleness tracking
                                try:
                                    self.provenance_registry.register(
                                        f"price.{ticker}",
                                        value=latest_bar["close"],
                                        provider="data_feeds",
                                    )
                                except Exception:
                                    pass  # provenance is advisory, never blocks
                            else:
                                logger.warning("OHLC INTEGRITY FAIL for %s: O=%.2f H=%.2f L=%.2f C=%.2f V=%.0f — skipping",
                                               ticker, o, h, l, c, latest_bar["volume"])
                        else:
                            logger.warning("BAD BAR for %s: %s — skipping indicators", ticker, bar_issues)

                    # === STALENESS GATE: Reject bars from previous trading days ===
                    # If the latest intraday bar is not from today (UTC date), the feed
                    # is returning cached/historical data. Computing indicators on these
                    # bars produces misleading signals — yesterday's momentum is not
                    # today's momentum. Skip indicator computation entirely and log clearly.
                    _bar_is_fresh = True
                    if not df.empty:
                        try:
                            _raw_ts = df.index[-1]
                            if hasattr(_raw_ts, "to_pydatetime"):
                                _latest_bar_dt = _raw_ts.to_pydatetime()
                                if _latest_bar_dt.tzinfo is None:
                                    _latest_bar_dt = _latest_bar_dt.replace(tzinfo=timezone.utc)
                            else:
                                _latest_bar_dt = datetime.now(timezone.utc)
                            _today_utc = datetime.now(timezone.utc).date()
                            _bar_date = _latest_bar_dt.date()
                            # Allow 1-day lag for tickers where yfinance delivers bars
                            # with a 15-min delay (e.g. LSE .L tickers). But bars from
                            # 2+ days ago are definitely stale.
                            _days_old = (_today_utc - _bar_date).days
                            if _days_old >= 2:
                                logger.warning(
                                    "STALE_BARS %s: latest bar is %d days old (%s) — skipping indicators",
                                    ticker, _days_old, _bar_date,
                                )
                                _bar_is_fresh = False
                            elif _days_old == 1:
                                # 1 day old — acceptable on Monday morning before LSE open
                                # or on weekends. Log at DEBUG so it doesn't spam.
                                from zoneinfo import ZoneInfo as _ZI
                                _uk_now = datetime.now(_ZI("Europe/London"))
                                _lse_open_today = _uk_now.time() >= __import__("datetime").time(8, 0)
                                _is_weekday = _uk_now.weekday() < 5
                                if _is_weekday and _lse_open_today:
                                    logger.info(
                                        "STALE_BARS %s: bar from yesterday (%s) during LSE hours — feed lag",
                                        ticker, _bar_date,
                                    )
                                    # Still allow — yfinance sometimes lags 1 day for .L tickers
                        except Exception:
                            pass  # staleness check is advisory — never blocks scan

                    # Compute indicators
                    if self._indicator_engine and not df.empty and _bar_is_fresh:
                        _df_underlying = None
                        try:
                            from uk_isa.isa_universe import get_underlying_yahoo_ticker as _get_u
                            _u_sym = _get_u(ticker)
                            if _u_sym:
                                _df_underlying = _underlying_data_cache.get(_u_sym)
                        except Exception:
                            pass
                        snapshot = self._indicator_engine.compute_all(
                            df, ticker, df_underlying=_df_underlying,
                        )

                        # Detect patterns
                        if self._pattern_detector:
                            patterns = self._pattern_detector.detect_patterns(
                                df, snapshot, snapshot.or_high_5m, snapshot.or_low_5m
                            )
                            snapshot.patterns_detected = [p["name"] for p in patterns]

                        indicators[ticker] = snapshot

                        # MoveAttribution: call per-ticker with correct signature (B-fix)
                        # Uses snapshot.price (current) and df_daily prev_close for move %
                        if self.move_attribution and snapshot.price > 0:
                            try:
                                _prev_close = (
                                    float(df_daily.iloc[-2]["Close"])
                                    if df_daily is not None and len(df_daily) >= 2
                                    else 0.0
                                )
                                if _prev_close > 0:
                                    _attr = self.move_attribution.check_move(
                                        ticker=ticker,
                                        current_price=snapshot.price,
                                        prev_close=_prev_close,
                                        indicators={
                                            "or_high_5m": snapshot.or_high_5m,
                                            "or_low_5m": snapshot.or_low_5m,
                                            "bb_upper": getattr(snapshot, "bb_upper", None),
                                        },
                                    )
                                    if _attr:
                                        # Get confidence boost from personality profile
                                        _boost = self.move_attribution.get_confidence_boost(
                                            ticker, _attr.get("primary_driver", "")
                                        )
                                        if _boost:
                                            self._pending_chain_boosts[ticker] = (
                                                self._pending_chain_boosts.get(ticker, 0) + _boost
                                            )
                                            logger.info(
                                                "CHAIN_BOOST: %s driver=%s boost=+%d "
                                                "(Thomas & Zhang beta personality match)",
                                                ticker, _attr["primary_driver"], _boost,
                                            )
                            except Exception as _ma_err:
                                logger.warning("MoveAttribution %s: %s", ticker, _ma_err)

                # Get narrative context
                if self._news_feed:
                    narrative = self._news_feed.get_narrative_score(ticker)
                    narratives[ticker] = NarrativeContext(
                        timestamp=datetime.now(timezone.utc),
                        ticker=ticker,
                        narrative_score=narrative if isinstance(narrative, (int, float)) else 0,
                    )
                else:
                    narratives[ticker] = NarrativeContext(
                        timestamp=datetime.now(timezone.utc), ticker=ticker
                    )

                # Sector flow
                sector_flows[ticker] = SectorFlow(
                    timestamp=datetime.now(timezone.utc), ticker=ticker
                )

            except Exception as e:
                logger.error("Error processing %s: %s", ticker, e)
                indicators[ticker] = IndicatorSnapshot(
                    timestamp=datetime.now(timezone.utc), ticker=ticker
                )

        # === CLASSIFY: Determine regime ===
        market_ctx = self._build_market_context(indicators)
        self._current_market_ctx = market_ctx

        # Update bot router with current regime — activates/deactivates specialist bots
        self.bot_router.update_regime(market_ctx.regime)

        # === CIRCUIT BREAKERS: Check all 5 breakers before processing signals ===
        cb_result = self.circuit_breakers.check_all(
            daily_pnl=self._daily_pnl_pct * self.equity,  # Convert pct to dollars
            equity=self.equity,
            vix_current=market_ctx.vix or 0.0,
            vix_prev_close=market_ctx.vix3m or market_ctx.vix or 0.0,  # Approx prev close
            spy_15min_change=0.0,  # Updated from data feeds when available
            open_positions=list(self.virtual_trader.open_positions.values()),
            recent_trades=[],
        )
        # T-12: Push circuit breaker state to VirtualTrader for execution-time re-check
        self.virtual_trader.update_circuit_breaker_state(
            allow_new_entries=cb_result.get("allow_new_entries", False)
        )

        if cb_result.get("force_close_all"):
            logger.critical("CIRCUIT BREAKER RED: %s — emergency flatten all", cb_result["action"])
            with self.virtual_trader._lock:
                for pos_id in list(self.virtual_trader.open_positions.keys()):
                    pos = self.virtual_trader.open_positions[pos_id]
                    if pos.status == "OPEN":
                        self.virtual_trader.close_position(
                            pos_id, self._derive_exit_price_simple(pos), "CIRCUIT_BREAKER_RED", market_ctx.regime.value
                        )
            return []
        if not cb_result.get("allow_new_entries"):
            logger.warning("CIRCUIT BREAKER: %s — no new entries allowed", cb_result["action"])
            return []

        # === SESSION BOUNDARY: Check if current phase allows new entries ===
        # Use LSE phases for UK_ISA mode, US ET phases otherwise
        if cfg.get_primary_mode() == "UK_ISA":
            session_phase = self.session_manager.get_current_lse_phase()
        else:
            session_phase = self.session_manager.get_current_phase()
        if not session_phase.get("allow_new_entries", True):
            logger.info("SESSION PHASE: %s — no new entries (%s)",
                        session_phase.get("phase", "UNKNOWN"),
                        session_phase.get("message", ""))
            return []
        session_size_mult = session_phase.get("effective_size_multiplier", 1.0)

        # Feed correlation spikes to circuit breakers for enhanced risk awareness
        corr_spikes = self.correlation_matrix.detect_correlation_spike()
        if corr_spikes:
            logger.warning("CORRELATION SPIKES DETECTED: %d pairs above threshold", len(corr_spikes))

        # === REGIME TRANSITION ACTIONS ===
        # Section 7: When regime changes, execute immediate protective actions
        # FIX 2026-03-11: decrement buffer AFTER executing actions to prevent
        # firing every scan cycle (was sending Telegram every 60s indefinitely)
        if self.regime_classifier.in_transition:
            self._execute_regime_transition_actions(market_ctx.regime)
            self.regime_classifier.decrement_transition_buffer()

        # === SELF-LEARNING: Load meta weights for strategy prioritisation ===
        # MetaLearner rebalances strategy weights nightly based on edge data.
        # Higher-weighted strategies run first and get priority in execution.
        meta_weights: dict[str, float] = {}
        try:
            import json
            _mw_path = Path("data/meta_weights.json")
            if _mw_path.exists():
                _mw_data = json.loads(_mw_path.read_text())
                meta_weights = _mw_data.get("weights", {})
        except Exception:
            pass

        # === SELF-LEARNING: Check DriftDetector defensive mode ===
        # If feature drift is detected, reduce risk and prefer safe strategies.
        drift_defensive = False
        try:
            _dr_path = Path("data/drift_report.json")
            if _dr_path.exists():
                _dr_data = json.loads(_dr_path.read_text())
                drift_defensive = bool(_dr_data.get("alerted", False))
                if drift_defensive:
                    logger.warning("LEARNING: DriftDetector DEFENSIVE MODE active — reducing risk 50%%")
        except Exception:
            pass

        # === SELF-LEARNING: Load ticker profiles for universe prioritisation ===
        ticker_priority: dict[str, float] = {}
        try:
            ticker_profiles_data = self.learning.get_all_ticker_profiles() if hasattr(self.learning, 'get_all_ticker_profiles') else {}
            for tp in (ticker_profiles_data if isinstance(ticker_profiles_data, list) else ticker_profiles_data.values() if isinstance(ticker_profiles_data, dict) else []):
                if isinstance(tp, dict):
                    ticker_priority[tp.get("ticker", "")] = tp.get("priority_score", 50)
        except Exception:
            pass

        # === SELF-LEARNING: Sort tickers by learning priority score ===
        if ticker_priority:
            tickers = sorted(tickers, key=lambda t: ticker_priority.get(t, 50), reverse=True)

        # === UNIVERSE GOVERNANCE: Auto-delist dead tickers ===
        try:
            governed_tickers = []
            for t in tickers:
                if not self.universe_governance.auto_delist_check(t):
                    governed_tickers.append(t)
                else:
                    logger.warning("GOVERNANCE: Auto-delisted %s (3+ days empty data)", t)
            tickers = governed_tickers
        except Exception as gov_err:
            logger.warning("Universe governance check failed: %s", gov_err)

        # === DECAY HALT: Filter tickers with proven-negative expectancy per-ticker ===
        # DecayDetector.is_halted(ticker=t) returns True when rolling win-rate has
        # decayed below minimum threshold. Skip entire ticker — no signal generated.
        try:
            if hasattr(self, "learning") and hasattr(self.learning, "decay_detector"):
                _active_tickers = []
                for t in tickers:
                    if self.learning.decay_detector.is_halted(ticker=t):
                        logger.warning(
                            "DECAY_HALT: %s skipped this cycle — ticker-level edge gone", t
                        )
                    else:
                        _active_tickers.append(t)
                tickers = _active_tickers
        except Exception as _dt_err:
            logger.warning("Per-ticker decay halt check failed (non-blocking): %s", _dt_err)

        # === EDGE DECAY: Check fatigue and intraday momentum ===
        # Fatigue: penalizes quality_multiplier when too many trades taken today.
        # Intraday momentum: uses first-hour return to bias last-hour trades.
        fatigue_mult = 1.0
        try:
            # Get approximate daily trade count from open positions + recent DB
            _daily_tc = 0
            try:
                with transaction() as _tc_conn:
                    _daily_tc = get_daily_trade_count(_tc_conn, "BULL") + get_daily_trade_count(_tc_conn, "BEAR")
            except Exception:
                pass
            fatigue = self.edge_decay.check_fatigue(trades_today=_daily_tc)
            fatigue_mult = fatigue.get("quality_multiplier", 1.0)
            if fatigue.get("fatigued"):
                logger.warning("EDGE_DECAY: Fatigue detected — quality_mult=%.2f (%s)",
                             fatigue_mult, fatigue.get("message", ""))
            # Apply intraday momentum bias (last hour window)
            momentum_bias = self.edge_decay.get_intraday_momentum_bias()
            if momentum_bias != 0:
                logger.debug("EDGE_DECAY: Intraday momentum bias = %+d", momentum_bias)
        except Exception as fatigue_err:
            logger.warning("Edge decay fatigue check failed: %s", fatigue_err)

        # === PROVENANCE: Check data freshness before strategy decisions ===
        # If provenance tracking is enabled and critical fields are stale,
        # log warnings. Staleness of price data = reduced confidence later.
        _stale_tickers: set[str] = set()
        try:
            if self.provenance_registry.enabled:
                prov_report = self.provenance_registry.check_all()
                if prov_report["stale"] > 0:
                    logger.warning(
                        "PROVENANCE: %d/%d fields stale: %s",
                        prov_report["stale"], prov_report["total"],
                        [d["field_name"] for d in prov_report["stale_fields"]],
                    )
                    # Build set of tickers with stale price data for confidence penalty
                    for sf in prov_report["stale_fields"]:
                        fn = sf.get("field_name", "")
                        if fn.startswith("price."):
                            _stale_tickers.add(fn.replace("price.", ""))
        except Exception as prov_err:
            logger.warning("Provenance check failed (non-blocking): %s", prov_err)

        # === DECIDE: Run strategies (ordered by meta-weight priority) ===
        raw_signals: list[Signal] = []

        # T-03: Cache indicators for anomaly detection in next cycle
        self._latest_indicators = indicators

        # Sort strategies by meta-weight (highest first = best recent performers)
        strategy_order = list(self._strategies)
        if meta_weights:
            strategy_order.sort(
                key=lambda s: meta_weights.get(s.strategy_id, meta_weights.get(s.name, 0.5)),
                reverse=True,
            )

        for strategy in strategy_order:
            # Filter by requested strategy IDs
            if strategy_ids and strategy.strategy_id not in strategy_ids:
                continue

            # Check if strategy is active (not paused/killed)
            if not self.telegram.is_strategy_active(strategy.strategy_id):
                continue

            # SELF-LEARNING: Skip low-weight strategies in defensive mode
            strategy_weight = meta_weights.get(strategy.strategy_id, meta_weights.get(strategy.name, 0.5))
            if drift_defensive and strategy_weight < 0.3:
                logger.info("LEARNING: Skipping %s (weight=%.2f) — defensive mode active",
                           strategy.strategy_id, strategy_weight)
                continue

            # DECAY HALT ENFORCEMENT (Sprint 5 B5 fix): skip strategies with proven negative expectancy
            # DecayDetector tracks rolling win-rate decay — is_halted() returns True when edge is gone.
            try:
                if hasattr(self, "learning") and hasattr(self.learning, "decay_detector"):
                    if self.learning.decay_detector.is_halted(strategy=strategy.strategy_id):
                        logger.warning(
                            "DECAY_HALT: Strategy %s halted — edge below minimum threshold, skipping",
                            strategy.strategy_id,
                        )
                        continue
            except Exception as _decay_err:
                logger.warning("Decay halt check failed (non-blocking): %s", _decay_err)

            try:
                signals = strategy.scan(
                    tickers=tickers,
                    indicators=indicators,
                    market_ctx=market_ctx,
                    sector_flows=sector_flows,
                    narratives=narratives,
                )
                raw_signals.extend(signals)
                logger.info("Strategy %s produced %d signals (weight=%.2f)",
                           strategy.strategy_id, len(signals), strategy_weight)
            except Exception as e:
                logger.error("Strategy %s scan failed: %s",
                           strategy.strategy_id, e)

        logger.info("Raw signals from all strategies: %d", len(raw_signals))

        # =====================================================================
        # S15 PRIORITY PATH — Bypass the 18-gate gauntlet
        # =====================================================================
        # S15 signals go through 5 essential gates only, then execute directly.
        # The full gauntlet was killing every S15 signal (confidence 30-40 vs floor 60).
        # Research: Kelly (1956), Thorp (2006), Ang et al. (2006)
        # =====================================================================
        # CHAIN REACTION BOOST: apply accumulated move_attribution boosts to signals
        # Thomas & Zhang (2008) beta=0.40 — peer ETP confidence adjustment
        # =====================================================================
        if self._pending_chain_boosts:
            for _sig in raw_signals:
                _boost = self._pending_chain_boosts.get(_sig.ticker, 0)
                if _boost:
                    _sig.confidence = min(100, _sig.confidence + _boost)
                    logger.debug(
                        "CHAIN_BOOST applied: %s +%d → confidence=%d",
                        _sig.ticker, _boost, _sig.confidence,
                    )

        # =====================================================================
        # ML CONFIDENCE BLEND (Sprint 5 fix): blend rule-based confidence with
        # LightGBM+XGBoost ensemble prediction. blend_confidence() = 70% rule +
        # 30% ML. Only active once ml_model is trained (is_trained flag).
        # Academic: Dietterich (2000) ensemble diversity; Crammer et al. (2006) PA.
        # =====================================================================
        if self.ml_model is not None:
            _ml_available = getattr(self.ml_model, "is_trained", False)
            # Sprint 0.5: Use signal.id not ticker to avoid multi-strategy collateral damage.
            # Multiple strategies CAN emit signals for same ticker — ticker-based veto kills all.
            _vetoed_signal_ids: set[str] = set()  # R21-04: collect vetoes by unique signal ID
            for _sig in raw_signals:
                try:
                    _snap = indicators.get(_sig.ticker, {})
                    # M-09: _snap may be IndicatorSnapshot (dataclass) or dict — handle both
                    def _snap_val(obj, key, default):
                        if isinstance(obj, dict):
                            return float(obj.get(key, default))
                        return float(getattr(obj, key, default) or default)
                    _features = {
                        "ticker": _sig.ticker,
                        "regime": market_ctx.regime.value if market_ctx else "UNKNOWN",
                        "rvol": _snap_val(_snap, "rvol", 1.0),
                        "adx": _snap_val(_snap, "adx14", 25.0),
                        "rsi": _snap_val(_snap, "rsi14", 50.0),
                        "atr_pct": _snap_val(_snap, "atr_pct", 0.01),
                        "confidence": float(_sig.confidence),
                        "hour_of_day": datetime.now(timezone.utc).hour,
                        "day_of_week": datetime.now(timezone.utc).weekday(),
                        "strategy": _sig.strategy or "S15",
                    }
                    # Mandate 3: De Prado (2018) meta-labelling — binary veto gate
                    # Replaces old 70/30 blend with trade-or-skip decision
                    import asyncio as _asyncio
                    _loop = _asyncio.get_event_loop()
                    _verdict = await _loop.run_in_executor(None, self.ml_model.meta_label, _features)
                    if _verdict.get("veto"):
                        _vetoed_signal_ids.add(_sig.id)  # R21-04: veto by signal ID, not ticker
                        logger.info(
                            "META_LABEL_VETO: %s [%s] P=%.3f thresh=%.2f — signal will be removed",
                            _sig.ticker, _sig.id, _verdict["p_success"], _verdict["threshold"],
                        )
                        continue
                    elif _verdict.get("model_active"):
                        logger.debug(
                            "META_LABEL_PASS: %s [%s] P=%.3f (ML active)",
                            _sig.ticker, _sig.id, _verdict["p_success"],
                        )
                except Exception as _ml_err:
                    logger.warning("ML blend failed for %s (non-blocking): %s", _sig.ticker, _ml_err)
            # R21-04: Remove vetoed signals AFTER iteration (by signal ID, not ticker)
            if _vetoed_signal_ids:
                raw_signals = [s for s in raw_signals if s.id not in _vetoed_signal_ids]

        # =====================================================================
        s15_signals = [s for s in raw_signals if s.strategy == "S15"]
        non_s15_signals = [s for s in raw_signals if s.strategy != "S15"]

        if s15_signals:
            s15_executed = await self._execute_s15_priority_path(
                s15_signals, indicators, market_ctx, sector_flows, narratives
            )
            if s15_executed:
                qualified_signals_s15 = s15_executed
            else:
                qualified_signals_s15 = []
        else:
            qualified_signals_s15 = []

        # =====================================================================
        # S16 MEDIUM GAUNTLET — Lighter than the 18-gate pipeline
        # =====================================================================
        # S16 signals go through 5 essential gates (LSE hours, portfolio risk,
        # correlation, confidence floor 55, dynamic sizing) then execute.
        # The full gauntlet was rejecting viable S16 universal scanner plays.
        # =====================================================================
        s16_signals = [s for s in non_s15_signals if s.strategy == "S16"]
        non_priority_signals = [s for s in non_s15_signals if s.strategy != "S16"]

        if s16_signals:
            s16_executed = await self._check_s16_gauntlet(
                s16_signals, indicators, market_ctx, sector_flows, narratives
            )
            qualified_signals_s16 = s16_executed if s16_executed else []
        else:
            qualified_signals_s16 = []

        raw_signals = non_priority_signals  # Remaining signals go through normal gauntlet

        # Bridge Pipeline A (strategies) with Pipeline B (signal_engine) quality bar
        try:
            from signal_engine.unified_risk_gate import get_unified_risk_gate
            urg = get_unified_risk_gate()
            pre_count = len(raw_signals)
            for _idx, sig in enumerate(raw_signals):
                # 0-04: Shallow-copy signal + nested lists to prevent cross-list mutation
                sig = copy.copy(sig)
                sig.patterns_detected = list(sig.patterns_detected)
                sig.qualification_log = list(sig.qualification_log)
                raw_signals[_idx] = sig

                direction_str = sig.direction.value if hasattr(sig.direction, 'value') else str(sig.direction)
                # Signal model has no factor_group; use strategy as proxy
                factor_group = getattr(sig, 'factor_group', sig.strategy or "UNKNOWN")
                sig_risk = getattr(sig, 'risk_pct', 0.0075)
                allowed, reason = urg.check(
                    ticker=sig.ticker,
                    direction=direction_str,
                    factor_group=factor_group,
                    risk_pct=sig_risk,
                    pathway="STRATEGY_SCAN",
                )
                if not allowed:
                    # Soft penalty instead of hard block — downgrade confidence
                    sig.confidence = max(0, sig.confidence - 15)
                    sig.rejection_reason = f"URG: {reason}"
            logger.info("[BRIDGE] unified risk gate checked %d signals", pre_count)
        except Exception as bridge_err:
            logger.warning("[BRIDGE] unified risk gate check skipped: %s", bridge_err)

        # === SIGNAL LOGGER: Log all scored signals before qualification ===
        try:
            if self.signal_logger and raw_signals:
                regime_tag = market_ctx.regime.value if market_ctx else "UNKNOWN"
                regime_conf = market_ctx.regime_confidence if market_ctx else 0.0
                session_tag = market_ctx.time_window.value if market_ctx else "UNKNOWN"
                self.signal_logger.log_plays(
                    raw_signals, session=session_tag,
                    regime_tag=regime_tag, regime_confidence=regime_conf,
                )
        except Exception:
            pass  # Signal logging is advisory, never blocking

        # === QUALIFY: 7-stage pipeline ===
        with transaction() as conn:
            from delivery.database import get_recent_signals, get_open_positions, insert_signal

            # Fix #3/#4/#5/#9: Update running state from database before qualifying
            self._update_state_from_db(conn)

            recent = get_recent_signals(conn, hours=4)
            # Fix #11: Wrap sqlite3.Row objects so risk rules can use attribute access
            open_positions = [_RowProxy(row) for row in get_open_positions(conn)]

            # Fix #6: Check session protection and drawdown recovery BEFORE the loop
            from core.clock import now_et as _now_et_fn
            now_et = _now_et_fn()
            session_action = self.session_protection.get_session_action(self._daily_pnl_pct)
            weekly_action = self.session_protection.get_weekly_action(
                self._weekly_pnl_pct, now_et.weekday()
            )
            if session_action.get("halt"):
                logger.warning("SESSION HALT: %s (daily PnL=%.4f)",
                             session_action["action"], self._daily_pnl_pct)
                return []
            if weekly_action.get("halt"):
                logger.warning("WEEKLY HALT: %s (weekly PnL=%.4f)",
                             weekly_action["action"], self._weekly_pnl_pct)
                return []

            # Drawdown recovery check
            dd_pct = (self._daily_pnl_pct + self._weekly_pnl_pct) / 2  # Approximate
            dd_level = self.drawdown_recovery.get_level(dd_pct)
            dd_protocol = self.drawdown_recovery.get_protocol(dd_level)
            # Canonical confidence floor — see ThresholdRegistry (E-01)
            session_min_confidence = session_action.get("min_confidence", 65)
            session_size_modifier = session_action.get("size_modifier", 1.0)

            # Run Overseer periodic check (cross-bot risk enforcement)
            overseer_positions = []
            for pos_row in open_positions:
                overseer_positions.append({
                    "ticker": pos_row.ticker,
                    "direction": pos_row.direction if hasattr(pos_row, "direction") else "LONG",
                    "bot_instance": pos_row.bot_instance if hasattr(pos_row, "bot_instance") else "BULL",
                    "shares": pos_row.shares if hasattr(pos_row, "shares") else 0,
                    "current_price": pos_row.current_price if hasattr(pos_row, "current_price") else 0,
                    "stop": pos_row.current_stop if hasattr(pos_row, "current_stop") else 0,
                })
            bot_statuses = self.bot_router.get_all_status()
            bot_daily_pnls = {s["instance"]: s["daily_pnl"] for s in bot_statuses}
            overseer_actions = self.overseer.run_all_checks(
                positions=overseer_positions,
                bot_statuses=bot_statuses,
                equity=self.equity,
                bot_daily_pnls=bot_daily_pnls,
            )
            if overseer_actions:
                self.overseer.apply_actions(overseer_actions, self.bot_router)

            for signal in list(raw_signals):  # 0-04: iterate copy — list may be referenced elsewhere
                try:
                    # 0-04: Shallow-copy signal + nested lists to prevent in-place mutation
                    # while the same object is referenced by multiple lists (raw_signals,
                    # strategy output, signal_logger). ConfidenceBreakdown has only float
                    # fields — immutable, no copy needed.
                    signal = copy.copy(signal)
                    signal.patterns_detected = list(signal.patterns_detected)
                    signal.qualification_log = list(signal.qualification_log)

                    # === SESSION BOUNDARY: Per-signal phase-aware entry filter ===
                    phase_allowed, phase_reason = self.session_manager.should_allow_entry_for_ticker(
                        ticker=signal.ticker,
                        signal_confidence=int(signal.confidence),
                    )
                    if not phase_allowed:
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = f"SESSION_PHASE: {phase_reason}"
                        continue

                    # === ROUTE: Send signal through BotRouter for personality adjustment ===
                    indicator_snap = indicators.get(signal.ticker,
                        IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=signal.ticker))
                    atr = getattr(indicator_snap, "atr14", 0.0) or 0.0
                    bot, routed_signal = self.bot_router.route_signal(signal, atr)
                    if bot is None:
                        # No bot wants this signal — skip
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = "BOT_ROUTER: No active bot for this signal"
                        continue
                    signal = routed_signal

                    # === LEARN: Apply learning adjustments ===
                    learn_adj = self.learning.get_signal_adjustments(
                        ticker=signal.ticker,
                        strategy=signal.strategy,
                        direction=signal.direction.value,
                        regime=market_ctx.regime.value,
                    )
                    if learn_adj.get("should_disable"):
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = "LEARNING: Strategy disabled in this regime"
                        continue
                    signal.confidence += learn_adj.get("confidence_adj", 0)
                    signal.confidence = max(0, min(100, signal.confidence))

                    # === APPLY MAE/MFE RECALIBRATED STOPS/TARGETS ===
                    # The learning engine computes optimal stop/target from historical
                    # trade execution data, but these were NEVER applied. Now they are.
                    if learn_adj.get("stop_mult") and learn_adj["stop_mult"] > 0:
                        try:
                            old_stop = signal.stop
                            atr_val = getattr(indicators.get(signal.ticker), 'atr14', 0) or 0
                            if atr_val > 0:
                                new_stop_dist = atr_val * learn_adj["stop_mult"]
                                if signal.direction == Direction.LONG:
                                    signal.stop = round(signal.entry - new_stop_dist, 2)
                                else:
                                    signal.stop = round(signal.entry + new_stop_dist, 2)
                                logger.debug("LEARNING: Adjusted stop for %s: %.2f -> %.2f (mult=%.2f)",
                                           signal.ticker, old_stop, signal.stop, learn_adj["stop_mult"])
                        except Exception as mae_err:
                            logger.warning("MAE/MFE stop adjustment failed: %s", mae_err)

                    if learn_adj.get("target_1r") and learn_adj["target_1r"] > 0:
                        try:
                            old_target = signal.target_1r
                            signal.target_1r = round(learn_adj["target_1r"], 2)
                            logger.debug("LEARNING: Adjusted target_1r for %s: %.2f -> %.2f",
                                       signal.ticker, old_target, signal.target_1r)
                        except Exception as mfe_err:
                            logger.warning("MAE/MFE target adjustment failed: %s", mfe_err)

                    # === AUTOPSY: Hard block for terrible setup grades ===
                    # If trade autopsy data shows this strategy × ticker combo
                    # has avg setup_grade < 30, block entirely (saves capital).
                    try:
                        from delivery.database import get_connection as _gc_autopsy
                        _ac = _gc_autopsy()
                        try:
                            _autopsy_rows = _ac.execute(
                                """SELECT AVG(setup_grade) as avg_setup, COUNT(*) as cnt
                                   FROM trade_autopsies
                                   WHERE strategy = ? AND ticker = ?
                                   AND created_at > datetime('now', '-30 days')""",
                                (signal.strategy, signal.ticker),
                            ).fetchone()
                            if _autopsy_rows and _autopsy_rows["cnt"] >= 5:
                                avg_setup = _autopsy_rows["avg_setup"] or 50
                                if avg_setup < 30:
                                    signal.status = SignalStatus.SKIPPED
                                    signal.rejection_reason = (
                                        f"AUTOPSY: avg setup_grade={avg_setup:.0f} "
                                        f"for {signal.strategy}+{signal.ticker} "
                                        f"({_autopsy_rows['cnt']} trades)"
                                    )
                                    continue
                        finally:
                            _ac.close()
                    except Exception:
                        pass  # Autopsy check is non-critical

                    # === SECTOR META-BOT: Adjust confidence based on sector flows ===
                    # Maps ISA tickers to their underlying sector, boosts/reduces
                    # confidence based on sector relative strength ranking.
                    try:
                        # Strip .L suffix and leverage prefix for sector lookup
                        base_ticker = signal.ticker.replace(".L", "")
                        # Map ISA leveraged ETPs to underlying
                        _ISA_TO_UNDERLYING = {
                            "NVD3": "NVDA", "TSL3": "TSLA", "TSM3": "TSM",
                            "AMD3": "AMD", "ARM3": "ARM", "MU2": "MU",
                            "AVGO3": "AVGO", "GPT3": "NVDA",  # AI/GPU proxy
                            "QQQ3": "QQQ", "QQQ5": "QQQ", "3LUS": "SPY",
                            "3SEM": "SMH", "SP5L": "SPY", "NVDS": "NVDA",
                            "TSLS": "TSLA", "QQQS": "QQQ", "3USS": "SPY",
                            "COIN3": "COIN", "PLTR3": "PLTR",
                        }
                        underlying = _ISA_TO_UNDERLYING.get(base_ticker)
                        if underlying:
                            sector_adj = self.sector_meta_bot.get_ticker_sector_adjustment(underlying)
                            if sector_adj != 0:
                                signal.confidence += sector_adj
                                signal.confidence = max(0, min(100, signal.confidence))
                                logger.debug("SECTOR: %s (%s) → %+d conf", signal.ticker, underlying, sector_adj)
                    except Exception:
                        pass

                    # === ATTENTION DETECTOR: Update and filter exhausted tickers ===
                    # If attention is in EXHAUSTING phase, penalize momentum-based entries
                    # (the crowd is leaving, contrarian entries may be better)
                    try:
                        ind_snap_attn = indicators.get(signal.ticker)
                        if ind_snap_attn:
                            self.attention_detector.update(
                                signal.ticker,
                                rvol=getattr(ind_snap_attn, 'rvol', 1.0) or 1.0,
                                news_count=0,
                                gap_pct=0.0,
                            )
                            attn_phase = self.attention_detector.get_attention_phase(signal.ticker)
                            if attn_phase == "EXHAUSTING":
                                # Penalize momentum entries during attention exhaustion
                                signal.confidence = max(0, signal.confidence - 8)
                                logger.debug("ATTENTION: %s in EXHAUSTING phase → conf -8", signal.ticker)
                            elif attn_phase == "FADED":
                                # Attention fully faded — contrarian entry window closed
                                signal.confidence = max(0, signal.confidence - 5)
                    except Exception:
                        pass

                    # === PROVENANCE: Penalize signals based on stale price data ===
                    # If price data for this ticker is stale, reduce confidence (stale data = unreliable)
                    if signal.ticker in _stale_tickers:
                        signal.confidence = max(0, signal.confidence - 10)
                        logger.warning("PROVENANCE: %s price data stale → conf -10", signal.ticker)

                    # === VOLUME PROFILE: Structural level confidence adjustment ===
                    # Price at POC (high volume node) = strong support → boost confidence
                    # Price above Value Area = breakout territory → slight boost
                    # Price below Value Area = potential rejection zone → penalize
                    try:
                        ind_snap_vp = indicators.get(signal.ticker)
                        if ind_snap_vp and self._data_feeds:
                            vp_bars = self._data_feeds.get_intraday_bars(signal.ticker)
                            if vp_bars is not None and not vp_bars.empty:
                                vp_profile = self.volume_profile_engine.compute_profile(vp_bars)
                                vp_price = getattr(ind_snap_vp, 'price', 0) or 0
                                if vp_profile.poc > 0 and vp_price > 0:
                                    vp_position = self.volume_profile_engine.get_price_position(
                                        vp_price, vp_profile,
                                    )
                                    if vp_position == "AT_POC":
                                        # Price at Point of Control — strong structural support
                                        signal.confidence = min(100, signal.confidence + 3)
                                        logger.debug("VOLUME_PROFILE: %s AT_POC → conf +3", signal.ticker)
                                    elif vp_position == "ABOVE_VA":
                                        if signal.direction == Direction.LONG:
                                            signal.confidence = min(100, signal.confidence + 2)
                                            logger.debug("VOLUME_PROFILE: %s ABOVE_VA LONG → conf +2", signal.ticker)
                                    elif vp_position == "BELOW_VA":
                                        if signal.direction == Direction.LONG:
                                            signal.confidence = max(0, signal.confidence - 3)
                                            logger.debug("VOLUME_PROFILE: %s BELOW_VA LONG → conf -3", signal.ticker)
                                    # Check for LVN breakaway — fast move through thin zone = conviction
                                    if self.volume_profile_engine.is_breakaway_through_lvn(vp_price, vp_profile):
                                        signal.confidence = min(100, signal.confidence + 4)
                                        logger.debug("VOLUME_PROFILE: %s LVN breakaway → conf +4", signal.ticker)
                    except Exception:
                        pass  # volume profile is advisory, never blocks

                    # === STRATEGY-CONTEXT MATRIX: Auto-disable bad strategy+regime combos ===
                    try:
                        _scm_regime = market_ctx.regime.value if market_ctx else "UNKNOWN"
                        if self.learning.strategy_tracker.is_disabled(signal.strategy, _scm_regime):
                            signal.status = SignalStatus.SKIPPED
                            signal.rejection_reason = (
                                f"STRATEGY_MATRIX: {signal.strategy} disabled in {_scm_regime} "
                                f"(expectancy < {self.learning.strategy_tracker.DISABLE_THRESHOLD}R)"
                            )
                            # Record OOS signal for potential probation re-enable
                            self.learning.strategy_tracker.record_oos_signal(
                                signal.strategy, _scm_regime, signal.confidence / 100.0,
                            )
                            continue
                    except Exception:
                        pass  # Strategy matrix is advisory, never blocking

                    # === TOURNAMENT: Check if strategy is benched ===
                    if self.tournament.is_benched(signal.strategy):
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = (
                            f"TOURNAMENT: {signal.strategy} is benched "
                            f"({self.tournament.points.get(signal.strategy, 0):.0f} pts)"
                        )
                        # Record OOS signal for potential reinstatement
                        reinstate_alert = self.tournament.record_oos_signal(
                            signal.strategy, signal.confidence / 100.0
                        )
                        if reinstate_alert and reinstate_alert.get("type") == "STRATEGY_REINSTATED":
                            try:
                                _t = asyncio.get_event_loop().create_task(
                                    self.telegram.send_alert(
                                        f"STRATEGY REINSTATED: {reinstate_alert['strategy']}\n"
                                        f"OOS Avg R: {reinstate_alert['oos_avg_r']:.3f} "
                                        f"over {reinstate_alert['oos_signals']} signals\n"
                                        f"New points: {reinstate_alert['new_points']:.0f}"
                                    )
                                )
                                self._background_tasks.add(_t)
                                _t.add_done_callback(self._background_tasks.discard)
                            except Exception as e:
                                logger.warning("Strategy reinstatement Telegram alert failed: %s", e)
                        continue

                    # === PRE-MARKET INTEL: Apply bias-based confidence adjustment ===
                    if market_ctx.premarket_brief:
                        brief = market_ctx.premarket_brief
                        # Boost confidence when signal aligns with pre-market bias
                        if brief.market_bias == "BULLISH" and signal.direction == Direction.LONG:
                            signal.confidence += int(brief.bias_confidence * 10)  # Up to +10
                        elif brief.market_bias == "BEARISH" and signal.direction == Direction.SHORT:
                            signal.confidence += int(brief.bias_confidence * 10)
                        # Penalize when signal opposes strong bias
                        elif brief.market_bias == "BULLISH" and signal.direction == Direction.SHORT:
                            signal.confidence -= int(brief.bias_confidence * 5)  # Up to -5
                        elif brief.market_bias == "BEARISH" and signal.direction == Direction.LONG:
                            signal.confidence -= int(brief.bias_confidence * 5)
                        signal.confidence = max(0, min(100, signal.confidence))

                    # === MARKET INTERNALS: Apply composite confidence adjustment ===
                    if market_ctx.internals_confidence_adj != 0:
                        signal.confidence += market_ctx.internals_confidence_adj
                        signal.confidence = max(0, min(100, signal.confidence))

                    # === EDGE DECAY: Time-of-day confidence adjustment ===
                    time_adj = self.edge_decay.get_time_adjustment(
                        strategy=signal.strategy,
                        regime=market_ctx.regime.value,
                    )
                    if time_adj != 0:
                        signal.confidence += time_adj
                        signal.confidence = max(0, min(100, signal.confidence))

                    # === PERFORMANCE ATTRIBUTION: Strategy quality feedback ===
                    # Uses rolling attribution history to penalize strategies with
                    # consistently weak components (e.g., poor exit quality, bad timing).
                    try:
                        attr_history = getattr(self.perf_attribution, '_attribution_history', [])
                        if len(attr_history) >= 10:
                            strat_attrs = [
                                a for a in attr_history[-50:]
                                if a.get("strategy") == signal.strategy
                            ]
                            if len(strat_attrs) >= 5:
                                avg_grade = sum(
                                    a.get("overall_grade", 50) for a in strat_attrs
                                ) / len(strat_attrs)
                                # If average grade below 35, penalize confidence
                                if avg_grade < 35:
                                    penalty = min(15, int((35 - avg_grade) / 2))
                                    signal.confidence = max(0, signal.confidence - penalty)
                                    logger.debug(
                                        "ATTRIBUTION: %s avg_grade=%.0f → conf -%d",
                                        signal.strategy, avg_grade, penalty,
                                    )
                                # If average grade above 70, mild boost
                                elif avg_grade > 70:
                                    boost = min(5, int((avg_grade - 70) / 6))
                                    signal.confidence = min(100, signal.confidence + boost)
                    except Exception as attr_err:
                        logger.warning("Attribution feedback failed: %s", attr_err)

                    # === CONFLUENCE: Multi-timeframe agreement scoring ===
                    try:
                        indicator_snap_for_conf = indicators.get(signal.ticker)
                        if indicator_snap_for_conf and hasattr(indicator_snap_for_conf, 'price') and indicator_snap_for_conf.price:
                            tf_data = {}
                            # Build timeframe data from available multi-TF indicators.
                            # IndicatorSnapshot has: ema9, ema20, ema50 (5-min candles),
                            # ema10w (10-week EMA), vwap.
                            # We use different EMA combinations per timeframe to avoid
                            # all 5 TFs producing identical alignment scores.
                            # For higher TFs without explicit data, we approximate using
                            # available multi-period EMAs and check for optional TF-specific
                            # attributes that the indicator engine may populate.
                            snap = indicator_snap_for_conf
                            price = snap.price or 0
                            tf_data["5min"] = {
                                "price": price,
                                "ema20": snap.ema20 or 0,
                                "ema50": snap.ema50 or 0,
                                "vwap": snap.vwap or 0,
                            }
                            tf_data["15min"] = {
                                "price": price,
                                "ema20": getattr(snap, "ema20_15m", 0) or snap.ema50 or 0,
                                "ema50": getattr(snap, "ema50_15m", 0) or snap.ema50 or 0,
                                "vwap": snap.vwap or 0,
                            }
                            tf_data["1h"] = {
                                "price": price,
                                "ema20": getattr(snap, "ema20_1h", 0) or snap.ema50 or 0,
                                "ema50": getattr(snap, "ema50_1h", 0) or snap.ema10w or snap.ema50 or 0,
                                "vwap": snap.vwap or 0,
                            }
                            tf_data["daily"] = {
                                "price": price,
                                "ema20": getattr(snap, "ema20_d", 0) or snap.ema50 or 0,
                                "ema50": getattr(snap, "ema50_d", 0) or snap.ema10w or snap.ema50 or 0,
                                "vwap": 0,  # VWAP not meaningful on daily
                            }
                            tf_data["weekly"] = {
                                "price": price,
                                "ema20": getattr(snap, "ema20_w", 0) or snap.ema10w or 0,
                                "ema50": getattr(snap, "ema50_w", 0) or snap.ema10w or 0,
                                "vwap": 0,  # VWAP not meaningful on weekly
                            }
                            ind_data = {
                                "rsi14": indicator_snap_for_conf.rsi14 or 50,
                                "macd_histogram": indicator_snap_for_conf.macd_histogram or 0,
                                "bb_upper": indicator_snap_for_conf.bb_upper or 0,
                                "bb_lower": indicator_snap_for_conf.bb_lower or 0,
                                "bb_middle": indicator_snap_for_conf.bb_middle or 0,
                                "price": indicator_snap_for_conf.price or 0,
                            }
                            mkt_data = {
                                "spy_trend": "BULLISH" if (market_ctx.spy_vs_vwap or 0) > 0 else "BEARISH",
                                "sector_etf_trend": "NEUTRAL",
                                "vix_trend": "DECLINING" if (market_ctx.vix or 20) < 20 else "RISING",
                                "volume_on_pullback": "flat",
                                "volume_on_breakout": "increasing" if (indicator_snap_for_conf.rvol or 0) > 1.5 else "flat",
                                "rvol_5min": indicator_snap_for_conf.rvol or 1.0,
                            }
                            conf_result = self.confluence_scorer.score_confluence(
                                signal.direction.value, tf_data, ind_data, mkt_data,
                            )
                            confluence_score = conf_result["score"]

                            # Check minimum confluence for this strategy
                            min_confluence = self.confluence_scorer.get_minimum_confluence(signal.strategy)
                            if confluence_score < min_confluence:
                                signal.status = SignalStatus.SKIPPED
                                signal.rejection_reason = (
                                    f"CONFLUENCE: score {confluence_score} < "
                                    f"min {min_confluence} for {signal.strategy}"
                                )
                                continue

                            # Apply confluence confidence adjustment
                            signal.confidence = self.confluence_scorer.adjust_confidence(
                                signal.confidence, confluence_score,
                            )
                    except Exception as conf_err:
                        self._gate_failure_counts["confluence"] = self._gate_failure_counts.get("confluence", 0) + 1
                        logger.warning("SOFT GATE confluence failed (continuing): %s", conf_err)

                    # === PORTFOLIO RISK GATE: Check concentration, directional, budget limits ===
                    try:
                        pos_dicts = []
                        # Use VirtualPosition objects (have strategy field) for portfolio risk,
                        # not DB rows from positions table (which lack strategy column).
                        vt_positions = list(self.virtual_trader.open_positions.values())
                        for vp in vt_positions:
                            pos_dicts.append({
                                "ticker": vp.ticker,
                                "direction": vp.direction,
                                "strategy": vp.strategy or "unknown",
                                "bot_instance": vp.bot_instance or "BULL",
                                "shares": vp.shares or 0,
                                "entry": vp.entry_price or 0,
                                "current_price": self._derive_exit_price_simple(vp),
                                "stop": vp.current_stop or 0,
                                "risk_dollars": vp.risk_dollars or 0,
                            })
                        self.portfolio_risk.equity = self.equity
                        risk_gate = self.portfolio_risk.should_allow_new_trade(
                            pos_dicts, signal, self.equity, market_ctx.regime,
                        )
                        if not risk_gate["allowed"]:
                            signal.status = SignalStatus.SKIPPED
                            signal.rejection_reason = (
                                f"PORTFOLIO_RISK: {'; '.join(risk_gate['reasons'][:3])}"
                            )
                            continue
                    except Exception as prm_err:
                        self._gate_failure_counts["portfolio_risk"] = self._gate_failure_counts.get("portfolio_risk", 0) + 1
                        logger.error("HARD GATE portfolio_risk crashed — rejecting signal %s: %s", signal.ticker, prm_err)
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = f"HARD_GATE_CRASH: portfolio_risk: {str(prm_err)[:100]}"
                        continue

                    # === ADAPTIVE INTEL: Apply per-ticker intelligence ===
                    ticker_intel = self.adaptive_intel.get_ticker_intel(signal.ticker)
                    if ticker_intel and ticker_intel.trades >= 10:
                        # Disable strategies that consistently lose on this ticker
                        if (signal.strategy == ticker_intel.worst_strategy and
                                ticker_intel.worst_strategy_wr < 30):
                            signal.status = SignalStatus.SKIPPED
                            signal.rejection_reason = (
                                f"ADAPTIVE: {signal.strategy} has "
                                f"{ticker_intel.worst_strategy_wr:.0f}% WR on {signal.ticker}"
                            )
                            continue

                    # === OVERSEER: Check signal against portfolio constraints ===
                    ov_approved, ov_status, portfolio_heat = self.overseer.evaluate_signal(
                        signal, overseer_positions, self.equity,
                    )
                    signal.overseer_status = ov_status
                    signal.portfolio_heat = portfolio_heat
                    if not ov_approved:
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = f"OVERSEER: {ov_status}"
                        continue

                    # Fix #6: Apply session protection min confidence filter
                    effective_min_conf = max(
                        session_min_confidence,
                        dd_protocol.get("min_confidence", 60),
                    )
                    if signal.confidence < effective_min_conf:
                        signal.status = SignalStatus.SKIPPED
                        signal.rejection_reason = (
                            f"SESSION: conf {signal.confidence} < min {effective_min_conf}"
                        )
                        continue

                    # === EXPECTANCY MODEL: Gate signals with negative expected R ===
                    # Uses Edge Ledger data to predict expected net R for this
                    # strategy × regime × track combination. ABSTAIN = hard reject,
                    # WATCH = soft penalty. Only TRADE = full proceed.
                    try:
                        indicator_snap_exp = indicators.get(signal.ticker)
                        atr_pct_exp = getattr(indicator_snap_exp, 'atr_pct', 1.0) or 1.0
                        net_rr = abs(signal.target_1r - signal.entry) / max(
                            abs(signal.entry - signal.stop), 0.001
                        ) if signal.stop and signal.entry else 2.0
                        expectancy_out = self.expectancy_model.predict(
                            signal_id=signal.id,
                            strategy_tag=signal.strategy,
                            regime_tag=market_ctx.regime.value,
                            track="INTRADAY_SWING",
                            time_window=market_ctx.time_window.value,
                            net_rr=net_rr,
                            composite_score=signal.confidence,
                            fill_risk_score=0.0,
                        )
                        if expectancy_out.decision == "ABSTAIN":
                            signal.status = SignalStatus.SKIPPED
                            signal.rejection_reason = (
                                f"EXPECTANCY: ABSTAIN — {expectancy_out.why}"
                            )
                            continue
                        elif expectancy_out.decision == "WATCH":
                            # Soft penalty: reduce confidence by 10 but don't reject
                            signal.confidence = max(0, signal.confidence - 10)
                            logger.debug(
                                "EXPECTANCY: WATCH for %s %s — %s (conf -10)",
                                signal.ticker, signal.strategy, expectancy_out.why,
                            )
                    except Exception as exp_err:
                        logger.warning("Expectancy model gate failed (continuing): %s", exp_err)

                    # === EXECUTION QUALITY: Assess fill risk + slippage ===
                    # Predicts expected slippage and fill risk. SKIP = hard reject,
                    # DOWNSIZE = halve position in qualification, WATCH = soft penalty.
                    try:
                        indicator_snap_eq = indicators.get(signal.ticker)
                        eq_rvol = getattr(indicator_snap_eq, 'rvol', 1.0) or 1.0
                        eq_atr_pct = getattr(indicator_snap_eq, 'atr_pct', 1.0) or 1.0
                        eq_result = self.execution_quality_model.predict(
                            signal_id=signal.id,
                            ticker=signal.ticker,
                            rvol=eq_rvol,
                            atr_pct=eq_atr_pct,
                            time_window=market_ctx.time_window.value,
                            composite=signal.confidence,
                            direction=signal.direction.value,
                        )
                        if eq_result.recommendation == "SKIP":
                            signal.status = SignalStatus.SKIPPED
                            signal.rejection_reason = (
                                f"EXEC_QUALITY: SKIP — fill_risk={eq_result.fill_risk_score:.2f}, "
                                f"slippage={eq_result.expected_slippage_bps:.1f}bps"
                            )
                            continue
                        elif eq_result.recommendation == "DOWNSIZE":
                            # Flag for post-qualification downsizing
                            signal._exec_quality_downsize = True
                            logger.debug(
                                "EXEC_QUALITY: DOWNSIZE for %s (fill_risk=%.2f, slip=%.1fbps)",
                                signal.ticker, eq_result.fill_risk_score,
                                eq_result.expected_slippage_bps,
                            )
                        elif eq_result.recommendation == "WATCH":
                            signal.confidence = max(0, signal.confidence - 5)
                    except Exception as eq_err:
                        logger.warning("Execution quality model failed (continuing): %s", eq_err)

                    # Run qualification pipeline
                    qualified = self.qualifier.qualify(
                        signal=signal,
                        indicators=indicators.get(signal.ticker,
                            IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=signal.ticker)),
                        market_ctx=market_ctx,
                        sector_flow=sector_flows.get(signal.ticker,
                            SectorFlow(timestamp=datetime.now(timezone.utc), ticker=signal.ticker)),
                        narrative=narratives.get(signal.ticker,
                            NarrativeContext(timestamp=datetime.now(timezone.utc), ticker=signal.ticker)),
                        open_positions=open_positions,
                        recent_signals=recent,
                        consecutive_losses=self._consecutive_losses,
                        daily_pnl_pct=self._daily_pnl_pct,
                        weekly_pnl_pct=self._weekly_pnl_pct,
                    )

                    # Fix #6: Apply session size modifier
                    if session_size_modifier < 1.0 and qualified.shares:
                        qualified.shares = max(1, int(qualified.shares * session_size_modifier))
                        qualified.risk_dollars = (qualified.risk_dollars or 0) * session_size_modifier

                    # === DYNAMIC SIZER: 8-factor position sizing ===
                    try:
                        sizing = self.dynamic_sizer.calculate_position_size(
                            signal=qualified,
                            regime=market_ctx.regime,
                            equity=self.equity,
                            open_positions=list(self.virtual_trader.open_positions.values()),
                            recent_trades=[],
                        )
                        # Apply circuit breaker + session phase + fatigue + time-of-day size multipliers
                        cb_mult = cb_result.get("size_multiplier", 1.0)
                        # Time-of-day scalar: reduces size during low-alpha periods (lunch chop)
                        try:
                            tod_scalar = self.edge_decay.get_time_of_day_scalar()
                        except Exception:
                            tod_scalar = 1.0
                        combined_mult = cb_mult * session_size_mult * fatigue_mult * tod_scalar
                        sized_shares = sizing["shares"]
                        if combined_mult < 1.0 and sized_shares > 0:
                            sized_shares = max(1, int(sized_shares * combined_mult))

                        if sized_shares > 0:
                            qualified.shares = sized_shares
                            qualified.risk_dollars = sizing["risk_dollars"] * combined_mult
                            qualified.risk_pct = sizing["risk_pct"]
                    except Exception as ds_err:
                        self._gate_failure_counts["dynamic_sizer"] = self._gate_failure_counts.get("dynamic_sizer", 0) + 1
                        logger.warning("SOFT GATE dynamic_sizer failed (using qualifier sizing): %s", ds_err)

                    # === COST DRAG: Validate net edge after costs (Frazzini, Israel & Moskowitz 2015) ===
                    # Korajczyk & Sadka (2004): momentum profits reduce 50-80% after costs.
                    # Veto trades where cost drag eliminates net edge entirely.
                    if self.cost_drag and qualified.shares and qualified.entry and qualified.entry > 0:
                        try:
                            position_gbp = qualified.shares * qualified.entry
                            stop_pct = (
                                (qualified.risk_dollars / (qualified.shares * qualified.entry))
                                if qualified.risk_dollars and qualified.shares > 0 and qualified.entry > 0
                                else 0.01
                            )
                            net_edge = self.cost_drag.get_net_edge_after_costs(
                                ticker=qualified.ticker,
                                gross_r=1.0,  # Assume 1R gross target
                                position_gbp=position_gbp,
                                stop_pct=max(0.001, stop_pct),
                            )
                            # Capacity check — flag if > 2% ADV (RED)
                            constrained, cap_reason = self.cost_drag.is_capacity_constrained(
                                qualified.ticker, position_gbp
                            )
                            if constrained:
                                logger.info(
                                    "COST_DRAG: Capacity %s — halving size",
                                    cap_reason,
                                )
                                qualified.shares = max(1, qualified.shares // 2)
                                qualified.risk_dollars = (qualified.risk_dollars or 0) * 0.5
                            elif net_edge < 0.0:
                                # Costs exceed expected gross edge — veto the trade
                                logger.info(
                                    "COST_DRAG: Vetoed %s — net_edge=%.3fR after %.1fbps drag",
                                    qualified.ticker, net_edge,
                                    self.cost_drag.get_total_drag_bps(
                                        qualified.ticker, position_gbp
                                    )["total_bps"],
                                )
                                continue
                        except Exception as _cd_err:
                            logger.warning("CostDrag check failed (non-critical): %s", _cd_err)

                    # === EXECUTION QUALITY: Apply DOWNSIZE if flagged ===
                    if getattr(signal, '_exec_quality_downsize', False) and qualified.shares:
                        qualified.shares = max(1, qualified.shares // 2)
                        qualified.risk_dollars = (qualified.risk_dollars or 0) * 0.5
                        logger.info("EXEC_QUALITY: Downsized %s %s to %d shares (high fill risk)",
                                   qualified.ticker, qualified.strategy, qualified.shares)

                    # === SMART ROUTER: Liquidity check + slippage prediction ===
                    try:
                        indicator_snap_liq = indicators.get(qualified.ticker)
                        adv = getattr(indicator_snap_liq, "dollar_volume", 0) or 0
                        if adv > 0 and qualified.entry > 0:
                            adv_shares = adv / qualified.entry  # Approximate ADV in shares
                        else:
                            adv_shares = 1_000_000  # Default for unknown

                        liq = self.smart_router.assess_liquidity(
                            qualified.ticker, adv_shares,
                        )
                        # Cap shares by liquidity
                        is_etp = qualified.bot == Bot.A if hasattr(qualified, 'bot') else False
                        capped_shares = self.smart_router.cap_shares_by_liquidity(
                            qualified.ticker, qualified.shares,
                            adv_shares, liq["score"], is_etp=is_etp,
                        )
                        if capped_shares < qualified.shares:
                            qualified.shares = capped_shares
                            # Recalculate risk dollars after cap
                            per_share_risk = abs(qualified.entry - qualified.stop) if qualified.stop else 0
                            if per_share_risk > 0:
                                qualified.risk_dollars = capped_shares * per_share_risk

                        # Predict slippage for cost tracking
                        rvol = getattr(indicator_snap_liq, "rvol", 1.0) or 1.0
                        predicted_slip = self.smart_router.predict_slippage(
                            qualified.ticker, qualified.shares,
                            qualified.entry, liq["score"], rvol=rvol,
                        )
                        # Store slippage prediction on signal for audit
                        qualified.predicted_slippage = predicted_slip
                    except Exception as sr_err:
                        logger.warning("Smart routing failed: %s", sr_err)

                    # Tournament size multiplier — Darwinian capital allocation
                    # Strategies with more points get larger size; benched = reduced
                    tourn_mult = self.tournament.get_size_multiplier(qualified.strategy)
                    if tourn_mult != 1.0 and qualified.shares:
                        qualified.shares = max(1, int(qualified.shares * tourn_mult))
                        qualified.risk_dollars = (qualified.risk_dollars or 0) * tourn_mult
                        logger.debug("LEARNING: Tournament mult %.2f applied to %s", tourn_mult, qualified.strategy)

                    # SELF-LEARNING: Drift Detector defensive mode — halve position size
                    # When feature drift is detected, the system is operating outside
                    # learned parameters. Reduce risk until drift resolves.
                    if drift_defensive and qualified.shares:
                        qualified.shares = max(1, qualified.shares // 2)
                        qualified.risk_dollars = (qualified.risk_dollars or 0) * 0.5
                        logger.info("LEARNING: Drift defensive mode — halved size for %s %s",
                                   qualified.ticker, qualified.strategy)

                    # Check immutable risk rules
                    if qualified.status != SignalStatus.SKIPPED:
                        # Fix #2: Query actual trade counts from database
                        bot_inst = qualified.bot_instance.value
                        passed, violations = self.risk_rules.check_all(
                            signal=qualified,
                            equity=self.equity,
                            daily_pnl_pct=self._daily_pnl_pct,
                            weekly_pnl_pct=self._weekly_pnl_pct,
                            daily_trade_count=get_daily_trade_count(conn, bot_inst),
                            weekly_trade_count=get_weekly_trade_count(conn, bot_inst),
                            consecutive_losses=self._consecutive_losses,
                            open_positions=open_positions,
                        )
                        if not passed:
                            qualified.status = SignalStatus.SKIPPED
                            qualified.rejection_reason = f"RISK: {'; '.join(violations)}"

                    # Check emotional firewall
                    if qualified.status != SignalStatus.SKIPPED:
                        passed, patterns = self.firewall.check_all(
                            signal=qualified,
                            equity=self.equity,
                            standard_size=qualified.shares,
                            recent_trades=[],
                            open_positions=open_positions,
                            daily_pnl_pct=self._daily_pnl_pct,
                            last_stopout_time=self._last_stopout_time,
                        )
                        if not passed:
                            qualified.status = SignalStatus.SKIPPED
                            qualified.rejection_reason = f"FIREWALL: {'; '.join(patterns)}"

                            # Log each firewall pattern to database + notify Telegram
                            try:
                                from delivery.database import insert_firewall_event
                                for pattern in patterns:
                                    insert_firewall_event(conn, {
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "pattern": pattern,
                                        "signal_ticker": qualified.ticker,
                                        "signal_direction": qualified.direction.value,
                                        "signal_strategy": qualified.strategy,
                                        "signal_confidence": qualified.confidence,
                                        "action_taken": "BLOCKED",
                                        "reason": qualified.rejection_reason,
                                    })
                                await self.telegram.send_firewall_block(
                                    qualified, "; ".join(patterns), qualified.rejection_reason
                                )
                            except Exception as fw_err:
                                logger.warning("Firewall event logging failed: %s", fw_err)

                    # Log signal to database
                    insert_signal(conn, {
                        "id": qualified.id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "ticker": qualified.ticker,
                        "strategy": qualified.strategy,
                        "direction": qualified.direction.value,
                        "confidence": qualified.confidence,
                        "entry": qualified.entry,
                        "stop": qualified.stop,
                        "target_1r": qualified.target_1r,
                        "target_2r": qualified.target_2r,
                        "regime": market_ctx.regime.value,
                        "status": qualified.status.value,
                        "bot": qualified.bot.value,
                        "bot_instance": qualified.bot_instance.value,
                        "risk_dollars": qualified.risk_dollars,
                        "risk_pct": qualified.risk_pct,
                        "shares": qualified.shares,
                        "rvol": qualified.rvol,
                        "gex_regime": market_ctx.gex_regime.value,
                        "time_window": market_ctx.time_window.value,
                        "isa_ticker": qualified.isa_ticker,
                        "overseer_status": qualified.overseer_status,
                        "portfolio_heat": qualified.portfolio_heat,
                        "rejection_reason": qualified.rejection_reason,
                    })

                    if qualified.status != SignalStatus.SKIPPED:
                        # === SANITY GATES: Fail-closed validation ===
                        try:
                            sanity_ok, sanity_fails = run_signal_sanity_gates({
                                "confidence": qualified.confidence,
                                "entry": qualified.entry,
                                "stop": qualified.stop,
                                "direction": qualified.direction.value,
                                "ticker": qualified.ticker,
                            })
                            if not sanity_ok:
                                qualified.status = SignalStatus.SKIPPED
                                qualified.rejection_reason = f"SANITY: {'; '.join(sanity_fails)}"
                        except Exception as sg_err:
                            logger.error("Sanity gates failed (allowing signal through unchecked): %s", sg_err)

                    if qualified.status != SignalStatus.SKIPPED:
                        # === TIER-BASED LOGIC: Apply position sizing & entry patterns ===
                        self._apply_tier_based_logic(qualified, qualified.ticker)
                        qualified_signals.append(qualified)
                    else:
                        # --- MISSED TRADE JOURNAL: Record rejected signals ---
                        try:
                            self.missed_trade_journal.record_miss({
                                "ticker": qualified.ticker,
                                "direction": qualified.direction.value,
                                "strategy": qualified.strategy,
                                "confidence": qualified.confidence,
                                "entry": qualified.entry,
                                "stop": qualified.stop,
                                "target_1r": qualified.target_1r,
                                "rejection_reason": qualified.rejection_reason or "UNKNOWN",
                            })
                        except Exception as mtj_err:
                            logger.warning("Missed trade journal record failed: %s", mtj_err)

                except Exception as e:
                    logger.error(
                        "Qualification EXCEPTION for %s %s: %s",
                        signal.direction.value, signal.ticker, e,
                        exc_info=True,
                    )

        # === MERGE S15 PRIORITY RESULTS ===
        # S15 signals already executed via priority path — add to qualified for delivery
        if qualified_signals_s15:
            qualified_signals.extend(qualified_signals_s15)

        # === MERGE S16 MEDIUM GAUNTLET RESULTS ===
        # S16 signals already executed via medium gauntlet — add to qualified for delivery
        if qualified_signals_s16:
            qualified_signals.extend(qualified_signals_s16)

        # === EXECUTE: Open virtual positions for qualified signals ===
        # Note: S15/S16 signals already have positions opened via priority paths
        for signal in qualified_signals:
            try:
                # S15 already executed via priority path — skip re-execution
                if signal.strategy == "S15" and signal in qualified_signals_s15:
                    continue
                # S16 already executed via medium gauntlet — skip re-execution
                if signal.strategy == "S16" and signal in qualified_signals_s16:
                    continue

                # ── Discipline Gate (absolute veto power) ──
                _disc_vix_main = self._current_market_ctx.vix if self._current_market_ctx else 0.0
                _disc_regime_main = self._current_market_ctx.regime.value if self._current_market_ctx else "NEUTRAL"
                disc_ok, disc_reason = self.discipline.should_trade(
                    setup_quality=signal.confidence if hasattr(signal, 'confidence') else 50,
                    expected_r=0.0,
                    regime=_disc_regime_main,
                    vix=_disc_vix_main,
                )
                if not disc_ok:
                    logger.info("DISCIPLINE VETO: %s | %s", signal.ticker, disc_reason)
                    continue

                indicator_snap = indicators.get(signal.ticker,
                    IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=signal.ticker))

                # Build COMPREHENSIVE indicator snapshot — every field for future learning
                full_snapshot = {
                    # Price structure
                    "price": indicator_snap.price,
                    "vwap": indicator_snap.vwap,
                    "vwap_upper_1s": indicator_snap.vwap_upper_1s,
                    "vwap_lower_1s": indicator_snap.vwap_lower_1s,
                    "vwap_upper_2s": indicator_snap.vwap_upper_2s,
                    "vwap_lower_2s": indicator_snap.vwap_lower_2s,
                    # EMAs
                    "ema9": indicator_snap.ema9,
                    "ema20": indicator_snap.ema20,
                    "ema50": indicator_snap.ema50,
                    "ema10w": indicator_snap.ema10w,
                    "ema_alignment": indicator_snap.ema_alignment,
                    # Momentum
                    "rsi14": indicator_snap.rsi14,
                    "macd_line": indicator_snap.macd_line,
                    "macd_signal": indicator_snap.macd_signal,
                    "macd_histogram": indicator_snap.macd_histogram,
                    "stochastic_rsi": indicator_snap.stochastic_rsi,
                    # Volatility
                    "atr14": indicator_snap.atr14,
                    "atr_pct": indicator_snap.atr_pct,
                    "bb_upper": indicator_snap.bb_upper,
                    "bb_lower": indicator_snap.bb_lower,
                    "bb_middle": indicator_snap.bb_middle,
                    "keltner_upper": indicator_snap.keltner_upper,
                    "keltner_lower": indicator_snap.keltner_lower,
                    "adx14": indicator_snap.adx14,
                    # Volume
                    "rvol": indicator_snap.rvol,
                    "volume_spike": indicator_snap.volume_spike,
                    "dollar_volume": indicator_snap.dollar_volume,
                    "obv": indicator_snap.obv,
                    "mfi14": indicator_snap.mfi14,
                    "cumulative_delta": indicator_snap.cumulative_delta,
                    "speed_of_tape": indicator_snap.speed_of_tape,
                    # Opening range
                    "or_high_5m": indicator_snap.or_high_5m,
                    "or_low_5m": indicator_snap.or_low_5m,
                    "or_high_15m": indicator_snap.or_high_15m,
                    "or_low_15m": indicator_snap.or_low_15m,
                    # Microstructure
                    "bid_ask_spread": indicator_snap.bid_ask_spread,
                    "microstructure_score": indicator_snap.microstructure_score,
                    # Fundamentals
                    "market_cap": indicator_snap.market_cap,
                    # Patterns
                    "patterns_detected": indicator_snap.patterns_detected,
                    # === MARKET CONTEXT at entry ===
                    "regime": market_ctx.regime.value,
                    "regime_confidence": market_ctx.regime_confidence,
                    "regime_duration_bars": market_ctx.regime_duration_bars,
                    "time_window": market_ctx.time_window.value,
                    "gex_regime": market_ctx.gex_regime.value,
                    "gex_value": market_ctx.gex_value,
                    "dix_value": market_ctx.dix_value,
                    "dix_signal": market_ctx.dix_signal,
                    "vix": market_ctx.vix,
                    "vix3m": market_ctx.vix3m,
                    "vix_term_structure": market_ctx.vix_term_structure,
                    "internals_composite": market_ctx.internals_composite,
                    "tick": market_ctx.tick,
                    "trin": market_ctx.trin,
                    "add": market_ctx.add,
                    "vold": market_ctx.vold,
                    "dxy": market_ctx.dxy,
                    "ten_year_yield": market_ctx.ten_year_yield,
                    "put_call_ratio": market_ctx.put_call_ratio,
                    "macro_score": market_ctx.macro_score,
                    "calendar_risk": market_ctx.calendar_risk,
                    "fomc_today": market_ctx.fomc_today,
                    "cpi_nfp_today": market_ctx.cpi_nfp_today,
                    # SPY/QQQ context
                    "qqq_vs_vwap": market_ctx.qqq_vs_vwap,
                    "spy_vs_vwap": market_ctx.spy_vs_vwap,
                    # Portfolio state
                    "portfolio_heat": signal.portfolio_heat,
                    "concurrent_positions": len(open_positions),
                    # Confidence breakdown
                    "confidence_L1": signal.confidence_breakdown.layer1_price_action,
                    "confidence_L2": signal.confidence_breakdown.layer2_regime,
                    "confidence_L3": signal.confidence_breakdown.layer3_sector_flow,
                    "confidence_L4": signal.confidence_breakdown.layer4_macro,
                    "confidence_L5": signal.confidence_breakdown.layer5_narrative,
                    "confidence_penalties": signal.confidence_breakdown.penalties,
                    # Pre-market alignment
                    "premarket_bias": "",
                    "premarket_bias_confidence": 0.0,
                    # Narrative
                    "narrative_score": 0,
                    "catalyst_type": "",
                    # Sector
                    "sector_rs": 0.0,
                }

                # Enrich with pre-market intelligence if available
                if market_ctx.premarket_brief:
                    brief = market_ctx.premarket_brief
                    full_snapshot["premarket_bias"] = brief.market_bias
                    full_snapshot["premarket_bias_confidence"] = brief.bias_confidence

                # Enrich with narrative context
                narr = narratives.get(signal.ticker)
                if narr:
                    full_snapshot["narrative_score"] = narr.narrative_score
                    full_snapshot["catalyst_type"] = narr.catalyst_type

                # Enrich with sector flow
                sf = sector_flows.get(signal.ticker)
                if sf:
                    full_snapshot["sector_rs"] = sf.rs_vs_spy

                # === EXECUTION PLANNER: Cost-aware execution plan + spread gate ===
                # Builds limit price, net R:R after costs, cancel conditions, and DNT checks.
                # VETO spread gate = hard reject. DNT = hard reject. Net R:R stored for audit.
                try:
                    exec_plan = self.execution_planner.build(
                        signal_id=signal.id,
                        ticker=signal.ticker,
                        direction=signal.direction.value,
                        entry=signal.entry,
                        stop=signal.stop,
                        target1=signal.target_1r or signal.entry * 1.02,
                        raw_rr=getattr(signal, 'rr', 2.0),
                        track=getattr(signal, 'track', "INTRADAY_SWING"),
                        rvol=getattr(indicators.get(signal.ticker), 'rvol', 1.0) or 1.0,
                        regime=market_ctx.regime.value,
                    )
                    # Store execution plan data in snapshot for audit trail
                    full_snapshot["exec_plan_net_rr"] = exec_plan.net_rr_after_costs
                    full_snapshot["exec_plan_spread_bps"] = exec_plan.spread_proxy_bps
                    full_snapshot["exec_plan_rt_cost_bps"] = exec_plan.round_trip_cost_bps
                    full_snapshot["exec_plan_spread_gate"] = exec_plan.spread_gate
                    full_snapshot["exec_plan_order_type"] = exec_plan.order_type
                    full_snapshot["exec_plan_pm_summary"] = exec_plan.pm_summary

                    # VETO spread gate = hard reject (spread too wide for profitable execution)
                    if exec_plan.spread_gate == "VETO":
                        logger.warning(
                            "EXEC_PLAN VETO: %s %s — spread=%dbps > VETO threshold",
                            signal.direction.value, signal.ticker, exec_plan.spread_proxy_bps,
                        )
                        continue

                    # Do-not-trade conditions (halt, SHOCK regime, etc.)
                    if exec_plan.do_not_trade:
                        logger.warning(
                            "EXEC_PLAN DNT: %s %s — %s",
                            signal.direction.value, signal.ticker,
                            "; ".join(exec_plan.dnt_reasons),
                        )
                        continue

                    logger.info("EXEC_PLAN: %s", exec_plan.pm_summary)
                except Exception as ep_err:
                    logger.warning("Execution planner failed (continuing): %s", ep_err)

                # === ADAPTIVE ENGINE: Check learned recommendation before execution ===
                try:
                    adaptive_rec = self.adaptive_engine.get_recommendation(
                        ticker=signal.ticker,
                        regime=market_ctx.regime.value,
                        strategy=signal.strategy,
                        direction=signal.direction.value,
                        confidence=signal.confidence,
                    )
                    if adaptive_rec.get("action") == "BLOCK":
                        logger.info("ADAPTIVE BLOCK: %s — %s", signal.ticker, adaptive_rec.get("reason"))
                        continue
                    if adaptive_rec.get("confidence_adjustment"):
                        signal.confidence += adaptive_rec["confidence_adjustment"]
                        logger.debug(
                            "ADAPTIVE ADJ: %s confidence %+d (now %d) — %s",
                            signal.ticker, adaptive_rec["confidence_adjustment"],
                            signal.confidence, adaptive_rec.get("reason", ""),
                        )
                except Exception as ae_err:
                    logger.warning("Adaptive engine recommendation failed (continuing): %s", ae_err)

                try:
                    self._signal_queue.put_nowait({
                        "signal": signal,
                        "indicators": full_snapshot,
                        "source": "GENERAL_GAUNTLET",
                        "kelly_risk_pct": 0.0075,
                        "gauntlet_passed": True,
                    })
                except Full:
                    logger.warning("SIGNAL_QUEUE_FULL: dropped signal %s — queue at capacity", signal.ticker)
                else:
                    logger.info("SIGNAL QUEUED (GENERAL_GAUNTLET): %s %s conf=%d",
                               signal.direction.value, signal.ticker, signal.confidence)
            except Exception as e:
                logger.error("Signal queue publication failed for %s: %s", signal.ticker, e)

        # === AI SIGNAL VALIDATION: Gemini-enhanced narrative for top signals ===
        ai_key = os.environ.get("GEMINI_API_KEY", "")
        if ai_key and qualified_signals:
            await self._ai_enhance_signals(qualified_signals, market_ctx, ai_key)

        # === DELIVER: Push to Telegram + Sheets ===
        # Fix #13: Collect async tasks and await them with error logging
        delivery_tasks: list[asyncio.Task] = []
        for signal in qualified_signals:
            try:
                task = asyncio.create_task(
                    self.telegram.send_signal(signal),
                    name=f"deliver-{signal.ticker}",
                )
                task.add_done_callback(self._delivery_task_done)
                delivery_tasks.append(task)
                self.sheets.log_signal(signal)
                logger.info("DELIVERED: %s %s %s conf=%d",
                           signal.direction.value, signal.ticker,
                           signal.strategy, signal.confidence)
            except Exception as e:
                logger.error("Delivery failed for %s: %s", signal.ticker, e)

        # Await all delivery tasks so exceptions are not silently lost
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)

        # Record scan health heartbeat
        try:
            self.scan_health.record_tick()
            self.scan_health.record_engine_run(
                signals_emitted=len(qualified_signals),
                signals_logged=len(raw_signals),
            )
            # Persist health state so /api/scan_health returns live data (B2 fix)
            self.scan_health.save()
        except Exception:
            pass

        logger.info("=== SCAN COMPLETE: %d qualified, %d rejected ===",
                     len(qualified_signals),
                     len(raw_signals) - len(qualified_signals))

        return qualified_signals

    def _apply_tier_based_logic(self, signal: Signal, ticker: str) -> None:
        """Apply tier-based position sizing and entry pattern to a signal.

        This wires in the TierBasedEntryDetector to:
        1. Classify ticker tier based on volatility
        2. Adjust position size per tier
        3. Apply entry pattern metadata
        4. Enforce Tier 3 session exit discipline

        Args:
            signal: Signal object to enhance
            ticker: Ticker symbol
        """
        if not self.tier_entry_detector:
            return

        try:
            # Get indicator data from signal if available
            rsi = getattr(signal.indicators, 'rsi', 50.0) if signal.indicators else 50.0
            rvol = getattr(signal.indicators, 'rvol', 0.8) if signal.indicators else 0.8

            # Estimate tier from daily range (simplified from historical data)
            daily_range_pct = 5.0  # Default
            if ticker.endswith(".L"):
                daily_range_pct = 4.5
            elif ticker in ["TSLA"]:
                daily_range_pct = 8.5
            elif ticker in ["NVDA"]:
                daily_range_pct = 7.2

            # Classify tier
            tier_class = self.tier_entry_detector.classify_tier(
                ticker=ticker,
                daily_range_pct=daily_range_pct,
            )

            # Adjust signal position size by tier (as % of account)
            # The dynamic_sizer will use this as a baseline
            if tier_class.position_size_pct > 0:
                # Store tier info in signal metadata for downstream processing
                if not hasattr(signal, '_tier_info'):
                    signal._tier_info = {}
                signal._tier_info['tier'] = tier_class.tier
                signal._tier_info['position_size_pct'] = tier_class.position_size_pct
                signal._tier_info['holding_style'] = tier_class.holding_style

            logger.debug(
                "Tier logic applied: %s tier=%d, pos_size=%.2f%%, style=%s",
                ticker, tier_class.tier, tier_class.position_size_pct * 100, tier_class.holding_style
            )
        except Exception as _tier_err:
            logger.debug(f"Tier logic application failed for {ticker}: {_tier_err}")

    async def _send_tier_entry_alert(self, entry_signal) -> None:
        """Send Telegram alert for detected tier-based entry pattern.

        Args:
            entry_signal: EntrySignal object with entry type, confidence, price, etc.
        """
        if not entry_signal:
            return

        try:
            entry_type = entry_signal.entry_type.value.upper()

            if entry_type == "TYPE_B":
                # 🚀 Type B early runner (PRIORITY - your edge)
                msg = (
                    f"🚀 EARLY RUNNER: {entry_signal.ticker}\n"
                    f"RVOL {entry_signal.rvol:.2f}x | RSI {entry_signal.rsi:.0f} | "
                    f"Entry {entry_signal.entry_price:.2f} → Target {entry_signal.target_price:.2f}\n"
                    f"Confidence {entry_signal.confidence:.0f}% | {entry_signal.rationale}"
                )
            elif entry_type == "TYPE_A":
                # 📉 Type A dip recovery
                msg = (
                    f"📉 DIP RECOVERY: {entry_signal.ticker}\n"
                    f"RSI {entry_signal.rsi:.0f} (oversold) | Entry {entry_signal.entry_price:.2f} → "
                    f"Target {entry_signal.target_price:.2f}\n"
                    f"Confidence {entry_signal.confidence:.0f}% | {entry_signal.rationale}"
                )
            elif entry_type == "TYPE_C":
                # 📈 Type C overbought fade
                msg = (
                    f"📈 OVERBOUGHT FADE: {entry_signal.ticker}\n"
                    f"RSI {entry_signal.rsi:.0f} (overbought) | Entry {entry_signal.entry_price:.2f} → "
                    f"Target {entry_signal.target_price:.2f}\n"
                    f"Confidence {entry_signal.confidence:.0f}% | {entry_signal.rationale}"
                )
            else:
                return

            # Send via Telegram
            if self.telegram:
                try:
                    await self.telegram.send_message(msg)
                except Exception as _tg_err:
                    logger.warning(f"Failed to send Telegram alert: {_tg_err}")

        except Exception as _alert_err:
            logger.warning(f"Tier entry alert generation failed: {_alert_err}")

    async def _send_tier3_exit_alert(self, exit_instruction) -> None:
        """Send Telegram alert for Tier 3 mandatory exit.

        Args:
            exit_instruction: ExitInstruction object
        """
        if not exit_instruction:
            return

        try:
            if exit_instruction.urgency == "critical":
                msg = f"⏰ {exit_instruction.message}"
            elif exit_instruction.urgency == "warning":
                msg = f"⏰ {exit_instruction.message}"
            else:
                return

            if self.telegram:
                try:
                    await self.telegram.send_message(msg)
                except Exception as _tg_err:
                    logger.warning(f"Failed to send Tier 3 exit alert: {_tg_err}")

        except Exception as _alert_err:
            logger.warning(f"Tier 3 exit alert generation failed: {_alert_err}")

    async def _scan_universe_async(self, schedule: RefreshSchedule) -> UniverseSnapshot:
        """Execute a universe scan for the refresh scheduler.

        This method is called by the UniverseRefreshIntegration when a scheduled
        refresh occurs. It scans the universe based on the schedule phase and type.

        Args:
            schedule: RefreshSchedule with phase, scan_type, and timing info

        Returns:
            UniverseSnapshot with current universe state and tier classification
        """
        try:
            from datetime import datetime, timezone
            from core.universe_refresh_scheduler import Phase, ScanType, TickerProfile

            now = datetime.now(timezone.utc)
            logger.info(
                "Universe refresh scan started: phase=%s, type=%s",
                schedule.phase.value,
                schedule.scan_type.value,
            )

            # Determine which tickers to scan based on phase
            lse_tickers = []
            euro_tickers = []
            us_tickers = []
            asia_tickers = []

            if schedule.phase in [Phase.PHASE_1, Phase.PHASE_2]:
                # LSE + Euro phase — scan ISA universe
                try:
                    lse_tickers = list(cfg.ISA_FUNDS) if hasattr(cfg, "ISA_FUNDS") else []
                except Exception:
                    lse_tickers = [
                        "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
                        "TSL3.L", "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L"
                    ]

            elif schedule.phase == Phase.PHASE_3:
                # US-only phase — scan US tickers
                us_tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "QQQ"]

            elif schedule.phase in [Phase.PHASE_4, Phase.PHASE_5]:
                # Asia phase — scan Asian markets
                asia_tickers = ["0700.HK", "9618.HK", "1398.HK"]

            # Combine all tickers
            all_tickers = lse_tickers + euro_tickers + us_tickers + asia_tickers

            # Build ticker profiles with sample volatility data
            ticker_profiles = {}
            for ticker in all_tickers[:10]:  # Limit to first 10 for performance
                try:
                    # Get daily range estimate (simplified)
                    daily_range_pct = 5.0  # Default estimate
                    if ticker.endswith(".L"):
                        daily_range_pct = 4.5  # LSE ETPs tend to be less volatile
                    elif ticker.startswith("0") or ticker.startswith("1"):
                        daily_range_pct = 6.0  # Hong Kong stocks
                    elif ticker in ["TSLA"]:
                        daily_range_pct = 8.5
                    elif ticker in ["NVDA"]:
                        daily_range_pct = 7.2

                    profile = TickerProfile(
                        ticker=ticker,
                        daily_range_pct=daily_range_pct,
                        tier="moderate",  # Placeholder — will be set by __post_init__
                        liquidity_score=0.85,
                        isa_eligible=ticker.endswith(".L"),
                        holding_style="scalp",  # Placeholder — will be set by __post_init__
                    )

                    # Wire tier-based entry logic
                    if self.tier_entry_detector:
                        try:
                            tier_class = self.tier_entry_detector.classify_tier(
                                ticker=ticker,
                                daily_range_pct=daily_range_pct,
                            )
                            profile.position_size_pct = tier_class.position_size_pct
                            # entry_pattern will be set by signal generation, not here
                        except Exception as _tier_calc_err:
                            logger.debug(f"Tier classification failed for {ticker}: {_tier_calc_err}")

                    ticker_profiles[ticker] = profile
                except Exception as e:
                    logger.warning(f"Failed to profile {ticker}: {e}")

            # Detect new runners (simplified: just first scan in this session)
            new_runners = []
            if schedule.scan_type == ScanType.INITIAL:
                new_runners = lse_tickers[:2] if lse_tickers else []

            # Create snapshot
            snapshot = UniverseSnapshot(
                timestamp=now,
                phase=schedule.phase,
                scan_type=schedule.scan_type,
                lse_tickers=lse_tickers,
                euro_tickers=euro_tickers,
                us_tickers=us_tickers,
                asia_tickers=asia_tickers,
                total_count=len(all_tickers),
                new_runners=new_runners,
                removed_tickers=[],
                ticker_profiles=ticker_profiles,
            )

            logger.info(
                "Universe refresh complete: %d tickers (%d LSE, %d Euro, %d US, %d Asia), "
                "%d new runners",
                snapshot.total_count,
                len(lse_tickers),
                len(euro_tickers),
                len(us_tickers),
                len(asia_tickers),
                len(new_runners),
            )

            return snapshot

        except Exception as e:
            logger.error(f"Universe scan failed: {e}", exc_info=True)
            from core.universe_refresh_scheduler import UniverseSnapshot
            from datetime import datetime, timezone
            return UniverseSnapshot(
                timestamp=datetime.now(timezone.utc),
                phase=schedule.phase,
                scan_type=schedule.scan_type,
                total_count=0,
            )

    async def _ai_enhance_signals(
        self, signals: list, market_ctx, ai_key: str,
    ) -> None:
        """AI-enhanced signal validation using Gemini Flash.

        For each qualified signal, asks Gemini to assess the setup quality
        and provide a brief AI verdict. This enriches the signal's narrative
        context and adds an AI confidence modifier.

        Only processes signals with confidence >= 65 to conserve API calls.
        Timeout: 8 seconds per signal. Non-blocking — failures are silent.
        """
        try:
            if httpx is None:
                try:
                    import httpx as _httpx
                except ImportError:
                    return
            else:
                _httpx = httpx

            regime = market_ctx.regime.value if market_ctx else "UNKNOWN"
            vix = getattr(market_ctx, 'vix', 0) or 0

            async with _httpx.AsyncClient(timeout=8) as client:
                for signal in signals:
                    if signal.confidence < 65:
                        continue  # Skip low-confidence signals to save API calls
                    try:
                        prompt = (
                            f"You are an expert quantitative trading analyst. "
                            f"Assess this signal in ONE sentence (max 20 words):\n"
                            f"Ticker: {signal.ticker} | Direction: {signal.direction.value} | "
                            f"Strategy: {signal.strategy} | Confidence: {signal.confidence}/100\n"
                            f"Entry: ${signal.entry:.2f} | Stop: ${signal.stop:.2f} | "
                            f"Target: ${signal.target_1r:.2f}\n"
                            f"Regime: {regime} | VIX: {vix:.1f} | RVOL: {signal.rvol:.1f}\n"
                            f"Respond with ONLY: STRONG/MODERATE/WEAK followed by your reason."
                        )
                        resp = await client.post(
                            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                            headers={"x-goog-api-key": ai_key},
                            json={
                                "contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"maxOutputTokens": 40, "temperature": 0.1},
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            candidates = data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                if parts:
                                    ai_verdict = parts[0].get("text", "").strip()
                                    if ai_verdict:
                                        # Store AI verdict in qualification_log (list[str])
                                        if not isinstance(signal.qualification_log, list):
                                            signal.qualification_log = []
                                        signal.qualification_log.append(f"[AI] {ai_verdict}")
                                        # Apply confidence modifier based on AI assessment
                                        upper = ai_verdict.upper()
                                        if upper.startswith("STRONG"):
                                            signal.confidence = min(100, signal.confidence + 3)
                                        elif upper.startswith("WEAK"):
                                            signal.confidence = max(40, signal.confidence - 5)
                                        logger.info(
                                            "AI SIGNAL: %s %s → %s (conf now %d)",
                                            signal.ticker, signal.direction.value,
                                            ai_verdict[:50], signal.confidence,
                                        )
                    except Exception as e:
                        logger.warning("AI signal enhancement failed for %s: %s", signal.ticker, e)
        except Exception as e:
            logger.warning("AI signal enhancement unavailable: %s", e)

    def _on_trade_closed(self, virtual_trade) -> None:
        """Callback from VirtualTrader when a trade closes — feeds ALL learning systems.

        Bridges the VirtualTrade dataclass to the models.Trade expected by LearningEngine
        using a lightweight adapter. Also runs Trade Autopsy for every closed trade.
        Now also: sends Telegram notification, logs to strategy_daily_stats,
        and persists partial executions.
        """
        try:
            from models import Trade, Direction
            # Extract patterns_detected from indicator_snapshot_entry so
            # pattern_tracker.record_pattern_outcome() gets populated (B5 fix)
            _entry_snap = virtual_trade.indicator_snapshot_entry or {}
            if isinstance(_entry_snap, str):
                try:
                    import json as _json
                    _entry_snap = _json.loads(_entry_snap)
                except Exception:
                    _entry_snap = {}
            _patterns_at_entry = _entry_snap.get("patterns_detected", [])

            trade = Trade(
                id=virtual_trade.id,
                ticker=virtual_trade.ticker,
                direction=Direction.LONG if virtual_trade.direction == "LONG" else Direction.SHORT,
                strategy=virtual_trade.strategy,
                time_entered=datetime.fromisoformat(virtual_trade.entry_time) if virtual_trade.entry_time else datetime.now(timezone.utc),
                time_exited=datetime.fromisoformat(virtual_trade.exit_time) if virtual_trade.exit_time else datetime.now(timezone.utc),
                entry_price=virtual_trade.entry_price,
                exit_price=virtual_trade.exit_price,
                shares=virtual_trade.shares,
                pnl_dollars=virtual_trade.net_pnl,
                pnl_r_multiple=virtual_trade.r_multiple,
                risk_dollars=virtual_trade.risk_dollars,
                regime_state=virtual_trade.regime_at_entry,
                confidence_score=virtual_trade.confidence,
                entry_quality=virtual_trade.entry_quality,
                exit_quality=virtual_trade.exit_quality,
                duration_minutes=virtual_trade.duration_minutes,
            )
            # Attach patterns so learning_engine.pattern_tracker gets data (B5 fix)
            trade.patterns = _patterns_at_entry

            # === TRADE CLOSE NOTIFICATION — every trade gets reported ===
            try:
                _t = asyncio.get_event_loop().create_task(
                    self.telegram.send_trade_closed(virtual_trade)
                )
                self._background_tasks.add(_t)
                _t.add_done_callback(self._background_tasks.discard)
            except Exception as e:
                logger.warning("Trade close Telegram notification failed: %s", e)

            # === GOOGLE SHEETS TRADE LOG — auto-log every trade close ===
            # Passes BOTH trade (models.Trade) and virtual_trade (VirtualTrade) for full data
            try:
                self.sheets.log_trade(trade, virtual_trade=virtual_trade)
            except Exception as e:
                logger.warning("Sheets trade log failed (non-critical): %s", e)

            # === B-TEAM TRADE RECORDING — feed league evaluation system ===
            try:
                if self.b_team:
                    self.b_team.record_trade(
                        ticker=virtual_trade.ticker,
                        pnl=virtual_trade.net_pnl,
                        r_multiple=virtual_trade.r_multiple,
                        strategy=virtual_trade.strategy,
                        hold_minutes=virtual_trade.duration_minutes,
                    )
            except Exception as e:
                logger.warning("B-Team trade recording failed: %s", e)

            # === PARTIAL EXECUTION AUDIT — persist ladder partials to audit table ===
            try:
                from delivery.database import insert_partial_execution
                with transaction() as conn:
                    for partial in (virtual_trade.partials or []):
                        insert_partial_execution(conn, {
                            "position_id": virtual_trade.position_id,
                            "timestamp": virtual_trade.exit_time or datetime.now(timezone.utc).isoformat(),
                            "rung": partial.get("rung", 0),
                            "action": f"PARTIAL_RUNG_{partial.get('rung', 0)}",
                            "shares_sold": partial.get("shares", 0),
                            "sell_pct": partial.get("shares", 0) / virtual_trade.shares if virtual_trade.shares > 0 else 0,
                            "price": partial.get("price", 0),
                            "pnl_realized": partial.get("pnl", 0),
                            "r_at_execution": virtual_trade.r_multiple,
                        })
            except Exception as e:
                logger.warning("Partial execution audit failed: %s", e)

            # === EXIT CONTEXT ENRICHMENT — capture market state at close ===
            # This data is NON-NEGOTIABLE: every trade must have exit context for learning
            try:
                import json
                exit_context = {}

                # VIX at exit
                if self._current_market_ctx:
                    ctx = self._current_market_ctx
                    exit_context["vix_at_exit"] = ctx.vix
                    exit_context["regime_at_exit"] = ctx.regime.value
                    exit_context["gex_regime_at_exit"] = ctx.gex_regime.value
                    exit_context["internals_at_exit"] = ctx.internals_composite
                    exit_context["time_window_at_exit"] = ctx.time_window.value
                    exit_context["dix_at_exit"] = ctx.dix_value

                # SPY direction during trade (market_direction_during)
                entry_snap = virtual_trade.indicator_snapshot_entry
                if isinstance(entry_snap, str):
                    try:
                        entry_snap = json.loads(entry_snap)
                    except Exception as e:
                        logger.warning("JSON parse of entry_snap failed: %s", e)
                        entry_snap = {}
                spy_at_entry = entry_snap.get("spy_vs_vwap", 0)
                spy_at_exit = self._current_market_ctx.spy_vs_vwap if self._current_market_ctx else 0
                exit_context["market_direction_during"] = spy_at_exit - spy_at_entry

                # Exit efficiency: (actual_exit - entry) / (MFE - entry)
                if virtual_trade.peak_r > 0 and virtual_trade.r_multiple != 0:
                    exit_context["exit_efficiency"] = virtual_trade.r_multiple / virtual_trade.peak_r
                else:
                    exit_context["exit_efficiency"] = 0.0

                # MAE/MFE (already on trade but ensure they're in exit snapshot)
                exit_context["peak_r"] = virtual_trade.peak_r
                exit_context["trough_r"] = virtual_trade.trough_r

                # Tournament rank at close
                try:
                    rankings = self.tournament.get_rankings()
                    for i, (strat, _pts) in enumerate(rankings):
                        if strat == virtual_trade.strategy:
                            exit_context["tournament_rank_at_close"] = i + 1
                            break
                except Exception as e:
                    logger.warning("Tournament rank query at trade close failed: %s", e)

                # Merge exit context into indicator_snapshot_exit
                existing_exit = virtual_trade.indicator_snapshot_exit
                if isinstance(existing_exit, str):
                    try:
                        existing_exit = json.loads(existing_exit)
                    except Exception as e:
                        logger.warning("JSON parse of existing exit snapshot failed: %s", e)
                        existing_exit = {}
                if not isinstance(existing_exit, dict):
                    existing_exit = {}
                existing_exit.update(exit_context)
                virtual_trade.indicator_snapshot_exit = existing_exit

                # Persist updated exit context to DB
                with transaction() as conn:
                    conn.execute(
                        "UPDATE virtual_trades SET indicator_snapshot_exit = ? WHERE id = ?",
                        (json.dumps(existing_exit), virtual_trade.id),
                    )
            except Exception as e:
                logger.warning("Exit context enrichment failed: %s", e)

            # --- Feed the 10-module learning engine ---
            self.learning.record_trade(trade)

            # --- V3.0: Portfolio Heat Monitor — record P&L for RC-02 daily limit ---
            if self.portfolio_heat:
                try:
                    pnl_pct = (virtual_trade.net_pnl / self.equity * 100) if self.equity > 0 else 0.0
                    heat_status = self.portfolio_heat.record_trade(
                        ticker=virtual_trade.ticker,
                        pnl_dollars=virtual_trade.net_pnl or 0.0,
                        pnl_pct=pnl_pct,
                        strategy=virtual_trade.strategy or "UNKNOWN",
                    )
                    if heat_status.get("alert"):
                        logger.warning("RC-02 HEAT ALERT: %s", heat_status["alert"])
                except Exception as _heat_err:
                    logger.error("Portfolio heat record failed: %s", _heat_err)

            # --- V3.0: Day-of-Week Filter — track actual win rate by day ---
            if self.dow_filter:
                try:
                    is_win = (virtual_trade.net_pnl or 0) > 0
                    self.dow_filter.record_trade(
                        win=is_win,
                        trade_date=virtual_trade.exit_time.date() if virtual_trade.exit_time else None,
                    )
                except Exception as _dow_err:
                    logger.warning("Day-of-week filter record failed: %s", _dow_err)

            # --- Adaptive learning engine update ---
            try:
                self.adaptive_engine.update_from_trade({
                    "ticker": virtual_trade.ticker,
                    "direction": virtual_trade.direction,
                    "strategy_tag": virtual_trade.strategy,
                    "regime_tag": virtual_trade.regime_at_entry or "",
                    "outcome": (
                        "HIT_TARGET" if (virtual_trade.r_multiple or 0) > 0
                        else "HIT_STOP"
                    ),
                    "pnl_r_net": virtual_trade.r_multiple or 0.0,
                    "pnl_r_gross": virtual_trade.r_multiple or 0.0,
                    "mfe_pct": virtual_trade.peak_r or 0.0,
                    "mae_pct": virtual_trade.trough_r or 0.0,
                    "duration_minutes": virtual_trade.duration_minutes or 0,
                    "entry": virtual_trade.entry_price,
                    "stop": (
                        virtual_trade.entry_price - (virtual_trade.risk_dollars / max(virtual_trade.shares, 1))
                        if virtual_trade.direction == "LONG" and virtual_trade.shares > 0 and virtual_trade.risk_dollars > 0
                        else virtual_trade.entry_price + (virtual_trade.risk_dollars / max(virtual_trade.shares, 1))
                        if virtual_trade.shares > 0 and virtual_trade.risk_dollars > 0
                        else virtual_trade.entry_price
                    ),
                    "target1": virtual_trade.exit_price or virtual_trade.entry_price,
                    "exit_price": virtual_trade.exit_price or 0.0,
                    "generated_at": virtual_trade.entry_time or "",
                    "closed_at": virtual_trade.exit_time or "",
                    "confidence": virtual_trade.confidence or 0.0,
                    "cost_bps": 10.0,
                })
            except Exception as e:
                logger.warning("Adaptive engine update failed: %s", e)

            # --- DIRECT FAILURE ANALYSIS: Richer categorisation from virtual_trade context ---
            if virtual_trade.r_multiple < 0:
                try:
                    _exit_ctx = virtual_trade.indicator_snapshot_exit
                    if isinstance(_exit_ctx, str):
                        try:
                            _exit_ctx = json.loads(_exit_ctx)
                        except Exception:
                            _exit_ctx = {}
                    if not isinstance(_exit_ctx, dict):
                        _exit_ctx = {}
                    _failure_data = {
                        "ticker": virtual_trade.ticker,
                        "strategy": virtual_trade.strategy,
                        "direction": virtual_trade.direction,
                        "r_multiple": virtual_trade.r_multiple,
                        "exit_reason": virtual_trade.exit_reason or "",
                        "peak_r": virtual_trade.peak_r or 0,
                        "trough_r": virtual_trade.trough_r or 0,
                        "slippage": getattr(virtual_trade, "slippage", 0) or 0,
                        "regime_at_entry": virtual_trade.regime_at_entry or "",
                        "regime_at_exit": _exit_ctx.get("regime_at_exit", virtual_trade.regime_at_entry or ""),
                    }
                    _fail_cat = self.learning.failure_analysis.record_failure(_failure_data)
                    logger.info(
                        "FAILURE_DIRECT: %s %s R=%.2f → %s",
                        virtual_trade.ticker, virtual_trade.strategy,
                        virtual_trade.r_multiple, _fail_cat,
                    )
                except Exception:
                    pass  # Failure analysis is advisory, never blocking

            # --- DIRECT INDICATOR TRACKER: Feed rich indicator snapshots from virtual_trade ---
            try:
                _entry_snap_ind = virtual_trade.indicator_snapshot_entry
                if isinstance(_entry_snap_ind, str):
                    try:
                        _entry_snap_ind = json.loads(_entry_snap_ind)
                    except Exception:
                        _entry_snap_ind = {}
                if isinstance(_entry_snap_ind, dict) and _entry_snap_ind:
                    self.learning.indicator_tracker.record_trade({
                        "r_multiple": virtual_trade.r_multiple or 0,
                        "direction": virtual_trade.direction or "LONG",
                        "regime": virtual_trade.regime_at_entry or "UNKNOWN",
                        "indicators": _entry_snap_ind,
                    })
            except Exception:
                pass  # Indicator tracking is advisory, never blocking

            # --- Feed Dynamic Sizer: update Kelly stats + streak ---
            try:
                self.dynamic_sizer.update_from_trade(
                    trade.pnl_r_multiple or 0.0,
                    ticker=trade.ticker or "",
                )
            except Exception as ds_err:
                logger.warning("Dynamic sizer trade update failed: %s", ds_err)

            # --- W12 Incremental Learner: PA online update on every trade ---
            # Crammer et al. (2006): mistake bound O(sqrt(T)), no batching needed.
            # Runs alongside LightGBM — provides fast regime adaptation between nightly retrains.
            if self.incremental_learner:
                try:
                    outcome_dict = {
                        "confidence": virtual_trade.confidence or 50,
                        "rvol": getattr(virtual_trade, "rvol", 1.0) or 1.0,
                        "adx": getattr(virtual_trade, "adx", 20) or 20,
                        "vix": (
                            self._current_market_ctx.vix
                            if self._current_market_ctx else 18
                        ),
                        "sector_rank": getattr(virtual_trade, "sector_rank", 3) or 3,
                        "momentum_score": getattr(virtual_trade, "momentum_score", 0.5) or 0.5,
                        "regime": virtual_trade.regime_at_entry or "UNKNOWN",
                        "status": "WIN" if (virtual_trade.r_multiple or 0) > 0 else "LOSS",
                    }
                    # Get active learning weight (Settles 2009: weight by uncertainty)
                    weight = 1.0
                    if self.active_learning:
                        weight = max(0.2, self.active_learning.get_learning_value(
                            virtual_trade.confidence or 50
                        ))
                    self.incremental_learner.update(outcome_dict, weight=weight)
                except Exception as _il_err:
                    logger.warning("IncrementalLearner update failed (non-critical): %s", _il_err)

            # --- W12 Bayesian Win Rate: update posterior on every trade ---
            # Gelman et al. (2013): 40% tighter CI than Wilson after 5 trades.
            # Per-regime priors carry knowledge across regime transitions.
            if self.bayesian_win_rate:
                try:
                    is_win = (virtual_trade.r_multiple or 0) > 0
                    regime = virtual_trade.regime_at_entry or "ALL"
                    strategy = virtual_trade.strategy or "ALL"
                    # Update overall posterior
                    self.bayesian_win_rate.update(
                        wins=1 if is_win else 0,
                        losses=0 if is_win else 1,
                        key="ALL",
                        regime=regime,
                    )
                    # Update per-strategy posterior
                    self.bayesian_win_rate.update(
                        wins=1 if is_win else 0,
                        losses=0 if is_win else 1,
                        key=f"strat:{strategy}",
                        regime=regime,
                    )
                except Exception as _bwr_err:
                    logger.warning("BayesianWinRate update failed (non-critical): %s", _bwr_err)

            # --- Feed Circuit Breakers: update consecutive loss tracking ---
            try:
                self.circuit_breakers.record_trade_result(trade.pnl_r_multiple or 0.0)
            except Exception as cb_err:
                logger.warning("Circuit breaker trade update failed: %s", cb_err)

            # --- Feed Discipline Engine: track streaks, cooldowns, excellence ---
            try:
                r_multiple = virtual_trade.r_multiple or 0.0
                pnl_pct = (virtual_trade.net_pnl / self.equity * 100) if self.equity > 0 else 0.0
                disc_insights = self.discipline.record_trade(r_multiple, pnl_pct)
                if disc_insights.get("events"):
                    for event in disc_insights["events"]:
                        logger.info("DISCIPLINE: %s", event)
            except Exception as disc_err:
                logger.warning("Discipline trade recording failed: %s", disc_err)

            # --- Feed Performance Attribution Engine: 6-factor decomposition ---
            try:
                import json as _json
                entry_snap = virtual_trade.indicator_snapshot_entry
                if isinstance(entry_snap, str):
                    try:
                        entry_snap = _json.loads(entry_snap)
                    except Exception as e:
                        logger.warning("JSON parse of attribution entry_snap failed: %s", e)
                        entry_snap = {}
                exit_snap = virtual_trade.indicator_snapshot_exit
                if isinstance(exit_snap, str):
                    try:
                        exit_snap = _json.loads(exit_snap)
                    except Exception as e:
                        logger.warning("JSON parse of attribution exit_snap failed: %s", e)
                        exit_snap = {}
                attribution = self.perf_attribution.attribute_trade({
                    "trade_id": virtual_trade.id,
                    "ticker": virtual_trade.ticker,
                    "strategy": virtual_trade.strategy,
                    "direction": virtual_trade.direction,
                    "entry_price": virtual_trade.entry_price,
                    "exit_price": virtual_trade.exit_price,
                    "stop_price": (
                        virtual_trade.entry_price - (virtual_trade.risk_dollars / max(virtual_trade.shares, 1))
                        if virtual_trade.direction == "LONG"
                        else virtual_trade.entry_price + (virtual_trade.risk_dollars / max(virtual_trade.shares, 1))
                    ) if virtual_trade.shares > 0 and virtual_trade.risk_dollars > 0 else 0,
                    "target_price": 0,  # Not available on VirtualTrade — attribution handles gracefully
                    "shares": virtual_trade.shares,
                    "entry_time": virtual_trade.entry_time,
                    "exit_time": virtual_trade.exit_time,
                    "r_multiple": virtual_trade.r_multiple,
                    "pnl_dollars": virtual_trade.net_pnl,
                    "mfe_r": virtual_trade.peak_r,
                    "mae_r": virtual_trade.trough_r,
                    "regime_at_entry": virtual_trade.regime_at_entry,
                    "regime_at_exit": getattr(virtual_trade, "regime_at_exit", ""),
                    "confidence": virtual_trade.confidence,
                    "entry_indicators": entry_snap if isinstance(entry_snap, dict) else {},
                    "exit_indicators": exit_snap if isinstance(exit_snap, dict) else {},
                    "market_return_during": (exit_snap or {}).get("market_direction_during", 0),
                })
                logger.debug(
                    "ATTRIBUTION: %s grade=%d setup=%.0f timing=%.0f sizing=%.0f",
                    virtual_trade.ticker,
                    attribution.get("overall_grade", 0),
                    attribution.get("factors", {}).get("setup_quality", 0),
                    attribution.get("factors", {}).get("timing_quality", 0),
                    attribution.get("factors", {}).get("sizing_quality", 0),
                )
            except Exception as pa_err:
                logger.warning("Performance attribution failed: %s", pa_err)

            # --- Feed Edge Decay Engine: record trade into time buckets ---
            try:
                self.edge_decay.record_trade(
                    strategy=trade.strategy,
                    regime=trade.regime_state or "UNKNOWN",
                    entry_time=trade.time_entered,
                    r_multiple=trade.pnl_r_multiple or 0.0,
                )
            except Exception as ed_err:
                logger.warning("Edge decay trade record failed: %s", ed_err)

            # --- Persist Edge Decay state periodically (every trade) ---
            try:
                with transaction() as conn:
                    self.edge_decay.save_state(conn)
            except Exception as save_err:
                logger.warning("Edge decay state save failed: %s", save_err)

            # --- Strategy Tournament: record trade result ---
            try:
                alert = self.tournament.record_trade(
                    strategy=trade.strategy,
                    r_multiple=trade.pnl_r_multiple or 0.0,
                )
                if alert and alert.get("type") == "STRATEGY_BENCHED":
                    _t = asyncio.get_event_loop().create_task(
                        self.telegram.send_alert(
                            f"STRATEGY BENCHED: {alert['strategy']}\n"
                            f"Points: {alert['points']:.0f} | "
                            f"Trades: {alert['trades']}\n"
                            f"Reason: {alert['reason']}"
                        )
                    )
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)
            except Exception as e:
                logger.warning("Tournament record_trade failed: %s", e)

            # --- Run Trade Autopsy (5-grade analysis) ---
            try:
                entry_indicators = getattr(virtual_trade, 'indicator_snapshot_entry', {}) or {}
                autopsy = self.trade_autopsy.analyse(
                    trade=virtual_trade,
                    entry_indicators=entry_indicators,
                    market_ctx={"regime": virtual_trade.regime_at_entry},
                )
                # AI-enhanced lesson for significant trades (|R| >= 1.0)
                ai_key = os.environ.get("GEMINI_API_KEY", "")
                if ai_key:
                    try:
                        _t = asyncio.get_event_loop().create_task(
                            self.trade_autopsy.enhance_with_ai(
                                autopsy, virtual_trade,
                                ai_api_key=ai_key, ai_model="gemini-2.5-flash",
                            )
                        )
                        self._background_tasks.add(_t)
                        _t.add_done_callback(self._background_tasks.discard)
                    except Exception as e:
                        logger.warning("AI autopsy enhancement failed: %s", e)
                # Persist autopsy to database
                with transaction() as conn:
                    self.trade_autopsy.persist(conn, autopsy)
            except Exception as e:
                logger.warning("Trade autopsy failed (non-critical): %s", e)

            # --- AI Research Engine: event-driven triggers ---
            # Silver et al. (2016): self-critique loops accelerate improvement.
            # Triggers are non-blocking — run in background via asyncio task.
            if self.ai_research:
                try:
                    current_regime = virtual_trade.regime_at_entry or "UNKNOWN"
                    r_multiple = virtual_trade.r_multiple or 0.0

                    # TRIGGER 1: Performance autopsy on consecutive loss streak (≥4)
                    # or rolling win rate drop. Only fire once per streak to avoid spam.
                    if self._consecutive_losses >= 4:
                        recent_outcomes = self._get_recent_outcomes(n=40)
                        losing_trades = [o for o in recent_outcomes if (o.get("r_multiple") or 0) < 0]
                        wins = sum(1 for o in recent_outcomes if (o.get("r_multiple") or 0) > 0)
                        recent_wr = wins / len(recent_outcomes) if recent_outcomes else 0.5
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            self.ai_research.performance_autopsy,
                            losing_trades, current_regime, recent_wr,
                        )
                        logger.info(
                            "AI RESEARCH: Performance autopsy triggered — %d consecutive losses",
                            self._consecutive_losses,
                        )

                    # TRIGGER 2: Anomaly explanation when actual_r deviates >1.5R from
                    # ticker's historical average (Bali et al. 2011 MAX anomaly detection).
                    # Only trigger on significant negative deviations (not routine losses).
                    elif r_multiple < -1.0:
                        recent_outcomes = self._get_recent_outcomes(n=100)
                        ticker_outcomes = [
                            o for o in recent_outcomes
                            if o.get("ticker") == virtual_trade.ticker
                        ]
                        if len(ticker_outcomes) >= 5:
                            expected_r = sum(
                                o.get("r_multiple", 0) for o in ticker_outcomes
                            ) / len(ticker_outcomes)
                            deviation = r_multiple - expected_r
                            if deviation < -1.5:
                                trade_ctx = {
                                    "strategy": virtual_trade.strategy,
                                    "regime": current_regime,
                                    "confidence": virtual_trade.confidence,
                                    "exit_reason": virtual_trade.exit_reason or "",
                                    "duration_minutes": virtual_trade.duration_minutes or 0,
                                    "peak_r": virtual_trade.peak_r or 0,
                                }
                                asyncio.get_event_loop().run_in_executor(
                                    None,
                                    self.ai_research.anomaly_explanation_query,
                                    virtual_trade.ticker, expected_r, r_multiple, trade_ctx,
                                )
                                logger.info(
                                    "AI RESEARCH: Anomaly explanation triggered — %s "
                                    "expected=%.2fR actual=%.2fR deviation=%.2fR",
                                    virtual_trade.ticker, expected_r, r_multiple, deviation,
                                )
                except Exception as _air_err:
                    logger.warning("AI Research Engine trade trigger failed (non-critical): %s", _air_err)

        except Exception as e:
            logger.error("Learning engine record_trade failed: %s", e)

    async def _execute_s15_priority_path(
        self,
        s15_signals: list[Signal],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """S15 PRIORITY PATH — bypasses the 18-gate gauntlet.

        S15 signals go through 5 essential gates only, then execute directly.
        This is THE critical fix: the full gauntlet was mathematically killing
        every S15 signal (confidence 30-40 vs system floor 60).

        5 ESSENTIAL GATES:
        1. LSE Hours (already checked by S15.scan())
        2. Daily Loss Limit (max -3% daily drawdown)
        3. Max 1 S15 Position at a time
        4. VIX Filter (no 5x above VIX 22, half-size above 22) — already in S15.scan()
        5. Position Sizing (quarter-Kelly capped at 0.75% risk) — Kelly 1956, Thorp 2006
        """
        executed: list[Signal] = []

        for signal in s15_signals:
            try:
                # T-10: Extract tier from signal metadata
                _signal_tier = "SLOW"
                if hasattr(signal, 'metadata') and signal.metadata:
                    _signal_tier = signal.metadata.get("tier", "SLOW")

                # T-10: FAST tier lunch block — gap signals from 09:05 are stale by 11:30
                if _signal_tier == "FAST":
                    from core.clock import now_uk as _now_uk_fn_t10
                    _now_uk_t10 = _now_uk_fn_t10()
                    _is_lunch_t10 = (
                        (_now_uk_t10.hour == 11 and _now_uk_t10.minute >= 30)
                        or _now_uk_t10.hour == 12
                    )
                    if _is_lunch_t10:
                        logger.info(
                            "S15 FAST PATH: %s BLOCKED during lunch (11:30-12:59) — stale gap signal",
                            signal.ticker,
                        )
                        continue

                # GATE 0: Discipline Engine (absolute authority) — "No trade > bad trade"
                _disc_vix = self._current_market_ctx.vix if self._current_market_ctx else 0.0
                _disc_regime = self._current_market_ctx.regime.value if self._current_market_ctx else "NEUTRAL"
                disc_ok, disc_reason = self.discipline.should_trade(
                    setup_quality=signal.confidence if hasattr(signal, 'confidence') else 65,
                    regime=_disc_regime,
                    vix=_disc_vix,
                )
                if not disc_ok:
                    logger.info("S15 DISCIPLINE VETO: %s | %s", signal.ticker, disc_reason)
                    continue

                # T-10: FAST tier skips confidence modifiers (PEAD, VWAP, sector, etc.)
                # FAST tier already has 3/4 leading indicator agreement as consensus.
                # Only SLOW tier gets the 9 confidence adjustments below.
                #
                # SA-1 ARCHITECTURE NOTE: FAST signals pass through here and proceed to
                # execution. The Earnings Fade Gate below runs for ALL tiers (safety veto).
                # All SLOW-only modifiers below are gated with `if _signal_tier != "FAST"`.
                # If adding a NEW modifier, you MUST gate it with `if _signal_tier != "FAST"`
                # unless it is a safety veto that should apply to all tiers.
                if _signal_tier == "FAST":
                    logger.info(
                        "S15 FAST PATH QUALIFIED: %s %s (8 gates, skipping confidence modifiers)",
                        signal.direction.value if hasattr(signal.direction, 'value') else signal.direction,
                        signal.ticker,
                    )

                # === SAFETY VETOES (run for ALL tiers including FAST) ===
                # GATE RC-07b: Earnings Fade Gate — Kim & Verrecchia (1991)
                # Pre-earnings run-up ≥ 8% in last 10 sessions → VETO new longs
                if self.earnings_fade_gate:
                    try:
                        if self.earnings_fade_gate.is_fade_risk(signal.ticker):
                            logger.info(
                                "RC-07b EARNINGS FADE VETO: %s — pre-earnings run-up ≥ 8%%, "
                                "beat likely priced in. Skipping long entry.",
                                signal.ticker,
                            )
                            continue
                    except Exception as _fade_err:
                        logger.warning("Earnings fade gate error (non-critical): %s", _fade_err)

                # === SLOW-ONLY CONFIDENCE MODIFIERS (FAST tier skips all of these) ===

                # V3.1: PEAD boost — Bernard & Thomas (1989)
                # If this ticker just had a clean earnings beat (low run-up), apply PEAD boost
                if _signal_tier != "FAST" and self.sue_pead:
                    try:
                        pead_sig = self.sue_pead.get_pead_signal(
                            signal.ticker, is_lse_etp=signal.ticker.endswith(".L")
                        )
                        if pead_sig and pead_sig.get("direction") == "LONG":
                            boost = pead_sig.get("confidence_boost", 0)
                            if hasattr(signal, "confidence"):
                                signal.confidence = min(100, signal.confidence + boost)
                            logger.info(
                                "PEAD BOOST: %s SUE=%.1f +%d conf (day %d post-earnings)",
                                signal.ticker, pead_sig["sue"], boost,
                                pead_sig["days_since_announcement"],
                            )
                    except Exception as _pead_err:
                        logger.warning("PEAD boost error: %s", _pead_err)

                # V3.1: VWAP signal — Madhavan, Richardson & Roomans (1997)
                # Adjust confidence based on VWAP position
                if _signal_tier != "FAST" and self.vwap_engine:
                    try:
                        snap = indicators.get(signal.ticker)
                        if snap and snap.vwap and snap.price:
                            vwap_result = self.vwap_engine.classify(
                                signal.ticker,
                                current_price=snap.price,
                                vwap=snap.vwap,
                                rvol=snap.rvol or 1.0,
                            )
                            vwap_adj = vwap_result.get("confidence_adjustment", 0)
                            if vwap_adj != 0 and hasattr(signal, "confidence"):
                                signal.confidence = max(30, min(100, signal.confidence + vwap_adj))
                                logger.debug(
                                    "VWAP %s: %s dev=%.2f%% conf%+d",
                                    vwap_result.get("signal"), signal.ticker,
                                    vwap_result.get("vwap_deviation_pct", 0), vwap_adj,
                                )
                    except Exception as _vwap_err:
                        logger.warning("VWAP signal error: %s", _vwap_err)

                # V3.1: Sector momentum — Moskowitz & Grinblatt (1999)
                if _signal_tier != "FAST" and self.sector_momentum_engine:
                    try:
                        sec_adj = self.sector_momentum_engine.get_confidence_adjustment(signal.ticker)
                        if sec_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + sec_adj))
                            logger.debug(
                                "SECTOR MOMENTUM: %s adj=%+d (sector=%s rank=%s)",
                                signal.ticker, sec_adj,
                                self.sector_momentum_engine.get_sector(signal.ticker),
                                self.sector_momentum_engine.get_sector_rank(signal.ticker),
                            )
                    except Exception as _sec_err:
                        logger.warning("Sector momentum error: %s", _sec_err)

                # ── V3.2: WIRED SILENT MODULES ──────────────────────────────────

                # Options Expiry Pinning — Ni, Pearson & Poteshman (2005)
                # Pre-expiry week: -8 conf (pinning suppresses momentum)
                # Post-monthly-expiry Mon–Wed: +5 conf (gamma freed)
                if _signal_tier != "FAST" and self.expiry_pinning:
                    try:
                        exp_adj = self.expiry_pinning.get_confidence_adjustment(signal.ticker)
                        if exp_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + exp_adj))
                            logger.debug("EXPIRY PINNING: %s adj=%+d", signal.ticker, exp_adj)
                    except Exception as _exp_err:
                        logger.warning("Expiry pinning error: %s", _exp_err)

                # Window Dressing — Lakonishok et al. (1994)
                # Quarter-end: +5 conf for YTD winners; new-Q: -5 conf (unwind risk)
                if _signal_tier != "FAST" and self.window_dressing:
                    try:
                        # Treat rvol > 1.5 as proxy for YTD winner status
                        rvol = getattr(signal, "rvol", 1.0) or 1.0
                        wd_adj = self.window_dressing.get_confidence_adjustment(
                            signal.ticker, is_ytd_winner=rvol > 1.5
                        )
                        if wd_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + wd_adj))
                            logger.debug("WINDOW DRESSING: %s adj=%+d", signal.ticker, wd_adj)
                    except Exception as _wd_err:
                        logger.warning("Window dressing error: %s", _wd_err)

                # Gap Analytics — Lou et al. (2013)
                # Classify overnight gap and adjust confidence accordingly
                if _signal_tier != "FAST" and self.gap_analytics:
                    try:
                        ticker_ind = indicators.get(signal.ticker)
                        if ticker_ind:
                            open_px = getattr(ticker_ind, "open_price", None)
                            prev_close = getattr(ticker_ind, "prev_close", None)
                            rvol_val = getattr(ticker_ind, "rvol", 1.0) or 1.0
                            if open_px and prev_close and prev_close > 0:
                                gap_result = self.gap_analytics.classify_gap(
                                    signal.ticker, open_px, prev_close, rvol_val
                                )
                                gap_adj = gap_result.get("confidence_adjustment", 0)
                                if gap_adj != 0 and hasattr(signal, "confidence"):
                                    signal.confidence = max(30, min(100, signal.confidence + gap_adj))
                                    logger.debug(
                                        "GAP ANALYTICS: %s gap=%s adj=%+d",
                                        signal.ticker, gap_result.get("gap_type", ""), gap_adj,
                                    )
                    except Exception as _gap_err:
                        logger.warning("Gap analytics error: %s", _gap_err)

                # Short Squeeze — Cohen, Diether & Malloy (2007)
                # High short interest + price strength = squeeze amplifier (+8 conf)
                if _signal_tier != "FAST" and self.short_squeeze:
                    try:
                        sq_boost = self.short_squeeze.get_confidence_boost(signal.ticker)
                        if sq_boost != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + sq_boost))
                            logger.debug("SHORT SQUEEZE: %s boost=%+d", signal.ticker, sq_boost)
                    except Exception as _sq_err:
                        logger.warning("Short squeeze error: %s", _sq_err)

                # ── V3.2 NEW ACADEMIC SIGNAL WIRING ─────────────────────────────

                # Order Flow Imbalance — Chordia & Subrahmanyam (2004)
                # Volume-weighted buy/sell imbalance: OFI > 0.3 = bullish +8, < -0.3 = bearish -8
                if _signal_tier != "FAST" and self.order_flow_imbalance:
                    try:
                        ofi_adj = self.order_flow_imbalance.get_confidence_adjustment(signal.ticker)
                        if ofi_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + ofi_adj))
                            logger.debug("OFI: %s adj=%+d", signal.ticker, ofi_adj)
                    except Exception as _e:
                        logger.warning("OFI error: %s", _e)

                # Overnight Gap Persistence — Lou et al. (2013)
                # Overnight gaps persist for leveraged ETPs 68% of the time (not mean-reverting)
                if _signal_tier != "FAST" and self.overnight_gap:
                    try:
                        gap_persist_adj = self.overnight_gap.get_confidence_adjustment(signal.ticker)
                        if gap_persist_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + gap_persist_adj))
                            logger.debug("OVERNIGHT GAP: %s adj=%+d", signal.ticker, gap_persist_adj)
                    except Exception as _e:
                        logger.warning("Overnight gap error: %s", _e)

                # Analyst Revision Momentum — Boni (2004), Womack (1996)
                # Upward EPS/price-target revisions in past 5 days = persistent alpha signal
                if _signal_tier != "FAST" and self.analyst_revision:
                    try:
                        rev_adj = self.analyst_revision.get_confidence_adjustment(signal.ticker)
                        if rev_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + rev_adj))
                            logger.debug("ANALYST REVISION: %s adj=%+d", signal.ticker, rev_adj)
                    except Exception as _e:
                        logger.warning("Analyst revision error: %s", _e)

                # Cross-Asset Macro — Erb & Harvey (2006)
                # DXY strength, VIX term structure, credit spread proxy signals
                if _signal_tier != "FAST" and self.cross_asset_macro:
                    try:
                        macro_adj = self.cross_asset_macro.get_confidence_adjustment(signal.ticker)
                        if macro_adj != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + macro_adj))
                            logger.debug("CROSS-ASSET MACRO: %s adj=%+d", signal.ticker, macro_adj)
                    except Exception as _e:
                        logger.warning("Cross-asset macro error: %s", _e)

                # Accruals Quality Veto — Sloan (1996)
                # High accruals (earnings > operating CF) = earnings quality warn/veto
                # Reduces or cancels PEAD confidence boost for low-quality earnings
                if _signal_tier != "FAST" and self.accruals_veto:
                    try:
                        # Get veto status — True = cancel/reduce confidence significantly
                        av_blocked, av_reason = self.accruals_veto.should_veto(signal.ticker)
                        if av_blocked and hasattr(signal, "confidence"):
                            # Hard veto: reduce confidence by 20 (kills marginal setups)
                            signal.confidence = max(30, signal.confidence - 20)
                            logger.info(
                                "ACCRUALS VETO: %s confidence reduced — %s",
                                signal.ticker, av_reason,
                            )
                    except Exception as _e:
                        logger.warning("Accruals veto error: %s", _e)

                # ── END V3.2 SILENT MODULE WIRING ───────────────────────────────

                # GATE 1: LSE Hours — already enforced by S15.scan(), double-check
                from core.clock import now_uk as _now_uk_fn
                now_uk = _now_uk_fn()
                if now_uk.hour < 9 or (now_uk.hour >= 15 and now_uk.minute >= 15):
                    logger.info("S15 PRIORITY: outside LSE hours, skipping %s", signal.ticker)
                    continue

                # GATE 2: Daily Loss Limit — max -3% daily drawdown (also in discipline engine)
                if self._daily_pnl_pct < -0.03:
                    logger.warning(
                        "S15 PRIORITY: daily drawdown %.2f%% exceeds -3%% limit, skipping",
                        self._daily_pnl_pct * 100,
                    )
                    continue

                # GATE 3: Max 1 S15 position at a time
                s15_open = sum(
                    1 for pos in self.virtual_trader.open_positions.values()
                    if pos.status == "OPEN" and pos.strategy == "S15"
                )
                if s15_open >= 1:
                    logger.info("S15 PRIORITY: already have %d open S15 position(s), skipping", s15_open)
                    continue

                # GATE 4: VIX filter — already handled in S15._score_ticker (5x blocked, flag set)
                # Half-size flag is on the signal metadata (set by S15 scoring)

                # GATE 5: Position Sizing — Merton Continuous-Time Kelly (Mandate 1)
                # Merton (1971): f* = μ/σ², leverage-adjusted per MacLean, Thorp & Ziemba (2011)
                # Half-Kelly with sample-size ramp, hard-capped at 0.75% (immutable)
                risk_pct = self.kelly.get_risk_pct(ticker=signal.ticker) if hasattr(self, 'kelly') and self.kelly else 0.0075
                risk_dollars = self.equity * risk_pct

                # VIX half-size: reduce risk by 50% when VIX > 22
                vix = getattr(market_ctx, 'vix', 0) or 0
                if vix >= 22.0:
                    risk_dollars *= 0.5
                    logger.info("S15 PRIORITY: VIX=%.1f >= 22 — half-size: risk=$%.2f", vix, risk_dollars)

                # IV Crush — Amin & Lee (1997): size/stop adjustment for high-IV environments
                _iv_size_mult = 1.0
                _iv_stop_mult = 1.0
                if _signal_tier != "FAST" and self.iv_crush:
                    try:
                        _iv_size_mult = self.iv_crush.get_size_multiplier(signal.ticker)
                        _iv_stop_mult = self.iv_crush.get_stop_multiplier(signal.ticker)
                        if _iv_size_mult != 1.0 or _iv_stop_mult != 1.0:
                            risk_dollars *= _iv_size_mult
                            logger.debug(
                                "IV CRUSH: %s size×%.2f stop×%.2f risk=$%.2f",
                                signal.ticker, _iv_size_mult, _iv_stop_mult, risk_dollars,
                            )
                    except Exception as _iv_err:
                        logger.warning("IV crush sizing error: %s", _iv_err)

                # ── V3.2 INSTITUTIONAL RISK GATES ────────────────────────────────

                # Net Expectancy Veto — Thorp (1997), Vince (1992)
                # Skip if net expectancy E < 0.05 (no edge after costs)
                if _signal_tier != "FAST" and self.net_expectancy:
                    try:
                        _ne_blocked, _ne_reason = self.net_expectancy.get_entry_veto(
                            signal.ticker, "S15", market_ctx.regime.value
                        )
                        if _ne_blocked:
                            logger.info(
                                "S15: NET EXPECTANCY VETO %s — %s", signal.ticker, _ne_reason
                            )
                            continue
                    except Exception as _e:
                        logger.warning("Net expectancy veto error: %s", _e)

                # Capacity Constraint Skip — Bouchaud et al. (2009)
                # Skip thin ETPs where order size > 2% ADV (significant market impact)
                if _signal_tier != "FAST" and self.capacity_monitor:
                    try:
                        _cap_skip, _cap_reason = self.capacity_monitor.should_skip_thin_etp(
                            signal.ticker, risk_dollars
                        )
                        if _cap_skip:
                            logger.info(
                                "S15: CAPACITY SKIP %s — %s", signal.ticker, _cap_reason
                            )
                            continue
                    except Exception as _e:
                        logger.warning("Capacity monitor error: %s", _e)

                # Tail Loss Size Reduction — Taleb (2007), Bali et al. (2011)
                # CVaR_5% > -3R: reduce all sizes 25%
                if _signal_tier != "FAST" and self.tail_loss:
                    try:
                        _outcomes = self._get_recent_outcomes(200)
                        _tl_mult = self.tail_loss.get_size_multiplier(_outcomes)
                        if _tl_mult < 1.0:
                            risk_dollars *= _tl_mult
                            logger.info(
                                "S15: TAIL LOSS size ×%.2f risk=$%.2f", _tl_mult, risk_dollars
                            )
                    except Exception as _e:
                        logger.warning("Tail loss sizing error: %s", _e)

                # Regime Stability Size Multiplier — Guidolin & Timmermann (2007)
                # First 3 days after regime change: 50% size max
                if _signal_tier != "FAST" and self.regime_stability:
                    try:
                        _regime_str = (
                            market_ctx.regime.value
                            if hasattr(market_ctx.regime, "value")
                            else str(market_ctx.regime)
                        )
                        self.regime_stability.record_regime(_regime_str)
                        _rs_score = self.regime_stability.get_stability_score(_regime_str)
                        _rs_mult = _rs_score.get("size_multiplier", 1.0)
                        if _rs_mult < 1.0:
                            risk_dollars *= _rs_mult
                            logger.info(
                                "S15: REGIME STABILITY size ×%.2f stability=%.2f — %s day %d",
                                _rs_mult, _rs_score["combined"], _regime_str,
                                _rs_score["days_in_regime"],
                            )
                    except Exception as _e:
                        logger.warning("Regime stability sizing error: %s", _e)

                # Performance Relegation Size Multiplier — B_TEAM gets 0.5x
                if _signal_tier != "FAST" and self.performance_relegation:
                    try:
                        _pr_mult = self.performance_relegation.get_size_multiplier(signal.ticker)
                        if _pr_mult < 1.0:
                            risk_dollars *= _pr_mult
                            logger.info(
                                "S15: RELEGATION size ×%.2f for %s", _pr_mult, signal.ticker
                            )
                    except Exception as _e:
                        logger.warning("Performance relegation sizing error: %s", _e)

                # ── END V3.2 INSTITUTIONAL RISK GATES ────────────────────────────

                # Calculate position size
                entry_price = signal.entry
                stop_price = signal.stop
                # Apply IV stop multiplier — widen stop when IV is elevated
                if _iv_stop_mult != 1.0 and stop_price and entry_price:
                    stop_distance = abs(entry_price - stop_price)
                    widened_distance = stop_distance * _iv_stop_mult
                    if signal.direction == "LONG":
                        stop_price = entry_price - widened_distance
                    else:
                        stop_price = entry_price + widened_distance
                    signal.stop = stop_price
                risk_per_share = abs(entry_price - stop_price)
                if risk_per_share <= 0:
                    logger.warning("S15 PRIORITY: zero risk per share for %s, skipping", signal.ticker)
                    continue

                shares = max(1, int(risk_dollars / risk_per_share))
                signal.shares = shares
                signal.risk_dollars = risk_dollars

                # Build indicator snapshot for learning
                indicator_snap = indicators.get(signal.ticker,
                    IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=signal.ticker))
                full_snapshot = {
                    "price": indicator_snap.price,
                    "vwap": indicator_snap.vwap,
                    "ema9": indicator_snap.ema9,
                    "ema20": indicator_snap.ema20,
                    "ema50": indicator_snap.ema50,
                    "rsi14": indicator_snap.rsi14,
                    "macd_histogram": indicator_snap.macd_histogram,
                    "atr14": indicator_snap.atr14,
                    "atr_pct": indicator_snap.atr_pct,
                    "rvol": indicator_snap.rvol,
                    "adx14": indicator_snap.adx14,
                    "bb_upper": indicator_snap.bb_upper,
                    "bb_lower": indicator_snap.bb_lower,
                    "bb_middle": indicator_snap.bb_middle,
                    "obv": indicator_snap.obv,
                    "stochastic_rsi": indicator_snap.stochastic_rsi,
                    "regime": market_ctx.regime.value,
                    "regime_confidence": market_ctx.regime_confidence,
                    "vix": market_ctx.vix,
                    "time_window": market_ctx.time_window.value,
                    "gex_regime": market_ctx.gex_regime.value,
                    "pathway": "S15_PRIORITY",
                }

                # === PLAY SCORING WITH PERCENTAGE BRACKETS ===
                # Score the play using self-learning system before execution
                play_score = None
                try:
                    if self.b_team:
                        indicator_snap_obj = indicators.get(signal.ticker)
                        play_atr_pct = getattr(indicator_snap_obj, 'atr_pct', 0) or 0
                        play_rvol = getattr(indicator_snap_obj, 'rvol', 0) or 0
                        play_score = self.b_team.score_play(
                            ticker=signal.ticker,
                            signal_confidence=signal.confidence,
                            atr_pct=play_atr_pct,
                            rvol=play_rvol,
                            regime=market_ctx.regime.value if hasattr(market_ctx.regime, 'value') else str(market_ctx.regime),
                            direction=signal.direction.value if hasattr(signal.direction, 'value') else str(signal.direction),
                        )
                        logger.info(
                            "S15 PLAY SCORE: %s %s | Score=%d/100 | Bracket=%s | Grade=%s",
                            signal.direction.value, signal.ticker,
                            play_score["score"], play_score["bracket"], play_score["grade"],
                        )
                        for r in play_score.get("reasons", []):
                            logger.info(
                                "  → %s: %s (weight=%s, +%.1f) — %s",
                                r["factor"], r["value"], r["weight"], r["contribution"], r["reason"],
                            )
                        # Attach score to signal for downstream consumers
                        signal.play_score = play_score
                except Exception as e:
                    logger.warning("Play scoring failed (non-blocking): %s", e)

                # EXECUTE — publish to signal queue (V5.0 decoupled execution)
                try:
                    self._signal_queue.put_nowait({
                        "signal": signal,
                        "indicators": full_snapshot,
                        "source": "S15_PRIORITY",
                        "kelly_risk_pct": risk_pct if risk_pct else 0.0075,
                        "gauntlet_passed": True,
                    })
                except Full:
                    logger.warning("SIGNAL_QUEUE_FULL: dropped signal %s — queue at capacity", signal.ticker)
                    continue
                logger.warning(
                    "S15 PRIORITY QUEUED: %s %s | tier=%s | "
                    "stop £%.2f | target £%.2f | risk £%.2f | VIX=%.1f | "
                    "conf=%d | score=%d/%s | pathway=S15_PRIORITY",
                    signal.direction.value if hasattr(signal.direction, 'value') else signal.direction,
                    signal.ticker, _signal_tier,
                    signal.stop, signal.target_1r, risk_dollars, vix,
                    signal.confidence,
                    play_score["score"] if play_score else 0,
                    play_score["grade"] if play_score else "?",
                )
                executed.append(signal)

                # Send Telegram immediately (includes play score)
                try:
                    await self.telegram.send_signal(signal)
                    self.sheets.log_signal(signal)
                except Exception as e:
                    logger.warning("S15 PRIORITY delivery failed: %s", e)

            except Exception as e:
                logger.error("S15 PRIORITY PATH failed for %s: %s", signal.ticker, e, exc_info=True)

        return executed

    async def _check_s16_gauntlet(
        self,
        s16_signals: list[Signal],
        indicators: dict[str, IndicatorSnapshot],
        market_ctx: MarketContext,
        sector_flows: dict[str, SectorFlow],
        narratives: dict[str, NarrativeContext],
    ) -> list[Signal]:
        """S16 MEDIUM GAUNTLET — lighter than the 18-gate pipeline.

        S16 (Universal Scanner) signals go through 5 essential gates:
        1. LSE Hours check (for .L tickers; US tickers check NYSE hours)
        2. Portfolio risk — max 5 S16 positions at a time
        3. Correlation — no 2 S16 positions 80%+ correlated
        4. Minimum confidence 55 (lower than system-wide 60)
        5. Dynamic sizing with liquidity tier caps

        This lighter pipeline prevents the full 18-gate gauntlet from
        rejecting viable universal scanner plays while still enforcing
        essential risk controls.
        """
        executed: list[Signal] = []

        for signal in s16_signals:
            try:
                ticker = signal.ticker
                is_lse = ticker.upper().endswith(".L")

                # GATE 0: Discipline Engine (absolute authority) — "No trade > bad trade"
                _disc_vix_s16 = self._current_market_ctx.vix if self._current_market_ctx else 0.0
                _disc_regime_s16 = self._current_market_ctx.regime.value if self._current_market_ctx else "NEUTRAL"
                disc_ok, disc_reason = self.discipline.should_trade(
                    setup_quality=signal.confidence if hasattr(signal, 'confidence') else 55,
                    regime=_disc_regime_s16,
                    vix=_disc_vix_s16,
                )
                if not disc_ok:
                    logger.info("S16 DISCIPLINE VETO: %s | %s", ticker, disc_reason)
                    continue

                # GATE 1: Market Hours
                if is_lse:
                    from core.clock import now_uk as _now_uk_fn
                    now_uk = _now_uk_fn()
                    if now_uk.hour < 8 or (now_uk.hour >= 16):
                        logger.info("S16 GAUNTLET: outside LSE hours, skipping %s", ticker)
                        continue
                else:
                    from core.clock import now_et as _now_et_fn
                    now_et = _now_et_fn()
                    if now_et.hour < 9 or (now_et.hour == 9 and now_et.minute < 30) or now_et.hour >= 16:
                        logger.info("S16 GAUNTLET: outside NYSE hours, skipping %s", ticker)
                        continue

                # GATE 2: Portfolio Risk — max 5 S16 positions at a time
                s16_open = sum(
                    1 for pos in self.virtual_trader.open_positions.values()
                    if pos.status == "OPEN" and pos.strategy == "S16"
                )
                if s16_open >= 5:
                    logger.info("S16 GAUNTLET: already have %d open S16 positions (max 5), skipping %s",
                               s16_open, ticker)
                    continue

                # GATE 3: Correlation — no 2 S16 positions 80%+ correlated
                corr_blocked = False
                if hasattr(self, 'correlation_matrix') and self.correlation_matrix:
                    for pos in self.virtual_trader.open_positions.values():
                        if pos.status == "OPEN" and pos.strategy == "S16":
                            corr = self.correlation_matrix.get_correlation(ticker, pos.ticker)
                            if abs(corr) >= 0.80:
                                logger.info(
                                    "S16 GAUNTLET: %s correlated %.2f with open %s (>=0.80), skipping",
                                    ticker, corr, pos.ticker,
                                )
                                corr_blocked = True
                                break
                if corr_blocked:
                    continue

                # V3.2: Earnings Sentiment — Tetlock (2007) NLP news sentiment boost
                # Fired in S16 path: earnings headline sentiment boosts pre/post-event signals
                if self.earnings_sentiment:
                    try:
                        sent_boost = self.earnings_sentiment.get_confidence_boost(ticker)
                        if sent_boost != 0 and hasattr(signal, "confidence"):
                            signal.confidence = max(30, min(100, signal.confidence + sent_boost))
                            logger.debug("EARNINGS SENTIMENT: %s boost=%+d", ticker, sent_boost)
                    except Exception as _sent_err:
                        logger.warning("Earnings sentiment error: %s", _sent_err)

                # GATE 4: Minimum confidence 55 (lower than system-wide 60)
                if signal.confidence < 55:
                    logger.info("S16 GAUNTLET: confidence %d < 55 floor for %s, skipping",
                               int(signal.confidence), ticker)
                    continue

                # GATE 5: Dynamic Sizing with Liquidity Tier Caps
                risk_pct = 0.0075  # 0.75% base
                risk_dollars = self.equity * risk_pct

                # Liquidity tier caps (reduce size for lower liquidity)
                liquidity_tier = "UNKNOWN"
                if is_lse:
                    try:
                        from uk_isa.lse_registry import LSERegistry
                        registry = LSERegistry()
                        product = registry.get_product(ticker)
                        if product:
                            liquidity_tier = getattr(product, 'liquidity_tier', 'UNKNOWN') or 'UNKNOWN'
                    except Exception:
                        pass

                tier_multipliers = {
                    "HIGH": 1.0,
                    "MEDIUM": 0.7,
                    "LOW": 0.4,
                    "VERY_LOW": 0.2,
                    "ILLIQUID": 0.0,
                    "UNKNOWN": 0.5,
                }
                tier_mult = tier_multipliers.get(liquidity_tier, 0.5)
                if tier_mult <= 0:
                    logger.info("S16 GAUNTLET: %s liquidity tier %s is ILLIQUID, skipping",
                               ticker, liquidity_tier)
                    continue
                risk_dollars *= tier_mult

                # VIX adjustment
                vix = getattr(market_ctx, 'vix', 0) or 0
                if vix >= 25.0:
                    risk_dollars *= 0.5
                    logger.info("S16 GAUNTLET: VIX=%.1f >= 25 — half-size for %s", vix, ticker)

                # Calculate position size
                entry_price = signal.entry
                stop_price = signal.stop
                risk_per_share = abs(entry_price - stop_price)
                if risk_per_share <= 0:
                    logger.warning("S16 GAUNTLET: zero risk per share for %s, skipping", ticker)
                    continue

                shares = max(1, int(risk_dollars / risk_per_share))
                signal.shares = shares
                signal.risk_dollars = risk_dollars

                # Build indicator snapshot for learning
                indicator_snap = indicators.get(signal.ticker,
                    IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker=signal.ticker))
                full_snapshot = {
                    "price": indicator_snap.price,
                    "vwap": indicator_snap.vwap,
                    "ema9": indicator_snap.ema9,
                    "ema20": indicator_snap.ema20,
                    "ema50": indicator_snap.ema50,
                    "rsi14": indicator_snap.rsi14,
                    "macd_histogram": indicator_snap.macd_histogram,
                    "atr14": indicator_snap.atr14,
                    "atr_pct": indicator_snap.atr_pct,
                    "rvol": indicator_snap.rvol,
                    "adx14": indicator_snap.adx14,
                    "regime": market_ctx.regime.value,
                    "regime_confidence": market_ctx.regime_confidence,
                    "vix": market_ctx.vix,
                    "time_window": market_ctx.time_window.value,
                    "gex_regime": market_ctx.gex_regime.value,
                    "pathway": "S16_MEDIUM_GAUNTLET",
                    "liquidity_tier": liquidity_tier,
                }

                # === PLAY SCORING ===
                play_score = None
                try:
                    if self.b_team:
                        play_atr_pct = getattr(indicator_snap, 'atr_pct', 0) or 0
                        play_rvol = getattr(indicator_snap, 'rvol', 0) or 0
                        play_score = self.b_team.score_play(
                            ticker=signal.ticker,
                            signal_confidence=signal.confidence,
                            atr_pct=play_atr_pct,
                            rvol=play_rvol,
                            regime=market_ctx.regime.value if hasattr(market_ctx.regime, 'value') else str(market_ctx.regime),
                            direction=signal.direction.value if hasattr(signal.direction, 'value') else str(signal.direction),
                        )
                        logger.info(
                            "S16 PLAY SCORE: %s %s | Score=%d/100 | Grade=%s | Tier=%s",
                            signal.direction.value, signal.ticker,
                            play_score["score"], play_score["grade"], liquidity_tier,
                        )
                        signal.play_score = play_score
                except Exception as e:
                    logger.warning("S16 play scoring failed (non-blocking): %s", e)

                # EXECUTE — publish to signal queue (V5.0 decoupled execution)
                try:
                    self._signal_queue.put_nowait({
                        "signal": signal,
                        "indicators": full_snapshot,
                        "source": "S16_MEDIUM_GAUNTLET",
                        "kelly_risk_pct": risk_pct if risk_pct else 0.0075,
                        "gauntlet_passed": True,
                    })
                except Full:
                    logger.warning("SIGNAL_QUEUE_FULL: dropped signal %s — queue at capacity", signal.ticker)
                    continue
                logger.warning(
                    "S16 GAUNTLET QUEUED: %s %s | "
                    "stop £%.2f | target £%.2f | risk £%.2f | tier=%s | "
                    "conf=%d | pathway=S16_MEDIUM_GAUNTLET",
                    signal.direction.value, signal.ticker,
                    signal.stop, signal.target_1r, risk_dollars, liquidity_tier,
                    int(signal.confidence),
                )
                executed.append(signal)

                # Send Telegram + log
                try:
                    await self.telegram.send_signal(signal)
                    self.sheets.log_signal(signal)
                except Exception as e:
                    logger.warning("S16 GAUNTLET delivery failed: %s", e)

            except Exception as e:
                logger.error("S16 GAUNTLET failed for %s: %s", signal.ticker, e, exc_info=True)

        return executed

    def _execute_regime_transition_actions(self, new_regime: RegimeState) -> None:
        """Execute protective actions when a regime transition is detected.

        Section 7: Immediate actions on regime transitions.
        Now logs all actions to regime_transition_actions table and sends Telegram alerts.
        """
        prev = self.regime_classifier.previous_regime
        logger.warning("REGIME TRANSITION: %s → %s — executing actions", prev.value, new_regime.value)

        def _log_regime_action(action: str, tickers: list[str], pnl: float):
            """Persist regime transition action to audit table + Telegram."""
            try:
                from delivery.database import insert_regime_transition_action
                with transaction() as conn:
                    insert_regime_transition_action(conn, {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "old_regime": prev.value,
                        "new_regime": new_regime.value,
                        "action_taken": action,
                        "positions_affected": len(tickers),
                        "tickers_closed": ",".join(tickers),
                        "realized_pnl": pnl,
                    })
            except Exception as e:
                logger.warning("Regime transition audit failed: %s", e)
            try:
                _t = asyncio.get_event_loop().create_task(
                    self.telegram.send_regime_action(
                        prev.value, new_regime.value, len(tickers), pnl
                    )
                )
                self._background_tasks.add(_t)
                _t.add_done_callback(self._background_tasks.discard)
            except Exception as e:
                logger.warning("Regime transition Telegram notification failed: %s", e)

        def _derive_exit_price(pos) -> float:
            """Derive current market price from VirtualPosition fields."""
            if pos.shares and pos.shares > 0 and pos.unrealised_pnl:
                if pos.direction == "LONG":
                    return pos.entry_price + (pos.unrealised_pnl / pos.shares)
                else:
                    return pos.entry_price - (pos.unrealised_pnl / pos.shares)
            return pos.entry_price  # fallback if no P&L data yet

        # SHOCK: Emergency flatten everything
        if new_regime == RegimeState.SHOCK:
            logger.critical("SHOCK REGIME — EMERGENCY FLATTEN ALL POSITIONS")
            closed_tickers = []
            total_pnl = 0.0
            with self.virtual_trader._lock:
                for pos_id in list(self.virtual_trader.open_positions.keys()):
                    pos = self.virtual_trader.open_positions[pos_id]
                    if pos.status == "OPEN":
                        trade = self.virtual_trader.close_position(
                            pos_id, _derive_exit_price(pos), "REGIME_SHOCK_FLATTEN", new_regime.value
                        )
                        if trade:
                            closed_tickers.append(trade.ticker)
                            total_pnl += trade.net_pnl
            # Halt all bots
            for bot in self.bot_router._all_bots:
                bot.halt("SHOCK regime — emergency flatten")
            _log_regime_action("EMERGENCY_FLATTEN_ALL", closed_tickers, total_pnl)
            return

        # RISK_OFF: Flatten everything
        if new_regime == RegimeState.RISK_OFF:
            logger.warning("RISK_OFF REGIME — FLATTENING ALL POSITIONS")
            closed_tickers = []
            total_pnl = 0.0
            with self.virtual_trader._lock:
                for pos_id in list(self.virtual_trader.open_positions.keys()):
                    pos = self.virtual_trader.open_positions[pos_id]
                    if pos.status == "OPEN":
                        trade = self.virtual_trader.close_position(
                            pos_id, _derive_exit_price(pos), "REGIME_RISK_OFF_FLATTEN", new_regime.value
                        )
                        if trade:
                            closed_tickers.append(trade.ticker)
                            total_pnl += trade.net_pnl
            _log_regime_action("RISK_OFF_FLATTEN_ALL", closed_tickers, total_pnl)
            return

        # TRENDING_UP → TRENDING_DOWN: Flatten all longs
        up_regimes = {RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD}
        down_regimes = {RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD}

        if prev in up_regimes and new_regime in down_regimes:
            logger.warning("UP → DOWN transition — flattening all longs")
            closed_tickers = []
            total_pnl = 0.0
            with self.virtual_trader._lock:
                for pos_id in list(self.virtual_trader.open_positions.keys()):
                    pos = self.virtual_trader.open_positions[pos_id]
                    if pos.status == "OPEN" and pos.direction == "LONG":
                        trade = self.virtual_trader.close_position(
                            pos_id, _derive_exit_price(pos), "REGIME_FLIP_FLATTEN_LONG", new_regime.value
                        )
                        if trade:
                            closed_tickers.append(trade.ticker)
                            total_pnl += trade.net_pnl
            _log_regime_action("FLATTEN_LONGS_UP_TO_DOWN", closed_tickers, total_pnl)
            return

        # TRENDING_DOWN → TRENDING_UP: Flatten all shorts + LONG inverse ETPs
        # BUG FIX: Inverse ETPs (QQQS.L, 3USS.L etc.) are LONG positions that profit
        # when market goes DOWN. On a DOWN→UP transition, these LONG inverse positions
        # must also be closed — they'll lose money as market turns bullish.
        # F-03: import from single source of truth (config.universe_constants)
        from config.universe_constants import INVERSE_ETPS_SET as _INVERSE_ETPS_SET
        if prev in down_regimes and new_regime in up_regimes:
            logger.warning("DOWN → UP transition — flattening all shorts + LONG inverse ETPs")
            closed_tickers = []
            total_pnl = 0.0
            with self.virtual_trader._lock:
                for pos_id in list(self.virtual_trader.open_positions.keys()):
                    pos = self.virtual_trader.open_positions[pos_id]
                    if pos.status != "OPEN":
                        continue
                    # Close SHORT positions (original logic)
                    should_close = pos.direction == "SHORT"
                    # ALSO close LONG positions on inverse ETPs — they profit from DOWN
                    if pos.direction == "LONG" and pos.ticker in _INVERSE_ETPS_SET:
                        should_close = True
                        logger.warning("DOWN→UP: closing LONG inverse ETP %s (profits from down, market turning up)", pos.ticker)
                    if should_close:
                        trade = self.virtual_trader.close_position(
                            pos_id, _derive_exit_price(pos), "REGIME_FLIP_FLATTEN_SHORT_AND_INVERSE", new_regime.value
                        )
                        if trade:
                            closed_tickers.append(trade.ticker)
                            total_pnl += trade.net_pnl
            _log_regime_action("FLATTEN_SHORTS_AND_INVERSE_DOWN_TO_UP", closed_tickers, total_pnl)
            return

        # TRENDING_UP → RANGE_BOUND: Tighten all long stops to breakeven
        if prev in up_regimes and new_regime == RegimeState.RANGE_BOUND:
            logger.warning("UP → RANGE transition — tightening all long stops to breakeven")
            tightened = []
            with self.virtual_trader._lock:
                for pos in self.virtual_trader.open_positions.values():
                    if pos.status == "OPEN" and pos.direction == "LONG":
                        pos.current_stop = max(pos.current_stop, pos.entry_price)
                        tightened.append(pos.ticker)
            _log_regime_action("TIGHTEN_STOPS_TO_BREAKEVEN", tightened, 0.0)
            return

    async def run_premarket_intelligence(self, scan_window: str) -> None:
        """Run pre-market intelligence scan and deliver brief to Telegram + DB.

        Called at 09:00 GMT (UK scan) and 09:00 ET (US scan).
        This is NOT a strategy scan — it's intelligence that enriches subsequent scans.
        """
        if self.kill_switch.is_killed():
            logger.warning("Kill switch active — skipping premarket intelligence")
            return

        self._init_feeds()

        if not self._premarket_engine:
            logger.error("PreMarketIntelligenceEngine not initialized — skipping")
            return

        logger.info("=== PRE-MARKET INTELLIGENCE: %s ===", scan_window)

        try:
            brief = await self._premarket_engine.run_scan(scan_window)
            self._latest_premarket_brief = brief

            # Store in database
            try:
                from delivery.database import insert_premarket_brief
                with transaction() as conn:
                    insert_premarket_brief(conn, brief.to_dict())
                logger.info("Pre-market brief stored in database")
            except Exception as e:
                logger.error("Failed to store premarket brief: %s", e)

            # Deliver to Telegram
            try:
                msg = brief.to_telegram()
                await self.telegram.send_alert(msg)
                logger.info("Pre-market brief sent to Telegram (%d chars)", len(msg))
            except Exception as e:
                logger.error("Failed to send premarket brief to Telegram: %s", e)

            logger.info(
                "=== PRE-MARKET INTEL COMPLETE: bias=%s conf=%.0f%% etps=%d stocks=%d setups=%d ===",
                brief.market_bias, brief.bias_confidence * 100,
                len(brief.etp_briefs), len(brief.stock_alerts),
                len(brief.high_conviction_setups),
            )

        except Exception as e:
            logger.error("Pre-market intelligence scan failed: %s", e)

    @staticmethod
    def _delivery_task_done(task: asyncio.Task) -> None:
        """Callback for delivery tasks — log any exceptions."""
        if task.cancelled():
            logger.warning("Delivery task %s was cancelled", task.get_name())
        elif exc := task.exception():
            logger.error("Delivery task %s failed: %s", task.get_name(), exc)

    def _build_market_context(self, indicators: dict[str, IndicatorSnapshot]) -> MarketContext:
        """Build the complete market context from available data."""
        ctx = MarketContext(timestamp=datetime.now(timezone.utc))

        # === FETCH market structure data FIRST so VIX is available for regime classification ===
        if self._market_structure:
            try:
                ms_data = self._market_structure.get_full_context()
                ctx.gex_value = ms_data.get("gex_value", 0)
                ctx.dix_value = ms_data.get("dix_value", 0)
                ctx.vix = ms_data.get("vix", 0)
                ctx.vix3m = ms_data.get("vix3m", 0)
                ctx.internals_composite = ms_data.get("composite_score", 0)
                ctx.internals_confidence_adj = ms_data.get("internals_confidence_adj", 0)
            except Exception as e:
                logger.error("Market structure fetch failed: %s", e)

        # Get QQQ and SPY indicators for regime classification
        qqq = indicators.get("QQQ", IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker="QQQ"))
        spy = indicators.get("SPY", IndicatorSnapshot(timestamp=datetime.now(timezone.utc), ticker="SPY"))

        # Compute slope_per_bar from QQQ 5-min close prices via linear regression
        # Slope = average % change per bar over last 12 bars (1 hour of 5-min data)
        slope_per_bar = 0.0
        try:
            if self._data_feeds:
                qqq_bars = self._data_feeds.get_intraday_bars("QQQ", interval="5m")
                if not qqq_bars.empty and len(qqq_bars) >= 6:
                    closes = qqq_bars["Close"].tail(12).values
                    n = len(closes)
                    if n >= 6 and closes[0] > 0:
                        # Simple linear regression slope: Σ[(x-x̄)(y-ȳ)] / Σ[(x-x̄)²]
                        # where y = fractional price change from first bar
                        x = list(range(n))
                        y = [(c / closes[0]) - 1.0 for c in closes]
                        x_mean = sum(x) / n
                        y_mean = sum(y) / n
                        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
                        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
                        if denominator > 0:
                            slope_per_bar = numerator / denominator  # fractional change per bar
        except Exception as e:
            logger.warning("slope_per_bar computation failed (using 0.0): %s", e)

        # Classify regime (now uses real VIX and real slope from price series)
        regime = self.regime_classifier.classify(
            qqq_price=qqq.price,
            qqq_vwap=qqq.vwap,
            spy_price=spy.price,
            spy_vwap=spy.vwap,
            ema9=qqq.ema9,
            ema20=qqq.ema20,
            ema50=qqq.ema50,
            slope_per_bar=slope_per_bar,
            vix=ctx.vix,
        )
        ctx.regime = regime
        ctx.regime_confidence = 0.0
        ctx.regime_duration_bars = self.regime_classifier._regime_duration_bars

        # Wave 2 — HMM regime overlay (Nystrup et al. 2017)
        # If the HMM has a confirmed regime, it overrides the threshold-based
        # classifier. The HMM refits hourly (cached) and uses a 3-day
        # confirmation lag to suppress false transitions.
        # IMPORTANT: SHOCK from threshold classifier is NEVER overridden
        # (the threshold classifier is the only one with intraday VIX data).
        if (
            hasattr(self, "hmm_regime")
            and self.hmm_regime is not None
            and regime != RegimeState.SHOCK
        ):
            try:
                hmm_regime = self.hmm_regime.update()
                if hmm_regime is not None and hmm_regime != regime:
                    logger.debug(
                        "HMM regime override: %s → %s",
                        regime.value, hmm_regime.value,
                    )
                    ctx.regime = hmm_regime
            except Exception as _hmm_err:
                logger.warning("HMM regime overlay failed (non-blocking): %s", _hmm_err)

        # Persist regime to database so Command Center dashboard can read it
        try:
            from delivery.database import insert_regime_history
            # Use ctx.regime (may be HMM-overridden) for database persistence
            _final_regime = ctx.regime
            regime_str = _final_regime.value if hasattr(_final_regime, "value") else str(_final_regime)
            with transaction() as conn:
                insert_regime_history(conn, {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "state": regime_str,
                    "confidence": 0.0,
                    "qqq_vs_vwap": round(qqq.price - (qqq.vwap or 0), 4),
                    "spy_vs_vwap": round(spy.price - (spy.vwap or 0), 4),
                    "ema_alignment": f"{qqq.ema9:.2f}/{qqq.ema20:.2f}/{qqq.ema50:.2f}" if qqq.ema9 else "",
                    "vix": ctx.vix or 0.0,
                    "gex": str(ctx.gex_value) if ctx.gex_value else "",
                    "dix": ctx.dix_value or 0.0,
                    "internals_composite": str(ctx.internals_composite or ""),
                    "trigger_reason": f"slope={slope_per_bar:.6f}",
                })
            logger.info("Regime persisted to DB: %s (VIX=%.1f)", regime_str, ctx.vix or 0.0)
        except Exception as e:
            logger.warning("Regime history persist failed: %s", e)

        # Time window — proper ET conversion using zoneinfo (handles EDT/EST automatically)
        from core.clock import now_et as _now_et_fn
        now_et = _now_et_fn()
        ctx.time_window = self.time_engine.get_current_window(now_et.hour, now_et.minute)

        # Calendar
        if self._calendar_feed:
            try:
                ctx.calendar_risk = self._calendar_feed.get_calendar_risk()
                ctx.fomc_today = self._calendar_feed.is_fomc_day()
                ctx.cpi_nfp_today = self._calendar_feed.is_cpi_nfp_day()
            except Exception as e:
                logger.error("Calendar feed error: %s", e)

        # Pre-market intelligence — attach latest brief so strategies can see it
        brief = None
        if self._premarket_engine:
            brief = getattr(self._premarket_engine, "latest_brief", None)
        if not brief:
            brief = self._latest_premarket_brief
        if brief:
            ctx.premarket_brief = brief

        return ctx

    # ─── V8.0 STATE MANAGER SCHEDULED HANDLERS ──────────────────────────────

    async def _freeze_config_ulysses(self) -> None:
        """Ulysses Lock: freeze config at 07:55 UK, 5 min before LSE open.

        SHA256 hash is stored in Redis. During market hours, verify_config_hash()
        checks that the in-memory config hasn't mutated. Mismatch → halt.
        """
        if not self.state_manager:
            return
        try:
            config_dict = cfg.load_config()
            config_hash = await self.state_manager.freeze_config(config_dict)
            self._frozen_config_hash = config_hash
            logger.info("ULYSSES_LOCK: Config frozen at 07:55 UK — hash=%s", config_hash[:16])
        except Exception as e:
            logger.error("ULYSSES_LOCK: freeze_config failed: %s", e)

    async def _state_manager_reconcile_nightly(self) -> None:
        """Nightly StateManager reconciliation: Redis ↔ SQLite + reset daily P&L."""
        if not self.state_manager:
            return
        try:
            discrepancies = await self.state_manager.reconcile_with_sqlite()
            if discrepancies:
                for d in discrepancies:
                    logger.warning("NIGHTLY_RECONCILIATION: %s", d)
            else:
                logger.info("NIGHTLY_RECONCILIATION: Redis ↔ SQLite OK")
            await self.state_manager.reset_daily_pnl()
            logger.info("NIGHTLY: Daily P&L reset in Redis")
        except Exception as e:
            logger.error("NIGHTLY_RECONCILIATION: failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # H-08: PRE-MARKET IBKR GO/NO-GO
    # ─────────────────────────────────────────────────────────────────────────

    async def _h08_ibkr_premarket_check(self) -> None:
        """H-08: 07:50 UK — Check IBKR connection before market open.

        If ib.isConnected() is False, fire Telegram alert:
        "IBKR DISCONNECTED — 2FA REQUIRED"

        This gives the operator 10 minutes to complete 2FA before the
        08:00 hard halt kicks in.
        """
        try:
            from data_hub.sources.ibkr_source import IBKRSource
            ibkr_connected = IBKRSource.IS_AVAILABLE
        except Exception:
            ibkr_connected = False

        if not ibkr_connected:
            alert_msg = (
                "H-08 IBKR DISCONNECTED — 2FA REQUIRED\n"
                "Time: 07:50 UK (10 min to LSE open)\n"
                "Action: Complete IB Gateway 2FA NOW\n"
                "If not connected by 08:00: ALL TRADING HALTED\n"
                "No yfinance gap trading allowed."
            )
            logger.critical("[H-08] %s", alert_msg)
            try:
                await self.telegram.send_alert(alert_msg)
            except Exception as tg_err:
                logger.error("[H-08] Telegram alert failed: %s", tg_err)
        else:
            logger.info("[H-08] 07:50 UK pre-market check: IBKR connected — GO")

    async def _h08_ibkr_premarket_halt(self) -> None:
        """H-08: 08:00 UK — Hard halt if IBKR still disconnected.

        If still not connected by 08:00 UK:
        1. Set nzt:halt_reason=IBKR_DISCONNECTED in Redis
        2. HALT all trading
        3. No yfinance gap trading allowed
        """
        try:
            from data_hub.sources.ibkr_source import IBKRSource
            ibkr_connected = IBKRSource.IS_AVAILABLE
        except Exception:
            ibkr_connected = False

        if not ibkr_connected:
            halt_msg = (
                "H-08 TRADING HALTED — IBKR DISCONNECTED AT 08:00 UK\n"
                "Halt reason: IBKR_DISCONNECTED\n"
                "No yfinance gap trading allowed.\n"
                "Manual action required: complete 2FA and restart."
            )
            logger.critical("[H-08] %s", halt_msg)

            # Set halt reason in Redis
            try:
                import redis as _redis_mod
                _r = _redis_mod.Redis(
                    host='redis', port=6379,
                    password='nzt48redis', decode_responses=True,
                    socket_connect_timeout=5, socket_timeout=5,
                )
                _r.set("nzt:halt_reason", "IBKR_DISCONNECTED")
                logger.info("[H-08] Redis nzt:halt_reason=IBKR_DISCONNECTED set")
            except Exception as redis_err:
                logger.error("[H-08] Redis halt_reason set failed: %s", redis_err)

            # Also halt via virtual trader kill switch
            if hasattr(self, 'virtual_trader'):
                self.virtual_trader._trading_halted = True
                logger.info("[H-08] VirtualTrader._trading_halted = True")

            try:
                await self.telegram.send_alert(halt_msg)
            except Exception as tg_err:
                logger.error("[H-08] Telegram halt alert failed: %s", tg_err)
        else:
            logger.info("[H-08] 08:00 UK pre-market check: IBKR connected — TRADING ALLOWED")

            # Clear any previous halt reason
            try:
                import redis as _redis_mod
                _r = _redis_mod.Redis(
                    host='redis', port=6379,
                    password='nzt48redis', decode_responses=True,
                    socket_connect_timeout=5, socket_timeout=5,
                )
                existing = _r.get("nzt:halt_reason")
                if existing == "IBKR_DISCONNECTED":
                    _r.delete("nzt:halt_reason")
                    logger.info("[H-08] Cleared previous IBKR_DISCONNECTED halt reason")
            except Exception:
                pass

    def setup_scheduler(self) -> None:
        """Set up APScheduler with the automated scan schedule from Section IV.

        UK Times:
        06:00 — Pre-market: S1, S3, S5, S6, S7, S10 + context
        12:00 — Midday: S7, S9, S11
        14:30 — US Open: S2, S4, S8, S11, S14
        16:00 — Mid-session: S2, S3, S4, S9
        19:00 — Rebalance: S12 (THE KEY SIGNAL)
        20:30 — Late Session: S2, S3, S13
        Sunday 20:00 — Weekly: S1, S7, S13
        """
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            scheduler = AsyncIOScheduler(timezone="Europe/London")

            # Pre-market scan (06:00 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=6, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S1", "S3", "S5", "S6", "S7", "S10"]},
                id="pre_market",
                name="Pre-Market Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # H-08: Pre-Market IBKR Go/No-Go — 07:50 UK every trading day (Mon-Fri)
            scheduler.add_job(
                self._h08_ibkr_premarket_check,
                CronTrigger(
                    hour=7, minute=50, day_of_week="mon-fri",
                    timezone="Europe/London",
                ),
                id="h08_ibkr_premarket_check",
                name="H-08 IBKR Pre-Market Go/No-Go (07:50 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # H-08: Pre-Market IBKR HALT — 08:00 UK every trading day (Mon-Fri)
            scheduler.add_job(
                self._h08_ibkr_premarket_halt,
                CronTrigger(
                    hour=8, minute=0, day_of_week="mon-fri",
                    timezone="Europe/London",
                ),
                id="h08_ibkr_premarket_halt",
                name="H-08 IBKR Pre-Market HALT Check (08:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # V8.0 Ulysses Lock — freeze config 5 min before LSE open (07:55 UK)
            scheduler.add_job(
                self._freeze_config_ulysses,
                CronTrigger(hour=7, minute=55, timezone="Europe/London"),
                id="ulysses_lock_freeze",
                name="Ulysses Lock Config Freeze (07:55 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # LSE OPEN scan (08:00 UK) — KEY: ISA ETP opportunities at market open
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=8, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S1", "S2", "S6", "S7", "S13", "S14"]},
                id="lse_open",
                name="LSE Open Scan (08:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Midday scan (12:00 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=12, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S7", "S9", "S11"]},
                id="midday",
                name="Midday Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # US Open scan (14:30 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=14, minute=30, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S2", "S4", "S8", "S11", "S14"]},
                id="us_open",
                name="US Open Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Mid-session scan (16:00 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=16, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S2", "S3", "S4", "S9"]},
                id="mid_session",
                name="Mid-Session Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Rebalance scan (19:00 UK) — THE KEY SIGNAL
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=19, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S12"]},
                id="rebalance",
                name="Rebalance Flow Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Late session scan (20:30 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=20, minute=30, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S2", "S3", "S13"]},
                id="late_session",
                name="Late Session Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Weekly scan (Sunday 20:00 UK)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(day_of_week="sun", hour=20, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S1", "S7", "S13"]},
                id="weekly",
                name="Weekly Scan",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # NEW: Pre-market movers scan (09:00 UK = 04:00 ET)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=9, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S5", "S10", "S11", "S14"]},
                id="premarket_movers",
                name="Pre-Market Movers Scan (09:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # NEW: Mid-session setups (15:00 UK = 10:00 ET)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=15, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S1", "S2", "S3", "S13"]},
                id="midsession_setups",
                name="Mid-Session Setups (15:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # NEW: Afternoon push entries (17:00 UK = 12:00 ET)
            scheduler.add_job(
                self.run_scan,
                CronTrigger(hour=17, minute=0, timezone="Europe/London"),
                kwargs={"strategy_ids": ["S2", "S3", "S4", "S13"]},
                id="afternoon_push",
                name="Afternoon Push (17:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Pre-LSE brief (07:30 UK) — 30min before LSE open, send overnight summary
            scheduler.add_job(
                self.run_premarket_intelligence,
                CronTrigger(hour=7, minute=30, timezone="Europe/London"),
                kwargs={"scan_window": "PRE_LSE_0730"},
                id="pre_lse_brief",
                name="Pre-LSE Brief (07:30 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Pre-market intelligence: UK 09:00 GMT (1hr after LSE open)
            scheduler.add_job(
                self.run_premarket_intelligence,
                CronTrigger(hour=9, minute=0, timezone="Europe/London"),
                kwargs={"scan_window": "UK_0900"},
                id="premarket_intel_uk",
                name="Pre-Market Intelligence (UK 09:00)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Pre-market intelligence: US 09:00 ET (30min before US open)
            scheduler.add_job(
                self.run_premarket_intelligence,
                CronTrigger(hour=9, minute=0, timezone="America/New_York"),
                kwargs={"scan_window": "US_0900"},
                id="premarket_intel_us",
                name="Pre-Market Intelligence (US 09:00 ET)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # Position reconciler (every 10 seconds during market hours)
            scheduler.add_job(
                self._reconcile_positions,
                "interval",
                seconds=10,  # F-10: reconciliation every 10s (was 30s)
                id="reconciler",
                name="Position Reconciler",
                max_instances=1,
                coalesce=True,
            )

            # Nightly Intelligence Cycle — THE BRAIN (21:30 UK = after US market close)
            scheduler.add_job(
                self._run_nightly_intelligence,
                CronTrigger(hour=21, minute=30, timezone="Europe/London"),
                id="nightly_intelligence",
                name="Nightly Intelligence Cycle",
                max_instances=1,
                coalesce=True,
            )

            # Go/No-Go Scorecard — daily check (21:45 UK)
            scheduler.add_job(
                self._run_go_nogo_check,
                CronTrigger(hour=21, minute=45, timezone="Europe/London"),
                id="go_nogo_check",
                name="Go/No-Go Scorecard Check",
                max_instances=1,
                coalesce=True,
            )

            # ── V3.2 NEW SCHEDULER JOBS ──────────────────────────────────────────
            # Performance Relegation check — 23:30 UTC nightly (W2)
            # Scores all tickers for A/B team; emits Telegram demotion votes
            scheduler.add_job(
                self._run_performance_relegation,
                CronTrigger(hour=23, minute=30, timezone="UTC"),
                id="performance_relegation",
                name="Performance Relegation Check (23:30 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Net Expectancy refresh — 23:00 UTC nightly (W9a)
            # Recomputes E = (WR × AvgWin) - (1-WR × AvgLoss) - CostDrag for all tickers
            scheduler.add_job(
                self._refresh_net_expectancy,
                CronTrigger(hour=23, minute=0, timezone="UTC"),
                id="net_expectancy_refresh",
                name="Net Expectancy Refresh (23:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Earnings Calendar check — 04:00 UTC daily (W4 earnings_calendar.py)
            # Auto-fetches upcoming earnings announcements for all ISA tickers + underlying
            scheduler.add_job(
                self._refresh_earnings_calendar,
                CronTrigger(hour=4, minute=0, timezone="UTC"),
                id="earnings_calendar_refresh",
                name="Earnings Calendar Refresh (04:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Cross-Asset Macro update — 07:00 UTC daily (W4d, before LSE open)
            # DXY, VIX term structure, LQD/IEF credit spread proxy
            scheduler.add_job(
                self._refresh_cross_asset_macro,
                CronTrigger(hour=7, minute=0, timezone="UTC"),
                id="cross_asset_macro_refresh",
                name="Cross-Asset Macro Update (07:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Learning state save — every hour
            scheduler.add_job(
                self._save_learning_state,
                "interval",
                hours=1,
                id="learning_save",
                name="Learning State Persistence",
                max_instances=1,
                coalesce=True,
            )

            # Intraday equity snapshot — every hour for drawdown curve tracking
            scheduler.add_job(
                self._record_equity_snapshot_intraday,
                "interval",
                hours=1,
                id="equity_intraday",
                name="Intraday Equity Snapshot",
                max_instances=1,
                coalesce=True,
            )

            # Missed trade journal — update outcomes every 30 min
            scheduler.add_job(
                self._update_missed_trade_outcomes,
                "interval",
                minutes=30,
                id="missed_trade_update",
                name="Missed Trade Outcome Tracker",
                max_instances=1,
                coalesce=True,
            )

            # Pre-close audit (15:45 ET — 15 min before close)
            scheduler.add_job(
                self._run_pre_close_audit,
                CronTrigger(hour=15, minute=45, timezone="America/New_York"),
                id="pre_close_audit",
                name="Pre-Close Audit",
                max_instances=1,
                coalesce=True,
            )

            # Daily reset for data validator + session manager (04:00 ET — start of new day)
            scheduler.add_job(
                self._run_daily_reset,
                CronTrigger(hour=4, minute=0, timezone="America/New_York"),
                id="daily_reset",
                name="Daily Feed & Session Reset",
                max_instances=1,
                coalesce=True,
            )

            # EOD Force Close — close ALL non-SWING positions at market close (16:00 ET)
            # User specified: latest cash out = end of the trading day
            scheduler.add_job(
                self._force_close_eod,
                CronTrigger(hour=16, minute=0, timezone="America/New_York"),
                id="eod_force_close",
                name="EOD Force Close (16:00 ET)",
                max_instances=1,
                coalesce=True,
            )

            # A-12: 5x ETP Hard Kill — dedicated scheduler at 15:30 UK sharp
            # Cannot be delayed by scan loop stalls. 3% overnight gap on 5x = 15% loss.
            scheduler.add_job(
                self._force_close_5x_etps,
                CronTrigger(hour=15, minute=30, timezone="Europe/London"),
                id="5x_hard_kill_1530",
                name="A-12: 5x ETP Hard Kill (15:30 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=120,
            )

            # LSE Force Close — close ALL .L positions at 15:50 UK
            # The existing EOD close fires at 16:00 ET = 21:00 UK — 5 hours AFTER LSE closes!
            # This ensures ISA positions are closed before LSE closing auction mechanics.
            scheduler.add_job(
                self._force_close_lse,
                CronTrigger(hour=15, minute=50, timezone="Europe/London"),
                id="lse_force_close",
                name="LSE Force Close (15:50 UK)",
                max_instances=1,
                coalesce=True,
            )

            # ── B-TEAM EVALUATION — Every trading day at 16:00 UK (post-LSE close) ──
            scheduler.add_job(
                self._evaluate_b_team,
                CronTrigger(hour=16, minute=0, day_of_week="mon-fri", timezone="Europe/London"),
                id="b_team_eval",
                name="B-Team League Evaluation (16:00 UK)",
                max_instances=1,
                coalesce=True,
            )

            # ── WEEKLY LEAGUE SUMMARY — Sunday 18:00 UK (E3) ──
            scheduler.add_job(
                self._send_weekly_league_summary,
                CronTrigger(day_of_week="sun", hour=18, minute=0, timezone="Europe/London"),
                id="weekly_league_summary",
                name="Weekly B-Team League Summary (Sun 18:00 UK)",
                max_instances=1,
                coalesce=True,
            )

            # ── SECTOR MONITORING — 5-minute cycle during LSE hours ──
            scheduler.add_job(
                self._run_sector_monitor,
                CronTrigger(minute="*/5", hour="9-15", day_of_week="mon-fri", timezone="Europe/London"),
                id="sector_monitor",
                name="Sector Rotation Monitor (5min LSE hours)",
                max_instances=1,
                coalesce=True,
            )

            # ── V8.0 PDF TRIPLE REPORT SCHEDULE ─────────────────────────────
            # PDF 1: PRE-LSE Brief  — 07:00 UK (LSE opens 08:00)
            #   Focus: overnight moves, LSE universe setup, today's ISA candidates
            scheduler.add_job(
                self._generate_v2_pdf1,
                CronTrigger(hour=7, minute=0, timezone="Europe/London"),
                id="pdf_v2_pre_lse",
                name="V2 PDF1 Pre-LSE Brief (07:00 UK)",
                kwargs={"session": "PRE_LSE"},
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )
            # PDF 2: PRE-NYSE Brief — 13:30 UK (NYSE opens 14:30 UK / 09:30 ET)
            #   Focus: LSE morning recap, US pre-market signals, cross-session momentum
            scheduler.add_job(
                self._generate_v2_pdf2,
                CronTrigger(hour=13, minute=30, timezone="Europe/London"),
                id="pdf_v2_pre_nyse",
                name="V2 PDF2 Pre-NYSE Brief (13:30 UK)",
                kwargs={"session": "PRE_NYSE"},
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )
            # PDF 3: EOD Review — 22:00 UK (NYSE closes 21:00 UK / 16:00 ET)
            #   Focus: full dual-session review, S15 autopsy, tomorrow setup
            scheduler.add_job(
                self._generate_v2_pdf3,
                CronTrigger(hour=22, minute=0, timezone="Europe/London"),
                id="pdf_v2_eod_review",
                name="V2 PDF3 EOD Review (22:00 UK)",
                kwargs={"session": "EOD_INSTITUTIONAL"},
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )
            # MEGA PDF — 22:30 UK (30 min after EOD Review — uses EOD artifacts)
            #   Full 40-80 page project analysis: architecture, signals, calibration, roadmap
            scheduler.add_job(
                self._generate_v2_mega_pdf,
                CronTrigger(hour=22, minute=30, timezone="Europe/London"),
                id="pdf_v2_mega",
                name="V2 Mega PDF Full Analysis (22:30 UK)",
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )

            # ── V2 NEW PDF WINDOWS (110/100 Institutional Upgrade) ──────────
            # OVERNIGHT RISK BRIEF — 06:30 UK (before LSE registry refresh)
            #   Focus: overnight risk events, Asia/US afterhours, portfolio exposure
            scheduler.add_job(
                self._generate_overnight_risk_pdf,
                CronTrigger(hour=6, minute=30, timezone="Europe/London"),
                id="pdf_overnight_risk",
                name="Overnight Risk Brief (06:30 UK)",
                max_instances=1,
                misfire_grace_time=300,
                coalesce=True,
            )
            # MID-SESSION RISK — 16:40 UK (LSE close + 10 min, NYSE midday)
            #   Focus: LSE session recap, open exposure, regime shifts, NYSE midday
            scheduler.add_job(
                self._generate_mid_session_pdf,
                CronTrigger(hour=16, minute=40, timezone="Europe/London"),
                id="pdf_mid_session_risk",
                name="Mid-Session Risk Brief (16:40 UK)",
                max_instances=1,
                misfire_grace_time=300,
                coalesce=True,
            )
            # MASTER SPEC — 00:00 UK (midnight, full system specification)
            #   Focus: complete system state, architecture, all parameters, full audit
            scheduler.add_job(
                self._generate_master_spec_pdf,
                CronTrigger(hour=0, minute=0, timezone="Europe/London"),
                id="pdf_master_spec",
                name="Master Spec Document (00:00 UK)",
                max_instances=1,
                misfire_grace_time=300,
                coalesce=True,
            )

            # ── V2 REGISTRY REFRESH (06:30 UK daily — before PDF1 fires) ────
            scheduler.add_job(
                self._refresh_lse_registry,
                CronTrigger(hour=6, minute=30, timezone="Europe/London"),
                id="lse_registry_refresh",
                name="LSE Registry Daily Refresh (06:30 UK)",
                max_instances=1,
                coalesce=True,
            )

            # ── V2 SECTOR ROTATION SCAN (every 60 seconds — live data) ──────
            scheduler.add_job(
                self._run_sector_rotation_scan,
                "interval",
                seconds=60,
                id="sector_rotation_scan",
                name="Sector Rotation Scan (60s)",
                max_instances=1,
                coalesce=True,
            )

            # 24/7 continuous scan — runs every 60 seconds, never stops
            # All strategies, all tickers, around the clock
            scheduler.add_job(
                self.run_scan,
                "interval",
                seconds=60,
                id="continuous_24_7",
                name="24/7 Continuous Scan (60s)",
                max_instances=1,
                misfire_grace_time=30,
                coalesce=True,
            )

            # ── OUTCOME RESOLVER (every 15 min — resolves PENDING signals) ────
            scheduler.add_job(
                self._resolve_signal_outcomes,
                "interval",
                minutes=15,
                id="outcome_resolver",
                name="Signal Outcome Resolver (15min)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # ── EOD OUTCOME BATCH RESOLUTION (22:00 UTC daily) ────
            scheduler.add_job(
                self._resolve_outcomes_eod,
                CronTrigger(hour=22, minute=0, timezone="UTC"),
                id="outcome_resolution_eod",
                name="EOD Outcome Batch Resolution (22:00 UTC)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # ── EDGE LEDGER REBUILD (nightly at 22:30 UTC) ────
            scheduler.add_job(
                self._rebuild_edge_ledger,
                CronTrigger(hour=22, minute=30, timezone="UTC"),
                id="edge_ledger_rebuild",
                name="Edge Ledger Nightly Rebuild (22:30 UTC)",
                max_instances=1,
            )

            # ── SIGNAL ENGINE PERIODIC RUN (every 15 min — institutional engine) ──
            # Ensures War Room always has fresh plays, not just 3x/day from PDF jobs
            scheduler.add_job(
                self._run_signal_engine_periodic,
                "interval",
                minutes=15,
                id="signal_engine_periodic",
                name="Signal Engine Periodic (15min)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )

            # ── SYSTEM HEALTH MONITOR (every 5 min — memory / degradation alerts) ──
            scheduler.add_job(
                self._check_system_health,
                "interval",
                minutes=5,
                id="system_health_check",
                name="System Health Check (5min)",
                max_instances=1,
            )

            # ── V3.0 NIGHTLY JOBS ─────────────────────────────────────────────

            # Task 1.7: Nightly earnings run-up score compute (23:00 UTC daily)
            # Scores all tickers for pre-earnings run-up so RC-07b can gate next day
            scheduler.add_job(
                self._compute_earnings_runup_scores,
                CronTrigger(hour=23, minute=0, timezone="UTC"),
                id="earnings_runup_nightly",
                name="Earnings Run-Up Score Compute (23:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Task 2.7: Short squeeze data refresh (07:00 UTC daily — FINRA T+1 available)
            scheduler.add_job(
                self._refresh_short_squeeze_data,
                CronTrigger(hour=7, minute=0, timezone="UTC"),
                id="short_squeeze_refresh",
                name="Short Squeeze Data Refresh (07:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # Task 3.3: Auto-improvement loop (Sunday 21:00 UTC weekly)
            scheduler.add_job(
                self._run_weekly_auto_improvement,
                CronTrigger(day_of_week="sun", hour=21, minute=0, timezone="UTC"),
                id="weekly_auto_improvement",
                name="Weekly Auto-Improvement Loop (Sunday 21:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # AI Research Engine: Monthly self-assessment (1st of each month 22:00 UTC)
            # Gelman et al. (2013): Bayesian periodic review — monthly cadence matches
            # sufficient trade accumulation for statistically meaningful assessment.
            if self.ai_research:
                scheduler.add_job(
                    self._run_monthly_ai_self_assessment,
                    CronTrigger(day=1, hour=22, minute=0, timezone="UTC"),
                    id="monthly_ai_self_assessment",
                    name="Monthly AI Self-Assessment (1st of month 22:00 UTC)",
                    max_instances=1,
                    coalesce=True,
                )

            # Task 3.5: Friday indicator ranking Telegram report (21:00 UTC every Friday)
            scheduler.add_job(
                self._send_indicator_ranking_report,
                CronTrigger(day_of_week="fri", hour=21, minute=0, timezone="UTC"),
                id="friday_indicator_ranking",
                name="Friday Indicator Ranking Report (21:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # ── V3.0 PORTFOLIO HEAT RESET (midnight UTC — new trading day) ────
            scheduler.add_job(
                self._reset_portfolio_heat,
                CronTrigger(hour=0, minute=1, timezone="UTC"),
                id="portfolio_heat_reset",
                name="Portfolio Heat Daily Reset (00:01 UTC)",
                max_instances=1,
            )

            # ── V3.2 MISSING SCHEDULER JOBS ────────────────────────────────────

            # W3: Data retention — rotate outcomes.jsonl if > 2000 lines (04:00 UTC daily)
            scheduler.add_job(
                self._rotate_outcomes_data,
                CronTrigger(hour=4, minute=0, timezone="UTC"),
                id="outcomes_rotation",
                name="Outcomes Data Rotation (04:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # W3: Model backup — backup ml_model before any retrain (04:05 UTC daily)
            scheduler.add_job(
                self._backup_ml_model,
                CronTrigger(hour=4, minute=5, timezone="UTC"),
                id="model_backup_daily",
                name="ML Model Daily Backup (04:05 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # W6 Bug fix: sector_momentum daily update (07:00 UTC — before LSE open)
            # Previously only ran in weekly Sunday cycle — now runs every trading day
            scheduler.add_job(
                self._refresh_sector_momentum_daily,
                CronTrigger(hour=7, minute=0, timezone="UTC"),
                id="sector_momentum_daily",
                name="Sector Momentum Daily Refresh (07:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # W6 Bug fix: SOFR-OIS proxy update (07:30 UTC daily)
            # Uses SGOV/BIL ratio as free SOFR proxy (T-bill ETF spread)
            scheduler.add_job(
                self._refresh_sofr_proxy,
                CronTrigger(hour=7, minute=30, timezone="UTC"),
                id="sofr_proxy_refresh",
                name="SOFR-OIS Proxy Refresh (07:30 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # W1: Intraday momentum — record open prices (09:00 UK)
            scheduler.add_job(
                self._record_intraday_opens,
                CronTrigger(hour=9, minute=0, timezone="Europe/London"),
                id="intraday_record_open",
                name="Intraday Momentum Record Open (09:00 UK)",
                max_instances=1,
                coalesce=True,
            )

            # W1: Intraday momentum — record half-hour prices (09:30 UK)
            scheduler.add_job(
                self._record_intraday_halfhour,
                CronTrigger(hour=9, minute=30, timezone="Europe/London"),
                id="intraday_record_halfhour",
                name="Intraday Momentum Record Half-Hour (09:30 UK)",
                max_instances=1,
                coalesce=True,
            )

            # W1: Intraday momentum — generate EOD signal (20:00 UTC = 15:00 ET)
            scheduler.add_job(
                self._run_intraday_eod_signal,
                CronTrigger(hour=20, minute=0, timezone="UTC"),
                id="intraday_eod_signal",
                name="Intraday Momentum EOD Signal (20:00 UTC)",
                max_instances=1,
                coalesce=True,
            )

            # ── V9.5 AUTONOMOUS ML DAEMON (18:00 UTC daily) ────────────────
            # Ouroboros: retrain → recalibrate → reap → reload
            if self.ml_daemon:
                scheduler.add_job(
                    self._run_autonomous_ml_daemon,
                    CronTrigger(hour=18, minute=0, timezone="UTC"),
                    id="v95_ml_daemon",
                    name="V9.5 Autonomous ML Daemon (18:00 UTC)",
                    max_instances=1,
                    coalesce=True,
                )

            # ── V9.5 CUSUM NIGHTLY CHECK (21:00 UTC daily) ─────────────────
            # Separate from daemon — checks quarantine expiry on its own schedule
            if self.cusum_reaper:
                scheduler.add_job(
                    self._run_cusum_nightly,
                    CronTrigger(hour=21, minute=0, timezone="UTC"),
                    id="v95_cusum_nightly",
                    name="V9.5 CUSUM Nightly Check (21:00 UTC)",
                    max_instances=1,
                    coalesce=True,
                )

            # ── H-10: CLOUDWATCH METRICS (every 60s) ────────────────────
            if self.cloudwatch_emitter and self.cloudwatch_emitter.available:
                scheduler.add_job(
                    self._emit_cloudwatch_metrics,
                    "interval",
                    seconds=60,
                    id="cloudwatch_metrics",
                    name="CloudWatch Metrics (60s)",
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=30,
                )

            # ── H-14: WEEKLY PERFORMANCE REPORT (Sunday 20:00 UK) ────────
            scheduler.add_job(
                self._generate_weekly_report,
                CronTrigger(day_of_week="sun", hour=20, minute=0, timezone="Europe/London"),
                id="weekly_performance_report",
                name="Weekly Performance Report (Sun 20:00 UK)",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=300,
            )

            # === UNIVERSE REFRESH INTEGRATION ===
            # Dynamically schedule universe refreshes for each phase
            if _UNIVERSE_REFRESH_AVAILABLE:
                try:
                    self.universe_refresh_integration = setup_universe_refresh_integration(
                        scheduler,
                        artifacts_dir=Path("artifacts"),
                        universe_scan_fn=self._scan_universe_async,
                    )
                    logger.info(
                        "Universe Refresh Integration initialized with %d scheduled jobs",
                        len(self.universe_refresh_integration.scheduled_jobs),
                    )
                except Exception as _uri_err:
                    logger.warning("Universe Refresh Integration failed to initialize: %s", _uri_err)
                    self.universe_refresh_integration = None
            else:
                self.universe_refresh_integration = None

            scheduler.start()
            logger.info(
                "APScheduler V3.0 started: 11 cron scans + 24/7 60s continuous + "
                "7 PDFs [Overnight 06:30 | Pre-LSE 07:00 | Pre-NYSE 13:30 | "
                "Mid-Session 16:40 | EOD 22:00 | MEGA 22:30 | Master 00:00 UK] + "
                "LSE registry 06:30 + sector rotation 60s + go/no-go + learning save + "
                "missed trade tracker + reconciler + pre-close audit + daily reset + "
                "outcome resolver 15min + EOD outcome batch 22:00 UTC + "
                "V3 earnings runup 23:00 UTC + short squeeze 07:00 UTC + "
                "auto-improvement Sun 21:00 UTC + indicator ranking Fri 21:00 UTC + "
                "monthly AI self-assessment 1st/month 22:00 UTC | "
                "UK ISA PRIMARY MODE | RC-07b EARNINGS FADE GATE ACTIVE"
            )

            # === WIRING VALIDATOR: startup dead-code check ===
            # Feitelson (2015): automated structural validation prevents dead module accumulation.
            # Runs every startup — logs unwired modules, sends Telegram alert if any found.
            if _WIRING_VALIDATOR_AVAILABLE:
                try:
                    wiring_result = validate_wiring("main.py", fail_hard=False)
                    if not wiring_result["pass"]:
                        # Non-blocking: log warning and send Telegram alert
                        _wiring_msg = _wiring_telegram_summary(wiring_result)
                        logger.warning("WIRING_VALIDATOR: Dead code detected at startup: %s",
                                       wiring_result["unwired"])
                        # Schedule async Telegram send (scheduler not yet in event loop here)
                        try:
                            _t = asyncio.get_event_loop().create_task(
                                self.telegram.send_alert(_wiring_msg)
                            )
                            self._background_tasks.add(_t)
                            _t.add_done_callback(self._background_tasks.discard)
                        except Exception:
                            pass  # Telegram alert is advisory — never block startup
                    else:
                        logger.info(
                            "WIRING_VALIDATOR: All %d modules wired ✓",
                            wiring_result["total"],
                        )
                except Exception as _wv_err:
                    logger.warning("Wiring validator failed (non-critical): %s", _wv_err)

            return scheduler

        except ImportError:
            logger.error("APScheduler not installed. Manual scan mode only.")
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # H-10: CLOUDWATCH METRICS HANDLER
    # ─────────────────────────────────────────────────────────────────────────

    async def _emit_cloudwatch_metrics(self) -> None:
        """H-10: Emit system health metrics to CloudWatch every 60 seconds.
        Non-blocking, non-critical — never crashes the engine.
        """
        if not self.cloudwatch_emitter or not self.cloudwatch_emitter.available:
            return
        try:
            self.cloudwatch_emitter.emit(
                state_manager=self.state_manager,
                scan_health_tracker=self.scan_health,
            )
        except Exception as e:
            logger.debug("CloudWatch metrics emit failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # H-14: WEEKLY PERFORMANCE REPORT HANDLER
    # ─────────────────────────────────────────────────────────────────────────

    async def _generate_weekly_report(self) -> None:
        """H-14: Generate and send weekly performance report (Sunday 20:00 UK).
        Computes WR, PF, Sharpe, max drawdown, trades by ticker/regime, gate rejections.
        Sends via Telegram and stores in data/weekly_reports/.
        """
        try:
            from delivery.weekly_report import WeeklyPerformanceReport
            report = WeeklyPerformanceReport()
            result = report.generate()

            if result and result.get("report_path"):
                report_path = result["report_path"]
                logger.info("Weekly report generated: %s", report_path)

                # Send via Telegram
                try:
                    summary = result.get("summary", "Weekly performance report")
                    await self.telegram.send_document(
                        report_path,
                        caption=f"Weekly Performance Report\n{summary}",
                    )
                    logger.info("Weekly report sent via Telegram")
                except Exception as tg_err:
                    logger.warning("Weekly report Telegram send failed: %s", tg_err)

                # Also send summary as text message
                try:
                    text_summary = result.get("text_summary", "")
                    if text_summary:
                        await self.telegram.send_alert(text_summary)
                except Exception:
                    pass
            else:
                logger.warning("Weekly report generation returned no output")
        except Exception as e:
            logger.error("Weekly report generation failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # V3.0 NIGHTLY JOB HANDLERS
    # ─────────────────────────────────────────────────────────────────────────

    async def _compute_earnings_runup_scores(self) -> None:
        """Task 1.7 — Nightly pre-earnings run-up score compute (23:00 UTC).
        Scores all S16 tickers for 10-day pre-earnings run-up.
        RC-07b gate reads this state file before each entry.
        """
        if not self.earnings_fade_gate:
            return
        try:
            from core.earnings_fade_gate import compute_all_runup_scores
            tickers = cfg.get_isa_tickers() if cfg.get_primary_mode() == "UK_ISA" else cfg.get_tickers()
            # Also include S16 US stocks
            us_tickers = ["NVDA", "TSLA", "AMD", "TSM", "MU", "ARM"]
            all_tickers = list(set(tickers + us_tickers))
            scores = compute_all_runup_scores(all_tickers)
            logger.info("Earnings run-up scores computed for %d tickers. Fade risks: %s",
                       len(scores),
                       [t for t, s in scores.items() if s.get("is_fade_risk")])
        except Exception as e:
            logger.warning("Earnings run-up nightly compute failed: %s", e)

    async def _refresh_short_squeeze_data(self) -> None:
        """Task 2.7 — Daily FINRA short interest data refresh (07:00 UTC)."""
        if not self.short_squeeze:
            return
        try:
            tickers = cfg.get_isa_tickers() if cfg.get_primary_mode() == "UK_ISA" else cfg.get_tickers()
            us_tickers = ["NVDA", "TSLA", "AMD", "TSM", "MU", "ARM"]
            all_tickers = list(set(tickers + us_tickers))
            results = self.short_squeeze.update_all(all_tickers)
            squeeze_tickers = [t for t, si in results.items() if si and si >= 15.0]
            if squeeze_tickers:
                msg = f"🩳 SHORT SQUEEZE ALERT: {', '.join(squeeze_tickers)} — short interest ≥ 15%"
                await self.telegram.send_message(msg)
                logger.info("Short squeeze alert sent: %s", squeeze_tickers)
            else:
                logger.info("Short squeeze refresh: no squeeze risks detected")
        except Exception as e:
            logger.warning("Short squeeze refresh failed: %s", e)

    async def _run_weekly_auto_improvement(self) -> None:
        """Task 3.3 — Sunday 21:00 UTC auto-improvement loop.
        Runs the adaptive intelligence engine nightly analysis,
        generates approved_params.json for next week.
        """
        try:
            logger.info("=== WEEKLY AUTO-IMPROVEMENT LOOP ===")
            if hasattr(self.adaptive_intel, "run_nightly_analysis"):
                await self.adaptive_intel.run_nightly_analysis()
            elif hasattr(self.adaptive_engine, "run_improvement_cycle"):
                self.adaptive_engine.run_improvement_cycle()
            else:
                logger.info("Auto-improvement: no run_nightly_analysis method found — skipping")

            # Retrain ML meta-model if enough new trades
            if self.ml_model and hasattr(self.ml_model, "should_retrain"):
                if self.ml_model.should_retrain():
                    self.ml_model.train()
                    logger.info("ML meta-model retrained on Sunday cycle")

            # Moskowitz & Grinblatt (1999): update sector momentum rankings weekly
            if self.sector_momentum_engine:
                try:
                    ranks = self.sector_momentum_engine.fetch_and_update_sectors()
                    summary = self.sector_momentum_engine.get_telegram_summary()
                    await self.telegram.send_message(summary)
                    logger.info("Sector momentum updated: %d sectors ranked", len(ranks))
                except Exception as _sec_err:
                    logger.warning("Sector momentum update failed: %s", _sec_err)

            # === AI Research Engine: Weekly Academic Scan + Indicator Calibration ===
            # Silver et al. (2016): self-critique loops accelerate improvement.
            # Crammer et al. (2006) + Settles (2009): weekly cycle = full learning loop.
            if self.ai_research:
                # 1. Weekly academic scan — find recent papers relevant to the system
                try:
                    logger.info("AI RESEARCH: Starting weekly academic scan...")
                    loop = asyncio.get_event_loop()
                    scan_response = await loop.run_in_executor(
                        None, self.ai_research.weekly_academic_scan
                    )
                    if scan_response:
                        # Truncate to 800 chars for Telegram (full text in log)
                        await self.telegram.send_message(
                            f"📚 WEEKLY ACADEMIC SCAN\n"
                            f"{'─'*38}\n"
                            f"{scan_response[:800]}\n"
                            f"{'─'*38}\n"
                            f"Full results: data/ai_research_log.jsonl"
                        )
                        logger.info("AI RESEARCH: Weekly academic scan complete — sent to Telegram")
                    else:
                        logger.info("AI RESEARCH: Weekly academic scan returned no results (no API key?)")
                except Exception as _scan_err:
                    logger.warning("AI Research weekly scan failed: %s", _scan_err)

                # 2. Indicator calibration — check if indicator weights need recalibrating
                # Fires when effectiveness data is available from the indicator tracker
                try:
                    current_regime = (
                        self._current_market_ctx.regime.value
                        if self._current_market_ctx else "UNKNOWN"
                    )
                    indicator_effectiveness = {}
                    if hasattr(self.learning, "indicator_tracker"):
                        try:
                            indicator_effectiveness = (
                                self.learning.indicator_tracker.get_effectiveness_by_regime(
                                    current_regime
                                )
                            )
                        except Exception:
                            # Fallback: use per-indicator win rates from edge ledger
                            indicator_effectiveness = getattr(
                                self.learning, "_indicator_effectiveness_cache", {}
                            )

                    if indicator_effectiveness:
                        # Only run if any indicator shows >15% divergence from expected 0.5 baseline
                        values = list(indicator_effectiveness.values())
                        divergence = max(abs(v - 0.5) for v in values if isinstance(v, (int, float)))
                        if divergence > 0.15:
                            logger.info(
                                "AI RESEARCH: Indicator calibration triggered — "
                                "max divergence %.1f%% in %s regime",
                                divergence * 100, current_regime,
                            )
                            loop = asyncio.get_event_loop()
                            cal_response = await loop.run_in_executor(
                                None,
                                self.ai_research.indicator_calibration_query,
                                indicator_effectiveness, current_regime,
                            )
                            if cal_response:
                                logger.info(
                                    "AI RESEARCH: Indicator calibration complete — "
                                    "suggestions queued for review"
                                )
                        else:
                            logger.info(
                                "AI RESEARCH: Indicator calibration skipped — "
                                "max divergence %.1f%% < 15%% threshold",
                                divergence * 100,
                            )
                    else:
                        logger.info(
                            "AI RESEARCH: Indicator calibration skipped — "
                            "no effectiveness data available yet"
                        )
                except Exception as _cal_err:
                    logger.warning("AI Research indicator calibration failed: %s", _cal_err)

                # 3. Send pending suggestions summary — prompts human review
                try:
                    summary = self.ai_research.get_telegram_suggestions_summary()
                    await self.telegram.send_message(summary)
                except Exception as _sum_err:
                    logger.warning("AI Research suggestions summary failed: %s", _sum_err)

        except Exception as e:
            logger.warning("Weekly auto-improvement loop failed: %s", e)

    async def _send_indicator_ranking_report(self) -> None:
        """Task 3.5 — Friday 21:00 UTC indicator ranking Telegram report."""
        if not self.indicator_ranking:
            return
        try:
            report = self.indicator_ranking.generate_report()
            await self.telegram.send_message(report)
            logger.info("Friday indicator ranking report sent")
        except Exception as e:
            logger.warning("Indicator ranking report failed: %s", e)

    async def _run_monthly_ai_self_assessment(self) -> None:
        """1st of each month 22:00 UTC — full AI fund manager strategy review.

        Gelman et al. (2013) Bayesian Data Analysis: periodic full-system review
        provides the highest-information update to the learning cycle. Monthly cadence
        matches ~100+ new trades — the minimum for statistically reliable assessment.
        """
        if not self.ai_research:
            return
        try:
            logger.info("=== MONTHLY AI SELF-ASSESSMENT ===")
            # Build full performance stats from edge ledger + recent outcomes
            recent = self._get_recent_outcomes(n=500)
            wins = [o for o in recent if (o.get("r_multiple") or 0) > 0]
            losses = [o for o in recent if (o.get("r_multiple") or 0) <= 0]
            total = len(recent)
            win_rate = len(wins) / total if total > 0 else 0.0
            avg_win_r = sum(o.get("r_multiple", 0) for o in wins) / len(wins) if wins else 0.0
            avg_loss_r = sum(o.get("r_multiple", 0) for o in losses) / len(losses) if losses else 0.0
            net_expectancy = (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)

            # Per-strategy breakdown
            strat_stats: dict = {}
            for o in recent:
                strat = o.get("strategy", "UNKNOWN")
                if strat not in strat_stats:
                    strat_stats[strat] = {"trades": 0, "wins": 0, "total_r": 0.0}
                strat_stats[strat]["trades"] += 1
                r = o.get("r_multiple", 0) or 0
                if r > 0:
                    strat_stats[strat]["wins"] += 1
                strat_stats[strat]["total_r"] += r

            current_regime = (
                self._current_market_ctx.regime.value
                if self._current_market_ctx else "UNKNOWN"
            )

            full_stats = {
                "total_trades": total,
                "win_rate": round(win_rate, 4),
                "avg_win_r": round(avg_win_r, 4),
                "avg_loss_r": round(avg_loss_r, 4),
                "net_expectancy": round(net_expectancy, 4),
                "current_regime": current_regime,
                "current_equity": self.equity,
                "daily_pnl_pct": round(self._daily_pnl_pct, 4),
                "consecutive_losses": self._consecutive_losses,
                "strategy_breakdown": {
                    strat: {
                        "trades": v["trades"],
                        "win_rate": round(v["wins"] / v["trades"], 4) if v["trades"] > 0 else 0,
                        "avg_r": round(v["total_r"] / v["trades"], 4) if v["trades"] > 0 else 0,
                    }
                    for strat, v in strat_stats.items()
                },
            }

            loop = asyncio.get_event_loop()
            assessment = await loop.run_in_executor(
                None, self.ai_research.self_assessment_query, full_stats
            )

            if assessment:
                # Send full assessment to Telegram (truncated — full text in log)
                await self.telegram.send_alert(
                    f"🧠 MONTHLY AI STRATEGY REVIEW\n"
                    f"{'─'*38}\n"
                    f"Trades analysed: {total} | WR: {win_rate:.1%} | "
                    f"Net E: {net_expectancy:+.3f}R\n"
                    f"{'─'*38}\n"
                    f"{assessment[:1000]}\n"
                    f"{'─'*38}\n"
                    f"Full review: data/ai_research_log.jsonl\n"
                    f"Suggestions queued for review — reply APPROVE/REJECT <param>"
                )
                logger.info(
                    "AI RESEARCH: Monthly self-assessment complete — total_trades=%d net_e=%.3f",
                    total, net_expectancy,
                )
            else:
                logger.info("AI RESEARCH: Monthly self-assessment returned no response (no API key?)")

        except Exception as e:
            logger.warning("Monthly AI self-assessment failed: %s", e)

    async def _reset_portfolio_heat(self) -> None:
        """Reset portfolio heat monitor at midnight UTC for new trading day."""
        if not self.portfolio_heat:
            return
        try:
            self.portfolio_heat.reset_for_new_session()
            logger.info("Portfolio heat monitor reset for new session")
        except Exception as e:
            logger.error("Portfolio heat reset failed — stale heat data may bypass limits: %s", e)

    # ── V3.2 NEW SCHEDULER HANDLERS ─────────────────────────────────────────

    def _get_recent_outcomes(self, n: int = 200) -> list:
        """Load last N outcomes from outcomes.jsonl for risk metric calculations.
        Returns empty list gracefully if file unavailable.
        """
        import json as _json
        outcomes_path = "data/outcomes.jsonl"
        try:
            import os as _os
            if not _os.path.exists(outcomes_path):
                return []
            with open(outcomes_path) as _f:
                lines = _f.readlines()
            # Take last n lines only (ring-buffer pattern — never load all)
            recent_lines = lines[-n:] if len(lines) > n else lines
            result = []
            for line in recent_lines:
                line = line.strip()
                if line:
                    try:
                        result.append(_json.loads(line))
                    except Exception:
                        pass
            return result
        except Exception:
            return []

    async def _rotate_outcomes_data(self) -> None:
        """W3: Rotate outcomes.jsonl at 04:00 UTC — keep last 2000 hot, archive rest."""
        if not self.data_retention:
            return
        try:
            result = self.data_retention.rotate_outcomes()
            if result["rotated"]:
                logger.info(
                    "Outcomes rotated: %d archived, %d hot",
                    result["archived_lines"], result["hot_lines"],
                )
            else:
                logger.debug(
                    "Outcomes rotation: not needed (%d lines)", result["hot_lines"],
                )
        except Exception as e:
            logger.warning("Outcomes rotation failed: %s", e)

    async def _backup_ml_model(self) -> None:
        """W3: Backup ml_model.pkl at 04:05 UTC — daily backup with pruning."""
        if not self.data_retention:
            return
        try:
            result = self.data_retention.backup_model()
            if result["backed_up"]:
                logger.info("ML model backed up: %s", result["backup_path"])
            else:
                logger.debug("ML model backup: no model file found yet")
        except Exception as e:
            logger.warning("ML model backup failed: %s", e)

    # ── V9.5 Autonomous ML Daemon + CUSUM ─────────────────────────────────

    async def _run_autonomous_ml_daemon(self) -> None:
        """V9.5: Daily Ouroboros — retrain → recalibrate → reap → reload (18:00 UTC)."""
        if not self.ml_daemon:
            return
        try:
            result = await self.ml_daemon.run()
            # Send Telegram summary
            parts = []
            if result.ml_retrained:
                parts.append(f"ML retrained (AUC={result.ml_cv_auc:.3f}, n={result.ml_n_trades})")
            if result.cusum_degraded:
                parts.append(f"CUSUM degraded: {', '.join(result.cusum_degraded)}")
            if result.cusum_probation:
                parts.append(f"CUSUM probation: {', '.join(result.cusum_probation)}")
            if result.hot_reload_published:
                parts.append("hot-reload published")
            if parts:
                summary = " | ".join(parts)
                await self.telegram.send_message(
                    f"[V9.5] ML DAEMON ({result.duration_seconds:.1f}s)\n{summary}"
                )
        except Exception as e:
            logger.error("[V9.5] Autonomous ML Daemon failed: %s", e)

    async def _run_cusum_nightly(self) -> None:
        """V9.5: CUSUM nightly quarantine expiry check (21:00 UTC)."""
        if not self.cusum_reaper:
            return
        try:
            moved = self.cusum_reaper.check_quarantine_expiry()
            if moved:
                msg = f"[V9.5] CUSUM: {len(moved)} strategies moved to PROBATION: {', '.join(moved)}"
                logger.info(msg)
                await self.telegram.send_message(msg)
            else:
                logger.info("[V9.5] CUSUM nightly: no quarantine changes")
        except Exception as e:
            logger.error("[V9.5] CUSUM nightly check failed: %s", e)

    async def _refresh_sector_momentum_daily(self) -> None:
        """W6 fix: Daily sector momentum update (07:00 UTC — before LSE open).
        Previously only ran in weekly Sunday cycle; now runs every trading day.
        Moskowitz & Grinblatt (1999): sector momentum explains individual momentum.
        """
        if not self.sector_momentum_engine:
            return
        try:
            ranks = self.sector_momentum_engine.fetch_and_update_sectors()
            logger.info("Sector momentum daily refresh: %d sectors ranked", len(ranks))
        except Exception as e:
            logger.warning("Sector momentum daily refresh failed: %s", e)

    async def _refresh_sofr_proxy(self) -> None:
        """W6 fix: SOFR-OIS proxy update (07:30 UTC daily).
        Uses SGOV/BIL price ratio as free proxy for short-term funding stress.
        SGOV = 0-3M T-bills, BIL = 1-3M T-bills; ratio divergence signals stress.
        """
        if not self.liquidity_monitor:
            return
        try:
            # SGOV / BIL ratio — both near $100; divergence = funding spread proxy
            sgov = self._data_feeds.get_realtime_price("SGOV") if self._data_feeds else 0.0
            bil = self._data_feeds.get_realtime_price("BIL") if self._data_feeds else 0.0
            sgov = sgov if sgov > 0 else None
            bil = bil if bil > 0 else None
            if sgov and bil and bil > 0:
                # Express as synthetic spread: deviation from 1.0 ratio in basis points
                ratio = sgov / bil
                # Normal ratio ~1.0; spread approx = (ratio - 1) * 10000 bps
                spread_bps = abs(ratio - 1.0) * 10000
                self.liquidity_monitor.update_sofr_ois(spread_bps)
                logger.info("SOFR proxy updated: SGOV/BIL spread=%.1fbps", spread_bps)
            else:
                logger.debug("SOFR proxy: could not fetch SGOV/BIL prices")
        except Exception as e:
            logger.warning("SOFR proxy refresh failed: %s", e)

    async def _record_intraday_opens(self) -> None:
        """W1: Record open prices for intraday momentum engine (09:00 UK).
        Gao et al. (2018): first-30-min return predicts EOD direction.
        """
        if not self.intraday_momentum:
            return
        try:
            underlying_map = {
                "QQQ3.L": "QQQ", "3LUS.L": "QQQ", "QQQ5.L": "QQQ", "SP5L.L": "SPY",
                "GPT3.L": "MSFT", "NVD3.L": "NVDA", "TSL3.L": "TSLA", "TSM3.L": "TSM",
                "MU2.L": "MU", "QQQS.L": "QQQ", "3USS.L": "SPY", "3SEM.L": "SMH",
            }
            recorded = 0
            for lse_ticker, us_underlying in underlying_map.items():
                try:
                    price = self._data_feeds.get_realtime_price(us_underlying) if self._data_feeds else 0.0
                    price = price if price > 0 else None
                    if price:
                        self.intraday_momentum.record_open_price(us_underlying, price)
                        recorded += 1
                except Exception:
                    pass
            logger.info("Intraday momentum: recorded open prices for %d tickers", recorded)
        except Exception as e:
            logger.warning("Intraday open recording failed: %s", e)

    async def _record_intraday_halfhour(self) -> None:
        """W1: Record half-hour prices for intraday momentum engine (09:30 UK).
        Captures first-30-min return for FHR signal generation.
        """
        if not self.intraday_momentum:
            return
        try:
            underlying_map = {
                "QQQ": "QQQ", "SPY": "SPY", "MSFT": "MSFT", "NVDA": "NVDA",
                "TSLA": "TSLA", "TSM": "TSM", "MU": "MU", "SMH": "SMH",
            }
            recorded = 0
            for ticker in underlying_map:
                try:
                    price = self._data_feeds.get_realtime_price(ticker) if self._data_feeds else 0.0
                    price = price if price > 0 else None
                    if price:
                        self.intraday_momentum.record_half_hour_price(ticker, price)
                        recorded += 1
                except Exception:
                    pass
            logger.info("Intraday momentum: recorded half-hour prices for %d tickers", recorded)
        except Exception as e:
            logger.warning("Intraday half-hour recording failed: %s", e)

    async def _run_intraday_eod_signal(self) -> None:
        """W1: Generate intraday momentum EOD signals (20:00 UTC = 15:00 ET).
        Fires on underlying US tickers; maps to ISA ETP confidence adjustments.
        """
        if not self.intraday_momentum:
            return
        try:
            underlying_to_etps = {
                "QQQ": ["QQQ3.L", "3LUS.L", "QQQ5.L", "QQQS.L"],
                "SPY": ["SP5L.L", "3USS.L"],
                "NVDA": ["NVD3.L"],
                "TSLA": ["TSL3.L"],
                "TSM": ["TSM3.L"],
                "MU": ["MU2.L"],
                "SMH": ["3SEM.L"],
            }
            signals_generated = 0
            for underlying, etps in underlying_to_etps.items():
                try:
                    signal = self.intraday_momentum.get_eod_signal(underlying)
                    if signal and signal.get("direction") != "NEUTRAL":
                        direction = signal["direction"]
                        conf_adj = signal.get("confidence_adjustment", 0)
                        logger.info(
                            "Intraday EOD signal: %s → %s (conf %+d) affects %s",
                            underlying, direction, conf_adj, etps,
                        )
                        signals_generated += 1
                except Exception:
                    pass
            logger.info("Intraday momentum EOD: %d signals generated", signals_generated)
        except Exception as e:
            logger.warning("Intraday EOD signal failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # F-02: Async signal queue consumer — processes queued signals
    # ─────────────────────────────────────────────────────────────────────────
    async def _signal_queue_consumer(self) -> None:
        """Async consumer loop that drains the priority signal queue.

        Runs continuously as a background task. Processes signals in priority
        order (highest composite_score first via C-12 PrioritySignalQueue).
        Sleeps 1s between drain cycles to avoid busy-waiting.
        """
        logger.info("F-02: Signal queue consumer started")
        while not self.kill_switch.is_killed():
            try:
                processed = 0
                while not self._signal_queue.empty():
                    try:
                        item = self._signal_queue.get_nowait()
                    except Empty:
                        break
                    sig = item.get("signal")
                    source = item.get("source", "UNKNOWN")
                    if sig is None:
                        continue
                    try:
                        # Log and deliver the signal
                        logger.info(
                            "F-02 CONSUMER: processing %s %s | source=%s | conf=%.0f",
                            sig.direction.value if hasattr(sig.direction, "value") else sig.direction,
                            sig.ticker, source, sig.confidence,
                        )
                        # Persist to database via signal logger
                        if hasattr(self, "signal_logger") and self.signal_logger:
                            try:
                                self.signal_logger.log_signal(sig)
                            except Exception as _sl_err:
                                logger.warning("Signal logger failed for %s: %s", sig.ticker, _sl_err)
                        # 1-09: Telegram delivery removed from consumer to avoid
                        # duplicate notifications — signals are already sent via
                        # run_scan() delivery section and S15 priority path.
                        # Push to API for dashboard
                        try:
                            self._api_pusher.push_signal(sig)
                        except Exception:
                            pass
                        processed += 1
                    except Exception as proc_err:
                        logger.error("F-02 CONSUMER: failed to process signal %s: %s", sig.ticker, proc_err)
                if processed > 0:
                    logger.info("F-02 CONSUMER: drained %d signals this cycle", processed)
            except Exception as drain_err:
                logger.error("F-02 CONSUMER: drain cycle error: %s", drain_err)
            await asyncio.sleep(1)
        logger.info("F-02: Signal queue consumer stopped (kill switch)")

    async def _reconcile_positions(self) -> None:
        """30-second position reconciliation loop.

        Updates live prices via VirtualTrader (which runs profit ladder,
        checks stops, regime flips, time expiry), then updates DB positions.
        Also runs Overseer cross-bot checks.
        """
        if self.kill_switch.is_killed():
            return

        # 1-07: Skip reconciliation outside LSE market hours
        try:
            from core.clock import is_lse_open
            if not is_lse_open():
                return
        except ImportError:
            pass

        try:
            # === VirtualTrader price update cycle ===
            # Gather live prices for all open virtual positions
            if self.virtual_trader.open_positions and self._data_feeds:
                price_data: dict[str, float] = {}
                for pos in self.virtual_trader.open_positions.values():
                    if pos.ticker not in price_data:
                        try:
                            # V3.2: Try realtime_feed first (Polygon/TwelveData) — W1 wiring
                            price = 0.0
                            if self.realtime_feed:
                                try:
                                    rt = self.realtime_feed.get_price(pos.ticker)
                                    price = rt.get("price", 0.0) if isinstance(rt, dict) else float(rt or 0)
                                except Exception:
                                    pass
                            # Fallback: standard data_feeds chain
                            if price <= 0:
                                price = self._data_feeds.get_realtime_price(pos.ticker)
                            price_data[pos.ticker] = price
                        except Exception as e:
                            logger.warning("Realtime price fetch failed for %s: %s", pos.ticker, e)

                if price_data:
                    current_regime = self._current_market_ctx.regime.value if self._current_market_ctx else ""
                    events = self.virtual_trader.update_prices(price_data, current_regime)

                    # Mandate 5: Chandelier Exit trailing stop check
                    if self.chandelier:
                        for pos in list(self.virtual_trader.open_positions.values()):
                            if pos.ticker in price_data and pos.status == "OPEN":
                                try:
                                    _ch_result = self.chandelier.update(
                                        trade_id=pos.id,
                                        current_price=price_data[pos.ticker],
                                    )
                                    if _ch_result.get("exit"):
                                        # Chandelier trailing stop hit — close position
                                        _ch_trade = self.virtual_trader.close_position(
                                            pos.id, price_data[pos.ticker],
                                            f"CHANDELIER_EXIT_RUNG_{_ch_result['rung']}",
                                            current_regime,
                                        )
                                        if _ch_trade:
                                            events.append({
                                                "type": "TRADE_CLOSED",
                                                "trade_id": _ch_trade.id,
                                                "ticker": _ch_trade.ticker,
                                                "r_multiple": _ch_trade.r_multiple,
                                                "net_pnl": _ch_trade.net_pnl,
                                                "reason": f"CHANDELIER_EXIT_RUNG_{_ch_result['rung']}",
                                            })
                                        self.chandelier.close(pos.id)
                                    elif _ch_result.get("scale_out"):
                                        logger.info(
                                            "CHANDELIER SCALE_OUT: %s at +%.1f%% — reduce 50%%",
                                            pos.ticker, _ch_result.get("pct_move", 0),
                                        )
                                except Exception as _ce:
                                    logger.warning("Chandelier update error for %s: %s", pos.ticker, _ce)

                    for event in events:
                        if event.get("type") == "TRADE_CLOSED":
                            logger.info(
                                "VT CLOSED: %s R=%.2f PnL=$%.2f (%s)",
                                event["ticker"], event["r_multiple"],
                                event["net_pnl"], event["reason"],
                            )
                            # Clean up Chandelier state on close
                            if self.chandelier:
                                self.chandelier.close(event.get("trade_id", ""))
                        elif event.get("type") == "LADDER":
                            logger.info(
                                "VT LADDER: %s rung %d — %s",
                                event["ticker"], event["rung"], event.get("action", ""),
                            )

            # === EXIT ENGINE: Score open positions for exit urgency ===
            # Runs after VirtualTrader's built-in stops/ladders. Catches regime flips,
            # RSI divergence, time expiry, and other exit signals the ladder misses.
            try:
                remaining_positions = list(self.virtual_trader.open_positions.values())
                if remaining_positions and self._data_feeds:
                    import pandas as pd
                    exit_pos_dicts = []
                    exit_bars_batch = {}
                    for vp in remaining_positions:
                        entry_time = vp.entry_time
                        if isinstance(entry_time, str):
                            try:
                                entry_time = datetime.fromisoformat(entry_time)
                            except Exception:
                                entry_time = datetime.now(timezone.utc)
                        exit_pos_dicts.append({
                            "ticker": vp.ticker,
                            "direction": vp.direction,
                            "entry_price": vp.entry_price,
                            "current_price": price_data.get(vp.ticker, vp.entry_price),
                            "entry_time": entry_time,
                            "peak_r": vp.peak_r or 0.0,
                            "strategy": vp.strategy or "",
                            "shares": vp.shares or 0,
                        })
                        # Try to get OHLC bars for RSI divergence detection
                        if vp.ticker not in exit_bars_batch:
                            try:
                                bars = self._data_feeds.get_intraday_bars(vp.ticker, interval="5m", period="1d")
                                if bars is not None and not bars.empty:
                                    exit_bars_batch[vp.ticker] = bars
                            except Exception:
                                pass  # ExitEngine handles missing bars gracefully

                    if exit_pos_dicts:
                        current_regime = self._current_market_ctx.regime.value if self._current_market_ctx else ""
                        exit_scores = self.exit_engine.score_exits(
                            exit_pos_dicts, exit_bars_batch, current_regime,
                        )
                        # Force close positions with EXIT_NOW intent
                        for es in exit_scores:
                            if es.get("sell_intent") == "EXIT_NOW":
                                ticker = es["ticker"]
                                logger.warning(
                                    "EXIT_ENGINE: FORCE CLOSE %s — score=%d, reasons: %s",
                                    ticker, es.get("exit_score", 0),
                                    "; ".join(es.get("kill_conditions", [])),
                                )
                                try:
                                    # Look up position_id from open_positions by ticker
                                    _exit_pos_id = None
                                    for _pid, _pobj in self.virtual_trader.open_positions.items():
                                        if _pobj.ticker == ticker and _pobj.status == "OPEN":
                                            _exit_pos_id = _pid
                                            break
                                    if _exit_pos_id:
                                        self.virtual_trader.close_position(
                                            _exit_pos_id,
                                            price_data.get(ticker, es.get("current_price", 0)),
                                            reason=f"EXIT_ENGINE: {'; '.join(es.get('kill_conditions', ['EXIT_NOW']))}",
                                        )
                                    else:
                                        logger.warning("ExitEngine: no open position found for ticker %s", ticker)
                                except Exception as close_err:
                                    logger.warning("ExitEngine force close failed for %s: %s", ticker, close_err)
                            elif es.get("sell_intent") == "PARTIAL" and es.get("exit_score", 0) >= 70:
                                logger.info(
                                    "EXIT_ENGINE: PARTIAL signal for %s (score=%d) — handled by profit ladder",
                                    es["ticker"], es.get("exit_score", 0),
                                )
            except Exception as exit_eng_err:
                logger.warning("ExitEngine scoring failed (non-critical): %s", exit_eng_err)

            # === RVOL SPIKE DETECTION: Event-driven ad-hoc scanning ===
            # Check if any ticker has RVOL crossing 2.0 — trigger scan for that ticker
            try:
                if self._data_feeds and self._indicator_engine:
                    tickers = cfg.get("bot_b_universe.tickers", [])
                    for ticker in tickers:
                        try:
                            if hasattr(self._indicator_engine, 'get_cached_rvol'):
                                rvol = self._indicator_engine.get_cached_rvol(ticker)
                            else:
                                rvol = 0
                            if rvol >= 2.0:
                                # Check if we already scanned this ticker recently (5 min cooldown)
                                now = datetime.now(timezone.utc)
                                last_scan = self._rvol_scan_cache.get(ticker)
                                if last_scan is None or (now - last_scan).total_seconds() > 300:
                                    self._rvol_scan_cache[ticker] = now
                                    # Evict oldest if cache exceeds 100 entries
                                    if len(self._rvol_scan_cache) > 100:
                                        oldest = min(self._rvol_scan_cache, key=self._rvol_scan_cache.get)
                                        del self._rvol_scan_cache[oldest]
                                    logger.warning("RVOL SPIKE: %s RVOL=%.2f — triggering ad-hoc scan", ticker, rvol)
                                    await self.run_scan(
                                        strategy_ids=["S2", "S4", "S8", "S14"],
                                        tickers=[ticker],
                                    )
                        except Exception as e:
                            logger.warning("RVOL ad-hoc scan failed for %s: %s", ticker, e)
            except Exception as rvol_err:
                logger.warning("RVOL spike detection error: %s", rvol_err)

            # === DB-based position reconciliation (existing flow) ===
            with transaction() as conn:
                from delivery.database import get_open_positions, upsert_position

                positions = get_open_positions(conn)
                for pos_row in positions:
                    # Get current price
                    ticker = pos_row["ticker"]
                    if self._data_feeds:
                        current_price = self._data_feeds.get_realtime_price(ticker)
                    else:
                        continue

                    if current_price <= 0:
                        continue

                    # Build a Position object from the row
                    from models import Position, LadderRung
                    position = Position(
                        id=pos_row["id"],
                        ticker=ticker,
                        direction=Direction.LONG if pos_row["direction"] == "LONG"
                            else Direction.SHORT,
                        entry=pos_row["entry"],
                        shares=pos_row["shares"],
                        current_stop=pos_row["current_stop"],
                        original_stop=pos_row["original_stop"],
                        ladder_rung=LadderRung(pos_row["ladder_rung"] or 0),
                        remaining_pct=pos_row["remaining_pct"],
                        current_price=current_price,
                        bot=Bot(pos_row["bot"]) if pos_row["bot"] else Bot.B,
                    )

                    # Run profit ladder
                    if position.bot == Bot.A:
                        action = self.etp_ladder.evaluate(position, current_price)
                    else:
                        action = self.profit_ladder.evaluate(
                            position, current_price, atr=0.0
                        )

                    if action:
                        logger.info(
                            "LADDER %s %s: %s",
                            position.ticker, action.rung.name, action.action
                        )
                        # Update position in DB
                        updates = {
                            "id": position.id,
                            "current_price": current_price,
                            "unrealised_pnl": position.unrealised_pnl,
                            "unrealised_r": position.unrealised_r,
                            "ladder_rung": action.rung.value,
                            "remaining_pct": action.remaining_pct,
                            "last_update": datetime.now(timezone.utc).isoformat(),
                        }
                        if action.new_stop is not None:
                            updates["current_stop"] = action.new_stop

                        upsert_position(conn, updates)

                        # Send Telegram alert for significant ladder events
                        if action.sell_pct > 0:
                            await self.telegram.send_alert(
                                f"💰 {action.action}\n"
                                f"{position.ticker} — sell {action.sell_pct*100:.0f}%"
                            )

        except Exception as e:
            logger.error("Position reconciliation error: %s", e)

    async def _run_pre_close_audit(self) -> None:
        """Run pre-close audit at 15:45 ET — 15 min before market close.

        Reviews all open positions and flags any requiring manual attention
        (e.g., positions that should be flattened before close).
        """
        try:
            positions = list(self.virtual_trader.open_positions.values())
            pos_dicts = []
            for pos in positions:
                # Derive current_price from unrealised_pnl since VirtualPosition has no current_price field
                remaining_shares = int(pos.shares * getattr(pos, 'remaining_pct', 1.0))
                if remaining_shares > 0 and pos.unrealised_pnl is not None:
                    if pos.direction == "LONG":
                        current_price = pos.entry_price + (pos.unrealised_pnl / remaining_shares)
                    else:
                        current_price = pos.entry_price - (pos.unrealised_pnl / remaining_shares)
                else:
                    current_price = pos.entry_price
                pos_dicts.append({
                    "ticker": pos.ticker,
                    "direction": pos.direction,
                    "bot": getattr(pos, "bot", "B"),
                    "shares": pos.shares,
                    "entry_price": pos.entry_price,
                    "current_price": current_price,
                    "stop": pos.current_stop,
                    "pnl_r": pos.unrealised_r,
                    "risk_dollars": getattr(pos, "risk_dollars", 0),
                    "entry_time": pos.entry_time,
                    "liquidity_score": 80.0,
                })
            # run_pre_close_audit returns list[dict], not dict; no equity param
            audit_actions = self.session_manager.run_pre_close_audit(
                open_positions=pos_dicts,
            )
            if audit_actions:
                msg_lines = ["PRE-CLOSE AUDIT (15:45 ET)", "=" * 30]
                for action in audit_actions:
                    msg_lines.append(
                        f"  {action.get('ticker', '?')}: {action.get('action', '?')} "
                        f"— {action.get('reason', '')}"
                    )
                await self.telegram.send_alert("\n".join(msg_lines))
            logger.info("Pre-close audit: %d positions reviewed, %d actions",
                        len(pos_dicts), len(audit_actions))
        except Exception as e:
            logger.error("Pre-close audit failed: %s", e)

    async def _force_close_eod(self) -> None:
        """Force close ALL open positions at end of trading day (16:00 ET).

        The user specified: latest cash out = end of the trading day.
        This ensures no positions are held overnight (except explicit SWING trades).
        """
        try:
            with self.virtual_trader._lock:
                positions = list(self.virtual_trader.open_positions.items())
                if not positions:
                    logger.info("EOD: No open positions to close")
                    return

                closed_count = 0
                total_pnl = 0.0
                for pos_id, pos in positions:
                    if pos.status != "OPEN":
                        continue
                    # Skip SWING trades — they are explicitly held overnight
                    if pos.bot_instance == "SWING":
                        logger.info("EOD: Skipping SWING position %s %s", pos.ticker, pos.direction)
                        continue

                    # Derive exit price from latest unrealised P&L
                    exit_price = self._derive_exit_price_simple(pos)

                    trade = self.virtual_trader.close_position(
                        pos_id, exit_price, "EOD_FORCE_CLOSE",
                        regime_at_exit=self._current_market_ctx.regime.value if self._current_market_ctx else "",
                    )
                    if trade:
                        closed_count += 1
                        total_pnl += trade.net_pnl

                if closed_count > 0:
                    pnl_sign = "+" if total_pnl >= 0 else ""
                    msg = (
                        f"EOD FORCE CLOSE\n"
                        f"Closed {closed_count} positions\n"
                        f"Realised P&L: {pnl_sign}${total_pnl:.2f}"
                    )
                    await self.telegram.send_alert(msg)
                    logger.info("EOD: Closed %d positions, P&L=$%.2f", closed_count, total_pnl)

        except Exception as e:
            logger.error("EOD force close failed: %s", e, exc_info=True)

    async def _force_close_5x_etps(self) -> None:
        """A-12: Hard kill all 5x ETP positions at 15:30 UK.

        Dedicated scheduler job — separate from scan loop to prevent
        stall-related misses. 3% overnight gap on 5x = 15% portfolio loss.
        """
        _5X_TICKERS = {"QQQ5.L", "SP5L.L"}  # 5x leveraged ETPs
        if not hasattr(self, 'virtual_trader'):
            return
        open_pos = getattr(self.virtual_trader, 'open_positions', {})
        closed = []
        for pos_id, pos in list(open_pos.items()):
            if pos.ticker in _5X_TICKERS:
                try:
                    exit_price = self._derive_exit_price_simple(pos)
                    self.virtual_trader.close_position(pos_id, exit_price, reason="A-12_5x_hard_kill_1530")
                    closed.append(pos.ticker)
                except Exception as e:
                    logger.error("A-12: Failed to close 5x position %s: %s", pos.ticker, e)
        if closed:
            logger.critical(
                "A-12 5x HARD KILL at 15:30 UK: closed %d positions: %s",
                len(closed), closed,
            )

    async def _force_close_lse(self) -> None:
        """Force close ALL .L positions at 15:50 UK — 10 minutes before LSE closing auction.

        The existing EOD close fires at 16:00 ET = 21:00 UK, which is 5 HOURS after LSE
        closes. This ensures ISA leveraged ETP positions are closed properly within
        LSE trading hours, avoiding overnight decay risk on 3x/5x products.
        """
        try:
            with self.virtual_trader._lock:
                positions = list(self.virtual_trader.open_positions.items())
                if not positions:
                    logger.info("LSE CLOSE: No open positions")
                    return

                closed_count = 0
                total_pnl = 0.0
                for pos_id, pos in positions:
                    if pos.status != "OPEN":
                        continue
                    # Only close .L (LSE) tickers — US positions handled by EOD close
                    if not pos.ticker.endswith(".L"):
                        continue
                    # Skip SWING trades
                    if pos.bot_instance == "SWING":
                        logger.info("LSE CLOSE: Skipping SWING position %s", pos.ticker)
                        continue

                    exit_price = self._derive_exit_price_simple(pos)
                    trade = self.virtual_trader.close_position(
                        pos_id, exit_price, "LSE_FORCE_CLOSE_1550",
                        regime_at_exit=self._current_market_ctx.regime.value if self._current_market_ctx else "",
                    )
                    if trade:
                        closed_count += 1
                        total_pnl += trade.net_pnl

                if closed_count > 0:
                    pnl_sign = "+" if total_pnl >= 0 else ""
                    msg = (
                        f"LSE FORCE CLOSE (15:50 UK)\n"
                        f"Closed {closed_count} ISA positions\n"
                        f"Realised P&L: {pnl_sign}£{total_pnl:.2f}"
                    )
                    await self.telegram.send_alert(msg)
                    logger.info("LSE CLOSE: Closed %d .L positions, P&L=£%.2f", closed_count, total_pnl)
                else:
                    logger.info("LSE CLOSE: No .L positions to close")

        except Exception as e:
            logger.error("LSE force close failed: %s", e, exc_info=True)

    async def _evaluate_b_team(self) -> None:
        """Evaluate B-Team promotion/relegation. Runs daily at 16:00 UK (post-LSE)."""
        try:
            if not self.b_team:
                return

            events = self.b_team.evaluate()
            if not events:
                logger.info("B-TEAM EVAL: No league changes today")
                return

            # Send Telegram notifications for each event
            for event in events:
                try:
                    await self.telegram.send_league_update(event)
                except Exception as e:
                    logger.warning("B-Team Telegram alert failed: %s", e)

            # Save state
            self.b_team.save_state()
            logger.info("B-TEAM EVAL: %d league changes processed", len(events))

        except Exception as e:
            logger.error("B-Team evaluation failed: %s", e, exc_info=True)

    async def _send_weekly_league_summary(self) -> None:
        """Send weekly B-Team league summary via Telegram. Runs Sunday 18:00 UK.

        Compiles the full league table (A-Team, B-Team, C-Team) with
        per-ticker stats (trades, win rate, avg R, total P&L) and sends
        a formatted summary to Telegram for weekly review.
        """
        try:
            if not self.b_team:
                logger.info("WEEKLY LEAGUE: B-Team not initialized, skipping")
                return

            league_table = self.b_team.get_league_table()
            if not league_table:
                logger.info("WEEKLY LEAGUE: No league data available")
                return

            a_team = [t for t in league_table if t.get("team") == "A"]
            b_team = [t for t in league_table if t.get("team") == "B"]
            c_team = [t for t in league_table if t.get("team") == "C"]

            def _format_tier(name: str, tickers: list[dict]) -> str:
                if not tickers:
                    return f"\n<b>{name}</b>: (empty)\n"
                lines = [f"\n<b>{name}</b> ({len(tickers)} tickers):"]
                for t in tickers[:10]:  # Cap at 10 per tier for readability
                    ticker = t.get("ticker", "?")
                    trades = t.get("trades", 0)
                    wr = (t.get("win_rate", 0) or 0) * 100
                    avg_r = t.get("avg_r", 0) or 0
                    pnl = t.get("total_pnl", 0) or 0
                    grade = t.get("grade", "?")
                    lines.append(
                        f"  <code>{ticker:>8s}</code>  {trades:>3d}T  "
                        f"WR={wr:>4.0f}%  R={avg_r:>+5.2f}  "
                        f"PnL=£{pnl:>+8.2f}  [{grade}]"
                    )
                if len(tickers) > 10:
                    lines.append(f"  ... +{len(tickers) - 10} more")
                return "\n".join(lines)

            total_trades = sum(t.get("trades", 0) for t in league_table)
            total_pnl = sum(t.get("total_pnl", 0) or 0 for t in league_table)

            message = (
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>WEEKLY LEAGUE SUMMARY</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Total: {len(league_table)} tickers | {total_trades} trades | £{total_pnl:+,.2f}\n"
                f"{_format_tier('🅰️ A-TEAM (Active Trading)', a_team)}\n"
                f"{_format_tier('🅱️ B-TEAM (Watchlist)', b_team)}\n"
                f"{_format_tier('🅲 C-TEAM (Benched)', c_team)}\n"
                f"\n━━━━━━━━━━━━━━━━━━━"
            )

            await self.telegram._send_message(message)
            logger.info(
                "WEEKLY LEAGUE: Summary sent — A=%d B=%d C=%d | %d trades | £%.2f PnL",
                len(a_team), len(b_team), len(c_team), total_trades, total_pnl,
            )

        except Exception as e:
            logger.error("Weekly league summary failed: %s", e, exc_info=True)

    async def _run_sector_monitor(self) -> None:
        """5-minute sector rotation monitoring during LSE hours.

        Computes 5-day rolling RS for tracked sectors. Fires Telegram alert
        when leadership changes or RS crosses the 1.0 threshold.
        """
        try:
            if not self._current_market_ctx:
                return

            # Get current sector flows from market context
            sector_flows = getattr(self._current_market_ctx, 'sector_flows', {}) or {}
            if not sector_flows:
                return

            # Build RS snapshot
            sector_rs: dict[str, float] = {}
            for sec, flow in sector_flows.items():
                rs = getattr(flow, 'relative_strength', 0) or getattr(flow, 'rs_5d', 0) or 0
                if rs > 0:
                    sector_rs[sec] = rs

            if not sector_rs:
                return

            # Sort by RS
            ranked = sorted(sector_rs.items(), key=lambda x: x[1], reverse=True)
            top_3 = [s[0] for s in ranked[:3]]

            # Check for leadership changes vs last check
            if not hasattr(self, '_last_sector_top3'):
                self._last_sector_top3 = top_3
                return

            if top_3 != self._last_sector_top3:
                old_leader = self._last_sector_top3[0] if self._last_sector_top3 else "?"
                new_leader = top_3[0] if top_3 else "?"
                old_rs = sector_rs.get(old_leader, 0)
                new_rs = sector_rs.get(new_leader, 0)

                logger.info(
                    "SECTOR ROTATION: %s (RS=%.2f) → %s (RS=%.2f)",
                    old_leader, old_rs, new_leader, new_rs,
                )

                try:
                    await self.telegram.send_sector_rotation_alert(
                        old_leader=old_leader, new_leader=new_leader,
                        old_rs=old_rs, new_rs=new_rs,
                        sector_rs=sector_rs,
                    )
                except Exception as e:
                    logger.warning("Sector rotation alert failed: %s", e)

                self._last_sector_top3 = top_3

        except Exception as e:
            logger.error("Sector monitor failed: %s", e, exc_info=True)

    async def _run_daily_reset(self) -> None:
        """Daily reset at 04:00 ET — reset data validator counters and session manager."""
        try:
            self.data_validator.reset_daily()
            # F-14: Reset stale data monitor for new trading day
            self.stale_data_monitor.reset()
            # Clean up RVOL scan cache
            cache_size = len(self._rvol_scan_cache)
            self._rvol_scan_cache.clear()
            if cache_size:
                logger.debug("Cleaned up %d RVOL scan cache entries", cache_size)
            logger.info("Daily data validator counters reset")
        except Exception as e:
            logger.error("Daily data validator reset failed: %s", e)

        # V5.0 Phase 9: Reset circuit breakers daily
        if hasattr(self, 'circuit_breakers') and self.circuit_breakers:
            try:
                self.circuit_breakers.reset_daily(current_equity=self.equity)
                logger.info("Circuit breakers reset for new trading day")
            except Exception as cb_err:
                logger.error("Circuit breaker daily reset failed: %s", cb_err)

        # SA-5 FIX: Refresh SheetsLogger equity for accurate daily P&L %
        if hasattr(self, 'sheets') and self.sheets:
            try:
                self.sheets.update_equity(self.equity)
                logger.info("SheetsLogger equity refreshed: £%.2f", self.equity)
            except Exception as _sl_err:
                logger.error("SheetsLogger equity refresh failed: %s", _sl_err)

        # SD-1 FIX: Clear chain boosts from previous day (state leakage prevention)
        if hasattr(self, '_pending_chain_boosts'):
            self._pending_chain_boosts.clear()

        # T-17: Clear stale sector state
        if hasattr(self, '_last_sector_top3'):
            del self._last_sector_top3

        # T-17: Refresh loss counters from database (fail-safe: keep existing on error)
        if hasattr(self, '_update_state_from_db'):
            try:
                with transaction() as conn:
                    self._update_state_from_db(conn)
                logger.info("State refreshed from DB on daily reset")
            except Exception as _db_refresh_err:
                logger.error("Failed to refresh state from DB on daily reset: %s — keeping existing values", _db_refresh_err)

        # V8.0 StateManager: reset daily P&L + clear frozen config hash for new day
        if self.state_manager:
            try:
                await self.state_manager.reset_daily_pnl()
                self._frozen_config_hash = None  # Clear Ulysses Lock for new session
                logger.info("StateManager: daily P&L reset + Ulysses Lock cleared")
            except Exception as _sm_reset_err:
                logger.error("StateManager daily reset failed: %s", _sm_reset_err)

    async def _generate_and_send_pdf_report(self, session: str) -> None:
        """Generate a PDF intelligence report and send via Telegram.

        Args:
            session: 'PRE-LSE' or 'PRE-NYSE'
        """
        logger.info("=== PDF INTELLIGENCE REPORT: %s ===", session)
        try:
            report = PDFIntelligenceReport()
            if session == "PRE-LSE":
                pdf_path = report.generate_pre_lse_report()
            else:
                pdf_path = report.generate_pre_nyse_report()

            logger.info("PDF generated: %s", pdf_path)

            # Send via Telegram
            sent = await report.send_via_telegram(pdf_path)
            if sent:
                logger.info("PDF sent to Telegram: %s", session)
            else:
                logger.warning("PDF Telegram delivery failed for %s (PDF saved at %s)", session, pdf_path)

        except Exception as e:
            logger.error("PDF report generation failed for %s: %s", session, e, exc_info=True)

    # ── V8.0 PDF TRIPLE REPORT METHODS ───────────────────────────────────────

    # ── v3.0 ARTIFACT-FIRST HELPER ────────────────────────────────────────────

    def _run_engine_and_write_artifact(self, session: str, regime: str = "NEUTRAL",
                                       period: str = "10d"):
        """Run signal engine and write plays.json artifact atomically.

        Returns EngineResult on success, None on failure.
        Called at the start of each PDF job so the artifact is always fresh.
        Also logs signals and builds intel cards.
        """
        try:
            pipeline_result = run_pipeline(
                session=session,
                period=period,
                regime=regime,
                is_preview=False,
                generate_intel=True,
                n_plays_min=3,
                n_plays_max=20,
            )
            if pipeline_result.engine_result:
                result = pipeline_result.engine_result
                logger.info(
                    "[ARTIFACT] session=%s plays=%d strict=%d fallback=%d drought=%s "
                    "signals_logged=%d intel=%d",
                    session, len(result.plays), result.strict_count, result.fallback_count,
                    result.drought is not None, pipeline_result.signals_logged,
                    pipeline_result.intel_count,
                )
                # Update War Room state with latest engine result
                self._update_war_room_state(result)
                return result
            return None
        except Exception as exc:
            logger.error("[ARTIFACT] engine run failed for %s: %s", session, exc)
            return None

    def _update_war_room_state(self, engine_result) -> None:
        """Push latest engine result to War Room state for live display."""
        try:
            from command_center.state import get_state
            import asyncio
            import inspect
            state = get_state()
            update = state.update_from_engine(engine_result)
            if inspect.isawaitable(update):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        _t = asyncio.ensure_future(update)
                        self._background_tasks.add(_t)
                        _t.add_done_callback(self._background_tasks.discard)
                    else:
                        loop.run_until_complete(update)
                except RuntimeError:
                    # Never nest asyncio.run() inside a running loop — schedule instead
                    _t = asyncio.ensure_future(update)
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)

            # Push CC snapshot to unified API for WebSocket broadcast
            try:
                snapshot = state.get_snapshot()
                self._api_pusher.push_cc_snapshot(snapshot)
            except Exception as push_err:
                logger.warning("[APIPusher] CC snapshot push failed: %s", push_err)

        except Exception as e:
            logger.warning("War Room state update failed: %s", e)

    # ── WATCHDOG ARTIFACTS (Paper Launch Quality Layer) ─────────────────────

    def _write_watchdog_artifacts_for_session(self, session: str, engine_result) -> None:
        """Write system_state, reliability, quality_report, readiness artifacts."""
        try:
            from system_watchdog import (
                SystemWatchdog, compute_data_reliability, run_quality_gate,
                write_watchdog_artifacts,
            )
            wd = SystemWatchdog()
            wd.record_tick()
            wd.record_data_fetch()

            # Gather constitution metrics
            try:
                with transaction() as _wdog_conn:
                    daily_loss = get_daily_pnl(_wdog_conn)
                    consec = get_consecutive_losses(_wdog_conn, bot_instance="S15")
            except Exception:
                daily_loss = 0.0
                consec = 0
            open_pos = len(getattr(self.virtual_trader, "open_positions", {}))
            kill_active = self.kill_switch.is_killed() if hasattr(self, "kill_switch") else False

            state_report = wd.check_state(
                tick_count=len(getattr(engine_result, "plays", [])) if engine_result else 0,
                daily_loss_pct=daily_loss,
                consecutive_losses=consec,
                open_positions=open_pos,
                kill_switch_active=kill_active,
            )

            # Data reliability
            health = getattr(engine_result, "health_summary", None) if engine_result else None
            features_map = getattr(engine_result, "features_map", {}) if engine_result else {}
            reliability = compute_data_reliability(health, features_map)

            # Quality gate on plays
            plays = getattr(engine_result, "plays", []) if engine_result else []
            regime = getattr(engine_result, "regime", "NEUTRAL") if engine_result else "NEUTRAL"
            quality = run_quality_gate(plays, regime, features_map)

            write_watchdog_artifacts(session, state_report, reliability, quality)
            logger.info("[WATCHDOG] artifacts written for session=%s state=%s reliability=%.2f quality=%s",
                        session, state_report.state, reliability.score, quality.passed)

            # Alert on non-OK system state
            if state_report.state != "OK":
                try:
                    alert_msg = (
                        f"WATCHDOG {state_report.state}: {session}\n"
                        f"Reasons: {', '.join(state_report.reasons[:3])}\n"
                        f"Reliability: {reliability.score:.2f}\n"
                        f"Quality: {'PASS' if quality.passed else 'FAIL'}"
                    )
                    import asyncio
                    _t = asyncio.ensure_future(self.telegram.send_alert(alert_msg))
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)
                except Exception as wd_tg_err:
                    logger.warning("Watchdog Telegram alert failed: %s", wd_tg_err)

            # Alert on quality gate failures even when state is OK
            if not quality.passed and quality.violations:
                try:
                    import asyncio
                    _t = asyncio.ensure_future(self.telegram.send_alert(
                        f"QUALITY GATE FAIL: {session}\n"
                        f"Violations: {', '.join(str(v) for v in quality.violations[:3])}"
                    ))
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)
                except Exception as qg_err:
                    logger.warning("Quality gate Telegram alert failed: %s", qg_err)
        except Exception as exc:
            logger.warning("[WATCHDOG] artifact write failed for %s: %s", session, exc)

    def _check_system_health(self):
        """Check system health and alert on degraded state."""
        try:
            import psutil
            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
            if rss_mb > 1024:  # > 1GB
                logger.warning("SYSTEM: high memory usage: %.0f MB", rss_mb)
                try:
                    _t = asyncio.ensure_future(self.telegram.send_alert(
                        f"⚠️ NZT-48 HIGH MEMORY: {rss_mb:.0f} MB (threshold: 1024 MB)"
                    ))
                    self._background_tasks.add(_t)
                    _t.add_done_callback(self._background_tasks.discard)
                except Exception as tg_err:
                    logger.warning("Health check Telegram alert failed: %s", tg_err)
        except ImportError:
            pass  # psutil not available
        except Exception as e:
            logger.warning("System health check failed: %s", e)

    # ── V8.0 PDF TRIPLE REPORT METHODS (v3.0: artifact-first + session_status) ─

    async def _generate_v2_pdf1(self, session: str = "PRE_LSE") -> None:
        """V2 PDF 1: Pre-LSE Brief. Fires 07:00 UK — 1 hour before LSE opens."""
        import uuid
        run_id = str(uuid.uuid4())[:8].upper()
        logger.info("=== V2 PDF1: PRE-LSE BRIEF [%s] run=%s ===", session, run_id)
        artifacts_written = False
        pdf_written = False
        error_msg = ""
        _engine_result = None
        pdf_path_str = ""
        try:
            # Step 1: Run engine + write artifact
            _engine_result = self._run_engine_and_write_artifact(session, period="5d")
            artifacts_written = _engine_result is not None

            # Step 1b: Write watchdog artifacts (system_state, reliability, quality)
            self._write_watchdog_artifacts_for_session(session, _engine_result)

            # Step 2: Generate PDF
            if not _PDF_V2_MOMENTUM_AVAILABLE:
                logger.warning("pdf_v2_momentum not available — falling back to legacy PDF")
                await self._generate_and_send_pdf_report("PRE-LSE")
                pdf_written = True
            else:
                report = _MomentumPDFReport()
                pdf_path = report.generate(session=session)
                pdf_path_str = str(pdf_path)
                logger.info("V2 PDF1 generated: %s", pdf_path)
                import inspect
                _send = report.send_via_telegram(pdf_path)
                sent = (await _send) if inspect.isawaitable(_send) else _send
                pdf_written = True
                if sent:
                    logger.info("V2 PDF1 (%s) sent to Telegram", session)
                else:
                    logger.warning("V2 PDF1 Telegram delivery failed (saved at %s)", pdf_path)
        except Exception as e:
            error_msg = str(e)[:200]
            logger.error("V2 PDF1 generation failed: %s", e, exc_info=True)
        finally:
            try:
                from signal_engine.signal_card import update_session_status
                from datetime import datetime as _dt
                try:
                    from zoneinfo import ZoneInfo as _ZI
                    _uk_now = _dt.now(_ZI("Europe/London")).isoformat()
                except Exception as e:
                    logger.warning("ZoneInfo unavailable for PDF1 timestamp: %s", e)
                    _uk_now = _dt.utcnow().isoformat()
                update_session_status(
                    session, run_id, artifacts_written, pdf_written, error_msg,
                    pdf_path=pdf_path_str,
                    signals_strict_count=getattr(_engine_result, "strict_count", 0),
                    signals_fallback_count=getattr(_engine_result, "fallback_count", 0),
                    drought_flag=bool(getattr(_engine_result, "drought", None)),
                    top_blockers=getattr(_engine_result, "blocker_summary", [])[:3],
                    generated_at_uk=_uk_now,
                )
            except Exception as e:
                logger.warning("PDF1 session status update failed: %s", e)

    async def _generate_v2_pdf2(self, session: str = "PRE_NYSE") -> None:
        """V2 PDF 2: Pre-NYSE Brief. Fires 13:30 UK — 1 hour before NYSE opens."""
        import uuid
        run_id = str(uuid.uuid4())[:8].upper()
        logger.info("=== V2 PDF2: PRE-NYSE BRIEF [%s] run=%s ===", session, run_id)
        artifacts_written = False
        pdf_written = False
        error_msg = ""
        _engine_result2 = None
        pdf_path_str2 = ""
        try:
            # Step 1: Run engine + write artifact
            _engine_result2 = self._run_engine_and_write_artifact(session, period="5d")
            artifacts_written = _engine_result2 is not None

            # Step 1b: Write watchdog artifacts (system_state, reliability, quality)
            self._write_watchdog_artifacts_for_session(session, _engine_result2)

            # Step 2: Generate PDF
            if not _PDF_V2_RISK_AVAILABLE:
                logger.warning("pdf_v2_risk not available — falling back to legacy PDF for PDF2")
                try:
                    await self._generate_and_send_pdf_report("PRE-NYSE")
                    pdf_written = True
                    logger.info("PDF2 legacy fallback completed successfully")
                except Exception as e_fallback:
                    logger.error("PDF2 legacy fallback ALSO failed: %s", e_fallback, exc_info=True)
            else:
                report = _RiskPDFReport()
                pdf_path = report.generate(session=session)
                pdf_path_str2 = str(pdf_path)
                logger.info("V2 PDF2 generated: %s", pdf_path)
                import inspect
                _send = report.send_via_telegram(pdf_path)
                sent = (await _send) if inspect.isawaitable(_send) else _send
                pdf_written = True
                if sent:
                    logger.info("V2 PDF2 (%s) sent to Telegram", session)
                else:
                    logger.warning("V2 PDF2 Telegram delivery failed (saved at %s)", pdf_path)
        except Exception as e:
            error_msg = str(e)[:200]
            logger.error("V2 PDF2 generation failed: %s", e, exc_info=True)
        finally:
            try:
                from signal_engine.signal_card import update_session_status
                from datetime import datetime as _dt2
                try:
                    from zoneinfo import ZoneInfo as _ZI2
                    _uk_now2 = _dt2.now(_ZI2("Europe/London")).isoformat()
                except Exception as e:
                    logger.warning("ZoneInfo unavailable for PDF2 timestamp: %s", e)
                    _uk_now2 = _dt2.utcnow().isoformat()
                update_session_status(
                    session, run_id, artifacts_written, pdf_written, error_msg,
                    pdf_path=pdf_path_str2,
                    signals_strict_count=getattr(_engine_result2, "strict_count", 0),
                    signals_fallback_count=getattr(_engine_result2, "fallback_count", 0),
                    drought_flag=bool(getattr(_engine_result2, "drought", None)),
                    top_blockers=getattr(_engine_result2, "blocker_summary", [])[:3],
                    generated_at_uk=_uk_now2,
                )
            except Exception as e:
                logger.warning("PDF2 session status update failed: %s", e)

    async def _generate_v2_pdf3(self, session: str = "EOD_INSTITUTIONAL") -> None:
        """V2 PDF 3: EOD Review. Fires 22:00 UK — 1 hour after NYSE closes."""
        import uuid
        run_id = str(uuid.uuid4())[:8].upper()
        logger.info("=== V2 PDF3: EOD REVIEW [%s] run=%s ===", session, run_id)
        artifacts_written = False
        pdf_written = False
        error_msg = ""
        _engine_result3 = None
        pdf_path_str3 = ""
        try:
            # Step 1: Run engine + write artifact
            _engine_result3 = self._run_engine_and_write_artifact(session, period="10d")
            artifacts_written = _engine_result3 is not None

            # Step 1b: Write watchdog artifacts (system_state, reliability, quality)
            self._write_watchdog_artifacts_for_session(session, _engine_result3)

            # Step 2: Generate PDF
            if not _PDF_V2_DAILY_REVIEW_AVAILABLE:
                logger.warning("pdf_v2_daily_review not available — falling back to legacy PDF for PDF3")
                try:
                    await self._generate_and_send_pdf_report("EOD-REVIEW")
                    pdf_written = True
                    logger.info("PDF3 legacy fallback completed successfully")
                except Exception as e_fallback:
                    logger.error("PDF3 legacy fallback ALSO failed: %s", e_fallback, exc_info=True)
            else:
                report = _DailyReviewPDFReport()
                pdf_path = report.generate(session=session)
                pdf_path_str3 = str(pdf_path)
                logger.info("V2 PDF3 generated: %s", pdf_path)
                import inspect
                _send = report.send_via_telegram(pdf_path)
                sent = (await _send) if inspect.isawaitable(_send) else _send
                pdf_written = True
                if sent:
                    logger.info("V2 PDF3 (%s) sent to Telegram", session)
                else:
                    logger.warning("V2 PDF3 Telegram delivery failed (saved at %s)", pdf_path)
        except Exception as e:
            error_msg = str(e)[:200]
            logger.error("V2 PDF3 generation failed: %s", e, exc_info=True)
        finally:
            try:
                from signal_engine.signal_card import update_session_status
                from datetime import datetime as _dt3
                try:
                    from zoneinfo import ZoneInfo as _ZI3
                    _uk_now3 = _dt3.now(_ZI3("Europe/London")).isoformat()
                except Exception as e:
                    logger.warning("ZoneInfo unavailable for PDF3 timestamp: %s", e)
                    _uk_now3 = _dt3.utcnow().isoformat()
                update_session_status(
                    session, run_id, artifacts_written, pdf_written, error_msg,
                    pdf_path=pdf_path_str3,
                    signals_strict_count=getattr(_engine_result3, "strict_count", 0),
                    signals_fallback_count=getattr(_engine_result3, "fallback_count", 0),
                    drought_flag=bool(getattr(_engine_result3, "drought", None)),
                    top_blockers=getattr(_engine_result3, "blocker_summary", [])[:3],
                    generated_at_uk=_uk_now3,
                )
            except Exception as e:
                logger.warning("PDF3 session status update failed: %s", e)

    async def _generate_v2_mega_pdf(self) -> None:
        """V2 Mega PDF: Full Project Intelligence Report. Fires 22:30 UK.

        40-80 page analysis: cover, architecture, gate funnel, data quality audit,
        strategy design, scoring explainability, all session play archives,
        command center status, factor/regime, performance calibration, roadmap.
        """
        import uuid
        run_id = str(uuid.uuid4())[:8].upper()
        logger.info("=== V2 MEGA PDF: FULL ANALYSIS REPORT run=%s ===", run_id)
        artifacts_written = False
        pdf_written = False
        error_msg = ""
        _engine_result_mega = None
        pdf_path_str_mega = ""
        try:
            # Step 1: Run engine fresh + write MEGA artifact
            _engine_result_mega = self._run_engine_and_write_artifact("MEGA_EOD", period="10d")
            artifacts_written = _engine_result_mega is not None

            # Step 2: Generate Mega PDF
            try:
                from delivery.mega_report import MegaReport
            except ImportError as e_mega_import:
                logger.error(
                    "delivery.mega_report import FAILED: %s — attempting fallback (generate available V2 PDFs individually)",
                    e_mega_import, exc_info=True,
                )
                # Fallback: run the three individual V2 PDFs instead of one mega
                _fallback_count = 0
                for _fb_gen, _fb_name in [
                    (self._generate_v2_pdf1, "PDF1-momentum"),
                    (self._generate_v2_pdf2, "PDF2-risk"),
                    (self._generate_v2_pdf3, "PDF3-review"),
                ]:
                    try:
                        await _fb_gen()
                        _fallback_count += 1
                    except Exception as e_fb:
                        logger.error("Mega fallback %s failed: %s", _fb_name, e_fb)
                if _fallback_count > 0:
                    pdf_written = True
                    logger.info("Mega PDF fallback: %d/3 individual PDFs generated", _fallback_count)
                else:
                    logger.error("Mega PDF fallback: ALL individual PDFs failed — 0/3 generated")
                    error_msg = f"mega_report import failed ({e_mega_import}); all 3 fallbacks also failed"
                # Skip the normal mega generation path
                raise _MegaFallbackCompleted()

            report = MegaReport()
            pdf_path = report.generate(session="MEGA_EOD")
            pdf_path_str_mega = str(pdf_path)
            logger.info("V2 Mega PDF generated: %s", pdf_path)
            import inspect
            _send = report.send_via_telegram(pdf_path)
            sent = (await _send) if inspect.isawaitable(_send) else _send
            pdf_written = True
            if sent:
                logger.info("V2 Mega PDF sent to Telegram")
            else:
                logger.warning("V2 Mega PDF Telegram delivery failed (saved at %s)", pdf_path)
        except _MegaFallbackCompleted:
            pass  # Fallback already handled above — proceed to finally
        except Exception as e:
            error_msg = str(e)[:200]
            logger.error("V2 Mega PDF generation failed: %s", e, exc_info=True)
        finally:
            try:
                from signal_engine.signal_card import update_session_status
                from datetime import datetime as _dtm
                try:
                    from zoneinfo import ZoneInfo as _ZIm
                    _uk_nowm = _dtm.now(_ZIm("Europe/London")).isoformat()
                except Exception as e:
                    logger.warning("ZoneInfo unavailable for Mega PDF timestamp: %s", e)
                    _uk_nowm = _dtm.utcnow().isoformat()
                update_session_status(
                    "MEGA_EOD", run_id, artifacts_written, pdf_written, error_msg,
                    pdf_path=pdf_path_str_mega,
                    signals_strict_count=getattr(_engine_result_mega, "strict_count", 0),
                    signals_fallback_count=getattr(_engine_result_mega, "fallback_count", 0),
                    drought_flag=bool(getattr(_engine_result_mega, "drought", None)),
                    top_blockers=getattr(_engine_result_mega, "blocker_summary", [])[:3],
                    generated_at_uk=_uk_nowm,
                )
            except Exception as e:
                logger.warning("Mega PDF session status update failed: %s", e)

    # ── 110/100 New PDF Session Wrappers ──────────────────────────────────

    async def _generate_overnight_risk_pdf(self) -> None:
        """Overnight Risk Brief — 06:30 UK. Pre-LSE risk assessment."""
        logger.info("=== OVERNIGHT RISK PDF (06:30 UK) ===")
        try:
            from scheduled_jobs import run_overnight_risk_session
            run_overnight_risk_session()
            logger.info("Overnight Risk PDF generated successfully")
        except ImportError:
            logger.warning("Overnight Risk PDF module not available")
        except Exception as e:
            logger.error("Overnight Risk PDF generation failed: %s", e, exc_info=True)

    async def _generate_mid_session_pdf(self) -> None:
        """Mid-Session Risk Brief — 16:40 UK. Post-LSE close + NYSE midday."""
        logger.info("=== MID-SESSION RISK PDF (16:40 UK) ===")
        try:
            from scheduled_jobs import run_mid_session_risk
            run_mid_session_risk()
            logger.info("Mid-Session Risk PDF generated successfully")
        except ImportError:
            logger.warning("Mid-Session Risk PDF module not available")
        except Exception as e:
            logger.error("Mid-Session Risk PDF generation failed: %s", e, exc_info=True)

    async def _generate_master_spec_pdf(self) -> None:
        """Master Spec Document — 00:00 UK. Full system specification."""
        logger.info("=== MASTER SPEC PDF (00:00 UK) ===")
        try:
            from scheduled_jobs import run_master_spec
            run_master_spec()
            logger.info("Master Spec PDF generated successfully")
        except ImportError:
            logger.warning("Master Spec PDF module not available")
        except Exception as e:
            logger.error("Master Spec PDF generation failed: %s", e, exc_info=True)

    async def _resolve_signal_outcomes(self) -> None:
        """Resolve PENDING signals every 15 minutes using path-based outcome engine."""
        logger.info("=== OUTCOME RESOLVER ===")
        try:
            from learning.outcomes_engine import OutcomeEngine
            oe = OutcomeEngine()
            stats = oe.resolve_all_pending(max_signals=50)
            if stats and stats.get("resolved", 0) > 0:
                logger.info("Outcomes resolved: %s", stats)
        except ImportError:
            logger.debug("OutcomeEngine not available")
        except Exception as e:
            logger.warning("Outcome resolver failed: %s", e)

    async def _resolve_outcomes_eod(self) -> None:
        """End-of-day batch resolution of all pending outcomes at 22:00 UTC."""
        logger.info("=== EOD OUTCOME RESOLUTION ===")
        try:
            from learning.outcomes_engine import OutcomeEngine
            oe = OutcomeEngine()
            # Resolve all pending — no max_signals cap for EOD sweep
            result = oe.resolve_all_pending(max_signals=500)
            logger.info("EOD outcome resolution: %s", result)
        except ImportError:
            logger.debug("OutcomeEngine not available")
        except Exception as e:
            logger.warning("EOD outcome resolution failed: %s", e)

    async def _rebuild_edge_ledger(self) -> None:
        """Nightly edge ledger rebuild — updates per-bucket win rates."""
        logger.info("=== EDGE LEDGER REBUILD ===")
        try:
            from learning.edge_ledger import get_edge_ledger
            el = get_edge_ledger()
            result = el.rebuild()
            logger.info("Edge ledger rebuilt: %s", result)
        except ImportError:
            logger.debug("EdgeLedger not available")
        except Exception as e:
            logger.warning("Edge ledger rebuild failed: %s", e)

    async def _run_signal_engine_periodic(self) -> None:
        """Run Signal Engine every 15 minutes to keep War Room fresh."""
        if self.kill_switch.is_killed():
            return
        try:
            pipeline_result = run_pipeline(
                session="PERIODIC",
                period="5d",
                regime="NEUTRAL",
                is_preview=False,
                generate_intel=False,
                n_plays_min=3,
                n_plays_max=15,
            )
            if pipeline_result.engine_result:
                self._update_war_room_state(pipeline_result.engine_result)
                logger.info("[PERIODIC] plays=%d strict=%d fallback=%d",
                            len(pipeline_result.engine_result.plays),
                            pipeline_result.strict_count,
                            pipeline_result.fallback_count)
        except Exception as e:
            logger.warning("Periodic engine run failed: %s", e)

    async def _generate_preview_pdfs(self) -> None:
        """Generate all 3 preview PDFs on demand."""
        logger.info("=== PREVIEW PDF GENERATION ===")
        for session, pdf_type in [
            ("PRE_LSE", "momentum"),
            ("PRE_NYSE", "risk"),
            ("EOD_INSTITUTIONAL", "review"),
        ]:
            try:
                # Run pipeline first (preview mode)
                pipeline_result = run_pipeline(
                    session=session,
                    period="5d" if session != "EOD_INSTITUTIONAL" else "10d",
                    is_preview=True,
                    generate_intel=True,
                )
                # Then generate preview PDF
                pdf_path = generate_preview_pdf(session, pdf_type)
                if pdf_path:
                    logger.info("Preview PDF generated: %s", pdf_path)
                else:
                    logger.warning("Preview PDF failed for %s", session)
            except Exception as e:
                logger.error("Preview PDF %s failed: %s", session, e)

    async def _refresh_lse_registry(self) -> None:
        """Daily LSE registry refresh at 07:30 UK — discovers new/delisted products."""
        logger.info("=== LSE REGISTRY REFRESH ===")
        try:
            from uk_isa.lse_registry import LSERegistry
            registry = LSERegistry()
            registry.refresh()
            logger.info("LSE registry refreshed")
        except Exception as e:
            logger.warning("LSE registry refresh failed: %s", e)

    _SECTOR_ROTATION_COOLDOWN = 7200         # 2 hours between same-rotation alerts (was 1hr, too spammy)
    _SECTOR_ROTATION_MIN_CONFIDENCE = 0.50   # ignore below 50% (was 30%, too noisy)
    _SECTOR_ROTATION_COOLDOWN_FILE = "/tmp/nzt48_rotation_cooldowns.json"

    def _load_rotation_cooldowns(self) -> dict:
        """Load rotation cooldowns from disk (survives restarts)."""
        import json
        try:
            with open(self._SECTOR_ROTATION_COOLDOWN_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return {}

    def _save_rotation_cooldowns(self, data: dict) -> None:
        """Persist rotation cooldowns to disk."""
        import json
        try:
            with open(self._SECTOR_ROTATION_COOLDOWN_FILE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    async def _run_sector_rotation_scan(self) -> None:
        """Sector rotation scan every 60 seconds — detects leadership shifts in live data."""
        logger.info("=== SECTOR ROTATION SCAN ===")
        try:
            from uk_isa.sector_rotation import get_radar
            import time
            radar = get_radar()
            snapshot = radar.scan()
            if snapshot.transition_alert:
                ta = snapshot.transition_alert

                # Gate 1: minimum confidence threshold
                if ta.confidence < self._SECTOR_ROTATION_MIN_CONFIDENCE:
                    logger.info("Rotation %s->%s suppressed (confidence %.0f%% < %.0f%% min)",
                                ta.old_leader, ta.new_leader,
                                ta.confidence * 100, self._SECTOR_ROTATION_MIN_CONFIDENCE * 100)
                    return

                # Gate 2: cooldown per unique rotation (persisted to disk)
                cooldowns = self._load_rotation_cooldowns()
                rotation_key = f"{ta.old_leader}->{ta.new_leader}"
                now = time.time()
                last_sent = cooldowns.get(rotation_key, 0)
                if now - last_sent < self._SECTOR_ROTATION_COOLDOWN:
                    logger.info("Rotation %s suppressed (cooldown %ds remaining)",
                                rotation_key, int(self._SECTOR_ROTATION_COOLDOWN - (now - last_sent)))
                    return

                _conf_grade = "HIGH" if ta.confidence >= 0.75 else ("MEDIUM" if ta.confidence >= 0.55 else "LOW")
                _sep38 = "─" * 38
                msg = (
                    f"🔄 SECTOR ROTATION — {_conf_grade} CONFIDENCE\n"
                    f"{_sep38}\n"
                    f"From  : {ta.old_leader}\n"
                    f"To    : {ta.new_leader}\n"
                    f"Signal: {ta.confidence:.0%} confidence\n"
                    f"Watch : {', '.join(ta.instruments_to_watch[:3])}\n"
                    f"\nAction: {ta.actionable_insight}"
                )
                await self.telegram.send_alert(msg)
                cooldowns[rotation_key] = now
                self._save_rotation_cooldowns(cooldowns)
                logger.info("Rotation alert sent: %s -> %s (confidence %.0f%%)",
                            ta.old_leader, ta.new_leader, ta.confidence * 100)
            else:
                logger.info("Sector scan complete. Leader: %s", snapshot.current_leader)
        except Exception as e:
            logger.warning("Sector rotation scan failed: %s", e)

    # ── END V8.0 METHODS ─────────────────────────────────────────────────────

    async def _run_nightly_intelligence(self) -> None:
        """Run the nightly AI-powered intelligence cycle after market close.

        Now includes: performance analytics, strategy P&L attribution,
        missed trade filter analysis, firewall effectiveness report,
        and comprehensive digest sent to Telegram.
        """
        logger.info("=== NIGHTLY INTELLIGENCE CYCLE ===")
        try:
            with transaction() as conn:
                report = await self.adaptive_intel.run_nightly_cycle(
                    conn, learning_engine=self.learning,
                )
                # Send report to Telegram (via event bus P3 → nightly digest)
                try:
                    if self._tg_bus:
                        self._tg_bus.emit("P3", report.to_telegram(), "intelligence")
                    else:
                        await self.telegram.send_alert(report.to_telegram())
                except Exception as e:
                    logger.error("Failed to send intelligence report: %s", e)

                # Send tournament standings (via event bus P3 → nightly digest)
                try:
                    if self._tg_bus:
                        self._tg_bus.emit("P3", self.tournament.to_telegram(), "performance")
                    else:
                        await self.telegram.send_alert(self.tournament.to_telegram())
                except Exception as e:
                    logger.error("Failed to send tournament standings: %s", e)

                # === PERFORMANCE ANALYTICS — institutional-grade metrics ===
                try:
                    from learning.performance_analytics import PerformanceAnalytics
                    analytics = PerformanceAnalytics()
                    perf_report = analytics.compute(conn, days=30)
                    if self._tg_bus:
                        self._tg_bus.emit("P3", perf_report.to_telegram(), "performance")
                    else:
                        await self.telegram.send_alert(perf_report.to_telegram())

                    # Persist per-strategy daily stats
                    from delivery.database import insert_strategy_daily_stats
                    today_stats = analytics.compute_strategy_daily_stats(conn)
                    for strat, stats in today_stats.items():
                        insert_strategy_daily_stats(conn, stats)
                    logger.info("Strategy daily stats persisted for %d strategies", len(today_stats))
                except Exception as e:
                    logger.error("Performance analytics failed: %s", e)

                # === NIGHTLY DIGEST — comprehensive daily report ===
                try:
                    digest = self._build_nightly_digest(conn)
                    await self.telegram.send_nightly_digest(digest)
                except Exception as e:
                    logger.error("Nightly digest failed: %s", e)

                # === WIRE DISCONNECTED LEARNING MODULES ===
                # These 4 modules were built but never scheduled. Now they run nightly.

                # 1. OUTCOME RESOLUTION — resolve all pending signal outcomes
                # Without this, edge_ledger, drift_detector, and meta_learner are starved
                try:
                    from learning.outcomes_engine import get_outcome_engine
                    outcome_engine = get_outcome_engine()
                    resolved = outcome_engine.resolve_all_pending()
                    logger.info("NIGHTLY: Resolved %d pending signal outcomes", len(resolved))
                except Exception as oe_err:
                    logger.warning("Outcome resolution failed (nightly): %s", oe_err)

                # 2. EDGE LEDGER REBUILD — refresh edge statistics from resolved outcomes
                try:
                    from learning.edge_ledger import get_edge_ledger
                    ledger = get_edge_ledger()
                    ledger.rebuild()
                    logger.info("NIGHTLY: Edge ledger rebuilt with fresh outcome data")
                except Exception as el_err:
                    logger.warning("Edge ledger rebuild failed (nightly): %s", el_err)

                # 3. META-LEARNER UPDATE — rebalance strategy weights based on edge evidence
                # This was NEVER called despite being fully implemented
                try:
                    from learning.meta_learner import get_meta_learner
                    ml = get_meta_learner()
                    current_regime = self._current_market_ctx.regime.value if self._current_market_ctx else "UNKNOWN"
                    new_weights = ml.update(regime_tag=current_regime)
                    logger.info("NIGHTLY: MetaLearner updated — weights rebalanced for regime=%s", current_regime)
                except Exception as ml_err:
                    logger.warning("MetaLearner update failed (nightly): %s", ml_err)

                # 4. DRIFT DETECTION — detect feature/hit-rate drift and trigger defensive mode
                # Uses self.v32_drift_detector (global singleton — consolidated from local instance)
                try:
                    drift_instance = self.v32_drift_detector
                    if drift_instance is None:
                        # Fallback: local instantiation if global unavailable
                        from learning.drift import DriftDetector
                        drift_instance = DriftDetector()
                    drift_report = drift_instance.run_all()
                    if drift_report and drift_report.get("alerts"):
                        alert_msg = "⚠️ DRIFT DETECTED\n" + "\n".join(
                            f"• {a}" for a in drift_report["alerts"][:5]
                        )
                        if self._tg_bus:
                            self._tg_bus.emit("P1", alert_msg, "system")
                        else:
                            await self.telegram.send_alert(alert_msg)
                        logger.warning("NIGHTLY: Drift alerts: %s", drift_report["alerts"])
                    else:
                        logger.info("NIGHTLY: No drift detected — system stable")
                except Exception as dd_err:
                    logger.warning("Drift detection failed (nightly): %s", dd_err)

                # 5. CALIBRATION ENGINE — auto-apply safe, bounded parameter suggestions
                # Applies ONLY suggestions marked safe=True with sufficient evidence.
                # All changes bounded by hard limits (MAX_CHANGE_PER_CYCLE = 0.05).
                try:
                    from learning.calibration import CalibrationEngine
                    calibration = CalibrationEngine()
                    suggestions = calibration.get_suggestions()
                    applied_count = 0
                    for suggestion in suggestions:
                        if suggestion.safe and suggestion.bounded:
                            param = suggestion.param_name
                            old_val = suggestion.current
                            new_val = suggestion.suggested
                            # Guardrails check — extra safety validation
                            guard_ok, guard_reason = check_guardrails(
                                param, old_val, new_val,
                                n_outcomes=suggestion.evidence.split()[0] if suggestion.evidence else "0",
                            )
                            if not guard_ok:
                                logger.warning("GUARDRAILS: Blocked %s change: %s", param, guard_reason)
                                continue
                            # Apply bounded suggestion to running config
                            config_key = f"qualification.{param.lower()}"
                            cfg.set(config_key, new_val)
                            applied_count += 1
                            logger.info(
                                "CALIBRATION: Auto-applied %s: %.3f → %.3f (%s)",
                                param, old_val, new_val, suggestion.evidence,
                            )
                    if applied_count > 0:
                        _cal_lines = [
                            f"  {s.param_name:<28} {s.current:.3f} → {s.suggested:.3f}  ({s.evidence})"
                            for s in suggestions if s.safe and s.bounded
                        ]
                        _cal_msg = (
                            f"⚙️ AUTO-CALIBRATION APPLIED\n"
                            f"{'-'*38}\n"
                            f"Parameters updated: {applied_count}\n"
                            f"Evidence threshold: STRONG (100+ trades)\n"
                            f"{'-'*38}\n"
                            + "\n".join(_cal_lines) +
                            f"\n{'-'*38}\n"
                            f"All changes bounded: max ±5% per cycle.\n"
                            f"Review: /api/attribution for impact."
                        )
                        if self._tg_bus:
                            self._tg_bus.emit("P3", _cal_msg, "learning")
                        else:
                            await self.telegram.send_alert(_cal_msg)
                    else:
                        logger.info("NIGHTLY: No calibration changes needed (all within bounds)")
                except Exception as cal_err:
                    logger.warning("Calibration engine failed (nightly): %s", cal_err)

                # 6. PERFORMANCE ATTRIBUTION — generate and send daily attribution report
                try:
                    attr_history = getattr(self.perf_attribution, '_attribution_history', [])
                    if attr_history:
                        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        today_attrs = [
                            a for a in attr_history
                            if str(a.get("entry_time", ""))[:10] == today_str
                        ]
                        if today_attrs:
                            report = self.perf_attribution.generate_daily_report(today_attrs)
                            if report:
                                _attr_msg = f"📈 DAILY ATTRIBUTION\n{report[:800]}"
                                if self._tg_bus:
                                    self._tg_bus.emit("P3", _attr_msg, "performance")
                                else:
                                    await self.telegram.send_alert(_attr_msg)
                                logger.info("NIGHTLY: Sent daily attribution report (%d trades)", len(today_attrs))
                except Exception as pa_err:
                    logger.warning("Performance attribution report failed (nightly): %s", pa_err)

                # 7. ADAPTIVE LEARNING ENGINE — daily report + auto-improve + playbook save
                try:
                    learning_report = self.adaptive_engine.generate_daily_report()
                    self.adaptive_engine.save_playbook()
                    logger.info("NIGHTLY: Adaptive learning report: trades=%d, edge=%s",
                                learning_report.get("cumulative_trades", 0),
                                learning_report.get("overall_edge", "N/A"))
                    # Send key insights to Telegram
                    insights = []
                    for ri in learning_report.get("regime_insights", [])[:3]:
                        insights.append(f"  {ri}")
                    for ti in learning_report.get("ticker_insights", [])[:3]:
                        insights.append(f"  {ti}")
                    if learning_report.get("stop_suggestion"):
                        insights.append(f"  {learning_report['stop_suggestion']}")
                    if insights:
                        _total_t = learning_report.get('cumulative_trades', 0)
                        _edge = learning_report.get('overall_edge', 'N/A')
                        _learn_msg = (
                            f"\U0001f9e0 ADAPTIVE LEARNING ENGINE\n"
                            f"{'─'*38}\n"
                            f"Cumulative trades  : {_total_t:,}\n"
                            f"System edge        : {_edge}\n"
                            f"Insights:\n"
                            + "\n".join(f"  \u2022 {i}" for i in insights)
                        )
                        if self._tg_bus:
                            self._tg_bus.emit("P3", _learn_msg, "learning")
                        else:
                            await self.telegram.send_alert(_learn_msg)

                    # CONTINUOUS IMPROVEMENT LOOP
                    # "Today's excellence is tomorrow's average"
                    # Run improvement analysis daily. Apply only when evidence is STRONG (100+ trades).
                    improvement_candidates = self.adaptive_engine.compute_improvement_candidates()
                    logger.info(
                        "NIGHTLY: Improvement analysis: evidence=%s, %d changes candidate",
                        improvement_candidates.get("evidence_quality", "INSUFFICIENT"),
                        len(improvement_candidates.get("parameter_changes", [])),
                    )
                    applied_improvements = self.adaptive_engine.auto_apply_improvements(min_evidence="STRONG")
                    if applied_improvements:
                        _imp_msg = (
                            "AUTO-IMPROVEMENT APPLIED\n"
                            + f"{len(applied_improvements)} parameter(s) updated:\n"
                            + "\n".join(
                                f"  {c['param']}: {c['current']} → {c['recommended']} "
                                f"(OOS WR={c['oos_win_rate']:.0%})"
                                for c in applied_improvements
                            )
                            + "\n\nToday's excellence is tomorrow's average."
                        )
                        if self._tg_bus:
                            self._tg_bus.emit("P3", _imp_msg, "learning")
                        else:
                            await self.telegram.send_alert(_imp_msg)

                    # Daily Excellence Brief (Telegram)
                    excellence_brief = self.adaptive_engine.get_daily_excellence_brief()
                    logger.info("NIGHTLY: Excellence brief:\n%s", excellence_brief)
                    if self._tg_bus:
                        self._tg_bus.emit("P3", excellence_brief, "performance")
                    else:
                        await self.telegram.send_alert(excellence_brief)

                    # Per-ticker indicator ranking report (Fridays only — enough weekly data)
                    from core.clock import now_uk as _now_uk_fri
                    if _now_uk_fri().weekday() == 4:  # Friday = 4
                        ind_report = self.adaptive_engine.get_indicator_ranking_report()
                        logger.info("NIGHTLY: Indicator rankings:\n%s", ind_report)
                        if self._tg_bus:
                            self._tg_bus.emit("P3", ind_report, "intelligence")
                        else:
                            await self.telegram.send_alert(ind_report)

                except Exception as ae_err:
                    logger.warning("Adaptive engine report failed (nightly): %s", ae_err)

                # W12 Ensemble Diversity System: nightly retrain (Dietterich 2000)
                # LightGBM + XGBoost + PA stacking. Diversity measured by Q-statistic.
                # Active Learning weights (Settles 2009) applied during training.
                if self.ensemble_diversity:
                    try:
                        recent_outcomes = self._get_recent_outcomes(n=500)
                        if len(recent_outcomes) >= 20:
                            # Compute active learning weights for sample prioritisation
                            sample_weights = None
                            if self.active_learning:
                                try:
                                    sample_weights = self.active_learning.get_learning_weights(
                                        recent_outcomes
                                    )
                                except Exception:
                                    pass
                            result = self.ensemble_diversity.train_all(
                                recent_outcomes, sample_weights=sample_weights
                            )
                            if "error" not in result:
                                diversity = result.get("diversity_scores", {})
                                acc = result.get("ensemble_accuracy")
                                logger.info(
                                    "NIGHTLY: Ensemble retrained — acc=%.1f%% diversity=%s",
                                    (acc or 0) * 100, diversity,
                                )
                                # Send to Telegram if accuracy is notable
                                if acc is not None:
                                    _ens_msg = (
                                        f"🧬 ENSEMBLE RETRAIN\n"
                                        f"{'─'*38}\n"
                                        f"Accuracy   : {acc:.1%}\n"
                                        f"Samples    : {len(recent_outcomes)}\n"
                                        f"Diversity  : {', '.join(f'{k}={v:.2f}' for k, v in list(diversity.items())[:3])}\n"
                                        f"(Dietterich 2000: Q-stat diversity retained)"
                                    )
                                    if self._tg_bus:
                                        self._tg_bus.emit("P3", _ens_msg, "learning")
                                    else:
                                        await self.telegram.send_alert(_ens_msg)
                            else:
                                logger.info("NIGHTLY: Ensemble retrain skipped — %s", result.get("error"))
                        else:
                            logger.info(
                                "NIGHTLY: Ensemble retrain skipped — only %d outcomes (need 20)",
                                len(recent_outcomes),
                            )
                    except Exception as _ens_err:
                        logger.warning("Ensemble diversity nightly retrain failed: %s", _ens_err)

                # Save all learning module state
                self._save_all_learning_state(conn)

                # W12 Self-Learning Engine nightly summary
                try:
                    w12_summary = self.learning.get_w12_telegram_summary()
                    if self._tg_bus:
                        self._tg_bus.emit("P3", w12_summary, "learning")
                    else:
                        await self.telegram.send_alert(w12_summary)
                except Exception as _w12_tg_err:
                    logger.warning("W12 Telegram summary failed: %s", _w12_tg_err)

                # AI Research Engine: nightly pending suggestions summary
                # Ensures operator sees any queued suggestions before next trading session
                if self.ai_research:
                    try:
                        pending = self.ai_research.get_pending_suggestions()
                        if pending:
                            summary = self.ai_research.get_telegram_suggestions_summary()
                            if self._tg_bus:
                                self._tg_bus.emit("P3", summary, "intelligence")
                            else:
                                await self.telegram.send_alert(summary)
                            logger.info(
                                "AI RESEARCH: Sent %d pending suggestion(s) to Telegram",
                                len(pending),
                            )
                    except Exception as _air_nightly_err:
                        logger.warning("AI Research nightly summary failed: %s", _air_nightly_err)

                # Record daily System IQ — compute fresh score then log daily trend
                try:
                    self.learning._update_system_iq()
                except Exception:
                    pass  # IQ compute is advisory
                iq_record = self.learning.system_iq.record_daily()
                try:
                    _iq_msg = self.learning.system_iq.get_telegram_message()
                    if self._tg_bus:
                        self._tg_bus.emit("P3", _iq_msg, "system")
                    else:
                        await self.telegram.send_alert(_iq_msg)
                    if self.learning.system_iq.is_declining():
                        _iq_decline_msg = (
                            f"\u26a0\ufe0f SYSTEM IQ DECLINING\n"
                            f"{'─'*38}\n"
                            f"Learning velocity has turned negative.\n"
                            f"Required action:\n"
                            f"  1. Review last 20 trades for execution vs edge issues\n"
                            f"  2. Check indicator accuracy leaderboard for decay\n"
                            f"  3. Confirm regime classification is current\n"
                            f"  4. Consider pausing auto-improvement for 24h\n"
                            f"This alert clears automatically when IQ trend reverses."
                        )
                        if self._tg_bus:
                            self._tg_bus.emit("P1", _iq_decline_msg, "system")
                        else:
                            await self.telegram.send_alert(_iq_decline_msg)
                except Exception as e:
                    logger.error("Failed to send System IQ: %s", e)

                # === DISCIPLINE ENGINE: End-of-day report ===
                try:
                    if self.discipline.state.trades_today == 0:
                        no_trade = self.discipline.record_no_trade_day()
                        logger.info("DISCIPLINE: %s", no_trade["message"])
                    report = self.discipline.get_excellence_report()
                    _pnl_pct = report.get('daily_pnl_pct', 0) or 0
                    _pnl_arrow = "▲" if _pnl_pct > 0 else ("▼" if _pnl_pct < 0 else "—")
                    _wr = report.get('rolling_win_rate', 0) or 0
                    _wr_grade = "A" if _wr >= 0.60 else ("B" if _wr >= 0.50 else ("C" if _wr >= 0.42 else "D"))
                    _trades = report.get('trades_today', 0)
                    _cw = report.get('consecutive_wins', 0)
                    _cl = report.get('consecutive_losses', 0)
                    disc_msg = (
                        f"📋 DAILY CLOSE — DISCIPLINE & EXECUTION REPORT\n"
                        f"{'─'*38}\n"
                        f"Sessions traded : {_trades}  |  "
                        f"Daily P&L: {_pnl_arrow} {abs(_pnl_pct):.2f}%\n"
                        f"Rolling WR      : {_wr*100:.1f}% (Grade: {_wr_grade}) "
                        f"[{report.get('rolling_trades',0)}/{report.get('target_trades',0)} sample]\n"
                        f"Streak          : +{_cw}W / -{_cl}L"
                    )
                    if _trades == 0:
                        _no_trade = report.get('no_trade_days', 0)
                        disc_msg += (
                            f"\n\n⏸  NO-TRADE DAY  (Patience count: {_no_trade})\n"
                            f"   System scanned all sessions. No setup cleared all gates.\n"
                            f"   Capital preserved. Edge protection is execution excellence."
                        )
                    if report.get('in_cooldown'):
                        disc_msg += (
                            f"\n\n⛔ COOLDOWN PROTOCOL ACTIVE\n"
                            f"   Risk limit reached. No new positions until cooldown clears.\n"
                            f"   This is the system working correctly."
                        )
                    if _cl >= 3:
                        disc_msg += (
                            f"\n\n⚠️  CONSECUTIVE LOSS FLAG: {_cl} losses\n"
                            f"   Review last {_cl} setups for execution issues vs edge decay.\n"
                            f"   Next trade: confidence floor +10 required."
                        )
                    elif _cw >= 3:
                        disc_msg += (
                            f"\n\n✅ MOMENTUM: {_cw} consecutive wins — edge confirmed operational."
                        )
                    disc_msg += f"\n{'─'*38}"
                    if self._tg_bus:
                        self._tg_bus.emit("P3", disc_msg, "performance")
                    else:
                        await self.telegram.send_alert(disc_msg)
                    logger.info("DISCIPLINE: EOD report sent to Telegram")
                except Exception as disc_eod_err:
                    logger.warning("Discipline EOD report failed: %s", disc_eod_err)

                # === T-04: NIGHTLY GPD TAIL RISK BATCH ===
                # Compute GPD tail risk for all tradable tickers via yfinance daily returns.
                # Cache results in Redis with 24h TTL for fast intraday lookups by S15.
                # Also clears VIX emergency veto after successful batch completion.
                try:
                    import json as _json_gpd
                    import numpy as _np_gpd
                    from core.evt import TailRiskMonitor
                    import redis as _sync_redis_gpd

                    _gpd_redis = _sync_redis_gpd.Redis(
                        host='redis', port=6379,
                        password='nzt48redis', decode_responses=True,
                        socket_connect_timeout=3,
                    )
                    _gpd_monitor = TailRiskMonitor()
                    _gpd_computed = 0
                    _gpd_vetoed = 0

                    # Get all tradable tickers
                    from config.isa_universe import EXTENDED_UNIVERSE as _GPD_TICKERS
                    for _gpd_ticker in _GPD_TICKERS:
                        try:
                            _hist = self._data_feeds.get_daily_bars(_gpd_ticker, days=270) if self._data_feeds else None
                            if _hist is None or len(_hist) < 50:
                                continue
                            _closes = _hist["Close"].dropna().values.astype(float)
                            if len(_closes) < 50:
                                continue
                            _returns = _np_gpd.diff(_np_gpd.log(_closes))
                            if len(_returns) < 50:
                                continue
                            _veto, _reason = _gpd_monitor.veto_signal(_gpd_ticker, _returns)
                            _gpd_redis.setex(
                                f"nzt:gpd:{_gpd_ticker}", 86400,
                                _json_gpd.dumps({"veto": _veto, "reason": _reason}),
                            )
                            _gpd_computed += 1
                            if _veto:
                                _gpd_vetoed += 1
                        except Exception as _gpd_ticker_err:
                            logger.warning(
                                "T-04 GPD: %s failed: %s", _gpd_ticker, _gpd_ticker_err,
                            )

                    logger.info(
                        "T-04 GPD NIGHTLY BATCH: computed %d/%d tickers, %d vetoed",
                        _gpd_computed, len(_GPD_TICKERS), _gpd_vetoed,
                    )

                    # Clear VIX emergency veto after successful batch
                    for strat in self._strategies:
                        if hasattr(strat, '_emergency_tail_risk_veto'):
                            if strat._emergency_tail_risk_veto:
                                logger.info("T-04: clearing VIX emergency veto after successful GPD batch")
                            strat._emergency_tail_risk_veto = False

                except Exception as _gpd_batch_err:
                    logger.warning("T-04 GPD nightly batch failed: %s", _gpd_batch_err)

                # === TELEGRAM EVENT BUS: flush P3 queue → single nightly digest ===
                # All P3 events queued during the nightly cycle are consolidated here.
                # This replaces 16 individual send_alert() calls with 1 structured message.
                # (Hyman et al. 2019 — alert fatigue: >15/day degrades decision quality 47%)
                try:
                    if self._tg_bus is not None:
                        digest_text = self._tg_bus.flush_digest()
                        if digest_text:
                            await self.telegram.send_alert(digest_text)
                            logger.info("NIGHTLY: TelegramEventBus digest sent (%d chars)", len(digest_text))
                except Exception as _bus_err:
                    logger.warning("Nightly digest flush failed: %s", _bus_err)

        except Exception as e:
            logger.error("Nightly intelligence cycle failed: %s", e)

        # V8.0 StateManager: nightly reconciliation + daily P&L reset
        # Runs AFTER the intelligence cycle regardless of success/failure above
        await self._state_manager_reconcile_nightly()

    def _build_nightly_digest(self, conn) -> dict:
        """Build the comprehensive nightly digest for Telegram delivery."""
        digest = {
            "strategy_pnl": {},
            "missed_trade_summary": {},
            "firewall_summary": {},
            "filter_analysis": "",
            "top_autopsy_lesson": "",
        }

        # Per-strategy P&L today
        try:
            rows = conn.execute(
                """SELECT strategy, COUNT(*) as trades,
                   SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(net_pnl) as pnl,
                   AVG(r_multiple) as avg_r
                   FROM virtual_trades WHERE date(exit_time) = date('now')
                   GROUP BY strategy ORDER BY pnl DESC"""
            ).fetchall()
            for row in rows:
                digest["strategy_pnl"][row["strategy"]] = {
                    "pnl": row["pnl"] or 0,
                    "trades": row["trades"],
                    "wins": row["wins"],
                    "avg_r": row["avg_r"] or 0,
                }
        except Exception as e:
            logger.warning("Nightly digest strategy PnL query failed: %s", e)

        # Missed trade filter analysis
        try:
            missed = conn.execute(
                """SELECT COUNT(*) as total,
                   SUM(CASE WHEN outcome = 'WINNER' THEN 1 ELSE 0 END) as winners,
                   SUM(hypothetical_r) as edge_lost,
                   rejection_reason
                   FROM missed_trades WHERE date(timestamp) = date('now')
                   GROUP BY rejection_reason"""
            ).fetchall()
            total_blocked = sum(r["total"] for r in missed) if missed else 0
            total_winners = sum(r["winners"] or 0 for r in missed) if missed else 0
            edge_lost = sum(r["edge_lost"] or 0 for r in missed) if missed else 0
            worst_filter = ""
            worst_winners = 0
            for r in (missed or []):
                if (r["winners"] or 0) > worst_winners:
                    worst_winners = r["winners"] or 0
                    worst_filter = r["rejection_reason"] or ""
            digest["missed_trade_summary"] = {
                "total_blocked": total_blocked,
                "would_have_won": total_winners,
                "edge_lost_r": edge_lost,
                "worst_filter": worst_filter,
            }
        except Exception as e:
            logger.warning("Nightly digest missed trade analysis failed: %s", e)

        # Firewall events today
        try:
            from delivery.database import get_firewall_events_today
            fw_events = get_firewall_events_today(conn)
            patterns = {}
            for ev in fw_events:
                p = ev["pattern"]
                patterns[p] = patterns.get(p, 0) + 1
            digest["firewall_summary"] = {
                "total_blocks": len(fw_events),
                "patterns": patterns,
            }
        except Exception as e:
            logger.warning("Nightly digest firewall events query failed: %s", e)

        # Top autopsy lesson from today
        try:
            autopsy_row = conn.execute(
                """SELECT primary_lesson FROM trade_autopsies
                   WHERE date(created_at) = date('now')
                   ORDER BY overall_grade ASC LIMIT 1"""
            ).fetchone()
            if autopsy_row and autopsy_row["primary_lesson"]:
                digest["top_autopsy_lesson"] = autopsy_row["primary_lesson"]
        except Exception as e:
            logger.warning("Nightly digest autopsy lesson query failed: %s", e)

        # System IQ calculation
        try:
            from learning.edge_ledger import get_edge_ledger
            el = get_edge_ledger()
            ledger = el.load()
            if ledger:
                actionable = [v for v in ledger.values() if getattr(v, 'status', '') == 'ACTIONABLE']
                avg_wr = sum(getattr(v, 'win_rate', 0) for v in actionable) / len(actionable) if actionable else 0
                system_iq = round(avg_wr * 100, 1)
                logger.info("SYSTEM IQ: %.1f (from %d actionable buckets)", system_iq, len(actionable))
        except Exception as iq_err:
            logger.warning("System IQ calculation failed: %s", iq_err)

        return digest

    async def _record_equity_snapshot_intraday(self) -> None:
        """Record an hourly intraday equity snapshot for drawdown tracking."""
        try:
            from delivery.database import insert_equity_intraday
            unrealised = sum(
                p.unrealised_pnl for p in self.virtual_trader.open_positions.values()
                if p.status == "OPEN"
            )
            open_count = sum(
                1 for p in self.virtual_trader.open_positions.values()
                if p.status == "OPEN"
            )
            regime = self._current_market_ctx.regime.value if self._current_market_ctx else ""

            with transaction() as conn:
                insert_equity_intraday(conn, {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "equity": self.virtual_trader.equity,
                    "unrealised_pnl": unrealised,
                    "realised_pnl_today": self.virtual_trader.daily_pnl,
                    "open_positions": open_count,
                    "max_drawdown_intraday_pct": 0.0,  # Computed by analytics
                    "regime": regime,
                })
            logger.debug("Intraday equity snapshot: $%.2f (unrealised: $%.2f)",
                        self.virtual_trader.equity, unrealised)
        except Exception as e:
            logger.warning("Intraday equity snapshot failed: %s", e)

    async def _run_go_nogo_check(self) -> None:
        """Run daily Go/No-Go scorecard evaluation."""
        try:
            with transaction() as conn:
                scorecard = self.go_nogo.evaluate(conn)
                msg = self.go_nogo.to_telegram(scorecard)
                await self.telegram.send_alert(msg)
                logger.info(
                    "GO/NO-GO: %d/%d criteria met",
                    scorecard.met_count, scorecard.total_count,
                )
        except Exception as e:
            logger.error("Go/No-Go check failed: %s", e)

    # ── V3.2 NEW SCHEDULED METHODS ─────────────────────────────────────────────

    async def _run_performance_relegation(self) -> None:
        """W2: Nightly A/B team ticker scoring at 23:30 UTC.
        Emits Telegram demotion vote alerts — human must confirm within 24h.
        Auto-cancels (does NOT auto-demote) at 24h if no response.
        """
        if not self.performance_relegation:
            return
        try:
            outcomes = self._get_recent_outcomes(500)
            result = self.performance_relegation.check_all_tickers(outcomes)
            if result.get("promoted"):
                for t in result["promoted"]:
                    msg = f"✅ PROMOTION: {t} promoted to A-TEAM (win rate + avg R criteria met)"
                    await self.telegram.send_alert(msg)
                    logger.info("Performance: %s promoted to A-TEAM", t)
            if result.get("relegated"):
                for item in result["relegated"]:
                    ticker = item.get("ticker", item) if isinstance(item, dict) else item
                    stats_str = ""
                    if isinstance(item, dict):
                        stats_str = (
                            f" wr={item.get('win_rate', 0):.1%}"
                            f" avgR={item.get('avg_r', 0):.2f}"
                        )
                    msg = (
                        f"⚠️ A-TEAM DEMOTION VOTE: {ticker}{stats_str}\n"
                        f"Reply CONFIRM DEMOTE {ticker} within 24h or vote auto-cancels."
                    )
                    await self.telegram.send_alert(msg)
                    logger.info("Performance: demotion vote sent for %s", ticker)
        except Exception as e:
            logger.warning("Performance relegation check failed: %s", e)

    async def _refresh_net_expectancy(self) -> None:
        """W9a: Nightly net expectancy refresh at 23:00 UTC.
        Recomputes E = (WR × AvgWin) - ((1-WR) × AvgLoss) - CostDrag per ticker/regime.
        Emits Telegram alert if any ticker goes to marginal/negative expectancy.
        """
        if not self.net_expectancy:
            return
        try:
            outcomes = self._get_recent_outcomes(500)
            result = self.net_expectancy.refresh_all(outcomes)
            marginal = [t for t, v in result.items() if isinstance(v, dict) and v.get("grade") in ("MARGINAL", "NO_EDGE", "NEGATIVE")]
            if marginal:
                msg = (
                    f"⚠️ NET EXPECTANCY WARNING:\n"
                    + "\n".join(
                        f"  {t}: E={result[t].get('net_e', 0):.3f}R ({result[t].get('grade','')})"
                        for t in marginal
                    )
                )
                await self.telegram.send_alert(msg)
            logger.info("Net expectancy refreshed: %d tickers updated, %d marginal", len(result), len(marginal))
        except Exception as e:
            logger.warning("Net expectancy refresh failed: %s", e)

    async def _refresh_earnings_calendar(self) -> None:
        """W4: Daily earnings calendar refresh at 04:00 UTC.
        Auto-fetches upcoming earnings for all ISA underlying tickers.
        """
        if not self.earnings_calendar:
            return
        try:
            result = self.earnings_calendar.check_upcoming()
            upcoming_count = len(result) if isinstance(result, list) else 0
            logger.info("Earnings calendar refreshed: %d upcoming events", upcoming_count)
        except Exception as e:
            logger.warning("Earnings calendar refresh failed: %s", e)

    async def _refresh_cross_asset_macro(self) -> None:
        """W4d: Daily cross-asset macro update at 07:00 UTC (before LSE open).
        Updates DXY, VIX term structure, LQD/IEF credit spread proxy.
        Also feeds VIX + SPX 3-month return to DynamicSizer for Wave 2
        momentum crash prevention (Barroso & Santa-Clara 2015).
        """
        if not self.cross_asset_macro:
            return
        try:
            macro_data = self.cross_asset_macro.update()
            logger.debug("Cross-asset macro updated (DXY, VIX term structure, credit spread)")

            # Wave 2 Item 1: Feed macro state to DynamicSizer
            if hasattr(self, "dynamic_sizer") and self.dynamic_sizer:
                vix_spot = (
                    macro_data.get("vix_signal", {}).get("vix_spot", 0.0)
                    if macro_data else 0.0
                )
                spx_3m = self._fetch_spx_3m_return()
                self.dynamic_sizer.update_macro(
                    vix=vix_spot, spx_3m_return=spx_3m,
                )
                logger.debug(
                    "DynamicSizer macro updated: VIX=%.1f SPX_3m=%.3f",
                    vix_spot, spx_3m,
                )

            # Wave 2 Item 2: Force HMM regime refit on macro refresh
            if hasattr(self, "hmm_regime") and self.hmm_regime is not None:
                try:
                    hmm_result = self.hmm_regime.update()
                    hmm_tag = hmm_result.value if hmm_result else "None"
                    logger.info(
                        "[Wave2] HMM regime refitted on macro refresh: %s",
                        hmm_tag,
                    )
                except Exception as _hmm_err:
                    logger.warning("HMM regime refit failed: %s", _hmm_err)

            # Wave 2 Item 4: ERC portfolio rebalance on macro refresh
            if (
                hasattr(self, "erc_optimizer")
                and self.erc_optimizer is not None
                and hasattr(self, "_correlation_engine")
                and self._correlation_engine is not None
            ):
                try:
                    self._refresh_erc_weights()
                except Exception as _erc_err:
                    logger.warning("ERC refresh failed: %s", _erc_err)

        except Exception as e:
            logger.warning("Cross-asset macro update failed: %s", e)

    def _fetch_spx_3m_return(self) -> float:
        """Fetch S&P 500 3-month return for momentum crash prevention.

        Uses ^GSPC (S&P 500 index) via yfinance. Returns decimal
        (e.g. -0.05 for a 5% decline). Returns 0.0 on error.
        """
        try:
            hist = self._data_feeds.get_daily_bars("^GSPC", days=63) if self._data_feeds else None
            if hist is None or len(hist) < 2:
                return 0.0
            first_close = hist["Close"].iloc[0]
            last_close = hist["Close"].iloc[-1]
            if first_close <= 0:
                return 0.0
            return (last_close - first_close) / first_close
        except Exception as e:
            logger.warning("SPX 3-month return fetch failed: %s", e)
            return 0.0

    def _refresh_erc_weights(self) -> None:
        """Refresh ERC portfolio weights using Ledoit-Wolf covariance.

        Wave 2, Item 4 (Maillard et al. 2010): computes Equal Risk
        Contribution weights for the ISA ticker universe and stores
        them in the ERC optimizer for DynamicSizer to query.
        """
        import numpy as _np

        # Get ISA tickers
        tickers = cfg.get_isa_tickers() if cfg.get_primary_mode() == "UK_ISA" else cfg.get_tickers()
        if not tickers or len(tickers) < 2:
            return

        # Fetch Ledoit-Wolf shrinkage covariance matrix
        try:
            corr_df = self._correlation_engine.get_correlation_matrix(
                tickers=list(tickers), shrinkage=True,
            )
        except Exception as _ce:
            logger.warning("ERC: correlation matrix fetch failed: %s", _ce)
            return

        if corr_df is None or corr_df.empty or len(corr_df) < 2:
            logger.debug("ERC: insufficient correlation data — skipping")
            return

        # Align tickers to matrix columns (some tickers may have no data)
        available_tickers = [t for t in tickers if t in corr_df.columns]
        if len(available_tickers) < 2:
            return

        # Extract the correlation sub-matrix as numpy array
        corr_matrix = corr_df.loc[available_tickers, available_tickers].values

        # Convert correlation → covariance using daily return volatilities
        # Approximate daily vol from the correlation engine's return data
        # For ERC: correlation matrix works as a proxy since we're solving
        # for relative weights. The absolute scale doesn't matter.
        # Using correlation directly is standard practice when only
        # relative risk contributions matter (Roncalli 2013, Chapter 5).
        cov_proxy = _np.array(corr_matrix, dtype=_np.float64)

        # Run ERC optimisation
        weights = self.erc_optimizer.optimise(available_tickers, cov_proxy)

        logger.info(
            "[Wave2] ERC weights refreshed: %d tickers | weights=%s",
            len(weights), weights,
        )

    async def _save_learning_state(self) -> None:
        """Persist all learning module state to database (hourly)."""
        try:
            with transaction() as conn:
                self._save_all_learning_state(conn)
        except Exception as e:
            logger.error("Learning state save failed: %s", e)

    def _save_all_learning_state(self, conn) -> None:
        """Save all learning module state to a single connection."""
        try:
            self.learning.indicator_tracker.save_state(conn)
            self.learning.strategy_tracker.save_state(conn)
            self.learning.failure_analysis.save_state(conn)
            self.learning.decay_detector.save_state(conn)
            self.learning.weight_optimizer.save_state(conn)
            self.learning.param_optimizer.save_state(conn)
            self.learning.system_iq.save_state(conn)
            self.adaptive_intel.save_state(conn)
            self.tournament.save_state(conn)
            logger.info("All learning state saved to database")
        except Exception as e:
            logger.error("_save_all_learning_state error: %s", e)

    def _load_all_learning_state(self, conn) -> None:
        """Load all learning module state from database on startup."""
        try:
            self.learning.indicator_tracker.load_state(conn)
            self.learning.strategy_tracker.load_state(conn)
            self.learning.failure_analysis.load_state(conn)
            self.learning.decay_detector.load_state(conn)
            self.learning.weight_optimizer.load_state(conn)
            self.learning.param_optimizer.load_state(conn)
            self.learning.system_iq.load_state(conn)
            self.adaptive_intel.load_state(conn)
            self.tournament.load_state(conn)
            logger.info("All learning state loaded from database")
        except Exception as e:
            logger.warning("_load_all_learning_state error (expected on first run): %s", e)

    async def _update_missed_trade_outcomes(self) -> None:
        """Update outcomes for pending missed trades (check prices)."""
        try:
            if not self._data_feeds:
                return

            # Get current prices for all pending missed trades
            pending = [mt for mt in self.missed_trade_journal._pending_followups
                       if mt.outcome in ("UNKNOWN", "")]
            if not pending:
                return

            price_data = {}
            for mt in pending:
                if mt.ticker not in price_data:
                    try:
                        price_data[mt.ticker] = self._data_feeds.get_realtime_price(mt.ticker)
                    except Exception as e:
                        logger.warning("Missed trade price fetch failed for %s: %s", mt.ticker, e)

            if price_data:
                self.missed_trade_journal.update_outcomes(price_data)

                # Persist finalized missed trades
                finalized = [mt for mt in self.missed_trade_journal._pending_followups
                             if mt.outcome != "UNKNOWN"]
                if finalized:
                    with transaction() as conn:
                        for mt in finalized:
                            self.missed_trade_journal.persist(conn, mt)
                    # Remove persisted ones from pending
                    self.missed_trade_journal._pending_followups = [
                        mt for mt in self.missed_trade_journal._pending_followups
                        if mt.outcome in ("UNKNOWN", "")
                    ]

        except Exception as e:
            logger.warning("Missed trade outcome update error: %s", e)

    async def start(self) -> None:
        """Start the NZT-48 system."""
        logger.info("=" * 60)
        logger.info("NZT-48 TRADING SYSTEM v%s — STARTING", self._version)
        logger.info("Mode: %s", self.mode)
        logger.info("=" * 60)

        # Initialize database
        (_PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
        init_db()

        # Load all learning state from database (restores intelligence from prior sessions)
        try:
            with transaction() as conn:
                self._load_all_learning_state(conn)
        except Exception as e:
            logger.warning("Learning state load failed (expected on first run): %s", e)

        # V8.0 StateManager boot: connect to Redis, hydrate state, reconcile with SQLite
        if self.state_manager:
            try:
                await self.state_manager.connect()
                await self.state_manager.hydrate_from_redis()
                discrepancies = await self.state_manager.reconcile_with_sqlite()
                if discrepancies:
                    for d in discrepancies:
                        logger.warning("STARTUP_RECONCILIATION: %s", d)
                else:
                    logger.info("STARTUP_RECONCILIATION: Redis ↔ SQLite OK")
                # Check if kill switch was persisted from a previous session
                if await self.state_manager.is_killed():
                    kill_info = await self.state_manager.get_kill_info()
                    logger.critical(
                        "STATE_MANAGER: Kill switch ACTIVE from previous session: %s",
                        kill_info,
                    )
            except Exception as _sm_boot_err:
                logger.error("StateManager boot failed (non-critical): %s", _sm_boot_err)

        # Initialize Telegram
        await self.telegram.initialize()
        # Fix #7: Start Telegram polling so commands are received
        await self.telegram.start_polling()

        # Initialize Google Sheets
        self.sheets.initialize()

        # Set up signal handlers for kill switch (method 3)
        def handle_signal(signum, frame):
            logger.critical("Received signal %d — activating kill switch", signum)
            self.kill_switch.set_process_killed()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        # Start scheduler
        scheduler = self.setup_scheduler()

        # ── V2 COMMAND CENTER ─────────────────────────────────────────────
        # Start realtime tick loop (30s active / 120s off-hours)
        try:
            from command_center.tick_loop import TickLoop

            async def _tg_alert(text: str) -> None:
                try:
                    await self.telegram.send_alert(text)
                except Exception as e:
                    logger.warning("Tick loop Telegram alert failed: %s", e)

            self._tick_loop = TickLoop(
                use_extended=True,
                telegram_fn=_tg_alert,
                api_pusher=self._api_pusher,
                virtual_trader=self.virtual_trader,
                engine_ref=self,
                signal_queue=self._signal_queue,
            )
            await self._tick_loop.start()
            logger.info("[COMMAND CENTER] Tick loop started")
        except Exception as cc_err:
            logger.warning("[COMMAND CENTER] Tick loop failed to start: %s", cc_err)
            self._tick_loop = None

        # Start API heartbeat (so dashboard knows engine is alive)
        async def _heartbeat_loop():
            while not self.kill_switch.is_killed():
                try:
                    self._api_pusher.heartbeat()
                except Exception:
                    pass
                await asyncio.sleep(30)
        _t = asyncio.create_task(_heartbeat_loop())
        self._background_tasks.add(_t)
        _t.add_done_callback(self._background_tasks.discard)

        # CC routes are now served by the unified API on :8000/cc/*
        # No embedded server needed — engine pushes state via APIPusher
        logger.info("[COMMAND CENTER] CC routes served by unified API on :8000/cc/*")

        # F-02: Start async signal queue consumer (drains priority queue)
        _sq_task = asyncio.create_task(self._signal_queue_consumer())
        self._background_tasks.add(_sq_task)
        _sq_task.add_done_callback(self._background_tasks.discard)
        logger.info("F-02: Signal queue consumer task started")

        # ── END COMMAND CENTER ────────────────────────────────────────────

        # Send startup notification (with cooldown to prevent spam on rapid restarts)
        import time as _time
        _startup_cooldown_file = "/tmp/nzt48_last_startup_alert.txt"
        _startup_cooldown_secs = 300  # 5 min between startup alerts
        _send_startup = True
        try:
            with open(_startup_cooldown_file, "r") as _f:
                last_ts = float(_f.read().strip())
                if _time.time() - last_ts < _startup_cooldown_secs:
                    _send_startup = False
                    logger.info("Startup Telegram suppressed (cooldown, last was %ds ago)",
                                int(_time.time() - last_ts))
        except (FileNotFoundError, ValueError):
            pass
        if _send_startup:
            await self.telegram.send_alert(
                "NZT-48 v8.0 Apex Predator Engine started.\n"
                "Signal Engine: strict + fallback modes active.\n"
                "PDFs: Pre-LSE 07:00 | Pre-NYSE 13:30 | EOD 22:00 | Mega 22:30 UK\n"
                "Tick loop: 30s active / 120s off-hours."
            )
            try:
                with open(_startup_cooldown_file, "w") as _f:
                    _f.write(str(_time.time()))
            except Exception:
                pass

        # Run initial scan
        logger.info("Running initial scan...")
        signals = await self.run_scan()
        logger.info("Initial scan produced %d qualified signals", len(signals))

        # Keep running
        try:
            while not self.kill_switch.is_killed():
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutdown requested...")
        finally:
            # Persist open positions before shutdown
            try:
                if hasattr(self, 'virtual_trader') and self.virtual_trader.open_positions:
                    logger.info("Persisting %d open positions on shutdown...", len(self.virtual_trader.open_positions))
                    for pos_id, pos in self.virtual_trader.open_positions.items():
                        logger.info("  Open position: %s %s @ %.2f", pos.ticker, pos.direction, pos.entry_price)
            except Exception as e:
                logger.error("Failed to log open positions on shutdown: %s", e)

            # Save all learning state before shutdown
            try:
                with transaction() as conn:
                    self._save_all_learning_state(conn)
                    logger.info("Learning state saved on shutdown")
            except Exception as e:
                logger.error("Failed to save learning state on shutdown: %s", e)

            # V8.0 StateManager: close Redis connection
            if self.state_manager:
                try:
                    await self.state_manager.close()
                except Exception as _sm_close_err:
                    logger.error("StateManager close failed: %s", _sm_close_err)

            if scheduler:
                scheduler.shutdown()
            await self.telegram.stop()
            logger.info("NZT-48 shut down cleanly.")


# === Entry Point ===

def _validate_env():
    """Validate critical environment variables before engine start."""
    warnings = []
    # Required for signal delivery
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        warnings.append("TELEGRAM_BOT_TOKEN not set — signals will NOT be delivered")
    if not os.environ.get("TELEGRAM_CHAT_ID"):
        warnings.append("TELEGRAM_CHAT_ID not set — signals will NOT be delivered")
    # Recommended
    if os.environ.get("NZT48_API_KEY", "") in ("", "CHANGE_ME_GENERATE_A_SECURE_KEY"):
        warnings.append("NZT48_API_KEY is default/empty — API kill switch is UNPROTECTED")
    if warnings:
        print("=" * 60)
        print("  NZT-48 STARTUP WARNINGS")
        print("=" * 60)
        for w in warnings:
            print(f"  ⚠️  {w}")
        print("=" * 60)
        print()


def main():
    """Main entry point for the NZT-48 trading system."""
    _validate_env()

    # V9.5 Phase 1a: uvloop — ~20-30% event loop latency reduction.
    # Graceful fallback to standard asyncio if uvloop not installed.
    try:
        import uvloop
        uvloop.install()
        print("  [V9.5] uvloop installed — event loop optimized")
    except ImportError:
        pass  # Standard asyncio — fine for production

    orchestrator = NZT48Orchestrator()
    asyncio.run(orchestrator.start())


if __name__ == "__main__":
    main()
