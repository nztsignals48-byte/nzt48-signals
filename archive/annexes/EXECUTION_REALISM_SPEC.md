# Execution Realism Specification

| Field           | Value                                    |
|-----------------|------------------------------------------|
| Document ID     | NZT48-ANNEX-EXR-001                      |
| Version         | 1.0                                      |
| Status          | **BINDING**                              |
| Classification  | Internal -- Investment Committee / PM     |
| Effective Date  | 2026-02-27                               |
| Review Cadence  | Monthly or after any live-mode activation |
| Owner           | PM / Execution Engineer                  |
| Cross-references | NZT48-ANNEX-RC-001 (Risk Constitution), NZT48-ANNEX-UGV-001 (Universe Governance Plan), NZT48-ANNEX-SGS-001 (Sanity Gate Spec) |

---

## 1. PURPOSE

### 1.1 Problem Statement

The NZT-48 virtual trader currently assumes instant fills at mid-price with zero slippage, zero commission, and no spread gating. This produces paper P&L figures that **systematically overstate** achievable live returns. For leveraged LSE ETPs -- which exhibit wider spreads, lower liquidity, and slower fill times than US large-cap equities -- the gap between paper and live performance can be 0.3-0.7% per round-trip trade. On a 2% daily target, this represents a 15-35% erosion of gross returns before any strategy alpha is considered.

### 1.2 Objective

Bridge the gap between paper and live execution by:

1. Modelling slippage, spread, commission, and liquidity constraints within the paper trading engine.
2. Documenting every execution assumption so that the transition to live trading introduces **no surprise degradation**.
3. Establishing a "realism gap" metric that quantifies the difference between naive and realistic P&L on every trade.
4. Providing conservative defaults that err on the side of understating paper returns.

### 1.3 Governing Principle

**If in doubt, assume worse execution.** Every parameter in this specification defaults to the pessimistic end of the observed range. A system that consistently beats its conservative paper results in live trading is preferable to one that consistently disappoints.

---

## 2. SLIPPAGE MODEL

### 2.1 Base Slippage Rates

| Product Class | Slippage Per Side | Round-Trip | Rationale |
|---------------|-------------------|------------|-----------|
| 3x leveraged ETPs | 0.15% | 0.30% | Typical bid-ask spread 0.20-0.40%; crossing half the spread = 0.10-0.20%; add 0.05% for market impact |
| 5x leveraged ETPs | 0.25% | 0.50% | Wider spreads (0.40-0.80%); lower liquidity; higher market impact |

### 2.2 Slippage Adjusters

Slippage increases from the base rate under the following conditions. Adjusters are **additive** (they stack):

| Condition | Additional Slippage (Per Side) | Trigger |
|-----------|-------------------------------|---------|
| Large position size | +0.05% | Notional > GBP 500 |
| Market open/close window | +0.10% | Trade executed in first or last 30 minutes of LSE session (08:00-08:30 or 16:00-16:30 UK) |
| High volatility regime | +0.10% | Volatility regime classifier returns `HIGH_VOL` |
| Low RVOL | +0.05% | Relative volume < 0.6x average |

### 2.3 Total Slippage Budget

| Scenario | Entry Slippage | Exit Slippage | Round-Trip Total |
|----------|---------------|---------------|------------------|
| Best case (3x, mid-session, normal vol, small size) | 0.15% | 0.15% | 0.30% |
| Typical case (3x, normal conditions, GBP 300 position) | 0.15% | 0.15% | 0.30% |
| Worst case (5x, open/close window, HIGH_VOL, large size) | 0.45% | 0.45% | 0.90% |

### 2.4 Impact on 2% Daily Target

| Scenario | Gross Target | Round-Trip Slippage | Commission (see Section 9) | Net Target Required |
|----------|-------------|---------------------|---------------------------|---------------------|
| Best case | 2.00% | 0.30% | 0.10% | 1.60% |
| Typical case | 2.00% | 0.30% | 0.10% | 1.60% |
| Worst case | 2.00% | 0.90% | 0.15% | 0.95% |

**Implication:** The strategy must generate gross alpha of 2.30-3.05% to deliver a net 2.00% after all execution costs. The S15 signal scoring must account for this by adding the estimated round-trip cost to the required move magnitude.

