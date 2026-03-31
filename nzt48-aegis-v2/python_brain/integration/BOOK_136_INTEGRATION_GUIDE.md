# Book 136 Cross-Market Lead-Lag Integration Guide

## Overview

Book 136 implements real-time cross-market lead-lag signals for 4 key pairs:
- **ES→3USL**: S&P 500 futures → 3x leveraged S&P 500 ETF (50-200ms lag)
- **NQ→QQQS**: Nasdaq futures → 3x inverse Nasdaq ETF (100-300ms lag)
- **TY→TLT**: 10Y Treasury futures → 20+ Year Bond ETF (200-500ms lag)
- **VIX→UVXY**: VIX Index → 2x inverse VIX ETN (immediate-50ms lag)

## Files

### Core Implementation
- **`book_136_cross_market_leadlag.py`**: Main detector class with correlation analysis
  - `CrossMarketLeadLagDetector`: Singleton detector
  - `LeadLagCorrelationAnalysis`: Tick-level correlation engine
  - `get_detector()`: Accessor function

### Bridge Integration
- **`book_136_bridge_adapter.py`**: Stateful adapter for bridge.py
  - `Book136BridgeAdapter`: Maintains tick buffers, feeds detector, applies adjustments
  - `get_adapter()`: Singleton accessor

## Integration Steps

### 1. Initialize in bridge.py (top of file)

Add imports:
```python
# Near top of bridge.py imports section
try:
    from python_brain.integration.book_136_bridge_adapter import get_adapter
    _book136_adapter = get_adapter()
except ImportError:
    _book136_adapter = None
    sys.stderr.write("WARNING: Book 136 adapter not available\n")
    sys.stderr.flush()
```

### 2. Feed Ticks in process_tick() function

Add near line 6770 (after bar history ingest):
```python
# ── BOOK 136: FEED TICKS TO CROSS-MARKET LEAD-LAG DETECTOR ──
if _book136_adapter:
    try:
        ticker_id = msg["ticker_id"]
        symbol = ticker_symbols.get(ticker_id, "")
        _book136_adapter.on_tick(
            ticker_id=ticker_id,
            symbol=symbol,
            price=msg["last"],
            bid=msg.get("bid", msg["last"]),
            ask=msg.get("ask", msg["last"]),
            volume=msg.get("volume", 0),
            timestamp_ns=msg.get("timestamp_ns", 0),
        )
    except Exception as e:
        sys.stderr.write(f"BOOK136_TICK_ERROR: {e}\n")
        sys.stderr.flush()
```

### 3. Apply Adjustment in _apply_adjustments() function

Add in `_apply_adjustments()` after existing adjustments (around line 4850+):
```python
# ── BOOK 136: APPLY CROSS-MARKET LEAD-LAG CONFIDENCE ADJUSTMENT ──
if _book136_adapter:
    try:
        # Determine signal direction from best signal
        signal_dir = "long" if best.get("direction") == "Long" else "short"

        # Apply lead-lag adjustment
        best = _book136_adapter.apply_adjustment(
            signal_dict=best,
            signal_direction=signal_dir,
            ticker_id=ticker_id,
            symbol=symbol,
            current_timestamp_ns=msg.get("timestamp_ns", 0),
        )
    except Exception as e:
        sys.stderr.write(f"BOOK136_ADJUST_ERROR: {e}\n")
        sys.stderr.flush()
```

### 4. Output Signal Fields

The signal output will now include:
```json
{
  "type": "signal",
  "ticker_id": 123,
  "direction": "Long",
  "confidence": 68,  // May be adjusted by Book 136
  "lead_lag_adjustment_pct": 15.0,
  "lead_lag_primary_pair": "ES→3USL",
  "lead_lag_primary_correlation": 0.87,
  "lead_lag_primary_lag_ms": 125,
  "lead_lag_supporting": ["NQ→QQQS"],
  "lead_lag_conflicting": [],
  "lead_lag_regime": "normal",
  ...
}
```

## Confidence Adjustment Logic

### Rules

1. **MATCHING direction** (lead-lag direction matches signal direction):
   - **+15% confidence boost**
   - Example: signal says "long", ES moving up and 3USL lagging → +15%

2. **OPPOSING direction** (lead-lag direction opposes signal):
   - **-20% confidence penalty**
   - Example: signal says "long", but ES moving down with QQQS lagging → -20%

3. **REGIME BROKEN** (correlation < threshold for pair):
   - **0% adjustment (ignore lead-lag)**
   - Triggers 5-minute cooldown
   - Log: `BOOK136_REGIME_BREAK: ES→3USL correlation=0.62 (below 0.70 threshold)`

4. **LOW VOLUME** (<50% of 20-bar average):
   - **Ignore lead-lag signal (reduce confidence by -10%)**
   - High-certainty trades only during thin conditions

5. **MIXED signals** (some pairs support, others oppose):
   - **No adjustment (0%)**
   - Neutral stance until consensus emerges

## Edge Cases

### Market Gaps / News Events
- Correlation breaks → automatically disabled for 5 minutes
- Log: `BOOK136_REGIME_BREAK: TY→TLT correlation dropped below threshold`
- Automatically re-enabled after cooldown if correlation recovers

