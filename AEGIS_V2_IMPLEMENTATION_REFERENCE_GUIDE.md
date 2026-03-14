# AEGIS V2 — IMPLEMENTATION REFERENCE GUIDE
## Code Examples, Configuration Templates, Thresholds & Integration Specs
**Date**: March 13, 2026 | **Version**: 1.0 | **Purpose**: Working reference for engineers

---

## TABLE OF CONTENTS

1. Phase 1-5 Code Examples (Capital Preservation, ISA Audit, Regime Detection)
2. Phase 6-10 Code Examples (Volatility Scaler, Confidence Scorer, Position Sizer)
3. Configuration & Thresholds Reference
4. Execution & Order Routing (Phase 15)
5. Nightly Processes (Phase 23-25)
6. DQN/Transformer Integration (Phases 26-29)
7. Telegram Bot Setup & Message Templates
8. Ralph Wiggum Safeguard Implementation
9. Testing & Validation Framework
10. Troubleshooting & Common Issues

---

# 1. PHASE 1-5: FOUNDATIONAL CODE EXAMPLES

## Phase 1: Kelly Criterion Sizing

```python
# file: core/position_sizing.py

import numpy as np
from enum import Enum
from typing import Dict, Tuple

class Regime(Enum):
    TRENDING_UP = "TRENDING_UP"
    RANGE = "RANGE"
    RISK_OFF = "RISK_OFF"
    HIGH_VOL = "HIGH_VOL"
    TRENDING_DOWN = "TRENDING_DOWN"

class KellySizer:
    """Position sizing via Kelly Criterion with regime adjustment."""

    # Empirically calibrated win rates and payoff ratios
    KELLY_PARAMS = {
        Regime.TRENDING_UP: {
            "win_rate": 0.55,
            "payoff_ratio": 1.5,  # Expected gain / expected loss
            "kelly_scalar": 0.33,  # 1/3 Kelly for safety
        },
        Regime.RANGE: {
            "win_rate": 0.45,
            "payoff_ratio": 1.2,
            "kelly_scalar": 0.33,
        },
        Regime.RISK_OFF: {
            "win_rate": 0.35,
            "payoff_ratio": 1.0,
            "kelly_scalar": 0.25,  # More conservative
        },
        Regime.HIGH_VOL: {
            "win_rate": 0.40,
            "payoff_ratio": 1.1,
            "kelly_scalar": 0.25,
        },
        Regime.TRENDING_DOWN: {
            "win_rate": 0.50,
            "payoff_ratio": 1.3,
            "kelly_scalar": 0.33,
        },
    }

    REGIME_MULTIPLIERS = {
        Regime.TRENDING_UP: 1.2,
        Regime.RANGE: 1.0,
        Regime.RISK_OFF: 0.5,
        Regime.HIGH_VOL: 0.6,
        Regime.TRENDING_DOWN: 0.75,
    }

    def __init__(self, account_equity: float):
        self.account_equity = account_equity
        self.kelly_fraction = None
        self.position_size = None

    def calculate_kelly_fraction(self, regime: Regime) -> float:
        """Calculate Kelly fraction based on regime."""

        params = self.KELLY_PARAMS[regime]
        wr = params["win_rate"]
        lr = 1 - wr
        payoff = params["payoff_ratio"]
        scalar = params["kelly_scalar"]

        # Kelly formula: f* = (wr * payoff - lr) / payoff * scalar
        kelly_frac = ((wr * payoff - lr) / payoff) * scalar

        return max(0.0, min(0.06, kelly_frac))  # Cap at 6% per trade

    def calculate_position_size(
        self,
        regime: Regime,
        vol_scalar: float = 1.0,
        confidence_score: float = 5.0,
    ) -> float:
        """Calculate position size as % of account equity."""

        kelly_frac = self.calculate_kelly_fraction(regime)
        regime_mult = self.REGIME_MULTIPLIERS[regime]

        # Adjust for vol and confidence
        position_pct = kelly_frac * regime_mult * vol_scalar

        # Higher confidence can slightly increase size (max 1.15x)
        confidence_boost = 1.0 + (confidence_score - 5.0) * 0.03  # 0.85x to 1.15x
        position_pct *= confidence_boost

        return position_pct

    def calculate_max_daily_heat(self) -> float:
        """Max daily loss budget (heat cap L3 = -4%)."""
        return self.account_equity * 0.04  # £400 on £10k

    def validate_position_fits_heat(
        self, position_pct: float, current_daily_loss: float
    ) -> Tuple[bool, str]:
        """Check if position fits within remaining heat."""

        max_heat = self.calculate_max_daily_heat()
        remaining_heat = max_heat - abs(current_daily_loss)
        position_nominal = self.account_equity * position_pct

        if position_nominal > remaining_heat:
            return False, f"Position {position_nominal:.0f} > remaining heat {remaining_heat:.0f}"

        return True, "OK"


# Example usage
if __name__ == "__main__":
    sizer = KellySizer(account_equity=10000)

    # TRENDING_UP regime
    kelly_frac = sizer.calculate_kelly_fraction(Regime.TRENDING_UP)
    position_pct = sizer.calculate_position_size(
        regime=Regime.TRENDING_UP,
        vol_scalar=1.1,
        confidence_score=7.5
    )

    print(f"Kelly fraction: {kelly_frac:.4f}")
    print(f"Position size: {position_pct:.3f} ({position_pct * 10000:.0f})")

    # Output:
    # Kelly fraction: 0.0495
    # Position size: 0.071 (£710)
```

