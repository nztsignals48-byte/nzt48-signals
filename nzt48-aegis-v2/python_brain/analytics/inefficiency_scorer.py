"""analytics/inefficiency_scorer.py — Book 36: Inefficient Market Hunting.

Rates each instrument on 5 dimensions of market inefficiency:
  1. Retail Participation (0.25)
  2. Liquidity Inverse (0.20)
  3. Information Asymmetry (0.20)
  4. Structural Flows (0.25)
  5. Regulatory Friction (0.10)

Composite >= 6.0 required for trading.  Includes rebalancing flow predictor
and NAV premium/discount tracker for leveraged ETPs.
"""

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ── Scoring Constants ───────────────────────────────────────────────────

DIMENSION_WEIGHTS = {
    "retail_participation": 0.25,
    "liquidity_inverse": 0.20,
    "information_asymmetry": 0.20,
    "structural_flows": 0.25,
    "regulatory_friction": 0.10,
}

# Baseline scores for LSE leveraged ETPs and US large-caps
# Keys: rp, lq, ia, sf, rf, underlying, leverage, est_aum, adv
BASELINE_SCORES: Dict[str, dict] = {
    # LSE 3x Long
    "3LNV": {"rp": 9, "lq": 8, "ia": 7, "sf": 9, "rf": 7, "underlying": "NVDA", "leverage": 3, "est_aum": 150e6, "adv": 5e6},
    "QQQ3": {"rp": 9, "lq": 7, "ia": 6, "sf": 9, "rf": 7, "underlying": "NDX", "leverage": 3, "est_aum": 300e6, "adv": 15e6},
    "3LTS": {"rp": 9, "lq": 8, "ia": 7, "sf": 9, "rf": 7, "underlying": "TSLA", "leverage": 3, "est_aum": 100e6, "adv": 4e6},
    "3USL": {"rp": 8, "lq": 6, "ia": 5, "sf": 9, "rf": 7, "underlying": "SPX", "leverage": 3, "est_aum": 200e6, "adv": 10e6},
    "3LAL": {"rp": 8, "lq": 8, "ia": 7, "sf": 8, "rf": 7, "underlying": "GOOGL", "leverage": 3, "est_aum": 80e6, "adv": 3e6},
    "3LMS": {"rp": 8, "lq": 7, "ia": 6, "sf": 8, "rf": 7, "underlying": "MSFT", "leverage": 3, "est_aum": 90e6, "adv": 4e6},
    "3LME": {"rp": 8, "lq": 8, "ia": 7, "sf": 8, "rf": 7, "underlying": "META", "leverage": 3, "est_aum": 70e6, "adv": 3e6},
    "3LAZ": {"rp": 8, "lq": 7, "ia": 6, "sf": 8, "rf": 7, "underlying": "AMZN", "leverage": 3, "est_aum": 85e6, "adv": 3.5e6},
    "3LAP": {"rp": 8, "lq": 7, "ia": 6, "sf": 8, "rf": 7, "underlying": "AAPL", "leverage": 3, "est_aum": 95e6, "adv": 4.5e6},
    "SP5L": {"rp": 7, "lq": 5, "ia": 5, "sf": 7, "rf": 7, "underlying": "SPX", "leverage": 2, "est_aum": 250e6, "adv": 12e6},
    # LSE 3x Inverse
    "3SNV": {"rp": 9, "lq": 9, "ia": 7, "sf": 9, "rf": 7, "underlying": "NVDA", "leverage": -3, "est_aum": 40e6, "adv": 2e6},
    "QQQS": {"rp": 9, "lq": 8, "ia": 6, "sf": 9, "rf": 7, "underlying": "NDX", "leverage": -3, "est_aum": 80e6, "adv": 5e6},
    "3STS": {"rp": 9, "lq": 9, "ia": 7, "sf": 9, "rf": 7, "underlying": "TSLA", "leverage": -3, "est_aum": 30e6, "adv": 1.5e6},
    "3USS": {"rp": 8, "lq": 7, "ia": 5, "sf": 9, "rf": 7, "underlying": "SPX", "leverage": -3, "est_aum": 60e6, "adv": 4e6},
    # US large-caps (reference — low inefficiency)
    "NVDA": {"rp": 6, "lq": 2, "ia": 2, "sf": 4, "rf": 2, "underlying": "NVDA", "leverage": 1, "est_aum": 0, "adv": 500e6},
    "TSLA": {"rp": 7, "lq": 2, "ia": 2, "sf": 4, "rf": 2, "underlying": "TSLA", "leverage": 1, "est_aum": 0, "adv": 300e6},
    "AAPL": {"rp": 4, "lq": 1, "ia": 1, "sf": 3, "rf": 2, "underlying": "AAPL", "leverage": 1, "est_aum": 0, "adv": 400e6},
}


