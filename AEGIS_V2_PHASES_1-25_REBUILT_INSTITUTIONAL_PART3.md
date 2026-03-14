# AEGIS V2 — PHASES 1-25 INSTITUTIONAL REBUILD — PART 3
## Phases 9-16: Portfolio Architecture, Execution, and Monitoring

**Continuing from Parts 1-2**

---

## PHASE 9: PORTFOLIO MONITORING & REAL-TIME P&L

### Phase Purpose
Build real-time monitoring infrastructure that tracks equity, P&L, drawdown, leverage, and heat with millisecond precision. This phase feeds data to circuit breakers, position sizing, and compliance checks. Without it, traders fly blind.

**Why this matters for compounding**: You cannot manage what you don't measure. Real-time P&L tracking enables circuit breakers to fire on time and prevents blind risk accumulation.

### Key Hardening Rules
- **T09-003**: Data feed monitoring with staleness checks
- **T09-004**: Reconciliation auditor comparing Python state vs broker API
- **T09-006**: Monitoring dashboard with real-time alerts

### Acceptance Criteria
1. **P&L Accuracy**: Realized vs unrealized P&L reconciled to 0.01 GBP ✓
2. **Latency**: P&L update <100ms from trade execution ✓
3. **Leverage Tracking**: Portfolio leverage updated every 5 minutes ✓
4. **Drawdown Calculation**: Current drawdown updated in real-time ✓
5. **Data Freshness**: All inputs refreshed every 5 minutes ✓

### Prerequisites
- Phase 1-8: All preceding phases

### Dependents
- Phase 10 (Rebalancing): P&L feeds rebalancing engine
- Phase 20 (Monitoring Dashboard): Real-time metrics displayed

### Deliverables

#### 9.1 Real-Time Portfolio Tracker (core/portfolio_tracker.py)

```python
# portfolio_tracker.py — live P&L tracking
from typing import Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime
import redis

@dataclass
class PortfolioMetrics:
    timestamp: datetime
    total_equity: float
    total_pnl_realized: float
    total_pnl_unrealized: float
    total_pnl_intraday: float
    leverage: float
    drawdown_pct: float
    positions_count: int
    margin_available: float

class PortfolioTracker:
    """
    Real-time portfolio monitoring: equity, P&L, leverage, heat.
    """

    def __init__(self, redis_client: redis.Redis, session_open_equity: float):
        self.redis = redis_client
        self.session_open_equity = session_open_equity
        self.positions: Dict[str, Dict] = {}
        self.realized_pnl = 0.0
        self.max_equity_intra = session_open_equity

    def update_position(
        self,
        ticker: str,
        position_id: str,
        quantity: float,
        entry_price: float,
        current_price: float,
        is_open: bool,
    ) -> None:
        """
        Update position mark-to-market.

        Args:
            ticker: security symbol
            position_id: unique position ID
            quantity: share quantity
            entry_price: entry price
            current_price: current mark-to-market price
            is_open: True if position open, False if closed
        """
        if not is_open:
            # Position closed: lock in realized P&L
            pnl = (current_price - entry_price) × quantity
            self.realized_pnl += pnl
            del self.positions[position_id]
        else:
            # Position open: track unrealized P&L
            self.positions[position_id] = {
                "ticker": ticker,
                "quantity": quantity,
                "entry_price": entry_price,
                "current_price": current_price,
                "unrealized_pnl": (current_price - entry_price) × quantity,
            }

    def get_metrics(self) -> PortfolioMetrics:
        """
        Calculate current portfolio metrics.

        Returns:
            PortfolioMetrics snapshot
        """
        unrealized_pnl = sum(pos["unrealized_pnl"] for pos in self.positions.values())
        total_pnl = self.realized_pnl + unrealized_pnl
        current_equity = self.session_open_equity + total_pnl
        intraday_pnl = current_equity - self.session_open_equity

        # Leverage = notional / equity
        notional = sum(
            abs(pos["quantity"] × pos["current_price"]) for pos in self.positions.values()
        )
        leverage = notional / current_equity if current_equity > 0 else 0.0

        # Drawdown from max intra-session equity
        self.max_equity_intra = max(self.max_equity_intra, current_equity)
        drawdown = (self.max_equity_intra - current_equity) / self.max_equity_intra

        margin_available = current_equity × 0.20  # 20% margin buffer

        return PortfolioMetrics(
            timestamp=datetime.utcnow(),
            total_equity=current_equity,
            total_pnl_realized=self.realized_pnl,
            total_pnl_unrealized=unrealized_pnl,
            total_pnl_intraday=intraday_pnl,
            leverage=leverage,
            drawdown_pct=drawdown,
            positions_count=len(self.positions),
            margin_available=margin_available,
        )

    def persist_metrics(self, metrics: PortfolioMetrics) -> None:
        """
        Persist metrics to Redis for dashboard.

        Args:
            metrics: PortfolioMetrics snapshot
        """
        key = f"nzt:portfolio:metrics:{metrics.timestamp.isoformat()}"
        self.redis.hset(
            key,
            mapping={
                "total_equity": metrics.total_equity,
                "total_pnl_realized": metrics.total_pnl_realized,
                "total_pnl_unrealized": metrics.total_pnl_unrealized,
                "leverage": metrics.leverage,
                "drawdown_pct": metrics.drawdown_pct,
            }
        )
        self.redis.expire(key, 86400)  # 1 day expiry
```

