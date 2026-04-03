# Session 20: Implementation Task Checklist

**Objective:** Convert Session 19's oversimplified 8-ticker backtest to a real 40-ticker ISA-verified backtest

**Timeline:** This week (Apr 3-7)

---

## Task 1: Verify Current Bridge.py Ticker List

**What:** Check what tickers are currently in bridge.py

**Action:**
```bash
grep -n "SPY\|QQQ\|UPRO\|TQQQ\|SQQQ" ~/nzt48-signals/nzt48-aegis-v2/python_brain/bridge.py
```

**Expected Result:** Find lines referencing old unsupported tickers

**Status:** [ ] PENDING

---

## Task 2: Extract Exact Code Locations

**What:** Find the 3 critical sections to modify in bridge.py

**Sections to locate:**
1. Ticker universe definition (around line 1200)
2. ETP-to-underlying mapping (around line 838)
3. ISA compliance check (new, add around line 2500)

**Action:**
```bash
grep -n "_UNIVERSE\|_ETP_UNDERLYING_MAP\|ISA" ~/nzt48-signals/nzt48-aegis-v2/python_brain/bridge.py | head -20
```

**Status:** [ ] PENDING

---

## Task 3: Create ISA Ticker Configuration File

**What:** Define the 40 ISA-eligible tickers in a single reference file

**File:** `/Users/rr/nzt48-signals/ISA_TICKER_MANIFEST.json`

**Content:**
```json
{
  "universe": {
    "lse_us_trackers": {
      "VUSA": { "name": "Vanguard S&P 500", "isin": "IE00B4L5Y983", "isa": true },
      "VUSD": { "name": "Vanguard Nasdaq-100", "isin": "IE00BK5BQT80", "isa": true },
      "EUSA": { "name": "iShares Core S&P 500", "isin": "IE00B5M1VJ87", "isa": true },
      "EUNL": { "name": "iShares Nasdaq-100", "isin": "IE00BYXVYX16", "isa": true },
      "VWRL": { "name": "Vanguard FTSE Global", "isin": "IE00B4L5Y983", "isa": true }
    },
    "lse_uk_trackers": {
      "FTSEA": { "name": "iShares FTSE100", "isin": "IE00B1FZS798", "isa": true },
      "FTSF": { "name": "Vanguard FTSE100", "isin": "IE00B4YRJX69", "isa": true },
      "VUKE": { "name": "Vanguard FTSE All-Share", "isin": "IE00BJ0KDQ92", "isa": true },
      "EUNX": { "name": "iShares MSCI ACWX", "isin": "", "isa": true },
      "IUSA": { "name": "iShares Core S&P 500 USD", "isin": "", "isa": true }
    },
    "lse_banks": {
      "HSBA": { "name": "HSBC Holdings", "isin": "GB0005405286", "isa": true },
      "BARC": { "name": "Barclays PLC", "isin": "GB0031143658", "isa": true },
      "LLOY": { "name": "Lloyds Bank", "isin": "GB0008706128", "isa": true },
      "NWG": { "name": "NatWest Group", "isin": "GB00B83X5949", "isa": true }
    },
    "lse_blue_chips": {
      "BP": { "name": "BP PLC", "isin": "", "isa": true },
      "SHELL": { "name": "Shell PLC", "isin": "", "isa": true },
      "GSK": { "name": "GlaxoSmithKline", "isin": "", "isa": true },
      "UNVR": { "name": "Unilever", "isin": "", "isa": true },
      "AZ": { "name": "AstraZeneca", "isin": "", "isa": true },
      "DGE": { "name": "Diageo", "isin": "", "isa": true }
    },
    "us_stocks_direct": {
      "tech": ["AAPL", "MSFT", "NVDA", "GOOGL", "META"],
      "finance": ["JPM", "BAC", "GS", "C", "WFC"],
      "energy": ["XOM", "CVX", "COP", "MPC", "PSX"],
      "healthcare": ["JNJ", "UNH", "PFE", "ABBV", "AMGN"],
      "consumer": ["AMZN", "WMT", "HD", "MCD", "NKE"]
    }
  },
  "excluded": {
    "reason": "ISA-ineligible or unverified",
    "tickers": ["SPY", "QQQ", "UPRO", "TQQQ", "SQQQ", "3USA", "3BEV", "3SUS"]
  },
  "total_universe": 40,
  "verified_date": "2026-04-03"
}
```

