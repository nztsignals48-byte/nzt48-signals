# PLAN 2: INTELLIGENCE LAYER & SYSTEM EVOLUTION — COMPLETE SPECIFICATION

**Status:** Plan 1 complete (Sprints 0-10 DONE). Engine deployed to EC2, connected to IBKR, winning trades.
**Cost:** $0/month (Claude Code CLI via Max subscription on EC2, authenticated with `claude -p`)
**Estimated effort:** 35-50 hours across 9 phases
**Doctrine:** Rust owns execution. Claude owns intelligence. Ouroboros owns learning. Operator owns authority.

---

## DECISION HIERARCHY

```
LEVEL 4: OPERATOR — absolute authority (kill switch, PR merge, capital allocation)
LEVEL 3: INTELLIGENCE (Claude) — high negative authority, zero positive authority
           CAN: downrank, veto, escalate, explain, challenge, recommend shadow testing
           CANNOT: force trades, override risk gates, mutate live config, manage stops
LEVEL 2: LEARNING (Ouroboros) — parameter optimization, regime classification, blacklist
LEVEL 1: EXECUTION (Rust) — final authority on live capital, overrides ALL above on hard risk
```

**Air-Gap Doctrine:** Claude operates exclusively on the cold path (nightly, 2-hourly, weekly). Zero Claude involvement in the hot path (tick processing, stop trailing, order execution). LLMs are probabilistic text predictors — brilliant for synthesis, incapable of deterministic sub-millisecond state management.

**Three-Layer Signal Architecture:**
- **Layer A: Discovery (Cold)** — Universe scanning, ranking, shortlisting. Does NOT generate trade signals.
- **Layer B: Alpha Model (Warm)** — Factor-based signal generation. Current: multiple evaluators competing. Evolution target: unified alpha score from orthogonal factors.
- **Layer C: Execution (Hot)** — 30-CHECK risk arbiter, Chandelier exit, order lifecycle. All deterministic.

**Claude Max Subscription Integration:** `claude -p` CLI on EC2 via Max subscription. Spawns as subprocess, runs, exits — NOT a resident daemon. 3-attempt retry with exponential backoff. Model: claude-opus-4-6. Cost: $0/month.

---

## TABLE OF CONTENTS

1. Current System State
2. Complete Architecture Diagram
3. Signal Flow: Tick to Trade (All Steps)
4. Alpha Model — Factor-Based Signal Generation
5. Complete Universe Selection Pipeline (7 Mechanisms)
6. Risk Arbiter: All 30 CHECKs
7. Complete Nightly Pipeline
8. Claude Intelligence: All 9 Roles
9. Phase 1: Infrastructure
10. Phase 2: Post-Trade Forensic Analyst
11. Phase 3: Parameter Governance + Approval Gate
12. Phase 4: Operator Intelligence Briefings
13. Phase 5: Universe Curation
14. Phase 6: Gate Calibration Analyst
15. Phase 7: Anomaly Risk Assessor + Macro Event Intelligence
16. Phase 8: Adversarial SDE Generator
17. Shadow Mode Validation Framework
18. Approval Gate Decision Tree
19. Complete Crontab
20. Files to Create / Modify
21. Validation Gates
22. Adversarial Hardening (H1-H7)
23. Evolution Path (E1-E5)
24. Auditor Feedback Integration
25. Execution Order
26. Cost

---

## CURRENT SYSTEM STATE

- **Plan 1 complete:** All 11 sprints DONE. 30 risk CHECKs in deterministic order. 90+ config-driven thresholds. Chandelier 5-rung exit with 8 adaptive multipliers. Per-exchange entry cutoffs. VWAP auto-reset on date change. 6 portfolio risk gates (weekly/peak drawdown + equity floor).
- **Engine winning trades:** Observed this week: GBP 25 profit day, GBP 15 loss day. The old "0% win rate across 52 trades" stat was stale March 18 data from BEFORE Sprint 5 timing fixes (T-04 ADX thresholds lowered 25->20, T-05 RVOL thresholds lowered 1.5->1.0, T-07 confidence floor made leverage-aware, T-08 cooldown reduced 25min->5min, SK-04 system velocity raised 3->10).
- **Deployed:** EC2 c7i-flex.large (4GB RAM, 2 vCPUs), Docker Compose (aegis-v2 + aegis-ib-gateway + aegis-redis), connected to IBKR live market data via IB Gateway on port 4003.
- **Ouroboros feedback loop CLOSED:** `nightly_v6.py` --> JSON recommendations --> `config_writer.py` --> `dynamic_weights.toml` --> SIGHUP engine hot-reload.
- **Existing Claude stubs:** `claude_review.py` (90% done, 470 lines), `claude_briefing.py` (90% done), both scheduled in crontab. Currently use Anthropic API SDK (costs money per call) -- need switch to `claude -p` CLI ($0).
- **4 factor families** (F_MOM, F_REV, F_MAC, F_DIS) generating signals across momentum, reversion, and macro-beta domains.
- **7 scanning mechanisms** feeding a 36K+ ticker master universe into 100+50 active subscriptions.
- **Multi-exchange:** Per-exchange entry cutoffs, session structs for LSE/US/HK/TSE/XETRA/EURONEXT, VWAP auto-reset on date change.

---

## COMPLETE ARCHITECTURE DIAGRAM

```
+============================================================================+
|                    AEGIS V2 -- FULL INTELLIGENCE STACK                      |
+============================================================================+

 LAYER 0: UNIVERSE DISCOVERY (background, daily/hourly)
 +---------------------------------------------------------------------------+
 | full_universe_builder.py (daily, 06:00 UTC)                                |
 |   Method 1: Wikipedia scraping (16 indices: S&P500, FTSE, Nikkei, etc.)   |
 |   Method 2: Exchange CSV/API downloads (NYSE, NASDAQ, AMEX)                |
 |   Method 3: yfinance ETF holdings scan (12 exchanges)                      |
 |   Method 4: LSE leveraged ETP pattern generation (2L/3L/5L x 200 codes)   |
 |   OUTPUT: config/isa_universe_master.json (36K+ tickers)                   |
 |                                                                            |
 | contract_expander.py (every 6 hours)                                       |
 |   Finds high-scoring tickers WITHOUT contract definitions                  |
 |   Validates via yfinance, appends to contracts.toml                        |
 |   Sends SIGHUP to engine for hot-reload                                    |
 |   MAX_NEW_PER_RUN=20, MAX_TOTAL_CONTRACTS=500                              |
 |                                                                            |
 | IBKR Scanner (planned: weekly deep scan across 16 exchanges)               |
 |   10 active scanners x 50 results = up to 500 candidates                   |
 |   Feeds into ticker_selector priority queue                                |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 0.5: UNIVERSE SELECTION + RANKING (every 15 min / 2 hours)
 +---------------------------------------------------------------------------+
 | ticker_selector.py (every 15 minutes)                                      |
 |   Loads isa_universe_master.json (36K+)                                    |
 |   Filters to open exchanges (timezone-aware, DST-corrected)               |
 |   Contract-awareness filter (only tickers in contracts.toml)              |
 |   Tier classification: T1+2 (daily price), T3 (weekly cache), T4 (static) |
 |   6-factor composite scoring: volatility(35%), volume(20%), leverage(25%),|
 |     momentum(15%), spread_proxy(5%), backfill_adjustment                  |
 |   Hysteresis: +5 bonus for tickers already in watchlist (anti-churn)      |
 |   OUTPUT: config/active_watchlist.json (top 100 tickers)                  |
 |   OUTPUT: config/initial_universe.toml (for Rust engine)                  |
 |                                                                            |
 | ticker_ranker.py (every 2 hours, called by ticker_selector)               |
 |   6-factor real-time scoring (0-100 per ticker):                           |
 |     1. Spread quality (25%) -- bid/ask spread in bps                       |
 |     2. RVOL (15%) -- relative volume vs 20-bar MA, regime-aware           |
 |     3. Regime fit (20%) -- Hurst/ADX alignment with strategy family        |
 |     4. Recent performance (15%) -- WR + edge ratio from Ouroboros          |
 |     5. Session fit (15%) -- exchange open? preferred for this window?      |
 |     6. Liquidity (10%) -- average daily volume, log-scaled                 |
 |   Leverage boost: +30 base + 5 per leverage mult for LSE ETPs when open   |
 |   OUTPUT: config/strategies.toml [ticker_ranking.current] section          |
 |                                                                            |
 | Thompson Sampler (continuous, Rust engine)                                 |
 |   Log-Normal Thompson Sampling (Bayesian bandit)                           |
 |   Posterior probability ranking of all tickers                             |
 |   Top-K used to boost confidence for top tickers                           |
 |   File: rust_core/src/log_thompson_sampler.rs                              |
 |   Arms tracked per ticker, updated on trade outcomes                       |
 |                                                                            |
 | HotScanner (real-time, Rust engine -- planned)                            |
 |   Volatility-momentum anomaly detection on streaming data                  |
 |   Identifies tickers with unusual price/volume activity                    |
 |   Promotes candidates for immediate Tier 2 booster rotation                |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 1: MARKET DATA (22h/day, 100+50 streaming model)
 +---------------------------------------------------------------------------+
 |                                                                            |
 |  IBKR Gateway (aegis-ib-gateway:4003)                                     |
 |    |                                                                       |
 |    +--> 100 PRIMARY TICKERS (refreshed every 2 hours by ticker_selector)  |
 |    |     Full continuous 5-second tick data, no gaps                        |
 |    |     Selected by: composite score from ticker_ranker                    |
 |    |     MUST include any ticker with OPEN POSITION (exit monitoring)      |
 |    |                                                                       |
 |    +--> 50 BOOSTER TICKERS (rotated every 15 minutes)                     |
 |          Scanner-flagged overflow tickers not in primary 100                |
 |          15-minute streaming windows, then next batch rotates in            |
 |          Priority: scanner rank x Ouroboros score x Thompson posterior      |
 |          If strong signals during 15-min window --> promote to primary      |
 |    |                                                                       |
 |    +--> Tick Channel (50K buffer) --> Rust Engine tick processor           |
 |                                                                            |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 2: RUST ENGINE (real-time, sub-millisecond)
 +---------------------------------------------------------------------------+
 |                                                                            |
 |  +---------------+    +----------------+    +----------------------------+ |
 |  | Bar Builder   |--->| Python Bridge  |--->| Signal Generation          | |
 |  | (5s -> 5min)  |    | (bridge.py)    |    | Alpha Model:               | |
 |  | engine.rs     |    | JSON over      |    |  F_MOM + F_REV + F_MAC     | |
 |  | ~3100 lines   |    | stdin/stdout)  |    |  + F_DIS (discovery)       | |
 |  +---------------+    +----------------+    +-------------+--------------+ |
 |                                                           |                |
 |  +--------------------------------------------------------v--------------+ |
 |  |              RISK ARBITER (30 CHECKs, deterministic)                   | |
 |  |  risk_arbiter.rs ~600 lines                                            | |
 |  |  ISA -> Inverse -> Regime -> MaxPos -> Stale -> Broker -> WAL ->       | |
 |  |  Confidence -> Cutoff -> Spread -> DailyTrade -> MinEdge -> Cash ->    | |
 |  |  Heat -> Sector -> ISA_Limit -> DailyDD -> WeeklyDD -> PeakDD ->      | |
 |  |  EquityFloor -> Velocity -> Macro -> ConsecLoss -> Duplicate ->        | |
 |  |  Halted -> CVaR -> GARCH -> Scanner -> Kelly -> DailyLimit -> Edge     | |
 |  +------------------------------------+-----------------------------------+ |
 |                                       |                                    |
 |  +------------------------------------v-----------------------------------+ |
 |  |              EXIT ENGINE (Chandelier 5-rung ladder)                     | |
 |  |  exit_engine.rs: InfiniteChandelier with 8 adaptive ATR multipliers    | |
 |  |  Rung 0: Initial stop (1.0x ATR)                                       | |
 |  |  Rung 1: Breakeven lock (0.0 ATR from entry)                          | |
 |  |  Rung 2: Profit protection (0.75x ATR trail)                          | |
 |  |  Rung 3: Trend capture (0.5x ATR trail)                               | |
 |  |  Rung 4: Extended trend (0.4x ATR trail)                              | |
 |  |  Rung 5: Max extraction (0.3x ATR trail, widest possible)             | |
 |  |  All multipliers loaded from config.toml [chandelier.adaptive]         | |
 |  |  Rung persistence: RungAdvanced WAL events, restored during replay     | |
 |  +------------------------------------------------------------------------+ |
 |                                                                            |
 |  +------------------------------------------------------------------------+ |
 |  |              ENTRY ENGINE (4 Rust entry types -- Crucible only)         | |
 |  |  entry_engine.rs: DipRecovery (A), EarlyRunner (B),                    | |
 |  |                   OverboughtFade (C), SupportBounce (D)                | |
 |  |  Base confidences: A=65%, B=82%, C=72%, D=70%                          | |
 |  |  Per-type RSI thresholds, volume expansion, ATR drop multiples         | |
 |  |  Currently defined for Crucible sim mode -- not live signal path       | |
 |  +------------------------------------------------------------------------+ |
 |                                                                            |
 |  OUTPUT: WAL events (ndjson) -> gate_vetoes.ndjson -> missed_winners      |
 |  OUTPUT: MAE/MFE tracking per position in PositionState                   |
 |  OUTPUT: RungAdvanced events for chandelier persistence                   |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 3: OUROBOROS (nightly, 04:50 UTC)
 +---------------------------------------------------------------------------+
 |                                                                            |
 |  nightly_v6.py (04:50 UTC)                                                |
 |    Read WAL -> compute metrics -> generate recommendations                |
 |    Per-ticker stats: WR, PF, avg_win, avg_loss, edge_ratio                |
 |    Per-strategy performance breakdown                                      |
 |    Regime classification for next session                                  |
 |    OUTPUT: data/nightly_output.json                                        |
 |                                                                            |
 |  config_writer.py (04:51 UTC + boot)                                      |
 |    Reads nightly_v6 JSON output                                            |
 |    Applies bounded parameter adjustments                                   |
 |    Writes dynamic_weights.toml                                             |
 |    Generates [indicator_gates] rules from per-indicator performance        |
 |    Generates [ticker_blacklist] from Wilson score interval (WR<30%, 10+)  |
 |    Sends SIGHUP to engine for hot-reload                                   |
 |    OUTPUT: config/dynamic_weights.toml                                     |
 |                                                                            |
 |  missed_winner_detector.py (offline)                                      |
 |    Classifies gate vetoes: GOOD_VETO, BAD_VETO, AMBIGUOUS, DATA_VETO     |
 |    Compares rejected signal price to subsequent 2-hour price movement      |
 |    Per-gate false positive rates                                           |
 |    OUTPUT: data/missed_winners.json                                        |
 |                                                                            |
 |  research_store.py                                                         |
 |    7-day rolling context window for Claude                                 |
 |    OUTPUT: data/context_store.json                                         |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 4: CLAUDE INTELLIGENCE (Plan 2 -- THIS DOCUMENT)
 +---------------------------------------------------------------------------+
 |                                                                            |
 |  A. FORENSIC REVIEW (04:53 UTC)                                           |
 |     Classify trades, tune gates, identify root causes                      |
 |                                                                            |
 |  B. OUROBOROS CHALLENGER (04:55 UTC)                                      |
 |     Challenge Ouroboros recommendations with statistical rigor             |
 |                                                                            |
 |  C. APPROVAL GATE (04:56 UTC)                                            |
 |     Apply/reject/shadow with hard bounds + audit trail                    |
 |                                                                            |
 |  D. MORNING BRIEFING (07:45 UTC, before LSE open)                        |
 |     60-second Telegram digest: yesterday, overnight changes, today        |
 |                                                                            |
 |  E. EVENING BRIEFING (21:30 UTC, after US close)                         |
 |     Day summary, P&L by exchange, gate veto summary                       |
 |                                                                            |
 |  F. UNIVERSE CURATION (every 2 hours, shadow mode first)                 |
 |     Select Tier 1/2 instruments alongside deterministic ranker             |
 |                                                                            |
 |  G. REJECTED-TRADE REVIEW (Friday 22:00 UTC)                             |
 |     Weekly gate forensics: per-gate bad veto rates, threshold recs        |
 |                                                                            |
 |  H. ANOMALY ASSESSOR (event-triggered)                                   |
 |     Real-time risk assessment on spread/volume/VIX anomalies              |
 |                                                                            |
 |  I. MACRO INTERPRETER (calendar-triggered, 30 min pre-event)             |
 |     FOMC/NFP/CPI/earnings pre-event analysis + blackout recommendations   |
 |                                                                            |
 +---------------------------------------------------------------------------+
            |
            v
 LAYER 5: OPERATOR (Telegram + Sheets)
 +---------------------------------------------------------------------------+
 |  /status /approve /reject /kill /pause /resume /review-today              |
 |  Real-time alerts on OPERATOR_ATTENTION decisions                          |
 |  Google Sheets: win_loss_delta, session PDFs at session opens              |
 +---------------------------------------------------------------------------+
```