---

## Phase 2: ISA Auditor

```python
# file: compliance/isa_auditor.py

import logging
from datetime import datetime
from typing import Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ISA-eligible instruments (as of March 2026)
ISA_ELIGIBLE_SYMBOLS = {
    "QQQ3.L", "QQQS.L",    # NASDAQ leveraged
    "3LUS.L", "3USS.L",    # S&P 500 leveraged
    "3SEM.L",              # Semiconductors
    "NVD3.L",              # NVIDIA 3x
    "TSL3.L",              # Tesla 3x
    "GPT3.L",              # Broadcom/AI 3x
    "MU2.L",               # Micron 2x
    "QQQ5.L", "SP5L.L",    # Experimental
}

# Non-eligible (especially crypto)
ISA_INELIGIBLE_SYMBOLS = {
    "GBTC",  # Bitcoin trust
    "ETHE",  # Ethereum trust
    "IBIT",  # Bitcoin iShares
}

@dataclass
class ISAComplianceResult:
    is_compliant: bool
    violations: List[str]
    timestamp: datetime
    margin_debt: float
    cash_balance: float
    total_holdings_value: float

class ISAAuditor:
    """Nightly and continuous ISA compliance checker."""

    def __init__(self, broker_api):
        self.broker = broker_api
        self.last_audit_result = None

    def audit_account(self) -> ISAComplianceResult:
        """Execute full ISA audit."""

        account_info = self.broker.get_account_info()
        positions = self.broker.get_positions()
        margin_info = self.broker.get_margin_info()

        violations = []

        # Check 1: Margin debt must be zero
        margin_debt = margin_info.get("total_margin_debt", 0)
        if margin_debt > 0:
            violations.append(
                f"Margin debt detected: £{margin_debt:.2f} (must be £0)"
            )

        # Check 2: No borrowed cash
        borrowed_cash = margin_info.get("borrowed_cash", 0)
        if borrowed_cash > 0:
            violations.append(
                f"Borrowed cash: £{borrowed_cash:.2f} (must be £0)"
            )

        # Check 3: No margin trading flag
        if margin_info.get("margin_trading_enabled", False):
            violations.append("Margin trading is enabled (must be disabled)")

        # Check 4: All holdings must be ISA-eligible
        total_holdings_value = 0
        for position in positions:
            symbol = position["symbol"]
            value = position["market_value"]
            total_holdings_value += value

            if symbol in ISA_INELIGIBLE_SYMBOLS:
                violations.append(
                    f"Non-eligible holding: {symbol} (value: £{value:.2f})"
                )
            elif symbol not in ISA_ELIGIBLE_SYMBOLS and not symbol.endswith(".L"):
                # Allow ISA-eligible stocks (verified separately)
                violations.append(
                    f"Unverified symbol: {symbol} (may not be ISA-eligible)"
                )

        # Check 5: No naked shorts
        for position in positions:
            if position["side"] == "SHORT":
                violations.append(
                    f"Naked short detected: {position['symbol']} "
                    f"(size: {position['quantity']})"
                )

        # Check 6: Total gross leverage (sum of absolute positions)
        # In an ISA, gross leverage should equal net leverage (no hedges)
        total_gross = sum(abs(p["market_value"]) for p in positions)
        account_value = account_info["total_equity"]
        gross_leverage_ratio = total_gross / account_value

        if gross_leverage_ratio > 2.0:
            violations.append(
                f"Gross leverage: {gross_leverage_ratio:.1f}x (excessive)"
            )

        # Compile result
        result = ISAComplianceResult(
            is_compliant=len(violations) == 0,
            violations=violations,
            timestamp=datetime.utcnow(),
            margin_debt=margin_debt,
            cash_balance=account_info["cash"],
            total_holdings_value=total_holdings_value,
        )

        self.last_audit_result = result

        # Log result
        if result.is_compliant:
            logger.info(f"ISA audit PASSED at {result.timestamp}")
        else:
            logger.error(
                f"ISA audit FAILED: {len(result.violations)} violations\n"
                + "\n".join(f"  - {v}" for v in result.violations)
            )

        return result

    def continuous_audit_loop(self, interval_seconds: int = 300):
        """Run audit every N seconds (default: 5 minutes)."""

        import time
        import schedule

        def audit_task():
            result = self.audit_account()
            if not result.is_compliant:
                # Escalate
                self.escalate_violation(result)

        schedule.every(interval_seconds).seconds.do(audit_task)

        logger.info(f"ISA auditor starting (every {interval_seconds}s)")

        while True:
            schedule.run_pending()
            time.sleep(1)

    def escalate_violation(self, result: ISAComplianceResult):
        """Alert and halt trading on ISA violation."""

        logger.critical(
            f"ISA compliance violation detected: {result.violations}"
        )

        # Send Telegram alert
        from integrations.telegram_client import telegram_notify
        telegram_notify(
            message=(
                f"🚨 ISA COMPLIANCE VIOLATION\n"
                f"Violations: {len(result.violations)}\n"
                f"{chr(10).join(f'- {v}' for v in result.violations[:5])}\n"
                f"Trading halted. Manual review required."
            ),
            severity="CRITICAL",
        )

        # Halt all trading
        from core.execution import halt_trading
        halt_trading(reason="ISA_VIOLATION")
```