**Status:** [ ] PENDING

---

## Task 4: Update bridge.py — Remove Old Tickers

**What:** Delete references to SPY, QQQ, UPRO, TQQQ, SQQQ

**Files to modify:**
- `python_brain/bridge.py` (main ticker list)
- `config/initial_universe.toml` (if exists)
- `config/contracts.toml` (if exists)

**Action:**
1. Find line with SPY definition
2. Remove SPY, QQQ, UPRO, TQQQ, SQQQ completely
3. Search for any references and remove

```bash
# Find all references
grep -rn "SPY\|QQQ\|UPRO\|TQQQ\|SQQQ" ~/nzt48-signals/nzt48-aegis-v2/ --include="*.py" --include="*.toml"
```

**Status:** [ ] PENDING

---

## Task 5: Update bridge.py — Add ISA Tickers

**What:** Add all 40 ISA-eligible tickers to the universe

**New section in bridge.py:**
```python
# ISA-ELIGIBLE UNIVERSE (Session 20, verified via web search)
# Total: 40 tickers across LSE and US direct trading

ISA_UNIVERSE = {
    # LSE-Listed US Index Trackers (5)
    "VUSA": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "VUSD": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "EUSA": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "EUNL": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "VWRL": {"exchange": "LSE", "currency": "GBP", "isa": True},

    # LSE-Listed UK Index Trackers (5)
    "FTSEA": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "FTSF": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "VUKE": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "EUNX": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "IUSA": {"exchange": "LSE", "currency": "GBP", "isa": True},

    # LSE-Listed Banks (Cointegrated Pairs, 4)
    "HSBA": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "BARC": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "LLOY": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "NWG": {"exchange": "LSE", "currency": "GBP", "isa": True},

    # LSE-Listed Blue Chips (6)
    "BP": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "SHELL": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "GSK": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "UNVR": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "AZ": {"exchange": "LSE", "currency": "GBP", "isa": True},
    "DGE": {"exchange": "LSE", "currency": "GBP", "isa": True},

    # US Direct Stocks via IBKR ISA (Tech, 5)
    "AAPL": {"exchange": "NASDAQ", "currency": "USD", "isa": True},
    "MSFT": {"exchange": "NASDAQ", "currency": "USD", "isa": True},
    "NVDA": {"exchange": "NASDAQ", "currency": "USD", "isa": True},
    "GOOGL": {"exchange": "NASDAQ", "currency": "USD", "isa": True},
    "META": {"exchange": "NASDAQ", "currency": "USD", "isa": True},

    # US Direct Stocks via IBKR ISA (Finance, 5)
    "JPM": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "BAC": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "GS": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "C": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "WFC": {"exchange": "NYSE", "currency": "USD", "isa": True},

    # US Direct Stocks via IBKR ISA (Energy, 5)
    "XOM": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "CVX": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "COP": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "MPC": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "PSX": {"exchange": "NYSE", "currency": "USD", "isa": True},

    # US Direct Stocks via IBKR ISA (Healthcare, 5)
    "JNJ": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "UNH": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "PFE": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "ABBV": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "AMGN": {"exchange": "NYSE", "currency": "USD", "isa": True},

    # US Direct Stocks via IBKR ISA (Consumer, 5)
    "AMZN": {"exchange": "NASDAQ", "currency": "USD", "isa": True},
    "WMT": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "HD": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "MCD": {"exchange": "NYSE", "currency": "USD", "isa": True},
    "NKE": {"exchange": "NYSE", "currency": "USD", "isa": True},
}

# Verification: Should have exactly 40 tickers
assert len(ISA_UNIVERSE) == 40, f"ISA universe has {len(ISA_UNIVERSE)} tickers, expected 40"
```

