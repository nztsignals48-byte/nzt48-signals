# Universe Perfection System - Integration Guide

## Overview

The Universe Perfection System provides 3-tier asset classification and quality filtering for the NZT-48 trading system.

**Key Components:**
- `TieredUniverseScanner`: Classifies assets into BLUE_CHIP, SPECIALIST, EXPANSION tiers
- `PerfectAssetOptimizer`: Filters assets through quality gates before execution
- `OrchestratorIntegrationManager`: Coordinates parallel scanning threads

**Key Metrics:**
- 33 comprehensive unit tests - **ALL PASSING**
- Scan duration: <1 second for full universe
- Confidence thresholds: 60% (BLUE_CHIP), 65% (SPECIALIST), 70% (EXPANSION)
- Graceful handling of missing/delisted assets

---

## Architecture

### 3-Tier Classification System

```
┌─────────────────────────────────────────────────────────────┐
│ Market Data (volume, spread, freshness, volatility, trends) │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   ┌─────────┐     ┌──────────┐    ┌─────────────┐
   │ TIER 1  │     │ TIER 2   │    │ TIER 3      │
   │ BLUE    │     │ SPECIALIST   │ EXPANSION   │
   │ CHIP    │     │          │    │             │
   └────┬────┘     └────┬─────┘    └──────┬──────┘
        │               │                  │
        │ 60s scan      │ 90s scan        │ 180s scan
        │ 60% conf      │ 65% conf        │ 70% conf
        │ 5M+ vol       │ 1M+ vol         │ 500k+ vol
        │ <10bps spread │ <20bps spread   │ <30bps spread
        │               │                  │
        └───────────────┼──────────────────┘
                        │
                        ▼
            ┌───────────────────────────┐
            │ PerfectAssetOptimizer     │
            │ - Tradeability check      │
            │ - Signal quality check    │
            │ - Data quality check      │
            │ - Regime stability check  │
            │ - Correlation risk check  │
            └────────────┬──────────────┘
                         │
                         ▼
            ┌──────────────────────────┐
            │ WHITELISTED ASSETS       │
            │ Ready for execution      │
            └──────────────────────────┘
```

### Scan Frequencies & Confidence Thresholds

| Tier | Scan Freq | Confidence | Min Volume | Max Spread | Signal Reliability |
|------|-----------|------------|------------|------------|-------------------|
| BLUE_CHIP | 60s | 60% | 5M | 10bps | 100% |
| SPECIALIST | 90s | 65% | 1M | 20bps | 85% |
| EXPANSION | 180s | 70% | 500k | 30bps | 70% |

---

## Integration with Orchestrator

### Step 1: Initialize Manager

```python
from src.universe.orchestrator_integration import create_integration_manager

# In your orchestrator initialization
manager = create_integration_manager(
    db_conn=your_database_connection,
    data_hub=your_data_feed_hub,
    initialize_tables=True
)
manager.start()
```

### Step 2: In Main Trading Loop

```python
# Each iteration, get latest whitelist (non-blocking)
whitelist = manager.get_latest_whitelist()

# Filter candidates for execution
for candidate in signal_candidates:
    if candidate['ticker'] in [a['ticker'] for a in whitelist]:
        # Asset is PERFECT - proceed with execution
        execute_trade(candidate)
```

### Step 3: Log Results

```python
# After scanning, log results to database
manager.log_scan_result(
    result_type="TIER1",
    assets=tier1_ranked_assets,
    failed=tier1_failed_assets,
    scan_duration_sec=0.15
)
```

---

## API Reference

### TieredUniverseScanner

```python
from src.universe.tiered_universe_scanner import TieredUniverseScanner, AssetMetrics

scanner = TieredUniverseScanner(
    universe_config={
        "core_list": ["QQQ3.L", "3LUS.L", ...],
        "peer_candidates": ["AMD3.L", "ARM3.L", ...],
        "sector_radar": ["BAC3.L", "GS3.L", ...]
    }
)

# Scan single tier
tier1_assets = scanner.scan_tier1(metrics_dict)
tier2_assets = scanner.scan_tier2(metrics_dict)
tier3_assets = scanner.scan_tier3(metrics_dict)

# Full scan all tiers
result = scanner.rank_assets(
    metrics_dict,
    top_n_per_tier={"BLUE_CHIP": 3, "SPECIALIST": 2, "EXPANSION": 1}
)

print(result.blue_chip_count)        # Number of BLUE_CHIP assets
print(result.specialist_count)       # Number of SPECIALIST assets
print(result.expansion_count)        # Number of EXPANSION assets
print(result.ranked_by_tier)         # Dict of tier -> [RankedAsset]
print(result.failed_tickers)         # Assets that failed gates
print(result.scan_duration_sec)      # How long scan took
```