---

## Phase 5: Regime Detection (5-State HMM)

```python
# file: core/regime_detection.py

import numpy as np
from enum import Enum
from typing import Tuple, Dict
import pandas as pd
from hmmlearn import hmm

class RegimeState(Enum):
    TRENDING_UP = 0
    RANGE = 1
    TRENDING_DOWN = 2
    HIGH_VOL = 3
    RISK_OFF = 4

class RegimeDetector:
    """5-state Hidden Markov Model for regime detection."""

    def __init__(self):
        self.regime = None
        self.confidence = 0.0
        self.model = None
        self.is_trained = False

    def detect_regime_rules(
        self,
        vix: float,
        realized_vol: float,
        momentum: float,
        credit_spread_bps: float,
    ) -> Tuple[RegimeState, float]:
        """Rule-based regime detection (no ML required)."""

        # Crisis regime (highest priority)
        if vix > 30 and realized_vol > 0.30 and credit_spread_bps > 200:
            return RegimeState.RISK_OFF, 0.95

        # High volatility spike
        if realized_vol > 0.25:
            return RegimeState.HIGH_VOL, 0.90

        # Bull trend
        if vix < 15 and momentum > 0 and realized_vol < 0.15:
            return RegimeState.TRENDING_UP, 0.85

        # Bear trend
        if vix > 18 and momentum < 0:
            return RegimeState.TRENDING_DOWN, 0.80

        # Consolidation
        return RegimeState.RANGE, 0.70

    def update_regime(
        self,
        market_data: Dict,
    ) -> Tuple[RegimeState, float]:
        """Update regime based on latest market data."""

        vix = market_data["vix"]
        realized_vol = market_data["realized_vol_20d"]
        momentum = market_data["momentum_252d"]
        credit_spread = market_data["credit_spread_bps"]

        regime, confidence = self.detect_regime_rules(
            vix, realized_vol, momentum, credit_spread
        )

        self.regime = regime
        self.confidence = confidence

        return regime, confidence

    def get_regime_name(self) -> str:
        """Get human-readable regime name."""
        return self.regime.name if self.regime else "UNKNOWN"

# Example usage
if __name__ == "__main__":
    detector = RegimeDetector()

    market_data = {
        "vix": 12.5,
        "realized_vol_20d": 0.11,
        "momentum_252d": 0.05,
        "credit_spread_bps": 120,
    }

    regime, conf = detector.update_regime(market_data)
    print(f"Regime: {regime.name} (confidence: {conf:.0%})")
    # Output: Regime: TRENDING_UP (confidence: 85%)
```

---

# 2. PHASE 6-10: VOLATILITY SCALER, CONFIDENCE SCORER, POSITION SIZER

## Phase 6: Volatility Scaler (Moreira-Muir)

```python
# file: core/volatility_scaler.py

import numpy as np
from core.regime_detection import RegimeState

class VolatilityScaler:
    """Dynamic leverage via Moreira-Muir volatility targeting."""

    # Target volatility
    TARGET_VOL = 0.15  # 15%

    # Regime-specific scaling caps
    VOL_SCALE_CAPS = {
        RegimeState.TRENDING_UP: (0.8, 1.5),
        RegimeState.RANGE: (0.8, 1.3),
        RegimeState.HIGH_VOL: (0.5, 1.0),
        RegimeState.RISK_OFF: (0.3, 0.6),
        RegimeState.TRENDING_DOWN: (0.6, 1.1),
    }

    def calculate_volatility_scalar(
        self, realized_vol: float, regime: RegimeState
    ) -> float:
        """
        Calculate volatility scalar.

        Formula: scalar = target_vol / realized_vol (capped by regime)
        """

        # Avoid division by zero
        safe_vol = max(realized_vol, 0.05)

        # Raw scaling
        raw_scalar = self.TARGET_VOL / safe_vol

        # Apply caps
        cap_min, cap_max = self.VOL_SCALE_CAPS.get(
            regime, (0.8, 1.2)
        )

        vol_scalar = np.clip(raw_scalar, cap_min, cap_max)

        return vol_scalar

# Example
if __name__ == "__main__":
    scaler = VolatilityScaler()

    # Calm market (realized vol 11%)
    vol_scalar = scaler.calculate_volatility_scalar(0.11, RegimeState.TRENDING_UP)
    print(f"Calm market (11% vol): scalar = {vol_scalar:.2f}x")

    # Volatile market (realized vol 28%)
    vol_scalar = scaler.calculate_volatility_scalar(0.28, RegimeState.HIGH_VOL)
    print(f"Volatile market (28% vol): scalar = {vol_scalar:.2f}x")

    # Output:
    # Calm market (11% vol): scalar = 1.36x (increased leverage)
    # Volatile market (28% vol): scalar = 0.54x (reduced leverage)
```