### 2.5 Implementation

```python
@dataclass
class SlippageEstimate:
    base_bps: float           # Base slippage in basis points (15 or 25)
    size_adj_bps: float       # Position size adjuster
    time_adj_bps: float       # Market open/close adjuster
    vol_adj_bps: float        # Volatility regime adjuster
    rvol_adj_bps: float       # Low relative volume adjuster
    total_per_side_bps: float # Sum of all components
    leverage_class: str       # '3x' or '5x'

def estimate_slippage(
    leverage_factor: int,
    notional_gbp: float,
    trade_time: datetime,
    vol_regime: str,
    rvol: float,
) -> SlippageEstimate:
    """
    Returns conservative slippage estimate for a single side (entry or exit).
    All values in basis points (1 bp = 0.01%).
    """
    base = 15.0 if leverage_factor <= 3 else 25.0
    size_adj = 5.0 if notional_gbp > 500 else 0.0

    uk_hour = trade_time.hour  # Assumes UK timezone
    uk_minute = trade_time.minute
    in_window = (uk_hour == 8 and uk_minute < 30) or (uk_hour >= 16)
    time_adj = 10.0 if in_window else 0.0

    vol_adj = 10.0 if vol_regime == 'HIGH_VOL' else 0.0
    rvol_adj = 5.0 if rvol < 0.6 else 0.0

    total = base + size_adj + time_adj + vol_adj + rvol_adj

    return SlippageEstimate(
        base_bps=base,
        size_adj_bps=size_adj,
        time_adj_bps=time_adj,
        vol_adj_bps=vol_adj,
        rvol_adj_bps=rvol_adj,
        total_per_side_bps=total,
        leverage_class=f'{leverage_factor}x',
    )
```

---

## 3. SPREAD GATING

### 3.1 Maximum Acceptable Spread for Entry

No trade may be entered if the current bid-ask spread exceeds the threshold for the product class. Spread-gated signals are logged, not discarded -- they may become eligible if the spread narrows within the signal's validity window.

| Product Class | Normal Regime Threshold | HIGH_VOL Regime Threshold | Action on Breach |
|---------------|------------------------|---------------------------|------------------|
| 3x leveraged ETPs | 0.50% | 0.75% | BLOCK entry, log `SPREAD_GATE_BLOCK` |
| 5x leveraged ETPs | 0.80% | 1.20% | BLOCK entry, log `SPREAD_GATE_BLOCK` |

### 3.2 Spread Calculation

```
spread_pct = (ask - bid) / mid * 100
where mid = (ask + bid) / 2
```

### 3.3 Spread Data Source Priority

1. **Primary:** Real-time bid/ask from yfinance (fields: `bid`, `ask`).
2. **Fallback:** Estimate from intraday high-low range of the most recent 5-minute bar: `estimated_spread = (high - low) / mid * 50` (assumes spread is approximately 50% of the 5-min range for low-liquidity ETPs).
3. **Last resort:** If no spread data is available, **apply maximum slippage assumption** for the product class. The trade is permitted but penalized with worst-case slippage. Log `SPREAD_DATA_UNAVAILABLE`.

### 3.4 Implementation

```python
def check_spread_gate(
    bid: float | None,
    ask: float | None,
    leverage_factor: int,
    vol_regime: str,
    fallback_high: float | None = None,
    fallback_low: float | None = None,
) -> tuple[bool, float, str]:
    """
    Returns (gate_passed: bool, spread_pct: float, tag: str).
    """
    if bid is not None and ask is not None and bid > 0 and ask > bid:
        mid = (ask + bid) / 2
        spread_pct = (ask - bid) / mid * 100
        source = 'LIVE_QUOTE'
    elif fallback_high is not None and fallback_low is not None:
        mid = (fallback_high + fallback_low) / 2
        spread_pct = (fallback_high - fallback_low) / mid * 50
        source = 'ESTIMATED_5MIN'
    else:
        # No data: assume worst case, allow trade with max slippage
        return True, -1.0, 'SPREAD_DATA_UNAVAILABLE'

    if vol_regime == 'HIGH_VOL':
        threshold = 0.75 if leverage_factor <= 3 else 1.20
    else:
        threshold = 0.50 if leverage_factor <= 3 else 0.80

    if spread_pct > threshold:
        return False, spread_pct, f'SPREAD_GATE_BLOCK_{source}_{spread_pct:.3f}%'

    return True, spread_pct, f'SPREAD_OK_{source}'
```