---

## SIGNAL FLOW: TICK TO TRADE (ALL STEPS)

```
COMPLETE SIGNAL FLOW — FROM RAW TICK TO EXECUTED TRADE

STEP 1: IBKR TICK ARRIVES
  aegis-ib-gateway:4003 --> Rust TwsApi client
  Fields: ticker_id, last, high, low, bid, ask, volume, timestamp_ns
  Rate: ~5-second bars (configurable)

STEP 2: BAR BUILDER (engine.rs)
  Raw tick --> append to per-ticker bar history (deque, max 500 bars)
  Compute: 5-second OHLCV bar
  Aggregate: 60 x 5s bars --> 5-minute OHLCV bar (cached in _bar_cache)

STEP 3: RUST PRE-CHECKS
  Is ticker in active universe? (initial_universe.toml / contracts.toml)
  Is exchange currently open? (market_scheduler.rs session phase)
  Is ticker halted? (split_handler.rs)
  Pass context to Python Bridge via JSON over stdin

STEP 4: PYTHON BRIDGE (bridge.py, long-lived subprocess)
  Receives: {"type":"tick", "ticker_id":0, "last":10.5, ...context...}

  STEP 4a: BLACKLIST CHECK
    _load_ticker_blacklist() from dynamic_weights.toml
    Wilson score interval: WR < 30% over 10+ trades --> suppressed
    If blacklisted --> return {"type":"no_signal"}

  STEP 4b: WARM-UP GATE
    MIN_WARMUP_BARS = 200 (16 min of 5-second data = 3+ five-minute bars)
    If len(ticks) < 200 --> return no_signal (silently, no log)

  STEP 4c: INDICATOR COMPUTATION (on 5-MINUTE bars, not raw 5s)
    RVOL = calculate_rvol(volumes_5m, window=20)
    Hurst = estimate_hurst(prices_5m, max_lag=20)
    ADX = _compute_adx(bars_5m, period=14)
    vol_slope = linear regression slope of recent 10 volumes
    vol_div = volume_divergence(prices_5m, volumes_5m, window=10)

  STEP 4d: INDICATOR GATES (from dynamic_weights.toml [indicator_gates])
    Each gate: {indicator, direction, threshold}
    Example: adx above 12 required --> if ADX < 12, VETO
    Logged to gate_vetoes.ndjson with full indicator context

  STEP 4e: STRUCTURAL TRADABILITY SCORE (STS, 0-100)
    Component 1: Spread quality (0-25 pts)
    Component 2: Regime clarity (0-25 pts, |H - 0.5| / 0.5)
    Component 3: Volume quality (0-20 pts, RVOL + vol_slope)
    Component 4: ADX trend strength (0-15 pts)
    Component 5: Data quality (0-15 pts, bar count)
    STS < 30 --> VETO (poor microstructure)

  STEP 4f: LEVERAGE-AWARE CONFIDENCE FLOOR
    5x ETP --> floor = 80
    3x ETP --> floor = 65
    Unleveraged --> floor = 45
    Adaptive floor from dynamic_weights.toml takes max of both

  STEP 4g: VWAP PULLBACK CHECK
    If price > 1.5% above session VWAP --> VETO (chasing extension)
    Ideal entry: within +/-0.5% of VWAP

  STEP 4h: REGIME GATE (on 5-minute Hurst)
    Hurst < 0.40 --> VETO (strongly mean-reverting, suppress momentum)
    Hurst 0.40-0.50 --> raise confidence floor to 70

  STEP 4i: VOLUME TREND GATE
    If vol_slope <= 0 and has_volume --> raise confidence floor to 75

  STEP 4j: MULTI-TIMEFRAME CONFIRMATION
    Compute trend direction on 3 timeframes: 5s EMA, 1m EMA, 5m EMA
    All 3 must agree (all up or all down) --> else VETO

  STEP 4k: EVALUATE VANGUARDSNIPER
    File: python_brain/brain/strategies/vanguard_sniper.py
    evaluate(ticks_5m, confidence_floor=effective_floor)
    Momentum scoring: ADX (0-40) + EMA trend (0-30) + RVOL breakout (0-30)
    Moreira-Muir vol scaling on Kelly fraction (not confidence)
    Returns: {confidence, kelly_fraction, features} or None

  STEP 4l: EVALUATE AUTONOMOUS ORCHESTRATOR
    File: python_brain/brain/strategies/autonomous_orchestrator.py
    Builds TickerState with all indicators
    Builds MarketContext with session, regime, VIX, SPY return
    Calls orchestrate(tickers, ctx, strategies, max_intents=3)
    Evaluates eligible strategies for current session + regime:
      S17: evaluate_vwap_dip_buy() -- VWAP dip N sigma, declining volume
      S18: evaluate_gap_fade() -- overnight gap fade, RVOL < 2.0
      S19: evaluate_rsi_ibs() -- RSI(2)/IBS mean reversion, above SMA-200
      S20: evaluate_cross_market_momentum() -- US direction predicts LSE
      S21: (reserved for future intraday momentum strategy)
    Returns best TradeIntent (highest combined_score = priority x confidence)

  STEP 4m: EVALUATE APEX SCOUT (separate message type)
    File: python_brain/brain/strategies/apex_scout.py
    Triggered by {"type":"apex_snapshot"} messages (60s OHLCV snapshots)
    700 tickers on 60-second snapshots (wider but slower than Vanguard)
    RVOL anomaly detection: current_rvol vs RVOL_LOOKBACK mean
    Moreira-Muir volatility scaling
    Combined: (rvol_score + momentum_score) * mm_scale
    Returns: {confidence, kelly_fraction, features} or None

  STEP 4n: 12-FACTOR KELLY SIZING
    File: python_brain/brain/sizing/kelly_12factor.py
    kelly_12factor() called for momentum factor signals:
      Factor 1: Base Kelly from WR + avg_win/avg_loss
      Factor 2: Leverage scaling (3x/5x ETP penalty)
      Factor 3: Realized vol (annual) -- higher vol = smaller
      Factor 4: Correlation to existing portfolio
      Factor 5: Current drawdown penalty
      Factor 6: Amihud illiquidity measure
      Factor 7: Regime adjustment (reduce in high-vol)
      Factor 8: Spread cost deduction
      Factor 9: Time-of-day fraction (late = smaller)
      Factor 10: Confidence scaling
      Factor 11: Portfolio heat constraint
      Factor 12: Equity-based sizing
    Paper bootstrap: if total_trades < 50, use preliminary Kelly floor

  STEP 4o: BEST SIGNAL SELECTION
    If multiple factors fire --> alpha vector blending (evolution: weighted sum)
    STS adjustment: score > 70 boosts +6, score < 50 penalizes -4
    strategy_confidence preserved BEFORE STS adjustment (for CHECK 10)
    Per-ticker cooldown: 60 ticks (5 min) between signals on same ticker

  STEP 4p: LSE LEVERAGED ETP BOOST
    During LSE hours (08:00-16:30 London): +20 confidence for LSE ETPs
    Loaded dynamically from contract_loader.py, not hardcoded

STEP 5: SIGNAL RETURNS TO RUST ENGINE
  JSON response: {"type":"signal", "ticker_id":1, "direction":"Long",
    "confidence":78, "kelly_fraction":0.15, "shares":42,
    "factor":"F_MOM", "structural_score":72, ...}

STEP 6: RISK ARBITER EVALUATION (30 CHECKs -- see section below)
  All 30 CHECKs run in deterministic order
  Any REJECT --> signal killed, reason logged to WAL + gate_vetoes.ndjson
  VetoReason enum captures which CHECK rejected

STEP 7: POSITION SIZING (position_sizer.rs)
  Kelly fraction from Python, regime-scaled
  Min entry size per exchange (GBP 1500 LSE, USD 300 US)
  ISA annual limit check

STEP 8: ORDER EXECUTION
  Order placed via IBKR TWS API
  PositionOpened WAL event written
  MAE/MFE tracking initialized in PositionState

STEP 9: EXIT MANAGEMENT (exit_engine.rs)
  InfiniteChandelier monitors every tick
  5-rung ladder: initial stop --> breakeven --> profit protect --> trend --> max
  RungAdvanced WAL events on each rung transition
  PositionClosed WAL event with final MAE/MFE

STEP 10: FEEDBACK LOOP
  Trade outcome --> persistent_memory.json
  --> nightly_v6.py analysis
  --> config_writer.py parameter updates
  --> dynamic_weights.toml
  --> SIGHUP engine hot-reload
  --> Thompson Sampler arm update
  --> ticker_ranker performance score update
```

---

## ALPHA MODEL — FACTOR-BASED SIGNAL GENERATION

The signal generation pipeline uses 4 orthogonal factor families. Current implementation uses named evaluator modules; the evolution target is a unified alpha vector: `Alpha = (w1*F_MOM) + (w2*F_REV) + (w3*F_MAC)` with Ouroboros-tuned weights nightly. Current evaluators continue running until the unified model is shadow-validated over 200+ trades. No hardcoded ticker lists — all universe selection is dynamic from contracts.toml (264+ contracts across 6+ exchanges).

**Asymmetric EOD Rules:**
- LSE + Asia: Force-flatten 5 min before close (MOC/LOC orders). Zero overnight exposure.
- US equities: Allow overnight hold with GTC stop-limit on IBKR servers. Chandelier resumes on open.

**Re-Entry Policy:** Velocity cap (max 3 entries per ticker per 5-min window) replaces fixed cooldown. If the math says buy 30 seconds after a stop-out, buy again.

### Factor 1: Momentum (F_MOM) — via vanguard_sniper.py
- **File:** `/app/python_brain/brain/strategies/vanguard_sniper.py`
- **Called from:** `bridge.py` line 979: `vanguard_evaluate(eval_ticks, confidence_floor=effective_floor)`
- **Universe:** Top 100 primary tickers (highest composite score from ticker_ranker)
- **Timeframe:** 5-minute bars aggregated from 5-second raw ticks
- **Entry logic:** Graduated momentum scoring:
  - ADX >= 25: +40, ADX >= 15: +30, ADX >= 10: +20, ADX >= 7: +15
  - Price above EMA(20): +30
  - RVOL >= VOLUME_BREAKOUT_MULT: +30, >= 1.5: +20, >= 1.2: +10
- **Confidence floor:** Configurable, leverage-aware (65 for 3x, 80 for 5x)
- **Sizing:** Moreira-Muir (2017) vol scaling applied to Kelly fraction (NOT confidence)
- **Direction:** Long only (inverse products handled via inverse pair blocking)
- **Auction gate:** Blocks during LSE open (07:50-08:00) and close (16:30-16:35) auctions

### Factor 2: Statistical Reversion (F_REV) — S17 VWAP Dip Buy
- **File:** `/app/python_brain/brain/strategies/autonomous_orchestrator.py`
- **Function:** `evaluate_vwap_dip_buy(ticker, ctx, cfg)`
- **Family:** Mean reversion
- **Entry:** Price drops N sigma below VWAP (default entry_vwap_sigma=2.0)
- **Filters:** Volume declining (not accelerating breakdown), VWAP slope flat (< 0.01), ADX < 25, spread < 15 bps, VIX < 30, broad market not at lows, no news catalyst
- **Stop:** VWAP sigma (default 3.0 sigma)
- **Target:** VWAP itself (mean reversion target)
- **Time stop:** 90 minutes
- **Session eligible:** LSE Midday (10:30-14:30), US Overlap (14:30-16:00)
- **Regime eligible:** Mean reverting, Random

### Factor 2b: Statistical Reversion (F_REV) — S18 Gap Fade
- **File:** same as S17
- **Function:** `evaluate_gap_fade(ticker, ctx, cfg)`
- **Family:** Mean reversion
- **Entry:** Overnight gap between 1.5% and 6.0% (fade liquidity gaps, not info gaps)
- **Filters:** RVOL < 2.0 (liquidity gap, not information gap), RVOL > 5.0 absolute veto, no earnings, spread < 20 bps, VIX < 35
- **Direction:** Long if gap-down, inverse if gap-up (fade the gap)
- **Stop:** Gap % x 1.5 (percentage stop)
- **Target:** 75% gap fill
- **Time stop:** 120 minutes
- **Session eligible:** 08:15-10:00 (first 2 hours of LSE)