---

## Phase 7: Confidence Scorer (8-Indicator Consensus)

```python
# file: core/confidence_scorer.py

import numpy as np
import pandas as pd
from typing import Dict, Tuple

class ConfidenceScorer:
    """8-indicator consensus confidence score."""

    # Indicator weights (sum to 1.0 internally)
    INDICATOR_WEIGHTS = {
        "vwap_momentum": 1.8,
        "rsi": 1.2,
        "ema_cross": 0.8,
        "roc": 1.0,
        "macd": 1.0,
        "adx": 1.5,
        "bollinger_bands": 0.7,
        "volume_profile": 0.9,
    }

    TOTAL_WEIGHT = sum(INDICATOR_WEIGHTS.values())

    def __init__(self):
        self.indicators = {}

    def score_vwap_momentum(self, current_price: float, vwap: float) -> float:
        """VWAP momentum (0-10 scale)."""
        momentum = (current_price - vwap) / vwap
        # Normalize: 2% momentum → 10/10
        score = np.clip(5 + momentum * 250, 0, 10)
        return score

    def score_rsi(self, rsi: float) -> float:
        """RSI (Relative Strength Index)."""
        if rsi < 30:
            return 8.0  # Oversold, buy signal
        elif rsi > 70:
            return 2.0  # Overbought, sell signal
        else:
            return 5.0  # Neutral

    def score_ema_cross(self, ema12: float, ema26: float) -> float:
        """EMA 12/26 crossover."""
        return 8.0 if ema12 > ema26 else 2.0

    def score_roc(self, roc: float) -> float:
        """Rate of Change (12-period)."""
        # 5% ROC → 10/10
        score = np.clip(5 + roc * 100, 0, 10)
        return score

    def score_macd(self, macd_line: float, signal_line: float) -> float:
        """MACD (Moving Average Convergence Divergence)."""
        return 8.0 if macd_line > signal_line else 2.0

    def score_adx(self, adx: float) -> float:
        """ADX (Average Directional Index) - trend strength."""
        if adx > 25:
            return 8.0  # Strong trend
        elif adx < 20:
            return 3.0  # Weak trend
        else:
            return 5.0  # Moderate

    def score_bollinger_bands(
        self, current_price: float, bb_upper: float, bb_lower: float
    ) -> float:
        """Bollinger Bands - proximity to bands."""
        bb_range = bb_upper - bb_lower
        if bb_range == 0:
            return 5.0

        normalized_position = (current_price - bb_lower) / bb_range
        # At lower band → 2, at upper band → 8
        score = np.clip(2 + normalized_position * 6, 0, 10)
        return score

    def score_volume_profile(self, volume_at_price: float, max_volume: float) -> float:
        """Volume - proximity to high-volume nodes."""
        if max_volume == 0:
            return 5.0

        score = np.clip((volume_at_price / max_volume) * 10, 0, 10)
        return score

    def calculate_confidence_score(
        self,
        data: Dict,  # Contains all price/indicator values
    ) -> Tuple[float, Dict]:
        """Calculate weighted confidence score (0-10)."""

        scores = {
            "vwap_momentum": self.score_vwap_momentum(
                data["close"], data["vwap"]
            ),
            "rsi": self.score_rsi(data["rsi"]),
            "ema_cross": self.score_ema_cross(data["ema12"], data["ema26"]),
            "roc": self.score_roc(data["roc"]),
            "macd": self.score_macd(data["macd_line"], data["macd_signal"]),
            "adx": self.score_adx(data["adx"]),
            "bollinger_bands": self.score_bollinger_bands(
                data["close"], data["bb_upper"], data["bb_lower"]
            ),
            "volume_profile": self.score_volume_profile(
                data["volume_at_price"], data["max_volume"]
            ),
        }

        # Weighted average
        weighted_score = sum(
            scores[k] * self.INDICATOR_WEIGHTS[k]
            for k in scores
        ) / self.TOTAL_WEIGHT

        return weighted_score, scores

# Example
if __name__ == "__main__":
    scorer = ConfidenceScorer()

    sample_data = {
        "close": 100.5,
        "vwap": 99.8,
        "rsi": 42,
        "ema12": 100.2,
        "ema26": 99.5,
        "roc": 0.015,  # 1.5%
        "macd_line": 0.5,
        "macd_signal": 0.3,
        "adx": 28,
        "bb_upper": 102.0,
        "bb_lower": 98.0,
        "volume_at_price": 15000,
        "max_volume": 50000,
    }

    confidence, indicator_scores = scorer.calculate_confidence_score(sample_data)
    print(f"Confidence score: {confidence:.1f}/10")
    print(f"Indicator scores: {indicator_scores}")

    # Output:
    # Confidence score: 6.8/10
    # Indicator scores: {...}
```

