# IBKR Data Feed Upgrade — Phase 8 Optional Enhancement

**Last Updated:** 2026-03-10
**Type:** Optional Phase 8 component (WP or SC)
**Effort:** ~2-3 hours (one refactoring session)
**Cost:** $0 (already connected to IBKR for execution)
**Benefit:** Zero latency + zero API costs + real-time Level 1 quotes

---

## Why Upgrade?

### Current (Phases 0-4)
- **Data Source:** yfinance (web scraping)
- **Latency:** ~1-5 seconds (scraping overhead)
- **Cost:** $0
- **API Limits:** Unlimited
- **Reliability:** Good (yfinance has outages occasionally)
- **Real-Time Quotes:** NO (EOD only)
- **Spread Data:** NO

### Upgraded (Phase 8+)
- **Data Source:** IBKR Gateway (direct broker connection)
- **Latency:** <100ms (direct API)
- **Cost:** $0 (you're already connected for execution!)
- **API Limits:** None (broker native)
- **Reliability:** Excellent (H-07 auto-reconnection + Docker restart)
- **Real-Time Quotes:** YES (Level 1 bid/ask/last)
- **Spread Data:** YES (calculate slippage accurately)

---

## Implementation Plan

### Step 1: Review Existing Implementation
File: `/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py`
- 565 lines of production code
- H-07 auto-reconnection protocol (10-min timeout + Docker restart)
- Thread-safe ib_insync integration
- Contract qualification + caching
- LSE contract mapping (LSEETF/USD, LSE/GBP, etc.)

### Step 2: Integrate Into AEGIS V2

**Option A: Copy-Paste Integration (1-2 hours)**
```bash
# 1. Copy IBKRSource class to AEGIS V2
cp /Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py \
   /Users/rr/nzt48-signals/nzt48-aegis-v2/python_brain/ouroboros/ibkr_source.py

# 2. Add to imports in bootstrap/GARCH calibration:
from ibkr_source import IBKRSource

# 3. Instantiate in main startup:
ibkr = IBKRSource()

# 4. Create fetch_bars wrapper:
def get_ohlcv(ticker, period="5d", interval="1h"):
    if ibkr.IS_AVAILABLE:
        df = ibkr.fetch_bars(ticker, period, interval)
        if df is not None:
            return df
    # Fallback to yfinance
    return yfinance.download(ticker, period=period)

# 5. Create fetch_quote wrapper (new):
def get_real_time_quote(ticker):
    if ibkr.IS_AVAILABLE:
        quote = ibkr.fetch_quote(ticker)
        if quote is not None:
            return quote
    # yfinance doesn't provide real-time, so this is IBKR-only
    return None
```

**Option B: Rust Bridge Integration (2-3 hours)**
```
- Write Rust wrapper around IBKRSource (via PyO3)
- Add to garch_inference.rs for real-time spread monitoring
- Returns Level 1 quotes via Python→Rust FFI (same as RM-3)
```

### Step 3: Update Configuration

Add to `.env` and `.env.production`:
```bash
# IBKR Data Feed (Phase 8+ upgrade)
IBKR_HOST=127.0.0.1
IBKR_PORT=4004
IBKR_CLIENT_ID=101
IBKR_ENABLED=true
```

### Step 4: Testing

**Acceptance Tests (Phase 8 component):**
```bash
# AT-IBKR-1: Connection
pytest tests/test_ibkr_connection.py::test_ibkr_gateway_connects

# AT-IBKR-2: Contract qualification (LSE)
pytest tests/test_ibkr_connection.py::test_lse_contract_map

# AT-IBKR-3: Fetch bars
pytest tests/test_ibkr_connection.py::test_fetch_bars_lse

# AT-IBKR-4: Real-time quotes
pytest tests/test_ibkr_connection.py::test_fetch_quote_bid_ask

# AT-IBKR-5: Fallback on disconnect
pytest tests/test_ibkr_connection.py::test_reconnection_fallback

# AT-IBKR-6: H-07 auto-reconnect
pytest tests/test_ibkr_connection.py::test_h07_reconnection_loop
```

---

## Usage After Integration

### Fetching OHLCV (Automatic Fallback)
```python
# Tries IBKR first, falls back to yfinance
df = get_ohlcv("QQQ3.L", period="5d", interval="1h")

# Real-time detection:
# IBKR available: <100ms latency
# IBKR unavailable: yfinance fallback (~2-5s)
```

### Fetching Real-Time Quotes (IBKR Native)
```python
# NEW: Real-time Level 1 quotes (not available in yfinance)
quote = get_real_time_quote("QQQ3.L")

# Returns:
{
    "ticker": "QQQ3.L",
    "bid": 123.45,
    "ask": 123.50,
    "last": 123.48,
    "bid_size": 100,
    "ask_size": 200,
    "spread_bps": 4.07,  # 4 basis points
    "source": "ibkr",
    "timestamp": "2026-03-10T14:30:00.123456+00:00"
}

# Use spread_bps for slippage calculation:
# Expected slippage = spread / 2 = 2 bp (excellent for UK ISA)
```

### Monitoring Connection Status
```python
# Check if IBKR is available
if ibkr.IS_AVAILABLE:
    print("Using IBKR data (zero latency)")
else:
    print("IBKR offline, using yfinance (fallback)")

# Check if in degraded mode (>10 min disconnect)
if ibkr.is_degraded:
    print("WARNING: IBKR offline for >10 min, manual intervention needed")
    # Alert Telegram
```

---

## Benefits for AEGIS V2

### 1. Zero-Latency GARCH Calibration
- Current: yfinance ~5s latency per ticker × 50 tickers = ~250s total
- Upgraded: IBKR <100ms × 50 = ~5s total (50× faster!)
- Real-time adjustment possible (not just nightly)

### 2. Real-Time Slippage Monitoring
- IBKR provides bid/ask/spread every tick
- Kalman filter can auto-adjust risk gates based on current spread
- Warn before executing in thin markets

### 3. Reduced API Costs (Already Connected!)
- You're already connected to IBKR for execution
- Leverage the same connection for data (no additional cost)
- Zero new API subscriptions needed

### 4. Improved LSE Leveraged ETP Coverage
- IBKR native contract qualification for .L tickers
- Proper exchange mapping (LSEETF/USD vs. LSE/GBP)
- Historical bars in exact LSE trading hours

### 5. H-07 Auto-Reconnection
- Automatic Docker restart on 3 consecutive failures
- 10-minute timeout before falling back to yfinance
- Telegram alerts on disconnect/reconnect

---

## Implementation Checklist

- [ ] **Read** existing IBKRSource (`/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py`)
- [ ] **Copy** IBKRSource to AEGIS V2
- [ ] **Test** contract qualification for LSE tickers (QQQ3.L, TSL3.L, etc.)
- [ ] **Test** real-time quote fetching
- [ ] **Implement** fallback wrapper (get_ohlcv, get_real_time_quote)
- [ ] **Update** .env configuration
- [ ] **Write** acceptance tests (6 tests)
- [ ] **Integrate** into GARCH calibration
- [ ] **Monitor** in paper trading (48-hour run)
- [ ] **Update** documentation

**Effort:** 2-3 hours (one Phase 8 component)
**Timing:** Recommended during Phase 8 infrastructure seal
**Blocking:** NOT blocking (optional enhancement)

---

## Comparison: IBKR vs. yfinance

| Feature | yfinance | IBKR Gateway |
|---------|----------|--------------|
| **Latency** | 2-5s (web scrape) | <100ms (direct API) |
| **Real-time quotes** | NO | YES ✓ |
| **Bid/ask spreads** | NO | YES ✓ |
| **LSE coverage** | YES | YES + better ✓ |
| **Cost** | Free | Free (already connected!) |
| **API limits** | None | None |
| **Reliability** | Good (occasional outages) | Excellent (H-07 protocol) |
| **Requires subscription** | NO | NO (execution broker) |

---

## When NOT to Implement

Skip this upgrade if:
- You don't need real-time quotes (Phase 0-4 yfinance is fine)
- You're satisfied with current latency
- You want to minimize code changes during Phase 8
- You plan to use third-party data aggregator later

---

## Implementation Request for Phase 8

**When Claude is assigned Phase 8 Infrastructure Seal:**

Use this as a reference implementation (WP or SC):

```
COMPONENT: IBKR Native Data Feed Integration
TYPE: SC-XX or WP-X (within Phase 8)
EFFORT: 2-3 hours
PRIORITY: Optional (nice-to-have, not blocking)
DESCRIPTION: Upgrade from yfinance to IBKR Gateway for real-time quotes + zero latency

IMPLEMENTATION:
1. Copy IBKRSource from V1 (/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py)
2. Integrate into python_brain/ouroboros/
3. Create fallback wrapper functions
4. Write 6 acceptance tests
5. Test in 48-hour paper run

GATE: All 6 IBKR ATs passing + zero connection errors in 48h

BENEFIT:
- Zero latency on OHLCV fetching (50× faster)
- Real-time Level 1 quotes (new capability)
- Zero new API costs (already connected for execution)
- Better LSE leveraged ETP handling
```

---

*IBKR_DATAFEED_UPGRADE.md — Generated 2026-03-10*
*Status: Ready for Phase 8 implementation*
*Source: IBKRSource from NZT-48 V1 (/Users/rr/nzt48-signals/data_hub/sources/ibkr_source.py)*