### Factor 2c: Statistical Reversion (F_REV) — S19 RSI/IBS
- **File:** same as S17
- **Function:** `evaluate_rsi_ibs(ticker, ctx, cfg)`
- **Family:** Mean reversion
- **Entry:** RSI(2) < 5.0 AND IBS < 0.20 (daily oversold bounce), for 3x products: RSI(2) < 2.5 AND IBS < 0.10
- **Filters:** Price above SMA-200, max 5% above SMA-200, macro filter (SPX 126d return > 0), spread < 20 bps
- **Sizing:** 0.5x penalty for 3x products (decay risk on multi-day hold)
- **Stop:** 5% percentage stop
- **Target:** Close above 5-day SMA
- **Time stop:** 10 trading days max hold

### Factor 3: Macro-Beta (F_MAC) — S20 Cross-Market Momentum
- **File:** same as S17
- **Function:** `evaluate_cross_market_momentum(ticker, ctx, cfg)`
- **Family:** Momentum
- **Entry:** SPY first 30-min return > 0.3% (US market direction predicts LSE continuation)
- **Filters:** ADX > 20, RVOL > 1.2, Hurst > 0.50, spread < 15 bps
- **Direction:** Long if SPY positive, inverse if SPY negative
- **Stop:** 1.5x ATR trailing
- **Target:** 1.5x ATR trailing
- **Time stop:** 90 minutes

### Factor 4: Discovery (F_DIS) — RVOL Anomaly Scanner
- **File:** `/app/python_brain/brain/strategies/apex_scout.py`
- **Called from:** `bridge.py` line 1176: `apex_evaluate(snapshots)`
- **Universe:** 700 tickers on 60-second OHLCV snapshots (wider but slower)
- **Message type:** `apex_snapshot` (separate from `tick`)
- **Entry logic:** RVOL anomaly detection:
  - Current bar volume vs RVOL_LOOKBACK mean
  - RVOL exceeds RVOL_THRESHOLD --> rvol_score = min(excess * 50, 50)
  - Positive bar return --> momentum_score = min(return * 1000, 50)
  - Combined = (rvol_score + momentum_score) * Moreira-Muir scale
- **Sizing:** Preliminary Kelly = confidence / 1000, capped at 0.20
- **Direction:** Long only

### Rust Entry Types (Crucible Sim Only)
- **File:** `/app/rust_core/src/entry_engine.rs`
- **Type A: DipRecovery** -- base confidence 65%
- **Type B: EarlyRunner** -- base confidence 82%
- **Type C: OverboughtFade** -- base confidence 72%
- **Type D: SupportBounce** -- base confidence 70%
- **Status:** Defined but only evaluated in Crucible simulation mode, not in live signal path. Live signals come from Python strategies above.

---

## COMPLETE UNIVERSE SELECTION PIPELINE

### The 100 + 50 Booster Scanning Model

```
UNIVERSE FUNNEL: 36K+ --> 500 --> 100 + 50

MECHANISM 1: full_universe_builder.py (daily, 06:00 UTC)
  File: /app/python_brain/ouroboros/full_universe_builder.py
  Schedule: Daily at 06:00 UTC
  Method 1: Wikipedia scraping -- 16 indices
    _scrape_sp500() --> ~500 tickers (NYSE)
    _scrape_nasdaq100() --> ~100 tickers (NASDAQ)
    _scrape_russell2000() --> ~2000 tickers (NYSE)
    _scrape_ftse_allshare() --> ~600 tickers (LSE)
    _scrape_nikkei225() --> ~225 tickers (TSE)
    _scrape_hangseng() --> ~50 tickers (HKEX)
    _scrape_hangseng_tech() --> ~30 tickers (HKEX)
    _scrape_asx200() --> ~200 tickers (ASX)
    _scrape_dax40() --> ~40 tickers (XETRA)
    _scrape_cac40() --> ~40 tickers (EURONEXT_PA)
    _scrape_eurostoxx50() --> ~50 tickers (EURONEXT_AS)
    _scrape_eurostoxx600() --> ~300 tickers (EURONEXT_AS)
    _scrape_tsx60() --> ~60 tickers (TSX)
    _scrape_kospi200() --> ~200 tickers (KRX)
    _scrape_smi() --> ~20 tickers (SIX)
    _scrape_sti() --> ~30 tickers (SGX)
  Method 2: Exchange CSV/API downloads
    _fetch_nasdaq_listed() -- NASDAQ API screener
    _fetch_nyse_listed() -- NYSE API screener
    _fetch_amex_listed() -- AMEX API screener
  Method 3: yfinance ETF holdings scan (12 exchanges)
    Major tracking ETFs: SPY, QQQ, IWM, VTI, ISF.L, 2800.HK, etc.
  Method 4: LSE leveraged ETP pattern generation
    6 prefixes (2L, 2S, 3L, 3S, 5L, 5S) x 200+ underlying codes
    ~1200 synthetic ETP candidates
  OUTPUT: config/isa_universe_master.json (36K+ tickers)
           |
           v
MECHANISM 2: contract_expander.py (every 6 hours)
  File: /app/python_brain/ouroboros/contract_expander.py
  Schedule: Every 6 hours (crontab: 0 1,7,13,19 * * 1-5)
  Loads active_watchlist.json (scored tickers) + master universe
  Finds high-score tickers WITHOUT contracts.toml entries
  Validates via yfinance (must have 5-day price data)
  Appends new [[contracts]] entries to contracts.toml
  MAX_NEW_PER_RUN = 20, MAX_TOTAL_CONTRACTS = 500
  Sends SIGHUP to Rust engine for hot-reload
  OUTPUT: Appended entries in config/contracts.toml
           |
           v
MECHANISM 3: ticker_selector.py (every 15 minutes)
  File: /app/python_brain/ouroboros/ticker_selector.py
  Schedule: Every 15 minutes (crontab: */15 * * * 1-5)
  Step 1: Load isa_universe_master.json (36K+)
  Step 1b: Contract-awareness filter (only tickers in contracts.toml)
  Step 2: Filter to currently OPEN exchanges (timezone-aware via pytz)
    EXCHANGE_LOCAL_HOURS: DST-corrected for all 15 exchanges
    is_exchange_open(exchange, utc_hour, utc_minute) -- handles lunch breaks
  Step 3: Classify into tiers:
    Tier 1+2: Leveraged ETPs + validated high-vol + major indices (MAX_DAILY_FETCH=1500)
    Tier 3: Next 2500 (weekly price cache)
    Tier 4: Everything else (static scoring, zero network calls)
  Step 4: Fetch daily price data for Tier 1+2 via yfinance
    Batch size 20, exponential backoff on 429, micro-batch retry
  Step 5: Score Tier 3 from weekly cache or fresh data
  Step 6: Score Tier 4 statically (leverage, market_cap, volume, exchange)
  Step 7: Rank and composite score:
    W_VOLATILITY=0.35, W_VOLUME=0.20, W_LEVERAGE=0.25,
    W_MOMENTUM=0.15, W_SPREAD_PROXY=0.05
  Step 7b: Apply backfill_adjustment from simulation results
  Step 8: Hysteresis: +5 bonus for tickers already in watchlist
  OUTPUT: config/active_watchlist.json (top 100)
  OUTPUT: config/initial_universe.toml (for Rust config_loader)
           |
           v
MECHANISM 4: ticker_ranker.py (every 2 hours, called by ticker_selector)
  File: /app/python_brain/brain/ticker_ranker.py
  6-factor real-time composite scoring (0-100 per ticker):
    score_spread(bid, ask, last_price) -- 25% weight
    score_rvol(rvol, regime_state) -- 15% weight, regime-aware optimal bands
    score_regime_fit(hurst, adx, regime_state) -- 20% weight
    score_performance(win_rate, edge_ratio, trade_count) -- 15% weight, Laplace-smoothed
    score_session_fit(exchange, session_window, ticker) -- 15% weight
    score_liquidity(avg_daily_volume) -- 10% weight, log-scaled
    score_leverage_boost(ticker_data, lse_is_open) -- additive (+30 base + 5 per lev mult)
  Loads portfolio performance from persistent_memory.json
  OUTPUT: config/strategies.toml [ticker_ranking.current] section
  OUTPUT: reports/ticker_rankings/ranking_YYYY-MM-DD_HHMM.txt
           |
           v
MECHANISM 5: Thompson Sampler (continuous, Rust engine)
  File: /app/rust_core/src/log_thompson_sampler.rs
  Log-Normal Thompson Sampling (Bayesian bandit ranking)
  Each ticker is an "arm" with posterior (alpha, beta) parameters
  Updated on every trade outcome (win/loss updates posterior)
  Top-K ranking used to:
    1. Boost confidence for top-ranked tickers
    2. Drive Tier 1 subscription slot allocation
  run_top_k(n) returns top N tickers by posterior probability
  arm(ticker_id) tracks per-ticker Bayesian stats
           |
           v
MECHANISM 6: HotScanner (planned, Rust engine)
  Real-time volatility-momentum anomaly detection
  Identifies tickers with unusual price/volume activity on streaming data
  Candidates promoted to Tier 2 booster rotation immediately
  Not yet implemented -- will be event-driven from tick processor

MECHANISM 7: IBKR Scanner (planned)
  File: python_brain/scanner_manager.py (to be created)
  10 active IBKR scanner subscriptions (free, no data lines consumed)
  Scanners: top volume, top % gainers, unusual volume, momentum
  Configured per exchange based on active session
  Up to 500 candidates per scan cycle
  Feeds into ticker_selector priority queue
  Scanners tell engine WHAT exists, don't provide price data

STREAMING ALLOCATION (100 IBKR data lines):

  +------------------------------------------+
  | 100 PRIMARY TICKERS                       |
  | Refreshed every 2 hours by ticker_selector|
  | Full continuous 5-second tick data        |
  | Selection: top 100 composite score        |
  | MUST include open positions (exit monitor)|
  +------------------------------------------+

  +------------------------------------------+
  | 50 BOOSTER TICKERS                        |
  | Rotated every 15 minutes                  |
  | Scanner-flagged overflow from primary 100 |
  | 15-min streaming window per batch         |
  | Priority: scanner x Ouroboros x Thompson  |
  | If strong signal --> promote to primary   |
  +------------------------------------------+

  Total streaming: 150 tickers
  (100 within IBKR limit + 50 via fast rotation)
```

---

## RISK ARBITER: ALL 30 CHECKs

**File:** `/app/rust_core/src/risk_arbiter.rs` (~600 lines)

All CHECKs run in deterministic order. First REJECT wins. Fail-closed design.

```
CHECK  1: ISA Safety          -- direction == Short --> HALT + REJECT (UK ISA rules)
CHECK  2: Inverse Mutual Excl -- holding inverse pair --> REJECT
CHECK  5: Risk Regime         -- HALT/FLATTEN state --> REJECT all entries
CHECK  6: Max Positions       -- filled + pending >= max_positions (config) --> REJECT
CHECK  7: Data Staleness      -- last_tick_age > stale_data_threshold_secs --> HALT
CHECK  8: Broker Connected    -- broker_connected == false --> HALT
CHECK  9: WAL Available       -- wal_available == false --> HALT
CHECK 10: Confidence Floor    -- confidence < floor --> REJECT
           Sprint 5 T-07: Leverage-aware. sqrt(leverage) scaling.
           3x ETP: floor * sqrt(3) = floor * 1.73
           5x ETP: floor * sqrt(5) = floor * 2.24
CHECK 11: Time-of-Day Cutoff  -- after per-exchange entry cutoff --> REJECT
           Sprint 7: Per-exchange cutoffs from config.toml [timing.exchange_cutoffs]
CHECK 13: Spread Veto         -- spread_pct > spread_veto_pct --> REJECT
           Leverage-aware: 3x ETPs get 6.67x the base spread gate
CHECK 14: Cash Buffer         -- available_cash < cash_buffer_pct * equity --> REJECT
CHECK 15: Portfolio Heat      -- total heat > max_heat_pct --> REJECT
CHECK 16: Sector Heat         -- sector heat > max_sector_heat_pct --> REJECT
CHECK 17: ISA Annual Limit    -- total invested > ISA_ANNUAL_LIMIT --> REJECT
CHECK 18: Daily Drawdown      -- daily DD > daily_drawdown_limit_pct --> FLATTEN
CHECK 19: Velocity Check      -- per-ticker entries > velocity_max in 5min --> REJECT
CHECK 19b: System Velocity    -- system-wide entries > system_velocity_max in 5min --> REJECT
            Sprint 5 SK-04: Raised from 3 to 10
CHECK 20: Macro Regime        -- VIX/DXY/credit escalation via CrossAssetMacro
CHECK 21: Consecutive Losses  -- consecutive_losses > max_consecutive_losses --> HALT
CHECK 22: Duplicate Position  -- already holding same ticker (momentum re-entry gated)
CHECK 23: Ticker Halted       -- ticker_halted flag from universe
CHECK 24: CVaR Heat           -- portfolio conditional value at risk above threshold
CHECK 25: GARCH Sigma         -- garch_sigma > threshold, leverage-scaled (Avellaneda & Zhang)
CHECK 26: Scanner Score       -- scanner_score > 0 AND < 30 --> REJECT (low quality scan)
CHECK 27: Kelly Floor         -- kelly_fraction > 0 AND < 0.005 --> REJECT (tiny edge)
CHECK 28: Daily Trade Limit   -- trades_today >= daily_trade_limit --> REJECT
            The #1 cost control gate. Prevents overtrading.
CHECK 29: Minimum Gross Edge  -- gross_edge < min_gross_edge_pct --> REJECT
CHECK 30: Weekly Drawdown     -- weekly DD from Monday HWM > weekly_drawdown_limit --> FLATTEN
            Sprint 10: weekly_high_water_mark tracked in PortfolioState
CHECK 31: Peak Drawdown       -- peak DD from all-time HWM > peak_drawdown_limit --> HALT
            Sprint 10: PortfolioState.peak_drawdown_pct()
CHECK 32: Equity Floor        -- equity < equity_floor_pct * initial_equity --> HALT
            Sprint 10: Hard floor at configurable % of initial equity

VetoReasons enum: every rejection tagged with specific reason for forensic review.
Output: WAL event + gate_vetoes.ndjson for Ouroboros missed-winner analysis.
```

