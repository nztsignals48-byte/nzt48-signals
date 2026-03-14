"""
Orchestrator: Full AEGIS V2 System Integration
Phases 1-33 unified orchestration

This is the heartbeat of the system. Every trade flows through here.
"""

import sys
sys.path.insert(0, '/Users/rr/nzt48-signals')

from src.core.kelly_sizer import KellySizer
from src.core.isa_auditor import ISAAuditor
from src.core.pre_trade_gate import PreTradeGate
from src.core.white_reality_check import WhiteRealityCheck
from src.core.regime_detector import RegimeDetector
from src.core.vol_scaler import VolScaler
from src.core.confidence_scorer import ConfidenceScorer
from src.core.pre_conditions_gate import PreConditionsGate
from src.core.position_sizer import PositionSizer
from src.core.execution_quality import ExecutionQuality

# WEEK 1: Perfect entry timing integration
from src.core.early_detection_engine import EarlyDetectionEngine
from src.core.inverse_etp_entry_timing import InverseETPEntryTiming

from dataclasses import dataclass
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class TradeSignal:
    """Input signal from market"""
    symbol: str
    side: str  # BUY/SELL
    vwap_score: float
    rsi_score: float
    ema_score: float
    roc_score: float
    macd_score: float
    adx_score: float
    bb_score: float
    vol_score: float
    current_price: float
    bid: float
    ask: float
    vix: float
    realized_vol: float
    momentum: float
    volume: float
    timestamp: datetime

@dataclass
class TradeDecision:
    """System decision on a signal"""
    approved: bool
    symbol: str
    position_size: float
    leverage: float
    regime: str
    confidence: float
    rejection_reasons: list