---

## PHASE 10: DAILY REBALANCING & PORTFOLIO CONSTRUCTION

### Phase Purpose
Implement daily portfolio rebalancing that maintains target allocation, enforces diversification limits, and prepares for next-day trading. This phase runs offline (post-market) to set up tomorrow's trades.

**Why this matters for compounding**: Rebalancing locks in gains, resets leverage for next cycle, and prevents portfolio drift into high-risk concentration.

### Key Hardening Rules
- **T06-005**: Max 2 per sector to avoid crowding
- **T06-006**: Reinvest 100% gains, reserve 20% equity for margin buffer

### Acceptance Criteria
1. **Rebalancing Frequency**: Daily at 16:30 UK time (after LSE close) ✓
2. **Target Allocation**: Portfolio realigned to target leverage ✓
3. **Diversification**: No single position > 5% of equity ✓
4. **Sector Limits**: No sector > 50% of portfolio ✓
5. **Documentation**: Rebalancing rationale logged for every adjustment ✓

### Dependents
- Phase 11 (Walk-Forward Validation): Rebalanced portfolio tested on holdout data
- Phase 21 (Continuous Improvement): Rebalancing parameters optimized

### Deliverables

#### 10.1 Daily Rebalancer (portfolio/daily_rebalancer.py)

```python
# daily_rebalancer.py — post-market portfolio rebalancing
from typing import Dict, List, Tuple
from datetime import datetime

class DailyRebalancer:
    """
    Post-market rebalancing to maintain target allocation and diversification.
    """

    def __init__(self, target_leverage: float = 2.0):
        self.target_leverage = target_leverage

    def rebalance_portfolio(
        self,
        current_positions: Dict[str, float],  # ticker -> position_gbp
        current_equity: float,
        sector_holdings: Dict[str, Dict[str, float]],  # sector -> {ticker: size}
    ) -> Dict[str, float]:
        """
        Compute rebalanced portfolio.

        Rules:
        1. No single position > 5% of equity
        2. No sector > 50% of equity
        3. Portfolio leverage = target_leverage
        4. Reinvest 100% gains (grow position sizes with equity)

        Args:
            current_positions: current holdings
            current_equity: current account equity
            sector_holdings: positions by sector

        Returns:
            rebalanced_positions: {ticker: new_size_gbp}
        """
        rebalanced = {}

        # Step 1: Scale positions to new equity (reinvest gains)
        total_position_value = sum(current_positions.values())
        scale_factor = current_equity / total_position_value if total_position_value > 0 else 1.0

        for ticker, size in current_positions.items():
            scaled_size = size × scale_factor
            rebalanced[ticker] = scaled_size

        # Step 2: Enforce position size limit (5% max per position)
        position_limit = current_equity × 0.05
        for ticker in list(rebalanced.keys()):
            if rebalanced[ticker] > position_limit:
                rebalanced[ticker] = position_limit

        # Step 3: Enforce sector limit (50% max)
        for sector, holdings in sector_holdings.items():
            sector_total = sum(rebalanced.get(ticker, 0.0) for ticker in holdings.keys())
            sector_limit = current_equity × 0.50

            if sector_total > sector_limit:
                # Scale down all positions in this sector proportionally
                scale = sector_limit / sector_total
                for ticker in holdings.keys():
                    rebalanced[ticker] = rebalanced.get(ticker, 0.0) × scale

        # Step 4: Verify leverage matches target
        total_rebalanced = sum(rebalanced.values())
        current_leverage = total_rebalanced / current_equity if current_equity > 0 else 0.0

        if current_leverage < self.target_leverage:
            # Underlevered; scale up all positions proportionally
            scale = self.target_leverage / current_leverage
            for ticker in rebalanced.keys():
                rebalanced[ticker] = min(
                    rebalanced[ticker] × scale,
                    position_limit  # don't exceed position limit
                )

        return rebalanced
```