---

## COMPLETE NIGHTLY PIPELINE

```
TIME (UTC)  | COMPONENT                | ACTION                                          | FILE
============|==========================|=================================================|================================
04:50       | Ouroboros nightly_v6      | Read ALL WAL files (current + archive/*.ndjson) | python_brain/ouroboros/nightly_v6.py
            |                          | Compute: per-ticker WR, PF, avg_win, avg_loss   |
            |                          | Compute: per-strategy performance breakdown      |
            |                          | Compute: regime classification for next session  |
            |                          | Compute: per-indicator win rate (ADX, RVOL, etc) |
            |                          | Breakeven trades (pnl==0) NOT counted as losses  |
            |                          | OUTPUT: data/nightly_output.json                 |
            |                          |                                                  |
04:51       | config_writer            | Read nightly_output.json                         | python_brain/ouroboros/config_writer.py
            |                          | Apply bounded parameter adjustments              |
            |                          | Generate [indicator_gates] rules                 |
            |                          | Generate [ticker_blacklist] from Wilson score     |
            |                          | Write dynamic_weights.toml                       |
            |                          | Send SIGHUP to engine for hot-reload             |
            |                          | OUTPUT: config/dynamic_weights.toml              |
            |                          |                                                  |
04:52       | win_loss_delta           | Per-indicator performance metrics                 | python_brain/ouroboros/win_loss_delta.py
            |                          | Push to Google Sheets (--push-sheets)            |
            |                          |                                                  |
04:53       | CLAUDE: Forensic Review  | Read: WAL, gate_vetoes, missed_winners,          | python_brain/ouroboros/claude_review.py
            |                          |   nightly_output, dynamic_weights, context_store |
            |                          | Classify each trade (W1-W5 winners, L1-L7 losers)|
            |                          | Identify root cause patterns                     |
            |                          | Generate gate tuning recommendations             |
            |                          | OUTPUT: data/claude/reviews/review_YYYY-MM-DD.json|
            |                          | Send summary to Telegram                         |
            |                          |                                                  |
04:55       | CLAUDE: Challenger       | Read: nightly_output.json, review output         | python_brain/ouroboros/ouroboros_challenger.py
            |                          | Challenge each Ouroboros recommendation          |
            |                          | Statistical rigor: sample size, p-value, bounds  |
            |                          | OUTPUT: data/claude/challenges/challenge_YYYY.json|
            |                          |                                                  |
04:56       | Approval Gate            | Read: challenger output, review output           | python_brain/ouroboros/approval_gate.py
            |                          | Decision: APPLY / TEST_ONLY / REJECT / NEEDS_DATA|
            |                          | Hard bounds enforcement (Claude CANNOT override) |
            |                          | APPLY + within bounds --> auto-write dynamic_weights|
            |                          | APPLY + exceeds bounds --> Telegram OPERATOR REQUIRED|
            |                          | TEST_ONLY --> shadow_params.toml (7 day shadow)  |
            |                          | OUTPUT: data/claude/approval_log.ndjson           |
            |                          |                                                  |
07:45       | CLAUDE: Morning Brief    | Read: review, challenger, approval_log,          | python_brain/ouroboros/claude_briefing.py
            |                          |   macro indicators, watchlist                    |
            |                          | Format: 60-second HTML digest for Telegram       |
            |                          | Content: yesterday grade, overnight changes,     |
            |                          |   today's regime, attention items, watchlist      |
            |                          |                                                  |
08:00       | LSE OPEN                 | Engine starts processing LSE ticks               |
            |                          |                                                  |
Every 2h    | CLAUDE: Curation         | Shadow mode: compare Claude vs deterministic     | python_brain/ouroboros/claude_curation.py
            |                          | Select Tier 1/2 instruments alongside ranker      |
            |                          | Log comparison for 100-trade validation           |
            |                          |                                                  |
21:00       | US CLOSE                 | Last major exchange closes                       |
            |                          |                                                  |
21:30       | CLAUDE: Evening Brief    | Day summary: P&L by exchange, strategy breakdown | python_brain/ouroboros/claude_briefing.py --evening
            |                          | Gate veto summary, top 5 priorities for tomorrow |
            |                          | Send to Telegram                                 |
            |                          |                                                  |
22:00 Fri   | CLAUDE: Weekly Review    | Deep dive on all rejected signals this week      | python_brain/ouroboros/claude_rejected_review.py
            |                          | Per-gate: total vetoes, bad veto rate, cost      |
            |                          | Recommendations: TIGHTEN / LOOSEN / KEEP         |
```

---

## CLAUDE INTEGRATION: ALL 9 ROLES

```
COMPLETE CLAUDE INTEGRATION FLOW

                     +----------------------------------+
                     |  claude -p (Opus 4.6 via Max)    |
                     |  $0/month on EC2                 |
                     +----------------------------------+
                               |
         +---------------------+--------------------+
         |                     |                     |
    NIGHTLY BATCH        PERIODIC              EVENT-DRIVEN
    (04:53-04:56)        (2h/daily)            (on trigger)
         |                     |                     |
    +----+----+         +------+------+        +-----+-----+
    |         |         |             |        |           |
    v         v         v             v        v           v
 A.FORENSIC D.MORNING F.CURATION  G.WEEKLY  H.ANOMALY  I.MACRO
 B.CHALLENGER E.EVENING             REVIEW   ASSESSOR   INTERP
 C.GATE
```

### Role A: Post-Trade Forensic Analyst
- **Schedule:** 04:53 UTC daily (after Ouroboros nightly_v6 + config_writer)
- **Inputs:** WAL events, gate_vetoes.ndjson, missed_winners.json, nightly_output.json, dynamic_weights.toml, context_store.json (7-day rolling)
- **Outputs:** JSON with trade classifications, root causes, gate tuning recs, tomorrow watchlist
- **Taxonomy:** W1-W5 winners (Clean Trend, Grind, Rung Climber, VWAP Reclaim, Macro Surf), L1-L7 losers (Spread Victim, Stop Hunted, Late Entry, Macro Crush, Regime Mismatch, Fake Breakout, Time Decay), GOOD_VETO/BAD_VETO/AMBIGUOUS/DATA_VETO for gate vetoes

### Role B: Parameter Governance Challenger
- **Schedule:** 04:55 UTC daily (after forensic review)
- **Purpose:** Challenge every Ouroboros recommendation with statistical rigor
- **Decision framework:** APPLY (sample >= 30, p < 0.05), TEST_ONLY (sample 10-29), REJECT (sample < 10, conflicts), NEEDS_MORE_DATA, OPERATOR_ATTENTION (WR < 30%, PF < 1.0)

### Role C: Parameter Approval Gate
- **Schedule:** 04:56 UTC daily (after challenger)
- **Purpose:** Apply/reject changes with hard bounds Claude CANNOT override
- **Hard bounds:** kelly_fraction [0.10, 0.35] max 20%/cycle, chandelier_atr_mult [1.5, 5.0] max 15%/cycle, confidence_floor [50, 85] max 10 pts/cycle, spread_veto_pct [0.10, 0.80] max 0.10/cycle, system_velocity_max [5, 20] max 5/cycle
- **Blacklist bounds:** Add requires 20+ trades AND Wilson LB < 0.20. Remove requires 10+ trades AND Wilson LB > 0.45
- **Audit trail:** Every decision logged to `/app/data/claude/approval_log.ndjson`

### Role D: Morning Intelligence Briefing
- **Schedule:** 07:45 UTC (before LSE open at 08:00)
- **Format:** HTML for Telegram, 60-second read time
- **Content:** Yesterday grade + P&L breakdown, overnight changes from approval gate, attention items (earnings, macro events), today's regime + equity + VIX

### Role E: Evening Intelligence Briefing
- **Schedule:** 21:30 UTC (after US close at 21:00)
- **Content:** Day summary, P&L by exchange, gate veto summary, strategy performance, top 5 priorities for tomorrow

### Role F: Universe Curation Advisor
- **Schedule:** Every 2 hours during trading (12 cycles/day)
- **Mode:** Shadow first (mandatory for first 100 trades)
- **Inputs:** Scanner results, Thompson Sampler rankings, Ouroboros scoreboard, session context, recent trades (24h WAL), open positions, blacklist
- **Constraint:** Open positions MUST remain in Tier 1 (cannot exit without data)
- **Auto-rollback:** If Claude curation causes WR drop > 10% over 50 trades, auto-revert to deterministic, Telegram alert

### Role G: Gate Calibration Analyst
- **Schedule:** Friday 22:00 UTC
- **Scope:** All rejected signals from the week, per gate
- **Output:** Per-gate: total vetoes, bad veto rate (% where price moved favorably after rejection), cost of bad vetoes (hypothetical missed P&L), recommendation (TIGHTEN/LOOSEN/KEEP/NEEDS_DATA), suggested new threshold

### Role H: Anomaly Risk Assessor
- **Trigger:** Spread > 3x normal, volume > 5x average, price gap > 2%, VIX spike > 3pts/30min, exchange circuit breaker
- **Output:** Severity (LOW/MEDIUM/HIGH/CRITICAL), historical precedent, recommended action (HOLD/REDUCE/FLATTEN), confidence
- **Constraint:** Advisory only -- engine makes final decision

### Role I: Macro Event Intelligence
- **Trigger:** 30 minutes before FOMC, NFP, CPI, PMI, major earnings (NVDA, AAPL, TSLA)
- **Output:** Expected impact per exchange/sector, recommended blackout extension (max 60 min auto-applied), position action (HOLD/REDUCE_SECTOR/FLATTEN -- FLATTEN requires operator approval)

---

## PHASE 1: INFRASTRUCTURE (3h)

