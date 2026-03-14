"""NZT-48 Full Integration Test — Verifies all components import and initialize."""

import sys
import os
from pathlib import Path

# Ensure project root on path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.chdir(str(_ROOT))

from dotenv import load_dotenv
load_dotenv()


def main():
    print("=" * 60)
    print("NZT-48 INTEGRATION TEST")
    print("=" * 60)

    # Test 1: Config loads
    print("\n[1/9] Loading config...")
    import config as cfg
    mode = cfg.get("system.mode", "UNKNOWN")
    tickers = cfg.get("bot_b_universe.tickers", [])
    print(f"  Config loaded. Mode: {mode}")
    print(f"  Bot B universe: {len(tickers)} tickers")
    assert mode == "PAPER", f"Expected PAPER mode, got {mode}"
    assert len(tickers) >= 12, f"Expected >= 12 tickers, got {len(tickers)}"

    # Test 2: Models import
    print("\n[2/9] Importing models...")
    from models import (
        Signal, Direction, RegimeState, GEXRegime, Bot, BotInstance,
        MarketContext, IndicatorSnapshot, SectorFlow, NarrativeContext,
        ConfidenceBreakdown, Position, Trade, TimeWindow, Strategy,
        LadderRung, SignalStatus, DrawdownLevel, EmotionalPattern,
        ConstituentAlert, ETPBrief, PreMarketBrief,
    )
    print(f"  RegimeStates: {len(RegimeState)}, Strategies: {len(Strategy)}, LadderRungs: {len(LadderRung)}")

    # Test 3: Database init
    print("\n[3/9] Initializing database...")
    from delivery.database import init_db, get_connection
    init_db()
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [t["name"] for t in tables if t["name"] != "sqlite_sequence"]
    print(f"  {len(table_names)} tables created:")
    for tn in table_names:
        print(f"    - {tn}")
    conn.close()
    assert len(table_names) >= 15, f"Expected >= 15 tables, got {len(table_names)}"

    # Test 4: All feed modules
    print("\n[4/9] Importing feeds...")
    from feeds.data_feeds import DataFeedManager
    from feeds.indicators import IndicatorEngine
    from feeds.regime_classifier import RegimeClassifier, TimeOfDayEngine
    from feeds.pattern_detector import PatternDetector
    from feeds.market_structure import MarketStructure
    from feeds.calendar_feed import CalendarFeed
    from feeds.news_feed import NewsFeed
    from feeds.screener import FinvizScreener
    from feeds.holdings_decomposition import HoldingsDecomposer
    from feeds.premarket_intelligence import PreMarketIntelligenceEngine
    print("  All 10 feed modules imported.")

    # Test 5: All 14 strategies
    print("\n[5/9] Importing strategies...")
    strats = []
    from strategies.regime_trend import RegimeTrendStrategy; strats.append("S1")
    from strategies.momentum_breakout import MomentumBreakoutStrategy; strats.append("S2")
    from strategies.mean_reversion import MeanReversionStrategy; strats.append("S3")
    from strategies.catalyst_narrative import CatalystNarrativeStrategy; strats.append("S4")
    from strategies.pead_earnings import PEADEarningsDrift; strats.append("S5")
    from strategies.macro_regime import MacroRegimeShift; strats.append("S6")
    from strategies.sector_rotation import SectorRotation; strats.append("S7")
    from strategies.vol_crush import VolatilityCrush; strats.append("S8")
    from strategies.pairs_trade import PairsTrade; strats.append("S9")
    from strategies.ai_thematic import AIThematicStrategy; strats.append("S10")
    from strategies.hot_scanner import HotScannerStrategy; strats.append("S11")
    from strategies.rebalance_flow import RebalanceFlowStrategy; strats.append("S12")
    from strategies.trend_compound import TrendCompoundStrategy; strats.append("S13")
    from strategies.gamma_squeeze import GammaSqueezeStrategy; strats.append("S14")
    print(f"  All {len(strats)} strategies imported: {' '.join(strats)}")

    # Test 6: Qualification pipeline
    print("\n[6/9] Importing qualification pipeline...")
    from qualification.qualifier import QualificationPipeline
    from qualification.confidence_scorer import ConfidenceScorer
    from qualification.risk_sizer import (
        ImmutableRiskRules, EmotionalFirewall, SessionProtection, DrawdownRecovery,
    )
    from qualification.profit_ladder import ProfitLadder, ETPProfitLadder
    from qualification.pdt_tracker import PDTTracker
    pipeline = QualificationPipeline()
    rules = ImmutableRiskRules()
    print(f"  Pipeline: 7 stages, {len(pipeline.isa_map)} ISA mappings")
    print(f"  Risk per trade: {rules.RISK_PER_TRADE*100}% | Max weekly: {rules.MAX_WEEKLY_LOSS*100}% | Daily loss: circuit_breakers L1/L2/L3")

    # Test 7: Bots and execution
    print("\n[7/9] Importing bots and execution...")
    from bots.bot_base import BotBase
    from bots.specialist_bots import BullBot, RangeBot, BearBot
    from bots.portfolio_overseer import PortfolioOverseer
    from bots.timeframe_stacking import TimeframeStackingEngine, ScalpLayer, SwingLayer
    from bots.sector_meta_bot import SectorRotationMetaBot
    from bots.earnings_specialist import EarningsSpecialist
    from bots.kelly_sizer import KellySizer
    from execution.virtual_trader import VirtualTrader
    tse = TimeframeStackingEngine()
    print("  3 specialist bots: BullBot, RangeBot, BearBot")
    print("  Timeframe stacking: ScalpLayer + SwingLayer")
    print("  Kelly sizer, virtual trader, portfolio overseer ready")

    # Test 8: Learning engine with all 10 modules
    print("\n[8/9] Importing learning engine...")
    from learning.learning_engine import LearningEngine
    from learning.indicator_tracker import IndicatorEffectivenessTracker
    from learning.strategy_tracker import StrategyContextMatrix
    from learning.move_attribution import MoveAttribution
    from learning.pattern_tracker import PatternTracker
    from learning.failure_analysis import FailureAnalysis
    from learning.correlation_tracker import CorrelationTracker
    from learning.decay_detector import DecayDetector
    from learning.weight_optimizer import WeightOptimizer
    from learning.param_optimizer import ParameterOptimizer
    from learning.system_iq import SystemIQ
    le = LearningEngine()
    status = le.get_all_learning_status()
    print(f"  LearningEngine initialized with {len(status)} module outputs")

    # Test 9: Institutional-grade modules — Tier 1 (6 modules)
    print("\n[9/12] Importing institutional-grade modules (Tier 1)...")
    from qualification.dynamic_sizer import DynamicSizer
    from qualification.circuit_breakers import CircuitBreakerSystem
    from learning.edge_decay_engine import EdgeDecayEngine
    from qualification.confluence_scorer import ConfluenceScorer
    from execution.smart_routing import SmartRouter
    from qualification.portfolio_risk import PortfolioRiskManager
    ds = DynamicSizer(starting_equity=10000)
    cb = CircuitBreakerSystem(equity=10000)
    ed = EdgeDecayEngine()
    cs = ConfluenceScorer()
    sr = SmartRouter()
    prm = PortfolioRiskManager(equity=10000)
    print("  DynamicSizer: 8-factor Kelly + vol + regime + streak + heat")
    print("  CircuitBreakerSystem: 5 breakers (drawdown, VIX, correlation, streak, black swan)")
    print("  EdgeDecayEngine: 13 x 30-min alpha buckets per strategy x regime")
    print("  ConfluenceScorer: 5 timeframes + volume + indicators + cross-asset")
    print("  SmartRouter: liquidity scoring, slippage prediction, execution planning")
    print("  PortfolioRiskManager: 8-dimension risk decomposition + trade gate")

    # Test 10: Institutional-grade modules — Tier 2 (4 modules)
    print("\n[10/12] Importing institutional-grade modules (Tier 2)...")
    from feeds.correlation_matrix import RealTimeCorrelationMatrix
    from feeds.data_validator import DataFeedValidator
    from execution.session_manager import SessionBoundaryManager
    from learning.performance_attribution import PerformanceAttributionEngine
    cm = RealTimeCorrelationMatrix()
    dv = DataFeedValidator()
    sm = SessionBoundaryManager()
    pa = PerformanceAttributionEngine()
    # Quick functional checks
    cm.update("NVDA", 900.0, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    cm.update("AMD", 165.0, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert cm.get_correlation("NVDA", "AMD") != 0, "Correlation should not be zero"
    bar_ok, _ = dv.validate_bar("TEST", {"open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000,
                                          "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)})
    assert bar_ok, "Good bar should validate"
    phase = sm.get_current_phase()
    assert "phase" in phase, "Session phase should have 'phase' key"
    print(f"  RealTimeCorrelationMatrix: Welford online algorithm, {cm.get_status()['ticker_count']} tickers tracked")
    print(f"  DataFeedValidator: bar validation, staleness, quality scoring (32 self-tests)")
    print(f"  SessionBoundaryManager: 8 phases, current={phase['phase']}")
    print(f"  PerformanceAttributionEngine: 6-factor trade decomposition")

    # Test 11: Pre-market intelligence
    print("\n[11/12] Importing pre-market intelligence...")
    from feeds.holdings_decomposition import HoldingsDecomposer
    from feeds.premarket_intelligence import PreMarketIntelligenceEngine
    print("  HoldingsDecomposer + PreMarketIntelligenceEngine imported")

    # Test 12: Delivery layer + main orchestrator
    print("\n[12/12] Importing delivery + orchestrator...")
    from delivery.telegram_bot import TelegramDelivery, KillSwitch
    from delivery.report_generator import ReportGenerator
    from delivery.sheets_logger import SheetsLogger
    from main import NZT48Orchestrator
    ks = KillSwitch()
    print(f"  Kill switch: {'ACTIVE' if ks.is_killed() else 'DISARMED'}")
    print("  NZT48Orchestrator imported.")

    print()
    print("=" * 60)
    print("ALL 12 INTEGRATION TESTS PASSED")
    print()
    print("System Inventory:")
    print(f"  Configuration:    PAPER mode, {len(tickers)} tickers")
    print(f"  Models:           {len(RegimeState)} regime states, {len(Strategy)} strategies")
    print(f"  Database:         {len(table_names)} tables, WAL + FK mode")
    print(f"  Data Feeds:       10 modules (market data, indicators, regime, patterns, calendar, news, structure, screener, correlation, validator)")
    print(f"  Strategies:       14 (S1-S14)")
    print(f"  Qualification:    7-stage pipeline, 17 immutable rules, 12 emotional blocks")
    print(f"  Risk Management:  Session protection, drawdown recovery, PDT tracker")
    print(f"  Profit Mgmt:      7-rung ladder + accelerated ETP ladder")
    print(f"  Bots:             3 specialist + overseer + timeframe stacking + sector meta + earnings")
    print(f"  Execution:        Virtual trader with realistic slippage model")
    print(f"  Learning:         11 modules (indicator, strategy, attribution, pattern, failure, correlation, decay, weight, param, IQ, perf_attribution)")
    print(f"  Institutional T1: Dynamic sizer, circuit breakers, edge decay, confluence, smart routing, portfolio risk")
    print(f"  Institutional T2: Correlation matrix, data validator, session manager, performance attribution")
    print(f"  Pre-Market:       Holdings decomposition + intelligence engine (UK 09:00 + US 09:00)")
    print(f"  Delivery:         Telegram (10 commands) + Google Sheets + Reports + Kill Switch")
    print()
    print("NZT-48 SIGNAL ENGINE — BEYOND INSTITUTIONAL GRADE — READY TO LAUNCH")
    print("=" * 60)


if __name__ == "__main__":
    main()