# ── Dataclasses ─────────────────────────────────────────────────────────

@dataclass
class InefficiencyScore:
    """Inefficiency rating for a single instrument."""
    ticker: str
    retail_participation: float
    liquidity_inverse: float
    information_asymmetry: float
    structural_flows: float
    regulatory_friction: float
    composite: float
    rating: str  # Prime, Strong, Moderate, Weak, Avoid
    last_updated: str = ""

    @staticmethod
    def compute_composite(rp: float, lq: float, ia: float,
                          sf: float, rf: float) -> float:
        return rp * 0.25 + lq * 0.20 + ia * 0.20 + sf * 0.25 + rf * 0.10

    @staticmethod
    def classify(composite: float) -> str:
        if composite >= 8.0:
            return "Prime"
        elif composite >= 7.0:
            return "Strong"
        elif composite >= 6.0:
            return "Moderate"
        elif composite >= 5.0:
            return "Weak"
        return "Avoid"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class RebalancingPrediction:
    """Predicted rebalancing flow for a leveraged ETP."""
    ticker: str
    underlying_ticker: str
    leverage: float
    underlying_return: float
    estimated_aum: float
    rebalancing_direction: str  # BUY or SELL
    rebalancing_notional: float  # GBP
    rebalancing_as_pct_adv: float
    signal_strength: str  # STRONG, MODERATE, WEAK
    confidence: float

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class NAVSignal:
    """NAV premium/discount signal for a leveraged ETP."""
    ticker: str
    market_price: float
    indicative_nav: float
    premium_pct: float  # positive = premium, negative = discount
    signal: str  # BUY, SELL, HOLD
    magnitude: str  # LARGE, MEDIUM, SMALL
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ── InefficiencyScorer ──────────────────────────────────────────────────