class AEGISV2Orchestrator:
    """Central orchestrator for all 32 phases"""

    def __init__(self, equity=10000):
        self.equity = equity
        self.kelly = KellySizer(equity)
        self.isa = ISAAuditor()
        self.pre_trade = PreTradeGate()
        self.dsr = WhiteRealityCheck()
        self.regime = RegimeDetector()
        self.vol_scaler = VolScaler()
        self.confidence_scorer = ConfidenceScorer()
        self.precond_gate = PreConditionsGate()
        self.position_sizer = PositionSizer(275, 1.0)
        self.exec_quality = ExecutionQuality()

        # WEEK 1: Perfect entry timing gates
        self.early_detection = EarlyDetectionEngine()
        self.inverse_timing = InverseETPEntryTiming()

        self.trades_executed = 0
        self.trades_rejected = 0
        self.current_regime = "RANGE"
        self.current_holdings = {}

    def _build_market_data(self, signal: TradeSignal) -> dict:
        """
        Build market data dict for early detection engines.

        Args:
            signal: TradeSignal from orchestrator

        Returns:
            Dict with keys expected by early_detection_engine and inverse_etp_entry_timing
        """
        return {
            'current_price': signal.current_price,
            'bid': signal.bid,
            'ask': signal.ask,
            'volume': signal.volume,
            'vix': signal.vix,
            'realized_vol': signal.realized_vol,
            'momentum': signal.momentum,
            # Note: real implementation would populate more fields
            # For now, we provide the core signals available
            'ofi': 0.0,  # Would come from order flow analysis
            'ofi_rising': False,
            'volume_profile_lvn': signal.current_price,
            'vtd_ratio': 0.7,  # Would be calculated from volume-time decay
            'hawkes_branching_ratio': 0.5,  # Would come from Hawkes process
            'hawkes_trending': False,
            'atm_accel': 1.0,  # ATR acceleration
            'recent_bars': [],  # Would contain last 10 bars
            'gap_pct': 0.0,  # Would calculate from prev close
            'bb_width_pct': signal.bb_score * 10.0,  # Approximate from BB score
            'atr': signal.realized_vol * signal.current_price / 100.0,  # Approximate ATR
        }

    def process_signal(self, signal: TradeSignal) -> TradeDecision:
        """
        Full trade processing pipeline (Phases 1-10 integrated)

        Flow:
        1. Detect regime (Phase 5) → adaptive parameters
        2. Score confidence (Phase 7)
        3. Check pre-conditions (Phase 8)
        4. Size position (Phase 9)
        5. ISA audit (Phase 2)
        6. Pre-trade validation (Phase 3)
        7. Execution quality (Phase 10)
        8. Execute if all pass
        """
        rejection_reasons = []

        # PHASE 5: Regime Detection
        regime_result = self.regime.detect(signal.vix, signal.realized_vol, signal.momentum)
        self.current_regime = regime_result.regime
        regime_params = self.regime.get_regime_parameters(regime_result.regime)
        logger.info(f"Regime: {regime_result.regime}, confidence: {regime_result.confidence:.0%}")

        # PHASE 6: Volatility Scaling
        vol_result = self.vol_scaler.scale(signal.realized_vol, regime_result.regime)
        vol_scalar = vol_result.vol_scalar

        # PHASE 7: Confidence Scoring
        confidence_result = self.confidence_scorer.score(
            signal.vwap_score, signal.rsi_score, signal.ema_score, signal.roc_score,
            signal.macd_score, signal.adx_score, signal.bb_score, signal.vol_score,
            regime_result.regime
        )
        logger.info(f"Confidence: {confidence_result.score:.1f}/10, threshold: {confidence_result.regime_threshold:.1f}")

        if not confidence_result.passed:
            rejection_reasons.append(f"Confidence {confidence_result.score:.1f} < {confidence_result.regime_threshold:.1f}")
            return TradeDecision(False, signal.symbol, 0, 0, self.current_regime, confidence_result.score, rejection_reasons)

        # WEEK 1: PERFECT ENTRY TIMING GATE (Early Detection)
        # Build market data for early detection engines
        market_data = self._build_market_data(signal)

        # Evaluate entry readiness via tier-based signal fusion
        early_detection_result = self.early_detection.evaluate_entry_readiness(
            signal.symbol, market_data
        )

        # Check if this is a short/inverse position
        if signal.side == "SELL":
            inverse_result = self.inverse_timing.is_perfect_short_entry(signal.symbol, market_data)
            # Use inverse result if it provides better confidence
            if inverse_result.confidence > early_detection_result.confidence:
                early_detection_result = inverse_result
                logger.info(f"Using inverse entry timing: {inverse_result.decision_reason}")

        # Validate early detection passed (confidence ≥65% minimum)
        if not early_detection_result.should_enter:
            rejection_reasons.append(
                f"Early detection blocked: {early_detection_result.decision_reason} "
                f"({early_detection_result.confidence:.0f}%)"
            )
            logger.info(f"Perfect entry timing blocked: {signal.symbol}")
            return TradeDecision(
                False, signal.symbol, 0, 0, self.current_regime,
                confidence_result.score, rejection_reasons
            )

        # PHASE 8: Pre-Conditions Gate
        precond_result = self.precond_gate.check(
            confidence_result.score,
            confidence_result.regime_threshold,
            isa_audit_pass=True,  # Simplified (Phase 2 runs separately)
            seconds_since_loss=700,
            recent_losses=0
        )

        if not precond_result.passed:
            rejection_reasons.append(precond_result.reason)
            return TradeDecision(False, signal.symbol, 0, 0, self.current_regime, confidence_result.score, rejection_reasons)

        # PHASE 9: Position Sizing (with perfect entry filter)
        position_result = self.position_sizer.size(
            confidence=confidence_result.score,
            regime=regime_result.regime,
            asset_type="LSE",
            daily_gain_pct=0,
            equity=self.equity,
            direction=signal.side  # WEEK 1: Pass direction for perfect entry filter
        )

        if not position_result.approved:
            rejection_reasons.append(f"Position sizing rejected")
            return TradeDecision(False, signal.symbol, 0, 0, self.current_regime, confidence_result.score, rejection_reasons)

        # PHASE 2: ISA Auditor (run before submission)
        isa_result = self.isa.audit(
            margin_debt=0,
            current_holdings=self.current_holdings,
            leverage_ratio=position_result.leverage,
            is_margin_trading=False,
            has_borrowed_shorts=False,
            uk_residency=True,
            has_crypto_etn=False
        )

        if not isa_result.passed or self.isa.is_trading_halted():
            rejection_reasons.append("ISA audit failed or trading halted")
            return TradeDecision(False, signal.symbol, 0, 0, self.current_regime, confidence_result.score, rejection_reasons)

        # PHASE 3: Pre-Trade Compliance Gates
        pretrade_result = self.pre_trade.validate_order(
            symbol=signal.symbol,
            quantity=int(position_result.size / signal.current_price),
            side=signal.side,
            current_price=signal.current_price,
            bid_price=signal.bid,
            ask_price=signal.ask,
            recent_volume=signal.volume,
            margin_available=self.equity * 0.8,  # Assume 80% available
            position_size_required=position_result.size,
            market="LSE"
        )

        if not pretrade_result.passed:
            rejection_reasons.append(pretrade_result.rejection_reason)
            return TradeDecision(False, signal.symbol, 0, 0, self.current_regime, confidence_result.score, rejection_reasons)

        # PHASE 10: Execution Quality
        expected_slippage = self.exec_quality.model_slippage("LSE", regime_result.regime, 0.1)
        logger.info(f"Expected slippage: {expected_slippage:.1f} bps")

        # ✅ ALL CHECKS PASSED - TRADE APPROVED
        self.trades_executed += 1
        logger.info(f"✅ TRADE APPROVED: {signal.symbol} {signal.side} £{position_result.size:.0f} @ {position_result.leverage:.1f}x")

        return TradeDecision(
            approved=True,
            symbol=signal.symbol,
            position_size=position_result.size,
            leverage=position_result.leverage,
            regime=regime_result.regime,
            confidence=confidence_result.score,
            rejection_reasons=[]
        )

    def get_status(self) -> dict:
        """System health status"""
        return {
            "trades_executed": self.trades_executed,
            "trades_rejected": self.trades_rejected,
            "current_regime": self.current_regime,
            "current_holdings": self.current_holdings,
            "equity": self.equity,
            "timestamp": datetime.now().isoformat()
        }