---

## 4. FILL ASSUMPTIONS

### 4.1 Paper Mode Fill Model

Paper mode fills are **not** at mid-price. They are adjusted to reflect the cost of crossing the spread and absorbing market impact.

| Fill Type | Price Calculation | Rationale |
|-----------|-------------------|-----------|
| Entry (buy) | `mid_price + (mid_price * slippage_pct / 100)` | Buyer crosses the spread; fill is worse than mid |
| Exit at stop (sell) | `stop_price - (stop_price * slippage_pct / 100)` | Stop triggers in adverse conditions; fill is worse than stop level |
| Exit at target (sell) | `target_price - (target_price * slippage_pct / 100)` | Conservative: assume you don't get the exact target price |
| Exit at time limit (sell) | `current_price - (current_price * slippage_pct / 100)` | Forced exit; no price improvement expected |

### 4.2 Partial Fills

| Mode | Handling |
|------|----------|
| Paper | Not modeled. Assume 100% fill on every order. |
| Live (future) | Minimum fill ratio: 80%. If less than 80% of the order fills within 5 seconds, cancel the remainder. Do not chase partial fills with market orders. |

### 4.3 Fill Delay

| Mode | Delay | Notes |
|------|-------|-------|
| Paper | 0 seconds | Instantaneous fill at adjusted price |
| Live (future) | 1-3 seconds expected | LSE ETPs with low liquidity may take longer. If fill not received within 10 seconds, cancel and re-evaluate. |

### 4.4 Implementation

```python
def compute_paper_fill_price(
    signal_price: float,
    slippage_pct: float,
    fill_type: str,  # 'ENTRY' | 'STOP' | 'TARGET' | 'TIME_EXIT'
) -> float:
    """
    Adjusts signal price for slippage. Always moves the fill price
    against the trader (worse execution).
    """
    slip = signal_price * slippage_pct / 100

    if fill_type == 'ENTRY':
        return signal_price + slip  # Pay more
    elif fill_type in ('STOP', 'TARGET', 'TIME_EXIT'):
        return signal_price - slip  # Receive less
    else:
        raise ValueError(f'Unknown fill_type: {fill_type}')
```

---

## 5. ORDER TYPE SPECIFICATION

### 5.1 Paper Mode

All orders in paper mode are **simulated market orders** with slippage-adjusted fill prices (see Section 4). No order book interaction is modeled.

### 5.2 Live Mode (Recommended Configuration)

| Order Purpose | Order Type | Offset | Time-in-Force | Rationale |
|---------------|-----------|--------|---------------|-----------|
| Entry | LIMIT BUY | Signal price + 0.10% | IOC (Immediate or Cancel) | Aggressive limit that is likely to fill but prevents overpaying if price spikes. IOC ensures no stale orders left on the book. |
| Stop loss | STOP-LIMIT | Stop trigger at stop price; limit at stop price - 0.20% | GTC (Good Till Cancel) | Stop triggers near-market behavior; 0.20% offset provides fill cushion without excessive slippage. GTC ensures the stop persists. |
| Profit target | LIMIT SELL | Target price (exact) | GTC (Good Till Cancel) | Passive order; sits on the book and waits. Better fill quality than market orders. |

### 5.3 Order Management Rules

1. **One-cancels-other (OCO):** Stop and target orders must be linked as OCO. When one fills, the other is cancelled automatically.
2. **No modification:** Once placed, stop and target levels are not modified. Trailing stops are not used in V1.
3. **Manual override:** The kill switch (Telegram, file-based, API) cancels all open orders and liquidates all positions at market.

---

## 6. TIME-IN-TRADE LIMITS

### 6.1 Maximum Holding Period

| Track | Maximum Hold | Rationale |
|-------|-------------|-----------|
| Intraday scalp (default) | 120 minutes | Leveraged ETPs are designed for intraday use. Extended holds introduce compounding drag, volatility decay, and tracking error. |

### 6.2 Time-Decay Exit Logic

When a position has been held for 120 minutes without being stopped or targeted:

```
IF unrealized_pnl > 0:
    EXIT IMMEDIATELY at market (slippage-adjusted)
    Tag: TIME_DECAY_EXIT_PROFIT

ELIF unrealized_pnl < 0 AND abs(unrealized_pnl) <= 50% of stop_distance:
    HOLD TO STOP
    Tag: TIME_DECAY_HOLD_NEAR_STOP

ELIF unrealized_pnl < 0 AND abs(unrealized_pnl) > 50% of stop_distance:
    EXIT IMMEDIATELY at market (slippage-adjusted)
    Tag: TIME_DECAY_EXIT_DEEP_LOSS
```

**Rationale:** A profitable position held for 120 minutes has realized its edge and should be banked. A losing position near its stop has limited additional downside and should be given the chance to recover. A losing position far from its stop is in no-man's-land and should be cut.

### 6.3 Hard Close

| Rule | Value | Enforcement |
|------|-------|-------------|
| Hard close time | 16:00 UK | All positions closed regardless of P&L |
| Buffer before LSE close | 30 minutes | LSE closes at 16:30; 30 min buffer ensures fills before close |
| Overnight hold | **PROHIBITED** | Leveraged ETPs have daily reset. Overnight holding introduces tracking error risk and is inconsistent with the intraday compounding strategy. |

### 6.4 Implementation

```python
def evaluate_time_decay(
    entry_time: datetime,
    current_time: datetime,
    unrealized_pnl_pct: float,
    stop_distance_pct: float,
    max_hold_minutes: int = 120,
) -> tuple[str, str]:
    """
    Returns (action: 'EXIT' | 'HOLD', tag: str).
    """
    held_minutes = (current_time - entry_time).total_seconds() / 60

    # Hard close at 16:00 UK
    if current_time.hour >= 16:
        return 'EXIT', 'HARD_CLOSE_1600'

    if held_minutes < max_hold_minutes:
        return 'HOLD', 'WITHIN_TIME_LIMIT'

    # Beyond max hold time
    if unrealized_pnl_pct > 0:
        return 'EXIT', 'TIME_DECAY_EXIT_PROFIT'
    elif abs(unrealized_pnl_pct) <= stop_distance_pct * 0.5:
        return 'HOLD', 'TIME_DECAY_HOLD_NEAR_STOP'
    else:
        return 'EXIT', 'TIME_DECAY_EXIT_DEEP_LOSS'
```

---

## 7. POSITION SIZING WITH REALISM

### 7.1 Raw Position Size

The base position sizing formula from the Risk Constitution (NZT48-ANNEX-RC-001):

```
raw_size = (equity * risk_pct) / (entry_price - stop_price)
notional = raw_size * entry_price
```

Where `risk_pct` = 2% of equity (the maximum loss if the stop is hit).

### 7.2 Slippage-Adjusted Position Size

Raw position size must be reduced to account for the fact that slippage widens the effective stop distance:

```
effective_stop_distance = (entry_price - stop_price) + entry_slippage + exit_slippage
adjusted_size = (equity * risk_pct) / effective_stop_distance
```

**Example:**
- Equity: GBP 10,000, Risk: 2% = GBP 200
- Entry: 100.00, Stop: 98.50 (1.50 distance)
- Entry slippage (0.15%): 0.15
- Exit slippage (0.15%): 0.15 (approximately, on the stop price)
- Effective stop distance: 1.50 + 0.15 + 0.15 = 1.80
- Raw size: 200 / 1.50 = 133 shares
- Adjusted size: 200 / 1.80 = 111 shares (**~17% reduction**)

### 7.3 Size Constraints

| Constraint | Value | Enforcement |
|-----------|-------|-------------|
| Minimum notional | GBP 100 | Below this, commission-to-trade ratio exceeds 3%, making the trade uneconomical. Signal is logged but not executed. Tag: `SIZE_BELOW_MINIMUM`. |
| Maximum notional | MIN(10% of equity, liquidity-adjusted max) | Prevents single-trade concentration risk and market impact. |
| Liquidity-adjusted max | Position must not exceed 5% of the ticker's average daily volume (in GBP terms) | Prevents excessive market impact. See Section 8 for details. |
| Low-liquidity multiplier | 0.25x standard size | Applied to tickers flagged as low-liquidity in UNIVERSE_GOVERNANCE_PLAN (e.g., XLUS.L, NVDS.L). |