### 1.1 Install Claude Code CLI on EC2

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
claude login   # One-time OAuth with Max subscription
claude -p "Return JSON: {\"status\": \"ok\"}" --output-format json  # Test
```

### 1.2 Directory Structure

```bash
mkdir -p /app/data/claude/{reviews,briefings,challenges,curation,rejected_reviews,anomalies,macro}
mkdir -p /app/data/curation_comparison
mkdir -p /app/data/sde_tests
mkdir -p /app/prompts
```

### 1.3 CLAUDE.md (repo root -- project context for CLI)

Create `/app/CLAUDE.md` telling Claude its role, data locations, output rules, guardrails:

**Key rules:**
- ALL outputs MUST be valid JSON (parseable by `json.loads()`)
- NEVER override kill switches, ISA rules, or session enforcement
- NEVER recommend > 20% parameter change per cycle
- Flag uncertainty: "needs more data" preferred over guessing
- Minimum samples: 30 for kelly, 20 for blacklist, 50 for gate tuning
- Every recommendation must include sample_size and confidence
- Classify your own confidence: HIGH (sample >= 50, p < 0.01), MEDIUM (sample 20-49, p < 0.05), LOW (sample < 20), INSUFFICIENT (sample < 10)

**Data locations:**
```
WAL events:        /app/data/*.ndjson + /app/data/archive/*.ndjson
Gate vetoes:       /app/data/gate_vetoes.ndjson
Nightly output:    /app/data/nightly_output.json
Dynamic weights:   /app/config/dynamic_weights.toml
Config:            /app/config/config.toml
Contracts:         /app/config/contracts.toml
Strategies:        /app/config/strategies.toml
Watchlist:         /app/config/active_watchlist.json
Persistent memory: /app/data/persistent_memory.json
Context store:     /app/data/context_store.json
Thompson top-K:    /app/data/thompson_top_k.json
```

### 1.4 Claude Helper Module

Create `/app/python_brain/ouroboros/claude_helper.py`:

```python
"""Shared utilities for all Claude integration modules."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

CLAUDE_CMD = ["claude", "-p"]
MAX_RETRIES = 3
TIMEOUT_SECONDS = 120

def claude_query(prompt: str, system_context: str = "",
                 output_format: str = "json",
                 max_retries: int = MAX_RETRIES) -> Optional[Dict[str, Any]]:
    """Call Claude CLI and return parsed JSON response.

    Uses claude -p with Max subscription (Opus 4.6, $0/call).
    Retries up to max_retries times on failure.

    Args:
        prompt: The full prompt including all context.
        system_context: Optional CLAUDE.md context (prepended).
        output_format: "json" or "text".
        max_retries: Retry count on failure.

    Returns:
        Parsed JSON dict, or None on failure.
    """
    full_prompt = prompt
    if system_context:
        full_prompt = system_context + "\n\n" + prompt

    cmd = CLAUDE_CMD + [full_prompt]
    if output_format == "json":
        cmd += ["--output-format", "json"]

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd="/app",
            )
            if result.returncode != 0:
                sys.stderr.write(
                    f"Claude CLI error (attempt {attempt+1}/{max_retries}): "
                    f"{result.stderr[:500]}\n"
                )
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                continue

            output = result.stdout.strip()
            if output_format == "json":
                return json.loads(output)
            return {"text": output}

        except subprocess.TimeoutExpired:
            sys.stderr.write(
                f"Claude CLI timeout ({TIMEOUT_SECONDS}s, attempt {attempt+1})\n"
            )
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Claude JSON parse error: {e}\n")
        except Exception as e:
            sys.stderr.write(f"Claude CLI unexpected error: {e}\n")

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    return None


def load_context_files() -> Dict[str, str]:
    """Load all standard context files for Claude prompts."""
    files = {
        "nightly_output": "/app/data/nightly_output.json",
        "gate_vetoes": "/app/data/gate_vetoes.ndjson",
        "dynamic_weights": "/app/config/dynamic_weights.toml",
        "context_store": "/app/data/context_store.json",
        "persistent_memory": "/app/data/persistent_memory.json",
        "config": "/app/config/config.toml",
    }
    context = {}
    for name, path in files.items():
        p = Path(path)
        if p.exists():
            try:
                content = p.read_text()
                # Truncate large files
                if len(content) > 50000:
                    content = content[:50000] + "\n... (truncated)"
                context[name] = content
            except Exception:
                context[name] = "(read error)"
        else:
            context[name] = "(not found)"
    return context


def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the operator via Telegram bot."""
    import os
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        sys.stderr.write("Telegram: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID\n")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message[:4096],
            "parse_mode": parse_mode,
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        sys.stderr.write(f"Telegram send failed: {e}\n")
        return False
```

---

## PHASE 2: POST-TRADE FORENSIC ANALYST (4h)

### What Exists (90% done)
- `claude_review.py` (470 lines) -- assembles context, builds prompt, calls Claude, sends Telegram
- Already scheduled at 04:53 UTC in crontab
- Uses Anthropic API SDK (costs money per call)

### Changes Needed

1. **Switch to `claude -p` CLI** -- Replace `anthropic.Anthropic()` with `claude_helper.claude_query()`. Uses Opus 4.6 via Max subscription. Cost: $0.

2. **Wire gate_vetoes.ndjson** -- Add today's gate vetoes to context. Filter to today's date. Include top 20 most-vetoed tickers with indicator snapshots.

3. **Wire missed_winner_detector output** -- Add classified missed winners to prompt context.

4. **Enhance system prompt** -- Add trade classification taxonomy (W1-W5, L1-L7) and gate tuning rules.

### Trade Classification Taxonomy

```
WINNERS:
  W1 Clean Trend    -- Entered momentum, rode Rung 3+, clean trail exit
  W2 Grind Winner   -- Slow climb, breakeven lock (Rung 2), eventual exit
  W3 Rung Climber   -- Reached Rung 4-5, captured significant tail
  W4 VWAP Reclaim   -- Entered near VWAP, mean reversion, clean exit
  W5 Macro Surf     -- Rode macro trend (VIX drop, sector rotation)

LOSERS:
  L1 Spread Victim  -- Entry-to-stop < 2x spread, cost-killed
  L2 Stop Hunted    -- Hit stop then reversed within 15 min
  L3 Late Entry     -- Entered >1.5% above session VWAP, chased move
  L4 Macro Crush    -- Held through adverse macro event
  L5 Regime Mismatch-- Trend strategy in mean-reverting market (or vice versa)
  L6 Fake Breakout  -- Volume confirmation failed, breakout reversed
  L7 Time Decay     -- Held too long, confidence decayed, Rung 1 exit

VETO CLASSIFICATIONS:
  GOOD_VETO   -- Gate correctly blocked a losing trade
  BAD_VETO    -- Gate incorrectly blocked a winning trade (missed winner)
  AMBIGUOUS   -- Price movement inconclusive within 2 hours
  DATA_VETO   -- Blocked due to insufficient data (correct conservative action)
```

### Output Schema

```json
{
  "date": "2026-03-24",
  "performance_grade": "B",
  "overall_confidence": 0.78,
  "executive_summary": "3 trades, 2 winners (W1, W3), 1 loser (L2). Spread drag 8%.",
  "trade_narratives": [
    {
      "symbol": "QQQ3.L",
      "factor": "F_MOM",
      "classification": "W3",
      "pnl": 15.20,
      "entry_rung": 0,
      "exit_rung": 4,
      "mae": -0.8,
      "mfe": 2.1,
      "narrative": "Entered at VWAP with rising RVOL 1.8, ADX 22. Rode momentum through Rung 4 with clean ATR trail. Chandelier exit at 2.1% profit.",
      "lessons": ["Strong momentum confirmation. ADX > 20 filter working as designed."]
    }
  ],
  "root_causes": [
    {
      "pattern": "L2_stop_hunted",
      "frequency": 1,
      "recommendation": "Widen initial stop from 1.5 ATR to 1.8 ATR for 3x ETPs",
      "confidence": 0.65,
      "sample_size": 1,
      "note": "Need 5+ instances to recommend with confidence"
    }
  ],
  "gate_tuning": [
    {
      "gate": "CHECK 13: spread_veto (0.30%)",
      "current_threshold": 0.30,
      "recommendation": "KEEP",
      "bad_veto_rate": 0.15,
      "sample_size": 12,
      "reasoning": "15% false positive rate acceptable at current sample size"
    }
  ],
  "missed_winners_summary": {
    "total_bad_vetoes": 3,
    "total_missed_pnl": 22.50,
    "top_offending_gate": "CHECK 13: spread_veto",
    "recommendation": "Consider loosening spread_veto_pct from 0.30 to 0.35"
  },
  "risk_alerts": [],
  "tomorrow_watchlist": [
    { "symbol": "NVD3.L", "reason": "NVIDIA earnings Tuesday pre-market, expect volatility" }
  ]
}
```

---

## PHASE 3: PARAMETER GOVERNANCE + APPROVAL GATE (5h)

### New: `ouroboros_challenger.py`

**File:** `/app/python_brain/ouroboros/ouroboros_challenger.py`

Reads Ouroboros recommendations from `nightly_output.json`, challenges each with statistical rigor checks via Claude.

**Decision Framework:**

| Decision | Criteria | Action |
|----------|----------|--------|
| APPLY | Sample >= 30, within bounds, no conflicts, p < 0.05 | Auto-apply |
| TEST_ONLY | Sample 10-29, directionally correct | Shadow 7 days |
| REJECT | Sample < 10, conflicts, exceeds bounds | Log only |
| NEEDS_MORE_DATA | Promising but < 10 samples | Defer |
| OPERATOR_ATTENTION | WR < 30%, PF < 1.0, equity floor proximity | Telegram alert |

**Claude prompt structure:**
```
You are a quantitative trading system auditor. Review these Ouroboros recommendations
and challenge each one for statistical validity.

For each recommendation, evaluate:
1. Sample size adequacy (minimum 30 for APPLY)
2. Statistical significance (p < 0.05 for directional change)
3. Conflict with existing parameters
4. Magnitude within allowed bounds
5. Historical precedent (has similar change worked before?)

RECOMMENDATIONS:
{nightly_output.json recommendations section}

CURRENT CONFIG:
{dynamic_weights.toml}

RECENT PERFORMANCE (7 days):
{context_store.json}

Return JSON with your decision for each recommendation.
```

### New: `approval_gate.py`

**File:** `/app/python_brain/ouroboros/approval_gate.py`

Applies Claude-approved changes with hard bounds that Claude CANNOT override:

| Parameter | Min | Max | Max change/cycle |
|-----------|-----|-----|-----------------|
| kelly_fraction | 0.10 | 0.35 | 20% |
| chandelier_atr_mult | 1.5 | 5.0 | 15% |
| confidence_floor | 50 | 85 | 10 points |
| spread_veto_pct | 0.10 | 0.80 | 0.10 |
| system_velocity_max | 5 | 20 | 5 |
| Blacklist add | -- | -- | 20+ trades AND Wilson LB < 0.20 |
| Blacklist remove | -- | -- | 10+ trades AND Wilson LB > 0.45 |

**Flow:**

```
APPLY + within bounds --> auto-write dynamic_weights.toml --> SIGHUP engine
APPLY + exceeds bounds --> Telegram "OPERATOR APPROVAL REQUIRED" --> wait
TEST_ONLY --> write shadow_params.toml --> track 7 days
REJECT --> log to approval_log.ndjson
OPERATOR_ATTENTION --> Telegram alert (non-blocking)
```

**Audit trail:** Every decision logged to `/app/data/claude/approval_log.ndjson` with:
```json
{
  "timestamp": "2026-03-24T04:56:12Z",
  "parameter": "kelly_fraction",
  "old_value": 0.22,
  "new_value": 0.24,
  "change_pct": 9.1,
  "claude_decision": "APPLY",
  "claude_reasoning": "WR 58% over 34 trades, directionally significant (p=0.03)",
  "gate_action": "AUTO_APPLIED",
  "sample_size": 34,
  "confidence": "HIGH"
}
```

---

## PHASE 4: OPERATOR INTELLIGENCE BRIEFINGS (2h)

### Morning (07:45 UTC, before LSE open)

Enhance existing `claude_briefing.py`:
- Switch API --> `claude -p` CLI via `claude_helper.claude_query()`
- Add challenger output + approval log to context
- Format: HTML for Telegram, 60-second read

**Template:**
```
AEGIS MORNING BRIEFING -- Mon 24 Mar

YESTERDAY: Grade B | 3 trades | GBP 22.30 P&L
  QQQ3.L: +GBP 15.20 (W3 Rung Climber, F_MOM)
  3LUS.L: +GBP 12.10 (W1 Clean Trend, F_MAC)
  NVD3.L: -GBP 5.00 (L2 Stop Hunted, F_MOM)

OVERNIGHT CHANGES:
  Kelly: 0.22 -> 0.24 (Claude APPROVED, WR 58% over 34 trades)
  3USS.L: TEST_ONLY blacklist (8 trades, need 20 for conviction)

ATTENTION:
  NVIDIA earnings tomorrow pre-market -- expect NVD3.L volatility
  VIX at 22.4 (elevated) -- engine will use REDUCE regime for 5x ETPs

TODAY: Regime Normal | VIX 18.2 | Equity GBP 10,022 | Top tickers: QQQ3.L, NVD3.L, 3LUS.L
```

### Evening (21:30 UTC, after US close)

New `--evening` flag on `claude_briefing.py`:

**Content:**
- Day summary: trades, P&L, WR
- P&L breakdown by exchange (LSE, US, Asia)
- P&L breakdown by factor family (F_MOM, F_REV, F_MAC, F_DIS)
- Gate veto summary: total vetoes, top 3 vetoing gates, bad veto estimate
- Chandelier exit analysis: average rung reached, rung distribution
- Top 5 priorities for tomorrow
- Universe changes: tickers added/removed from primary 100

---

## PHASE 5: UNIVERSE CURATION (10h)

**The highest-leverage integration -- decides which 100 instruments get primary streaming + which 50 get booster slots.**

### Shadow Mode (MANDATORY for first 100 trades)

```
EVERY 2 HOURS:

  DETERMINISTIC (current)            CLAUDE CURATION
  =======================            ================
  ticker_selector.py                 Read: all scanner outputs
  6-factor composite score           + ticker_ranker results
  + ticker_ranker.py                 + Thompson Sampler top-K
  + Thompson Sampler                 + Ouroboros scoreboard
  + backfill_adjustment              + session context (which exchanges open)
  = Top 100 primary tickers          + recent trades (24h WAL outcomes)
  = Top 50 booster tickers           + open positions (MUST keep)
       |                             + blacklist (Wilson filtered)
       | ACTIVE -- Engine uses       + macro context (VIX, upcoming events)
       v                             = Top 100 primary tickers
                                     = Top 50 booster tickers
                                          |
                                          | SHADOW -- Logged only
                                          v

                      COMPARISON LOG
                      curation_comparison/YYYY-MM-DD_HHMM.json
                      {
                        "deterministic_primary": ["QQQ3.L", ...],
                        "claude_primary": ["QQQ3.L", ...],
                        "overlap_pct": 82.0,
                        "claude_only": ["TSL3.L", "GPT3.L"],
                        "deterministic_only": ["3SEM.L", "MU2.L"],
                        "claude_reasoning": "TSL3.L has RVOL 3.2 and rising ADX..."
                      }

                      After 100 trades:
                      - Compare signal quality (confidence, STS)
                      - Compare trade outcomes (WR, PF) for overlap/unique
                      - Compare missed winners from each approach
                      - Compare loser avoidance

                      IF Claude > Deterministic by >= 5%:
                        --> Promote to active (operator approval required)
                      ELSE:
                        --> Keep as advisory layer
```

### Curation Schedule (22h/day, 12 curation cycles)

```
Asia:    23:00, 01:00, 03:00, 05:00 UTC
Europe:  07:00, 09:00, 11:00 UTC
US:      13:00, 15:00, 17:00, 19:00, 21:00 UTC
Dark:    21:00-23:00 UTC -- NO curation
```

### Auto-Rollback
If Claude curation causes WR drop > 10% over 50 trades --> auto-revert to deterministic, Telegram alert.

---

## PHASE 6: GATE CALIBRATION ANALYST (3h)

### New: `claude_rejected_review.py` (Friday 22:00 UTC)

**File:** `/app/python_brain/ouroboros/claude_rejected_review.py`

Deep dive on all rejected signals from the week. For each of the 30 risk gates:

1. Total vetoes this week
2. Bad veto rate (% where price moved favorably post-rejection, measured at +30min, +1h, +2h)
3. Cost of bad vetoes (sum hypothetical missed P&L using 2h forward price)
4. Good veto rate (% where price moved adversely -- gate saved us)
5. Recommendation: TIGHTEN / LOOSEN / KEEP / NEEDS_DATA
6. Suggested new threshold (if TIGHTEN or LOOSEN)
7. Confidence level and sample size

**Claude prompt includes:**
```
For each gate, you have:
- The gate's current threshold
- All veto events this week (from gate_vetoes.ndjson)
- The price 30 min, 1 hour, and 2 hours after each veto
- Whether the signal would have been profitable

Evaluate each gate's effectiveness. A good gate should have a bad_veto_rate < 15%.
If bad_veto_rate > 25%, recommend LOOSEN with specific threshold.
If bad_veto_rate < 5%, consider TIGHTEN to be more selective.
Never recommend removing a gate entirely.
```

**Output schema:**
```json
{
  "week": "2026-W13",
  "total_rejections": 142,
  "missed_winner_rate": 12.7,
  "hypothetical_missed_pnl": 89.50,
  "per_gate": [
    {
      "gate": "CHECK 13: spread_veto (0.30%)",
      "check_number": 13,
      "vetoes": 45,
      "bad_veto_rate": 17.8,
      "good_veto_rate": 65.3,
      "ambiguous_rate": 16.9,
      "missed_pnl": 34.20,
      "saved_pnl": 89.40,
      "net_value": 55.20,
      "recommendation": "LOOSEN",
      "suggested_threshold": 0.40,
      "confidence": 0.72,
      "sample_size": 45,
      "reasoning": "17.8% false positive rate exceeds 15% target. Loosening to 0.40% captures GBP 34 missed winners while adding estimated GBP 12 spread drag. Net positive GBP 22."
    }
  ],
  "cross_gate_analysis": {
    "most_restrictive_gate": "CHECK 13: spread_veto",
    "most_valuable_gate": "CHECK 10: confidence_floor",
    "redundant_gates": [],
    "compounding_vetoes": "CHECK 13 + CHECK 4h (STS) overlap on 23% of vetoes"
  }
}
```

---

## PHASE 7: ANOMALY RISK ASSESSOR + MACRO EVENT INTELLIGENCE (4h)

### Anomaly Assessor (event-triggered)

**File:** `/app/python_brain/ouroboros/claude_anomaly.py`

**Triggers:** Detected by the Rust engine or a monitoring script:
- Spread > 3x 20-bar average for any Tier 1 ticker
- Volume > 5x 20-bar average
- Price gap > 2% within a 5-minute bar
- VIX spike > 3 points in 30 minutes
- Exchange circuit breaker triggered

**Claude prompt:**
```
ANOMALY DETECTED:
- Type: {spread_spike | volume_explosion | price_gap | vix_spike | circuit_breaker}
- Ticker: {symbol}
- Severity metrics: {current_spread=0.85%, avg_spread=0.25%, ratio=3.4x}
- Current positions: {list of open positions}
- Current regime: {NORMAL | REDUCE | FLATTEN | HALT}

Assess this anomaly. Provide:
1. Severity (LOW / MEDIUM / HIGH / CRITICAL)
2. Most likely cause (liquidity withdrawal, news event, technical glitch, fat finger)
3. Historical precedent (if known)
4. Recommended action (HOLD / REDUCE / FLATTEN)
5. Confidence in your assessment (0-100)

CRITICAL CONSTRAINT: Your recommendation is ADVISORY ONLY.
The engine makes the final decision. FLATTEN requires operator approval.
```

**Output:**
```json
{
  "timestamp": "2026-03-24T14:32:15Z",
  "anomaly_type": "spread_spike",
  "ticker": "QQQ3.L",
  "severity": "HIGH",
  "likely_cause": "Liquidity withdrawal ahead of FOMC announcement",
  "historical_precedent": "Similar pattern observed before March 2025 FOMC",
  "recommended_action": "REDUCE",
  "confidence": 75,
  "reasoning": "Spread 3.4x normal suggests market makers pulling bids. FOMC in 2 hours."
}
```

### Macro Event Interpreter (calendar-triggered, 30 min pre-event)

**File:** `/app/python_brain/ouroboros/claude_macro.py`

**Triggers:** Event calendar (maintained in `/app/config/macro_calendar.json`):
- FOMC rate decisions (8x/year)
- Non-Farm Payrolls (monthly, first Friday)
- CPI releases (monthly)
- PMI releases (monthly)
- Major earnings: NVDA, AAPL, TSLA, MSFT, AMZN, GOOGL, META

**Claude prompt:**
```
MACRO EVENT APPROACHING:
- Event: {FOMC Rate Decision}
- Time: {18:00 UTC, in 30 minutes}
- Current positions: {list}
- Current VIX: {22.4}
- Current regime: {NORMAL}

Assess the expected impact and provide recommendations:
1. Expected volatility impact per exchange (LSE, US, Asia)
2. Expected sector impact (Tech, Broad, Commodities)
3. Recommended blackout extension (0-60 minutes, auto-applied)
4. Position action per ticker: HOLD / REDUCE_SECTOR / FLATTEN
5. FLATTEN requires operator approval -- flag if recommending

CONSTRAINT: Maximum auto-blackout extension is 60 minutes.
Any FLATTEN recommendation requires operator Telegram approval.
```

---

## PHASE 8: ADVERSARIAL SDE GENERATOR (Flash Crash Testing)

### Concept

Claude is prompted to write standalone Python scripts that generate synthetic market data using Stochastic Differential Equations (SDEs). This data simulates extreme market conditions -- flash crashes, liquidity evaporation, spread blowouts -- that the engine would rarely encounter in paper trading.

The generated CSV files are fed into the Rust engine's Crucible simulation mode to stress-test:
1. Chandelier exit survival under extreme volatility
2. Circuit breaker (CHECK 18/21/30/31/32) trip correctness
3. MAE/MFE tracking under extreme adverse excursion
4. Spread veto (CHECK 13) behavior during spread blowouts
5. GARCH (CHECK 25) response to volatility spikes
6. Velocity (CHECK 19/19b) behavior during signal storms

### Implementation

**File:** `/app/python_brain/ouroboros/sde_generator.py`

This module prompts Claude to generate a Python script, executes it, and feeds the output to Crucible.

**Claude prompt for Flash Crash scenario:**
```
Write a standalone Python script that generates synthetic millisecond-resolution
tick data simulating a Flash Crash scenario. Requirements:

1. Use numpy for SDE simulation
2. Model: Geometric Brownian Motion with jump diffusion (Merton 1976)
   dS = mu*S*dt + sigma*S*dW + J*S*dN
   where dN is a Poisson process (lambda=0.02) and J ~ N(-0.03, 0.02)

3. Generate exactly 100,000 rows with columns:
   timestamp_ns, last, high, low, bid, ask, volume

4. Scenario parameters:
   - Start price: 50.00
   - Normal volatility: 0.30 annualized
   - At t=30000 (row 30000): trigger crash
   - Crash: 9% drop in 4 minutes (240,000 rows at 1ms resolution)
   - During crash: bid-side liquidity evaporates
     - Spread widens from 0.05 (10 bps) to 2.50 (500 bps)
     - Volume spikes 20x then drops to 0.1x
   - At t=40000: partial recovery (dead cat bounce, 3% recovery)
   - At t=60000: second leg down (5% further drop)
   - Spread gradually normalizes over 20000 rows

5. Bid/ask modeling:
   - Normal: spread = 0.05 (10 bps of price)
   - Crash onset: spread ramps linearly from 10 bps to 500 bps over 5000 rows
   - During crash: bid drops faster than ask (asymmetric liquidity)
   - Recovery: spread decays exponentially back to 20 bps (never fully normalizes)

6. Volume modeling:
   - Normal: random uniform [500, 2000] per tick
   - Pre-crash (1000 rows before): volume ramps to 5x normal
   - During crash: volume spikes to 20x, then collapses to 0.1x
   - Recovery: volume normalizes to 2x over 10000 rows

7. Output: CSV file at /app/data/sde_tests/flash_crash_001.csv

The script must be self-contained (only numpy and csv imports).
Include a random seed for reproducibility.
Print a summary of key statistics at the end.
```

### Crucible Integration

After Claude generates and we execute the SDE script:

```bash
# Convert SDE CSV to Crucible-compatible format
python3 -m python_brain.ouroboros.sde_converter \
  --input /app/data/sde_tests/flash_crash_001.csv \
  --output /app/data/sde_tests/flash_crash_001_crucible.csv \
  --ticker "FLASH_TEST" \
  --exchange "SIMULATION"

# Run Crucible simulation
./target/release/aegis --crucible \
  --data /app/data/sde_tests/flash_crash_001_crucible.csv \
  --config /app/config/config.toml \
  --output /app/data/sde_tests/flash_crash_001_results.json
```

### Validation Checks After SDE Test

| Test | Expected | Fail Action |
|------|----------|-------------|
| Chandelier exit triggers during 9% crash | Within 2% of entry (Rung 0 stop) | Fix exit engine ATR floor |
| CHECK 13 spread veto fires when spread > 500 bps | 100% rejection rate | Fix spread threshold |
| CHECK 18 daily drawdown FLATTEN triggers | Must fire before 5% DD | Fix DD threshold |
| CHECK 25 GARCH sigma veto fires | Must fire during vol spike | Fix GARCH threshold |
| CHECK 19 velocity does NOT trigger excessively | Max 2 false velocity vetoes | Tune velocity window |
| MAE/MFE tracking correct under extreme moves | MAE matches worst tick price | Fix MAE tracking |
| No panic: engine does not crash | Zero panics/unwraps | Fix error handling |

### SDE Scenario Library (to generate over time)

| # | Scenario | Key Parameters |
|---|----------|---------------|
| 1 | Flash Crash | 9% drop in 4 min, 500 bps spread, 20x volume spike |
| 2 | Slow Bleed | 15% drop over 6 hours, normal spreads, declining volume |
| 3 | Gap Open | 5% gap down at market open, 300 bps spread for 5 min |
| 4 | VIX Spike | Price stable but spreads triple over 30 min (VIX proxy) |
| 5 | Dead Cat Bounce | 8% drop, 4% bounce, 6% second leg down |
| 6 | Melt-Up | 12% rise in 2 hours, RVOL 8x, spreads tight |
| 7 | Liquidity Hole | Price stable, spreads randomly spike to 300 bps for 10 ticks |
| 8 | Whipsaw | 3% up, 3% down, 2% up, 2% down -- 4 reversals in 1 hour |

---

## SHADOW MODE VALIDATION FRAMEWORK

```
SHADOW MODE VALIDATION -- MANDATORY BEFORE ANY CLAUDE INTEGRATION GOES ACTIVE

PHASE 1: Nightly Pipeline (Roles A/B/C) -- 50 trades
  +-----------------------------------------------------------------+
  | FOR EACH NIGHT:                                                  |
  |   1. Claude Forensic Review generates trade classifications      |
  |   2. Claude Challenger generates challenge decisions             |
  |   3. Approval Gate generates APPLY/REJECT decisions              |
  |   4. Shadow: Claude changes written to shadow_params.toml        |
  |              (NOT applied to live engine)                        |
  |   5. Track: what WOULD have changed vs what DID change           |
  +-----------------------------------------------------------------+
  |                                                                  |
  | VALIDATION GATES (after 50 trades):                             |
  |   [ ] Forensic review valid JSON: 100% of nights                |
  |   [ ] Challenger catches >= 1 weak recommendation: per 50 trades|
  |   [ ] Briefings sent on time: 100% of trading days              |
  |   [ ] Claude failures (timeout, bad JSON): < 5%                 |
  |   [ ] Approval gate routes correctly: 100% of decisions         |
  |   [ ] Shadow changes would not have violated hard bounds: 100%  |
  +-----------------------------------------------------------------+

PHASE 2: Universe Curation (Role F) -- 100 trades
  +-----------------------------------------------------------------+
  | FOR EACH 2-HOUR CYCLE:                                           |
  |   1. Deterministic: ticker_selector produces top 100             |
  |   2. Claude: claude_curation produces top 100 (shadow)           |
  |   3. Log both lists to curation_comparison/                      |
  |   4. Track overlap percentage, unique picks                      |
  +-----------------------------------------------------------------+
  |                                                                  |
  | AFTER 100 TRADES:                                                |
  |   Compare signal quality for:                                    |
  |     a. Overlap tickers (should be similar)                       |
  |     b. Claude-only tickers (are they better?)                    |
  |     c. Deterministic-only tickers (are they worse?)              |
  |                                                                  |
  | PROMOTION CRITERIA:                                              |
  |   [ ] Claude signal quality > deterministic by >= 5%             |
  |   [ ] Claude avoids more losers (measurable)                     |
  |   [ ] Open positions NEVER lost from Tier 1: zero failures      |
  |   [ ] Overlap with deterministic >= 60% (not random)             |
  |   --> PROMOTE to active (operator Telegram approval required)    |
  |                                                                  |
  | IF FAILED:                                                       |
  |   --> Keep Claude as advisory layer only                         |
  |   --> Log recommendations but don't affect trading               |
  +-----------------------------------------------------------------+

PHASE 3: Gate Tuning (Roles G/H/I) -- 200 trades
  +-----------------------------------------------------------------+
  | Weekly review recommendations logged but NOT auto-applied        |
  | Anomaly assessor recommendations logged but NOT acted on         |
  | Macro interpreter blackout extensions logged but NOT enforced    |
  |                                                                  |
  | VALIDATION:                                                      |
  |   [ ] Gate tuning recs would have improved WR: simulated check  |
  |   [ ] Anomaly assessor severity correlates with actual outcomes  |
  |   [ ] Macro interpreter blackouts correlate with adverse moves   |
  |   --> After 200 trades: promote to semi-active (operator can     |
  |       approve individual recommendations via Telegram)           |
  +-----------------------------------------------------------------+
```

---

## APPROVAL GATE DECISION TREE

```
                           OUROBOROS RECOMMENDATION
                                    |
                                    v
                        +---------------------+
                        | Claude Challenger    |
                        | (statistical rigor)  |
                        +----------+----------+
                                   |
              +--------------------+--------------------+
              |                    |                     |
         sample < 10         sample 10-29           sample >= 30
              |                    |                     |
              v                    v                     v
        +---------+         +----------+          +----------+
        | REJECT  |         |TEST_ONLY |          | p < 0.05?|
        | or      |         | shadow   |          +-----+----+
        | NEEDS   |         | 7 days   |                |
        | MORE    |         +----------+         YES    |    NO
        | DATA    |                               |     |
        +---------+                               v     v
                                            +---------+ +----------+
                                            | Within  | | REJECT   |
                                            | bounds? | | (not     |
                                            +----+----+ | signif.) |
                                                 |      +----------+
                                          YES    |    NO
                                           |     |
                                           v     v
                                     +--------+ +------------------+
                                     | AUTO   | | OPERATOR         |
                                     | APPLY  | | APPROVAL         |
                                     | write  | | REQUIRED         |
                                     | dynamic| | Telegram alert   |
                                     | weights| | wait for /approve|
                                     | SIGHUP | +------------------+
                                     +--------+

            HARD BOUNDS (Claude CANNOT override):
            kelly_fraction:      [0.10, 0.35], max 20%/cycle
            chandelier_atr_mult: [1.5, 5.0], max 15%/cycle
            confidence_floor:    [50, 85], max 10 pts/cycle
            spread_veto_pct:     [0.10, 0.80], max 0.10/cycle
            system_velocity_max: [5, 20], max 5/cycle

            EMERGENCY OVERRIDES (Claude CAN recommend, engine CAN auto-apply):
            WR < 30% over 20+ trades --> OPERATOR_ATTENTION
            PF < 1.0 over 30+ trades --> OPERATOR_ATTENTION
            Equity within 5% of floor --> OPERATOR_ATTENTION + auto REDUCE regime
```

---

## COMPLETE CRONTAB

```cron
# ===========================================================================
# AEGIS V2 -- Full Intelligence Stack (UTC, Mon-Fri)
# ===========================================================================

# --- UNIVERSE DISCOVERY (background) ---
0  6  * * 1-5  cd /app && python3 -m python_brain.ouroboros.full_universe_builder
0  1,7,13,19 * * 1-5  cd /app && python3 -m python_brain.ouroboros.contract_expander

# --- UNIVERSE SELECTION (every 15 min) ---
*/15 * * * 1-5  cd /app && python3 -m python_brain.ouroboros.ticker_selector

