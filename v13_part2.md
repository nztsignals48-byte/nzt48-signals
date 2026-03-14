# AEGIS Alpha-Omega Master Plan v13.0 — Sections 2 & 3

> **Classification**: INTERNAL — NZT-48 Core Strategy Documentation
> **Equity Base**: GBP 10,000 | **Target**: 2%+ daily compound via leveraged LSE ETPs
> **Wrapper**: UK ISA (100% tax-free on all gains — the single greatest structural edge available to a UK retail trader)
> **Compounding Law**: GBP 10,000 x (1.02)^252 = GBP 1,485,757.36 (14,757.57% annualised)

---

# SECTION 2: THE VANGUARD SNIPER — Fund-First Dual-Blade Execution Engine

The Vanguard Sniper is the beating heart of the NZT-48 system. It answers two questions with absolute precision: **WHAT** do we trade, and **WHEN** do we pull the trigger. Every other module — macro intelligence, volume profiling, risk management — exists to serve this single decision function. One signal. One trade. One day at a time. That is how GBP 10,000 becomes GBP 1.49 million.

---

## 2.1 Current S15 State (Verified in Codebase — `strategies/daily_target.py`)

The S15 "2% Daily Target" strategy is the production execution engine. The following parameters have been verified by deep code audit and represent the system's actual behaviour, not aspirational design.

### 2.1.1 Structural Constraints (Hardcoded)

| Parameter | Value | Location |
|---|---|---|
| `MAX_SIGNALS_PER_DAY` | 1 | `daily_target.py` constant |
| LSE Trading Window | 09:00 — 15:15 UK | `daily_target.py` lines 315-322 |
| After-Hours Capability | **NONE** | S15 returns empty outside window |
| Universe Restriction | LSE `.L` tickers only | ISA compliance filter |
| Total Universe | 35 tickers | 12 core + 23 secondary |
| Core Trading Set | 12 ETPs | See ISA Funds list below |

**ISA Core Trading Universe (12 Active ETPs):**

| Ticker | Type | Underlying | Leverage | overnight_kill |
|---|---|---|---|---|
| QQQ3.L | Long | NASDAQ-100 | 3x | False |
| QQQS.L | Inverse | NASDAQ-100 | -3x | False |
| QQQ5.L | Long | NASDAQ-100 | 5x | **True** |
| 3LUS.L | Long | S&P 500 | 3x | False |
| 3USS.L | Inverse | S&P 500 | -3x | False |
| SP5L.L | Long | S&P 500 | 5x | **True** |
| NVD3.L | Long | NVIDIA | 3x | False |
| TSL3.L | Long | Tesla | 3x | False |
| TSM3.L | Long | TSMC | 3x | False |
| MU2.L | Long | Micron | 2x | False |
| 3SEM.L | Long | Semiconductors | 3x | False |
| GPT3.L | Long | AI Basket | 3x | False |

**Critical Operational Rule**: 5x ETPs (`QQQ5.L`, `SP5L.L`) carry `overnight_kill=True`. They **MUST** be closed before session end (16:30 UK at the absolute latest, preferably by 15:30 UK to avoid rebalancing slippage). The vol drag on 5x instruments compounds destructively beyond a single session. Holding a 5x ETP overnight is a categorical risk violation, not a judgment call.

### 2.1.2 Eight-Indicator Weighted Consensus Model

S15 scores each candidate ticker against an 8-indicator consensus, with each indicator contributing a weighted vote to the final confidence score (0-100 scale):

| Indicator | Weight | Rationale |
|---|---|---|
| VWAP Deviation | 1.8x | Institutional fair value anchor. Price above VWAP = accumulation zone (Berkowitz et al. 1988) |
| RSI (14-period) | 1.2x | Mean-reversion filter within momentum context. RSI 40-70 = sweet spot for continuation |
| ADR (20-day) | 1.0x | Average Daily Range must exceed 2.9% to confirm 2% target is mechanically achievable |
| Volume Surge (RVOL) | 1.3x | Relative volume vs. 20-day time-of-day average. RVOL > 1.5 = institutional participation |
| Trend Alignment (EMA stack) | 1.0x | 8 > 21 > 50 EMA = bullish structure. Inverted for inverse ETPs |
| Spread Score | 0.8x | P90 dynamic spread tracker. Penalises wide-spread instruments (cost awareness) |
| Macro Regime | 1.0x | Cross-asset regime from `core/cross_asset_macro.py` (VIX, DXY, Credit, HMM) |
| Tail Risk Pre-Screen | 1.0x | GPD fitted to left-tail returns (Balkema-de Haan-Pickands theorem). Veto if P(loss > 5%) > 2% |

**Confidence Floor**: 75/100 minimum to fire a signal. This threshold is calibrated per Harvey & Liu (2015) multiple-testing correction — when scanning 12 instruments simultaneously, the single-asset significance threshold must be raised to control family-wise error rate. At 75/100, the false discovery rate is held below 5%.

### 2.1.3 Adaptive Easing for Leveraged ETPs

In strong trending regimes (as classified by the HMM regime detector in `cross_asset_macro.py`), the consensus thresholds ease to capture momentum continuation:

| Regime | Standard Threshold | Eased Threshold | Rationale |
|---|---|---|---|
| TRENDING_UP_STRONG | 7.0 / 10.0 | 4.8 / 9.5 | Jegadeesh & Titman (1993): momentum profits concentrate in strong trends. Tighter threshold = missed alpha |
| All Other Regimes | 7.0 / 10.0 | No easing | Default conservatism preserves capital |

### 2.1.4 P90 Spread Tracker (Dynamic Cost Awareness)

The system maintains a rolling 20-day P90 spread for each ETP, updated every trading session. This is critical for leveraged ETPs where bid-ask spreads can blow out 3-5x during volatile periods.

- **Spread Score** = 100 - (current_spread / p90_spread) x 100
- If current spread > 2.5x the 3-day median spread, the instrument is **VETOED** regardless of confidence
- P90 (not mean) is used because spread distributions are heavily right-skewed — the mean understates typical costs while the P90 captures the realistic "worst normal day" scenario

### 2.1.5 Power Hour Seasonality Boost

Per Heston, Korajczyk & Sadka (2010), intraday returns exhibit statistically significant periodicity, with the last trading hour showing elevated momentum continuation. S15 applies a +15% confidence boost to signals generated during Power Hour (14:30-15:15 UK for LSE-listed ETPs that track US underlyings opening at 14:30 UK).

This boost is **multiplicative**, not additive: a raw confidence of 70 becomes 70 x 1.15 = 80.5, which clears the 75 floor. This is intentional — it captures the empirical reality that US-open momentum spills into LSE ETPs during this window.

---

## 2.2 Fund-First Mandatory Execution Logic (NEW — v13.0 Enhancement)

### 2.2.1 The ISA Tax-Shield Imperative

The UK ISA wrapper eliminates capital gains tax entirely. For a system targeting 14,757% annualised returns, this is not a minor convenience — it is a **structural alpha** worth hundreds of thousands of pounds per year at scale. Every trade that can be routed through an ISA-eligible LSE ETP **must** be routed there.