### 7.4 Implementation

```python
def compute_position_size(
    equity_gbp: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    slippage_entry_pct: float,
    slippage_exit_pct: float,
    avg_daily_volume_gbp: float,
    is_low_liquidity: bool,
) -> dict:
    """
    Returns position size with all realism adjustments applied.
    """
    risk_amount = equity_gbp * risk_pct / 100
    raw_distance = entry_price - stop_price
    entry_slip = entry_price * slippage_entry_pct / 100
    exit_slip = stop_price * slippage_exit_pct / 100
    effective_distance = raw_distance + entry_slip + exit_slip

    raw_shares = risk_amount / raw_distance
    adjusted_shares = risk_amount / effective_distance

    notional = adjusted_shares * entry_price
    max_equity_notional = equity_gbp * 0.10
    max_liquidity_notional = avg_daily_volume_gbp * 0.05

    if is_low_liquidity:
        adjusted_shares *= 0.25
        notional = adjusted_shares * entry_price

    notional = min(notional, max_equity_notional, max_liquidity_notional)
    final_shares = int(notional / entry_price)
    final_notional = final_shares * entry_price

    blocked = final_notional < 100
    tag = 'SIZE_BELOW_MINIMUM' if blocked else 'SIZE_OK'

    return {
        'raw_shares': int(raw_shares),
        'adjusted_shares': int(adjusted_shares),
        'final_shares': final_shares,
        'final_notional_gbp': round(final_notional, 2),
        'slippage_reduction_pct': round((1 - adjusted_shares / raw_shares) * 100, 1),
        'blocked': blocked,
        'tag': tag,
    }
```

---

## 8. LIQUIDITY REALISM

### 8.1 Relative Volume (RVOL) Gate

No entry is permitted when the ticker's relative volume is below the minimum threshold:

| Condition | Threshold | Action |
|-----------|-----------|--------|
| RVOL < 0.4x | Entry blocked | Log `RVOL_GATE_BLOCK`. Signal remains valid; re-check on next scan cycle. |
| RVOL 0.4x - 0.6x | Entry permitted with caution | Apply +0.05% slippage adjuster (see Section 2.2). |
| RVOL >= 0.6x | Entry permitted | Standard slippage applies. |

### 8.2 Daily Volume Constraint

Position size must not exceed 5% of the ticker's average daily volume (ADV), measured in GBP notional:

```
max_position_gbp = adv_shares * current_price * 0.05
```

**Rationale:** Positions exceeding 5% of ADV create measurable market impact and risk adverse price movement against the order. For LSE leveraged ETPs with typical ADV of 10,000-500,000 shares, this constraint is binding for larger equity balances.

### 8.3 Market Impact Model

For positions exceeding 1% of daily volume, an additional slippage surcharge is applied:

```
position_as_pct_of_adv = (position_shares / adv_shares) * 100
if position_as_pct_of_adv > 1.0:
    additional_slippage_pct = (position_as_pct_of_adv - 1.0) * 0.05
```

**Example:** A position representing 3% of ADV incurs an additional `(3.0 - 1.0) * 0.05 = 0.10%` slippage per side on top of the base rate.

### 8.4 Low-Liquidity Ticker Classification

The following tickers are classified as low-liquidity per UNIVERSE_GOVERNANCE_PLAN and receive the 0.25x position sizing multiplier:

| Ticker | Avg Daily Volume | Classification Source |
|--------|-----------------|---------------------|
| XLUS.L | ~351 shares/day | UGV-001 Section 2.2 |
| NVDS.L | ~95 shares/day | UGV-001 Section 2.2 |

This list is maintained in `config/settings.yaml` under `universe.low_liquidity_tickers` and reviewed monthly per the Universe Governance Plan.

---

## 9. COMMISSION MODEL

### 9.1 Broker Commission Assumptions

| Broker | Fixed Cost | Variable Cost | Stamp Duty | Notes |
|--------|-----------|---------------|------------|-------|
| IBKR (ISA) | GBP 3.00 per trade | 0.10% | Exempt (ETPs) | Stamp duty is not charged on ETPs, only on UK equities |
| Trading 212 (ISA) | GBP 0.00 | ~0.10% implicit in spread | Exempt (ETPs) | Zero-commission but wider effective spreads |