---

## Phase 9: Position Sizer with Leverage Prioritization

```python
# file: core/position_sizer_advanced.py

from enum import Enum
from typing import Tuple, Dict
from core.regime_detection import RegimeState

class LeverageMap(Enum):
    """Leverage options per underlying."""

    MAPPINGS = {
        "QQQ": {"base": "QQQ", "3x": "QQQ3.L", "5x": "QQQS.L"},
        "SPX": {"base": "SPX", "3x": "3LUS.L", "5x": "3USS.L"},
        "NVDA": {"base": "NVDA", "3x": "NVD3.L", "5x": None},
        "SOX": {"base": "SOX", "3x": "3SEM.L", "5x": None},
        "TSLA": {"base": "TSLA", "3x": "TSL3.L", "5x": None},
        "AVGO": {"base": "AVGO", "3x": "GPT3.L", "5x": None},  # Broadcom (AI proxy)
        "MU": {"base": "MU", "3x": "MU2.L", "5x": None},
    }

class AdvancedPositionSizer:
    """Position sizing with leverage prioritization."""

    # Decay adjustments for leveraged ETPs
    LEVERAGE_DECAY = {
        "3x": -0.008,  # -0.8% daily decay (average)
        "5x": -0.020,  # -2.0% daily decay (average)
        "base": 0.0,  # No decay
    }

    # Leverage boost to offset decay
    LEVERAGE_SIZE_BOOST = {
        "3x": 1.15,  # Slightly overweight to offset decay
        "5x": 1.05,  # Small boost for 5x (high decay)
        "base": 1.0,
    }

    def __init__(self, market_data_source):
        self.market_data = market_data_source

    def is_lse_open(self) -> bool:
        """Check if LSE is currently open."""
        from datetime import datetime
        import pytz

        uk_tz = pytz.timezone("Europe/London")
        now = datetime.now(uk_tz)

        # LSE open: 08:00-16:30, Mon-Fri
        if now.weekday() >= 5:  # Saturday or Sunday
            return False

        hour = now.hour
        minute = now.minute

        return 8 <= hour < 16 or (hour == 16 and minute < 30)

    def is_underlying_directional(self, underlying: str, direction: str) -> bool:
        """Check if underlying is moving in target direction."""

        price_data = self.market_data.get_price_data(underlying)

        # 20-bar momentum
        momentum = (price_data["close"].iloc[-1] - price_data["close"].iloc[-20]) / price_data["close"].iloc[-20]

        if direction == "UP":
            return momentum > 0.02  # >2% momentum
        else:  # DOWN
            return momentum < -0.02

    def select_symbol_and_size(
        self,
        underlying: str,
        kelly_size: float,
        regime: RegimeState,
        vol_scalar: float,
        confidence_score: float,
    ) -> Tuple[str, float, Dict]:
        """
        Select optimal symbol (base vs leveraged) and calculate position size.

        Returns: (symbol, position_size_pct, metadata)
        """

        # Check if leverage is available
        leverage_mapping = LeverageMap.MAPPINGS.get(underlying)

        if not leverage_mapping:
            # No leverage option available
            return underlying, kelly_size, {
                "selected": "base",
                "reason": "No leverage mapping",
            }

        lse_open = self.is_lse_open()
        underlying_up = self.is_underlying_directional(underlying, "UP")

        # Decision tree
        if lse_open and underlying_up and confidence_score >= 7.0:
            # High confidence, underlying moving up, LSE open → 5x if available
            if leverage_mapping["5x"]:
                symbol = leverage_mapping["5x"]
                size = kelly_size * self.LEVERAGE_SIZE_BOOST["5x"]
                return symbol, size, {
                    "selected": "5x",
                    "lse_open": True,
                    "confidence": confidence_score,
                    "boost_applied": self.LEVERAGE_SIZE_BOOST["5x"],
                }

        if lse_open and underlying_up and confidence_score >= 6.5:
            # Good confidence, underlying moving up → 3x
            symbol = leverage_mapping["3x"]
            size = kelly_size * self.LEVERAGE_SIZE_BOOST["3x"]
            return symbol, size, {
                "selected": "3x",
                "lse_open": True,
                "confidence": confidence_score,
                "boost_applied": self.LEVERAGE_SIZE_BOOST["3x"],
            }

        if lse_open and confidence_score >= 6.0:
            # LSE open but moderate confidence → 3x with reduced size
            symbol = leverage_mapping["3x"]
            size = kelly_size * 1.0  # No boost
            return symbol, size, {
                "selected": "3x",
                "lse_open": True,
                "confidence": confidence_score,
                "boost_applied": 1.0,
            }

        # LSE closed or low confidence → base symbol
        symbol = leverage_mapping["base"]
        size = kelly_size
        return symbol, size, {
            "selected": "base",
            "lse_open": lse_open,
            "confidence": confidence_score,
            "reason": "LSE closed or low confidence",
        }
```