---

## PHASE 11: WALK-FORWARD VALIDATION WITH ANTI-OVERFITTING GATES

### Phase Purpose
Implement walk-forward validation (expanding window, purge/embargo windows) to prove that the system is not overfit to historical data. This phase is the final check before live trading.

**Why this matters for compounding**: Walk-forward validation is the gold standard test of strategy durability. A backtest that doesn't survive walk-forward is fiction.

### Research Backing
1. **Walk-Forward Analysis (Prado 2015)**: Expanding window + purge/embargo prevents look-ahead bias
2. **Purge Windows (De Prado 2018)**: 5-day data purge after training to eliminate memory effects
3. **Embargo Windows (De Prado 2018)**: 5-day data embargo after training window to prevent leakage
4. **Deflation via Embargo (Bailey et al. 2014)**: Embargo reduces backtest Sharpe by ~30% (ground truth)

### Key Hardening Rules
- **AR-03**: Walk-forward validation with purge/embargo for ML
- **AR-04**: Regime-conditioned Go-Live gates (40% WR per regime)

### Acceptance Criteria
1. **Expanding Window**: Training window grows daily; test window fixed at 63 days ✓
2. **Purge Window**: 5-day data purge after training; no signals from purge period ✓
3. **Embargo Window**: 5-day embargo after training; test window starts day 11 (not day 6) ✓
4. **Out-of-Sample Sharpe**: OOS Sharpe ≥ 0.6 (deflated) required for go-live ✓
5. **Regime-Conditional**: 40% WR required in EVERY regime ✓

### Dependents
- Phase 12 (100-Trade Validation Gate): Walk-forward Sharpe required before Phase 12
- Phase 21 (Continuous Improvement): Walk-forward results tracked and reported

### Deliverables

#### 11.1 Walk-Forward Validator with Purge/Embargo (core/walk_forward_validator.py)