### 9.2 Paper Mode Commission Model

Paper mode uses a **conservative composite** that is broker-agnostic:

| Component | Value | Applied |
|-----------|-------|---------|
| Fixed commission | GBP 3.00 | Per side (entry and exit) |
| Variable commission | 0.05% | Per side |
| Total per round-trip | GBP 6.00 + 0.10% of notional | Deducted from P&L |

### 9.3 Commission Impact Analysis

| Notional Trade Size | Fixed Cost (RT) | Variable Cost (RT) | Total Commission | Commission as % of Notional |
|--------------------|-----------------|-------------------|-----------------|---------------------------|
| GBP 100 | GBP 6.00 | GBP 0.10 | GBP 6.10 | 6.10% |
| GBP 200 | GBP 6.00 | GBP 0.20 | GBP 6.20 | 3.10% |
| GBP 500 | GBP 6.00 | GBP 0.50 | GBP 6.50 | 1.30% |
| GBP 1,000 | GBP 6.00 | GBP 1.00 | GBP 7.00 | 0.70% |
| GBP 2,000 | GBP 6.00 | GBP 2.00 | GBP 8.00 | 0.40% |

**Critical observation:** At GBP 100-200 notional, commissions alone consume 3-6% of the trade, making the 2% target mathematically impossible. The minimum viable trade size is approximately GBP 500, where commission overhead drops to 1.3%.

### 9.4 Implementation

```python
def compute_commission(
    notional_gbp: float,
    fixed_per_side: float = 3.00,
    variable_pct_per_side: float = 0.05,
) -> dict:
    """
    Returns round-trip commission breakdown.
    """
    fixed_rt = fixed_per_side * 2
    variable_rt = notional_gbp * (variable_pct_per_side / 100) * 2
    total = fixed_rt + variable_rt
    pct_of_notional = (total / notional_gbp * 100) if notional_gbp > 0 else float('inf')

    return {
        'fixed_rt_gbp': round(fixed_rt, 2),
        'variable_rt_gbp': round(variable_rt, 2),
        'total_rt_gbp': round(total, 2),
        'pct_of_notional': round(pct_of_notional, 2),
    }
```

---

## 10. P&L REALISM ADJUSTMENTS

### 10.1 P&L Calculation Waterfall

Every trade record must compute P&L at four levels:

| Level | Name | Calculation | Purpose |
|-------|------|-------------|---------|
| L1 | Gross P&L (naive) | `(exit_mid - entry_mid) / entry_mid * 100` | Baseline: what the strategy "sees" at signal level |
| L2 | Gross P&L (slippage-adjusted) | `(exit_filled - entry_filled) / entry_filled * 100` | After applying slippage to fill prices |
| L3 | Net P&L | `L2 - commission_pct` | After deducting commission as percentage of notional |
| L4 | Net P&L (equity impact) | `L3 * (notional / equity) * 100` | Actual equity change in percentage terms |

### 10.2 Realism Gap Metric

The **realism gap** is the difference between naive and realistic P&L:

```
realism_gap = L1 - L3
```

This metric is tracked per trade and aggregated daily, weekly, and monthly. It quantifies the cost of execution realism and serves as the primary indicator of paper-to-live translation accuracy.

| Realism Gap Range | Interpretation |
|-------------------|---------------|
| 0.20-0.40% | Normal for typical 3x ETP trades |
| 0.40-0.70% | Expected for 5x products or adverse conditions |
| > 0.70% | Investigate: conditions may not support profitable trading |
| < 0.20% | Investigate: model may be underestimating costs |

### 10.3 Daily P&L Aggregation

```
daily_net_pnl = SUM(trade_net_pnl for all trades in day)
daily_equity = previous_day_equity * (1 + daily_net_pnl / 100)
daily_realism_gap = SUM(trade_realism_gap for all trades in day)
```

**Compounding base:** End-of-day equity after all costs. This is the only valid equity figure for computing the next day's position sizes and risk limits.

### 10.4 Implementation

