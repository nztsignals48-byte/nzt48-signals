# AEGIS — Strategy: Universe + S15 + Scout
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 1: THE UNIVERSE REGISTRAR {#section-1}

## 1.0 Problem Statement

The current system operates on a narrow universe. V2 expands this via a 3-tier architecture already designed in `uk_isa/universe_manager.py`.

### Current State (Code Reality)

| Component | Status | Location |
|-----------|--------|----------|
| ISA Core Universe | 12 core + 10 extended = 22 tradable ETPs | `uk_isa/isa_universe.py` |
| LSE Registry | 46 hardcoded ETPs, prices refreshed via IBKR (primary) or yfinance (fallback) daily. NOT web-scraped — no new listing discovery. | `uk_isa/lse_registry.py` |
| Universe Manager | 3-tier: CORE / PEER / FULL_SCAN | `uk_isa/universe_manager.py` |
| Peer Finder | Discovers correlated peer instruments | `uk_isa/peer_finder.py` |
| Predictive Scoring | 6-component scoring engine | `uk_isa/predictive_scoring.py` |
| Multiframe Analytics | Multi-timeframe analysis | `uk_isa/multiframe_analytics.py` |
| Volatility Regime | 5-state vol classifier | `uk_isa/volatility_regime.py` |
| Sector Rotation | Sector momentum tracking | `uk_isa/sector_rotation.py` |
| Correlation Engine | Ledoit-Wolf shrinkage correlations | `uk_isa/correlation_engine.py` |
| Old US Bot B | 18 US equities | DORMANT in V2 |
| Old S3 Mean-Reversion | Mean-reversion strategy | DORMANT in V2 |

### V2 Target State: 3-Tier Universe

**Tier 1: CORE (60+ LSE leveraged ETPs, scanned every 60s)**
- All LSE leveraged/inverse ETPs passing Amihud + ASER filters
- Auto-updated via `lse_registry.py` daily scrape
- Requirements: ADR > 2.9%, median spread < 0.25%, Amihud illiquidity < 0.5% for heat size
- This is the PRIMARY trading universe

**Tier 2: PEER (6-20 correlated peers, scanned every 5 min)**
- Underlying stocks/ETFs that the CORE ETPs track
- Provides chain reaction intelligence (underlying moves -> ETP signals)
- Managed by `uk_isa/peer_finder.py`

**Tier 3: FULL_SCAN (up to 500 contextual tickers, scanned every 30 min)**
- Broader market context: sector ETFs, macro indicators, FTSE 350 constituents
- Feeds sector rotation and macro context signals
- NOT for direct trading — intelligence only

### What Needs Building

1. **Amihud Capacity Sieve** — new module `uk_isa/amihud_sieve.py`
   - `ILLIQ_i = mean(|r_t| / Volume_t) x L^1.5` for trailing 20 days
   - PASS if: `(heat_size x ILLIQ_i) < 0.005` (< 50 bps market impact)
2. **ASER Filter** — extend `lse_registry.py` with ADR/Spread ratio
3. **DSR Graduation Gate** — Bailey & Lopez de Prado (2014), t-stat >= 3.0
4. **Apex Scout Module** — `strategies/apex_scout.py` (see Section 3)

### What to KEEP from Old System
- `lse_registry.py` price refresh logic via IBKR (primary) or yfinance (fallback). Hardcoded 46-product `_SEED_CATALOG` at lines 44-102, NOT web scrape — no new listing discovery, `new_listings` counter always 0. Needs: ASER column, actual LSE scrape for new listing detection.
- `isa_universe.py` ticker metadata structure (extend with ISA evidence fields)
- `predictive_scoring.py` (6-component engine maps to Vanguard ranking)
- `correlation_engine.py` (Ledoit-Wolf shrinkage already implemented)

### What is DORMANT in V2
- Bot B US equities universe (preserved but disabled)
- Russell 3000 / FTSE 350 direct scanning (deferred to Phase 2+)
- S3 mean-reversion strategy (preserved but disabled)
- All US-centric time-of-day parameters

### Regulatory Risk
- FCA restricted marketing of leveraged ETPs to retail in 2023. Further restrictions or ISA-wrapper bans are possible.
- Monitor FCA consultations on leveraged products quarterly.
- Contingency: if FCA publishes consultation on retail restrictions, begin futures-based alternative strategy (outside ISA, subject to CGT). If ban announced, halt all trading, preserve capital, deploy futures within 30 days.
- **Stamp Duty**: All CORE ETPs must be verified stamp-duty-exempt. Add `stamp_duty_exempt` boolean to TICKER_REGISTRY metadata. Any ETP with uncertain status excluded until verified.

---

# SECTION 1B: FATAL FLAWS AUDIT {#section-1b}

## 12 Fatal Flaws in Current Code

### CRITICAL

| # | Flaw | Location | Fix |
|---|------|----------|-----|
| F-01 | Signal Queue drops signals silently (Queue(maxsize=50) + put_nowait). WORSE: queue is WRITE-ONLY — no consumer exists. Wrong exception class (`asyncio.QueueFull` vs `queue.Full` — Queue is `queue.Queue` from stdlib, imported at main.py:23). Bug at main.py:3081,4208,4437. Falls through to outer `except Exception`. | `main.py:23,3081,4208,4437` | Fix exception class to `queue.Full`. Add consumer coroutine OR remove queue (process signals inline). |
| F-02 | Regime transition flattens without grace period. `decrement_transition_buffer()` NEVER CALLED (orphaned). Zero VIX hysteresis. | `main.py:4500-4611`, `regime_classifier.py:293` | 3-tick confirmation + 5% proportional VIX deadband |
| F-03 | No portfolio-level correlation brake. QQQ3.L + NVD3.L + 3SEM.L + GPT3.L all >0.85 correlated to NASDAQ. | `main.py:2441-2474` | Max 2 per correlation cluster. Factor exposure cap (NASDAQ beta). |

### HIGH

| # | Flaw | Location | Fix |
|---|------|----------|-----|
| F-04 | Inverse ETP set hardcoded (5-6 unsynchronized ticker lists) | `main.py:4571-4574` (_INVERSE_ETPS_SET, 10 tickers), `config/__init__.py:153-158` (fallback 20 tickers), `uk_isa/isa_universe.py:478-481` (INVERSE_PAIRS, 4 entries), `main.py:2173-2180` (_ISA_TO_UNDERLYING), `main.py:5963-5966` (underlying_map), `main.py:6013-6015` (underlying_to_etps) | Create single source of truth in `uk_isa/isa_universe.py` — all 6 scattered lists MUST import from it |
| F-05 | Kill switch can get stuck (no auto-clear logic) | Redis key `nzt:kill` | Auto-clear at 06:00 UK if equity recovered |
| F-06 | ML feature leakage — confidence is both input and output | `core/ml_meta_model.py` | Remove `confidence` from features. Add `raw_indicator_count`. |
| F-07 | VIX default = 0 when fetch fails (max aggression, no risk controls) | `main.py:4675-4685` | Fail-CLOSED: default vix=99, regime=RISK_OFF |

### MEDIUM

| # | Flaw | Location | Fix |
|---|------|----------|-----|
| F-08 | 24/7 scanning without market hour gating | `main.py:5276` | Skip outside 06:00-22:00 UK on trading days |
| F-09 | Lunch RVOL threshold too high (1.7 filters 95% of setups) | `settings.yaml` | Lower to 1.3 |
| F-10 | Daily loss limit -1.5% too tight for volatile phases | `settings.yaml` | Scale with regime: TRENDING=-2.5%, RANGE=-1.5%, HIGH_VOL=-1.0% |
| F-11 | Kelly cap redundant (half-Kelly + hard 0.75% = Kelly is pointless) | `settings.yaml` | Regime-conditional Kelly multipliers (0.0-0.6). Remove hard cap within 0.75% envelope. |
| F-12 | 30-minute macro cache too stale for VIX | `cross_asset_macro.py` | 5-min for VIX, 30-min for DXY/credit/F&G |

## 7 Fatal Flaws in Aegis v10.0 Plan

| # | Flaw | Fix |
|---|------|-----|
| A-01 | 2% daily compounding assumes zero losing days | Model honestly: Scenario A (conservative) -> £18K Year 1, not £1.49M. Keep 2% as aspiration. The profit ladder's tail captures are the mechanism. |
| A-02 | Thomas & Zhang contagion coefficient is for earnings, not intraday | Replace static beta=0.40 with empirically calibrated pair-specific betas from outcomes.jsonl |
| A-03 | RVOL Z-Score > 3.0 too selective for large pool | Adaptive Z by regime: TRENDING=2.0, RANGE=3.0, RISK_OFF=3.5, SHOCK=disabled |
| A-04 | O2C velocity ranking ignores overnight gaps | Add Gap-to-Range filter. If median overnight gap > 50% of ADR, penalise -15 confidence. |
| A-05 | Stranger 0.5x penalty doesn't decay | Bayesian shrinkage: 0.25x -> 1.0x based on DSR + sample count |
| A-06 | No explicit no-signal-day protocol | Drought state machine (NONE -> WATCH -> ACTIVE -> CRITICAL). Quality floor NEVER below 50. |
| A-07 | CVaR sizing is per-position, not portfolio-wide | Compute portfolio CVaR. If portfolio CVaR > 3% of equity, block new entries. |

---

# SECTION 2: THE VANGUARD SNIPER — S15 {#section-2}

## Purpose

S15 is the core signal engine. It scores ALL instruments in the tradable universe against weighted indicators and identifies the highest-conviction setups.

## CRITICAL FINDING: S15 HAS 0% WIN RATE

**Data source**: `data/playbook.json` (103 total trades, last updated 2026-02-28)

| Regime | S15 Trades | Wins | Win Rate |
|--------|-----------|------|----------|
| TRENDING_UP_STRONG | 20 | 0 | **0%** |
| RANGE_BOUND | 15 | 0 | **0%** |
| TRENDING_UP_MOD | 8 | 0 | **0%** |
| NEUTRAL | 5 | 0 | **0%** |
| TRENDING_DOWN_STRONG | 4 | 0 | **0%** |
| **S15 TOTAL** | **52** | **0** | **0%** |

All 52 S15 trades have avg_r = 0.0 and avg_holding_mins = 0.0 — meaning they were ALL stopped out immediately or rejected after entry. The stop calibration data confirms: `mae_median = 0.0, mae_p75 = -0.3, mfe_median = 0.0, mfe_p25 = 0.5`. The system enters so late that price immediately reverses into the stop.

**Root cause**: 11 systemic timing defects (see Section 2B) cause the system to enter trades 15-60 minutes after the move has started. By that point, the mean reversion kicks in and the stop is hit.

**The good news**: The moves ARE there. The ticker selection is correct. The 2% daily target is achievable. The problem is purely execution timing.

## Operating Model

| Parameter | Value |
|-----------|-------|
| Universe scanned | Full CORE tier (60+ LSE leveraged ETPs) via `universe_manager.py` |
| Signals per day | Multiple — as many as qualify. No artificial limit. |
| Trade frequency | Depends on market conditions. 0-4+ trades per day. |
| No-trade days | Accepted. Cash is a position. No forced trades. |
| Short capability | Inverse ETPs prioritised in BEAR/RISK_OFF regimes |
| Same-ticker re-entry | Allowed. Can close and reopen same ticker in same session. |
| Binding constraints | Signal quality (min 65), portfolio heat (3.5%), max 4 concurrent, correlation brake |

### Current S15 State (Verified in Code) — WITH DEFECTS IDENTIFIED

- 8-indicator weighted consensus (VWAP 1.8x, RSI 1.2x, etc.) — **DEFECT: Lagging indicators (EMA20/50) reject fast moves**
- Adaptive easing for leveraged ETPs in trending regimes — **OK but insufficient**
- P90 spread tracker (dynamic cost awareness) — **OK**
- Tail risk pre-screen (GPD, Balkema-de Haan-Pickands) — **DEFECT: Downloads 270 days data PER CANDIDATE per cycle (now via IBKR, ~6s vs 24s with yfinance, but still should be nightly batch per T-04)**
- Power Hour seasonality boost (+15%) — **OK**
- Confidence floor: 75.0 in code (daily_target.py:71), 65 in Constitution R13, 60 in ImmutableRiskRules (risk_sizer.py:45) — **DEFECT: SK-03 three-way conflict**
- First 30-min blackout (daily_target.py:324-333, Admati & Pfleiderer 1988) — **DEFECT: Blocks highest-alpha window**
- Lunch dead zone 11:30-13:00 UK (daily_target.py:335-344, Jain & Joh 1988) — **DEFECT: Blocks US pre-market repricing**
- `_daily_signal_fired` single-fire limit (daily_target.py:297,348,497) — **DEFECT: Old V1 code, plan says remove (E-01)**
- `_MAX_SIGNALS_PER_DAY = 1` (daily_target.py:70) — **DEFECT: Redundant throttle, coupled with SK-04**
- ADX >= 25 requirement (daily_target.py:77) — **DEFECT: Lagging, rejects trend starts**
- RVOL >= 0.85 minimum (daily_target.py:66) — **DEFECT: Rejects gap moves on low initial volume**

### V2 Enhancements Required

**E-01: Remove Artificial 1-Signal Limit**
- Current: `MAX_SIGNALS_PER_DAY = 1` in `daily_target.py`
- V2: Remove this limit. Multiple instruments can qualify simultaneously.
- Portfolio-level governors (max 4 positions, 3.5% heat, correlation brake) are the ONLY trade-count limiters.

**E-02: Full Universe Scanning**
- Current: Scans 12 hardcoded ISA ETPs
- V2: Scans entire CORE tier (60+ ETPs) from `universe_manager.py`
- Secondary strategies can fire alongside S15 in the same scan cycle

**E-03: Chain Reaction Confidence Boost**
- Wire `move_attribution` module into S15 scoring
- Replace static Thomas & Zhang beta=0.40 with empirical pair-specific betas
- Cap at +20 confidence boost

**E-04: Vol-Managed Sizing (Moreira & Muir 2017)**
- For 3x ETPs, use underlying's realised vol x 3 as input to vol-targeting
- A 3x ETP with 60% ann vol gets 0.25x the position of a 20% ann vol stock

**E-05: Inverse Pivot (Bearish Regime Profit)**
- When regime = TRENDING_DOWN_STRONG or RISK_OFF: filter to inverse ETPs only
- Apply +10 confidence boost
- Entry on first retracement after VIX > 28.5 + price < 50-EMA + within 24h of spike
- Max hold 24h. Size 0.3x f*

---

# SECTION 3: THE APEX SCOUT {#section-3}

## This Does Not Exist Yet. Build From Scratch.

**Purpose**: Asynchronous discovery of RVOL anomalies across the PEER and FULL_SCAN tiers of the universe. Feeds intelligence back to S15 for chain reaction scoring.

**Architecture**: New file `strategies/apex_scout.py`

- Scans PEER tier every 5 minutes, FULL_SCAN tier every 30 minutes
- Regime-adaptive RVOL Z-threshold (TRENDING=2.0, RANGE=3.0, RISK_OFF=3.5, SHOCK=disabled)
- All Scout signals carry the Bayesian Stranger Penalty

**LSE Priority Mapping**: When Scout detects anomaly on underlying (e.g., NVIDIA):
1. Check `lse_mapper.get_etp_equivalent(ticker)` -> returns NVD3.L
2. If LSE equivalent exists AND LSE hours -> reroute to leveraged ETP
3. If no equivalent -> intelligence only (not traded directly)
4. Apply Bayesian Stranger Penalty to ALL Scout signals

**Data Cost Control**:
- Sunday night: Refresh FULL_SCAN universe, compute 20-day RVOL + ADR, filter to top candidates
- Daily 06:00 UK: Quick delta refresh
- Every 60s during market hours: IBKR real-time quotes via `IBKRSource.fetch_quote()` (primary)
- yfinance batch download as fallback only (if IBKR disconnects)

**Data Feed Architecture (IBKR Primary, yfinance Fallback)**:
- **Primary**: IB Gateway + IBC running in Docker on EC2 (`ghcr.io/gnzsnz/ib-gateway:stable`)
  - Real-time Level 1 quotes (~50-100ms latency)
  - Real bid/ask spreads (critical for T-03/T-04 entry decisions)
  - Official OHLCV bars (no yfinance scraper inconsistencies)
  - No rate limiting (vs yfinance throttling)
  - Connection: engine connects via Docker network at `ib-gateway:4002` (paper mode)
  - `data_hub/sources/ibkr_source.py`: client_id=10, persistent connection, auto-reconnect
  - `execution/ibkr_gateway.py`: client_id=2, separate connection for order routing (future)
- **Fallback**: yfinance (always available, 15-60s delayed, proxy spreads)
  - DataHub priority chain (`hub.py:78-82`): IBKR → yfinance → validator
  - If IBKR disconnects, `IBKRSource.IS_AVAILABLE` flips to False, DataHub auto-falls back
- **Weekly maintenance**: IB Gateway re-auth every Sunday evening. IBC auto-restarts daily. User must approve 2FA on phone every Monday morning. If missed, yfinance fallback activates. **GQ-01 reconnection loop**: `ibkr_source.py` must attempt reconnect every 5s when disconnected (up to 10 min). **GQ-02 Monday guardrail**: at 07:50 UK, if `not ib.isConnected()`, Telegram alert. If still disconnected at 08:00 UK, HALT (not degrade).
- **Go-Live Gate**: IBKR data feed is now available. `Market Data Feed | Real-time API (NOT yfinance)` criterion satisfied.
- Stale data invariant (RI-03) still enforced regardless of source.

---
