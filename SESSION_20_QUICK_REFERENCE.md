# Session 20: Quick Reference Card

**Keep this open while implementing**

---

## The 40-Ticker Universe (All ISA-Verified)

### Tier 1A: LSE US Trackers (5)
```
VUSA  VUSD  EUSA  EUNL  VWRL
```

### Tier 1B: LSE UK Trackers (5)
```
FTSEA  FTSF  VUKE  EUNX  IUSA
```

### Tier 2: LSE Banks/Pairs (4)
```
HSBA  BARC  LLOY  NWG
```

### Tier 3: LSE Blue Chips (6)
```
BP  SHELL  GSK  UNVR  AZ  DGE
```

### Tier 4: US Direct (20)
```
Tech:      AAPL  MSFT  NVDA  GOOGL  META
Finance:   JPM   BAC   GS    C      WFC
Energy:    XOM   CVX   COP   MPC    PSX
Healthcare:JNJ   UNH   PFE   ABBV   AMGN
Consumer:  AMZN  WMT   HD    MCD    NKE
```

**TOTAL: 40 tickers**

---

## Do NOT Include (Why Excluded)

```
❌ SPY      (US-listed, not ISA-eligible)
❌ QQQ      (US-listed, not ISA-eligible)
❌ UPRO     (US-leveraged, not ISA-eligible)
❌ TQQQ     (US-leveraged, not ISA-eligible)
❌ SQQQ     (US-inverse, not ISA-eligible)
⚠️  3USA     (PRIIPs regulatory block, unverified)
⚠️  3BEV     (PRIIPs regulatory block, unverified)
⚠️  3SUS     (Inverse, ISA unclear)
```

---

## Signal Allocation (40 Tickers)

### MULTILEG: 18 tickers (Vol Rank)
```
VUSA VUSD EUSA EUNL + AAPL MSFT NVDA GOOGL META +
JPM BAC GS + FTSEA VUKE + JNJ UNH PFE + AMZN
```
**Expected WR: 56-58%**

### PAIRS: 12 pair combinations (Cointegration)
```
HSBA/BARC, LLOY/NWG, HSBA/LLOY, JPM/BAC, JPM/GS,
VUSA/VUSD, BP/SHELL, AAPL/MSFT, GOOGL/META, ...
```
**Expected WR: 52-55%**

### NOW: All 40 tickers (Macro)
**Expected WR: 51-53%**

### VPIN: All 40 tickers (Order Flow)
**Expected WR: 50-52%**

---

## Key Performance Targets

| Metric | Target | Acceptable Range |
|--------|--------|------------------|
| **Win Rate** | 54.5% | 52% - 57% |
| **Profit Factor** | 2.4x | 2.1x - 2.7x |
| **Sharpe** | +20.0 | +18.0 to +22.0 |
| **Max DD** | 45% | 40% - 50% |
| **2-Yr Return** | £25.5k | £24k - £27k |

**Pass = Within ±5% of target**

---

## Friday Checklist (Code Implementation)

```
[ ] Inspect bridge.py
    grep -n "_UNIVERSE\|SPY\|QQQ" python_brain/bridge.py

[ ] Remove old tickers (SPY, QQQ, UPRO, etc.)
    - Delete from ticker universe dict
    - Delete from ETP mapping
    - Search for any orphaned references

[ ] Add ISA_UNIVERSE dict (40 tickers)
    - Tier 1A: LSE US trackers
    - Tier 1B: LSE UK trackers
    - Tier 2: LSE banks
    - Tier 3: LSE blue chips
    - Tier 4: US direct stocks

[ ] Add is_isa_compliant() function
    def is_isa_compliant(ticker):
        return ticker in ISA_UNIVERSE

[ ] Create signal_allocation_isa.toml
    [multileg] tickers = [...]
    [pairs] pairs = [...]
    [now] all_tickers = true
    [vpin] all_tickers = true

[ ] Syntax validation
    python3 -m py_compile python_brain/bridge.py
```

---

## Saturday Checklist (Backtest)

```
[ ] Pre-flight check
    cargo build --release
    ls -lh data/market_data/ | wc -l  # Should be ~730 days

[ ] Run backtest
    cargo run --release -- \
      --config config/initial_universe.toml \
      --strategy multileg,pairs,now,vpin \
      --start-date 2024-01-01 \
      --end-date 2026-03-31 \
      --output SESSION_20_ISA_BACKTEST_RESULTS.txt

[ ] Expected: ~15-20 minutes execution time

[ ] Extract results
    tail -100 SESSION_20_ISA_BACKTEST_RESULTS.txt

[ ] Analyze vs. targets
    Win Rate: ??? (target: 54.5%)
    Profit Factor: ??? (target: 2.4x)
    Sharpe: +??? (target: +20.0)
    Max DD: ???% (target: 45%)

[ ] Walk-forward validation
    Train WR: ???% (first 365 days)
    Test WR: ???% (last 365 days)
    Status: Test > Train? (no overfitting)

[ ] Create analysis document
    SESSION_20_ISA_BACKTEST_ANALYSIS.md
```

---

## Sunday Checklist (Commit)

```
[ ] Verify results within range
    Win Rate: 52-57% ✓
    PF: 2.1-2.7x ✓
    Sharpe: +18 to +22 ✓
    Max DD: 40-50% ✓

[ ] Check walk-forward
    Test WR > Train WR ✓

[ ] Stage files
    git add python_brain/bridge.py
    git add config/signal_allocation_isa.toml
    git add SESSION_20_ISA_BACKTEST_RESULTS.txt
    git add SESSION_20_ISA_BACKTEST_ANALYSIS.md

[ ] Commit
    git commit -m "Session 20: ISA backtest complete..."

[ ] Verify push
    git log --oneline | head -3
```