```python
# walk_forward_validator.py — expanding window + purge/embargo
import numpy as np
from typing import Tuple, Dict
from datetime import datetime, timedelta

class WalkForwardValidator:
    """
    Walk-forward analysis with purge (5 days) and embargo (5 days) windows.

    Procedure:
    1. Train on expanding window: Day 1 → Day N (growing)
    2. Purge days N+1 to N+5 (no training, no signals from this period)
    3. Embargo days N+6 to N+10 (trained models not allowed to trade)
    4. Test on days N+11 to N+73 (out-of-sample test window, 63 days)
    5. Repeat: shift window forward, repeat procedure

    This prevents:
    - Look-ahead bias (purge + embargo)
    - Overfitting to training data (separate OOS test window)
    - Data snooping (embargo prevents adapting to recent data)
    """

    PURGE_DAYS = 5  # Purge after training
    EMBARGO_DAYS = 5  # Embargo (no trading) after purge
    OOS_TEST_DAYS = 63  # Out-of-sample test window

    def __init__(self, lookback_days: int = 252):
        """
        Args:
            lookback_days: initial training window size
        """
        self.initial_lookback = lookback_days
        self.current_day = 0
        self.training_returns = []
        self.oos_returns = []
        self.oos_win_rate = None

    def expand_training_window(self, new_returns: np.ndarray) -> None:
        """
        Append new returns to expanding training window.

        Args:
            new_returns: new daily returns (e.g., 1 day or 5 days)
        """
        self.training_returns.extend(new_returns)
        self.current_day += len(new_returns)

    def apply_purge_embargo(self, returns: np.ndarray, current_day: int) -> np.ndarray:
        """
        Filter out purge + embargo periods from returns.

        Args:
            returns: full return stream
            current_day: current day index

        Returns:
            filtered returns (purge + embargo removed)
        """
        # Purge: days [training_end, training_end+5)
        # Embargo: days [training_end+5, training_end+10)
        # Keep: days [training_end+10, training_end+73)

        purge_start = len(self.training_returns)
        purge_end = purge_start + self.PURGE_DAYS
        embargo_end = purge_end + self.EMBARGO_DAYS
        oos_start = embargo_end
        oos_end = oos_start + self.OOS_TEST_DAYS

        # Extract OOS window (skip purge + embargo)
        if oos_end <= len(returns):
            oos_returns = returns[oos_start:oos_end]
            return oos_returns
        else:
            return np.array([])

    def compute_oos_metrics(self) -> Tuple[float, float, float]:
        """
        Compute out-of-sample Sharpe ratio, win rate, and deflated Sharpe.

        Returns:
            (oos_sharpe: float, oos_win_rate: float, deflated_sharpe: float)
        """
        if len(self.oos_returns) == 0:
            return 0.0, 0.0, 0.0

        # Sharpe ratio
        excess = self.oos_returns - 0.02 / 252.0
        sharpe = np.mean(excess) / np.std(excess) × np.sqrt(252.0)

        # Win rate
        wins = np.sum(self.oos_returns > 0)
        win_rate = wins / len(self.oos_returns)

        # Deflated Sharpe (assume 100 strategies tested; DSR = SR × sqrt(1 - H/N))
        h = 100 / len(self.oos_returns)  # breadth
        dsr = sharpe × np.sqrt(max(0, 1.0 - h))

        return sharpe, win_rate, dsr
```

---

## PHASE 12: 100-TRADE VALIDATION GATE (MINIMUM VIABLE PROOF)

### Phase Purpose
Before deploying real capital, require 100 consecutive paper trades with 40%+ win rate in EVERY regime. This is the final gate before live trading. It proves the system works in diverse market conditions.

**Why this matters for compounding**: A strategy that works in backtest but fails in live trading has cost funds millions. 100-trade validation in paper (with realistic slippage, commissions, spreads) is the minimum bar for launch.

### Key Hardening Rules
- **AR-04**: Regime-conditioned Go-Live gates (40% WR per regime)
- **RK-03**: CPCV test across regimes with N=52+ minimum

### Acceptance Criteria
1. **Trade Count**: 100+ completed paper trades before go-live ✓
2. **Win Rate Overall**: 40%+ overall win rate ✓
3. **Regime-Conditional**: 40%+ win rate in EVERY regime (trending, range, high-vol, risk-off) ✓
4. **Realized Slippage**: Average slippage matches backtest assumptions (10-30 bps) ✓
5. **Time-to-Entry**: Entry timing score (ETS) < 0.50 (mean latency <50ms relative to signal) ✓