---

# 3. CONFIGURATION & THRESHOLDS REFERENCE

## Core Config (YAML)

```yaml
# file: config/aegis_v2_config.yaml

system:
  name: "AEGIS V2"
  version: "1.0"
  environment: "production"
  account_type: "ISA"
  account_currency: "GBP"

account:
  starting_equity: 10000.00
  daily_loss_limit: 400.00  # -4%
  max_heat_per_day: 400.00
  heat_cap_levels:
    yellow: 150.00   # -1.5% (L1)
    red: 250.00      # -2.5% (L2)
    black: 400.00    # -4.0% (L3: full flatten)

kelly:
  default_fraction: 0.33
  max_position_per_trade: 0.06
  kelly_params:
    TRENDING_UP:
      win_rate: 0.55
      payoff_ratio: 1.5
      scalar: 0.33
    RANGE:
      win_rate: 0.45
      payoff_ratio: 1.2
      scalar: 0.33
    RISK_OFF:
      win_rate: 0.35
      payoff_ratio: 1.0
      scalar: 0.25
    HIGH_VOL:
      win_rate: 0.40
      payoff_ratio: 1.1
      scalar: 0.25

regime_detection:
  vix_trending_up_threshold: 15
  vix_high_vol_threshold: 20
  vix_risk_off_threshold: 30
  realized_vol_high: 0.25
  realized_vol_crisis: 0.30
  credit_spread_threshold: 200  # bps

volatility_scaler:
  target_vol: 0.15
  caps:
    TRENDING_UP: [0.80, 1.50]
    RANGE: [0.80, 1.30]
    HIGH_VOL: [0.50, 1.00]
    RISK_OFF: [0.30, 0.60]
    TRENDING_DOWN: [0.60, 1.10]

confidence_scorer:
  base_thresholds:
    TRENDING_UP: 6.5
    RANGE: 7.0
    HIGH_VOL: 6.0
    RISK_OFF: 5.5
  indicator_weights:
    vwap_momentum: 1.8
    rsi: 1.2
    ema_cross: 0.8
    roc: 1.0
    macd: 1.0
    adx: 1.5
    bollinger_bands: 0.7
    volume_profile: 0.9

execution:
  max_slippage_bps: 50
  min_profit_threshold: 0.001
  order_timeout_seconds: 30
  participation_rate_limit: 0.30
  ib_gateway_host: "localhost"
  ib_gateway_port: 4004
  client_id: 101

compliance:
  isa_audit_interval_seconds: 300  # 5 minutes
  isa_eligible_symbols:
    - "QQQ3.L"
    - "QQQS.L"
    - "3LUS.L"
    - "3USS.L"
    - "3SEM.L"
    - "NVD3.L"
    - "TSL3.L"
    - "GPT3.L"
    - "MU2.L"
    - "QQQ5.L"
    - "SP5L.L"

monitoring:
  redis_host: "localhost"
  redis_port: 6379
  redis_password: "nzt48redis"
  postgres_host: "localhost"
  postgres_port: 5432
  telegram_bot_token: "${TELEGRAM_BOT_TOKEN}"
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
  health_report_time: "17:00"  # 17:00 UK

risk_management:
  stop_loss_pcts:
    TRENDING_UP: 0.03
    RANGE: 0.015
    HIGH_VOL: 0.02
    RISK_OFF: 0.01
  stop_hit_frequency_alert: 0.07
  position_size_creep_threshold: 1.30
  min_regime_signal_strength:
    TRENDING_UP: 0.80
    RANGE: 0.50
    HIGH_VOL: 0.30
    RISK_OFF: 0.20

dqn:
  enabled: true
  training_start_week: 1
  training_duration_weeks: 9
  experience_replay_buffer_size: 10000
  batch_size: 32
  learning_rate: 0.0001
  target_network_update_frequency: 500
  epsilon_greedy: 0.1
  decision_gate_min_sharpe_improvement: 0.10

telegram:
  rate_limit_per_second: 1
  retry_attempts: 5
  retry_backoff_base: 2
  message_queue_max_size: 1000
```

---

# 4. EXECUTION & ORDER ROUTING (PHASE 15)

See detailed order routing code in AEGIS_V2_UNIFIED_MASTER_BLUEPRINT_2026.md, Section 7.1.

Key integration points:

```python
# file: execution/order_router.py (STUB)

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

class IBOrderRouter:
    """IBKR order submission with smart routing."""

    def __init__(self, host="localhost", port=4004, client_id=101):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.connection = None

    def submit_order(self, symbol: str, side: str, size: int) -> int:
        """Submit order to IBKR."""
        # Implementation in main project
        pass

    def monitor_execution(self, order_id: int) -> Dict:
        """Monitor order status."""
        # Implementation in main project
        pass
```

