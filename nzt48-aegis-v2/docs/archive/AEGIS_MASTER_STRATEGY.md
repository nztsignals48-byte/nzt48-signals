# AEGIS V2 Master Strategy Document

**Version**: 1.0 (2026-03-17)
**Authority**: Runtime truth. Where this document contradicts design docs, this document wins.
**Scope**: Complete description of what AEGIS V2 actually does, what it fails to do, and what must change before daily compounding begins.

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [Runtime Truth vs Intended Design](#2-runtime-truth-vs-intended-design)
3. [Core Philosophy](#3-core-philosophy)
4. [Market/Session/Calendar Doctrine](#4-marketsessioncalendar-doctrine)
5. [Universe Selection and Tracking](#5-universe-selection-and-tracking)
6. [Signal Generation Architecture](#6-signal-generation-architecture)
7. [Sniping and Entry Timing](#7-sniping-and-entry-timing)
8. [Trade Formation and Sizing](#8-trade-formation-and-sizing)
9. [Execution Engine and Order Lifecycle](#9-execution-engine-and-order-lifecycle)
10. [Profit Ladder and Exit Logic](#10-profit-ladder-and-exit-logic)
11. [Risk Architecture](#11-risk-architecture)
12. [Learning System / Adaptive Layer](#12-learning-system--adaptive-layer)
13. [Infrastructure and Runtime](#13-infrastructure-and-runtime)
14. [State, Storage, Sources of Truth](#14-state-storage-sources-of-truth)
15. [Monitoring, Telemetry, Explainability](#15-monitoring-telemetry-explainability)
16. [Current Known Flaws and Hidden Weaknesses](#16-current-known-flaws-and-hidden-weaknesses)
17. [Simplification and Streamlining Plan](#17-simplification-and-streamlining-plan)
18. [Upgrade Blueprint](#18-upgrade-blueprint)
19. [Daily Compounding Blueprint](#19-daily-compounding-blueprint)
20. [External Review Integration](#20-external-review-integration)
21. [Final Truth Section](#21-final-truth-section)

[Appendix A: Flaw Appendix](#appendix-a-flaw-appendix)
[Appendix B: Hidden Flaw Appendix](#appendix-b-hidden-flaw-appendix)
[Appendix C: Timing/Session Appendix](#appendix-c-timingsession-appendix)
[Appendix D: State/Storage Appendix](#appendix-d-statestorage-appendix)
[Appendix E: Highest Priority Fixes](#appendix-e-highest-priority-fixes)
[Appendix F: What Must Be True to Start Daily Compounding](#appendix-f-what-must-be-true-to-start-daily-compounding)
[Appendix G: Luxury Upgrades](#appendix-g-luxury-upgrades)
[Appendix H: Simplification Plan](#appendix-h-simplification-plan)

---

## 1. Executive Overview

AEGIS V2 is a Rust-and-Python hybrid algorithmic trading engine designed to compound capital daily inside a UK Individual Savings Account (ISA) by trading leveraged Exchange-Traded Products (ETPs) on the London Stock Exchange and global equity markets via Interactive Brokers (IBKR).

**What it is**: 27,608 lines of Rust (engine, risk, execution, infrastructure) + approximately 10,443 lines of Python (signal generation, nightly learning, universe management). The Rust engine runs continuously inside Docker on an EC2 c7i-flex.large instance. It receives 5-second market data bars from IBKR via the ibapi crate, routes ticks through a universe filter, sends them to a Python subprocess ("Python Brain") for signal evaluation, passes approved signals through a 27-check synchronous risk gate, sizes positions via 12-factor Kelly, and submits orders back through IBKR. A nightly Python process called Ouroboros performs learning, parameter tuning, and watchlist rotation.

**What it actually achieves today**: The engine starts, connects to IB Gateway, subscribes to market data, receives ticks, generates signals, passes them through the risk arbiter, sizes positions, and attempts to submit orders. As of this writing, zero trades have filled successfully due to cascading execution-layer failures documented in Section 2. The signal generation pipeline is alive and producing thousands of signals per session. The risk arbiter approves them. The sizing engine computes valid positions. But orders die at the broker interface.

**Account**: Paper trading mode, 10,000 GBP starting equity, UK ISA wrapper. IS_LIVE is hardcoded to `false` and the binary exits immediately if set to `true`.

**Core 12 ISA Tickers**: QQQ3.L, QQQS.L, 3LUS.L, 3USS.L, QQQ5.L, 5SPY.L, 3SEM.L, 3NVD.L, 3TSL.L, GPT3.L, 3TSM.L, 2MU.L. These are 3x and 5x leveraged/inverse ETPs on major indices and single stocks. The system is not limited to these -- Ouroboros can rotate in any ticker from a 36,000+ universe.

**Target**: 0.3-0.5% daily net returns (145-348% annualised). This requires fixing the execution layer first.

---

## 2. Runtime Truth vs Intended Design

This section catalogues every known divergence between what the design documents describe and what the code actually does in production. Runtime truth is the highest authority.

### 2.1 Order ID Bug (FIXED)

**Design**: Each order submitted to IBKR must have a unique order ID. The engine was supposed to call `next_valid_id()` from IBKR to get a monotonically increasing ID.

**Runtime truth**: `next_valid_id()` returned a static value. Every order after the first received `DuplicateOrderId` from IBKR and was silently rejected. Thousands of signals passed all 27 risk checks, were sized correctly, had valid WAL entries written -- and then vanished at the broker with no log output.

**Fix applied**: Added `order_counter: u64` to the `Engine` struct. Each `submit_order` call now increments the counter and generates `format!("order-{}", self.order_counter)`. This is a monotonic, crash-safe (WAL-recoverable) counter that guarantees unique IDs.

**Location**: `rust_core/src/engine.rs`, lines 426 and 1194-1195.

### 2.2 Silent Order Failure (FIXED)

**Design**: Failed order submissions should be logged and tracked so the operator knows when and why orders are rejected.

**Runtime truth**: The `submit_order()` error path returned silently with zero logging. The code was:
```
if let Err(_e) = self.broker.submit_order(...) { return; }
```

**Fix applied**: Added explicit `ORDER_REJECTED` eprintln with ticker, order_id, quantity, limit price, and error details. Now every broker rejection is visible in container logs.

**Location**: `rust_core/src/engine.rs`, lines 1219-1228.

### 2.3 FX Conversion Missing (IN PROGRESS)

**Design**: All portfolio values should be denominated in GBP (the ISA home currency). The `FxRateTable` exists in `currency.rs` with hardcoded default rates for EUR, CHF, SEK, NOK, DKK, PLN, USD to GBP.

**Runtime truth**: The `Currency` enum only defines GBP, EUR, CHF, SEK, NOK, DKK, PLN, USD. It is missing JPY, KRW, HKD, AUD -- the four currencies needed for Asian markets (TSE, KRX, HKEX, ASX). When the engine holds positions in Asian tickers, `mark_to_market()` adds raw prices in their native currencies: 1,500,000 yen + 3,400 pounds = nonsense. The `portfolio.equity` field becomes meaningless the moment a non-European, non-US position is opened.

**Impact**: All risk checks that reference `portfolio.equity` become unreliable. Daily drawdown calculation is wrong. Position sizing is wrong. ISA annual limit tracking is wrong. This is a P0 defect for global trading.

**What exists**: `FxRateTable::to_gbp()` works correctly for currencies it knows about. It silently returns `amount * 1.0` for unknown currencies (the `unwrap_or(1.0)` fallback), which means 1 JPY = 1 GBP in the current code.

### 2.4 IBKR Symbol Resolution

**Design**: The `derive_ibkr_symbol()` function strips yfinance suffixes (.L, .T, .AX, .KS, .HK, etc.) and applies exchange-specific transformations. HKEX strips leading zeros. KRX preserves them. The `ibkr_exchange()` function maps internal exchange names to IBKR API codes (TSE->TSEJ, HKEX->SEHK, KRX->KSE, etc.).

**Runtime truth**: Many Asian tickers return IBKR error code 200 ("No security definition has been found for the request"). The paper account has market data permissions, but the combination of symbol derivation + exchange mapping + currency + security type is incorrect for some tickers. LSE ETPs work reliably because they have been manually validated. Asian tickers have not been validated ticker-by-ticker.

**Impact**: During Asian hours (currently the majority of ACTIVE time), the engine generates signals for tickers it cannot actually trade. 10,581 SIGNAL_ARRIVED events were observed in a single session, all passing the 27-check risk arbiter, all approved for sizing, and most failing at broker submission due to wrong symbols or currencies.

### 2.5 Entry Mode Correctness

**Design**: ACTIVE mode runs 22 hours/day (23:00-21:00 London). Only DARK (21:00-23:00) blocks new entries.

**Runtime truth**: This works correctly. `SessionManager::compute_mode()` returns `Active` for 23:00-21:00 and `Dark` for 21:00-23:00. If positions are held during Dark, it returns `Carry`. The `entries_allowed()` method returns true for Active and all legacy mode aliases (ModeA, ModeB, ModeBPlus, ModeC, Auction). Unit tests verify exactly 22*60 minutes are Active and 2*60 minutes are Dark.

### 2.6 Regime Auto-Recovery

**Design**: HALT from stale data or watchdog should auto-clear when data resumes.

**Runtime truth**: The `halt_from_watchdog` flag distinguishes watchdog-caused HALTs from liquidation/panic/manual HALTs. When `halt_from_watchdog` is true and fresh data arrives, the engine clears HALT automatically. When false (e.g., 3 consecutive stop losses), only `manual_clear_halt()` can restore NORMAL. Consecutive stop losses reset daily via the daily reset path.

### 2.7 Signal Flow Funnel

**Design intent**: Signals should be generated, filtered, and result in filled trades.

**Actual measured flow**:
- 10,581 SIGNAL_ARRIVED (Python Brain generating signals)
- All pass 27-check risk arbiter
- All receive SIZING approval with computed position sizes
- Most fail at broker.submit_order() due to symbol/currency/minimum size errors
- Zero fills confirmed

The funnel is wide at the top and completely blocked at the bottom.

---

## 3. Core Philosophy

### 3.1 Daily Compounding

The entire system exists to achieve one thing: compound capital daily. Not weekly. Not monthly. Daily. The mathematics are simple but powerful:

- 0.3% daily net x 252 trading days = 113% annualised (with compounding)
- 0.5% daily net x 252 trading days = 251% annualised
- 1.0% daily net x 252 trading days = 1,124% annualised

The target is 0.3-0.5% daily net after fees. This is achievable with leveraged ETPs that routinely move 3-15% intraday. A 3x Nasdaq ETP gaining 1% in the underlying means 3% in the ETP. Capturing just 10-15% of that move on one trade hits the daily target.

### 3.2 Full ISA Utilisation

The UK ISA allows 20,000 GBP in annual contributions with zero capital gains tax and zero dividend tax on all gains. Every trade inside the ISA wrapper is tax-free. The engine tracks `isa_year_invested` against the 20,000 GBP limit and rejects trades that would breach it. This is checked by both the ISA gate (`isa_gate.rs`) and the risk arbiter (CHECK 17).

### 3.3 Inverse ETPs for Downside

The ISA Core 12 includes QQQS.L and 3USS.L (inverse ETPs). These go up when the underlying goes down. Because ISA rules prohibit short selling, inverse ETPs are the only way to profit from market declines within the tax wrapper. The risk arbiter (CHECK 1) immediately HALTs and rejects any `Direction::Short` order. The `inverse_blocker` in the portfolio state prevents holding both a long and inverse position on the same underlying simultaneously (CHECK 2, H32).

### 3.4 Continuous Improvement

Ouroboros runs nightly at 23:50 ET. It analyses the day's trades, updates Bayesian win rates, optimises Chandelier ATR multipliers, detects alpha decay, and generates a pre-market battle plan. Parameters are bounded by guardrails: Kelly fractions between 0.15-0.30, Chandelier ATR multipliers between 1.5-4.0, no parameter can drift more than 15% from baseline in a single night. The engine loads these parameters on startup via `ouroboros_loader.rs`.

---

## 4. Market/Session/Calendar Doctrine

### 4.1 The 22-Hour Active Window

The engine operates on a unified 22-hour active window:

| Period | London Time | Duration | Mode | Behaviour |
|--------|-------------|----------|------|-----------|
| ACTIVE | 23:00-21:00 | 22 hours | Active | All 6 markets monitored simultaneously. New entries allowed. Exits processed. Ticks streamed. |
| DARK | 21:00-23:00 | 2 hours | Dark/Carry | No new entries. Ouroboros nightly runs. If positions held, mode is Carry (frozen stops, no new entries). |

The implementation in `session_manager.rs` is precise:
```
ACTIVE_START = 23 * 3600  // 23:00 London
ACTIVE_END   = 21 * 3600  // 21:00 London
```
Active hours wrap midnight: `london_time_secs >= ACTIVE_START || london_time_secs < ACTIVE_END`.

### 4.2 Six Market Regions

The engine covers six market regions simultaneously during ACTIVE hours:

| Region | Markets | Approx UTC Hours | Exchange Codes |
|--------|---------|-------------------|----------------|
| Asia-Pacific | TSE, HKEX, KRX, SGX, ASX, NZX | 00:00-08:00 | TSEJ, SEHK, KSE, SGX, ASX |
| Europe | LSE, XETRA, Euronext (PA/AS/BR/LS), SIX, Nordic (STO/CPH/OSL/HEL), Milan, Madrid | 07:00-16:30 | LSE, LSEETF, IBIS, SBF, AEB, EBS, SFB, XCSE, OSE, HEX, BVME, BM |
| US | NYSE, NASDAQ, AMEX | 14:30-21:00 | SMART |

Market closures are handled naturally. When an exchange closes, its tickers score lower in the Ouroboros ranking and rotate out on the next 15-minute watchlist refresh. No explicit exchange-hour enforcement is needed in the engine for entry blocking (removed in the auction period check simplification, CHECK 12 comment: "Global engine trades 6 markets across all timezones. Spread veto provides natural protection during auction periods.").

### 4.3 Entry Cutoff

The risk arbiter enforces a hard entry cutoff at 20:55 London (CHECK 11). This is `entry_cutoff_secs = 20 * 3600 + 55 * 60 = 75,300 seconds`. The 5-minute buffer before Dark at 21:00 prevents entering trades that cannot be managed.

### 4.4 Calendar Awareness

Cron jobs run Monday-Friday only (`1-5` or `0-4` for Sunday-night Asian kickoff). The 15-minute ticker rotation covers:
- Asian + pre-market: `*/15 23 * * 0-4` and `*/15 0-7 * * 1-5`
- European + US: `*/15 8-20 * * 1-5`

The Sunday 23:00 entry (day 0) catches the NZX open (21:00 UTC) and prepares for TSE/KRX opens at midnight UTC on Monday.

---

## 5. Universe Selection and Tracking

### 5.1 Four-Tier Universe Architecture

The ticker selector (`python_brain/ouroboros/ticker_selector.py`) manages a 36,000+ ticker universe using a four-tier scoring system:

| Tier | Name | Count | Data Source | Refresh |
|------|------|-------|-------------|---------|
| 1 | HOT | ~200 | Real-time 5s bars from IBKR | Continuous |
| 2 | WARM | ~800 | Daily yfinance price fetch | Daily |
| 3 | APEX | ~2,000 | Weekly yfinance price cache | Weekly |
| 4 | COLD | ~30,000+ | Static scoring only (market cap, sector, leverage, exchange) | Never (no network calls) |

Only ~1,500 tickers (Tier 1+2) require daily yfinance API calls. Tier 3 uses a weekly cache. Tier 4 uses zero network calls. This design keeps API costs near zero while maintaining a massive opportunity set.

### 5.2 IBKR 100-Line Subscription Limit

IBKR allows a maximum of 100 simultaneous market data subscriptions. The `SubscriptionManager` in the Rust engine manages this budget via a `LineBudget`:
- Carry pool: 30 lines (positions held overnight)
- Active pool: 50 lines (tickers being actively traded)
- Scan pool: 20 lines (Ouroboros discovery/rotation)
- Total: 100 lines, invariant enforced at construction time

Every 15 minutes, Ouroboros writes a new `active_watchlist.json`. The engine detects the file modification via `watchlist_mtimes`, hot-reloads the watchlist, unsubscribes stale tickers, and subscribes new ones. The ISA Core 12 always occupy the first 12 slots. Remaining slots are allocated by Ouroboros scoring: momentum, volatility, liquidity, and sector balance.

### 5.3 Universe Routing

In the Rust engine, every tick is routed through the `Universe` module:
- **Vanguard** class: continuous tick delivery to Python Brain (Tier 1/2 tickers)
- **Apex** class: 60-second OHLCV snapshots buffered in `ApexCandle` structs (Tier 3)
- **Filtered**: ticks rejected before reaching strategies (Amihud illiquidity, wide spreads, erroneous ticks >5% deviation from 1s EMA, reverse splits >500% overnight, synthetic halts >30s gap)

No tick is ever routed to both Vanguard and Apex paths.

### 5.4 Contract Registration

Tickers must have a `ContractMapping` registered in the `IbkrBroker.contract_map` before orders can be submitted. The mapping includes: symbol, IBKR symbol (derived via `derive_ibkr_symbol()`), exchange, IBKR exchange (mapped via `ibkr_exchange()`), currency, security type, and leverage factor. Unregistered tickers generate signals that pass all risk checks but fail at order submission because no contract exists.

---

## 6. Signal Generation Architecture

### 6.1 Python Brain

Signal generation is entirely delegated to Python via a long-lived subprocess bridge. The Rust engine spawns a Python process (`python_brain/bridge.py`) that reads JSON messages on stdin and writes JSON responses on stdout.

**Protocol**:
- Engine sends: `{"type":"tick", "ticker_id":N, "last":X, "high":X, "low":X, "bid":X, "ask":X, "volume":N, "timestamp_ns":N, ...context...}`
- Brain responds: `{"type":"signal", "direction":"Long", "confidence":75.0, "kelly_fraction":0.08, "shares":5, "strategy":"VanguardSniper"}` or `{"type":"no_signal", "ticker_id":N}`
- Shutdown: `{"type":"shutdown"}`

The bridge accumulates bar history per ticker (up to 500 bars in a deque). On each tick it computes microstructure indicators (RVOL, Hurst exponent, volume divergence) and evaluates the VanguardSniper strategy.

### 6.2 VanguardSniper Strategy

The primary signal generator. Located in `python_brain/brain/strategies/vanguard_sniper.py`. Pure function, no side effects, no I/O, no state mutation.

**Indicators computed**:
- EMA (fast period, configurable)
- ADX (Wilder's Average Directional Index, period 14)
- Moreira-Muir (2017) volatility scaling: scales position inversely by realized volatility
- Volume breakout detection: volume > N * rolling average

**Signal generation logic**: When ADX exceeds threshold (indicating a strong trend), price is above EMA (bullish momentum), and volume confirms the move, VanguardSniper generates a Long signal with a confidence score. The confidence floor is 65.0 (configurable in `brain/config.py`).

**Critical 2026-03-11 fix**: A phantom fallback was removed that generated 78% confidence Long trades when the Python bridge was dead. Now: no Python signal = no trade. This is enforced at engine line 1121: `let Some(ref sig) = signal else { return; };`

### 6.3 Microstructure Indicators

Computed in the bridge before signal evaluation:
- **RVOL** (Relative Volume): `calculate_rvol(volumes, window=20)` -- current volume relative to 20-bar average
- **Hurst Exponent**: `estimate_hurst(prices, max_lag=20)` -- H>0.5 trending, H<0.5 mean-reverting, H~0.5 random walk. Classified into regimes: "trending", "mean_reverting", "random"
- **Volume Divergence**: `volume_divergence(prices, volumes, window=10)` -- price/volume disagreement

### 6.4 Confidence Floor

The risk arbiter enforces a hard confidence floor of 65.0 (CHECK 10). Any signal below this is vetoed. This prevents low-conviction entries. The floor is configurable via `RiskConfig.confidence_floor`.

---

## 7. Sniping and Entry Timing

### 7.1 Philosophy: Join Rides Early

The strategy is momentum-based. The goal is to detect early signs of a strong directional move and ride it for 2%+ profit. The VanguardSniper name reflects this: identify the move, enter early, let the Chandelier trailing stop protect the position while it runs.

### 7.2 Entry Gates

Before a signal can become an order, it must pass through multiple gates in sequence:

1. **Position check**: No entry if position already exists for this ticker
2. **Cooldown check**: No entry if ticker is in gap cooldown period
3. **Session mode**: Must be in Active mode (or legacy ModeA/B/B+/C/Auction)
4. **Auction period**: No entries during LSE auctions (`Clock::is_auction(time_secs)`)
5. **Predictive scorer lock**: Locked if 5 consecutive losses on this ticker
6. **Jump-diffusion regime gate**: Blocked if regime detector detects jump-diffusion signature
7. **Sector concentration**: Blocked if sector already holds >33% of equity
8. **Liquidation defense**: Blocked if ISA allowance < 3% of equity
9. **European session gate**: During ModeB (Europe-only), XLON must be open
10. **Python Brain signal**: Must receive a valid signal (no phantom fallback)
11. **Risk arbiter**: 27-check evaluation (see Section 11)
12. **ISA gate**: Trade value must not breach ISA limits

### 7.3 Early Runner Detection

The `EarlyRunnerDetector` (`entry_engine.rs`) is a stateful detector per ticker that tracks RVOL history. It is designed to identify tickers that are starting to run before the crowd piles in. Each ticker has its own detector instance stored in `early_runner_detectors: HashMap<TickerId, EarlyRunnerDetector>`.

---

## 8. Trade Formation and Sizing

### 8.1 Dual Kelly Architecture

Position sizing is computed in two places:

**Python Brain (12-factor Kelly)**: The primary sizer. Located in `python_brain/brain/sizing/kelly_12factor.py`. Applies 12 factors in sequence:

| Factor | Description | Source |
|--------|-------------|--------|
| 1 | Base Kelly from Bayesian Win Rate (H58) | Laplace-smoothed: (W*N + 0.5*10) / (N+10) |
| 2 | Volatility decay (3x: /9, 5x: /25) (H59) | leverage_factor squared |
| 3 | Moreira-Muir realized vol scaling | Target vol / realized vol, capped [0,2] |
| 4 | Correlation penalty | Reduce if correlated to existing portfolio |
| 5 | Drawdown scaling | Scale down proportional to current drawdown |
| 6 | Amihud liquidity scaling | Reduce for illiquid tickers |
| 7 | Regime scaling | Trending: 1.0, Reduce: 0.5, Mean-reverting: 0.3 |
| 8 | Spread cost adjustment | Deduct round-trip spread from Kelly |
| 9 | Time-of-day scaling | Reduce near close |
| 10 | Confidence scaling | Scale by signal confidence / 100 |
| 11 | Half-Kelly cap (0.5) | Never bet more than 50% Kelly |
| 12 | Portfolio heat limit (6%) | Cap total heat across all positions |

**Rust-side Kelly** (`position_sizer.rs`): A simpler 0.25 fractional Kelly calculator used as a fallback. Formula: `kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_loss`, then multiplied by 0.25.

### 8.2 Paper Bootstrap Problem

During paper trading with zero trade history, the 12-factor Kelly produces near-zero sizing because: (a) Bayesian shrinkage pulls win rate toward 50%, (b) vol decay for 3x ETPs divides by 9, (c) Moreira-Muir halves again. Result: approximately 60 GBP position vs the 1,500 GBP minimum.

**Fix**: The bridge uses a preliminary Kelly floor from VanguardSniper (`confidence/1000`, capped at 0.20) when `total_trades < 50`. This ensures valid position sizes during the 100-trade validation gate.

### 8.3 Risk Arbiter Sizing

After the 27-check risk gate approves a signal, the arbiter computes final size:
1. Effective Kelly = min(intent_kelly, per-ticker Ouroboros cap)
2. Kelly ramp = clamp(validated_trades / 250, 0.1, 1.0) -- gradual ramp-up
3. Ramped Kelly = effective_kelly * kelly_ramp
4. Raw size = ramped_kelly * portfolio.equity
5. Regime scale: Normal=1.0, Reduce=0.5 (Ouroboros-calibrated overrides available)
6. Adjusted size = raw_size * regime_scale
7. Minimum entry gate: if validated_trades >= 250 and adjusted_size < 1,500 GBP, reject

The minimum entry gate is suspended during Kelly ramp (trades < 250) to allow paper trading to function.

### 8.4 Share Count

The engine prefers Python's Kelly-computed share count: `if shares_hint > 0 { shares_hint } else { (adjusted_size / ask).max(1) as u32 }`. This means Python Brain has primary authority over sizing, with the Rust arbiter as a safety backstop.

### 8.5 Account Math

Starting equity: 10,000 GBP. ISA annual limit: 20,000 GBP. Maximum 6 positions (`max_positions`). With 6 positions at even allocation, each position is approximately 1,500-1,700 GBP (after cash buffer). With the 12-factor Kelly, positions will be smaller during the ramp-up phase.

---

## 9. Execution Engine and Order Lifecycle

### 9.1 Order Submission Pipeline

When a signal passes all gates:

1. `order_counter` increments (monotonic, starts from 0)
2. `order_id` = `format!("order-{}", order_counter)`
3. Limit price = ask * (1 + marketable_limit_buffer_pct / 100) -- slightly above ask for marketable limit
4. Limit price rounded to tick size (H65)
5. WAL entry written: `RoutedOrder { order_id, ticker_id, side, confidence, strategy, kelly_fraction, approved_size }`
6. `broker.submit_order(&order_id, ticker_id, OrderSide::Buy, qty, limit_price)` called
7. If error: `ORDER_REJECTED` logged with full context, function returns
8. If success: telemetry `orders_submitted` counter incremented
9. `Executioner` tracks the order as `TrackedOrder { lifecycle: Submitted, ... }`
10. T2T (tick-to-trade) latency recorded in `LatencyProfiler`

### 9.2 The Order ID Fix in Detail

The `order_counter` field was added to the `Engine` struct at line 426. Before the fix, the engine called a function that returned the same ID every time, causing IBKR to reject all orders after the first with `DuplicateOrderId`. This was the single most damaging bug in the system -- it rendered the entire execution layer inoperative while giving the impression that everything was working (no error logs, no telemetry alerts).

### 9.3 IBKR Broker Adapter

The `IbkrBroker` (`ibkr_broker.rs`) implements the `BrokerAdapter` trait. Key methods:
- `submit_order()`: builds an IBKR `Contract` from the registered `ContractMapping`, creates a limit order, and calls the ibapi client
- `is_connected()`: checks ibapi connection state
- `drain_events()`: reads fill/cancel/error events from the ibapi event stream

Contract building uses `derive_ibkr_symbol()` to convert watchlist symbols (e.g., "005930.KS") to IBKR format (e.g., "005930") and `ibkr_exchange()` to map exchange names (e.g., "KRX" -> "KSE").

### 9.4 Executioner

The `Executioner` (`exit_engine.rs`) manages order lifecycle states:
- Submitted -> Acknowledged -> PartialFill -> Filled
- Submitted -> Rejected
- Submitted -> Cancelled

Each `TrackedOrder` records: order_id, ticker_id, lifecycle state, submit timestamp, last update timestamp, quantity, filled quantity, limit price, retry count, and whether it is an exit order.

---

## 10. Profit Ladder and Exit Logic

### 10.1 Chandelier Strategy (Le Beau 1999)

The exit engine uses a trailing-stop-only strategy with five rungs. There are no partial sells. Every exit is 100% of the position.

| Rung | Threshold | Stop Logic |
|------|-----------|------------|
| 1 | Entry | Stop = entry - 1x ATR |
| 2 | +2% from entry | Stop = breakeven INCLUDING round-trip fees (0.2%) |
| 3 | +4% from entry | Trail = 1.0x ATR below peak |
| 4 | +6% from entry | Trail = 0.75x ATR below peak |
| 5 | +8% from entry | Trail = 0.5x ATR below peak |
| 5+ | Every additional +2% | Continue at 0.5x ATR below peak |

Once Rung 2 is reached, you cannot lose money on the trade (the stop is above entry + fees). Rung 5 uses the tightest trail (0.5x ATR), allowing winners to run while protecting accumulated profit.

### 10.2 Shadow Stops (H67)

All stops are computed internally by the engine. They are NOT native IBKR trailing stops. This is by design (H67): shadow stops give the engine full control over stop placement and avoid IBKR's trailing stop limitations. The engine monitors price on every tick and submits a market/limit sell when the stop is breached.

### 10.3 Stop Ratchet (H68)

Stops only move up, never down. Once a rung is reached, the stop price is locked at the new, higher level. If the price retraces but does not hit the stop, the rung is retained.

### 10.4 InfiniteChandelier (P4-C)

An advanced variant with 8 adaptive multipliers that adjust based on: volatility, time fraction, momentum, Amihud illiquidity, portfolio heat, and regime. Controlled by `ExitConfig.use_infinite_chandelier` (default: false). The adaptive multipliers are updated via `update_multipliers()` on each tick.

### 10.5 Exit Priority Hierarchy

When multiple exit conditions fire on the same tick, the highest priority wins:
**HALT > HardStop > Chandelier > EOD > Signal**

- HALT flatten: regime >= Flatten, use IOC (Immediate or Cancel) orders
- Hard stop: fixed stop hit
- Chandelier: trailing stop breached
- EOD: end-of-day flatten at 16:25 London (`eod_flatten_secs = 59100`)
- Signal reversal: Python Brain generates opposite signal

### 10.6 Dust Threshold

Positions below 500 GBP (`dust_threshold_gbp`) are market-sold immediately rather than trailing.

---

## 11. Risk Architecture

### 11.1 The 27-Check Risk Arbiter

The `RiskArbiter` (`risk_arbiter.rs`) is a synchronous, deterministic gate that evaluates every order intent in under 1ms. All checks are evaluated in strict order. The first failure stops evaluation and returns a rejection with a specific `VetoReason`.

| Check | Name | Condition | Action |
|-------|------|-----------|--------|
| 1 | ISA Short Sell Block | Direction == Short | HALT + REJECT |
| 2 | Inverse Mutual Exclusion (H32) | Holding inverse of same underlying | REJECT |
| 5 | Regime Gate | Regime >= FLATTEN | REJECT |
| 6 | Max Positions (H34) | filled + pending >= 6 | REJECT |
| 7 | Data Staleness | tick age > 120s | HALT + REJECT |
| 8 | Broker Connected | broker disconnected | HALT + REJECT |
| 9 | WAL Available | WAL writer unavailable | HALT + REJECT |
| 10 | Confidence Floor | confidence < 65.0 | REJECT |
| 11 | Entry Cutoff | time >= 20:55 London | REJECT |
| 12 | (Removed) | Was auction period block, removed for global trading | -- |
| 13 | Spread Veto (H36) | spread > 0.5% of bid | REJECT |
| 14 | Cash Buffer (H31) | cash buffer < 10% of equity | REJECT |
| 15 | Portfolio Heat | heat >= 15% of equity | REJECT |
| 16 | Sector Heat (H30) | sector heat >= 33% | REJECT |
| 17 | ISA Annual Limit | year invested >= 20,000 GBP | REJECT |
| 18 | Daily Drawdown (H29) | drawdown > 2% from HWM | FLATTEN + REJECT |
| 19 | Velocity (H37) | >= 5 intents in 1 second | REJECT |
| 20 | Macro Escalation | VIX/DXY/credit/F&G thresholds | REJECT |
| 21 | Consecutive Loss (H38) | >= 3 stop losses today | HALT + REJECT |
| 22 | Duplicate Position | max 1-3 based on IC and trade count | REJECT |
| 23 | Ticker Halted | reverse split, synthetic halt | REJECT |
| 24 | CVaR Heat | portfolio CVaR > 22.5% (1.5x heat limit) | REJECT |
| 25 | GARCH Vol | sigma > 0.80 * sqrt(leverage_factor) | REJECT |
| 26 | Scanner Score | score > 0 but < 30 | REJECT |
| 27 | Kelly Floor | kelly > 0 but < 0.5% | REJECT |

### 11.2 Four-State Regime Hierarchy

`HALT > FLATTEN > REDUCE > NORMAL`

- **NORMAL**: All systems go. Full position sizes.
- **REDUCE**: Reduced sizing (50% default, Ouroboros-calibrated). Auto-clears after 5 minutes of nominal conditions.
- **FLATTEN**: Close all positions. No new entries. Auto-clears after all positions closed + clean reconciliation.
- **HALT**: Emergency stop. No entries, no exits except emergency liquidation. Requires `manual_clear_halt()` unless caused by watchdog (auto-clears on data resumption).

Regime transitions are one-directional during escalation: `escalate()` only moves to a higher (more restrictive) state. De-escalation requires explicit clearing methods.

### 11.3 Macro Regime Awareness

The `CrossAssetMacro` module (`cross_asset_macro.rs`) monitors VIX, DXY, credit spreads, and Fear & Greed index. The `EvalContext` carries a `MacroIndicator` snapshot with staleness tracking (`macro_stale_threshold_ns = 300s`). CHECK 20 evaluates whether macro conditions warrant regime escalation.

### 11.4 Liquidation Defense (P16)

Three layers:
1. **ISA allowance gate**: Block entries if ISA allowance < 3% of equity
2. **Daily drawdown flatten**: DD > 2% -> escalate to FLATTEN
3. **Consecutive loss halt**: 3 stop losses -> escalate to HALT (Blood Oath H12). Sets `halt_from_watchdog = false` to prevent auto-recovery.

---

## 12. Learning System / Adaptive Layer

### 12.1 Ouroboros Nightly (v6.0)

Runs at 23:50 ET (04:50 UTC) every weekday via Supercronic. Located in `python_brain/ouroboros/nightly_v6.py`. Performs:

1. **Trade analysis**: Parses the day's WAL events, extracts fills, PnL, timing
2. **Regime accuracy check**: Were regime predictions correct?
3. **Parameter optimisation with guardrails**: Updates Kelly fractions (0.15-0.30 bounds), Chandelier ATR multipliers (1.5-4.0 bounds). No parameter drifts >15% per night.
4. **Alpha decay detection**: Compares 7-day vs 30-day rolling win rates. If 7d << 30d, signals alpha erosion.
5. **Daily report generation**: Written to `data/ouroboros_reports/`
6. **Pre-market battle plan**: Recommendations for tomorrow's session

**Quarantine rules**: Never writes to live WAL. Never influences live decisions in-session. Reads only finished day's journal.

### 12.2 Backfill Simulator

Runs at 07:00 UTC daily (`python_brain/ouroboros/backfill_simulator.py`). Pre-market learning using historical data to validate parameter changes before the market opens.

### 12.3 Thompson Sampling (P5-C)

The `LogThompsonSampler` (`log_thompson_sampler.rs`) implements multi-armed bandit allocation across tickers. On each trade close, it observes the return percentage. Over time, it learns which tickers deliver the best risk-adjusted returns and allocates more capital to them. This runs in the Rust engine, not in Python.

### 12.4 Predictive Scoring (P12)

The `PredictiveScorer` (`predictive_scoring.rs`) tracks per-ticker IC (Information Coefficient) and trade count. After 5 consecutive losses on a ticker, it locks the ticker from further entries. This prevents the system from repeatedly losing on a ticker that has stopped working.

### 12.5 15-Minute Watchlist Rotation

The ticker selector runs every 15 minutes during ACTIVE hours. It scores all tickers in the universe by momentum, volatility, liquidity, and sector balance. Only tickers from exchanges currently open are included (market-hours-aware filtering). The top 100 are written to `active_watchlist.json`. The engine detects the file change and rotates subscriptions within the 100-line IBKR budget.

---

## 13. Infrastructure and Runtime

### 13.1 EC2 Instance

- **Instance**: c7i-flex.large (us-east-1c)
- **Specs**: 2 vCPUs, 4 GB RAM, x86_64
- **Elastic IP**: 3.230.44.22 (permanent, free while instance running)
- **SSH**: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`

### 13.2 Docker Composition

Three containers on the `nzt48-signals_default` network:

| Container | Image | Purpose | Port |
|-----------|-------|---------|------|
| aegis-v2 | Custom Rust + Python | Trading engine | (none exposed) |
| aegis-redis | redis:7 | State store | 6379 (internal only) |
| ib-gateway | gnzsnz/ib-gateway | IB Gateway + IBC | 4004 (paper API) |

The aegis-v2 container uses `client_id=101` (V1 uses 100) to avoid conflicts. IB Gateway is shared with V1 on the same Docker network. The paper API port is **4004**, not 4002. This is a gotcha that has caused connection failures before.

### 13.3 Crontab (Supercronic)

The `crontab` file defines all scheduled tasks:

| Time (UTC) | Task | Frequency |
|------------|------|-----------|
| 04:50 | Ouroboros v6.0 Nightly Learning | Mon-Fri |
| 06:00 | ISA Universe Refresh | Mon-Fri |
| 06:30 | Smart Ticker Selector (daily full re-score) | Mon-Fri |
| 07:00 | Backfill Simulator | Mon-Fri |
| */15 23:00-20:45 | 15-minute ticker rotation | Mon-Fri (Sun night for Mon Asian) |
| 00:55, 07:55, 14:25 | Session Briefing PDFs + Telegram | Mon-Fri |
| */4h | Telegram heartbeat | Mon-Fri |
| */6h | FX Rate Refresh (writes fx_rates.toml) | Mon-Fri |
| 22:00 Sun | IBKR Full Universe Scanner | Weekly |
| 18:00 | Legacy Ouroboros (WAL analysis) | Mon-Fri |

### 13.4 IB Gateway

IB Gateway runs via the gnzsnz/ib-gateway Docker image with IBC (IB Controller) for automated daily restarts and re-authentication. Weekly 2FA re-auth required Monday morning. The gateway listens on port 4004 for paper trading API connections.

### 13.5 Redis

Internal Redis instance with password `nzt48redis`. Not exposed on host port. Used for state persistence: circuit breaker state, consecutive loss counters, regime state. Connected via the Docker internal network.

---

## 14. State, Storage, Sources of Truth

### 14.1 Write-Ahead Log (WAL)

The WAL (`wal_writer.rs`) is the ultimate source of truth (H26). Append-only NDJSON file, one per trading day. Events include:
- `RoutedOrder`: signal approved, order submitted
- `FillEvent`: order filled by broker
- `ExitEvent`: position closed
- `RegimeChange`: risk regime transition
- `Reconciliation`: 5-minute reconciliation results
- `StateHash`: hourly FNV-1a hash of engine state (H85)

The WAL writer checks disk space before each write. If free space drops below 5%, it returns `DiskSpaceLow` and the engine enters HALT (CHECK 9: WAL unavailable).

Dead letter directory: events that cannot be written to the main WAL are placed in `dead_letter/`.

### 14.2 WAL Replay

On startup, the engine replays the current day's WAL to reconstruct state. This provides crash recovery: if the engine restarts mid-day, it replays all events and resumes from the last known state.

### 14.3 WAL Compression (P18)

The `WalCompressor` rotates WAL files after 1 million events, preventing unbounded growth.

### 14.4 State Checkpoints (P19)

The `CheckpointManager` writes hourly state snapshots using FNV-1a hashing. These serve as verification points: if the replayed state hash does not match the checkpoint hash, a reconciliation mismatch is flagged.

### 14.5 Portfolio State

`PortfolioState` (`portfolio.rs`) is the in-memory source of truth for:
- Position map: ticker_id -> PositionState (avg_entry, qty, stop_price, etc.)
- Cash balance
- Equity (high-water mark, daily PnL)
- ISA year-to-date invested amount
- Consecutive stop loss counter
- Sector allocation map
- Inverse pair map
- VWAP cost-basis tracker per ticker (SC-10)

### 14.6 Reconciliation

Every 5 minutes (`RECONCILE_INTERVAL_NS = 300_000_000_000`), the engine runs `reconciler.rs` to compare local portfolio state against broker-reported positions and orders. Any mismatch triggers CRITICAL logging and FLATTEN. Orphaned orders (broker orders not tracked locally) are flagged. The `ReconcileAuditLog` enforces a 24h lock period after mismatch detection, requiring `manual_clear_halt()` to resume.

---

## 15. Monitoring, Telemetry, Explainability

### 15.1 Telemetry Stack

The `Telemetry` struct (`telemetry.rs`) contains lock-free atomic counters:
- `signals_generated`: total signals from Python Brain
- `signals_approved`: signals that passed risk arbiter
- `orders_submitted`: orders sent to broker
- Veto counters: per-reason rejection tracking via `record_veto()`

All counters use `AtomicU64` with `Ordering::Relaxed` for hot-path safety.

### 15.2 Latency Profiling (P22)

The `LatencyProfiler` (`latency_profiler.rs`) tracks 6 pipeline stages with nanosecond precision:
- TickToTrade (T2T): total latency from tick arrival to order submission
- Per-stage: signal generation, risk evaluation, sizing, order submission, broker acknowledgement

A `LatencyRing` ring buffer stores samples for percentile computation (P50/P95/P99).

### 15.3 Heartbeat

Every 5 minutes, the engine emits a status heartbeat to stderr. Telegram heartbeat runs every 4 hours via cron for remote monitoring.

### 15.4 Session Briefing PDFs

Three PDFs generated daily:
- Asian session briefing (00:55 UTC)
- European session briefing (07:55 UTC)
- American session briefing (14:25 UTC)

Each includes watchlist rankings, overnight gaps, regime state, and recommendations. Sent via Telegram.

### 15.5 Structured Logging

All engine logging goes to stderr (captured by Docker). Key log prefixes:
- `SIGNAL_ARRIVED`: Python Brain generated a signal
- `SIZING`: Risk arbiter sizing computation (every candidate that passes 27 checks)
- `ORDER_REJECTED`: Broker rejected the order (with full context)
- `T2T`: Tick-to-trade latency measurement
- `REGIME_GATE`: Jump-diffusion blocking
- `ISA_GATE`: ISA compliance rejection

---

## 16. Current Known Flaws and Hidden Weaknesses

### 16.1 Critical Flaws (Blocking Compounding)

**F-01: FX Conversion Missing for Asian Currencies**
Currency enum lacks JPY, KRW, HKD, AUD. Portfolio equity is meaningless when Asian positions are held. All risk checks that reference equity become unreliable. This is a P0 blocker for global trading.

**F-02: IBKR Symbol Resolution Failures**
Many Asian tickers return error code 200. The combination of symbol derivation, exchange mapping, and currency specification is incorrect for some tickers. Each ticker needs individual validation against IBKR's contract database.

**F-03: No Filled Trade Confirmation**
Despite the order ID fix and silent failure fix, zero trades have actually filled. The execution layer is producing signals, sizing them, and submitting orders, but no fills are confirmed. This could be a combination of F-02 (wrong symbols), minimum lot sizes, or market data subscription issues.

**F-04: Equity Denominator Bug**
`portfolio.equity` is the denominator for multiple risk checks (drawdown, heat, sizing). If it is corrupted by FX mixing (F-01), every downstream calculation is wrong. A position worth 1,500,000 JPY (~7,500 GBP) would appear as 1,500,000 GBP, making the portfolio appear to have 150x its actual value. All risk limits would appear satisfied when they should be breached.

### 16.2 Serious Flaws (Will Cause Losses)

**F-05: Tick Age Hardcoded to 1**
In the `EvalContext` construction (engine.rs line 1143), `last_tick_age_secs` is hardcoded to `1` instead of being computed from actual tick timestamps. This means CHECK 7 (data staleness) will never fire in practice, even if ticks are minutes old.

**F-06: Scanner Score and Kelly Floor Defaults**
In the `EvalContext`, scanner_score defaults to 50.0 and kelly_fraction_raw comes from the signal, but the default `EvalContext` has `scanner_score: 50.0` which might not reflect reality. More critically, garch_sigma defaults to 0.30 in the EvalContext default but is overridden per-tick -- verify that the override actually works for all tickers.

**F-07: FX Rates Are Hardcoded Statics**
The crontab FX rate refresh at line 45 writes a static Python dict to `fx_rates.toml`: `rates={'EURGBP':0.86,'CHFGBP':0.89,...}`. These are not live rates. They are the same rates written every 6 hours. The engine would need to actually fetch live FX rates from IBKR or a data provider. Currently, FX rates are identical to the defaults compiled into `currency.rs`.

**F-08: ISA Gate Hardcoded to XLON**
In engine.rs line 1187: `self.isa_gate.check("XLON", trade_value_gbp)`. This checks ISA compliance against the XLON exchange profile regardless of which exchange the order is actually on. For Asian tickers on TSE/HKEX/KRX, this may be incorrect.

### 16.3 Moderate Flaws (Inefficiency)

**F-09: VetoReason Sector Name Hardcoded**
In risk_arbiter.rs line 211, sector is hardcoded as `"sector".into()` instead of the actual sector name. This means the veto log does not identify which sector is over-concentrated.

**F-10: Velocity Check Backwards**
In risk_arbiter.rs lines 230-237, the velocity check filters by `*t == intent_ticker` on the first tuple element, but the velocity log is `Vec<(TickerId, u64)>` where the second element is the timestamp. The filter counts recent approvals for this specific ticker, not total velocity -- this may or may not match the intent of H37.

**F-11: Entry Cutoff is London-Only**
The 20:55 London cutoff blocks entries globally. For US tickers that trade until 21:00 UTC (16:00 ET), this leaves only 5 minutes. This is by design (Dark starts at 21:00) but may miss US close opportunities.

### 16.4 Architectural Weaknesses

**F-12: Python Brain as Single Point of Failure**
If the Python subprocess dies, crashes, or hangs, no signals are generated and no trades happen. The `PythonSubprocessManager` handles respawning, but there is a gap between crash and respawn where no signals flow.

**F-13: 100 IBKR Subscription Limit Constrains Universe**
With 12 ISA core + 88 rotation slots, the engine can only monitor 100 tickers at a time out of 36,000+. The 15-minute rotation helps but means opportunities on unmonitored tickers are missed entirely.

**F-14: No Backtesting Framework**
The engine has a `Crucible` mode and a `PaperBroker` for testing, but there is no systematic backtesting framework to validate the VanguardSniper strategy against historical data. All validation relies on forward paper trading.

---

## 17. Simplification and Streamlining Plan

The engine has 77 Rust source files and significant complexity. Much of it is infrastructure that has never been activated in production. The following simplifications would reduce maintenance burden without losing any capability that is currently operational.

### 17.1 Remove Unused Complexity

| Component | Status | Recommendation |
|-----------|--------|----------------|
| `quantum_apex.rs` | Never activated | Delete or move to attic branch |
| `neural_hawkes.rs` | Never activated | Delete or move to attic branch |
| `dqn_signal_weighting.rs` | Never activated | Delete or move to attic branch |
| Legacy session modes (ModeA/B/B+/C/Auction) | Mapped to Active but still in enum | Remove enum variants after confirming no references |
| `TradingMode` (5-mode clock) | Partially superseded by `SessionMode` | Consolidate to one mode system |
| ApexScout strategy | Separate from VanguardSniper | Evaluate if it adds value or just adds code |

### 17.2 Consolidate Configuration

The engine has configuration in multiple places: `config.rs` defaults, `config.toml`, `EngineConfig`, and Ouroboros artifacts. A single hierarchical config system (TOML -> env vars -> defaults) would be cleaner.

### 17.3 Reduce Engine Struct Size

The `Engine` struct has 60+ fields. Many are optional subsystems (Asian session, European session, cross-timezone, split handler, etc.). These could be grouped into feature structs or made optional with runtime feature flags.

---

## 18. Upgrade Blueprint

### 18.1 Immediate (This Week)

| ID | Upgrade | Effort | Impact |
|----|---------|--------|--------|
| U-01 | Add JPY, KRW, HKD, AUD to Currency enum | 2h | Unblocks Asian trading |
| U-02 | Compute real tick_age_secs instead of hardcoded 1 | 1h | Enables stale data detection |
| U-03 | Validate all ISA Core 12 contract mappings against IBKR | 2h | Ensures orders can fill |
| U-04 | Live FX rate fetch from IBKR or free API | 3h | Accurate portfolio valuation |
| U-05 | ISA gate: pass actual exchange, not hardcoded XLON | 1h | Correct compliance checks |
| U-06 | Fix sector name in VetoReason (F-09) | 15min | Better diagnostics |

### 18.2 Near-Term (This Month)

| ID | Upgrade | Effort | Impact |
|----|---------|--------|--------|
| U-07 | Build per-ticker IBKR contract validation tool | 8h | Automated symbol checking |
| U-08 | Add Telegram alerts for ORDER_REJECTED events | 4h | Real-time operator awareness |
| U-09 | Implement minimum lot size awareness per exchange | 4h | Prevents invalid order sizes |
| U-10 | Add fill rate tracking (signals -> fills) | 2h | Funnel visibility |
| U-11 | Reconciliation improvements: track reason for each mismatch | 4h | Faster root cause analysis |

### 18.3 Medium-Term (Next Quarter)

| ID | Upgrade | Effort | Impact |
|----|---------|--------|--------|
| U-12 | Backtesting framework using WAL replay on historical data | 40h | Strategy validation |
| U-13 | Multi-strategy support (rotate strategies, not just tickers) | 20h | Diversified alpha sources |
| U-14 | Position-level PnL tracking with FX-adjusted mark-to-market | 16h | Accurate performance reporting |
| U-15 | Broker failover: reconnection with state recovery | 12h | Resilience |
| U-16 | Strategy heat map: which tickers/times/regimes produce alpha | 20h | Learning acceleration |

### 18.4 Luxury (If Compounding Works)

See Appendix G.

---

## 19. Daily Compounding Blueprint

### 19.1 What Daily Compounding Requires

For the engine to compound at 0.3-0.5% daily:

1. **Trades must fill**. This is currently blocked. The execution layer must be fixed (F-01, F-02, F-03).
2. **Position sizing must be meaningful**. At 10,000 GBP equity with 0.25 fractional Kelly and 3x vol decay, positions are approximately 200-500 GBP. A 2% gain on 300 GBP is 6 GBP -- 0.06% of equity. Need either larger positions or more frequent trades.
3. **Win rate must exceed 40%**. With the Chandelier 5-rung profit ladder, losing trades are capped at approximately 1x ATR from entry. Winning trades can run indefinitely. A 40% win rate with 2:1 reward-to-risk produces positive expectancy.
4. **Drawdown protection must work**. The 2% daily drawdown -> FLATTEN mechanism prevents catastrophic days. But if FX mixing corrupts equity, this mechanism is unreliable (F-04).
5. **Compounding effect requires reinvestment**. Each day's profits increase the equity base for tomorrow's positions. The Kelly sizing automatically scales: if equity grows to 15,000 GBP, positions grow proportionally.

### 19.2 Compounding Math

| Daily Return | 30 Days | 90 Days | 180 Days | 252 Days |
|-------------|---------|---------|----------|----------|
| 0.1% | +3.0% | +9.4% | +19.7% | +28.6% |
| 0.3% | +9.4% | +31.0% | +71.6% | +113.2% |
| 0.5% | +16.1% | +57.0% | +146.1% | +251.7% |
| 1.0% | +34.8% | +145.1% | +500.8% | +1,124.2% |

At 0.3% daily, 10,000 GBP becomes 21,320 GBP in one year. At 0.5%, it becomes 35,170 GBP.

### 19.3 The 100-Trade Validation Gate

Before declaring the strategy viable, the engine must complete 100 paper trades. The gate criteria:
- Win Rate >= 40%
- Rung Advancement >= 60% (percentage of winning trades reaching Rung 2+)
- Profit Factor >= 1.5x (gross profit / gross loss)
- Maximum consecutive losses < 3

The Kelly ramp (`validated_trades / 250, clamped 0.1-1.0`) ensures the engine starts with 10% of full Kelly and gradually increases as the track record builds. At 100 trades, it is at 40% Kelly. At 250 trades, it reaches full Kelly.

---

## 20. External Review Integration

### 20.1 MERGED_MASTER_PLAN_v1.0 (2026-03-13)

A 5-persona audit (CIO, Trader, Risk Manager, Architect, ML Ops) identified:
- 8 timing defects (T-01 to T-08): blackouts, polling, indicator gates, ADX/RVOL thresholds, multi-signal
- 4 silent killers (SK-01 to SK-04): equity denominator bug, zombie halt, confidence alignment, dual throttles
- Phase Q1: fix timing + silent killers (~63h), then 100-Trade validation gate
- Phase Q2: selective KRONOS integration (3-4 items, ~40h)
- Go-live criteria: paper trading 1 week, 4 gates ALL pass (WR >= 60%, Rung >= 60%, PF >= 1.5x, losses < 3)

### 20.2 AEGIS Master Plan v15/v16

2,414 lines documenting 98 stop-ship items (40 P0 + 58 P1). Zero implemented at time of plan freeze. The plan was frozen at v16.0 with the directive "next action = CODE."

### 20.3 Approved KRONOS Upgrades

From the MERGED_MASTER_PLAN review:
- **Approved**: Confidence decay (quick win), VPIN (Q2+), Regime gates (Q2+)
- **Rejected**: Dynamic Kelly (conflict with existing 12-factor), Ghost stops (no edge), Signal decay hourly (too noisy), Order routing (already automatic via SMART), Chandelier+Ghost (redundant), Regime prediction (marginal value)

---

## 21. Final Truth Section

### What Is Strong

1. **Risk architecture**: The 27-check arbiter is comprehensive, deterministic, and fast (< 1ms). The 4-state regime hierarchy with fail-closed semantics is well-designed. ISA compliance is enforced at multiple levels.

2. **Chandelier exit strategy**: The 5-rung profit ladder with shadow stops is mathematically sound. The breakeven rung (Rung 2) eliminates losing trades after a 2% gain. The tightening trail lets winners run.

3. **Signal generation separation**: Keeping Python Brain as a pure-function subprocess is clean. The JSON bridge protocol is simple and debuggable. VanguardSniper is a reasonable momentum strategy with ADX, EMA, and volume confirmation.

4. **Infrastructure**: Docker on EC2 with WAL, Redis, and Supercronic is a solid production setup. The Ouroboros nightly learning loop with guardrails and drift limits is well-architected.

5. **Defensive coding**: Zero-division guards (H61), WAL integrity checks, disk space monitoring, panic guards, circuit breakers, reconciliation every 5 minutes. The Rust code enforces many invariants at compile time.

### What Is Weak

1. **No trades have ever filled**. The entire pipeline, from signal to order, works on paper. But the last mile -- actual order filling at the broker -- is broken. This is the definition of a system that looks good but does nothing.

2. **FX handling is incomplete**. The global trading ambition (6 markets, 36K tickers) is built on a currency system that only handles European currencies + USD. Asian trading is fundamentally broken at the valuation layer.

3. **Massive code surface with zero validated revenue**. 27,608 lines of Rust + 10,443 lines of Python have been written to support a system that has never completed a single trade. The code-to-validated-outcome ratio is currently infinity.

4. **Over-engineering relative to proven capability**. The system has Thompson sampling, Hayashi-Yoshida covariance, Student-t Kalman filters, GARCH(1,1) inference, EVT tail risk, neural Hawkes processes, and DQN signal weighting -- none of which matter until the system can fill a single order. Complexity is the enemy of reliability.

5. **Testing gap at the integration boundary**. Unit tests exist for individual modules (risk arbiter, exit engine, universe, etc.). But there is no integration test that submits an order through the real IBKR paper API and verifies a fill. The boundary between the engine and the real world is untested.

### What Is Blocking Daily Compounding

In priority order:

1. **Fix IBKR symbol resolution** for at least the ISA Core 12. Verify each contract mapping produces a valid IBKR contract that can be traded. This is a 2-hour task that unblocks everything.

2. **Add missing Asian currencies** to the Currency enum. Without this, any non-GBP/EUR/USD position corrupts portfolio equity and breaks all risk calculations.

3. **Verify a single trade fills end-to-end**. Submit a manually constructed order for QQQ3.L through the engine and confirm the fill event comes back. This proves the pipeline works.

4. **Run 100 paper trades** through the validation gate. Measure WR, rung advancement, profit factor, and consecutive losses.

5. **Only then** consider the MERGED_MASTER_PLAN's 98 stop-ship items, KRONOS upgrades, or any luxury improvements.

The honest truth: this system is architecturally sophisticated, risk-aware, and well-instrumented. It is also non-functional at the most basic level. The gap between design and reality is not in the strategy or the risk management -- it is in the last 50 lines of code where orders meet the broker. Fix that, and the system has a genuine chance of compounding. Leave it unfixed, and the 27,608 lines of Rust are an expensive journal entry.

---

## Appendix A: Flaw Appendix

| ID | Category | Description | Severity | Status |
|----|----------|-------------|----------|--------|
| F-01 | Currency | JPY/KRW/HKD/AUD missing from Currency enum | P0 | Open |
| F-02 | Execution | IBKR symbol resolution fails for many Asian tickers | P0 | Open |
| F-03 | Execution | Zero trades have filled successfully | P0 | Open |
| F-04 | Portfolio | Equity corrupted by mixed-currency addition | P0 | Open |
| F-05 | Risk | tick_age_secs hardcoded to 1, stale data check disabled | P1 | Open |
| F-06 | Risk | EvalContext defaults may mask real values | P2 | Open |
| F-07 | FX | FX rates are static hardcoded values, not live | P1 | Open |
| F-08 | Compliance | ISA gate hardcoded to XLON for all exchanges | P1 | Open |
| F-09 | Diagnostics | Sector name hardcoded to "sector" in VetoReason | P3 | Open |
| F-10 | Risk | Velocity check semantics may not match H37 intent | P3 | Open |
| F-11 | Timing | Entry cutoff is London-only, constrains US session | P3 | By design |
| F-12 | Resilience | Python Brain is single point of failure | P2 | Mitigated (respawn) |
| F-13 | Scalability | 100 IBKR subscription limit constrains universe | P3 | By design |
| F-14 | Validation | No backtesting framework | P2 | Open |

---

## Appendix B: Hidden Flaw Appendix

These are flaws that do not produce errors or warnings. They silently degrade performance or correctness.

| ID | Description | How It Hides |
|----|-------------|-------------|
| HF-01 | `unwrap_or(1.0)` in FxRateTable treats unknown currencies as 1:1 with GBP | No error, no warning. 1 JPY silently becomes 1 GBP |
| HF-02 | `last_tick_age_secs: 1` in EvalContext means stale data never triggers HALT | CHECK 7 always passes. No veto logged |
| HF-03 | Order ID bug (fixed) caused silent DuplicateOrderId rejections | No log output. Orders disappeared |
| HF-04 | submit_order error path (fixed) returned silently | No log output. Broker rejections invisible |
| HF-05 | FX rate cron writes same static rates every 6 hours | Appears to be "refreshing" but values never change |
| HF-06 | Python Brain crash between respawns: no signals flow, no error logged by engine | Engine sees empty signal, returns quietly |
| HF-07 | garch_sigma default 0.30 used when GARCH registry has no data for ticker | CHECK 25 uses default, not real volatility |
| HF-08 | `..EvalContext::default()` spreads unset fields with potentially wrong defaults | Macro indicator, volatilities map empty |
| HF-09 | kelly_ramp_trades starts at 0, so Kelly ramp = max(0/250, 0.1) = 0.1 permanently until updated | 10% Kelly forever if config never updated |
| HF-10 | ISA annual limit only tracks buy-side cost, not accounting for returned cash from sells | Over-counting: ISA budget depletes faster than reality |

---

## Appendix C: Timing/Session Appendix

### Session Transitions

```
21:00 London  ->  DARK begins (or CARRY if positions held)
                  Entries frozen
                  Ouroboros nightly runs at 23:50 ET (04:50 UTC)
                  Universe refresh at 06:00 UTC
                  Ticker selector at 06:30 UTC

23:00 London  ->  ACTIVE begins
                  Asia-Pacific markets opening (NZX already open)
                  15-minute rotation starts
                  Python Brain accepts signals

03:00-06:00   ->  Peak Asian session (TSE, HKEX, KRX all open)

08:00 London  ->  LSE opens, European session begins
                  ISA Core 12 become primary focus

14:30 London  ->  US markets open, overlap session
                  SMART routing for NYSE/NASDAQ

20:55 London  ->  Entry cutoff (CHECK 11)
                  No new positions after this time

21:00 London  ->  DARK begins again
```

### Entry Cutoff Timeline

| Time (London) | Event |
|---------------|-------|
| 20:55 | Hard entry cutoff in risk arbiter |
| 21:00 | Dark mode begins, entries frozen by session manager |
| 21:00 | US markets close |
| 21:00-23:00 | Maintenance window, Ouroboros runs |
| 23:00 | ACTIVE resumes for next day's Asian session |

---

## Appendix D: State/Storage Appendix

### Files Written by the Engine

| Path | Format | Purpose | Writer |
|------|--------|---------|--------|
| `events/YYYY-MM-DD.ndjson` | NDJSON | Write-ahead log (source of truth) | WalWriter |
| `dead_letter/*.ndjson` | NDJSON | Failed WAL writes | WalWriter |

### Files Written by Ouroboros

| Path | Format | Purpose | Writer |
|------|--------|---------|--------|
| `config/active_watchlist.json` | JSON | Current 100-ticker watchlist | ticker_selector.py |
| `config/isa_universe_master.json` | JSON | Full universe metadata | universe_refresh.py |
| `config/fx_rates.toml` | TOML | FX rates (static) | crontab inline Python |
| `config/dynamic_weights.toml` | TOML | Ouroboros-learned parameters | nightly_v6.py |
| `data/ouroboros_reports/*.json` | JSON | Daily reports | nightly_v6.py |
| `data/ouroboros_recommendations.json` | JSON | Parameter recommendations | nightly_v6.py |
| `data/universe_cache/price_cache.json` | JSON | Weekly price cache (Tier 3) | ticker_selector.py |

### Redis Keys

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `aegis:regime` | Current risk regime | None |
| `aegis:consecutive_losses` | Daily loss counter | Midnight reset |
| `aegis:circuit_breaker:*` | Circuit breaker state | Window-based |

### Engine State (In-Memory)

| Field | Type | Persistence |
|-------|------|-------------|
| `portfolio` | PortfolioState | WAL replay |
| `arbiter.regime` | RiskRegime | WAL replay + Redis |
| `positions` | HashMap<TickerId, PositionState> | WAL replay |
| `order_counter` | u64 | WAL replay (count RoutedOrder events) |
| `bar_history` | HashMap<TickerId, BarHistory> | Lost on restart (rebuilt from live data) |
| `kelly_calculator` | KellyCalculator | Config load |
| `thompson_sampler` | LogThompsonSampler | Lost on restart |

---

## Appendix E: Highest Priority Fixes

Ordered by impact on achieving daily compounding:

| Priority | Fix | Estimated Effort | Blocks |
|----------|-----|------------------|--------|
| 1 | Validate ISA Core 12 IBKR contracts (QQQ3.L, QQQS.L, etc.) | 2h | All trading |
| 2 | Submit and verify one manual trade fills through engine | 1h | Confidence in pipeline |
| 3 | Add JPY, KRW, HKD, AUD to Currency enum + FxRateTable | 2h | Asian trading |
| 4 | Compute real tick_age_secs from actual timestamps | 1h | Stale data protection |
| 5 | Implement live FX rate fetching (not static hardcoded) | 3h | Accurate valuation |
| 6 | Pass actual exchange to ISA gate, not hardcoded XLON | 1h | Compliance correctness |
| 7 | Build per-ticker IBKR contract validation script | 4h | Scaling to 100 tickers |
| 8 | Add Telegram alerts for ORDER_REJECTED | 2h | Operator awareness |

---

## Appendix F: What Must Be True to Start Daily Compounding

Every single one of these conditions must be true simultaneously:

1. At least one trade has filled end-to-end (signal -> risk -> size -> order -> fill -> position)
2. ISA Core 12 contract mappings are verified against IBKR paper API
3. Currency enum includes all currencies for actively traded markets
4. FX conversion produces correct GBP values for all positions
5. Portfolio equity is accurate (not corrupted by currency mixing)
6. Daily drawdown calculation uses correct equity (depends on #5)
7. Risk arbiter CHECK 7 uses real tick age (not hardcoded 1)
8. Ouroboros nightly has at least 7 days of trade data to learn from
9. Chandelier stops are computed with valid ATR (requires bar history warmup)
10. 100-trade validation gate passes: WR >= 40%, Rung >= 60%, PF >= 1.5x, consecutive losses < 3
11. Reconciliation runs clean for 24 consecutive hours
12. No unresolved orphan orders in the broker

---

## Appendix G: Luxury Upgrades

These are only relevant after daily compounding is proven and the 100-trade gate passes.

| ID | Upgrade | Effort | Value |
|----|---------|--------|-------|
| L-01 | Rust FFI for VanguardSniper (eliminate Python Bridge latency) | 80h | Microsecond signal generation |
| L-02 | DPDK network bypass for IBKR data feed | 120h | Sub-millisecond tick delivery |
| L-03 | DQN signal weighting (neural net strategy selector) | 200h | Adaptive strategy allocation |
| L-04 | Neural Hawkes process for event timing | 160h | Predict next move timing |
| L-05 | Quantum Apex regime predictor | 100h | Forward-looking regime detection |
| L-06 | Multi-account ISA management | 40h | Scale to family accounts |
| L-07 | Options overlay for income generation | 80h | Yield enhancement |
| L-08 | VPIN (Volume-Synchronized Probability of Informed Trading) | 20h | Toxicity detection |
| L-09 | Full Rust rewrite of Python Brain | 200h | Zero-copy signal path |
| L-10 | Dark pool routing via IBKR | 16h | Reduced market impact |

Total luxury budget: approximately 1,016 hours. None of this matters until Appendix F conditions are all met.

---

## Appendix H: Simplification Plan

### Phase 1: Delete Dead Code (4 hours)

| File | LOC | Reason |
|------|-----|--------|
| `quantum_apex.rs` | ~200 | Never activated, no callers |
| `neural_hawkes.rs` | ~200 | Never activated, no callers |
| `dqn_signal_weighting.rs` | ~200 | Never activated, no callers |

### Phase 2: Consolidate Session Modes (2 hours)

Remove legacy `ModeA`, `ModeB`, `ModeBPlus`, `ModeC`, `Auction` from `SessionMode` enum. Replace all references with `Active`. The `TradingMode` enum in `clock.rs` should be evaluated for removal or merger with `SessionMode`.

### Phase 3: Flatten Engine Struct (8 hours)

Group the 60+ fields in `Engine<B>` into logical sub-structs:
- `EngineCore` { broker, portfolio, arbiter, exit_engine, wal, clock, config }
- `EngineScanners` { hot_scanner, rotation_scanner, subscription_manager }
- `EngineRisk` { liquidation_defense, broker_health, circuit_breaker, watchdog }
- `EngineAnalytics` { garch_registry, evt_registry, multiframe_vol, hy_engine }
- `EngineAdaptive` { thompson_sampler, predictive_scorer, regime_detector }

### Phase 4: Unify Configuration (4 hours)

Single source: `config/config.toml` -> parsed by `config_loader.rs`. Defaults in `config.rs` become the fallback chain. Ouroboros writes parameter overrides to a separate `config/dynamic_weights.toml` that is layered on top.

### Phase 5: Remove Over-Instrumented Defaults (2 hours)

The `EvalContext::default()` spreads wrong values into real evaluations via `..EvalContext::default()`. Replace with explicit field construction that forces the caller to provide every value, or panic on unset fields.

---

## Appendix I: Runtime Update Log (2026-03-17)

### Session Summary

Major engineering sprint completed. The engine now starts, runs the full main loop, and generates simulated trades autonomously even when IB Gateway is unavailable. Key changes:

### Fixes Deployed

| Fix | Status | Details |
|-----|--------|---------|
| **Regime escalation in simulation mode** | FIXED | Regime was escalating to Flatten/Halt from stale VIX/macro/broker data. Added dual-point regime reset: inside `process_tick_with_signal()` (engine.rs:1646) and at end of main loop (main.rs:~700). WAL regime writes suppressed in simulation mode. |
| **Broker-less startup** | FIXED | Engine startup (8-step sequence) now skips broker-dependent steps 1/4/5 (connection check, reconciliation, orphan resolution) in simulation mode. Allows engine to run without IB Gateway. |
| **Broker reconnection** | FIXED | Max 10 connection attempts at startup (paper mode). After max retries, engine proceeds to main loop. Periodic 60s reconnection attempts in main loop auto-connect when IB Gateway becomes available. |
| **Half-Kelly sizing** | FIXED | For total_trades < 250, kelly_fraction is halved (0.5x). Reduces exposure during bootstrap when Kelly estimates are unreliable. |
| **Min position size** | FIXED | Minimum 5 GBP per trade enforced in position_sizer.rs. Prevents dust positions. |
| **WAL enrichment** | FIXED | `RoutedOrder` now includes `symbol`, `qty`, `currency` fields. `PositionClosed` includes `symbol`, `qty`. All with `#[serde(default)]` for backward compatibility with old WAL events. |
| **Daily sim report** | FIXED | Python report reads WAL ndjson directly (not docker logs). Date-filtered by `event_time_ns`. Uses enriched symbol/qty/currency fields. PyMuPDF PDF generation. Cron at 21:15 UTC Mon-Fri. |
| **Dockerfile build** | FIXED | Removed phantom `COPY src/ src/` (no top-level src/ exists). Fixed maturin wheel path from `rust_core/target/wheels/` to `target/wheels/`. |
| **Redis noeviction** | FIXED | Redis configured with `maxmemory-policy noeviction` and 256MB cap. Prevents silent key loss. |
| **Ouroboros crontab** | FIXED | Nightly learning at 23:50 ET, ticker selector every 15min during ACTIVE, daily sim report at 21:15 UTC. |

### Ouroboros Weight Verification (ALL 4 WIRED)

| Weight | Where Applied | Verified |
|--------|---------------|----------|
| `bayesian_win_rate` | nightly_v6.py → dynamic_weights.toml → ouroboros_loader.rs | YES |
| `chandelier_atr_mult` | engine.exit_engine.strategy_mut().set_trail_atr() | YES |
| `kelly_fractions` | engine.arbiter.kelly_fractions (per-ticker caps) | YES |
| `regime_scales` | engine.arbiter.regime_scales | YES |

### Kelly Calibration Analysis (Rank 2)

**Finding**: The `avg_win`/`avg_loss` hardcoded defaults (0.02/0.02) only affect kelly_12factor. During bootstrap (<50 trades), VanguardSniper's `preliminary_kelly = min(confidence/1000, 0.20)` dominates via the bootstrap floor in bridge.py. After half-Kelly reduction, effective kelly is ~0.035 for typical confidence=70 signals. This is reasonable and doesn't need immediate fixing.

**Decision**: Deferred until >50 trades accumulate. At that point, real avg_win/avg_loss from trade history will flow through kelly_12factor and produce calibrated sizing.

### Current Blocking Issue

**IB Gateway Authentication**: Both V1 and V2 IB Gateways show error: "The specified user has multiple Paper Trading users associated with it." **USER ACTION REQUIRED**: Configure paper trading-specific sub-account username (e.g., `DU1234567`) in `docker-compose.yml` TWS_USERID environment variable.

### Updated Flaw Status

| ID | Status Change |
|----|---------------|
| F-03 (Zero trades filled) | **Partially resolved** — Simulated trades fill internally. Real broker fills still blocked by IB Gateway auth. |
| F-12 (Python Brain SPOF) | **Mitigated** — Python subprocess manager with respawn logic, cooldown, fork bomb detection (RM-5). |

### Architecture Truth (as deployed 2026-03-17)

```
[EC2 c7i-flex.large, Elastic IP 3.230.44.22]
├── aegis-v2 container (Rust engine + Python brain + Supercronic + WAL watcher)
│   ├── aegis binary (Rust, ~27K LOC, main loop 60s polling)
│   ├── python_brain subprocess (VanguardSniper, kelly_12factor, bridge.py)
│   ├── supercronic (ouroboros nightly, ticker selector, daily sim report)
│   └── wal_watcher (Telegram notifications on WAL events)
├── aegis-ib-gateway container (gnzsnz/ib-gateway, port 4004 paper)
│   └── CURRENTLY FAILING (paper trading auth issue)
└── aegis-redis container (port 6379 internal, noeviction, 256MB, password: nzt48redis)
```

**Engine State**: Running main loop, idle (no ticks from IB Gateway). Periodic 60s broker reconnect attempts. All cron jobs executing on schedule. WAL watcher monitoring `events/2026-03-17.ndjson`.

---

*End of document. Runtime truth is the highest authority. When in doubt, read the code.*