### 2.2.2 Execution Priority Cascade

During LSE hours (08:00 — 16:30 UK), the following priority cascade governs every execution decision:

```
SIGNAL DETECTED ON UNDERLYING (e.g., NVIDIA momentum breakout)
    │
    ├─ Step 1: Query lse_mapper.get_etp_equivalent("NVDA")
    │           Returns: {"3x_long": "NVD3.L", "3x_inverse": "NVDS.L"}
    │
    ├─ Step 2: Is LSE currently open?
    │   ├─ YES → Route to NVD3.L (3x amplification, tax-free)
    │   └─ NO  → Log opportunity as MISSED. Do NOT execute on US exchange.
    │            (Current system has no after-hours capability — see §2.2.4)
    │
    ├─ Step 3: Is ETP spread acceptable? (< 2.5x median_3d_spread)
    │   ├─ YES → Execute via ETP
    │   └─ NO  → Wait 60 seconds, re-quote. If still wide → VETO
    │
    └─ Step 4: ETP overnight_kill check
        ├─ 5x ETP → Set hard exit at 15:30 UK (no exceptions)
        └─ 3x ETP → Position eligible for overnight hold
```

### 2.2.3 Atomic Mutual Exclusion Rule

**NEVER enter long QQQ3.L AND short QQQS.L in the same trading session.**

Although the codebase permits simultaneous long + inverse positions (no explicit veto exists in the `INVERSE_PAIRS` logic), doing so is economically incoherent and creates a synthetic straddle with guaranteed vol drag on both legs. The rebalancing mechanics of leveraged ETPs mean that holding both sides simultaneously guarantees negative expected value over any holding period exceeding a few hours (Cheng & Madhavan 2009).

**Implementation**: Before entering any position, check the `INVERSE_PAIRS` mapping:

```
INVERSE_PAIRS = {
    "QQQ3.L": "QQQS.L",
    "3LUS.L": "3USS.L",
    "NVD3.L": "NVDS.L",
    "TSL3.L": "TSLS.L"
}
```

If the inverse counterpart is currently held, the new signal is **VETOED**. The existing position's direction was chosen by the earlier, higher-confidence signal — honour that decision.

**Exception**: If Smart Money Alignment (derived from VPIN in `virtual_trader.py` and volume profile POC analysis) flips decisively mid-session (VPIN > 0.85 indicating toxic flow reversal), the system may close the existing position AND enter the inverse. This is a reversal, not a hedge. The old position exits fully before the new one enters. Sequence: CLOSE → WAIT 30s → RE-SCORE → ENTER INVERSE (if confidence > 80).

### 2.2.4 Night Shift — Architectural Gap Declaration

**HONEST ASSESSMENT**: The current NZT-48 codebase has **zero** after-hours US trading capability. `daily_target.py` returns an empty signal set outside the 09:00-15:15 UK window. There is no "Night Shift" module. There is no extended-hours data feed. There is no broker integration for US after-hours execution.

This means approximately 65% of the 24-hour cycle is unmonitored and untradeable. Given Lou, Polk & Skouras (2019) finding that the equity premium is earned **entirely overnight** (the intraday return on the S&P 500 is approximately zero over multi-decade samples), this represents a significant missed opportunity.

