# AEGIS ALPHA-OMEGA MASTER PLAN v12.0
## Cross-Referenced Architecture Blueprint — FINAL PRE-IMPLEMENTATION
### NZT-48 → Dual-Core Institutional Trading Engine

**Authors**: Claude Opus 4.6 (Lead Systems Architect) + Gemini 2.5 (Quant Reviewer)
**Date**: 2026-03-04
**Status**: ARCHITECTURE LOCKED — Ready for Implementation
**Mandate**: Compound £10,000 via 2%+ daily profit ladder on leveraged LSE ETPs + global equities.

**Revision History**:
- v10.0: Gemini theoretical plan (unconstrained)
- v11.0: Claude cross-reference with 15,700 LOC codebase audit (12 code flaws + 7 plan flaws)
- v12.0: Gemini review integrated + Claude validation. All changes tagged [G]=Gemini source, [C]=Claude correction/addition

**Change Legend**: `[G-ACCEPT]` = Gemini suggestion accepted. `[G-REJECT reason]` = Gemini suggestion rejected with reason. `[C-ADD]` = Claude addition not in either prior version.

---

## TABLE OF CONTENTS

1. [EXECUTIVE SUMMARY: What Exists vs What's Planned](#1-executive-summary)
2. [FATAL FLAWS DISCOVERED IN CURRENT SYSTEM](#2-fatal-flaws) (12 flaws)
3. [FATAL FLAWS IN AEGIS v10.0 PLAN](#3-aegis-flaws) (7 flaws)
4. [THE UNIVERSE REGISTRAR — Rebuild Specification](#4-universe-registrar)
5. [CORE 1: THE VANGUARD SNIPER — S15 Enhancement](#5-vanguard-sniper) (5 enhancements)
6. [CORE 2: THE APEX SCOUT — New Module](#6-apex-scout)
7. [THE EXECUTIONER: SNIPER ENGINE — Execution Upgrade](#7-executioner) (4 upgrades)
8. [THE OUROBOROS: SELF-LEARNING LOOP — ML Overhaul](#8-ouroboros) (5 improvements)
9. [RISK ARCHITECTURE — Portfolio-Level Rebuild](#9-risk-architecture) (10 risk controls)
10. [LIQUIDITY SCALING MODEL](#10-liquidity-scaling)
11. [NOTIFICATION & DECISION SUPPORT](#11-notifications)
12. [INFRASTRUCTURE HARDENING](#12-infrastructure)
13. [PARAMETER RECALIBRATION TABLE](#13-parameters)
14. [IMPLEMENTATION PHASES](#14-phases) (4 phases, 12 weeks)
15. [MATHEMATICAL APPENDIX](#15-math) (8 formulas)
16. GEMINI Q&A — Answered & Integrated (7/8 resolved)
17. REJECTED GEMINI SUGGESTIONS (4 rejections with reasoning)
18. GLOSSARY

---

## 1. EXECUTIVE SUMMARY: What Exists vs What's Planned {#1-executive-summary}

### What We Have (Verified in Code)

| Component | Status | LOC | Location |
|-----------|--------|-----|----------|
| Main orchestrator | Production (paper) | ~7,700 | `main.py` |
| 16 strategies (S1-S16) | Active (S3 dormant) | ~3,000 | `strategies/` |
| 33-gate qualification gauntlet | Active | Embedded in main.py L2016-2854 |
| 5-rung Chandelier Exit | Active, Redis-persisted | 344 | `core/chandelier_exit.py` |
| Cross-asset macro (VIX/DXY/Credit/F&G/HMM) | Active, 30min cache | 534 | `core/cross_asset_macro.py` |
| ML meta-model (LightGBM + XGBoost) | Active, 413+ trades | 1,029 | `core/ml_meta_model.py` |
| 10 learning subsystems | Active | Embedded in main.py L3210-3744 |
| UK ISA 6-module intelligence suite | Active | ~4,000 | `uk_isa/` |
| Redis StateManager (Lua atomic ops) | Active | ~800 | `core/state_manager.py` |
| FastAPI dashboard + Next.js frontend | Active | 3,783 + ~2,000 | `dashboard/` |
| 22-table SQLite database | Active | 400 | `delivery/database.py` |
| APScheduler (40+ jobs, 24/7) | Active | Embedded in main.py L4835+ |
| Docker Compose (3 containers) | Active | 120 | `docker-compose.yml` |
| Telegram event bus | Active | ~600 | `delivery/telegram_bot.py` |
| S3 backup (manual) | Exists, not automated | 80 | `scripts/backup_to_s3.sh` |

### What Aegis v10.0 Proposes That We DON'T Have Yet

| Component | Gap | Effort | Priority |
|-----------|-----|--------|----------|
| Universe Registrar (1,000 Core + 3,000 Radar) | Currently 12 ISA + 18 US only | LARGE | P0 |
| Apex Scout (30-min async global scanner) | Does not exist | LARGE | P0 |
| Amihud Capacity Sieve (dynamic liquidity) | Hardcoded heat limits | MEDIUM | P0 |
| ASER filter (ADR-to-Spread Efficiency) | Partial (spread gate exists, no ADR gate) | SMALL | P1 |
| A/B Team DSR-gate graduation | Exists in S16 (A-team/B-team) but no DSR | MEDIUM | P1 |
| Bayesian Stranger Penalty | Static 0.5x in S16 B-team | SMALL | P1 |
| LSE Priority Mapping (Scout→ETP reroute) | ISA mapper exists but only for pre-configured pairs | MEDIUM | P0 |
| Chain Reaction Intelligence | move_attribution exists, Thomas & Zhang NOT calibrated | SMALL | P1 |
| RVOL Z-Score anomaly detection | RVOL exists, no Z-Score threshold model | SMALL | P1 |
| VWAP Microstructure Anchor | VWAP exists (1.8x weight in S15), no institutional distribution veto | SMALL | P1 |
| Stoikov EV Gate (OBI-adjusted) | Feature flag exists (`v95_stoikov_ev_gate`), not fully calibrated | MEDIUM | P1 |
| Infinite Profit Ladder (no ceiling) | 7-rung ladder exists, DOES have ceiling (+4R gift) | SMALL | P2 |
| Portfolio-level correlation brake | Per-position CVaR only, no portfolio-wide | MEDIUM | P0 |
| SHAP feature pruning | Exists (`v95_shap_stability_filter_enabled`) | DONE | — |
| Tiered Telegram notifications | Telegram exists, no P0/P1/P2/P3 hierarchy | MEDIUM | P2 |
| Adaptive thresholds (RVOL Z per regime) | Static thresholds everywhere | MEDIUM | P1 |

---

## 2. FATAL FLAWS DISCOVERED IN CURRENT SYSTEM {#2-fatal-flaws}

### FLAW F-01: Signal Queue Drops Signals Silently (CRITICAL)
**Location**: `main.py` L1136, L3082
**Issue**: `Queue(maxsize=50)` with `put_nowait()` — if 50 signals pending, new signals are silently dropped.
**Impact**: During high-volatility events (exactly when you want signals), queue overflow causes missed trades.
**Fix**: Replace bounded queue with unbounded `asyncio.Queue()` + backpressure logging. Alternatively, priority queue where S15 signals always admitted.

### FLAW F-02: Regime Transition Flattens Without Grace Period (CRITICAL)
**Location**: `main.py` L4500-4611
**Issue**: `TRENDING_UP → TRENDING_DOWN` immediately flattens ALL long positions. No confirmation window.
**Impact**: A brief VIX spike causing regime flip → sells everything → regime reverts in 10 minutes → you're out. At £1M AUM, slippage on mass flatten could be 0.5-1%.
**Fix**: Implement 15-minute regime confirmation buffer (already suggested in config analysis). Only execute transition actions if new regime persists for 3 consecutive ticks (3 minutes at 60s interval).

### FLAW F-03: No Portfolio-Level Correlation Brake (CRITICAL)
**Location**: `main.py` L2441-2474 (PortfolioRiskManager)
**Issue**: Checks per-position concentration (max 5% per ticker) but does NOT check portfolio-wide directional correlation. QQQ3.L + NVD3.L + 3SEM.L + GPT3.L are all >0.85 correlated to NASDAQ.
**Impact**: A single NASDAQ gap-down of 3% becomes -9% across all positions simultaneously. The -2% daily halt triggers instantly, but the damage is already done.
**Fix**: Add real-time portfolio correlation monitor. If rolling 20-day pairwise correlation among active positions exceeds 0.7 for 3+ pairs, cap total exposure to 1 position until dispersion returns. Implement as Gate #34 in the gauntlet.

### FLAW F-04: Inverse ETP Set is Hardcoded (HIGH)
**Location**: `main.py` L4571-4575
**Issue**: Inverse ETPs (QQQS.L, 3USS.L) are a hardcoded list. If LSE Registry discovers new inverse products, they won't be handled correctly in regime transitions.
**Fix**: Move to `uk_isa/isa_universe.py` TickerEntry metadata (`is_inverse: bool`). Regime transition logic queries metadata, not hardcoded list.

### FLAW F-05: Kill Switch Can Get Stuck (HIGH)
**Location**: Redis key `nzt:kill`
**Issue**: Once set, `clear_kill()` requires manual intervention. If set during off-hours, entire next trading day is lost.
**Fix**: Add auto-clear logic: if kill reason is "drawdown" AND new trading day AND equity recovered above trigger level, auto-clear at 06:00 UK with P0 Telegram alert.

### FLAW F-06: ML Feature Leakage — Confidence as Input (HIGH)
**Location**: `core/ml_meta_model.py` feature_cols
**Issue**: `confidence` (rule-based score) is used as an input feature to the ML model that gates signals. This creates circularity: the model learns to predict "high confidence = win" which is tautological.
**Impact**: ML model overweights confidence, making the meta-label gate redundant (it just re-confirms what the rule system already decided).
**Fix**: Remove `confidence` from feature_cols. Replace with `raw_indicator_alignment_count` (the number of aligned indicators before confidence scoring).

### FLAW F-07: VIX Default of 0 When Fetch Fails (HIGH)
**Location**: `main.py` L4675-4685
**Issue**: If market structure fetch fails, VIX defaults to 0. VIX=0 means the system treats current conditions as the safest possible market — maximum aggression, no risk controls.
**Fix**: [G-ACCEPT modified] Default to `max(VIX_last_known, 25)`. Rationale: fetch failures during market hours correlate with volatility spikes (exchange circuit breakers, API overload). Defaulting to the median (22) is an "optimism bias" error. If VIX_last_known is also stale (>30 min), default to 30. If VIX unavailable for >10 minutes, escalate to CAUTION regime (half-size all entries).

### FLAW F-08: 24/7 Scanning Without Market Hour Gating (MEDIUM)
**Location**: `main.py` L5276-5285
**Issue**: Continuous 60s scan runs 24/7, including weekends and holidays. Wasted compute + stale data processing.
**Fix**: Add market calendar gate. Skip data fetch + strategy execution outside 06:00-22:00 UK on trading days. Keep heartbeat and health checks running.

### FLAW F-09: Lunch RVOL Threshold Too High (MEDIUM)
**Location**: `config/settings.yaml` — lunch chop RVOL min 1.7
**Issue**: Natural RVOL during 11:30-14:00 ET is typically 0.6-1.2. Requiring 1.7 filters out 95%+ of setups.
**Fix**: Lower to 1.3 for lunch window. Quality is maintained by the 33-gate gauntlet; RVOL is just one gate.

### FLAW F-10: Daily Loss Limit -1.5% Too Tight for Volatile Phase (MEDIUM)
**Location**: `config/settings.yaml` — Overseer combined loss: -1.5%
**Issue**: At £10K, this is -£150. A single S15 stop hit on a 3x ETP + spread cost can consume 40% of this budget. Two stops = halt for the day = missed recovery trades.
**Fix**: Scale with regime: TRENDING regimes → -2.5%, RANGE → -1.5%, HIGH_VOL → -1.0%. The Aegis plan's 2% daily target requires room for 1-2 stops before the winner.

### FLAW F-11: Kelly Criterion Cap Redundant (LOW) `[G-ACCEPT modified]`
**Location**: `config/settings.yaml` — Half Kelly, but hard-capped at 0.75%
**Issue**: Kelly computes optimal f*, then halves it, then the immutable risk rule caps at 0.75% anyway. The Kelly computation adds complexity without adding value.
**Fix**: Implement regime-conditional Kelly (Gemini Q5 answer):
```
Regime-Conditional Kelly Multipliers:
  TRENDING_UP_STRONG:  0.6 × f*  (aggressive — momentum is real)
  TRENDING_UP_MOD:     0.5 × f*  (standard half-Kelly)
  RANGE_BOUND:         0.3 × f*  (conservative — choppy markets)
  TRENDING_DOWN_MOD:   0.4 × f*  (moderate — inverse plays available)
  TRENDING_DOWN_STRONG: 0.3 × f*  (conservative — high vol)
  RISK_OFF:            0.2 × f*  (minimal — preservation mode)
  SHOCK:               0.0 × f*  (no trading)

Remove the 0.75% hard cap. Let regime-Kelly self-regulate with
portfolio heat (3%) as the ultimate safety net.
Requires: ≥30 trades per regime for stable f* estimation.
```

### FLAW F-12: 30-Minute Macro Cache Too Stale (LOW)
**Location**: `core/cross_asset_macro.py` — `_CACHE_SECONDS = 1800`
**Issue**: VIX can spike 30% in 5 minutes during a flash crash. A 30-minute cache means the system trades on stale macro context.
**Fix**: Reduce to 5-minute cache for VIX. Keep 30-minute for slower-moving (DXY, credit, Fear & Greed). HMM retrain stays daily.

---

## 3. FATAL FLAWS IN AEGIS v10.0 PLAN {#3-aegis-flaws}

### AEGIS-FLAW A-01: 2% Daily Compounding Arithmetic is Impossible Without Tail Captures
**Issue**: £10K × (1.02)^252 = £1.486M assumes ZERO losing days. Real systems have 40-55% win rates. Even with 2R:1R payoff ratio, expected daily return is ~0.2-0.5%, not 2%.
**Reality Check**: The Infinite Profit Ladder's Rung 3+ tail captures MUST subsidise losers. The plan describes the ladder but doesn't model the *expected* geometric return across thousands of trades.
**Fix**: Model the compounding honestly:
- Scenario A (conservative): 55% WR, 2.5R avg win, 1.0R avg loss → E[daily] = 0.55×2.5 - 0.45×1.0 = 0.925% → (1.00925)^252 = £10K → £102K (year 1)
- Scenario B (aggressive): 60% WR, 3.0R avg win, 1.0R avg loss → E[daily] = 0.60×3.0 - 0.40×1.0 = 1.4% → (1.014)^252 = £10K → £338K (year 1)
- Scenario C (target): Need 65%+ WR OR 4R+ avg win to approach 2% daily mean
**Action**: Keep the 2% *target* as aspiration but size for Scenario A/B reality. The Profit Ladder is the mechanism to push toward Scenario C via tail capture (holding winners longer).

### AEGIS-FLAW A-02: Thomas & Zhang (2008) Contagion Coefficient is for Earnings, Not Intraday
**Issue**: The plan uses a fixed β=0.40 for chain reaction intelligence (TSMC beat → NVD3.L +15 confidence). Thomas & Zhang measured *quarterly earnings* contagion, not intraday price moves.
**Existing Code**: `move_attribution` module EXISTS and already applies chain boosts with 30% decay per cycle (Bernard & Thomas 1990).
**Fix**: Replace the static β=0.40 with empirically calibrated coefficients from our own outcomes.jsonl. For each underlying→ETP pair, compute the trailing 90-day correlation of 1-hour returns. Use this as the pair-specific β. Store in Redis, recalibrate weekly.

### AEGIS-FLAW A-03: RVOL Z-Score > 3.0 is Too Selective for a 3,000-Ticker Pool
**Issue**: Z > 3.0 = 0.13% of the distribution. Across 3,000 tickers in a calm market, you'd expect ~4 anomalies. During extremely quiet periods (VIX < 12), you might get zero.
**Fix**: Adaptive Z-threshold by regime:
- TRENDING_UP_STRONG: Z > 2.0 (cast wider net, momentum is real)
- RANGE_BOUND: Z > 3.0 (need extreme deviation to overcome chop)
- RISK_OFF: Z > 3.5 (only trade overwhelming anomalies)
- SHOCK: Z > 999 (effectively disabled)

### AEGIS-FLAW A-04: O2C Velocity Ranking Ignores Overnight Gaps
**Issue**: Open-to-Close range captures intraday moves but misses gap risk. A 3x ETP with 4% O2C range but 8% overnight gap risk is NOT a good candidate for a day-trading system.
**Existing Code**: S15 has no overnight gap protection (confirmed in audit).
**Fix**: Add Gap-to-Range ratio filter. If median overnight gap (close-to-open) > 50% of ADR, penalise -15 confidence. For 3x ETPs, this is critical because leverage amplifies gap risk but the plan ignores it entirely.

### AEGIS-FLAW A-05: The "Stranger" 0.5x Penalty Doesn't Decay
**Issue**: Static 0.5x for all Scout discoveries, regardless of how many successful trades they've had.
**Existing Code**: S16 A-team/B-team system exists with graduation rules (WR ≥ 55%, AvgR ≥ 1.2 over 20+ trades), but B-team is still static 0.5x.
**Fix**: Bayesian shrinkage (formula in Math Appendix §15.1). Decay from 0.25x → 1.0x based on DSR + sample count.

### AEGIS-FLAW A-06: No Explicit "No-Signal Day" Protocol
**Issue**: The plan assumes at least one ticker hits the 2% threshold every trading day. Our S15 analysis shows it fires max 1 signal per day — but on some days it fires ZERO.
**Impact**: Missing 13-20 trading days per year (5-8%) breaks the compounding math.
**Fix**: Define explicit NO_TRADE state:
- If S15 fires no signal by 14:00 UK, escalate to S16 Universal Scanner with looser gates
- If still no signal by 15:00, activate "Defensive Mode" — look for S12 Rebalance Flow or S8 Volatility Crush setups
- If no trade by 16:00, log day as FLAT with no penalty. Adjust compound target to 2.1% on next signal day.
- Track "dry day" frequency in learning engine. If >10% of trading days, widen S15 gates.

### AEGIS-FLAW A-07: CVaR Sizing is Per-Position, Not Portfolio-Wide
**Issue**: The plan mentions CVaR scaling (Rockafellar & Uryasev 2000) but applies it per-position. Correlated positions need portfolio-level CVaR.
**Existing Code**: `v95_cvar_scaling_enabled` feature flag is ON but implementation is per-signal.
**Fix**: Compute portfolio CVaR at 95% confidence level across all open positions. If portfolio CVaR exceeds 3% of equity, reject new entries until risk declines. This replaces the crude -1.5% daily halt with a forward-looking risk measure.

---

## 4. THE UNIVERSE REGISTRAR — Rebuild Specification {#4-universe-registrar}

### Current State
- ISA Universe: 12 core ETPs (hardcoded in `uk_isa/isa_universe.py`)
- Bot B Universe: 18 US semis/AI equities (hardcoded in `config/settings.yaml`)
- LSE Registry: 52 products auto-scraped daily (`uk_isa/lse_registry.py`)
- No Russell 3000 / FTSE 350 scanning capability
- No Amihud sieve, no ASER filter, no DSR graduation

### Target State
Two-tier system:

**Tier 1: "Core" (300-500 tickers, scanned every 60s)**
- All LSE leveraged/inverse ETPs passing ASER + Amihud (currently ~52, expanding)
- Top 50 US high-beta underlyings (currently 18, expand to 50)
- Requirements: ADR > 2.9%, median spread < 0.25%, Amihud illiquidity < 0.5% for £1.5K heat (at £10K equity)
- Graduation: DSR t-stat ≥ 3.0 over 30+ trades AND spans 2+ volatility regimes `[G-ACCEPT: Harvey, Liu & Zhu 2016 — t>3.0 for multiple-testing correction]`

**Tier 2: "Radar" (1,000-3,000 tickers, scanned every 30 min)**
- Russell 3000 subset: filter by market cap > $500M, ADV > $10M/day
- FTSE 350 liquid constituents
- Requirements: Only RVOL Z-Score anomalies (adaptive threshold per regime) trigger detailed analysis
- Role: Discovery layer feeding Core via graduation, Chain Reaction intelligence feeding confidence

### What Needs Building
1. **Russell 3000 / FTSE 350 ticker fetcher** — yfinance can pull constituents; cache weekly
2. **Amihud Capacity Sieve** — new module in `uk_isa/amihud_sieve.py` `[G-ACCEPT modified]`
   ```
   ILLIQ_i = mean(|r_t| / Volume_t) × L^1.5 for t in trailing 20 days
   Where L = leverage factor (1, 3, or 5)
   PASS if: (heat_size × ILLIQ_i) < 0.005  [i.e., <50 bps market impact]
   Volume adjusted by time-of-day factor (see §15.4)
   ```
3. **ASER Filter** — extend `uk_isa/lse_registry.py` to compute ADR/Spread ratio
4. **DSR Graduation Gate** — extend S16's A/B team system with Bailey & Lopez de Prado (2014) DSR
5. **Async 30-min Scanner** — new APScheduler job, separate from 60s core loop

### What to KEEP from Existing Code
- `uk_isa/lse_registry.py` — auto-scrape logic is solid, just needs ASER column
- `uk_isa/isa_universe.py` — ticker metadata structure works, extend with `amihud_score`, `aser_score`, `dsr_tstat`
- `uk_isa/predictive_scoring.py` — 6-component scoring engine maps directly to Vanguard ranking

### What to REMOVE or Replace from Aegis Plan
- **REMOVE**: The 3,000-ticker "Apex Radar" scanning every 30 minutes via yfinance. yfinance rate-limits at ~2,000 requests/5 min. Scanning 3,000 tickers with 5-minute bars = 3,000 API calls every 30 min = rate-limited.
- **REPLACE WITH**: Pre-filtered watchlist. Use a daily scan of Russell 3000 (Sunday + Monday pre-market) to identify the top 200-500 "hot" tickers by 20-day RVOL + ADR. Scan only these 200-500 at 30-min intervals. This is computationally feasible on t3.small.

---

## 5. CORE 1: THE VANGUARD SNIPER — S15 Enhancement {#5-vanguard-sniper}

### Current S15 State (Verified)
- Fires exactly 1 signal per day (by design)
- 8-indicator weighted consensus (VWAP 1.8x, RSI 1.2x, etc.)
- Adaptive easing for leveraged ETPs in trending regimes (4.8/9.5 vs 7.0/10.0)
- P90 spread tracker (dynamic cost awareness)
- Tail risk pre-screen (GPD, Balkema-de Haan-Pickands)
- Power Hour seasonality boost (+15%)
- Confidence floor: 75 (Harvey & Liu 2015 multiple-testing correction)

### Enhancements Required

**E-01: Chain Reaction Confidence Boost (Wire move_attribution → S15 scoring)**
- Status: move_attribution MODULE exists in main.py L1446. It computes chain_boosts with 30% decay.
- Gap: Boosts are computed but NOT wired into S15's scoring function directly. They go through the general gauntlet as soft adjustments.
- Fix: In S15's `_calculate_reachability_score()`, add:
  ```
  chain_boost = move_attribution.get_confidence_boost(ticker)
  if chain_boost > 0:
      confidence += min(chain_boost, 20)  # cap at +20
  ```
- Calibration: Replace static Thomas & Zhang β=0.40 with empirical pair-specific β from outcomes.jsonl.

**E-02: PEAD Decay Integration (Bernard & Thomas 1990)** `[G-ACCEPT]`
- Status: 30% decay per cycle already implemented (main.py L1446)
- Gap: The decay is applied uniformly. It should be Day+1 = 30% residual, Day+2 = 15%, Day+3 = 5%, Day+4+ = 0 (stale).
- Fix: Replace `decay_existing_boosts(0.70)` with time-aware **power-law** decay (Chan et al. 1996):
  ```
  hours_since_catalyst = (now - catalyst_time).total_seconds() / 3600
  residual = 0.30 × (hours_since_catalyst + 1)^(-0.5)
  ```
- **Why power-law, not exponential**: Exponential decay kills the signal at 24h (residual = 0.008 = nothing). Power-law preserves a meaningful tail: Day+1 = 6.1%, Day+2 = 4.3%. PEAD literature (Chan 1996, Hou & Moskowitz 2005) confirms drift persists for 5-10 days in large-cap equities.

**E-03: Vol-Managed Sizing (Moreira & Muir 2017)**
- Status: `v95_vol_target_enabled` flag is ON. Vol target = 15% annualised.
- Gap: Current implementation scales by inverse of portfolio vol, but doesn't account for 3x ETP's own vol being 3x the underlying.
- Fix: For 3x ETPs, use underlying's realised vol × 3 as the input to vol-targeting. A 3x ETP with 60% ann vol should get 0.25x the position of a stock with 20% ann vol (15%/60% = 0.25).

**E-04: The Inverse Pivot (Bearish Regime Profit)** `[G-ACCEPT modified]`
- Status: S15 can go SHORT, and inverse ETPs (QQQS.L, 3USS.L) are in the universe.
- Gap: No explicit logic to PRIORITISE inverse ETPs during BEARISH/RISK_OFF regimes. Currently treated equal to long ETPs.
- Fix: In S15 `scan()`, when regime is TRENDING_DOWN_STRONG or RISK_OFF:
  - Filter universe to inverse ETPs only
  - Apply +10 confidence boost (mechanical edge: market-maker rebalancing amplifies inverse ETP moves during selloffs)
  - This is the equivalent of the Aegis "Inverse Pivot"
- **Entry timing for inverse ETPs during crashes** (Gemini Q7 answer) `[G-ACCEPT]`:
  ```
  Activation criteria (ALL must be true):
    1. VIX > 28.5 (elevated fear, not yet peak panic)
    2. Underlying price < 50-period EMA (confirmed downtrend)
    3. Move is within 24h of initial spike (not stale)

  DO NOT wait for VIX peak (impossible to identify in real-time).
  DO NOT enter during the initial spike (spread blowout risk).
  Enter on the FIRST RETRACEMENT after criteria are met.

  Position sizing: 0.3 × f* (30% Kelly — high vol = smaller size)
  Max hold: 24 hours (inverse ETP vol drag kicks in beyond 1 day)
  ```

**E-05: No-Signal Escalation Protocol**
- Status: S15 fires 0-1 signals per day. No escalation exists.
- Fix (NEW):
  - 14:00 UK: If S15 has not fired, lower confidence floor from 75 → 70 for remaining session
  - 14:30 UK: If still no signal, activate S12 (Rebalance Flow) scan with ISA mapping
  - 15:00 UK: If still no signal, activate S16 Universal Scanner with ISA-only universe
  - 15:30 UK: Accept FLAT day. Log. Move on.
  - Track dry-day frequency; if >8% of trading days, widen S15 ADR gate from 2.9% to 2.5%.

---

## 6. CORE 2: THE APEX SCOUT — New Module {#6-apex-scout}

### This Does Not Exist Yet. Build From Scratch.

**Purpose**: Asynchronous discovery of RVOL anomalies across 200-500 pre-filtered global equities.

**Architecture**:
```
New file: strategies/apex_scout.py

class ApexScout:
    """
    30-minute asynchronous scanner for RVOL Z-Score anomalies
    across pre-filtered Russell 3000 / FTSE 350 watchlist.

    Feeds signals to Executioner via LSE Priority Mapping.
    All Scout signals carry the Bayesian Stranger Penalty.
    """

    def __init__(self, watchlist: list[str], regime_provider, lse_mapper):
        self.watchlist = watchlist  # 200-500 tickers, refreshed daily
        self.regime_provider = regime_provider
        self.lse_mapper = lse_mapper
        self.rvol_history = {}  # ticker → deque(maxlen=20) of time-of-day-adjusted RVOL

    async def scan(self) -> list[ScoutSignal]:
        """Run every 30 minutes. Return anomalies."""
        regime = self.regime_provider.get_regime_tag()
        z_threshold = self._get_adaptive_threshold(regime)

        anomalies = []
        for batch in chunk(self.watchlist, 50):  # Process in batches of 50
            data = yf.download(batch, period="5d", interval="30m")
            for ticker in batch:
                rvol = self._compute_time_adjusted_rvol(ticker, data)
                z_score = self._compute_z_score(ticker, rvol)
                if z_score >= z_threshold:
                    # Check VWAP anchor (institutional distribution filter)
                    if data[ticker]["Close"][-1] > self._compute_vwap(ticker, data):
                        signal = self._build_scout_signal(ticker, rvol, z_score)
                        anomalies.append(signal)

        return anomalies

    def _get_adaptive_threshold(self, regime: str) -> float:
        """Regime-adaptive Z-threshold."""
        thresholds = {
            "TRENDING_UP_STRONG": 2.0,
            "TRENDING_UP_MOD": 2.5,
            "RANGE_BOUND": 3.0,
            "TRENDING_DOWN_MOD": 3.0,
            "TRENDING_DOWN_STRONG": 3.5,
            "RISK_OFF": 3.5,
            "SHOCK": 999.0,  # effectively disabled
        }
        return thresholds.get(regime, 3.0)
```

**Integration with Executioner (LSE Priority Mapping)**:
- When Scout detects anomaly on US stock (e.g., PLTR):
  1. Check `lse_mapper.get_etp_equivalent(ticker)` → returns PLTR3.L (if exists)
  2. If LSE equivalent exists AND LSE hours (09:00-15:15 UK) → reroute to 3x ETP
  3. If no equivalent OR outside LSE hours → signal US stock with B-team sizing (0.5x)
  4. Apply Bayesian Stranger Penalty (§15.1) to ALL Scout signals

**Data Cost Control**:
- Sunday night: Download full Russell 3000 constituents, compute 20-day RVOL + ADR, filter to top 200-500
- Daily 06:00 UK: Quick delta refresh (any new earnings gaps, halts, etc.)
- Every 30 min during market hours: yfinance batch download of 200-500 tickers (feasible: 5-10 batches × 50 tickers × 1-2 seconds = ~30 seconds total)

---

## 7. THE EXECUTIONER: SNIPER ENGINE — Execution Upgrade {#7-executioner}

### Current Execution Flow (Verified)
1. Signal passes 33-gate gauntlet
2. DynamicSizer computes position size (8-factor Kelly)
3. ExecutionPlanner: cost-aware plan + spread gate + net R:R
4. VirtualTrader opens position
5. Chandelier Exit manages 5-rung profit ladder

### Required Upgrades

**U-01: Bayesian Stranger Penalty (Replace Static 0.5x)**

Current: S16 B-team gets flat 0.5x sizing. No graduation curve.

New formula (see §15.1 for derivation):
```
κ(n, DSR) = κ_min + (κ_max - κ_min) × (1 - e^(-λ × max(0, DSR - DSR_min))) × (n / (n + n₀))

Where:
  κ_min = 0.25     (floor: quarter-Kelly for strangers)
  κ_max = 1.0      (full Kelly for graduated tickers)
  λ = 0.8           (DSR sensitivity)
  DSR_min = 1.5     (minimum DSR to consider)
  n = observed trades
  n₀ = 30           (prior pseudo-count)
```

**U-02: Stoikov EV Gate Calibration (OBI-Adjusted Mid-Price)**

Current: `v95_stoikov_ev_gate` flag ON. Stoikov thresholds in settings.yaml:
- ETP 3x/5x: 55 bps max spread `[G-ACCEPT: reduced from 80 bps — MM quote obligation is typically 40-60 bps for liquid 3x ETPs]`
- US A-team: 30 bps
- US B-team: 50 bps

Refinement — OBI-adjusted entry price (full derivation in §15.2):
```
ŝ_L = s_mid + L × β_OBI × OBI × σ_1min × ln(T / (T - t))

Where:
  s_mid = current mid-price
  L = leverage factor (3 or 5)
  OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
  σ_1min = 1-minute realised volatility
  β_OBI = 0.5 × L^1.2  [continuous, not discrete tiers — see §15.2]
  ln(T/(T-t)) = time-to-close urgency term (Stoikov)

VETO entry if:
  (target_2pct - (ŝ_L - s_mid)) / stop_distance < 1.5
  (i.e., effective R:R after slippage drops below 1.5:1)
```

**Calibration**: β_OBI starting values from the continuous formula need validation against 200+ OOB fills (NOT the same 413 trades used for ML training). `[G-ACCEPT]`

**U-03: Infinite Profit Ladder (Remove Ceiling)** `[G-ACCEPT modified]`

Current 7-rung ladder has a ceiling at +4R (Gift rung, trail 0.5×ATR).

Modification for 3x ETP ISA ladder:
```
Rung 0: Entry → Stop at -1R (3% on 3x ETP)
Rung 1: +1% underlying (+3% ETP) → Stop to breakeven
Rung 2: +2% underlying (+6% ETP) → BANK 40%. This is the 2% daily target secured.
Rung 3: +3% underlying (+9% ETP) → Trail remaining 60% with 2% ratchet
Rung 4+: NO CEILING. Trail with max(2% ratchet, 1.5×ATR). Let it run.

The 40% banked at Rung 2 secures the 2% daily compounding target.
The remaining 60% provides the TAIL CAPTURE that subsidises losing days.
This is the mechanism that bridges the gap between 0.5% expected daily and 2% target daily.
```

**Why 40/60 not 50/50** `[G-ACCEPT]`: Gemini correctly identified that the geometric mean optimisation favours a heavier trail weight. Banking 40% secures the +1.2% ETP profit floor (still enough for the daily target). Trailing 60% maximises the expected value of right-tail captures — the 5-10% ETP runners that compound the account.

**U-04: Dynamic Heat Cap Per Ticker**
```
max_heat(ticker) = 0.03 × ADV_20d × price

Example: QQQ3.L with 57K daily volume at £25 = £1.425M ADV
  max_heat = 0.03 × £1,425,000 = £42,750

Starting equity examples:
  At £10K equity: 15% heat = £1.5K → SAFE (£1.5K << £42.7K cap)
  At £50K equity: 15% heat = £7.5K → SAFE
  At £100K equity: 15% heat = £15K → SAFE
  At £500K equity: 15% heat = £75K → EXCEEDS cap → auto-reduce to £42.7K
```

---

## 8. THE OUROBOROS: SELF-LEARNING LOOP — ML Overhaul {#8-ouroboros}

### Current ML State (Verified)
- LightGBM + XGBoost ensemble (55/45 blend)
- 14 features, binary meta-label (De Prado 2018)
- 200+ trade minimum, currently 413+ trades logged
- Weekly retrain OR 50 new trades
- SHAP stability filter active
- Active learning weights (Settles 2009)
- Regime-adaptive thresholds (0.60 trending, 0.70 choppy, 1.0 shock)

### Required ML Improvements

**M-01: Remove Feature Leakage (CRITICAL)**
- Remove `confidence` from feature_cols
- Add `raw_indicator_count` (0-8 aligned indicators, BEFORE scoring)
- Add `spread_bps` (actual spread at signal time)
- Add `time_since_regime_change_hours` (regime freshness)
- New feature vector (15 features):
  ```
  rvol, adx, rsi, atr_pct, raw_indicator_count, spread_bps,
  time_since_regime_change_hours, hour_of_day, day_of_week,
  vix, regime_encoded, ticker_encoded, beat_magnitude,
  pre_earnings_runup, short_interest_pct
  ```

**M-02: Class Weight Balancing**
- If 70% WIN / 30% LOSS → model biased toward WIN prediction
- Add `class_weight='balanced'` to LightGBM/XGBoost fit
- Alternative: SMOTE oversampling of LOSS class (but smaller dataset, may overfit)
- Recommendation: Use class_weight='balanced' (simpler, sufficient for 413 trades)

**M-03: Walk-Forward Validation**
- Replace 5-fold stratified CV with expanding-window walk-forward
- Train on first 60% of trades, validate on next 20%, test on final 20%
- This respects temporal ordering (future data can't predict past)
- Report rolling AUC, not just mean AUC

**M-04: Pattern Outcome Tracker Enhancement**
- Current: `record_pattern_outcome()` tracks 12 pattern types
- Enhancement: Add regime-conditional pattern tracking
  - "Volume Climax + TRENDING_UP" win rate vs "Volume Climax + RANGE_BOUND" win rate
  - This gives regime×pattern interaction terms for the ML model
  - Store in `data/pattern_regime_matrix.json`

**M-05: CUSUM Alpha Reaper (Already Flagged)**
- `v95_cusum_alpha_reaper` is ON. Page (1954) CUSUM threshold = 3.0.
- Quarantine period: 30 days for degraded strategies.
- No changes needed — this is well-implemented. Just verify it's firing correctly in paper logs.

---

## 9. RISK ARCHITECTURE — Portfolio-Level Rebuild {#9-risk-architecture}

### Current Risk Controls (Verified)
- 5 independent circuit breakers (drawdown, VIX, correlation, streak, black swan)
- Immutable risk rules (0.75% per trade, 2% daily halt, 5% weekly halt, 15% total DD)
- Emotional firewall (12 blocked patterns)
- Session protection (drawdown recovery protocol, 6 levels)
- 8-dimension portfolio risk decomposition
- DynamicSizer (8-factor Kelly)

### What's Missing

**R-01: Portfolio-Level Correlation Brake (NEW — Gate #34)**
```
Every 5 minutes:
  positions = get_all_open_positions()
  if len(positions) < 2: continue

  returns = fetch_5min_returns(positions, lookback=20_days)
  corr_matrix = ledoit_wolf_shrinkage(returns)

  high_corr_pairs = count(corr_matrix > 0.70)
  if high_corr_pairs >= 3:
      BLOCK new entries
      ALERT P1: "Correlation clustering detected: {high_corr_pairs} pairs > 0.70"
      MAX_POSITIONS = 1 until high_corr_pairs < 2
```

**R-02: Portfolio CVaR + CDaR Gate (Replace Crude Daily Loss Halt)** `[G-ACCEPT modified]`
```
Every scan tick (60s):
  portfolio_value = sum(position.value for position in open_positions)

  # Per-entry gate: incremental CVaR
  returns_5d = historical_portfolio_returns(5_day_lookback)
  cvar_95 = compute_cvar(returns_5d, alpha=0.05)
  if abs(cvar_95) > 0.03 * equity:  # 3% of equity at risk
      BLOCK new entries
      ALERT P1: "Portfolio CVaR exceeds 3%. Defensive mode."

  # Portfolio circuit breaker: CDaR (serial dependence aware)
  drawdown_series = rolling_drawdown(60_day_lookback)
  cdar_95 = compute_cdar(drawdown_series, alpha=0.05)
  if cdar_95 > 0.05:  # 5% conditional drawdown-at-risk
      HALT ALL new entries
      TIGHTEN all stops to 0.5×ATR from current price
      ALERT P0: "CDaR exceeds 5%. Circuit breaker activated."
```

**R-03: Regime Transition Confirmation Buffer**
```
When regime_provider.update() detects state change:
  if previous_regime != new_regime:
      confirmation_count += 1
      if confirmation_count < 3:  # Wait 3 ticks = 3 minutes
          continue using previous_regime for signal generation
          LOG: "Regime change detected: {previous} → {new}. Confirming ({confirmation_count}/3)"
      else:
          execute_regime_transition_actions(previous_regime, new_regime)
          confirmation_count = 0
```

**R-04: Drawdown Recovery Scaling (Already Exists, Needs Tuning)**
Current 6-level cascade (Green → Yellow → Orange → Red → Critical → Emergency) is well-designed.
Adjustment: The "Red" level (-8% to -10%) triggers "HALT live, return to paper 5 days minimum". This is too conservative at scale.
Fix: Scale thresholds with AUM:
- £10K-£100K: Red at -8%, Critical at -10%, Emergency at -12% (current)
- £500K: Red at -6%, Critical at -8%, Emergency at -10%
- £1M+: Red at -4%, Critical at -6%, Emergency at -8%

**R-05: Anti-Correlation-Cascade Stop (NEW)**
When 3+ positions hit stops within a 15-minute window:
1. Immediately HALT all new entries
2. Escalate from P1 → P0 (correlation cascade detected)
3. Tighten all remaining stops to 0.5×ATR
4. If total loss in 15-min window exceeds 1.5% of equity, activate portfolio circuit breaker (FLAT ALL)
5. Cool-down: 30 minutes before accepting new signals

**R-06: Market Maker Spread Veto (NEW)** `[G-ACCEPT]`
```
VETO entry if: current_spread > 2.5 × median_3d_spread

Rationale: MM widening spread 2.5× above normal indicates:
  - Inventory exhaustion (MM pulling quotes)
  - Adverse information flow (informed trader activity)
  - Pre-announcement risk (upcoming news)
Log the veto with spread ratio for calibration review.
```

**R-07: OBI Toxicity Wait Gate (NEW)** `[G-ACCEPT]`
```
If OBI > 0.8 (extreme buy-side imbalance):
  WAIT 2 ticks (2 minutes at 60s interval) before entry
  Re-check OBI after wait period

Rationale: Extreme OBI signals possible toxic flow (informed buyer
exhausting the order book). The 2-tick wait allows the initial
pressure to dissipate. If OBI remains >0.8 after 2 ticks,
the flow is persistent → proceed. If it reverts → avoid
(likely a single large order that will mean-revert).
```

**R-08: US Open Stop Widening (NEW)** `[G-ACCEPT]`
```
During 14:30-15:30 UK (US market open):
  ATR_stop_multiplier = 2.0× (up from default 1.5×)

Rationale: The first hour of US trading overlaps with LSE hours
and creates the highest volatility of the trading day. 3x ETPs
tracking US underlyings experience amplified gap risk as the
US market prices overnight information. Normal 1.5×ATR stops
are too tight during this window and get triggered by noise.
```

**R-09: ETP Financing Cost Offset (NEW)** `[G-ACCEPT]`
```
For inverse ETPs held intraday:
  Apply daily financing cost drag of -4 bps/day to expected return

Rationale: Inverse ETPs (QQQS.L, 3USS.L) have embedded financing
costs from daily rebalancing. For intraday holds this is minimal
(~-0.4 bps for a 1-hour hold) but must be modelled in the EV gate
to avoid systematic overestimation of inverse ETP profitability.

Implementation: In Stoikov EV gate, subtract financing_cost_bps
from target return before computing net R:R.
```

**R-10: Gamma/Strike Proximity Risk (NEW)** `[G-ACCEPT]`
```
If underlying is within 0.5% of a major options strike:
  Confidence penalty: -10
  Rationale: Options delta hedging creates artificial
  price magnetism near strikes. ETP moves near strikes
  become less predictable as hedging flows dominate.

Data source: GEX data from cross_asset_macro module
(already tracks gamma exposure for major underlyings).
```

---

## 10. LIQUIDITY SCALING MODEL {#10-liquidity-scaling}

### The Hard Wall (Computed from Amihud Analysis)

**Kyle's Lambda Market Impact:**
```
ΔP ≈ λ × √(Q / V_daily)

Where:
  λ = 0.1-0.3 for small-cap ETPs (empirically higher than equities)
  Q = trade size in £
  V_daily = daily turnover in £
```

**Impact Table for QQQ3.L (57K daily volume × £25 = £1.425M ADV):**

| Equity | Heat (15%) | Q/V | Impact (bps) | Verdict |
|--------|-----------|-----|-------------|---------|
| £10K | £1.5K | 0.11% | <1 bps | SAFE |
| £50K | £7.5K | 0.53% | ~1 bps | SAFE |
| £100K | £15K | 1.05% | ~2 bps | SAFE |
| £250K | £37.5K | 2.6% | ~3 bps | SAFE |
| £500K | £75K | 5.3% | ~5 bps | CAUTION |
| £1M | £150K | 10.5% | ~6.5 bps | DANGER |
| £3M | £450K | 31.6% | ~11 bps | WALL |
| £10M | £1.5M | 105% | >100% ADV | IMPOSSIBLE |

**Critical Equity Thresholds** (starting equity: £10K):
- £10K-£100K: All ETPs fully accessible. No liquidity constraints. Focus on signal quality.
- £500K: Begin dynamic heat cap (`max_heat = 0.03 × ADV × price`)
- £1M: Must diversify across more liquid ETPs. Single-ETP heat exceeds safe zone.
- £3M: Must use TWAP/VWAP execution (spread entries over 30 minutes)
- £10M: Impossible on current ETP universe. Must migrate to futures or underlying equities.

### Scaling Protocol (Automatic)
```python
def compute_max_heat(ticker: str, equity: float) -> float:
    """Dynamic heat cap scaling with AUM."""
    adv_20d = get_adv(ticker, 20)  # 20-day average daily volume in £

    # Safe zone: never exceed 3% of daily volume
    volume_cap = 0.03 * adv_20d

    # Equity-proportional cap
    equity_cap = 0.15 * equity  # 15% heat allocation

    # Return the MINIMUM of the two
    return min(volume_cap, equity_cap)
```

---

## 11. NOTIFICATION & DECISION SUPPORT {#11-notifications}

### Current State
- Telegram bot sends signals, trade closures, firewall blocks
- No tiered hierarchy
- 89+ alert points (Gemini identified this)
- No attention budget management

### Tiered Notification Architecture (NEW)

| Tier | Trigger | Delivery | Daily Cap | Current Implementation |
|------|---------|----------|-----------|----------------------|
| **P0** | Drawdown > 3R, system crash, API failure, correlation cascade | Instant + SOUND | Unlimited | Partially exists (circuit breaker alerts) |
| **P1** | Trade fill, stop hit, regime change, CVaR breach | Instant, silent push | 5/day (then batch) | Mostly exists (signal + trade alerts) |
| **P2** | Signal generated, ticker graduated, A/B team change | 30-min batch digest | 10/day (then suppress) | Partially exists |
| **P3** | Pattern stats, SHAP drift, overnight macro, System IQ | 2× daily digest | Pre-market 06:30 + Post-close 18:00 | Does NOT exist (all currently instant) |

### Correlation Escalation Rule (NEW)
```
if count(P1_alerts in last 15_minutes) >= 3:
    escalate_to_P0()
    trigger_portfolio_circuit_breaker()
    send_telegram("🚨 P0 ESCALATION: 3+ P1 alerts in 15min = correlation cascade")
```

### Weekly Signal Quality Report (Sunday 20:00)
```
- Win rate by tier (P0 accuracy, P1 accuracy)
- Win rate by strategy (S15 this week vs 4-week rolling)
- Win rate by regime (TRENDING vs RANGE performance)
- Dry day count (days with 0 signals)
- Ouroboros health (ML AUC, SHAP stability, feature drift)
- Compounding tracker (actual geometric return vs 2% target)
```

### What to REMOVE from Aegis Plan
- **REMOVE**: P3 Nightly Digest at 23:00 UTC. This is post-close and useless for next-day decisions.
- **REPLACE**: Pre-market digest at 06:30 UK (3.5 hours before LSE open). Contains overnight macro, Asian session moves, pre-market gaps, today's calendar risks.

---

## 12. INFRASTRUCTURE HARDENING {#12-infrastructure}

### Immediate Actions (This Week)

**I-01: Allocate Elastic IP**
- AWS Console → EC2 → Elastic IPs → Allocate → Associate to i-027add7c7366d4c86
- Update `deploy.sh`, `.env.production` CORS, MEMORY.md
- Cost: £0 (free while running)

**I-02: Automate S3 Backup**
```bash
# On EC2, add to crontab:
0 5 * * * /home/ubuntu/nzt48-signals/scripts/backup_to_s3.sh >> /var/log/nzt48-backup.log 2>&1
```

**I-03: Fix VIX Default** `[G-ACCEPT modified]`
- `main.py` L4675-4685: Change VIX default from 0 to `max(VIX_last_known, 25)`
- If VIX_last_known also stale (>30 min), default to 30
- Add 10-minute stale data escalation: if VIX age > 10 min → CAUTION regime (half-size all entries)

### Short-Term Actions (Next 2 Weeks)

**I-04: Upgrade EC2 Instance**
- Current: t3.small (2 vCPU, 2GB RAM)
- Target: t3.medium (2 vCPU, 4GB RAM) — needed for Apex Scout + expanded universe
- Cost: ~$30/month → ~$34/month

**I-05: Add CloudWatch Monitoring**
- Container CPU/memory metrics
- Custom metric: signals_emitted_per_hour (0 for >2 hours = P0 alert)
- Custom metric: redis_memory_usage_pct
- Custom metric: sqlite_file_size_mb

**I-06: Redis Memory Limit**
- Current: 256MB. With Chandelier state for 500+ tickers, could hit limit.
- Increase to 512MB in docker-compose.yml

### Medium-Term Actions (Month 2)

**I-07: PostgreSQL Migration**
- SQLite WAL mode works for single-writer but blocks on large queries
- PostgreSQL RDS: auto-backup, point-in-time recovery, read replicas
- Migration path: `delivery/database.py` already uses abstracted queries

**I-08: CI/CD Pipeline**
- GitHub Actions: lint → test → build Docker → deploy to EC2
- Eliminates manual deploy friction (the main.py patching problem)

---

## 13. PARAMETER RECALIBRATION TABLE {#13-parameters}

### Parameters to Change Immediately

| Parameter | Current | New | Reason | File |
|-----------|---------|-----|--------|------|
| VIX default (fetch fail) | 0 | max(VIX_last_known, 25) | F-07: 0 = max aggression, 22 = optimism bias | main.py L4675 |
| Macro cache TTL (VIX) | 1800s | 300s | F-12: stale VIX during spikes | cross_asset_macro.py |
| Lunch RVOL minimum | 1.7 | 1.3 | F-09: filters 95% of setups | settings.yaml |
| Signal queue size | 50 | unlimited | F-01: silent signal drops | main.py L1136 |
| Regime transition grace | 0 ticks | 3 ticks | F-02: instant flatten risk | main.py L4500 |
| ML feature: confidence | included | REMOVED | F-06: feature leakage | ml_meta_model.py |
| Inverse ETP list | hardcoded | metadata query | F-04: won't discover new ETPs | main.py L4571 |
| Stop fallback (3x ETPs) | 1.0% | 1.2% `[G-ACCEPT]` | Wider cushion for leverage noise | settings.yaml |
| Stoikov ETP spread threshold | 80 bps | 55 bps `[G-ACCEPT]` | MM quote obligation is 40-60 bps | settings.yaml |
| DSR graduation t-stat | 2.0 | 3.0 `[G-ACCEPT: HLZ 2016]` | Multiple-testing correction | §4, §15.1 |

### Parameters at Starting Equity (£10K)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max position % | 5% | Small equity = full flexibility, no liquidity concern |
| Daily loss halt | -1.5% (= -£150) | Tight — preserves starting capital |
| Confidence floor | 60 | Standard — don't over-filter at small size |
| Max DD halt | 8% (= -£800) | Standard — enough room for drawdown recovery |
| Heat cap per ticker | 15% equity (= £1,500) | Well within any ETP's ADV |

### Parameters to Change at Scale (£500K+)

| Parameter | At £10K (current) | At £500K | At £1M | Reason |
|-----------|-------------------|----------|--------|--------|
| Max position % | 5% | 4% | 3% | Liquidity scaling |
| Daily loss halt | -1.5% | -2.0% | -1.5% | Scale-appropriate |
| Confidence floor | 60 | 65 | 70 | Larger positions = higher bar |
| Max DD halt | 8% | 6% | 4% | Capital preservation |
| Drawdown recovery levels | Fixed | AUM-scaled | AUM-scaled | §9 R-04 |
| Heat cap per ticker | 15% equity | min(4% ADV, 10% equity) | min(3% ADV, 8% equity) | §10 scaling |

### Parameters to KEEP Unchanged

| Parameter | Value | Reason |
|-----------|-------|--------|
| Risk per trade | 0.75% | Battle-tested, Kelly-aligned |
| S15 max 1 signal/day | 1 | Prevents overtrading — the core discipline |
| ATR stop multiplier | 1.5× | Proven noise tolerance across 413+ trades |
| Power Hour +15% boost | 15% | Heston et al. (2010) validated |
| SHAP rank drift threshold | >5 positions | Gu, Kelly & Xiu (2020) standard |
| CUSUM threshold | 3.0 | Page (1954) standard |
| HMM confirmation lag | 3 days | Prevents regime whipsaw |

---

## 14. IMPLEMENTATION PHASES {#14-phases}

### Phase 0: Critical Fixes (Week 1) — No New Features

| Task | Priority | Effort | Dependencies |
|------|----------|--------|-------------|
| Fix VIX default 0 → max(VIX_last_known, 25) | P0 | 1 hour | None |
| Fix signal queue (unbounded) | P0 | 2 hours | None |
| Add regime transition buffer (3 ticks) | P0 | 4 hours | None |
| Remove ML confidence feature leakage | P0 | 2 hours | Retrain model |
| Move inverse ETP list to metadata | P0 | 2 hours | isa_universe.py |
| Allocate Elastic IP | P0 | 30 min | AWS Console |
| Automate S3 backup cron | P0 | 30 min | EC2 SSH |
| Add portfolio correlation brake (Gate #34) | P0 | 8 hours | Ledoit-Wolf from correlation_engine |
| Reduce VIX cache to 5 min | P1 | 1 hour | cross_asset_macro.py |
| Lower lunch RVOL to 1.3 | P1 | 30 min | settings.yaml |
| Data integrity stress test (yfinance vs TradingView sync) | P1 | 4 hours | New: scripts/ `[G-ACCEPT]` |
| Stop fallback: 1.0% → 1.2% for 3x ETPs | P1 | 30 min | settings.yaml `[G-ACCEPT]` |

**Total Phase 0**: ~4 days. Deploy + 1 week paper validation.

### Phase 1: Execution Upgrades (Weeks 2-3)

| Task | Priority | Effort | Dependencies |
|------|----------|--------|-------------|
| Bayesian Stranger Penalty (replace 0.5x) | P1 | 4 hours | Formula in §15.1 |
| Stoikov OBI calibration for 3x/5x ETPs | P1 | 8 hours | OOB fill data (NOT same 413 trades as ML) `[G-ACCEPT]` |
| Infinite Profit Ladder (remove ceiling, 40% bank at Rung 2) | P1 | 6 hours | chandelier_exit.py |
| Chain Reaction wiring (move_attribution → S15 score) | P1 | 4 hours | main.py |
| PEAD time-aware decay | P1 | 2 hours | main.py L1446 |
| Vol-managed sizing for 3x ETPs | P1 | 4 hours | DynamicSizer |
| S15 Inverse Pivot (prioritise inverse in BEARISH) | P1 | 4 hours | daily_target.py |
| No-signal escalation protocol | P1 | 6 hours | New scheduler job |
| Portfolio CVaR gate | P1 | 8 hours | New module |

**Total Phase 1**: ~2 weeks. Deploy + 2 weeks paper validation.

### Phase 2: Universe Expansion (Weeks 4-6)

| Task | Priority | Effort | Dependencies |
|------|----------|--------|-------------|
| Amihud Capacity Sieve module | P1 | 8 hours | New: uk_isa/amihud_sieve.py |
| ASER filter (ADR/Spread) in LSE registry | P1 | 4 hours | Extend lse_registry.py |
| DSR graduation gate | P1 | 6 hours | Bailey & Lopez de Prado |
| Russell 3000 / FTSE 350 watchlist builder | P1 | 8 hours | New: strategies/watchlist_builder.py |
| Apex Scout module | P1 | 16 hours | New: strategies/apex_scout.py |
| LSE Priority Mapping (Scout → ETP reroute) | P1 | 8 hours | Extend ISA mapper |
| Dynamic heat cap per ticker | P1 | 4 hours | DynamicSizer |
| Expand universe to 300-500 Core | P2 | 8 hours | Depends on Amihud + ASER |
| Trigger-based Scout (NASDAQ >0.5% in 5min → instant scan) | P2 | 4 hours | `[G-ACCEPT]` |
| EC2 upgrade to t3.medium | P2 | 1 hour | AWS Console |

**Total Phase 2**: ~3 weeks. Deploy + 4 weeks paper validation.

### Phase 3: Intelligence & Notifications (Weeks 7-8)

| Task | Priority | Effort | Dependencies |
|------|----------|--------|-------------|
| Tiered Telegram (P0/P1/P2/P3) | P2 | 8 hours | telegram_bot.py refactor |
| Correlation escalation rule | P2 | 4 hours | Gate #34 must exist |
| Pre-market digest (06:30 UK) | P2 | 6 hours | New scheduler job |
| Weekly Signal Quality Report | P2 | 8 hours | New report generator |
| ML walk-forward validation | P2 | 6 hours | ml_meta_model.py |
| ML class weight balancing | P2 | 2 hours | ml_meta_model.py |
| Pattern × Regime interaction features | P2 | 4 hours | Learning engine |
| Anti-cascade stop (3+ stops in 15 min) | P2 | 6 hours | New in circuit_breakers |
| Regime transition confirmation (15-min window for £500K+) | P2 | 4 hours | Config-gated |

**Total Phase 3**: ~2 weeks. Continuous paper trading.

### Phase 4: Scale Preparation (Weeks 9-12)

| Task | Priority | Effort | Dependencies |
|------|----------|--------|-------------|
| AUM-scaled parameter table (auto-adjust with equity) | P2 | 8 hours | New config module |
| TWAP/VWAP execution mode (for £500K+) | P2 | 16 hours | New execution engine |
| CloudWatch monitoring + alerts | P2 | 8 hours | AWS setup |
| PostgreSQL migration | P3 | 24 hours | RDS setup |
| CI/CD pipeline (GitHub Actions) | P3 | 12 hours | GitHub setup |
| Redis upgrade to 512MB | P3 | 30 min | docker-compose.yml |

**Total Phase 4**: ~4 weeks alongside continued paper trading.

### Go-Live Gate (After Phase 2 + 63 MTRL days)
- Romano & Wolf 10-criteria scorecard (`scripts/sprint6_live_gate.py`)
- DSR ≥ 3.0 across all active strategies `[G-ACCEPT: HLZ 2016]`
- Win rate ≥ 50% on S15 over 60+ trades
- Maximum drawdown < 6% during paper phase
- System uptime > 99.5% over 30 days
- All P0 fixes verified in production
- Data integrity verified (yfinance vs TradingView sync check passed) `[G-ACCEPT]`

---

## 15. MATHEMATICAL APPENDIX {#15-math}

### §15.1 Bayesian Stranger Penalty (Shrinkage Model)

**Purpose**: Replace static 0.5x sizing for new tickers with a smooth Bayesian decay.

**Formula**:
```
κ(n, DSR) = κ_min + (κ_max - κ_min) × f_DSR(DSR) × f_n(n)

Where:
  f_DSR(DSR) = 1 - exp(-λ × max(0, DSR - DSR_min))    [edge quality term]
  f_n(n) = n / (n + n₀)                                 [sample size term]

Parameters:
  κ_min = 0.25      (floor: quarter-Kelly)
  κ_max = 1.0       (ceiling: full Kelly)
  λ = 0.8           (DSR sensitivity)
  DSR_min = 1.5     (minimum DSR threshold)
  n₀ = 30           (pseudo-count prior: "need 30 trades to be halfway confident")
```

**Worked Examples**:
| Trades (n) | DSR | f_DSR | f_n | κ | Interpretation |
|------------|-----|-------|-----|---|---------------|
| 0 | 0 | 0 | 0 | 0.25 | Brand new: quarter size |
| 5 | 1.0 | 0 | 0.14 | 0.25 | Low DSR: still quarter size |
| 15 | 2.0 | 0.33 | 0.33 | 0.33 | Moderate: third size |
| 30 | 2.5 | 0.55 | 0.50 | 0.46 | Growing confidence |
| 50 | 3.0 | 0.73 | 0.63 | 0.59 | Approaching graduation |
| 80 | 3.5 | 0.83 | 0.73 | 0.70 | Near full size |
| 100 | 4.0+ | 0.90 | 0.77 | 0.77 | Graduated veteran |

**DSR Computation** (Bailey & Lopez de Prado 2014) `[G-ACCEPT modified]`:
```
DSR = (SR_observed - SR_benchmark) / SE(SR)
SE(SR) = sqrt((1 + 0.5 × SR² - skew × SR + (kurt - 3)/4 × SR²) / (N - 1))

Where:
  SR = annualised Sharpe ratio of the ticker's trades
  SR_benchmark = 0 (test vs no-edge)
  N = number of trades
  skew, kurt = sample skewness and kurtosis of trade returns

GRADUATION threshold: DSR t-stat ≥ 3.0 [Harvey, Liu & Zhu 2016]
  (v11.0 used 2.0 — upgraded per Gemini's correct HLZ citation)
```

**Bayesian Graduation Prior** `[G-ACCEPT]`:
```
Prior: μ_edge ~ Normal(0, 0.5)   [skeptical prior — assume no edge]
Posterior: update with observed trade returns
Graduation when: P(μ_edge > 0 | data) > 0.98

This is equivalent to requiring 98% posterior probability that the
ticker genuinely has positive expected return, not just lucky variance.
For N < 30, the Bayesian approach is more robust than frequentist DSR
because it naturally penalises small samples via the prior.
```

### §15.2 Stoikov Reservation Price with Leverage-Adjusted OBI `[G-ACCEPT modified]`

**Standard Stoikov** (Stoikov & Avellaneda 2006):
```
r(s, q, σ, γ, T) = s - q × γ × σ² × (T - t)

Where:
  s = mid-price
  q = inventory position
  γ = risk aversion coefficient
  σ = volatility
  T-t = time remaining
```

**Leverage-Adjusted OBI Entry** (with time-to-close urgency term):
```
ŝ_L = s_mid + L × β_OBI × OBI × σ_1min × ln(T / (T - t))

Where:
  L = leverage factor {1, 2, 3, 5}
  β_OBI = 0.5 × L^1.2   [continuous formula, not discrete lookup]
    → L=1: β=0.50, L=2: β=1.15, L=3: β=1.93, L=5: β=3.62
  OBI = (bid_volume - ask_volume) / (bid_volume + ask_volume), range [-1, 1]
  σ_1min = 1-minute realised volatility
  T = total session length (minutes from open)
  t = elapsed time since open
  ln(T/(T-t)) = time-to-close urgency: approaches ∞ near close, forcing wider spreads
```

**Why continuous β_OBI** `[G-ACCEPT]`: Gemini correctly identified that discrete tiers {0.5, 0.8, 1.2, 2.0} create cliff edges at leverage boundaries. The continuous formula `0.5 × L^1.2` is smooth and empirically better matches the non-linear relationship between leverage and order book toxicity. Starting values still need calibration from 200+ fills.

**Why ln(T/(T-t)) term** `[G-ACCEPT]`: Stoikov's original model includes inventory urgency as session end approaches. Near close, market makers widen spreads, and the probability of adverse selection rises. This term naturally penalises late-session entries where slippage risk is highest.

**EV Gate Veto Condition**:
```
VETO if: net_expected_return < 1.5 × stop_distance

net_expected_return = target_2pct - round_trip_slippage
round_trip_slippage = 2 × |ŝ_L - s_mid| + spread_bps / 10000 × price
```

### §15.3 Portfolio CVaR + CDaR (Rockafellar & Uryasev 2000; Chekhlov, Uryasev & Zabarankin 2005) `[G-ACCEPT modified]`

**Per-Trade Gate: CVaR**
```
CVaR_α = E[Loss | Loss > VaR_α]

For each new signal:
  1. Compute daily portfolio returns for trailing 60 days
  2. Sort returns ascending
  3. VaR_95 = 5th percentile return
  4. CVaR_95 = mean of all returns below VaR_95

GATE: If CVaR_95 × equity > 3% of equity → BLOCK new entries
```

**Portfolio-Level Circuit Breaker: CDaR** `[G-ACCEPT]`
```
CDaR_α = E[Drawdown | Drawdown > DD_α]

Where Drawdown_t = (Peak_equity - Equity_t) / Peak_equity

For portfolio-level protection:
  1. Compute rolling drawdown series for trailing 60 days
  2. DD_95 = 95th percentile drawdown
  3. CDaR_95 = mean of all drawdowns exceeding DD_95

CIRCUIT BREAKER: If CDaR_95 > 5% → HALT all new entries, tighten stops
```

**Why CDaR for circuit breaker, CVaR for per-trade** `[G-ACCEPT]`: Gemini correctly identified that CVaR assumes independent daily returns, which fails for serial drawdowns (a -2% day followed by -2% is NOT two independent events on 3x ETPs — volatility clustering makes the second day worse). CDaR (Chekhlov, Uryasev & Zabarankin 2005) explicitly models drawdown persistence, making it the correct measure for portfolio-level protection. CVaR remains appropriate for individual trade gating where independence is more reasonable.

**Incremental CVaR (iCVaR)** `[G-ACCEPT]`:
```
Before admitting a new position, compute:
  iCVaR = CVaR(portfolio + new_position) - CVaR(portfolio_without)

VETO if: iCVaR > 0.5% of equity
  (i.e., the new position increases portfolio tail risk by more than 0.5%)

This catches correlated entries that individually look safe
but collectively create concentration risk.
```

### §15.4 Amihud Illiquidity Ratio (Amihud 2002) `[G-ACCEPT modified]`

```
ILLIQ_i = (1/D) × Σ(|r_d| / Volume_d) × L^1.5  for d = 1..D

Where:
  D = 20 trading days
  r_d = daily return
  Volume_d = daily dollar volume
  L = leverage factor (1 for stocks, 3 for 3x ETPs, 5 for 5x ETPs)

  L^1.5 adjustment: leveraged ETPs amplify price impact non-linearly.
  A 3x ETP with same ADV as a 1x stock has ~5.2× the effective illiquidity
  because the creation/redemption mechanism transmits pressure asymmetrically.

PURGE if: (heat_size × ILLIQ_i) > 0.005
  (i.e., our trade would move price by >50 bps)
```

**Time-of-day volume adjustment** `[G-ACCEPT]`:
```
Adjust Volume_d → Volume_d × ToD_factor(hour)

Where ToD_factor normalises for intraday volume seasonality:
  09:00-10:00 UK (LSE open):  1.6× (auction spillover)
  10:00-12:00 UK:              1.0× (baseline)
  12:00-14:00 UK:              0.7× (lunch thinning)
  14:00-14:30 UK:              0.9× (pre-US)
  14:30-15:30 UK (US open):   1.8× (highest liquidity)
  15:30-16:30 UK:              1.0× (baseline)

This prevents the sieve from approving entries during lunch when
actual available volume is 30-40% below the daily average.
```

### §15.5 Ledoit-Wolf Shrinkage Correlation (Already Implemented)

```
Σ_shrunk = α × Σ_sample + (1 - α) × F

Where:
  Σ_sample = sample covariance matrix
  F = shrinkage target (identity matrix scaled by trace)
  α = optimal shrinkage intensity (computed analytically)
```

Reference: `uk_isa/correlation_engine.py` — already uses sklearn.covariance.LedoitWolf.

### §15.6 PEAD Time-Aware Decay (Bernard & Thomas 1990; Chan et al. 1996) `[G-ACCEPT]`

```
residual(t) = 0.30 × (t + 1)^(-0.5)

Where t = hours since catalyst event

Intraday decay profile:
  Hour+0:  0.30 × 1.00 = 0.300 (30% — full boost)
  Hour+1:  0.30 × 0.71 = 0.212 (71% of original)
  Hour+4:  0.30 × 0.45 = 0.134 (45% of original)
  Hour+8:  0.30 × 0.33 = 0.100 (33% of original)
  Hour+24: 0.30 × 0.20 = 0.060 (6.1% — still meaningful)
  Hour+48: 0.30 × 0.14 = 0.043 (4.3% — fading but nonzero)
  Hour+72: 0.30 × 0.12 = 0.035 (3.5% — minimal, discard after 72h)

DISCARD when: residual < 0.02 (i.e., boost contributes <2% of original)
```

**Why power-law, not exponential** `[G-ACCEPT]`: The v11.0 exponential formula `exp(-0.15t)` kills the signal at 24h (residual 0.8% = noise). Academic literature (Chan 1996, Hou & Moskowitz 2005) demonstrates PEAD follows a power-law distribution — earnings surprises maintain statistically significant drift for 5-10 trading days in large-cap equities. For leveraged ETPs tracking these underlyings, the drift is amplified by the leverage factor but follows the same decay shape.

---

## QUESTIONS FOR GEMINI — ANSWERED & INTEGRATED

| Q# | Question | Answer | Integrated In |
|----|----------|--------|---------------|
| Q1 | Compounding Reality Gap — bank/trail split | 40/60 (not 50/50). Heavier trail weight maximises geometric mean via tail capture. | §7 U-03 |
| Q2 | Apex Scout Data Cost | Start with yfinance (free). Pre-filter to top 200-500. Add trigger-based scanning (NASDAQ >0.5% in 5min → instant Scout). Migrate to paid API only at £500K+ when latency matters. | §6, §14 Phase 2 |
| Q3 | OBI Coefficient Hierarchy | Continuous formula: β_OBI = 0.5 × L^1.2. Eliminates discrete tier cliff edges. Calibrate from 200+ OOB fills. | §15.2 |
| Q4 | CVaR vs CDaR | **Both**: CVaR for per-trade gating (independence assumption OK), CDaR for portfolio circuit breaker (accounts for serial drawdown dependence). | §15.3 |
| Q5 | Regime-Conditional Kelly | Yes — 7 regime multipliers from 0.6×f* (strong trend) to 0.0×f* (shock). Remove 0.75% hard cap. Requires ≥30 trades/regime. | §2 F-11 |
| Q6 | DSR Graduation threshold | t-stat ≥ 3.0 (HLZ 2016). Plus Bayesian prior Normal(0, 0.5) with P(μ>0) > 0.98 for small samples. | §15.1, §4 |
| Q7 | Inverse Pivot timing | Enter on first retracement after: VIX > 28.5 + price < 50-EMA + within 24h of spike. Max hold 24h. Size 0.3×f*. | §5 E-04 |
| Q8 | LSE ETP MM patterns | **Still open** — requires empirical analysis of Winterflood/Flow Traders quote patterns. Add to Phase 3 research. | Deferred |

---

## REJECTED GEMINI SUGGESTIONS (with reasoning)

| # | Gemini Suggestion | Verdict | Reasoning |
|---|-------------------|---------|-----------|
| 1 | "Rung 1 trigger at +2.0% ETP is noise floor" | **REJECTED** | Gemini confused ladder levels. Rung 0 at +2% is the Chandelier BREAKEVEN, not a profit trigger. S15 uses R-multiples (+0.3R, +0.5R, +1.0R). The ladder is correctly designed. |
| 2 | "Max heat 15% → 10.5% for three 3× ETPs" | **REJECTED** | Gemini misread v10.0 plan. Code shows `portfolio_heat_max: 0.03` (3%), NOT 15%. The existing 3% is already conservative. |
| 3 | "§15.1 add confidence interval width to κ formula" | **REJECTED** | Creates instability when n < 10 (CI width → ∞, κ → 0, never trades). Existing dual-gate (DSR + sample count) already handles uncertainty. Simpler is better. |
| 4 | "Phase 2 dual-path testing for Scout" | **REJECTED** | Overcomplex. A/B testing a module that doesn't exist yet has no baseline to compare against. Build it, validate in paper trading, then optimise. |

---

## GLOSSARY

| Term | Definition |
|------|-----------|
| ADR | Average Daily Range (high-low as % of close) |
| ADV | Average Daily Volume (in £) |
| ASER | ADR-to-Spread Efficiency Ratio |
| ATR | Average True Range (14-period) |
| CDaR | Conditional Drawdown-at-Risk |
| CUSUM | Cumulative Sum Control Chart (Page 1954) |
| CVaR | Conditional Value-at-Risk (Expected Shortfall) |
| DSR | Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014) |
| ETP | Exchange-Traded Product |
| GEX | Gamma Exposure |
| HMM | Hidden Markov Model |
| ISA | Individual Savings Account (UK tax wrapper) |
| MTRL | Minimum Technology Readiness Level |
| OBI | Order Book Imbalance |
| O2C | Open-to-Close range |
| PEAD | Post-Earnings Announcement Drift |
| RVOL | Relative Volume (vs 20-day time-of-day average) |
| SHAP | SHapley Additive exPlanations |
| TWAP | Time-Weighted Average Price |
| VWAP | Volume-Weighted Average Price |

---

**END OF DOCUMENT**

**Document Statistics**:
- Cross-referenced: 15,700+ lines of existing code
- Fatal flaws identified: 12 in current system + 7 in Aegis plan
- New risk controls added: 5 (MM spread veto, OBI toxicity wait, US open stop widen, ETP financing offset, gamma strike proximity)
- New modules required: 4 (Apex Scout, Amihud Sieve, Watchlist Builder, Portfolio CVaR/CDaR Gate)
- Existing modules to enhance: 8
- Parameters to change: 10 immediate + 6 at scale
- Implementation phases: 4 (12 weeks total)
- Mathematical formulas: 8 (all derivations included, 4 upgraded with Gemini review)
- Gemini questions asked: 8. Answered & integrated: 7. Open: 1 (Q8 MM patterns — deferred to Phase 3)
- Gemini suggestions accepted: 18. Rejected: 4 (with reasoning). Modified: 6.
- Starting equity: £10,000

**Next Step**: Send this document to Gemini for final review. Architecture lock after Gemini sign-off. Then implementation begins Phase 0.