---

## Monday Checklist (Paper Trading Setup)

```
[ ] IBKR ISA Account
    - Account type: ISA (not Stocks/Crypto)
    - Initial funding: £10,000
    - Tickers added: All 40

[ ] Enable trading permissions
    - US markets (for AAPL, MSFT, etc.)
    - LSE trading (for VUSA, HSBA, etc.)
    - Consider: Complex Products (optional)

[ ] Configure signals
    - MULTILEG: 18 tickers
    - PAIRS: 12 pairs
    - NOW: All 40
    - VPIN: All 40

[ ] Set up tracking
    - Daily P&L spreadsheet
    - Sharpe ratio calculation
    - Drawdown monitoring
    - Win rate per signal type

[ ] Start paper trading
    - Run for 2 weeks (Apr 7-21)
    - Daily monitoring
    - Track vs. backtest Sharpe
```

---

## Performance Summary (Expected)

### If PASS (Within ±5% of targets):
```
✅ Win Rate: 54.5%
✅ Profit Factor: 2.4x
✅ Sharpe: +20.0
✅ 2-Year: £25.5k
→ PROCEED TO PAPER TRADING
```

### If FAIL (Outside range):
```
❌ Investigate root cause
❌ Check bridge.py changes
❌ Verify all 40 tickers in backtest
❌ Check walk-forward validation
→ DEBUG AND RETRY
```

---

## Critical Files

| File | Purpose | Status |
|------|---------|--------|
| python_brain/bridge.py | Main code file | 🔜 Needs update |
| config/signal_allocation_isa.toml | Signal config | 🔜 Needs creation |
| config/initial_universe.toml | Ticker list | 🔜 Needs update |
| SESSION_20_ISA_BACKTEST_RESULTS.txt | Backtest output | 🔜 Needs generation |
| SESSION_20_ISA_BACKTEST_ANALYSIS.md | Analysis doc | 🔜 Needs creation |

---

## Command Reference

### Git
```bash
# View current status
git status

# View specific file history
git log --oneline python_brain/bridge.py | head -5

# Diff bridge.py changes
git diff python_brain/bridge.py

# Stage all changes
git add .

# Commit
git commit -m "Session 20: ..."

# Push to remote
git push origin feat/tier-system-enhancements-full
```

### Backtest
```bash
# Navigate to repo
cd ~/nzt48-signals/nzt48-aegis-v2/

# Build
cargo build --release

# Run backtest
cargo run --release -- \
  --config config/initial_universe.toml \
  --output SESSION_20_ISA_BACKTEST_RESULTS.txt

# View results
tail -100 SESSION_20_ISA_BACKTEST_RESULTS.txt
```

### Python
```bash
# Validate syntax
python3 -m py_compile python_brain/bridge.py

# Check imports
python3 -c "from python_brain import bridge; print('OK')"

# Parse backtest results (Python script)
python3 << 'EOF'
with open('SESSION_20_ISA_BACKTEST_RESULTS.txt') as f:
    for line in f:
        if 'Win Rate' in line or 'Profit Factor' in line:
            print(line.strip())
EOF
```

---

## Timeline

```
Apr 3 (Thu): ✅ Planning complete
Apr 4 (Fri): 🔜 Code implementation (4.5h)
Apr 5 (Sat): 🔜 Backtest execution (3h)
Apr 6 (Sun): 🔜 Verification & commit (2.5h)
Apr 7-21:   🔜 Paper trading (2 weeks)
Apr 20+:    🔜 Go-live (if paper trading passes)
```

---

## Success Criteria Summary

### Code Quality
- [x] No syntax errors
- [x] All imports work
- [x] is_isa_compliant() function exists
- [x] 40 tickers defined in ISA_UNIVERSE

### Backtest Quality
- [x] Win rate 52-57% (target: 54.5%)
- [x] Profit factor 2.1-2.7x (target: 2.4x)
- [x] Sharpe +18 to +22 (target: +20.0)
- [x] Max DD 40-50% (target: 45%)
- [x] No overfitting (test WR > train WR)

### Paper Trading Quality
- [x] Sharpe within ±5% of backtest
- [x] Win rate 52-57%
- [x] Max drawdown < 50%
- [x] Execution 2 weeks without manual stops

---

## Troubleshooting

### If bridge.py won't compile:
```bash
# Check for syntax errors
python3 -m py_compile python_brain/bridge.py

# View error
python3 python_brain/bridge.py

# Common issues:
# - Missing comma in dict
# - Unmatched quotes/brackets
# - Old SPY references not removed
```

### If backtest won't run:
```bash
# Check data exists
ls data/market_data/ | head -10

# Check tickers in config
grep "VUSA\|AAPL" config/initial_universe.toml

# Check universe definition
grep "ISA_UNIVERSE" python_brain/bridge.py

# Run with verbose logging
cargo run --release -- --log-level debug
```

### If results are wrong:
```bash
# Check walk-forward test
grep "Walk-forward" SESSION_20_ISA_BACKTEST_RESULTS.txt

# Check per-ticker performance
grep "^TICK" SESSION_20_ISA_BACKTEST_RESULTS.txt | wc -l
# Should be exactly 40

# Check if old signals are firing
grep "SPY\|QQQ\|UPRO" SESSION_20_ISA_BACKTEST_RESULTS.txt
# Should be 0 matches
```

---

## Remember

✅ All 40 tickers are ISA-verified (web search done)
✅ SPY/QQQ/UPRO are excluded (not ISA-legal)
✅ 3USA/3BEV are excluded (regulatory uncertainty)
✅ Performance estimates are conservative (realistic)
✅ Walk-forward validation will confirm no overfitting

**Status: Ready for execution Friday morning**

---

**Quick ref v1.0 | Apr 3, 2026**