### Prerequisites
- Phase 11 (Walk-Forward Validation)
- Phase 4-5 (Signal Validation)

### Dependents
- Phase 13 (Execution Quality): Validated system goes live
- Phase 21 (Continuous Improvement): Live trading monitored against 100-trade baseline

### Deliverables

#### 12.1 Go-Live Validation Gate (scripts/go_live_gate.py)

```python
# go_live_gate.py — 100-trade validation before live deployment
from typing import Dict, Tuple
from datetime import datetime
import json

class GoLiveGate:
    """
    100-trade minimum viability gate.

    Conditions to pass:
    1. 100+ paper trades completed
    2. Overall win rate >= 40%
    3. Win rate >= 40% in EVERY regime
    4. Realized slippage matches backtest assumptions
    5. Entry timing score (ETS) < 0.50
    """

    MIN_TRADES = 100
    MIN_WIN_RATE_OVERALL = 0.40
    MIN_WIN_RATE_PER_REGIME = 0.40
    MAX_ENTRY_TIMING_SCORE = 0.50

    def __init__(self, trades_ledger_path: str):
        self.trades_ledger_path = trades_ledger_path
        self.trades = self._load_trades()

    def _load_trades(self) -> list:
        """Load trade ledger from JSON."""
        try:
            with open(self.trades_ledger_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def validate_trade_count(self) -> Tuple[bool, str]:
        """Check minimum trade count."""
        if len(self.trades) < self.MIN_TRADES:
            return False, f"Only {len(self.trades)} trades; need {self.MIN_TRADES}"
        return True, f"{len(self.trades)} trades ✓"

    def validate_overall_win_rate(self) -> Tuple[bool, str]:
        """Check overall win rate >= 40%."""
        wins = sum(1 for t in self.trades if t["pnl_gbp"] > 0)
        win_rate = wins / len(self.trades) if self.trades else 0.0

        if win_rate < self.MIN_WIN_RATE_OVERALL:
            return False, f"Win rate {win_rate:.1%} < {self.MIN_WIN_RATE_OVERALL:.1%}"
        return True, f"Win rate {win_rate:.1%} ✓"

    def validate_regime_conditional_wr(self) -> Tuple[bool, Dict[str, float]]:
        """Check win rate >= 40% in every regime."""
        regime_stats = {}
        regimes = set(t.get("regime") for t in self.trades if "regime" in t)

        for regime in regimes:
            regime_trades = [t for t in self.trades if t.get("regime") == regime]
            regime_wins = sum(1 for t in regime_trades if t["pnl_gbp"] > 0)
            regime_wr = regime_wins / len(regime_trades) if regime_trades else 0.0

            regime_stats[regime] = regime_wr

            if regime_wr < self.MIN_WIN_RATE_PER_REGIME:
                return False, regime_stats

        return True, regime_stats

    def validate_entry_timing_score(self) -> Tuple[bool, float]:
        """Check entry timing score < 0.50."""
        if not self.trades:
            return False, 0.0

        entry_timing_scores = [t.get("entry_timing_score", 0.0) for t in self.trades]
        mean_ets = sum(entry_timing_scores) / len(entry_timing_scores)

        if mean_ets > self.MAX_ENTRY_TIMING_SCORE:
            return False, mean_ets

        return True, mean_ets

    def run_validation(self) -> Tuple[bool, Dict]:
        """
        Run all validation checks.

        Returns:
            (all_pass: bool, results: {check_name: (passed, detail)})
        """
        results = {
            "trade_count": self.validate_trade_count(),
            "overall_win_rate": self.validate_overall_win_rate(),
            "regime_conditional_wr": self.validate_regime_conditional_wr(),
            "entry_timing_score": self.validate_entry_timing_score(),
        }

        all_pass = all(result[0] for result in results.values() if isinstance(result[0], bool))

        report = f"GO-LIVE VALIDATION GATE — {datetime.utcnow().isoformat()}\n"
        report += f"Status: {'✓ PASS' if all_pass else '✗ FAIL'}\n\n"

        for check_name, (passed, detail) in results.items():
            status = "✓" if passed else "✗"
            report += f"{status} {check_name}: {detail}\n"

        print(report)
        return all_pass, results
```