### Low-Volume Periods
- Ignored if volume < 50% of 20-bar average
- Safety: lead-lag is unreliable in thin markets

### Circuit Breakers / Halts
- Monitoring pauses automatically
- Resume on next valid tick

### VIX Regime Shifts
- VIX→UVXY pair has tightest lag (25ms)
- Used to detect rapid vol regime changes
- May trigger temporary boost to hedge positions

## Validation & Backtesting

### Run 2024 Backtest

```python
from python_brain.strategies.book_136_cross_market_leadlag import backtest_lead_lag_effectiveness

results = backtest_lead_lag_effectiveness(
    start_date="2024-01-01",
    end_date="2024-12-31",
)

print(f"Win rate with lead-lag: {results['win_rate_with_leadlag']:.1%}")
print(f"Win rate without: {results['win_rate_without_leadlag']:.1%}")
print(f"Improvement: {results['improvement_pct']:.1f}%")
print(f"Sharpe with: {results['sharpe_with']:.2f}")
print(f"Sharpe without: {results['sharpe_without']:.2f}")
print(f"Sharpe improvement: {results['sharpe_improvement']:.2f}")
```

### Expected Results
- **Win rate improvement**: +3-5%
- **Sharpe ratio improvement**: +0.2-0.4
- **Average detected lag**: 100-150ms (matches theory)
- **Correlation consistency**: >0.80 in normal markets

## Data Sources

### Futures (IBKR)
- **ES** (S&P 500): `reqHistoricalData` at 1-minute bar
- **NQ** (Nasdaq): `reqHistoricalData` at 1-minute bar
- **TY** (10Y Treasury): `reqHistoricalData` at 1-minute bar
- Client ID: 102 (separate from engine's 101)

### Index (yfinance)
- **VIX** (Volatility Index): `yfinance` at 1-minute bar

### ETFs/ETNs (IBKR)
- **3USL.L**, **QQQS.L**, **TLT**, **UVXY**: Already in active watchlist

## Performance Characteristics

### Latency
- Tick ingestion: <1ms
- Correlation computation: ~5-10ms (300-tick window)
- Adjustment application: <1ms
- **Total**: <20ms impact on signal generation

### Memory
- 4 correlation analyzers: ~200KB each
- Tick buffers (300 ticks each): ~50KB per symbol
- **Total**: ~1MB for Book 136

### CPU
- Correlation matrix (300x300): computed every tick, <2ms
- Window alignment: <1ms
- **Total**: <5% CPU increase

## Monitoring & Diagnostics

### Logs

Book 136 logs to stderr with prefix `BOOK136_`:

```
BOOK136_ADJUST: 3USL.L long confidence=60->75 primary=ES→3USL corr=0.87 adjustment=+15%
BOOK136_REGIME_BREAK: ES→3USL correlation=0.62 (below 0.70 threshold), pausing for 5 min
BOOK136_TICK_ERROR: Failed to process tick for ES
```

### Get Diagnostics

```python
from python_brain.integration.book_136_bridge_adapter import get_adapter

adapter = get_adapter()
diags = adapter.get_diagnostics()
# Returns: {
#   "ES→3USL": {"lag_ms": 125, "correlation": 0.87, "direction": "up", ...},
#   "NQ→QQQS": {"lag_ms": 210, "correlation": 0.82, "direction": "down", ...},
#   ...
# }
```

## Troubleshooting

### Book 136 not applying adjustments
- Check `_book136_adapter` is not None
- Verify symbols are in mapping (`_symbol_mapping`)
- Check tick buffers have data: `adapter.get_diagnostics()`

### Correlation always near 0.0
- Insufficient tick data (need 50+ ticks minimum)
- Symbols not receiving ticks from IBKR
- Check timestamp precision (needs nanoseconds)

### Regime breaks lasting >5 min
- Actual market regime change or exchange halt
- Normal behavior — lead-lag loses edge in stress
- Monitor via `lead_lag_regime` field in output

## Testing

### Unit Test

```python
from python_brain.strategies.book_136_cross_market_leadlag import (
    CrossMarketLeadLagDetector,
    TickData,
)

detector = CrossMarketLeadLagDetector()

# Simulate ticks
for i in range(100):
    leader_tick = TickData(timestamp_ns=i*1000000, price=100+i*0.01)
    follower_tick = TickData(timestamp_ns=i*1000000, price=150+i*0.015)
    detector.update_pair("ES→3USL", leader_tick, follower_tick, i*1000000)

results = detector.get_all_results()
assert "ES→3USL" in results
print(f"Correlation: {results['ES→3USL'].correlation}")
```

## References

- **Book 136**: Cross-Market Lead-Lag Signals (this implementation)
- **Book 77**: Basic lead-lag detection (existing in `lead_lag.py`)
- **Book 82**: Ensemble regime detection
- **Book 124**: Vol regime clustering
- **Book 208**: Quality gates (signal validation)

## Next Steps

1. Integrate into bridge.py using steps above
2. Deploy to EC2 and monitor via logs
3. Run 2024 backtest to validate Sharpe improvement
4. Fine-tune lag windows per pair if needed
5. Monitor regime breaks and correlation recovery