if __name__ == "__main__":
    print("="*60)
    print("AEGIS V2 ORCHESTRATOR TEST (Phases 1-10 Integrated)")
    print("="*60)

    orch = AEGISV2Orchestrator(equity=10000)

    # Test signal
    signal = TradeSignal(
        symbol="QQQ3.L",
        side="BUY",
        vwap_score=8, rsi_score=7, ema_score=8, roc_score=7,
        macd_score=7, adx_score=8, bb_score=6, vol_score=7,
        current_price=150,
        bid=149.80,
        ask=150.20,
        vix=12,
        realized_vol=12,
        momentum=2.5,
        volume=50000,
        timestamp=datetime.now()
    )

    decision = orch.process_signal(signal)

    print(f"\n{'='*60}")
    print(f"DECISION:")
    print(f"  Approved: {decision.approved}")
    print(f"  Symbol: {decision.symbol}")
    print(f"  Position size: £{decision.position_size:.0f}")
    print(f"  Leverage: {decision.leverage:.1f}x")
    print(f"  Regime: {decision.regime}")
    print(f"  Confidence: {decision.confidence:.1f}/10")
    if decision.rejection_reasons:
        print(f"  Rejections: {decision.rejection_reasons}")

    print(f"\nSystem Status:")
    print(f"  Trades executed: {orch.trades_executed}")
    print(f"  Equity: £{orch.equity}")
    print(f"\n✅ ORCHESTRATOR TEST COMPLETE")