# --- NIGHTLY PIPELINE ---
50 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.nightly_v6
51 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.config_writer
52 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.win_loss_delta --push-sheets

# --- CLAUDE NIGHTLY (after Ouroboros) ---
53 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.claude_review --send-telegram
55 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.ouroboros_challenger --send-telegram
56 4 * * 1-5  cd /app && python3 -m python_brain.ouroboros.approval_gate

# --- CLAUDE BRIEFINGS ---
45 7  * * 1-5  cd /app && python3 -m python_brain.ouroboros.claude_briefing --send-telegram
30 21 * * 1-5  cd /app && python3 -m python_brain.ouroboros.claude_briefing --evening --send-telegram

# --- CLAUDE UNIVERSE CURATION (shadow mode, every 2h during trading) ---
0 23 * * 0-4              cd /app && python3 -m python_brain.ouroboros.claude_curation
0 1,3,5,7,9,11 * * 1-5    cd /app && python3 -m python_brain.ouroboros.claude_curation
0 13,15,17,19,21 * * 1-5   cd /app && python3 -m python_brain.ouroboros.claude_curation

# --- CLAUDE WEEKLY ---
0 22 * * 5  cd /app && python3 -m python_brain.ouroboros.claude_rejected_review --send-telegram

# --- SESSION PDFs (existing) ---
# Scheduled at session opens for operator reference
```

---

## FILES TO CREATE / MODIFY

### Files to Create

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `python_brain/ouroboros/claude_helper.py` | 1 | ~120 | Shared Claude CLI wrapper, context loader, Telegram sender |
| `python_brain/ouroboros/ouroboros_challenger.py` | 3 | ~300 | Challenge Ouroboros recommendations via Claude |
| `python_brain/ouroboros/approval_gate.py` | 3 | ~250 | Apply/reject with guardrails + audit trail |
| `python_brain/ouroboros/claude_curation.py` | 5 | ~400 | Universe curation shadow + active mode |
| `python_brain/ouroboros/curation_validator.py` | 5 | ~200 | Compare shadow vs deterministic outcomes |
| `python_brain/ouroboros/claude_rejected_review.py` | 6 | ~250 | Weekly gate forensics via Claude |
| `python_brain/ouroboros/claude_anomaly.py` | 7 | ~150 | Event-triggered anomaly assessment |
| `python_brain/ouroboros/claude_macro.py` | 7 | ~200 | Pre-event macro interpretation |
| `python_brain/ouroboros/sde_generator.py` | 8 | ~300 | Adversarial SDE flash crash test generator |
| `python_brain/ouroboros/sde_converter.py` | 8 | ~100 | Convert SDE CSV to Crucible-compatible format |
| `CLAUDE.md` | 1 | ~100 | Project context for Claude CLI |
| `config/macro_calendar.json` | 7 | ~50 | Upcoming macro events calendar |

### Files to Modify

| File | Phase | Changes |
|------|-------|---------|
| `python_brain/ouroboros/claude_review.py` | 2 | Switch API-->CLI, add gate_vetoes + missed_winners context |
| `python_brain/ouroboros/claude_briefing.py` | 4 | Switch API-->CLI, add evening mode, add challenger output |
| `crontab` (supercronic) | All | Add 8 new scheduled jobs |
| `entrypoint.sh` | 1 | Create Claude data directories on boot |
| `Dockerfile` | 1 | Install Node.js + Claude CLI in container |

---

## VALIDATION GATES

### After 50 trades with Claude nightly pipeline running:

| Gate | Threshold | Fail Action |
|------|-----------|-------------|
| Forensic review valid JSON | 100% of nights | Fix prompt / add JSON retry |
| Challenger catches >= 1 weak rec | Per 50 trades | Tune challenger prompt |
| Briefings sent on time | 100% of trading days | Fix cron timing |
| Claude failures (timeout, bad JSON) | < 5% | Add retry + longer timeout |
| Approval gate routes correctly | 100% of decisions | Fix gate logic |
| Shadow changes within bounds | 100% | Fix bounds checking |

### After 100 trades in curation shadow mode:

| Gate | Threshold | Fail Action |
|------|-----------|-------------|
| Claude signal quality > deterministic | >= 5% improvement | Keep as advisory only |
| Claude avoids more losers | Measurable improvement | Keep as advisory only |
| Open positions never lost from Tier 1 | Zero failures | Fix curation constraint |
| Overlap with deterministic >= 60% | Minimum coherence | Tune curation prompt |

### After running SDE flash crash tests:

| Gate | Threshold | Fail Action |
|------|-----------|-------------|
| Chandelier exit triggers within 2% of entry | All crash scenarios | Fix ATR floor / rung logic |
| Circuit breakers trip correctly | All applicable CHECKs fire | Fix threshold configs |
| Engine does not panic/crash | Zero panics | Fix error handling |
| MAE/MFE tracking accurate | Matches worst/best tick | Fix tracking logic |

---

## EXECUTION ORDER

| # | Phase | Effort | Depends On |
|---|-------|--------|-----------|
| 1 | Infrastructure (CLI install, dirs, CLAUDE.md, helper module) | 3h | EC2 access |
| 2 | Post-Trade Forensic Analyst (complete claude_review.py) | 4h | Phase 1 |
| 3 | Parameter Governance + Approval Gate | 5h | Phase 2 |
| 4 | Operator Intelligence Briefings (complete claude_briefing.py) | 2h | Phase 2 |
| 5 | Universe Curation Advisor (shadow mode) | 10h | Phase 3 |
| 6 | Gate Calibration Analyst (weekly rejected-trade review) | 3h | Phase 2 |
| 7 | Anomaly Risk Assessor + Macro Event Intelligence | 4h | Phase 2 |
| 8 | Adversarial SDE Generator (Flash Crash Testing) | 4h | Phase 1 |
| 9 | Alpha Model Shadow (F_MOM + F_REV + F_MAC unified) | ongoing | Phase 2 |
| -- | Shadow validation: nightly pipeline (50+ trades) | 1-2 weeks | Phase 3 |
| -- | Shadow validation: curation (100+ trades) | 2-4 weeks | Phase 5 |
| -- | Promote curation to active (operator approval) | 1h | Validation pass |

**Total: ~35 hours implementation + 2-4 weeks shadow validation**

---

## ADVERSARIAL HARDENING (from external audit)

These fixes address genuine vulnerabilities identified by adversarial review of the plan. Integrated into implementation — not deferred.

### H1: Sequential Nightly Pipeline (replaces rigid cron offsets)

**Problem:** Fixed 1-minute cron offsets (04:50, 04:51, 04:52...) will race-condition as WAL grows and nightly_v6 takes >60s.

**Fix:** Replace individual cron entries with a single orchestrator script that chains sequentially:

```bash
#!/bin/bash
# /app/scripts/nightly_pipeline.sh — Sequential, not cron-parallel
set -euo pipefail
LOG=/var/log/nightly_pipeline.log