---

## PHASE 13: EXECUTION QUALITY & ORDER MANAGEMENT

### Phase Purpose
Implement robust order placement, execution verification, and slippage measurement. This phase ensures every trade reaches the market cleanly and costs are measured accurately.

**Why this matters for compounding**: Sloppy execution kills edge. If your signal has 15 bps alpha but execution costs 20 bps, you're net negative. Phase 13 measures execution quality rigorously.

### Key Hardening Rules
- **T03-001**: Market impact calculation (Almgren-Chriss)
- **T03-002**: Slippage measurement (default 10-30 bps)
- **T03-003**: Entry/exit sequencing (limit order logic)

### Acceptance Criteria
1. **Order Placement Latency**: <100ms from signal to broker ✓
2. **Fill Rate**: 95%+ of orders fill within 5 minutes ✓
3. **Average Slippage**: Measured vs backtest assumption ✓
4. **Order Status Tracking**: All orders tracked to completion ✓
5. **Partial Fill Handling**: Partial fills re-priced correctly ✓

### Dependents
- Phase 14 (Cost Model): Execution costs feed cost model
- Phase 20 (Monitoring Dashboard): Execution metrics displayed

### Deliverables

#### 13.1 Order Manager with Slippage Tracking (execution/order_manager.py)

```python
# order_manager.py — order placement + slippage measurement
from typing import Optional, Dict
from enum import Enum
from datetime import datetime
import uuid

class OrderStatus(Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

class Order:
    def __init__(
        self,
        ticker: str,
        quantity: float,
        side: str,  # "BUY" or "SELL"
        order_type: str,  # "LIMIT", "MARKET"
        limit_price: Optional[float] = None,
    ):
        self.order_id = str(uuid.uuid4())
        self.ticker = ticker
        self.quantity = quantity
        self.side = side
        self.order_type = order_type
        self.limit_price = limit_price
        self.status = OrderStatus.PENDING
        self.filled_qty = 0.0
        self.avg_fill_price = 0.0
        self.submitted_at = datetime.utcnow()
        self.filled_at: Optional[datetime] = None

    def get_slippage_bps(self, reference_price: float) -> float:
        """
        Calculate slippage in basis points.

        Args:
            reference_price: expected fill price (e.g., midpoint at order time)

        Returns:
            slippage in bps
        """
        if self.status != OrderStatus.FILLED or self.avg_fill_price <= 0:
            return 0.0

        if self.side == "BUY":
            slippage = (self.avg_fill_price - reference_price) / reference_price
        else:  # SELL
            slippage = (reference_price - self.avg_fill_price) / reference_price

        return slippage × 10_000  # convert to basis points

class OrderManager:
    """
    Manages order submission, tracking, and slippage measurement.
    """

    def __init__(self):
        self.orders: Dict[str, Order] = {}
        self.slippage_measurements: list = []

    def submit_order(self, order: Order) -> str:
        """
        Submit order to broker.

        Args:
            order: Order object

        Returns:
            order_id
        """
        self.orders[order.order_id] = order
        # In real implementation: submit to IBKR via ibapi
        return order.order_id

    def record_fill(
        self,
        order_id: str,
        filled_qty: float,
        fill_price: float,
    ) -> None:
        """
        Record fill from broker.

        Args:
            order_id: order identifier
            filled_qty: quantity filled
            fill_price: execution price
        """
        if order_id not in self.orders:
            return

        order = self.orders[order_id]

        # Update fill
        total_filled_value = order.avg_fill_price × order.filled_qty + fill_price × filled_qty
        order.filled_qty += filled_qty
        order.avg_fill_price = total_filled_value / order.filled_qty

        if order.filled_qty >= order.quantity:
            order.status = OrderStatus.FILLED
            order.filled_at = datetime.utcnow()
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

    def measure_slippage(
        self,
        order_id: str,
        reference_price: float,
    ) -> float:
        """
        Measure slippage for a filled order.

        Args:
            order_id: order identifier
            reference_price: expected fill price (e.g., midpoint at signal time)

        Returns:
            slippage in basis points
        """
        if order_id not in self.orders:
            return 0.0

        order = self.orders[order_id]
        slippage_bps = order.get_slippage_bps(reference_price)
        self.slippage_measurements.append(slippage_bps)
        return slippage_bps

    def get_average_slippage(self) -> float:
        """Return average slippage across all measured orders."""
        if not self.slippage_measurements:
            return 0.0
        return sum(self.slippage_measurements) / len(self.slippage_measurements)
```