### PerfectAssetOptimizer

```python
from src.universe.perfect_asset_optimizer import PerfectAssetOptimizer

optimizer = PerfectAssetOptimizer()

# Check if single asset is tradeable
result = optimizer.is_tradeable(
    ticker="QQQ3.L",
    volume=8_000_000,
    spread_bps=8,
    is_delisted=False,
    data_freshness_sec=30
)

if result.is_tradeable:
    print(f"✓ {result.ticker} is tradeable")
else:
    print(f"✗ Issues: {result.issues}")

# Rank multiple candidates by quality
candidates = [
    {
        "ticker": "QQQ3.L",
        "tier": "BLUE_CHIP",
        "volume": 8_000_000,
        "spread_bps": 8,
        "signal_accuracy_pct": 72,
        "signal_reliability_pct": 85,
        "data_completeness_pct": 98,
        "data_freshness_sec": 30,
        "volatility_regime": "NORMAL",
        "adx": 35,
        "is_delisted": False,
    },
    # ... more candidates ...
]

early_detection_scores = {"QQQ3.L": 85.0, ...}

result = optimizer.rank_by_quality(candidates, early_detection_scores)

print(result.approved_count)    # Number approved
print(result.rejected_count)    # Number rejected
print(result.whitelist)         # List of AssetWhitelistEntry
print(result.rejections)        # Rejected assets + reasons
```

---

## Testing

All 33 tests pass with 100% success rate:

```bash
cd /Users/rr/nzt48-signals
python3 -m pytest tests/test_universe_perfection.py -v

# Output:
# ============================= 33 passed in 0.05s ==============================
```

### Test Coverage

**TieredUniverseScanner (14 tests):**
- ✓ Initialization
- ✓ BLUE_CHIP tier scanning (60s, 60% threshold)
- ✓ SPECIALIST tier scanning (90s, 65% threshold)
- ✓ EXPANSION tier scanning (180s, 70% threshold)
- ✓ Full scan across all tiers
- ✓ Confidence thresholds enforced
- ✓ Liquidity gates enforced (5M, 1M, 500k)
- ✓ Spread gates enforced (10bps, 20bps, 30bps)
- ✓ Data freshness gates (<5min)
- ✓ Scan speed (<1s for full universe)
- ✓ Graceful handling of missing assets
- ✓ Ranking order by confidence

**PerfectAssetOptimizer (12 tests):**
- ✓ Initialization
- ✓ Tradeability check (pass/fail)
- ✓ Volume gate (>500k)
- ✓ Spread gate (<30bps)
- ✓ Data freshness gate (<5min)
- ✓ Delisted asset rejection
- ✓ Quality ranking and approval
- ✓ Signal accuracy threshold (>60%)
- ✓ Signal reliability threshold (>75%)
- ✓ Extreme volatility rejection
- ✓ Quality score computation
- ✓ Early detection integration

**Integration (2 tests):**
- ✓ Scanner-to-optimizer pipeline
- ✓ Multiple scans consistency

**Edge Cases (5 tests):**
- ✓ Empty metrics dict handling
- ✓ Empty candidates list handling
- ✓ All candidates rejected scenario
- ✓ Unicode ticker handling
- ✓ Graceful delisted asset handling

---

## Database Schema

The system tracks asset health in the `asset_health` table:

```sql
CREATE TABLE asset_health (
    id INTEGER PRIMARY KEY,
    scan_timestamp TEXT NOT NULL,
    scan_type TEXT NOT NULL,              -- 'TIER1', 'TIER2', 'TIER3', 'OPTIMIZATION'
    ticker TEXT NOT NULL,
    tier TEXT NOT NULL,                   -- 'BLUE_CHIP', 'SPECIALIST', 'EXPANSION'
    confidence_pct REAL,
    liquidity_score REAL,
    volatility_score REAL,
    volume REAL,
    spread_bps REAL,
    data_freshness_sec INTEGER,
    tradeable BOOLEAN,
    quality_score REAL,
    signal_accuracy_pct REAL,
    signal_reliability_pct REAL,
    approved BOOLEAN,
    approval_reason TEXT,
    duration_sec REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for fast queries
CREATE INDEX idx_asset_health_timestamp ON asset_health(scan_timestamp);
CREATE INDEX idx_asset_health_ticker ON asset_health(ticker);
CREATE INDEX idx_asset_health_tier ON asset_health(tier);
CREATE INDEX idx_asset_health_approved ON asset_health(approved);
```