```python
@dataclass
class TradeRealism:
    # Prices
    signal_entry: float       # Mid-price at signal time
    signal_exit: float        # Mid-price at exit time
    filled_entry: float       # Slippage-adjusted entry
    filled_exit: float        # Slippage-adjusted exit

    # P&L levels
    gross_pnl_naive_pct: float   # L1
    gross_pnl_adjusted_pct: float # L2
    net_pnl_pct: float           # L3
    equity_impact_pct: float     # L4

    # Costs
    entry_slippage_pct: float
    exit_slippage_pct: float
    commission_pct: float

    # Realism gap
    realism_gap_pct: float       # L1 - L3

    # Tags
    fill_source: str             # 'PAPER_SIMULATED' | 'LIVE_FILL'
    spread_source: str           # 'LIVE_QUOTE' | 'ESTIMATED_5MIN' | 'UNAVAILABLE'


def compute_trade_realism(
    entry_mid: float,
    exit_mid: float,
    entry_slippage_pct: float,
    exit_slippage_pct: float,
    commission_total_gbp: float,
    notional_gbp: float,
    equity_gbp: float,
) -> TradeRealism:
    filled_entry = entry_mid * (1 + entry_slippage_pct / 100)
    filled_exit = exit_mid * (1 - exit_slippage_pct / 100)

    l1 = (exit_mid - entry_mid) / entry_mid * 100
    l2 = (filled_exit - filled_entry) / filled_entry * 100
    commission_pct = (commission_total_gbp / notional_gbp * 100) if notional_gbp > 0 else 0
    l3 = l2 - commission_pct
    l4 = l3 * (notional_gbp / equity_gbp)

    return TradeRealism(
        signal_entry=entry_mid,
        signal_exit=exit_mid,
        filled_entry=round(filled_entry, 4),
        filled_exit=round(filled_exit, 4),
        gross_pnl_naive_pct=round(l1, 4),
        gross_pnl_adjusted_pct=round(l2, 4),
        net_pnl_pct=round(l3, 4),
        equity_impact_pct=round(l4, 4),
        entry_slippage_pct=entry_slippage_pct,
        exit_slippage_pct=exit_slippage_pct,
        commission_pct=round(commission_pct, 4),
        realism_gap_pct=round(l1 - l3, 4),
        fill_source='PAPER_SIMULATED',
        spread_source='',  # Set by caller
    )
```

---

## 11. ACCEPTANCE TESTS

All acceptance tests must pass before this specification is considered implemented. Each test maps to a specific section and is verifiable from trade records and system logs.

| Test ID | Description | Section | Verification Method | Pass Criteria |
|---------|-------------|---------|--------------------|----|
| EXR-T01 | Paper trade P&L includes slippage deduction | 2, 4, 10 | Inspect trade record: `filled_entry != signal_entry` and `filled_exit != signal_exit` | Filled prices differ from mid-prices by at least the base slippage rate |
| EXR-T02 | Entry blocked when spread exceeds threshold | 3 | Query logs for `SPREAD_GATE_BLOCK` events. Verify no trade record exists for the blocked signal. | At least one block logged during testing with artificially widened spread |
| EXR-T03 | Position held >120 min triggers time-decay evaluation | 6 | Query logs for `TIME_DECAY_*` tags. Verify exit action matches the decision tree in Section 6.2. | Correct tag applied based on unrealized P&L vs stop distance |
| EXR-T04 | All positions closed by 16:00 UK | 6 | Run EOD audit query: no open positions after 16:00. | Zero open positions at 16:01 across 5 consecutive trading days |
| EXR-T05 | Position size reduced for low-liquidity ticker | 7, 8 | Compare position size for XLUS.L or NVDS.L against a CORE ticker with identical signal parameters. | Low-liquidity ticker size <= 25% of CORE ticker size |
| EXR-T06 | Commission deducted from P&L | 9, 10 | Inspect trade record: `net_pnl_pct < gross_pnl_adjusted_pct` | Commission amount matches formula in Section 9.2 |
| EXR-T07 | Realism gap metric tracked per trade | 10 | Inspect trade record: `realism_gap_pct` field is present and non-zero. | Realism gap falls within expected range (0.20-0.70% for typical trades) |

### 11.1 Test Execution Protocol