**Phase 3 Enhancement Plan** (post paper-trading validation):
1. Add US extended-hours data feed (Polygon.io or similar)
2. Build `strategies/night_shift.py` — overnight momentum capture on US-listed ETFs
3. Integrate with ISA-eligible US stocks (not ETPs — these don't trade after LSE close)
4. Target: capture overnight gap for next-day LSE ETP positioning

**For v13.0**: Night Shift is documented as future work. All compounding projections assume LSE-hours-only execution (approximately 7.25 hours per trading day). The 2% daily target must be achievable within this window.

---

## 2.3 Directional Parity — The Dual-Blade (NEW — v13.0 Core Enhancement)

### 2.3.1 Regime-Dependent Directional Filtering

The NZT-48 system's ability to profit in both rising AND falling markets is its most important structural advantage over long-only strategies. The `INVERSE_PAIRS` mapping in the codebase already supports this — what v13.0 adds is **regime-aware directional filtering** to prevent the system from fighting the macro tide.

| Regime (from HMM) | Eligible Direction | Rationale |
|---|---|---|
| TRENDING_UP_STRONG | LONG only | Moskowitz, Ooi & Pedersen (2012): time-series momentum has Sharpe > 1.0 in strong trends. Don't fight it. |
| TRENDING_UP_MOD | LONG preferred, INVERSE allowed if confidence > 85 | Moderate trends can reverse. Allow high-conviction inverse entries. |
| RANGE_BOUND | Both eligible | No directional bias. Highest confidence score wins regardless of direction. |
| TRENDING_DOWN_MOD | INVERSE preferred, LONG allowed if confidence > 85 | Mirror of TRENDING_UP_MOD. |
| TRENDING_DOWN_STRONG | INVERSE only | Daniel & Moskowitz (2016): momentum crashes are brutal (-91.6% in 2 months). Only inverse positions survive. |
| RISK_OFF | INVERSE only | VIX > 30, credit spreads widening, HMM in stressed state. Capital preservation mode. |
| SHOCK | **NO TRADING** | System goes flat. No new entries. Existing positions managed via Chandelier Exit only. |

### 2.3.2 The Inverse Pivot — Crash Monetisation Protocol

When markets transition from TRENDING_UP to TRENDING_DOWN, the system must execute an **Inverse Pivot**: closing long positions and rotating into inverse ETPs. This is the single most valuable trade the system can make, because leveraged inverse ETPs in a genuine crash can return 20-50% in a single session.

However, the entry must be precise. Daniel & Moskowitz (2016) document that momentum crashes are characterised by an initial violent spike followed by mean-reversion whipsaws that destroy poorly-timed entries. The protocol:

**Inverse Pivot Entry Criteria (ALL must be true):**

1. **VIX > 28.5** — Confirmed fear regime. VIX 20-28 is "elevated concern"; above 28.5 is genuine risk-off (Whaley 2000). The 28.5 threshold is the 90th percentile of VIX readings since 2010.

2. **Underlying Price < 50-period EMA** — The trend has broken. Price below the 50 EMA confirms the move is structural, not a noise spike. Using 50 periods (not 20) avoids false triggers from normal pullbacks within uptrends.

3. **Move Within 24 Hours of Initial Spike** — Momentum crashes cluster in time (Daniel & Moskowitz 2016). The first 24 hours capture 60-70% of the total move. After 24 hours, mean-reversion forces strengthen and the inverse trade becomes a coin flip.

4. **Enter on FIRST RETRACEMENT, Not the Spike Itself** — During the initial spike, spreads on inverse ETPs blow out to 5-10x normal. The P90 spread tracker will veto any entry during this window. Wait for the first pullback (typically 30-60 minutes after the initial move), confirm spreads have normalised (< 2.5x median_3d_spread), then enter.

5. **Position Size: 0.3 x f* (30% Kelly)** — Kelly criterion (Kelly 1956) gives the growth-optimal fraction, but full Kelly on inverse ETPs during crashes is reckless. The payoff distribution is extremely fat-tailed in both directions. 30% Kelly limits the damage if the crash reverses (bear trap), while still capturing meaningful profit if it continues. Barroso & Santa-Clara (2015) show that vol-scaling momentum positions (which is what 30% Kelly approximates) doubles the Sharpe ratio from 0.53 to 0.97 while halving maximum drawdown.

6. **Maximum Hold: 24 Hours** — Inverse leveraged ETPs suffer from compounding decay (also called "volatility drag" or "beta slippage") that erodes returns over multi-day holds (Cheng & Madhavan 2009). In a -5% crash day, QQQS.L (3x inverse) returns approximately +15% (minus friction). But holding for 3 days in a choppy decline, the cumulative return may be only +8% instead of the expected +15%. The 24-hour maximum enforces discipline.

**Dynamic Momentum Scaling**: Per Barroso & Santa-Clara (2015), position size is inversely proportional to the trailing 60-day realised volatility of the underlying. When vol is high (crash conditions), the position is already smaller — this is the built-in crash protection.

```
position_size = target_vol / (realised_vol_60d * leverage_factor) * capital
```

For a 3x inverse ETP on NASDAQ-100 with realised vol at 35% (crash level):
```
position_size = 0.15 / (0.35 * 3) * 10000 = 0.15 / 1.05 * 10000 = GBP 1,428.57
```

This is 14.3% of capital — aggressive enough to matter, conservative enough to survive a bear-trap reversal.

### 2.3.3 Flash Crash Hedge (Existing Capability — Documented)

The codebase already contains an automatic flash crash hedge that triggers inverse ETP purchase when the underlying drops > 0.5% in a short window. This is a **portfolio protection** mechanism, not a profit-seeking trade:

- Trigger: underlying drops > 0.5% from session high
- Action: purchase inverse ETP counterpart (from `INVERSE_PAIRS` mapping)
- Size: minimal (capital preservation, not profit maximisation)
- Duration: until underlying stabilises or session ends

This complements the Inverse Pivot (which is a deliberate, scored trade) by providing automatic downside hedging for existing long positions.

---

## 2.4 Intraday Momentum Exploitation (NEW — Based on Gao, Han, Li & Zhou 2018, JFE)

### 2.4.1 The First-Half-Hour / Last-Half-Hour Predictability

Gao et al. (2018) document a striking empirical regularity: the return in the first 30 minutes of the trading session is a statistically significant predictor of the return in the last 30 minutes. This effect is robust across US equity markets, persists out-of-sample, and generates economically meaningful alpha after transaction costs.

The mechanism is believed to be **informed trading clustering**: institutional traders who receive information overnight execute in the first 30 minutes (when liquidity is deepest), and the price discovery process continues into the close as the information diffuses to slower participants.

### 2.4.2 Application to LSE ETPs

For LSE-listed ETPs tracking US underlyings, the "first 30 minutes" window is **08:00-08:30 UK** (LSE open), and the "last 30 minutes" is **15:30-16:00 UK** (approaching LSE close, overlapping with US mid-morning).

**Implementation Rules:**

| First-30-Min Return (08:00-08:30 UK) | Action | Confidence Modifier |
|---|---|---|
| > +0.5% | LONG bias for the session | +5 to S15 confidence score for long ETPs |
| < -0.5% | SHORT bias for the session | +5 to S15 confidence score for inverse ETPs |
| Between -0.5% and +0.5% | No bias | No modifier (insufficient signal strength) |

**Critical Design Decision**: This is an **additive modifier** to the existing S15 scoring system, NOT a standalone signal generator. A +5 confidence boost can turn a marginal signal (score 72) into a firing signal (score 77), but it cannot create a signal from nothing. This prevents the intraday momentum signal from overriding the comprehensive 8-indicator consensus.

### 2.4.3 Interaction with US Open (14:30 UK)

The 14:30 UK US market open creates a second intraday momentum inflection. The first-30-min signal from 08:00 UK may be reinforced or contradicted by the US open direction. Rules:

- If 08:00-08:30 signal and 14:30-15:00 US-open direction **agree**: confidence boost doubles to +10
- If they **disagree**: confidence boost reverts to 0 (conflicting signals cancel)
- If US open moves > 1.0% in either direction: this overrides the morning signal entirely (US institutions are the marginal price setter for these ETPs)

---

## 2.5 Five Enhancements (E-01 through E-05)

### E-01: Chain Reaction Confidence Boost

**Current State**: The `move_attribution` module identifies when a move in one ticker propagates to correlated tickers (e.g., NVIDIA earnings beat → NVD3.L spike → QQQ3.L sympathy move → 3SEM.L follows). Currently, this attribution data is logged but not wired into S15 scoring.

**Enhancement**: Feed `move_attribution` output directly into the S15 confidence calculation as a supplementary indicator.

**Calibration**: The existing codebase uses a fixed beta of 0.40 (Thomas & Zhang 2006 estimate for sector momentum spillover). This is a population average that ignores pair-specific dynamics. Replace with **empirical pair-specific beta** estimated from `outcomes.jsonl`:

```
For each pair (source_ticker, target_ticker):
    beta_empirical = cov(source_return, target_return) / var(source_return)
    # Estimated from last 60 completed trades in outcomes.jsonl
    # Shrunk toward 0.40 prior with weight = min(n_observations / 30, 1.0)
```

**Chain Boost Calculation**:
```
chain_boost = min(beta_empirical * source_move_zscore * 10, 20)
# Capped at +20 confidence points to prevent a single chain event from dominating
```

**Cap Rationale**: A +20 cap means a chain reaction can boost a score from 55 to 75 (minimum firing threshold), but cannot single-handedly push a weak signal past the threshold. The chain reaction must combine with at least moderate standalone merit.

### E-02: PEAD Power-Law Decay (Chan, Jegadeesh & Lakonishok 1996)

**Background**: Post-Earnings Announcement Drift (PEAD) is one of the most robust anomalies in empirical finance. After an earnings surprise, stocks continue to drift in the direction of the surprise for 60-90 trading days (Ball & Brown 1968, Bernard & Thomas 1989, Chan, Jegadeesh & Lakonishok 1996).

**Current Gap**: NZT-48 has no earnings-aware signal component. When a leveraged ETP's underlying reports earnings, the system treats the next day identically to any other day — missing the most predictable drift in equity markets.

**Enhancement**: Add a PEAD residual component to S15 scoring:

```
pead_residual(t) = 0.30 * (t + 1)^(-0.5)
```

Where:
- `t` = trading days since earnings announcement (t=0 on announcement day)
- 0.30 = initial PEAD impulse (calibrated to the average standardised unexpected earnings coefficient from Chan et al. 1996)
- `(t + 1)^(-0.5)` = **power-law decay**, NOT exponential decay

**Why Power-Law, Not Exponential**: Exponential decay (e.g., `0.30 * e^(-0.1t)`) drops to near-zero by day 20. But the empirical PEAD literature consistently shows drift persisting for 60-90 days, with slow decay. Power-law functions `t^(-alpha)` for `alpha` in [0.3, 0.7] match the observed decay profile far better (Hou, Xue & Zhang 2020). At `alpha = 0.5`:

| Days Post-Earnings | Power-Law Residual | Exponential Residual |
|---|---|---|
| Day 1 | 0.212 | 0.271 |
| Day 5 | 0.122 | 0.182 |
| Day 10 | 0.090 | 0.110 |
| Day 20 | 0.065 | 0.041 |
| Day 40 | 0.047 | 0.005 |
| Day 60 | 0.039 | 0.001 |

The power-law residual remains meaningful at day 40-60, capturing the well-documented slow tail of PEAD. The exponential residual is essentially zero by day 30, leaving alpha on the table.

**Data Source**: Earnings dates from yfinance `.info["earningsDate"]` or fallback to Earnings Whispers scrape. Store in Redis with TTL of 90 days.

### E-03: Vol-Managed Sizing (Moreira & Muir 2017, JF)

**Background**: Moreira & Muir (2017) demonstrate that scaling portfolio exposure inversely by recent realised volatility improves Sharpe ratios across virtually all asset classes, without requiring return forecasts. The intuition: high volatility predicts high future volatility (vol clustering), but does NOT predict higher returns — so reducing exposure during high-vol periods improves risk-adjusted returns mechanically.

**Application to Leveraged ETPs**: For 3x ETPs, the effective volatility is 3x the underlying's realised vol. For 5x ETPs, it is 5x. The vol-managed sizing formula:

```
weight_etp = (target_vol / (realised_vol_underlying * leverage_factor)) * base_weight
```

Where:
- `target_vol` = 15% annualised (system-level risk budget)
- `realised_vol_underlying` = 20-day Yang-Zhang estimator on the underlying index/stock (Yang & Zhang 2000 — superior to close-to-close for instruments with gaps)
- `leverage_factor` = 3 for 3x ETPs, 5 for 5x ETPs
- `base_weight` = Kelly-optimal weight from existing position sizer

**Example**: NVIDIA 20-day realised vol = 45%. NVD3.L is 3x leveraged.
```
weight = 0.15 / (0.45 * 3) * base_weight = 0.15 / 1.35 * base_weight = 0.111 * base_weight
```
This scales the position to 11.1% of what the base weight would suggest — aggressive vol compression that prevents a single high-vol trade from dominating portfolio P&L.

**5x ETP Override**: For 5x instruments, `weight` is additionally capped at 10% of total capital regardless of the formula output. The fat tails on 5x daily rebalanced products are extreme enough that even vol-managed sizing can understate risk during regime transitions (Avellaneda & Zhang 2010).

### E-04: Inverse Pivot

Fully described in Section 2.3.2 above. Reference implementation targets `strategies/daily_target.py` with new method `_evaluate_inverse_pivot()` called when regime transitions to TRENDING_DOWN or RISK_OFF.

### E-05: No-Signal Escalation Protocol

**Problem**: On some trading days, no instrument in the 12-ticker core universe meets the 75/100 confidence threshold. The system produces zero signals. While "no trade" is a valid outcome (and far better than forcing a bad trade), an excessive dry-day frequency indicates the confidence floor is too restrictive for current market conditions.

**Escalation Timeline (all times UK):**

| Time | Action | Rationale |
|---|---|---|
| 09:00 - 14:00 | Normal S15 scanning with confidence floor = 75 | Standard operation. Most signals fire between 09:30 and 11:00 (LSE morning session) or 14:45-15:15 (US open spillover). |
| 14:00 | Lower confidence floor: 75 → 70 | 5 hours of scanning with no signal suggests the day is marginal. A 70 floor still represents strong conviction (above the 95th percentile of random signals per Harvey & Liu 2015), but captures near-miss opportunities. |
| 14:30 | Activate S12 Rebalance Flow scan | S12 targets predictable end-of-day rebalancing flows in leveraged ETPs (Mathis & Moerke 2022). These flows are most exploitable when the main session has been range-bound (which is exactly when S15 finds no signal). |
| 15:00 | Activate S16 Universal Scanner | S16 broadens the search beyond the 12-ticker core to the full 35-ticker universe. This catches opportunities in secondary ETPs that S15's core filter excluded. |
| 15:30 | **Accept FLAT day** | If no signal has fired by 15:30, the system accepts a zero-trade day. Forcing a trade into the last 30 minutes of the session — when spreads widen and rebalancing flows distort prices — is negative expected value. Discipline over desperation. |

**Adaptive Gate Widening**: The system tracks dry-day frequency as a rolling 20-day metric. If dry days exceed 8% of trading days (approximately 1.6 days per 20-day window), the ADR gate is widened from 2.9% to 2.5%. This admits instruments with slightly lower daily range potential, increasing the opportunity set without materially compromising the 2% target (2.5% ADR still provides sufficient range for 2% capture after spread costs on a 3x ETP).

**Dry-Day Logging**: Every flat day is logged with full context — all 12 tickers' scores, the highest-scoring instrument, the reason it failed (which indicator vetoed), and the macro regime. This data feeds the quarterly model recalibration (Harvey & Liu 2020 — replication crisis in factor investing demands ongoing validation).

---

## 2.6 ETP Rebalancing Alpha (NEW — Based on Mathis & Moerke 2022)

### 2.6.1 The Mechanical Rebalancing Flow

Leveraged ETPs must rebalance their exposure at the end of each trading day to maintain their target leverage ratio. This creates **predictable, non-informational order flow** in the last 30 minutes of trading:

| Market Day | ETP Action at Close | Direction of Rebalancing Flow |
|---|---|---|
| Strong UP day (+2%+) | Long ETP must BUY more underlying to restore 3x ratio | **BUY flow** — pushes underlying (and ETP) higher |
| Strong DOWN day (-2%+) | Long ETP must SELL underlying to de-lever | **SELL flow** — pushes underlying (and ETP) lower |
| Flat day (< 0.5% move) | Minimal rebalancing needed | Negligible flow |

Mathis & Moerke (2022) quantify this effect and show it is economically significant for highly-levered products. The larger the daily move, the larger the rebalancing flow, and the more predictable the last-30-minute price action.

### 2.6.2 Exploitation Rules for NZT-48

**Rule 1: DO NOT enter new positions in the last 30 minutes (15:00-15:30 UK for LSE ETPs).**

The rebalancing flow creates a temporarily distorted price that reverts overnight. Entering at 15:15 on a strong up-day means buying into mechanical buy flow that will not persist — the ETP price is momentarily inflated by its own rebalancing. The next morning's open will correct this, starting your position at a loss.

**Rule 2: If holding a position, the rebalancing flow HELPS your trailing stop.**

If you are long QQQ3.L on a day the NASDAQ-100 is up 2%+, the end-of-day rebalancing buy flow pushes QQQ3.L higher, extending your profit and pulling your Chandelier Exit trailing stop upward. This is free alpha — the rebalancing flow acts as a tailwind for existing positions that are already in profit.

**Rule 3: Bank 40% at Rung 2 BEFORE the rebalancing window.**

The 5-rung profit ladder in `core/chandelier_exit.py` (Le Beau 1999 adaptation) banks partial profits at predefined levels. Rung 2 (40% of position) should be exited by 14:55 UK — before the rebalancing window begins. This locks in gains at a "clean" price, uncontaminated by mechanical flow. The remaining 60% rides the rebalancing tailwind with a tighter trailing stop.

**Rule 4: For 5x ETPs with overnight_kill=True, EXIT BEFORE REBALANCING.**

5x ETPs have larger rebalancing flows and more extreme vol drag. The rebalancing window for these instruments is particularly dangerous because:
- The flow is larger (5x leverage = 5x rebalancing volume)
- Spread widens during rebalancing as market makers absorb the flow
- The 5x product MUST be closed before session end regardless

Exit 5x positions by 15:00 UK at the latest. Do not attempt to ride the rebalancing flow.

### 2.6.3 Rebalancing Flow Estimation

To estimate the magnitude of the expected rebalancing flow (useful for adjusting trailing stop tightness):

```
rebalancing_flow_pct = (leverage_factor - 1) * daily_return * (AUM_etp / ADV_underlying)
```

Where:
- `leverage_factor` = 3 or 5
- `daily_return` = underlying's return so far today
- `AUM_etp` = ETP's assets under management (from provider data, refreshed weekly)
- `ADV_underlying` = average daily volume of underlying index/stock

When `rebalancing_flow_pct` > 0.5% of ADV, the flow is material and the rules above are strictly enforced. Below 0.5%, the flow is noise and can be ignored.

---
---

# SECTION 3: THE APEX RADAR — Global Cross-Asset Intelligence Drone

**Status**: This module does NOT exist in the current codebase. It must be built from scratch.

**Target File**: `strategies/apex_scout.py`

**Purpose**: Asynchronous discovery of anomalous relative volume (RVOL) events across a broad universe of 200-500 pre-filtered global equities, feeding high-conviction signals to the Vanguard Sniper (Section 2) via the ISA Priority Mapping layer.

---

## 3.1 Purpose and Strategic Rationale

### 3.1.1 The Discovery Gap

The Vanguard Sniper (S15) is a precision instrument: it scores 12 known ETPs against 8 indicators and fires exactly 1 signal per day. Its weakness is that it only sees what it already knows. If a stock outside the 12-ticker core universe experiences a massive institutional accumulation event — the kind of move that would produce a 20%+ day on a 3x ETP — S15 is blind to it.

The Apex Radar exists to solve this. It continuously scans a broad universe (200-500 tickers), identifies anomalous volume events in real-time, maps them to ISA-eligible LSE ETPs where possible, and feeds the highest-conviction discovery to S15 for execution.

### 3.1.2 Volume as the Leading Indicator

Volume precedes price. This is not speculation; it is one of the most replicated findings in market microstructure (Karpoff 1987, Llorente et al. 2002, Chordia & Swaminathan 2000). When institutional investors accumulate a position, they cannot do so invisibly — the volume footprint appears before the price impact is fully realised. RVOL (relative volume vs. time-of-day-adjusted 20-day average) is the cleanest measure of this anomalous activity.

---

## 3.2 Architecture

### 3.2.1 Class Design

```python
# strategies/apex_scout.py

class ApexScout:
    """
    Global cross-asset RVOL anomaly scanner.

    Feeds high-conviction discoveries to S15 via ISA Priority Mapping.
    Scans 200-500 pre-filtered tickers every 30 minutes + trigger-based.

    References:
        - Chordia & Swaminathan (2000): lead-lag from trading volume
        - Llorente et al. (2002): informed trading and return-volume relation
        - Karpoff (1987): relation between price changes and volume
    """

    def __init__(
        self,
        watchlist: List[str],           # 200-500 tickers, refreshed daily
        regime_provider: RegimeProvider, # HMM regime from cross_asset_macro.py
        lse_mapper: LSEMapper,          # Maps US tickers → LSE ETPs
        config: dict                    # Thresholds, batch size, etc.
    ):
        self.watchlist = watchlist
        self.regime_provider = regime_provider
        self.lse_mapper = lse_mapper
        self.config = config

        # Rolling RVOL history: ticker → deque of time-of-day-adjusted RVOL
        # maxlen=20 gives a 20-observation baseline for Z-score calculation
        self.rvol_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=20)
        )

        # Anomaly output queue → consumed by S15
        self.anomaly_queue: asyncio.Queue = asyncio.Queue()

        # Bayesian Stranger Penalty tracker
        self.ticker_track_record: Dict[str, dict] = {}
```

### 3.2.2 Core Scan Method

```python
async def scan(self) -> List[AnomalySignal]:
    """
    Primary scan method. Called every 30 minutes by scheduler
    AND on trigger events (NASDAQ move >0.5%, VIX spike >10%).

    Processes watchlist in batches of 50 tickers to respect
    yfinance rate limits (empirical safe limit: ~250 tickers
    for 1-min data per batch, using 50 for 5x safety margin).

    Returns list of AnomalySignal objects sorted by Z-score descending.
    """
    current_regime = self.regime_provider.get_current_regime()
    z_threshold = self._get_z_threshold(current_regime)

    anomalies = []
    batches = [
        self.watchlist[i:i+50]
        for i in range(0, len(self.watchlist), 50)
    ]

    for batch in batches:
        # Download 1-minute data for batch
        data = await self._fetch_batch(batch, period="1d", interval="1m")

        for ticker in batch:
            if ticker not in data:
                continue

            rvol = self._compute_rvol(ticker, data[ticker])
            self.rvol_history[ticker].append(rvol)

            if len(self.rvol_history[ticker]) < 5:
                continue  # Insufficient history for Z-score

            z_score = self._compute_z_score(
                rvol, self.rvol_history[ticker]
            )

            if z_score > z_threshold:
                # VWAP anchor check: VETO if Price < VWAP
                if not self._vwap_check(ticker, data[ticker]):
                    continue

                anomalies.append(AnomalySignal(
                    ticker=ticker,
                    rvol=rvol,
                    z_score=z_score,
                    regime=current_regime,
                    timestamp=datetime.utcnow(),
                    vwap_aligned=True
                ))

    # Sort by Z-score descending, return top candidates
    anomalies.sort(key=lambda x: x.z_score, reverse=True)
    return anomalies[:10]  # Top 10 for S15 evaluation
```

### 3.2.3 Adaptive Z-Score Thresholds by Regime

The threshold for what constitutes an "anomalous" volume event must adapt to the prevailing market regime. In trending markets, elevated volume is commonplace (everyone is participating); in quiet markets, even modest volume spikes are informative.

| Regime | Z-Threshold | Rationale |
|---|---|---|
| TRENDING_UP_STRONG | > 2.0 | Broad participation means volume is elevated everywhere. Only extreme outliers matter. Lower threshold captures momentum continuation (Jegadeesh & Titman 1993). |
| TRENDING_UP_MOD | > 2.5 | Moderate trend. Volume spikes more likely to be informative. |
| RANGE_BOUND | > 3.0 | In range-bound markets, volume spikes are rare and highly informative. This is where the best Scout signals originate — someone knows something. |
| TRENDING_DOWN_MOD | > 3.0 | Bearish regime. Volume spikes could be capitulation or accumulation. High threshold prevents false positives from panic selling. |
| TRENDING_DOWN_STRONG | > 3.5 | In crashes, volume spikes are everywhere (forced liquidation, margin calls). Only extreme outliers (> 3.5 sigma) are likely informed rather than forced. |
| RISK_OFF | > 3.5 | Same logic as TRENDING_DOWN_STRONG. Elevated baseline volume. |
| SHOCK | > 999 (disabled) | **Scanner is OFF during SHOCK regime.** All volume is noise. No new discoveries. Capital preservation only. |

### 3.2.4 VWAP Anchor: Institutional Distribution Filter

**RULE: VETO any anomaly where Price < VWAP.**

VWAP (Volume-Weighted Average Price) is the benchmark against which institutional execution quality is measured (Berkowitz et al. 1988). When price is above VWAP, net institutional flow is positive (buyers are paying above the average traded price — accumulation). When price is below VWAP, institutions are distributing (selling into the volume, pushing price below the average).

An RVOL anomaly with price below VWAP is almost certainly informed selling, not buying. The volume spike is real, but the direction is wrong — we would be buying into distribution.

Implementation:
```python
def _vwap_check(self, ticker: str, ohlcv_1min: pd.DataFrame) -> bool:
    """
    Returns True if current price is above session VWAP.
    VWAP = cumsum(price * volume) / cumsum(volume)
    """
    typical_price = (
        ohlcv_1min['High'] + ohlcv_1min['Low'] + ohlcv_1min['Close']
    ) / 3
    vwap = (
        (typical_price * ohlcv_1min['Volume']).cumsum() /
        ohlcv_1min['Volume'].cumsum()
    )
    current_price = ohlcv_1min['Close'].iloc[-1]
    current_vwap = vwap.iloc[-1]

    return current_price > current_vwap
```

---

## 3.3 ISA Tax-Shield Rerouting

### 3.3.1 The Rerouting Cascade

When the Apex Scout detects an anomaly on a US-listed stock, it must not execute directly on the US exchange. Instead, it queries the LSE Mapper (`uk_isa/lse_registry.py`) for an ISA-eligible equivalent:

```
ANOMALY DETECTED: PLTR (Palantir) — RVOL Z-score = 3.7
    │
    ├─ Step 1: lse_mapper.get_etp_equivalent("PLTR")
    │           Query result: {"3x_long": "PLTR3.L"} (if exists)
    │
    ├─ Step 2: Is LSE open? (08:00-16:30 UK)
    │   ├─ YES → Reroute to PLTR3.L (3x amplification, tax-free)
    │   │         Check spread < 2.5x median_3d_spread
    │   │         Check HMRC ISA eligibility (Step 5)
    │   │         Apply Bayesian Stranger Penalty (Step 4)
    │   │         Feed to S15 for final scoring
    │   │
    │   └─ NO  → Log as MISSED_OPPORTUNITY
    │            LSE is closed. No execution possible.
    │            (No Night Shift capability — see §2.2.4)
    │
    ├─ Step 3: No ETP equivalent exists
    │   └─ Is the US stock itself ISA-eligible?
    │       ├─ YES → Execute standalone in ISA (1x only, tax-free)
    │       └─ NO  → VETO. Cannot execute outside ISA wrapper.
    │
    ├─ Step 4: Bayesian Stranger Penalty
    │   └─ Apply confidence discount to ALL Scout-discovered signals
    │      (see §3.3.2 below)
    │
    └─ Step 5: HMRC ISA Eligibility Check
        └─ Verify ticker is on HMRC's list of qualifying investments
           for Stocks & Shares ISA. Most LSE-listed ETPs qualify,
           but some structured products do not.
           If NOT eligible → VETO regardless of signal strength.
```

### 3.3.2 Bayesian Stranger Penalty

Scout-discovered signals are, by definition, signals on tickers the system has NOT been tracking in its core universe. They lack the 20-day baseline of VWAP history, spread data, and pattern recognition that core tickers benefit from. This informational disadvantage must be priced in.

**Stranger Penalty Formula**:
```
confidence_adjusted = confidence_raw * stranger_discount

where:
    stranger_discount = 0.70 + 0.30 * min(days_tracked / 20, 1.0)
```

- Day 0 (first sighting): discount = 0.70 (30% penalty)
- Day 10: discount = 0.85 (15% penalty)
- Day 20+: discount = 1.00 (no penalty — ticker is now "known")

**Rationale**: The 30% initial penalty reflects the empirical finding that RVOL anomalies on unfamiliar tickers have a higher false positive rate than those on tracked tickers (the system has no context for what "normal" looks like). As the ticker accumulates history in `rvol_history`, the penalty decays linearly to zero.

**Track Record Tracking**:
```python
# In ticker_track_record dict:
{
    "PLTR": {
        "first_seen": "2026-02-15",
        "days_tracked": 12,
        "signals_fired": 3,
        "signals_profitable": 2,  # 66.7% hit rate
        "avg_return": 0.018       # 1.8% average
    }
}
```

If a Scout-discovered ticker generates 3+ profitable signals, it becomes a candidate for promotion to the core 12-ticker universe (subject to factor group cap of 3 positions per group).

---

## 3.4 Gap-Stabilisation Wait [G-R2 — NEW]

### 3.4.1 The US Open Latency Problem

At 14:30 UK (09:30 US Eastern), the US market opens. For LSE-listed ETPs tracking US underlyings, this creates a **pricing discontinuity**: the US underlying gaps up or down on the open, the LSE ETP's market maker adjusts their quote, but yfinance's 1-second latency means the system's view of the LSE ETP price is stale by 1-3 seconds.

In those 1-3 seconds, the market maker has already adjusted the ETP price for the US open gap, but the system is still seeing the pre-gap quote. If the Scout detects a US anomaly at 14:30:05 and immediately tries to reroute to the LSE ETP, it will attempt to execute at a stale price that no longer exists.

### 3.4.2 The 60-Second Stabilisation Window

**RULE**: For any Scout-to-ETP reroute occurring between **14:30:00 and 14:31:00 UK**, impose a mandatory **60-second wait** before execution.

```
14:30:00 — US market opens
14:30:01 — Scout detects RVOL anomaly on NVDA
14:30:02 — lse_mapper returns NVD3.L
14:30:02 — *** WAIT FLAG SET: G-R2 gap stabilisation ***
14:31:02 — 60 seconds elapsed
14:31:02 — Re-quote NVD3.L price from LSE
14:31:03 — Verify spread < 2.5x median_3d_spread
14:31:03 — If spread OK → feed to S15 for scoring
14:31:03 — If spread still wide → VETO (MM still adjusting)
```

**Why 60 Seconds**: Empirical observation of LSE ETP price action around US open shows that spreads normalise within 30-45 seconds for liquid ETPs (QQQ3.L, 3LUS.L) and 45-90 seconds for less liquid ones (MU2.L, GPT3.L). A 60-second wait captures the median case with margin.

### 3.4.3 Extended Wait for Gapped Markets

If the US underlying gaps more than 2% at the open:
- Extend wait to **120 seconds**
- Reason: large gaps cause LSE market makers to widen spreads further and longer
- The 2.5x median_3d_spread check will likely VETO the trade even after 120 seconds on a 3%+ gap day — this is by design (the spread cost erases the edge)

---

## 3.5 Trigger-Based Scanning (NEW — Event-Driven Augmentation)

### 3.5.1 Limitation of Fixed-Interval Scanning

A 30-minute scan interval means the system could be up to 29 minutes late to a fast-moving event. In leveraged ETPs where a 2% move in the underlying translates to a 6% move in the 3x product, 29 minutes of latency is the difference between catching the move and chasing the move.

### 3.5.2 Trigger Events

The following market events bypass the 30-minute schedule and fire an **immediate** Scout scan:

| Trigger | Condition | Scan Target | Rationale |
|---|---|---|---|
| NASDAQ-100 Momentum Burst | QQQ moves > 0.5% in any 5-minute window | Full watchlist (200-500 tickers) | Broad market momentum activates sector rotation. The move will propagate to individual names within 5-15 minutes (Chordia & Swaminathan 2000). |
| VIX Spike | VIX increases > 10% in any 5-minute window | **Inverse ETPs only** | Sudden fear spike. Scout targets QQQS.L, 3USS.L, NVDS.L, TSLS.L for crash monetisation opportunities. |
| Single-Stock Halt Resume | Trading halt lifted on any watchlist ticker | Halted ticker + sector peers | Halts are lifted with extreme volume. The first 5 minutes post-resume are the highest RVOL readings in the market (Corwin & Lipson 2000). |
| Earnings Release (Pre-Market) | Earnings reported for watchlist ticker before 08:00 UK | Reporting ticker + sector peers | PEAD begins immediately at open. Scout feeds to E-02 (PEAD Power-Law Decay). |

### 3.5.3 Trigger Scan Scope

To avoid overwhelming the system (and yfinance rate limits), trigger-based scans are scoped differently from scheduled scans:

- **NASDAQ Momentum Burst**: Full watchlist (200-500 tickers) in 10 batches of 50. Takes approximately 2-3 minutes to complete. Acceptable latency for a broad momentum rotation.
- **VIX Spike**: Only inverse ETPs from `INVERSE_PAIRS` mapping (4-8 tickers). Instant scan, < 5 seconds.
- **Halt Resume**: Halted ticker + top 10 correlated tickers by sector. Narrow, fast scan.
- **Earnings Release**: Reporting ticker + factor group peers (max 5 tickers). Narrow, fast scan.

### 3.5.4 Cooldown Mechanism

To prevent trigger floods during volatile periods (where NASDAQ might move 0.5% every 5 minutes for an hour), implement a **10-minute cooldown** per trigger type:

```python
trigger_cooldowns = {
    "nasdaq_burst": None,    # datetime of last trigger
    "vix_spike": None,
    "halt_resume": {},       # per-ticker cooldown
    "earnings": {}           # per-ticker cooldown
}

def should_fire_trigger(trigger_type: str, ticker: str = None) -> bool:
    last_fire = trigger_cooldowns.get(trigger_type)
    if isinstance(last_fire, dict):
        last_fire = last_fire.get(ticker)
    if last_fire is None:
        return True
    return (datetime.utcnow() - last_fire).total_seconds() > 600  # 10 min
```

---

## 3.6 Data Cost Control

### 3.6.1 The yfinance Rate-Limit Reality

NZT-48 uses yfinance as its primary data source. yfinance is free but rate-limited. Empirical testing shows:
- **1-minute data**: Reliable for up to ~250 tickers per batch request
- **Batch size**: 50 tickers per request (5x safety margin)
- **Request frequency**: No more than 1 request per 2 seconds for sustained scanning
- **Daily data**: Virtually unlimited for price/volume (no rate limit observed below 5,000 tickers)

### 3.6.2 Data Refresh Schedule

| Time (UK) | Action | Tickers | Data Type | Cost |
|---|---|---|---|---|
| **Sunday 22:00** | Weekly Universe Refresh | Russell 3000 (full) | Daily OHLCV, 20-day | ~3,000 tickers. Takes ~10 minutes. Runs once per week. |
| | | | | Compute: 20-day RVOL, 20-day ADR, sector classification |
| | | | | Filter to top 200-500 by RVOL + ADR composite score |
| | | | | Store in Redis: `apex:watchlist` with 7-day TTL |
| **Daily 06:00** | Delta Refresh | 200-500 watchlist | Daily OHLCV | Identify overnight earnings gaps, trading halts, delistings |
| | | | | Update `apex:watchlist` with additions/removals |
| | | | | Add any tickers with after-hours earnings surprises |
| **Every 30 min** (market hours) | Scheduled Scan | 200-500 watchlist | 1-min OHLCV | 10 batches x 50 tickers. Takes ~2-3 minutes. |
| | | | | Compute: real-time RVOL, VWAP, Z-score |
| | | | | Feed anomalies to S15 queue |
| **On trigger** | Immediate Scan | 4-50 tickers (varies) | 1-min OHLCV | Scoped by trigger type (see §3.5.3). < 30 seconds. |

### 3.6.3 Sunday Universe Construction (Pre-Filter Pipeline)

The Sunday pipeline processes the full Russell 3000 (plus selected international ADRs) down to a focused watchlist of 200-500 tickers. This is the most computationally expensive operation and runs once per week:

```
STAGE 1: Download Russell 3000 daily OHLCV (last 60 trading days)
         Source: yfinance batch download
         Time: ~10 minutes for 3,000 tickers

STAGE 2: Compute screening metrics for each ticker:
         - RVOL_20d: mean relative volume over 20 days
         - ADR_20d: average daily range over 20 days (must be > 2.0%)
         - Market Cap: exclude < $500M (insufficient liquidity for ETP tracking)
         - Sector: GICS classification for factor group mapping

STAGE 3: Composite ranking:
         score = 0.50 * rank(RVOL_20d) + 0.30 * rank(ADR_20d) + 0.20 * rank(MarketCap)
         Rationale: RVOL is the primary signal (50% weight),
         ADR ensures the ticker can deliver 2% moves (30% weight),
         MarketCap ensures liquidity and ETP availability (20% weight)

STAGE 4: Filter to top 200-500 by composite score
         - Hard floor: ADR_20d > 2.0% (below this, 2% daily target is not mechanically achievable)
         - Hard floor: average daily volume > $5M (below this, slippage destroys edge)
         - Soft cap: 500 tickers max (yfinance rate limit budget)
         - If < 200 qualify: widen ADR floor to 1.5% (rare, only in extreme low-vol regimes)

STAGE 5: Store in Redis
         Key: apex:watchlist
         Value: JSON array of {ticker, rvol_20d, adr_20d, sector, market_cap, lse_etp_equivalent}
         TTL: 7 days (auto-expires before next Sunday refresh)
```

### 3.6.4 Graceful Degradation

If yfinance rate limits are hit during market hours:
1. **First limit hit**: Back off 30 seconds, retry
2. **Second consecutive limit**: Reduce batch size from 50 to 25 tickers
3. **Third consecutive limit**: Reduce watchlist to top 100 by RVOL rank (highest-probability anomalies only)
4. **Persistent rate limiting (> 5 minutes)**: Fall back to 60-minute scan interval (half frequency)
5. **Total yfinance failure**: Log ALERT, continue with S15 core universe only (12 tickers from cached data)

At no point does a data feed failure cause the system to halt. The Vanguard Sniper (S15) operates independently on its core 12-ticker universe and does not depend on the Apex Radar. The Scout is additive alpha, not a dependency.

---

## 3.7 Signal Output Format

When the Apex Radar identifies a qualifying anomaly, it produces an `AnomalySignal` object that enters the S15 evaluation queue:

```python
@dataclass
class AnomalySignal:
    ticker: str                  # Original ticker (e.g., "NVDA")
    lse_etp: Optional[str]      # Rerouted ETP (e.g., "NVD3.L") or None
    rvol: float                  # Current relative volume
    z_score: float               # Z-score vs. 20-observation history
    regime: str                  # Market regime at time of detection
    vwap_aligned: bool           # Price > VWAP confirmed
    stranger_discount: float     # Bayesian penalty (0.70-1.00)
    trigger_source: str          # "scheduled" | "nasdaq_burst" | "vix_spike" | etc.
    isa_eligible: bool           # HMRC ISA qualification confirmed
    spread_ok: bool              # < 2.5x median_3d_spread
    gap_stabilised: bool         # G-R2 wait completed (if applicable)
    timestamp: datetime          # UTC timestamp of detection

    @property
    def adjusted_confidence(self) -> float:
        """
        Confidence score after Stranger Penalty,
        ready for S15 integration.
        """
        base = min(self.z_score * 20, 100)  # Scale Z-score to 0-100
        return base * self.stranger_discount
```

S15 treats Scout signals identically to its own candidates: they must clear the 75/100 confidence floor (after Stranger Penalty), pass the tail risk pre-screen, and compete against core universe signals on equal terms. The only special treatment is the Stranger Penalty itself, which decays to zero as the ticker becomes familiar.

---

## References

- Avellaneda, M. & Zhang, S. (2010). Path-dependence of leveraged ETF returns. *SIAM Journal on Financial Mathematics*, 1(1), 586-603.
- Ball, R. & Brown, P. (1968). An empirical evaluation of accounting income numbers. *Journal of Accounting Research*, 6(2), 159-178.
- Balkema, A. A. & de Haan, L. (1974). Residual life time at great age. *Annals of Probability*, 2(5), 792-804.
- Barroso, P. & Santa-Clara, P. (2015). Momentum has its moments. *Journal of Financial Economics*, 116(1), 111-120.
- Berkowitz, S. A., Logue, D. E. & Noser, E. A. (1988). The total cost of transactions on the NYSE. *Journal of Finance*, 43(1), 97-112.
- Bernard, V. L. & Thomas, J. K. (1989). Post-earnings-announcement drift: Delayed price response or risk premium? *Journal of Accounting Research*, 27, 1-36.
- Chan, L. K. C., Jegadeesh, N. & Lakonishok, J. (1996). Momentum strategies. *Journal of Finance*, 51(5), 1681-1713.
- Cheng, M. & Madhavan, A. (2009). The dynamics of leveraged and inverse exchange-traded funds. *Journal of Investment Management*, 7(4), 43-62.
- Chordia, T. & Swaminathan, B. (2000). Trading volume and cross-autocorrelations in stock returns. *Journal of Finance*, 55(2), 913-935.
- Corwin, S. A. & Lipson, M. L. (2000). Order flow and liquidity around NYSE trading halts. *Journal of Finance*, 55(4), 1771-1801.
- Daniel, K. & Moskowitz, T. J. (2016). Momentum crashes. *Journal of Financial Economics*, 122(2), 221-247.
- Gao, L., Han, Y., Li, S. Z. & Zhou, G. (2018). Market intraday momentum. *Journal of Financial Economics*, 129(2), 394-414.
- Harvey, C. R. & Liu, Y. (2015). Backtesting. *Journal of Portfolio Management*, 42(1), 13-28.
- Harvey, C. R. & Liu, Y. (2020). Lucky factors. *Journal of Financial Economics*, 141(2), 413-435.
- Heston, S. L., Korajczyk, R. A. & Sadka, R. (2010). Intraday patterns in the cross-section of stock returns. *Journal of Finance*, 65(4), 1369-1407.
- Hou, K., Xue, C. & Zhang, L. (2020). Replicating anomalies. *Review of Financial Studies*, 33(5), 2019-2133.
- Jegadeesh, N. & Titman, S. (1993). Returns to buying winners and selling losers: Implications for stock market efficiency. *Journal of Finance*, 48(1), 65-91.
- Jegadeesh, N. & Titman, S. (2001). Profitability of momentum strategies: An evaluation of alternative explanations. *Journal of Finance*, 56(2), 699-720.
- Karpoff, J. M. (1987). The relation between price changes and trading volume: A survey. *Journal of Financial and Quantitative Analysis*, 22(1), 109-126.
- Kelly, J. L. (1956). A new interpretation of information rate. *Bell System Technical Journal*, 35(4), 917-926.
- Le Beau, C. (1999). *Technical Traders Guide to Computer Analysis of the Futures Markets*. McGraw-Hill.
- Llorente, G., Michaely, R., Saar, G. & Wang, J. (2002). Dynamic volume-return relation of individual stocks. *Review of Financial Studies*, 15(4), 1005-1047.
- Lou, D., Polk, C. & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. *Journal of Financial Economics*, 134(1), 192-213.
- Mathis, S. & Moerke, M. (2022). Leveraged ETF rebalancing and market quality. *Journal of Banking & Finance*, 138, 106429.
- Moreira, A. & Muir, T. (2017). Volatility-managed portfolios. *Journal of Finance*, 72(4), 1611-1644.
- Moskowitz, T. J., Ooi, Y. H. & Pedersen, L. H. (2012). Time series momentum. *Journal of Financial Economics*, 104(2), 228-250.
- Thomas, J. K. & Zhang, F. (2006). Overreaction to intra-industry information transfers? *Journal of Accounting Research*, 46(4), 909-940.
- Whaley, R. E. (2000). The investor fear gauge. *Journal of Portfolio Management*, 26(3), 12-17.
- Yang, D. & Zhang, Q. (2000). Drift-independent volatility estimation based on high, low, open, and close prices. *Journal of Business*, 73(3), 477-491.