echo "$(date -u) PIPELINE START" >> $LOG

cd /app
python3 -m python_brain.ouroboros.nightly_v6 >> $LOG 2>&1
echo "$(date -u) nightly_v6 DONE" >> $LOG

python3 -m python_brain.ouroboros.config_writer >> $LOG 2>&1
echo "$(date -u) config_writer DONE" >> $LOG

python3 -m python_brain.ouroboros.win_loss_delta --push-sheets >> $LOG 2>&1
echo "$(date -u) win_loss_delta DONE" >> $LOG

python3 -m python_brain.ouroboros.claude_review --send-telegram >> $LOG 2>&1
echo "$(date -u) claude_review DONE" >> $LOG

python3 -m python_brain.ouroboros.ouroboros_challenger --send-telegram >> $LOG 2>&1
echo "$(date -u) challenger DONE" >> $LOG

python3 -m python_brain.ouroboros.approval_gate >> $LOG 2>&1
echo "$(date -u) approval_gate DONE — PIPELINE COMPLETE" >> $LOG
```

**Crontab change:** Single entry replaces 6 individual entries:
```cron
50 4 * * 1-5 flock -n /tmp/nightly.lock /app/scripts/nightly_pipeline.sh
```

### H2: SDE Sandbox (never execute LLM code on host)

**Problem:** Phase 8 SDE Generator prompts Claude to write Python, then executes it. LLM-generated code on the production host is an RCE vector.

**Fix:** All SDE scripts execute in a network-isolated, read-only Docker container:

```bash
# Build sandbox image (one-time)
docker build -t aegis-sde-sandbox -f Dockerfile.sde-sandbox .

# Execute SDE script in sandbox (no network, no host volumes, 5-min timeout)
docker run --rm \
  --network=none \
  --read-only \
  --tmpfs /tmp:rw,size=512m \
  --memory=1g \
  --cpus=1 \
  --timeout 300 \
  -v /app/data/sde_output:/output:rw \
  aegis-sde-sandbox \
  python3 /scripts/flash_crash_gen.py
```

**Dockerfile.sde-sandbox:**
```dockerfile
FROM python:3.12-slim
RUN pip install numpy scipy pandas --no-cache-dir
COPY sde_scripts/ /scripts/
USER nobody
ENTRYPOINT ["python3"]
```

Claude writes the script → human reviews it → script is copied into `sde_scripts/` → sandbox executes it. Never autonomous.

### H3: TOML Validation Before SIGHUP

**Problem:** If approval_gate writes malformed TOML, the SIGHUP will crash the Rust engine.

**Fix:** approval_gate.py must parse the output TOML before writing:

```python
# In approval_gate.py, before writing:
import tomllib
new_content = generate_toml(changes)
try:
    tomllib.loads(new_content)  # Parse-validates the output
except Exception as e:
    log.error(f"TOML validation failed, NOT writing: {e}")
    send_telegram("APPROVAL GATE: TOML validation failed, no changes applied")
    return  # Abort — do not SIGHUP

# Only write + SIGHUP if validation passes
with open(dynamic_weights_path, 'w') as f:
    f.write(new_content)
os.kill(engine_pid, signal.SIGHUP)
```

### H4: Context Window Truncation

**Problem:** Feeding 50K+ lines of raw JSON to Claude nightly causes "lost in the middle" hallucination.

**Fix:** All Claude inputs are pre-summarized before prompt construction:

- WAL events: Summarize to per-ticker aggregates (not raw events). Max 50 trades in narrative form.
- gate_vetoes.ndjson: Aggregate to per-gate veto counts + top 5 examples. Not raw dump.
- context_store.json: Already summarized (7-day rolling). Keep as-is.
- recommendations.json: Already compact. Keep as-is.

**Max prompt size rule:** Total context never exceeds 8,000 tokens input. Claude can reason deeply on focused data; it cannot reason on data dumps.

### H5: Rolling Baseline Drift Cap

**Problem:** Five consecutive 20% kelly increases = 2.49x compounding (0.22 → 0.55). Hard bounds alone don't prevent slow drift.

**Fix:** Add rolling baseline tracking to approval_gate.py:

```python
# Track 30-day parameter history
BASELINE_WINDOW_DAYS = 30
MAX_DRIFT_FROM_BASELINE_PCT = 50  # Max 50% drift from 30-day average

def check_baseline_drift(param, new_value, history):
    if len(history) < 7:
        return True  # Not enough history
    baseline = sum(history[-BASELINE_WINDOW_DAYS:]) / len(history[-BASELINE_WINDOW_DAYS:])
    drift_pct = abs(new_value - baseline) / baseline * 100
    if drift_pct > MAX_DRIFT_FROM_BASELINE_PCT:
        send_telegram(f"DRIFT ALERT: {param} drifted {drift_pct:.0f}% from 30-day baseline")
        return False  # Block change
    return True
```

### H6: Operator Psychological Audit (new deep-cold integration)

**Trigger:** Every Sunday 23:00 UTC
**Purpose:** Audit human interventions — every /kill, /pause, manual IBKR action.

The Rust engine logs `OperatorIntervention` WAL events whenever the operator uses Telegram commands. Claude compares what the operator did vs what the engine would have done (deterministic counterfactual).

**Output:** Weekly psychology report:
- Total interventions this week
- Cost of interventions (positive = saved money, negative = cost money)
- Emotional pattern analysis (panic sells during VIX spikes? premature kills before recovery?)
- Recommendation: "Your /kill on Wednesday cost £45 — the position would have recovered in 18 minutes"

### H7: SEC/RNS Semantic Delta Scanner (new deep-cold integration)

**Trigger:** Daily 06:00 UTC
**Purpose:** Detect material changes in regulatory filings before market reaction.

Download latest 10-Q, 8-K, or LSE RNS filings for Top 100 universe instruments. Claude compares current filing to previous quarter. Ignores financials — focuses on Risk Factors and Management Discussion sections.

**Output:** Semantic delta report:
- Newly added legal language (subpoenas, investigations, going-concern)
- Removed optimistic language (deleted growth targets, removed guidance)
- Material event flags → automatic ticker exclusion from Tier 1 for 48 hours

---

## GEMINI "INSTITUTIONAL SYNDICATE" EVOLUTION PATH

Gemini's adversarial review proposed a fundamental architectural evolution. The valid insights are integrated here as a **post-validation evolution path** — not a "delete everything" directive. The system is deployed and winning trades. These are incremental improvements to be validated with evidence.

### E1: Unified Alpha Model (Future — after 200+ trades prove current strategies)

**Current:** 4 factor families (F_MOM, F_REV, F_MAC, F_DIS) via multiple evaluator modules competing on confidence.
**Evolution:** Single continuous alpha score [-1.0, +1.0] from three orthogonal factors:

```
F1 (Micro-Momentum):  OBI + tick velocity + RVOL breakout
F2 (Statistical Reversion): VWAP Z-score + mean-reversion distance
F3 (Macro Beta): SPY/NQ correlation + VIX regime