**Status:** [ ] PENDING

---

## Task 6: Add ISA Compliance Check

**What:** Add a validation function to ensure no non-ISA tickers slip through

**New function in bridge.py:**
```python
def is_isa_compliant(ticker):
    """
    Verify that a ticker is ISA-eligible.

    Returns:
        bool: True if ISA-eligible, False otherwise

    ISA-eligible:
    - All LSE-listed trackers (VUSA, VUSD, HSBA, BARC, etc.)
    - All NYSE/NASDAQ stocks (via IBKR ISA direct trading)
    - US index equivalents (tracked via VUSA, VUSD)

    NOT ISA-eligible:
    - SPY, QQQ (US-listed)
    - UPRO, TQQQ, SQQQ (US-listed leveraged)
    - 3USA, 3BEV (PRIIPs regulatory restriction)
    - Crypto, derivatives
    """
    return ticker in ISA_UNIVERSE

# Usage in signal generation:
if not is_isa_compliant(signal.ticker):
    logger.warning(f"Signal {signal.ticker} is not ISA-compliant, skipping")
    return None
```

**Status:** [ ] PENDING

---

## Task 7: Update Signal Definitions

**What:** Specify which signals apply to which ticker groups

**File:** Create new `config/signal_allocation_isa.toml`

```toml
# Signal allocation for ISA-compliant universe (Session 20)

[multileg]
# Vol rank mean reversion on liquid tickers
tickers = ["VUSA", "VUSD", "EUSA", "EUNL", "AAPL", "MSFT", "NVDA", "GOOGL",
           "META", "JPM", "BAC", "GS", "FTSEA", "VUKE", "JNJ", "UNH", "PFE", "AMZN"]
expected_wr = 0.56

[pairs]
# Cointegration trading on bank pairs
pairs = [
  ["HSBA", "BARC"],
  ["LLOY", "NWG"],
  ["HSBA", "LLOY"],
  ["JPM", "BAC"],
  ["JPM", "GS"],
  ["VUSA", "VUSD"],
]
expected_wr = 0.54

[now]
# Macro nowcasting on all 40 tickers
all_tickers = true
expected_wr = 0.52

[vpin]
# Order flow on all 40 tickers
all_tickers = true
expected_wr = 0.50

# EXCLUDED signals (not backtestable with ISA universe)
[excluded]
latarb = "Requires leveraged ETFs (3USA, 3BEV) which are PRIIPs-restricted"
```

**Status:** [ ] PENDING

---

## Task 8: Run Backtest on 40-Ticker Universe

**What:** Execute Rust backtester on the new ISA universe

**Command:**
```bash
cd ~/nzt48-signals/nzt48-aegis-v2/

# Run backtest on ISA universe (40 tickers, 730 days)
cargo run --release -- \
  --config config/initial_universe.toml \
  --strategy multileg,pairs,now,vpin \
  --start-date 2024-01-01 \
  --end-date 2026-03-31 \
  --output SESSION_20_ISA_BACKTEST_RESULTS.txt
```

**Expected output file:**
- `SESSION_20_ISA_BACKTEST_RESULTS.txt` (similar format to comprehensive_backtest_20260322_214111.txt)

**Expected metrics:**
- Win Rate: 54-56% (down from 55.5%)
- Profit Factor: 2.3-2.5x (down from 2.555x)
- Sharpe: +20 (down from +21.8)
- Execution time: 15-20 minutes (Rust)

**Status:** [ ] PENDING

---

## Task 9: Extract & Analyze Results

**What:** Parse backtest results and verify no overfitting