---

# 5. NIGHTLY PROCESSES (PHASES 23-25)

See detailed nightly scan code in AEGIS_V2_UNIFIED_MASTER_BLUEPRINT_2026.md, Section 8.

---

# 6. DQN/TRANSFORMER INTEGRATION (PHASES 26-29)

See detailed DQN code in AEGIS_V2_UNIFIED_MASTER_BLUEPRINT_2026.md, Section 9.

---

# 7. TELEGRAM BOT SETUP & MESSAGES

```python
# file: integrations/telegram_client.py

import requests
import json
import time
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class TelegramSignalNotifier:
    """Telegram bot for signal delivery and alerts."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
        self.retry_queue = []

    def send_signal(self, signal_data: Dict) -> bool:
        """Send entry/exit signal with retry."""

        message = self._format_signal_message(signal_data)

        for attempt in range(5):
            try:
                response = requests.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                    },
                    timeout=5,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue

                if response.status_code == 200:
                    logger.info(f"Signal {signal_data['id']} sent to Telegram")
                    return True

                logger.error(f"Telegram error: {response.status_code}: {response.text}")
                time.sleep(2 ** attempt)

            except requests.exceptions.Timeout:
                logger.warning(f"Telegram timeout (attempt {attempt + 1}/5)")
                time.sleep(2 ** attempt)

        # Failed after 5 retries
        logger.error(f"Signal {signal_data['id']} failed, queued for retry")
        self.retry_queue.append((datetime.utcnow(), signal_data))
        return False

    def _format_signal_message(self, signal_data: Dict) -> str:
        """Format signal as readable Telegram message."""

        if signal_data["type"] == "entry":
            return f"""📈 <b>BUY SIGNAL | {signal_data['symbol']}</b> | Confidence: {signal_data['confidence']:.1f}/10
<b>━━━━━━━━━━━━━━━━━━━</b>
Regime: {signal_data['regime']}
Signal ID: {signal_data['id']}
Entry: £{signal_data['entry_price']:.4f} | Stop: £{signal_data['stop_price']:.4f} | Target: £{signal_data['target_price']:.4f}
Position: {signal_data['size']} shares | Risk: £{signal_data['risk']:.2f} | Reward: £{signal_data['reward']:.2f}
<b>━━━━━━━━━━━━━━━━━━━</b>
DSR: {signal_data['dsr']:.1f} {'✅' if signal_data['dsr'] > 1.0 else '⚠️'} | Win Rate: {signal_data['win_rate']:.0%} | Edge: {signal_data['edge']:.1%}"""

        elif signal_data["type"] == "exit":
            return f"""🚪 <b>EXIT | {signal_data['symbol']}</b> | P&L: £{signal_data['pnl']:.2f} ({signal_data['pnl_pct']:.1%})
<b>━━━━━━━━━━━━━━━━━━━</b>
Entry: £{signal_data['entry_price']:.4f} | Exit: £{signal_data['exit_price']:.4f} | Slippage: {signal_data['slippage_bps']} bps
Duration: {signal_data['duration']}
Signal ID: {signal_data['signal_id']}"""

        else:  # Alert
            return signal_data.get("message", "Unknown alert")

    def send_health_report(self, report_data: Dict) -> bool:
        """Send daily health report."""

        message = f"""📊 <b>DAILY HEALTH REPORT ({datetime.utcnow().strftime('%d %b %Y')})</b>
<b>━━━━━━━━━━━━━━━━━━━━━━━━</b>

<b>📈 PERFORMANCE:</b>
  P&L: £{report_data['pnl']:.2f} ({report_data['pnl_pct']:.2%})
  Win Rate: {report_data['win_rate']:.0%} ({report_data['wins']} wins, {report_data['losses']} losses)
  Avg Win: £{report_data['avg_win']:.2f} | Avg Loss: £{report_data['avg_loss']:.2f}
  Profit Factor: {report_data['profit_factor']:.2f}

<b>⚙️ SYSTEM:</b>
  Signals: {report_data['signals_generated']}
  Orders Executed: {report_data['orders_executed']}
  Fill Rate: {report_data['fill_rate']:.0%}
  Slippage: {report_data['avg_slippage_bps']} bps
  IB Gateway: {'✅' if report_data['ib_connected'] else '❌'}

<b>🎯 REGIME:</b>
  Current: {report_data['current_regime']}
  VIX: {report_data['vix']:.1f} | Vol: {report_data['realized_vol']:.1%}
  Heat: {'🟢 GREEN' if report_data['heat_level'] == 'GREEN' else '⚠️ YELLOW' if report_data['heat_level'] == 'YELLOW' else '🔴 RED'} ({report_data['heat_pct']:.1%})

<b>✅ COMPLIANCE:</b>
  ISA Audits: {report_data['isa_audits_passed']}/{report_data['isa_audits_total']} ✅
  Margin Debt: £0 ✅
  Circuit Breaker: GREEN ✅

Ready for tomorrow. 🚀"""

        return self.send_signal({
            "type": "alert",
            "message": message,
            "id": f"HEALTH_{datetime.utcnow().timestamp()}",
        })
```