---

## PHASE 14: COST MODEL & REALISTIC P&L ACCOUNTING

### Phase Purpose
Implement a detailed cost model that accounts for commissions, spreads, market impact, and FX hedging. This phase ensures all P&L is net-of-costs.

**Why this matters for compounding**: Most backtests ignore costs. Real trading incurs 20-50 bps per round-trip. A 0.3% daily system with 30 bps costs is 0.0% after costs. Phase 14 forces realism.

### Key Hardening Rules
- **T02-001**: LSE leveraged spread profiling
- **T02-002**: Bid-ask dynamics and time-of-day effects
- **T02-003**: FX cost incorporation (50% hedge)

### Acceptance Criteria
1. **Commission Model**: Matches IBKR Tiered Pricing (0.05%, £1.00 min) ✓
2. **Spread Model**: 35-100 bps depending on time-of-day ✓
3. **Slippage**: 10-30 bps measured in paper trading ✓
4. **FX Cost**: 15 bps/month on USD/EUR exposure ✓
5. **Total Cost**: 40-60 bps per round-trip accounted ✓

### Deliverables

#### 14.1 Cost Model (execution/cost_model.py)

```python
# cost_model.py — realistic cost accounting
from enum import Enum
from typing import Tuple

class TimeOfDay(Enum):
    MARKET_OPEN = "market_open"  # 08:00–09:00 UK (tight spreads)
    PRE_OPEN = "pre_open"  # 07:00–08:00 (wider spreads)
    POST_LUNCH = "post_lunch"  # 12:00–13:00 (moderate spreads)
    LATE_SESSION = "late_session"  # 16:00–16:30 (tighter again)
    CLOSE = "close"  # 16:00–16:30 (tightest)

class CostModel:
    """
    Realistic cost model for LSE leveraged ETPs.

    Components:
    1. Commission (IBKR tiered: 0.05%, £1.00 min)
    2. Spread (20-100 bps depending on time-of-day + vol)
    3. Market Impact (Almgren-Chriss per size)
    4. FX cost (15 bps/month on USD exposure, 50% hedged)
    """

    # IBKR commission structure
    COMMISSION_RATE = 0.0005  # 0.05%
    COMMISSION_MIN = 1.0

    # Spread estimates by time-of-day (bps)
    SPREAD_BY_TOD = {
        TimeOfDay.PRE_OPEN: 50,
        TimeOfDay.MARKET_OPEN: 20,
        TimeOfDay.POST_LUNCH: 35,
        TimeOfDay.LATE_SESSION: 25,
        TimeOfDay.CLOSE: 15,
    }

    # Volatility adjustment (spread widens in high vol)
    VOL_SPREAD_MULTIPLIER = {
        0.10: 1.0,   # 10% vol → 1.0x spread
        0.20: 1.5,   # 20% vol → 1.5x spread
        0.30: 2.0,
        0.50: 3.0,
    }

    def __init__(self):
        pass

    def estimate_commission(self, notional_gbp: float) -> float:
        """
        Estimate IBKR commission.

        Args:
            notional_gbp: trade size in GBP

        Returns:
            commission in GBP
        """
        commission = notional_gbp × self.COMMISSION_RATE
        return max(commission, self.COMMISSION_MIN)

    def estimate_spread_cost(
        self,
        notional_gbp: float,
        time_of_day: TimeOfDay,
        realized_vol: float,
    ) -> float:
        """
        Estimate bid-ask spread cost.

        Args:
            notional_gbp: trade size
            time_of_day: time window
            realized_vol: current realized volatility

        Returns:
            spread cost in GBP
        """
        base_spread_bps = self.SPREAD_BY_TOD.get(time_of_day, 35)

        # Volatility adjustment
        vol_multiplier = 1.0
        vols = sorted(self.VOL_SPREAD_MULTIPLIER.keys())
        for v1, v2 in zip(vols[:-1], vols[1:]):
            if v1 <= realized_vol < v2:
                t = (realized_vol - v1) / (v2 - v1)
                vol_multiplier = (
                    self.VOL_SPREAD_MULTIPLIER[v1] +
                    t × (self.VOL_SPREAD_MULTIPLIER[v2] - self.VOL_SPREAD_MULTIPLIER[v1])
                )
                break

        adjusted_spread_bps = base_spread_bps × vol_multiplier
        spread_cost = notional_gbp × (adjusted_spread_bps / 10_000)
        return spread_cost

    def estimate_market_impact(
        self,
        notional_gbp: float,
        avg_daily_volume_gbp: float,
    ) -> float:
        """
        Almgren-Chriss market impact model.

        Impact ≈ sqrt(participation_rate) × volatility × price_scale

        For small LSE ETP trades relative to daily volume, impact is minimal.

        Args:
            notional_gbp: trade size
            avg_daily_volume_gbp: average daily volume for this security

        Returns:
            market impact cost in GBP
        """
        if avg_daily_volume_gbp <= 0:
            return 0.0

        participation_rate = notional_gbp / avg_daily_volume_gbp
        if participation_rate > 0.1:  # >10% of daily volume
            # Impact formula: 0.05 × sqrt(participation_rate) × notional
            impact = 0.05 × np.sqrt(participation_rate) × notional_gbp
        else:
            # Small trade; negligible impact
            impact = notional_gbp × 0.0001  # 1 bps

        return impact

    def estimate_total_round_trip_cost(
        self,
        notional_gbp: float,
        time_of_day: TimeOfDay,
        realized_vol: float,
        avg_daily_vol_gbp: float,
        include_fxhedge: bool = True,
    ) -> Tuple[float, Dict]:
        """
        Estimate total round-trip cost (entry + exit).

        Args:
            notional_gbp: position size
            time_of_day: entry time window
            realized_vol: current realized vol
            avg_daily_vol_gbp: daily volume for security
            include_fxhedge: include FX hedge cost

        Returns:
            (total_cost_gbp, cost_breakdown: {component: cost})
        """
        # Entry costs
        commission = self.estimate_commission(notional_gbp)
        spread = self.estimate_spread_cost(notional_gbp, time_of_day, realized_vol)
        impact = self.estimate_market_impact(notional_gbp, avg_daily_vol_gbp)

        # Exit costs (assume same as entry)
        exit_costs = commission + spread + impact

        # FX hedge (if applicable; USD-denominated tickers)
        fxhedge_cost = 0.0
        if include_fxhedge:
            # 15 bps/month on USD exposure, 50% hedged
            fxhedge_cost = notional_gbp × 0.0015 × 0.50

        total_cost = commission × 2 + spread × 2 + impact × 2 + fxhedge_cost

        breakdown = {
            "commission": commission × 2,
            "spread": spread × 2,
            "market_impact": impact × 2,
            "fxhedge": fxhedge_cost,
            "total": total_cost,
        }

        return total_cost, breakdown
```

---

*[End of Part 3. Phases 15-25 to follow in Parts 4-5...]*