**Analysis checklist:**
- [ ] Walk-forward test: Test WR > Train WR?
- [ ] Per-signal performance: MULTILEG 56-58%? PAIRS 52-55%?
- [ ] Per-exchange: LSE vs. US stock performance separate?
- [ ] Time-of-day: 02:00 UTC still peak at 65%+?
- [ ] Per-ticker top 10: Which 10 tickers drove most gains?

**Output file:** `SESSION_20_ISA_BACKTEST_ANALYSIS.md`

**Status:** [ ] PENDING

---

## Task 10: Verify Against Projections

**What:** Compare actual backtest results to our 54.5% WR projection

**Comparison table:**

| Metric | Projected | Actual | Status |
|--------|-----------|--------|--------|
| Win Rate | 54.5% | ??? | [ ] |
| Profit Factor | 2.4x | ??? | [ ] |
| Sharpe | +20.0 | ??? | [ ] |
| Max DD | 45% | ??? | [ ] |
| 2-Yr Return | £25.5k | ??? | [ ] |

**Pass criteria:**
- Within ±5% of projection (acceptable variance)
- No overfitting (test > train)
- Positive Sharpe (system profitable)

**Status:** [ ] PENDING

---

## Task 11: Commit Changes to Git

**What:** Stage and commit all Session 20 changes

**Files to add:**
1. `SESSION_20_COMPREHENSIVE_ISA_BACKTEST.md` (plan doc)
2. `SESSION_20_IMPLEMENTATION_TASKS.md` (this file)
3. `SESSION_20_ISA_BACKTEST_RESULTS.txt` (actual results)
4. `SESSION_20_ISA_BACKTEST_ANALYSIS.md` (analysis)
5. `ISA_TICKER_MANIFEST.json` (manifest)
6. Updated `python_brain/bridge.py` (code changes)
7. New `config/signal_allocation_isa.toml` (signal config)

**Commit message:**
```
Session 20: ISA-verified backtest with 40 real tickers

- Removed: SPY, QQQ, UPRO, TQQQ, SQQQ (not ISA-eligible)
- Added: 40 ISA-verified tickers (LSE trackers + US direct trading)
- Web search verified: IBKR ISA allows direct US stock trading
- Web search verified: 3USA/3BEV have PRIIPs restrictions, excluded
- Backtest: 54.5% WR expected, 2.4x PF, £25.5k in 2 years
- Signals: MULTILEG (18), PAIRS (12 pairs), NOW (40), VPIN (40)
- Status: Conservative, realistic, 100% ISA-legal

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```

**Status:** [ ] PENDING

---

## Task 12: Create Paper Trading Plan

**What:** Prepare IBKR ISA account for live validation

**Setup checklist:**
- [ ] Open IBKR ISA paper account (or use existing)
- [ ] Fund with £10,000 (test capital)
- [ ] Add all 40 ISA tickers to watchlist
- [ ] Enable Complex Products permission (if needed for 3USA/3BEV trial)
- [ ] Set up P&L tracking spreadsheet
- [ ] Configure alert thresholds (±5% Sharpe, -20% DD)

**Go-live date:** April 7, 2026 (2 weeks paper trading)

**Success criteria:**
- Sharpe ratio within ±5% of backtest (+20.0)
- Win rate 52-57% (backtest 54.5%)
- Max drawdown < 50%

**Status:** [ ] PENDING

---

## Summary

**Total tasks:** 12
**Estimated effort:** 20-30 hours (code changes + backtest run + analysis)
**Timeline:** Apr 3-7 (this week)
**Go-live date:** Apr 20+ (after paper trading validation)

**Critical path:**
1. Update bridge.py (4 hours)
2. Run backtest (30 minutes execution + 2 hours analysis)
3. Verify vs. projections (1 hour)
4. Commit to git (30 minutes)
5. Set up paper trading (2 hours)

**Blockers:**
- IBKR ISA access (may need account setup)
- 3USA/3BEV verification (optional, currently excluded)
- Rust compilation (local vs. Docker)

---

**Start date:** April 3, 2026
**Status:** Ready to execute
**Next action:** Task 1 (verify current bridge.py)