class InefficiencyScorer:
    """Rates instruments on 5-dimension inefficiency model.

    Composite = RP*0.25 + LQ*0.20 + IA*0.20 + SF*0.25 + RF*0.10
    Minimum composite 6.0 for trading.
    """

    MIN_COMPOSITE = 6.0

    def __init__(self, baselines: Optional[Dict[str, dict]] = None):
        self.baselines = baselines or BASELINE_SCORES

    def score_instrument(self, ticker: str,
                         realtime_spread: Optional[float] = None,
                         underlying_return_today: Optional[float] = None) -> InefficiencyScore:
        """Score a single instrument with optional real-time adjustments."""
        b = self.baselines.get(ticker, {})
        rp = b.get("rp", 5.0)
        lq = b.get("lq", 5.0)
        ia = b.get("ia", 5.0)
        sf = b.get("sf", 5.0)
        rf = b.get("rf", 5.0)

        # Real-time liquidity adjustment
        if realtime_spread is not None:
            if realtime_spread > 0.01:
                lq = min(10.0, lq + 1.0)
            elif realtime_spread < 0.003:
                lq = max(1.0, lq - 1.0)

        # Structural flow adjustment based on underlying move
        if underlying_return_today is not None:
            abs_ret = abs(underlying_return_today)
            if abs_ret > 0.02:
                sf = min(10.0, sf + 2.0)
            elif abs_ret > 0.01:
                sf = min(10.0, sf + 1.0)

        composite = InefficiencyScore.compute_composite(rp, lq, ia, sf, rf)
        rating = InefficiencyScore.classify(composite)

        return InefficiencyScore(
            ticker=ticker,
            retail_participation=rp,
            liquidity_inverse=lq,
            information_asymmetry=ia,
            structural_flows=sf,
            regulatory_friction=rf,
            composite=round(composite, 2),
            rating=rating,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

    def score_universe(self, tickers: Optional[List[str]] = None,
                       **kwargs) -> List[InefficiencyScore]:
        """Score all instruments, sorted by composite descending."""
        tickers = tickers or list(self.baselines.keys())
        scores = [self.score_instrument(t, **kwargs) for t in tickers]
        scores.sort(key=lambda s: s.composite, reverse=True)
        return scores

    def get_tradeable(self, tickers: Optional[List[str]] = None,
                      min_score: float = 6.0, **kwargs) -> List[str]:
        """Return only tickers above minimum inefficiency threshold."""
        scores = self.score_universe(tickers, **kwargs)
        return [s.ticker for s in scores if s.composite >= min_score]


# ── RebalancingFlowPredictor ────────────────────────────────────────────

class RebalancingFlowPredictor:
    """Predicts direction and magnitude of leveraged ETP rebalancing flows.

    R = AUM × |L-1| × |daily_return|
    Higher |underlying_return| = larger rebalancing = stronger signal.
    """

    MIN_RETURN = 0.005   # 0.5%
    STRONG_RETURN = 0.015  # 1.5%

    def __init__(self, baselines: Optional[Dict[str, dict]] = None):
        self.baselines = baselines or BASELINE_SCORES

    def predict(self, ticker: str,
                underlying_return: float,
                aum_override: Optional[float] = None) -> Optional[RebalancingPrediction]:
        """Predict rebalancing for a single ETP. Returns None if below threshold."""
        meta = self.baselines.get(ticker)
        if meta is None:
            return None

        leverage = meta.get("leverage", 1)
        abs_return = abs(underlying_return)
        if abs_return < self.MIN_RETURN:
            return None

        aum = aum_override or meta.get("est_aum", 0)
        adv = meta.get("adv", 1)

        rebal_notional = aum * abs(leverage - 1) * abs_return

        # Direction
        if leverage > 0:
            direction = "BUY" if underlying_return > 0 else "SELL"
        else:
            direction = "SELL" if underlying_return > 0 else "BUY"

        rebal_pct_adv = (rebal_notional / adv * 100) if adv > 0 else 0

        # Signal strength
        if abs_return >= self.STRONG_RETURN:
            strength = "STRONG"
            confidence = min(0.95, 0.7 + abs_return * 10)
        elif abs_return >= 0.01:
            strength = "MODERATE"
            confidence = min(0.80, 0.5 + abs_return * 10)
        else:
            strength = "WEAK"
            confidence = min(0.60, 0.3 + abs_return * 10)

        return RebalancingPrediction(
            ticker=ticker,
            underlying_ticker=meta.get("underlying", ""),
            leverage=leverage,
            underlying_return=underlying_return,
            estimated_aum=aum,
            rebalancing_direction=direction,
            rebalancing_notional=round(rebal_notional, 2),
            rebalancing_as_pct_adv=round(rebal_pct_adv, 2),
            signal_strength=strength,
            confidence=round(confidence, 3),
        )

    def scan_universe(self, underlying_returns: Dict[str, float]) -> List[RebalancingPrediction]:
        """Scan all ETPs for rebalancing signals, sorted by notional descending."""
        predictions = []
        for ticker, meta in self.baselines.items():
            underlying = meta.get("underlying", "")
            ret = underlying_returns.get(underlying, 0.0)
            pred = self.predict(ticker, ret)
            if pred is not None:
                predictions.append(pred)
        predictions.sort(key=lambda p: p.rebalancing_notional, reverse=True)
        return predictions


# ── NAVDiscountTracker ──────────────────────────────────────────────────

class NAVDiscountTracker:
    """Monitors ETP market prices vs indicative NAV for mean-reversion signals.

    Thresholds: 0.3% small, 0.5% medium, 1.0% large.
    """

    SMALL_THRESHOLD = 0.003
    MEDIUM_THRESHOLD = 0.005
    LARGE_THRESHOLD = 0.010

    def __init__(self, etp_nav_data: Optional[Dict[str, dict]] = None):
        """etp_nav_data: ticker → {previous_nav, underlying_ref_price, leverage,
                                    management_fee_daily, underlying_ticker}"""
        self.nav_data = etp_nav_data or {}
        self.signal_history: List[NAVSignal] = []

    def calculate_inav(self, ticker: str,
                       current_underlying_price: float) -> Optional[float]:
        """Calculate indicative NAV for a leveraged ETP."""
        data = self.nav_data.get(ticker)
        if data is None:
            return None

        prev_nav = data.get("previous_nav", 0)
        ref_price = data.get("underlying_ref_price", 0)
        leverage = data.get("leverage", 1)
        daily_fee = data.get("management_fee_daily", 0.0)

        if ref_price == 0 or prev_nav == 0:
            return None

        underlying_return = (current_underlying_price - ref_price) / ref_price
        etp_return = leverage * underlying_return - daily_fee
        return round(prev_nav * (1 + etp_return), 4)

    def check_premium_discount(self, ticker: str,
                                market_price: float,
                                current_underlying_price: float) -> Optional[NAVSignal]:
        """Check if market price deviates from iNAV. Returns signal if significant."""
        inav = self.calculate_inav(ticker, current_underlying_price)
        if inav is None or inav <= 0:
            return None

        premium_pct = (market_price - inav) / inav
        abs_premium = abs(premium_pct)

        if abs_premium < self.SMALL_THRESHOLD:
            signal, magnitude = "HOLD", "SMALL"
        elif abs_premium < self.MEDIUM_THRESHOLD:
            signal = "SELL" if premium_pct > 0 else "BUY"
            magnitude = "SMALL"
        elif abs_premium < self.LARGE_THRESHOLD:
            signal = "SELL" if premium_pct > 0 else "BUY"
            magnitude = "MEDIUM"
        else:
            signal = "SELL" if premium_pct > 0 else "BUY"
            magnitude = "LARGE"

        nav_signal = NAVSignal(
            ticker=ticker,
            market_price=market_price,
            indicative_nav=inav,
            premium_pct=round(premium_pct * 100, 3),
            signal=signal,
            magnitude=magnitude,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if signal != "HOLD":
            self.signal_history.append(nav_signal)
            # Keep history bounded
            if len(self.signal_history) > 500:
                self.signal_history = self.signal_history[-250:]

        return nav_signal

    def scan_universe(self, market_prices: Dict[str, float],
                      underlying_prices: Dict[str, float]) -> List[NAVSignal]:
        """Scan all ETPs for NAV signals, return actionable only."""
        signals = []
        for ticker, data in self.nav_data.items():
            mkt = market_prices.get(ticker)
            und = data.get("underlying_ticker", "")
            und_price = underlying_prices.get(und)
            if mkt is None or und_price is None:
                continue
            sig = self.check_premium_discount(ticker, mkt, und_price)
            if sig is not None and sig.signal != "HOLD":
                signals.append(sig)
        signals.sort(key=lambda s: abs(s.premium_pct), reverse=True)
        return signals


# ── Nightly Integration ─────────────────────────────────────────────────

def run_nightly_inefficiency_scan() -> Dict:
    """Nightly step: score all instruments and save report.

    Returns summary dict for recommendations.
    """
    scorer = InefficiencyScorer()
    scores = scorer.score_universe()

    tradeable = [s for s in scores if s.composite >= InefficiencyScorer.MIN_COMPOSITE]
    avoid = [s for s in scores if s.composite < InefficiencyScorer.MIN_COMPOSITE]

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_instruments": len(scores),
        "tradeable_count": len(tradeable),
        "avoid_count": len(avoid),
        "top_5": [s.to_dict() for s in scores[:5]],
        "prime_tickers": [s.ticker for s in scores if s.rating == "Prime"],
        "strong_tickers": [s.ticker for s in scores if s.rating == "Strong"],
    }

    # Save full report
    out_dir = Path("/app/data/analytics")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(str(out_dir / "inefficiency_scores.json"), "w") as f:
            json.dump([s.to_dict() for s in scores], f, indent=2)
    except Exception as e:
        log.warning("Failed to save inefficiency scores: %s", e)

    return summary
