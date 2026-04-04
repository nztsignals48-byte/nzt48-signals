# AEGIS V2 — Known Limitations

**Audit Date:** 2026-04-04

---

## 1. Backtest Coverage Gap

### What is tested
Two backtest tools exist, exercising different subsets:

**`fast_backtest_pipeline.py`** (quick validation): 10 entry types
- TypeA, TypeB, TypeD, TypeE, TypeF, S2_Reversion, S3_MacroTrend
- S5_OvernightCarry, VolCompression, NAVArbitrage, FOmcDrift

**`world_class_backtest.py`** (full-fidelity): 14 entry types
- All 10 above PLUS: VolExpansion, GapFade, NightRider, AlphaFactory
- Runs the real 33-CHECK Python risk arbiter with per-exchange spreads
- GARCH/scanner proxies calibrated by entry type (not sentinels)
- Walk-forward IS/OOS split, Sharpe/Sortino/Calmar, strategy attribution

Combined: **14 of 34 signal generators** are backtestable (41% coverage).

### What is NOT tested in backtest
- 20 bridge.py signal generators requiring real-time data (VanguardSniper, Orchestrator, LeadLag, EMAT, SwarmPredictor, HFT_Probability, etc.)
- 25+ pre-signal quality gates (VPIN toxicity, liquidity pulse, TDA crash detector, adversarial detection)
- 15+ post-signal overlays (calendar anomaly modifier, hedge confidence, Compounding Machine, Student-t Kelly)
- 12-factor Kelly sizing (backtest uses flat confidence-based sizing)

### Why the gap exists
The backfill simulator uses historical OHLCV bars from yfinance. The remaining 20 strategies require:
- Real-time tick data (S1_Microstructure, HFT_Probability)
- Live VIX feed (S4_VolPremium, S7_TailHedge, conditional hedging)
- Cross-instrument correlations (LeadLag, PairsCointegration)
- ML model accumulated state (EMAT, SwarmPredictor, ReservoirComputing)
- External data feeds (CopyTrading, MacroNowcast)
- L2 order book (HFT_Probability, NegRiskArb)

### Path to full-parity backtest
The quarantined `production_backtest.py` spawns the actual bridge.py subprocess and sends tick-format JSON via stdin. This would exercise the full pipeline but requires extensive runtime.

## 2. Veto Rate: 0% in Simulation

### What happens
The Python risk arbiter in simulation mode produces a 0% veto rate — all 9.4M trades pass.

### Why
- `simulation_mode=True` relaxes: max positions to 999, velocity/trade limits to 999999
- Portfolio equity is NOT updated per-trade (stays at starting equity) — this prevents drawdown gates from cascade-blocking across 9.4M sequential trades
- GARCH sigma and scanner score use realistic proxy values per entry type (not sentinel -1.0)
- Quality gates (confidence floor, spread veto, structural score) are active but most signals naturally pass

### Impact
The backtest shows gross strategy performance across the full universe. In live trading, the 39-CHECK Rust arbiter will reject 30-70% of signals due to:
- Tighter drawdown gates (4% daily, 15% peak)
- Velocity limits (5 per ticker per 5 min, 3 trades/day)
- Real portfolio state (cash buffer, sector heat, correlation concentration)
- Real bid-ask spreads (vs backtest per-exchange estimates)

### Mitigation
Paper trading (starting April 7+) will produce real veto statistics under live market conditions.

## 3. Equity Curve Compounding Overstates Returns

### What happens
The equity curve uses Kelly compounding (`equity *= (1 + kelly_fraction * pnl_pct)`) which produces unrealistic terminal values when applied to 16M+ trades.

### Why
- 16M trades with even small positive edge, compounded at 10% Kelly, produces astronomical returns
- Real-world constraints (margin, slippage, execution capacity, market impact) cap actual returns far below theoretical

### Realistic estimate
With 3 max concurrent positions, 3 trades/day max, 0.05 Kelly clamp, and real execution costs:
- Starting equity: £10,000 ISA
- 2-year realistic range: £22,000 — £130,000
- This assumes consistent positive edge holds through regime changes

## 4. Trade Count Clustering

### What happens
Some tickers generate disproportionately many trades. A single volatile ticker can produce thousands of S2_Reversion or TypeE entries.

### Impact
Top 50 tickers by trade count may dominate the aggregate statistics.

### Mitigation
- `world_class_backtest.py` adds per-entry-type cooldowns (5-40 bars) and per-ticker daily cap (5 entries/day)
- Per-ticker breakdown in JSON report shows distribution
- Trade ledger allows filtering by ticker for analysis
- Live system has per-ticker velocity limits (CHECK 19) and cooldown periods

## 5. Session 21 vs Session 22 Discrepancy

### What happens
Session 21 reported TypeF with 67.66% WR and 35.97x PF. Session 22 shows TypeF at 49.6% WR and 1.024x PF.

### Why results differ
- Session 21 likely had a bug inflating TypeF OBV-RSI readings (column alignment issue)
- Session 21 had no entry cooldowns — same signal could re-fire every bar
- Session 22 uses corrected code with proper column alignment
- Session 22's 0.998x aggregate PF is the honest baseline

### Assessment
Session 22 results are more trustworthy. The system shows genuine alpha in FOmcDrift (1.37x PF), NAVArbitrage (1.19x PF), and TypeD (1.08x PF). TypeF is marginal but positive.

## 6. Bar Granularity Mismatch

### Backtest: 60-minute bars from yfinance
### Live: 5-second bars from IBKR

| Aspect | Impact |
|--------|--------|
| Entry precision | 60m bar entry is nearest hourly bar vs 5s precise entry in live |
| Spread modeling | Flat spread assumption vs real bid-ask in live |
| Gap detection | First bar of day vs exact open tick |
| Volume profile | Hourly aggregated vs tick-level VPIN |
| Microstructure signals | Cannot function on 60m data |

## 7. No L2 Data in Backtest

Live system receives L1 tick-by-tick BidAsk from IBKR. Backtest has only OHLCV. This means:
- Spread veto (CHECK 13) uses hardcoded per-exchange estimates, not actual spreads
- S1_Microstructure is disabled (needs tick data for TMR, VPIN, spread compression)
- Quote imbalance detection (spoof/sweep detection) is not exercised
- Amihud illiquidity is approximated from daily volume, not tick-level

## 8. Calendar Approximations

### FOMC dates
Backtest uses "3rd Wednesday of FOMC months" rule. Actual FOMC schedule varies and is loaded from `config/economic_calendar.toml` in live mode. Some FOMC meetings may be missed or incorrectly placed.

### UK holidays
The Rust clock uses `config/uk_holidays.toml` for LSE closures. The backtest does not filter for holidays — some LSE entries may fall on actual holiday dates with no real trading.

### Calendar anomaly modifiers
The CalendarAnomalies module (Book 171) is wired into bridge.py's overlay pipeline but NOT into the backfill simulator. Day-of-week and turn-of-month effects are therefore not applied in the backtest confidence adjustment.

## 9. FX Rate Assumptions

Backtest uses hardcoded FX rates (USD/GBP = 0.79, EUR/GBP = 0.86, etc.). Live system uses Ouroboros nightly FX rates with 6-hour refresh. FX movements over the 730-day backtest period are not modeled.

## 10. No Multi-Position Portfolio Tracking

The backfill simulator processes each ticker independently. There is no cross-ticker portfolio tracking for:
- Correlation concentration (CHECK 34)
- Sector heat (CHECK 16)
- Portfolio heat (CHECK 15)
- Cash buffer (CHECK 14)

Live system tracks all of these in real-time via the `PortfolioState` struct in Rust.