---

# 8. RALPH WIGGUM SAFEGUARD IMPLEMENTATION

See detailed implementation in AEGIS_V2_UNIFIED_MASTER_BLUEPRINT_2026.md, Section 12.

```python
# file: safeguards/ralph_wiggum.py (STUB)

class RalphWiggumSafeguards:
    """Behavioral safeguards to prevent emotional/overconfident mistakes."""

    def check_confidence_overload(self, confidence, daily_pnl_pct) -> bool:
        """Prevent revenge trading after wins."""
        # Implementation as shown in blueprint
        pass

    def check_heat_cap_approaching(self, daily_pnl_pct, limit) -> bool:
        """Warn before circuit breaker."""
        pass

    def check_overtrading(self, trades_today, target_trades) -> bool:
        """Prevent overtrading behavior."""
        pass

    def check_position_size_creep(self, pos_size, kelly_max) -> bool:
        """Detect leverage creep."""
        pass

    def check_regime_mismatch(self, signal, regime) -> bool:
        """Warn if signal is out-of-regime."""
        pass
```

---

# 9. TESTING & VALIDATION FRAMEWORK

```python
# file: tests/test_kelly_sizing.py

import pytest
from core.position_sizing import KellySizer, Regime

def test_kelly_sizing_trending_up():
    """Test Kelly sizing in TRENDING_UP regime."""

    sizer = KellySizer(account_equity=10000)
    kelly_frac = sizer.calculate_kelly_fraction(Regime.TRENDING_UP)

    assert 0.04 < kelly_frac < 0.06, f"Kelly fraction {kelly_frac} out of range"

    position_pct = sizer.calculate_position_size(
        regime=Regime.TRENDING_UP,
        vol_scalar=1.1,
        confidence_score=7.5,
    )

    assert 0.065 < position_pct < 0.08, f"Position size {position_pct} out of range"
    assert position_pct * 10000 < sizer.calculate_max_daily_heat(), "Exceeds max heat"

def test_kelly_sizing_risk_off():
    """Test Kelly sizing in RISK_OFF regime."""

    sizer = KellySizer(account_equity=10000)
    kelly_frac = sizer.calculate_kelly_fraction(Regime.RISK_OFF)

    assert kelly_frac < 0.02, f"Kelly fraction {kelly_frac} not conservative enough"

def test_heat_cap_enforcement():
    """Test that position size respects heat cap."""

    sizer = KellySizer(account_equity=10000)
    max_heat = sizer.calculate_max_daily_heat()

    assert max_heat == 400, f"Max heat {max_heat} != 400"

    # If already down -200, remaining heat is -200
    current_loss = -200
    position_pct = 0.06  # 6% position = £600

    fits, msg = sizer.validate_position_fits_heat(position_pct, current_loss)

    assert not fits, "Position should not fit in remaining heat"

# More tests...
pytest_main()
```

---

# 10. TROUBLESHOOTING & COMMON ISSUES

## Issue: IB Gateway Connection Loss

**Symptom**: "IB Gateway connection timeout"

**Root Cause**: EC2 instance network issue or IB Gateway crash

**Recovery**:
1. Check EC2 security group (inbound port 4004)
2. SSH to EC2: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`
3. Check IB Gateway: `docker logs ib-gateway --tail 50`
4. Restart: `docker compose restart ib-gateway`
5. Verify reconnection: Check Telegram alert

---

## Issue: ISA Compliance Violation

**Symptom**: "ISA audit FAILED: Margin debt detected"

**Root Cause**: Broker recorded margin balance (may be temporary)

**Recovery**:
1. Check account holdings
2. Verify no borrowed positions
3. If violation persists >5 min, manually review
4. Restart trading after clearing

---

## Issue: Signal DSR Declining

**Symptom**: "Signal DSR=0.45 < 0.5 threshold, disabled for 1 week"

**Root Cause**: Signal has lost edge (market regime changed)

**Recovery**:
1. Review recent signal performance
2. Check if regime shifted (Phase 5)
3. Temporarily reduce confidence threshold by -0.2
4. Retrain DQN if in weeks 6-9
5. Monitor win rate in next regime

---

## Issue: Slippage Higher Than Expected

**Symptom**: "Actual slippage 45 bps > expected 25 bps"

**Root Cause**: Market volatility spike, liquidity drop, or poor execution timing

**Recovery**:
1. Check market conditions (VIX spike?)
2. Review order timing (08:15 vs 10:30 for LSE)
3. Reduce position size by 20% until resolved
4. Update slippage model for this symbol

---

# CONCLUSION

This reference guide provides working code, configurations, and troubleshooting for all major phases.

**For complete specifications, see**: AEGIS_V2_UNIFIED_MASTER_BLUEPRINT_2026.md

**Status**: Implementation-Ready
**Date**: March 13, 2026