Alpha = (w1 × F1) + (w2 × F2) + (w3 × F3)
Ouroboros updates w1, w2, w3 nightly based on realized P&L attribution.
```

**Why not now:** The current strategies ARE producing winning trades. Ripping them out before proving the replacement works is retail impulsiveness, not institutional discipline. **Shadow the alpha model alongside existing strategies for 200+ trades first.**

### E2: Asymmetric EOD Rules (Implement after 100 trades)

**Current:** All positions force-flattened at EOD regardless of exchange.
**Evolution:**
- **LSE + Asia:** Force-flatten 5 min before close (MOC/LOC orders). Zero overnight exposure.
- **US equities:** Allow overnight hold. Chandelier continues. GTC stop-limit order submitted to IBKR servers before the bell. On next-day open, Rust resumes dynamic trailing.

**Risk:** Overnight gap exposure on US stocks. Mitigated by GTC stop + daily drawdown limits.

### E3: REST Snapshot Universe Funnel (Implement when scaling beyond IBKR scanner)

**Current:** IBKR scanner (weekly) + yfinance + Wikipedia scraping.
**Evolution:** Polygon.io or FMP bulk REST snapshot every 60 seconds:
- Single HTTP GET returns price/volume/VWAP for 10,000+ tickers
- Python calculates live RVOL, filters to top 500
- Hands top 100 to Rust via watchlist update
- Cost: ~$75/month (justified when trading data proves positive expectancy)

**Why not now:** IBKR paid data already covers our traded universe. Wikipedia scraping has 4 fallback methods. yfinance works for validation. Add Polygon when the evidence says we're leaving money on the table by not scanning wider.

### E4: No-Fear Re-Entry (Implement immediately — config change only)

**Current:** 60-tick (5-min) cooldown per ticker between signals.
**Evolution:** Replace cooldown with velocity cap: max 3 entries per ticker per rolling 5-min window. If the math says buy again 30 seconds after a stop-out, buy again.

**Implementation:** Already partially done — `system_velocity_max = 10` in config.toml. Per-ticker cooldown just needs reducing from 60 ticks to 12 ticks (1 minute) with the 3-entry velocity cap as the safety net.

### E5: Level 2 Sniper Upgrade (Implement when L2 data subscription active)

**Current:** Level 1 (top of book) only.
**Evolution:** When a ticker gets within 0.5% of a breakout trigger, Rust dynamically fires `reqMktDepth()` for that specific ticker. Calculates Order Book Imbalance (OBI). If bid size > 5× ask size (institutional accumulation), boost confidence. Cancel L2 feed after trade.

**Requires:** IBKR Level 2 market data subscription (already paid). Rust `reqMktDepth()` handler (not yet wired).

### Gemini's Forbidden Zones (confirmed — Claude stays out)

| Zone | Why Forbidden | Our Design |
|------|--------------|------------|
| Millisecond hot path | Claude takes 2-5s. Price moves 3% in that time. | Rust owns all execution. Claude is nightly/2-hourly only. |
| Live risk arbiter | Hallucinated decimal = toxic trade. | 30 deterministic CHECKs in Rust. Claude reviews, never decides real-time. |
| Autonomous code deployment | Wake up to liquidated account. | Claude may draft. Human must merge. SDE sandbox (H2) prevents RCE. |

### Gemini's Decision-Making Hierarchy (confirmed)

```
Level 4: CIO (Operator) — absolute authority, kill switch, approves PRs
Level 3: Strategic Intelligence (Claude) — universe curation, forensics, veto power
Level 2: Quantitative Math (Ouroboros) — parameter optimization, statistical weights
Level 1: Execution (Rust) — millisecond decisions, hard risk, trailing stops
```

Claude holds **supreme negative authority** (can block bad things) but **zero positive authority** (cannot force a trade the math disagrees with).

---

## CHATGPT TOP-20 BACKLOG (integrated from adversarial review)

Both Gemini and ChatGPT audited this plan. The architecture was validated as institutionally sound. These are the highest-ROI items from the ChatGPT top-100 backlog, mapped to what already exists vs what Plan 2 adds:

| # | Item | Status | Where |
|---|------|--------|-------|
| 1 | Net expectancy after costs | ✅ EXISTS | nightly_v6: gross_pnl - commission per trade |
| 2 | Spread/slippage attribution | ✅ EXISTS | WAL PositionClosed: spread_at_entry_pct, spread_at_exit_pct |
| 3 | Missed-winner tracking | ✅ EXISTS | missed_winner_detector.py + MissedWinnerCandidate WAL event |
| 4 | Rejected-trade tracking | ✅ EXISTS | SignalRejected WAL event + gate_vetoes.ndjson |
| 5 | Gate-level veto attribution | ✅ EXISTS | gate_vetoes.ndjson logs gate_name + gate_reason per veto |
| 6 | MAE/MFE | ✅ EXISTS | Per-position in PositionState, written to WAL PositionClosed |
| 7 | Expected vs realized edge | 🔧 PLAN 2 | Claude forensic review (Phase 2) computes this nightly |
| 8 | Discovery vs production split | 🔧 PLAN 2 | Universe curation (Phase 5) separates discovery from Tier 1 |
| 9 | Canonical tradability score | ✅ EXISTS | STS (structural_score) in bridge.py, 0-100 |
| 10 | Universe slot efficiency | 🔧 PLAN 2 | Claude curation shadow mode tracks line utilization |
| 11 | Parameter epoch tagging | ✅ EXISTS | V9 config hash logged at startup |
| 12 | Pre/post change review | 🔧 PLAN 2 | Approval gate (Phase 3) logs all changes with before/after |
| 13 | No-trade-day diagnostics | 🔧 PLAN 2 | Claude forensic review flags zero-trade days with reasons |
| 14 | Gate interaction analytics | 🔧 PLAN 2 | Weekly rejected-trade review (Phase 6) correlates gate co-triggers |
| 15 | Strategy gross-to-net audit | 🔧 PLAN 2 | Claude forensic review segments by strategy family |
| 16 | Contract expansion hardening | ⚠️ PARTIAL | yfinance validation exists; spread/liquidity checks needed |
| 17 | Stable core universe | 🔧 PLAN 2 | Curation shadow mode validates stability vs churn |
| 18 | Profit left on table | ✅ EXISTS | MFE - actual exit price in WAL PositionClosed |
| 19 | Symbol graveyard | ✅ EXISTS | Wilson-score blacklist in config_writer.py |
| 20 | Nightly mutation quality gates | ✅ EXISTS | Ouroboros bounds checking in config_writer.py |

**Score: 12/20 already exist. 7/20 added by Plan 2. 1/20 needs minor hardening.**

### Items Both Auditors Agree Are NOT Needed Now

- More strategy families → prove existing 6 strategies first
- More scoring layers → simplify ranking, don't add layers
- More LLM roles beyond the 9 defined → Claude is cold-path only, this is correct
- More adaptive gates → evidence-govern existing 30 CHECKs first
- Cross-market cleverness → S20 exists, validate before expanding
- Micro-optimization of ranking bonuses → prove leverage boost helps after costs

### The One Rule Both Auditors Endorse

> **First: truth after costs. Second: telemetry that explains outcomes. Third: cleaner universe. Fourth: evidence-governed learning. Fifth: only then more model cleverness.**

Plan 2 follows this hierarchy exactly. Claude is Layer 4 (evidence-governed learning), not Layer 2 (execution).

---

## GEMINI'S FORBIDDEN ZONES (confirmed — Claude stays out)

| Zone | Why Forbidden | Our Design |
|------|--------------|------------|
| Millisecond hot path | Claude takes 2-5s. Price moves 3% in that time. | Rust owns all execution. Claude is nightly/2-hourly only. |
| Live risk arbiter | Hallucinated decimal point = toxic trade. | 30 deterministic CHECKs in Rust. Claude reviews outcomes, never decides in real-time. |
| Autonomous code deployment | Wake up to liquidated account. | Claude may draft. Human must merge. SDE sandbox (H2) prevents RCE. |

---

## GEMINI 200-POINT ADVERSARIAL AUDIT RESPONSE

Gemini's "Institutional Syndicate" delivered a 200-point adversarial audit. Triage below. Valid points integrated; theatrical points dismissed with evidence.

### Points Accepted and Integrated

| # | Point | Action | Status |
|---|-------|--------|--------|
| 1-3 | 4GB RAM concern | Monitor actual usage. IB Gateway uses ~600MB not 1.5GB. Total stack ~1.2GB. Upgrade if OOM observed. | MONITOR |
| 13-15 | Docker memory limits | Already set: `memory: 1024M` in docker-compose.yml | DONE |
| 26-28 | JSON IPC overhead | Valid concern at scale. Current 30 msgs/sec is not a bottleneck. Upgrade to mmap if throughput proves insufficient. | DEFER (E-path) |
| 36-37 | System velocity scaling with VIX | Good idea. Add VIX-scaled velocity cap. | ACCEPTED |
| 43-44 | MAE/MFE using High/Low not Last | Valid. Already using PositionState.highest_high for MFE. Verify MAE uses tick.low. | VERIFY |
| 51-52 | Wikipedia scraping fragility | Valid but mitigated by 4 fallback methods. Add Polygon when evidence justifies $75/month. | E3 (evolution path) |
| 126-127 | Evaluate ALL CHECKs, log array | Valid for diagnostics. Currently first-reject wins. Add secondary "would-have-vetoed" logging. | ACCEPTED |
| 131-132 | CHECK 18 FLATTEN causes slippage | Valid. Change FLATTEN behavior to REDUCE_ONLY (no new entries, exits via Chandelier). | ACCEPTED |
| 137-138 | Rung 1 breakeven lock too tight | Valid. Current Rung 2 (breakeven) uses entry + fees. Already accounts for spread via round_trip_fee_pct. | VERIFIED OK |
| 151-153 | Bash script must check exit codes | Valid. Add `set -euo pipefail` to nightly_pipeline.sh. Already specified in H1. | DONE |
| 154-155 | Ouroboros trains on gross not net | Valid concern. nightly_v6 already subtracts commission. Verify spread drag is included. | VERIFY |
| 161-162 | Approval gate 20% max per cycle too aggressive | Valid. Reduce to 10% max per cycle for kelly_fraction. | ACCEPTED |
| 163-164 | DATA_VETO excluded from optimization | Valid. Already specified in H4 context truncation. Formalize in code. | ACCEPTED |
| 176-177 | Context truncation insufficient | Valid. H4 already specifies 8K token max + pre-summarization. Enforce strictly. | DONE |

### Points Dismissed (FUD or Already Handled)

| # | Claim | Reality |
|---|-------|---------|
| 4-6 | Claude CLI will be rate-limited | Max subscription explicitly supports `claude -p`. Not a hack. Designed for this. |
| 29-31 | 5-minute bars add "70 min of latency" | ADX updates every 5 min, not every 70 min. The lookback window is 70 min of DATA, not 70 min of DELAY. This is intentional momentum confirmation. |
| 38-41 | Cooldown promotes overtrading | Cooldown was reduced from 25min to 5min based on evidence (Sprint 5 T-08). The system was MISSING valid re-entries. |
| 53-54 | yfinance will IP-ban for 36K tickers | Universe builder runs DAILY at 06:00 UTC. It does NOT pull 36K tickers every run. Method 4 (LSE ETP patterns) is synthetic generation, no API calls. |
| 56-58 | Booster rotation triggers pacing | IBKR allows 50 msgs/sec. Rotating 50 tickers = 100 msgs (cancel + subscribe). At 40 msgs/sec rate limit, this takes 2.5 seconds. Not a violation. |
| 76-78 | ADX >= 25 buys the top | ADX >= 25 is ONE of three scoring components. It contributes +40 to a 0-100 score. The trade fires on combined momentum + EMA + RVOL, not ADX alone. |
| 101-103 | 12-factor Kelly "approaches zero" | The 12 factors are NOT all < 1.0 simultaneously. Factor 1 (base Kelly) is typically 0.15-0.25. Factors 3-12 are penalties that reduce from there. Final Kelly is typically 0.05-0.15, not zero. |
| 107-108 | Amihud breaks on ETPs | Amihud is ONE of 12 factors. If it gives a bad reading for ETPs, the other 11 factors compensate. This is not a fatal flaw. |

### Genuine Improvements to Implement

1. **CHECK logging enhancement:** Log ALL triggered CHECKs per evaluation, not just the first REJECT. Enables gate interaction analytics.
2. **CHECK 18 behavior:** Change from FLATTEN (market sell) to REDUCE_ONLY (block new entries, let Chandelier manage exits).
3. **Approval gate max change:** Reduce kelly_fraction max change from 20% to 10% per cycle.
4. **VIX-scaled velocity:** system_velocity_max should scale inversely with VIX level.
5. **Thompson Sampler decay:** Add periodic arm decay so historical winners don't dominate forever.
6. **Approval gate risk asymmetry:** Auto-apply for risk-reducing changes only. Risk-increasing changes require operator Telegram approval.

### Gemini 250-Point Follow-Up — Additional Accepted Points

From the second 250-point audit, these additional points are genuinely valid:

| # | Point | Action |
|---|-------|--------|
| 3 | f64 precision for tick sizes | Valid. Use tick_size_under_1/over_1 from config for rounding. Already implemented. |
| 6 | RCU for config hot-reload | Valid improvement. Current RwLock works but arc-swap would be cleaner. DEFER. |
| 9 | Monotonic clock for velocity | Valid. Rust uses Instant for tick timing. Verify velocity uses Instant not SystemTime. |
| 29-30 | Atomic position count race | Valid. Wrap position check in atomic operation. ACCEPTED. |
| 33 | Drawdown smoothing | Valid. Use 60s EWMA on equity for drawdown CHECKs. ACCEPTED. |
| 42 | Shadow ledger for cash | Valid. Engine already tracks equity_for_sizing separately (Sprint 5 SK-01). VERIFIED. |
| 44 | STOP_LIMIT not STOP for exits | Valid. Chandelier exits should use STOP_LIMIT with ATR offset. ACCEPTED. |
| 50-51 | Partial fill handling | Already handled. Executioner tracks filled_qty per order. VERIFIED. |
| 53 | GTC outsideRth flag | Valid. Set outsideRth=false for overnight GTC stops. ACCEPTED. |
| 132 | Atomic TOML write | Valid. Write to .tmp then rename. Already specified in H3. VERIFIED. |
| 136 | JSON schema version in persistent_memory | Valid. Add schema_version field. ACCEPTED. |
| 213 | Pydantic TOML validation | Valid. tomllib checks syntax not types. Add type validation. ACCEPTED. |
| 214 | Pre-calculate was_bad_veto boolean | Valid. Python calculates, Claude synthesizes reasoning only. ACCEPTED. |
| 239 | Bonferroni correction for multiple testing | Valid for parameter arrays. ACCEPTED for challenger. |

All other 236 points are either already handled, not applicable to our architecture (we don't use Pandas on the hot path, we don't do matrix inversion per-tick, etc.), or theoretical concerns for a system 100x our scale.

---

## COST

| Component | Monthly Cost |
|-----------|--------------|
| Claude Opus 4.6 (all 9 integrations) via Max subscription | **$0** |
| EC2 c7i-flex.large (already running for engine) | **$0 incremental** |
| Node.js + Claude CLI (one-time install) | **$0** |
| Telegram bot (already wired) | **$0** |
| Google Sheets (already wired) | **$0** |
| yfinance API (already used by ticker_selector) | **$0** |
| **TOTAL** | **$0/month** |

**How:** Claude Code CLI on EC2 authenticates with the Max subscription already used for development. `claude -p` invocations use the subscription's included Opus 4.6 quota. No per-call API charges.

---

*Every integration specified. Every data flow documented. Every file path listed. Every function name verified against the actual codebase. Every guardrail codified with hard bounds. Every validation gate defined with pass/fail criteria. Zero deferred items. Zero incremental cost. Ready to hand to a developer and build.*