Initialize in your code:

```python
from src.universe.orchestrator_integration import init_asset_health_table

init_asset_health_table(db_conn)
```

---

## Quality Gates & Thresholds

### Tradeability Gates (Hard Blockers)
- **Minimum Volume**: 500k shares/day
- **Maximum Spread**: 30 basis points
- **Maximum Data Age**: 5 minutes (300s)
- **Delisted Check**: Must not be marked delisted

### Signal Quality Thresholds
- **Minimum Accuracy**: 60%
- **Minimum Reliability**: 75%
- **Sample Size**: At least 10 recent signals

### Data Quality Thresholds
- **Completeness**: ≥90% of required bars present
- **Data Gap**: No gaps >1 trading day
- **Freshness**: <5 minutes (300s)

### Regime Stability Checks
- **ADX Trending**: >20 for confirmed trend
- **Volatility Regime**: NORMAL or EXPANSION (rejects EXTREME)
- **Correlation Risk**: <0.85 with existing positions

### Quality Score Computation
```
quality_score = (
    tradeability * 0.30 +
    signal_quality * 0.35 +
    data_quality * 0.15 +
    regime_stability * 0.20
) * 100
```

Scale: 0-100, with bonuses for strong ADX readings

---

## Logging

All decisions are logged to the database and visible in logs:

```
INFO: Tier 1 (BLUE_CHIP): scanned 12 tickers, ranked 3 as tradeable (confidence >= 60%)
INFO: Tier 2 (SPECIALIST): scanned 10 tickers, ranked 2 as qualified (confidence >= 65%)
INFO: Tier 3 (EXPANSION): scanned 13 tickers, ranked 1 as qualified (confidence >= 70%)
INFO: Scan complete (0.25s): 3 BLUE_CHIP, 2 SPECIALIST, 1 EXPANSION, 25 failed
INFO: Optimization complete: 4/6 approved, quality avg=82%
INFO: QQQ3.L: APPROVED (quality=85%, early_det=80%)
INFO: DEAD.L: REJECTED (stale data; low_volume)
```

---

## Error Handling

The system gracefully handles:
- Missing assets (skipped silently)
- Delisted assets (explicitly rejected)
- Stale data (failed at freshness gate)
- Zero-volume tickers (failed at liquidity gate)
- Wide spreads (failed at spread gate)
- Unicode tickers (processed normally)
- Empty universes (returns empty result)
- Extreme volatility (explicit regime rejection)

---

## Performance Characteristics

- **Scan Speed**: <1 second for full 35-asset universe
- **Memory Usage**: ~1MB for full scanner + optimizer
- **CPU Impact**: Minimal during scans, allows parallel execution
- **Database Impact**: One INSERT per scan result (batched OK)

---

## Future Extensions

Potential enhancements (not yet implemented):
1. Real-time correlation matrix integration
2. Machine learning-based confidence scoring
3. Sector rotation integration
4. Earnings event calendar integration
5. Volatility regime prediction
6. Custom universe loading from config files
7. A/B testing framework for different thresholds
8. Explainability reports for rejections

---

## File Locations

```
/Users/rr/nzt48-signals/
├── src/universe/
│   ├── __init__.py                      # Package exports
│   ├── tiered_universe_scanner.py       # 3-tier classification (380 lines)
│   ├── perfect_asset_optimizer.py       # Quality filtering (410 lines)
│   ├── orchestrator_integration.py      # Orchestrator integration (200 lines)
│   └── INTEGRATION_GUIDE.md             # This file
└── tests/
    └── test_universe_perfection.py      # 33 comprehensive tests
```

---

## Contact & Support

For issues or questions:
1. Check test suite in `tests/test_universe_perfection.py`
2. Review log output for specific rejection reasons
3. Verify universe configuration in your orchestrator
4. Check database `asset_health` table for historical decisions

All decisions are fully traceable via logging and database records.