1. Tests EXR-T01, EXR-T06, EXR-T07 are verified on **every trade** automatically via assertions in the trade recording pipeline.
2. Tests EXR-T02, EXR-T03 require **simulated conditions** (artificially wide spread, held position) during the first week of operation.
3. Test EXR-T04 is verified via the **EOD reconciliation job** that runs at 16:05 UK daily.
4. Test EXR-T05 is verified when a low-liquidity ticker generates a signal (may require manual trigger during testing).

### 11.2 Evidence Pack

Each test produces an evidence record stored in `evidence/execution_realism/`:

```
evidence/
  execution_realism/
    EXR-T01_slippage_applied.json      # Sample trade with slippage breakdown
    EXR-T02_spread_gate_block.json     # Blocked signal with spread data
    EXR-T03_time_decay_exit.json       # Time-decay triggered exit
    EXR-T04_eod_audit_5day.json        # 5-day EOD position audit
    EXR-T05_low_liq_size_compare.json  # Size comparison: low-liq vs CORE
    EXR-T06_commission_deducted.json   # Trade with commission breakdown
    EXR-T07_realism_gap_sample.json    # 20-trade realism gap sample
```

---

## 12. CONFIGURATION

All parameters defined in this specification are configurable via `config/settings.yaml` under the `execution_realism` key:

```yaml
execution_realism:
  enabled: true  # Master switch: false = naive mid-price fills (legacy behavior)

  slippage:
    base_3x_bps: 15
    base_5x_bps: 25
    adj_large_position_bps: 5
    adj_large_position_threshold_gbp: 500
    adj_open_close_window_bps: 10
    adj_open_close_window_minutes: 30
    adj_high_vol_bps: 10
    adj_low_rvol_bps: 5
    adj_low_rvol_threshold: 0.6

  spread_gate:
    threshold_3x_pct: 0.50
    threshold_5x_pct: 0.80
    high_vol_multiplier: 1.5

  fills:
    paper_mode: slippage_adjusted  # 'mid_price' (legacy) | 'slippage_adjusted'
    live_entry_limit_offset_pct: 0.10
    live_stop_limit_offset_pct: 0.20

  time_limits:
    max_hold_minutes: 120
    hard_close_hour_uk: 16
    time_decay_stop_threshold_pct: 50

  position_sizing:
    min_notional_gbp: 100
    max_equity_pct: 10
    max_adv_pct: 5
    low_liquidity_multiplier: 0.25
    market_impact_threshold_adv_pct: 1.0
    market_impact_surcharge_per_pct: 0.05

  liquidity:
    min_rvol: 0.4
    low_rvol_threshold: 0.6

  commission:
    fixed_per_side_gbp: 3.00
    variable_per_side_pct: 0.05
```

### 12.1 Master Switch

Setting `execution_realism.enabled: false` reverts to legacy behavior (mid-price fills, zero slippage, zero commission). This is **only** permitted for A/B comparison testing and must not be used for performance reporting.

---

## 13. MIGRATION PATH

### 13.1 Phase 1: Paper Mode with Realism (Current)

- Implement all sections of this specification in the paper trading engine.
- Run dual-track P&L: both naive and realistic, for a minimum of 20 trading days.
- Validate realism gap falls within expected ranges.
- All acceptance tests pass.

### 13.2 Phase 2: Shadow Live Mode

- Connect to broker API in read-only mode.
- Compare paper fills against real-time bid/ask data.
- Calibrate slippage model against observed spreads.
- Adjust base rates if systematic bias is detected (>0.05% average deviation).

### 13.3 Phase 3: Limited Live Mode

- Execute with real capital using the order types specified in Section 5.2.
- Position sizes capped at 50% of paper-mode levels during the first 10 trading days.
- Compare live fills against paper predictions; update slippage model if needed.
- Graduate to full position sizes after 10 days with < 0.10% average fill deviation.

---

## 14. REVISION HISTORY

| Version | Date       | Author | Changes |
|---------|------------|--------|---------|
| 1.0     | 2026-02-27 | PM     | Initial specification. All sections. |

---

## 15. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| PM / System Owner | | | |
| Execution Engineer | | | |
| Risk Officer | | | |

---

*This document is BINDING per NZT48-ANNEX-EXR-001 v1.0. All execution code paths must comply with the parameters, gates, and constraints defined herein. Deviations require a formal amendment with IC/PM approval.*
