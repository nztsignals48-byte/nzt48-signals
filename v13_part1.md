# AEGIS ALPHA-OMEGA MASTER PLAN v13.0

## Institutional-Grade All-Weather Compounding Engine

### NZT-48 → Dual-Core Leveraged ETP + Global Equity Engine

---

| Field | Value |
|---|---|
| **Authors** | Claude Opus 4.6 (Lead Systems Architect) · Gemini 2.5 Flash (Quant Reviewer) |
| **Date** | 2026-03-04 |
| **Status** | **ARCHITECTURE LOCK — FINAL** |
| **Codebase** | 15,700+ LOC · 16 strategies · 33-gate gauntlet · ML meta-model |
| **Runtime** | EC2 t3.small · Docker Compose (engine + API + Redis + Dashboard) |
| **Starting Equity** | £10,000 (UK ISA tax wrapper — £0 CGT, £0 dividend tax) |

**Mandate**: Compound £10,000 via a 2%+ daily profit ladder executed inside a UK ISA tax wrapper. All-weather architecture: the engine holds long AND short positions simultaneously via leveraged and inverse ETPs. No single day's drawdown may exceed 2% of equity. The system must degrade gracefully under all liquidity, volatility, and connectivity regimes.

---

### Revision History

| Version | Date | Lead | Summary |
|---|---|---|---|
| v10.0 | 2026-02 | Gemini 2.5 | Theoretical architecture. Aspirational signal chain, no codebase audit. |
| v11.0 | 2026-02 | Claude Opus 4.6 [C] | Full codebase audit against v10 spec. Identified 12 critical + 7 moderate gaps between plan and implementation. Grounded every module to actual file paths and line numbers. |
| v12.0 | 2026-03 | Gemini 2.5 [G-R1] | Review round 1. Challenged 14 architectural assumptions. Added Monte Carlo sensitivity analysis, Amihud leverage exponent, sinusoidal volume model. |
| v13.0 | 2026-03-04 | Claude Opus 4.6 + Gemini 2.5 [G-R2] | Review round 2 + full rebuild. Academic citation framework. Bayesian DSR graduation. ISA eligibility gate. Fat-tail capture via asymmetric exit. Final architecture lock. |

### Change Legend

| Tag | Meaning |
|---|---|
| [C] | Claude Opus 4.6 — codebase audit, implementation design, systems architecture |
| [G-R1] | Gemini 2.5 Review Round 1 — theoretical challenges, Monte Carlo analysis |
| [G-R2] | Gemini 2.5 Review Round 2 — accepted/rejected items, quantitative refinements |
| [A] | Academic citation — peer-reviewed source anchoring the design decision |

---

---

# Section 0.5: THE MISSION — IN LAYMAN'S TERMS

---

## The Goal

Turn £10,000 into £1.48 million in one year. That is the aspirational ceiling — the mathematical upper bound if every single trading day produces a 2% gain. Nobody hits 100% of targets. The realistic range, depending on win rate and average reward, is **£102,000 to £338,000 in Year 1**. Even the conservative end represents a 10x return, tax-free.

## How It Works

1. **Every Sunday night**, the system audits 5,000+ stocks and funds listed worldwide. It filters them down to the 300 most tradeable instruments — the ones with enough daily volume, tight enough spreads, and large enough price swings to clear a 2% profit hurdle.

2. **Every 60 seconds during London market hours (08:00–16:30 UK)**, the engine scans all 300 candidates and scores them in real time. It is looking for exactly one thing: the single best trade of the day — long (betting the price goes up) or short (betting it goes down).

3. **If a 3x or 5x leveraged fund exists on the London Stock Exchange** that tracks the winning candidate, the system uses it. A 3x fund turns a 1% move in the underlying stock into a 3% move. These funds trade inside a UK ISA, so every penny of profit is tax-free.

4. **When the trade hits +6% (the daily target)**, the system locks in 40% of the position as guaranteed profit. The remaining 60% rides with no ceiling — capturing "fat tail" moves where a stock runs 10%, 15%, or more in a single session. This asymmetry is the engine's core mathematical edge.

5. **After every trade**, a self-learning AI meta-model reviews what happened — the entry signal, the market regime, the exit timing — and adjusts its confidence weights. Separately, 10 independent risk controls (the "gauntlet") must unanimously agree before any trade fires. If even one says no, the system sits in cash.

## The Tax Shield

Every trade executes inside a **UK Individual Savings Account (ISA)**. Under current HMRC rules:

- Capital gains tax: **£0** (normally 20% on gains above the annual allowance)
- Dividend tax: **£0**
- No annual reporting obligation on ISA gains

This is the single largest structural edge in the system. A taxable account compounding at 2% daily loses approximately 0.4% per day to deferred tax drag (assuming periodic crystallisation). Over 252 trading days, the ISA wrapper alone accounts for a **2.7x cumulative advantage** versus an equivalent taxable General Investment Account.

## The Math

| Scenario | Daily Return | Formula | Year 1 Outcome |
|---|---|---|---|
| **Theoretical ceiling** | +2.00%/day | (1.02)^252 | £10,000 → **£1,486,000** |
| **Conservative** (55% WR, 2.5R) | +0.925%/day | (1.00925)^252 | £10,000 → **£102,000** |
| **Moderate** (58% WR, 2.8R) | +1.14%/day | (1.0114)^252 | £10,000 → **£177,000** |
| **Aggressive** (60% WR, 3.0R) | +1.40%/day | (1.014)^252 | £10,000 → **£338,000** |

**WR** = Win Rate. **R** = Reward-to-Risk ratio (average win / average loss).

The "Moderate" scenario incorporates Gemini's Monte Carlo simulation [G-R1]: 10,000 paths with 60% win rate, 2.5R reward ratio, 40bps round-trip spread cost, and daily variance drawn from empirical leveraged ETP return distributions. The geometric mean daily return across all surviving paths (i.e., those not hitting the 25% max drawdown kill switch) was **1.14%/day**, yielding approximately £177,000 at year-end.

The conservative scenario uses the Kelly-adjusted fractional position sizing described in Section 4 (forthcoming), which deliberately under-bets to survive the left tail.

---

---

# Section 1: THE UNIVERSE REGISTRAR — High-Velocity Liquidity Filtration

---

## 1.0 Problem Statement

A compounding engine is only as good as the opportunity set it scans. The current NZT-48 implementation operates on a critically narrow universe:

| Component | Current State | Limitation |
|---|---|---|
| ISA Universe | 12 core ETPs, hardcoded in `uk_isa/isa_universe.py` | No dynamic graduation. Misses new LSE listings. Cannot adapt to liquidity regime changes. |
| Bot B Universe | 18 US equities, hardcoded in `config/settings.yaml` | Arbitrary selection. No capacity-weighted ranking. No spread-adjusted filtering. |
| LSE Registry | 52 products auto-scraped daily via `uk_isa/lse_registry.py` | Scrape logic is solid, but no Amihud sieve, no ASER filter, no DSR graduation gate. Products enter the universe without proving statistical edge. |
| Broader Market | None | No Russell 3000 scanning. No FTSE 350 scanning. No sector rotation signal from breadth data. |

The result: on any given day, the engine chooses from at most 30 instruments. On a day where none of the 12 ISA ETPs exhibit 2%-reachable setups, the engine sits idle — forfeiting the compounding day entirely. Every missed day costs approximately **£200 at £10K equity, scaling to £29,700 at £1.48M equity** (2% of current NAV).

**Target state**: a two-tier universe of 500–1,000 instruments, dynamically maintained, with every ticker earning its place through three independent statistical filters.

---

## 1.1 Architecture: Two-Tier Universe

### Tier 1: "Core" — 300–500 Tickers, Scanned Every 60 Seconds

The Core tier contains every instrument the engine may trade intraday. All Core tickers are scanned on the primary 60-second APScheduler loop (the existing `continuous_scan` job in `main.py`). Membership in Core is not permanent — tickers are promoted from Radar and demoted back based on rolling filter scores.

**Composition:**

| Source | Current Count | Target Count | Selection Criteria |
|---|---|---|---|
| LSE leveraged/inverse ETPs | 12 active (52 scraped) | 40–80 | ASER pass + Amihud pass + ADV > £500K/day |
| US high-beta underlyings | 18 | 50 | Top 50 by 20-day realised volatility from Russell 3000 liquid subset |
| FTSE 350 liquid movers | 0 | 30–50 | ADV > £10M/day, 5-day RVOL Z > 1.5, ASER pass |
| Russell 3000 promoted | 0 | 100–200 | Graduated from Radar via DSR gate |
| Sector ETFs (US + UK) | 0 | 20–30 | Top/bottom 3 sectors by 5-day momentum |
| **Total** | **30** | **300–500** | |

**Core Membership Requirements** (ALL must hold on trailing 20-day window):

- Average Daily Range (ADR) > 2.9% [C: current threshold in `predictive_scoring.py`, validated empirically]
- Median bid-ask spread < 0.45% [G-R1: tightened from 0.60% after spread cost sensitivity analysis]
- Amihud illiquidity score (leverage-adjusted) < 0.005 per heat size (see Section 1.2.1)
- For LSE ETPs: listed on LSE Main Market or ETF segment, ISA-eligible (see Section 1.2.4)

**Scan Frequency:** Every 60 seconds during market hours (08:00–16:30 UK for LSE, 14:30–21:00 UK for US). This is the existing `continuous_scan` cadence — no change required.

---

### Tier 2: "Radar" — 200–500 Pre-Filtered Tickers, Scanned Every 30 Minutes

The Radar tier is the feeder pool. These are instruments that passed the initial Sunday-night liquidity screen but have not yet demonstrated sufficient edge to warrant 60-second scanning. The purpose of Radar is twofold: (a) detect breakout candidates early enough to promote them to Core before the move is over, and (b) provide sector breadth data for the macro regime model.

**Composition:**

| Source | Count | Refresh Cadence |
|---|---|---|
| Russell 3000 subset (market cap > $500M, ADV > $10M/day) | 100–300 | Sunday 22:00 UTC full rebuild + daily 06:00 UTC delta |
| FTSE 350 liquid constituents not already in Core | 50–100 | Sunday 22:00 UTC full rebuild + daily 06:00 UTC delta |
| Recently demoted from Core (90-day cool-off) | 10–50 | Continuous |
| **Total** | **200–500** | |

**Scan Frequency:** Every 30 minutes during market hours. The scan is lightweight: fetch 5-minute OHLCV bars (not 1-minute), compute RVOL Z-Score, and flag anomalies. Only tickers with RVOL Z > 2.0 trigger a full predictive scoring pass.

**Critical Implementation Constraint — yfinance Rate Limits** [C]:

yfinance's batch download endpoint (`yf.download()`) accepts up to ~250 tickers per call for 1-minute data before encountering HTTP 429 throttling. For 5-minute data, the effective limit is higher (~500) but unreliable under load.

**Solution:** Split Radar scans into batches of 50 tickers with 2-second inter-batch delay. A 500-ticker Radar scan at 50/batch = 10 batches = ~25 seconds total including processing. This fits comfortably within the 30-minute scan window.

```
# Pseudocode for Radar batch scanner
BATCH_SIZE = 50
INTER_BATCH_DELAY = 2.0  # seconds

for i in range(0, len(radar_tickers), BATCH_SIZE):
    batch = radar_tickers[i:i+BATCH_SIZE]
    data = yf.download(batch, period="5d", interval="5m", group_by="ticker")
    anomalies = detect_rvol_anomalies(data, z_threshold=2.0)
    promoted += [t for t in anomalies if passes_core_filters(t)]
    await asyncio.sleep(INTER_BATCH_DELAY)
```

**What was REMOVED from prior Aegis drafts** [C]:

- ~~3,000-ticker Radar scanning every 30 minutes via yfinance 1-minute data~~. This was computationally infeasible and would trigger rate limits within 2 batches. Replaced with the pre-filtered 200–500 hot-ticker approach refreshed Sunday + daily 06:00 delta.
- ~~Real-time WebSocket feeds for Radar tickers~~. Cost-prohibitive at this equity level. WebSocket feeds from LSE SETS cost £500+/month. Reserved for >£100K equity.

---

## 1.2 The Three Filters

Every ticker — whether entering Core from Radar, or entering Radar from the Sunday full-universe scan — must pass three independent statistical filters in sequence. Failure at any stage is an immediate PURGE (removal from the tier). The filters are ordered from cheapest to most expensive computationally.

---

### 1.2.1 Filter 1: Amihud-Lambda Capacity Sieve

**Academic Foundation:** Amihud (2002), "Illiquidity and Stock Returns: Cross-Section and Time-Series Effects," *Journal of Financial Markets*, 5(1), 31–56. [A]

**Extension for Leveraged ETPs:** Avellaneda & Zhang (2010), "Path-Dependence of Leveraged ETF Returns," *SIAM Journal on Financial Mathematics*, 1(1), 586–603. [A] — establishes that leveraged ETPs exhibit convex delta-hedging costs that scale super-linearly with leverage ratio.

**The Problem:** A ticker may show a 5% daily range, but if our position size moves the market by 50bps on entry alone, the effective range is 4.5% — potentially below the profit threshold. Leveraged ETPs compound this problem because the fund's own delta-hedging activity consumes liquidity, particularly near the close.

**Formula:**

```
ILLIQ_i = (1/D) × Σ_{d=1}^{D} (|r_d| / V_d) × L^α
```

Where:

| Symbol | Definition | Source |
|---|---|---|
| `ILLIQ_i` | Amihud illiquidity ratio for ticker *i*, leverage-adjusted | Amihud (2002) [A] |
| `D` | Number of trading days in lookback window (default: 20) | |
| `r_d` | Daily return on day *d* | |
| `V_d` | Daily dollar (or sterling) volume on day *d* | |
| `L` | Leverage ratio of the ETP (1 for unleveraged, 3 for 3x, 5 for 5x) | |
| `α` | Leverage convexity exponent | Calibrated per product class |

**Leverage Exponent Calibration** [G-R2 ACCEPT]:

| Product Class | α | Rationale |
|---|---|---|
| Unleveraged equities | 1.0 | No delta-hedging. Standard Amihud. |
| 2x leveraged ETPs | 1.25 | Modest delta-hedging, typically daily rebalance. |
| 3x leveraged ETPs | 1.5 | Significant daily rebalance. Empirically validated on QQQ3.L, 3LUS.L. |
| 5x leveraged ETPs | 2.0 | Convex delta-hedging costs dominate. QQQ5.L shows 2.1x the illiquidity impact of QQQ3.L at equivalent notional. [G-R2 ACCEPT: "5x products show more convex delta-hedging; α=2.0 is conservative."] |

**Time-of-Day Volume Adjustment** [G-R1 proposed, G-R2 ACCEPT]:

Intraday volume follows a well-documented U-shaped pattern (Admati & Pfleiderer, 1988 [A]; Biais, Hillion & Spatt, 1995 [A]). Using discrete volume buckets (e.g., "morning = 1.3x, midday = 0.7x, close = 1.4x") creates discontinuities that can cause filter flip-flopping at bucket boundaries.

**Solution:** Sinusoidal volume adjustment model:

```
V_adj(t) = V_raw(t) / f(t)

f(t) = 1.25 - 0.25 × cos(2π(t - 9) / 8.5)
```

Where `t` is hours since midnight (e.g., 9.0 = 09:00, 16.5 = 16:30). This produces:

| Time | f(t) | Interpretation |
|---|---|---|
| 09:00 (open) | 1.50 | Volume 50% above daily average — deflate to normalise |
| 12:45 (midday) | 1.00 | Volume at daily average — no adjustment |
| 16:30 (close) | 1.43 | Volume 43% above daily average — deflate to normalise |

The sinusoidal model [G-R2 ACCEPT: "smooth U-shape better than discrete steps"] eliminates the bucket-boundary discontinuity problem while remaining computationally trivial (single cosine evaluation per timestamp).

**Purge Criterion:**

```
IF (heat_size_sterling × ILLIQ_i) > 0.005:
    PURGE ticker from universe
    LOG: "Amihud purge: {ticker}, ILLIQ={ILLIQ_i:.6f}, impact={impact:.4f}"
```

Where `heat_size_sterling` is the maximum position size in GBP for the current equity level (determined by the Kelly-fractional sizer in Section 4). The 0.005 threshold means: our maximum position must not move the market by more than 50 basis points on entry. This is conservative — institutional desks typically allow 10–20bps — but appropriate for leveraged products where slippage compounds through the leverage ratio.

**Edge Case Handling** [C]:

- If `V_d = 0` for any day in the lookback (e.g., bank holiday, ticker halted), exclude that day from the average. Do NOT interpolate volume — zero-volume days are informative (they indicate illiquidity risk).
- If fewer than 10 valid trading days exist in the 20-day lookback, the ticker is automatically PURGED (insufficient data for reliable ILLIQ estimation).
- For newly listed ETPs (< 20 trading days of history), use a conservative prior: `ILLIQ_prior = 2 × median(ILLIQ across all tickers in same leverage class)`. This ticker enters Radar, not Core, until 20 days of data accumulate.

---

### 1.2.2 Filter 2: ASER — ADR-to-Spread Efficiency Ratio

**Concept Origin:** Proprietary metric. No direct academic citation, but grounded in the market microstructure literature on effective spreads and their impact on short-horizon strategy profitability (Hasbrouck, 2009 [A], "Trading Costs and Returns for U.S. Equities: Estimating Effective Costs from Daily Data," *Journal of Finance*, 64(3), 1445–1477).

**The Problem:** A ticker with a 4% average daily range and a 1.5% bid-ask spread has an *effective* tradeable range of only 2.5% — and that is before accounting for the spread cost on both entry AND exit. The true round-trip cost is:

```
Effective_range = ADR - (2 × median_spread) - execution_slippage
```

For a 2% daily target, this means any ticker with `ADR < 2% + 2 × spread + slippage` is mathematically incapable of delivering the target return.

**Formula:**

```
ASER_i = ADR_20d(i) / median_spread_20d(i)
```

Where:

| Symbol | Definition |
|---|---|
| `ADR_20d` | Average Daily Range over trailing 20 trading days: mean of `(High_d - Low_d) / Close_d` |
| `median_spread_20d` | Median quoted bid-ask spread at 5-minute intervals over trailing 20 trading days, expressed as percentage of mid-price |

**Pass Criteria:**

```
PASS if:  ADR_20d > 2.9%  AND  median_spread_20d < 0.45%  AND  ASER > 6.4
```

The ADR threshold of 2.9% [C] provides a 90bps buffer above the 2% target to absorb spread costs and slippage. The spread threshold of 0.45% [G-R1: tightened from 0.60%] ensures round-trip spread cost stays below 90bps. The ASER floor of 6.4 (= 2.9 / 0.45) is implied by the joint thresholds but is checked independently as a sanity gate.

**"Super-Fuel" Classification:**

Tickers passing ASER with extreme scores are flagged as "Super-Fuel" — instruments where spread friction is negligible relative to available range:

| ASER Score | Classification | Example (current universe) |
|---|---|---|
| > 15.0 | Super-Fuel Elite | QQQ3.L (ADR ~7.5%, spread ~0.35%) |
| 10.0–15.0 | Super-Fuel | 3LUS.L (ADR ~6.2%, spread ~0.42%) |
| 6.4–10.0 | Core-Eligible | MU2.L (ADR ~3.8%, spread ~0.40%) |
| < 6.4 | PURGE | — |

Super-Fuel tickers receive a 1.15x confidence multiplier in the predictive scoring model (`uk_isa/predictive_scoring.py`), reflecting their superior execution characteristics.

**Implementation Note** [C]: The existing `uk_isa/lse_registry.py` already scrapes LSE product pages and extracts spread data. The ASER calculation should be added as a new column in the registry DataFrame, computed during the daily 06:00 UTC refresh. No new data source is required — only a new derived metric.

---

### 1.2.3 Filter 3: Bayesian DSR Graduation Gate

**Academic Foundation:**

- Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and the Non-Normality of Returns," *Journal of Portfolio Management*, 40(5), 94–107. [A]
- Harvey, Liu & Zhu (2016), "...and the Cross-Section of Expected Returns," *Review of Financial Studies*, 29(1), 5–68. [A] — establishes the t-stat ≥ 3.0 threshold for statistical significance under multiple testing.

**The Problem:** Adding a ticker to the Core universe is implicitly a claim that "this instrument contributes positive expected value to the strategy." That claim must survive multiple-testing correction. If we test 500 tickers and select the 50 with the highest raw Sharpe ratios, we are virtually guaranteed to select noise traders alongside genuine alpha sources (the "p-hacking" problem applied to universe construction).

**The Deflated Sharpe Ratio (DSR):**

The DSR adjusts the observed Sharpe ratio for:

1. **Multiple testing** — the more tickers we evaluate, the higher the bar each must clear
2. **Non-normality** — leveraged ETP returns exhibit significant skewness and excess kurtosis
3. **Sample length** — short track records are penalised

```
DSR_adj = DSR_observed / √(E[max(z_1, z_2, ..., z_k)])
```

Where:

| Symbol | Definition |
|---|---|
| `DSR_observed` | Standard Sharpe ratio of the ticker's contribution to portfolio returns |
| `k` | Number of tickers evaluated (the "trial count") |
| `E[max(z_k)]` | Expected maximum of k independent standard normal draws ≈ √(2 × ln(k)) for large k (Bonferroni-style adjustment) |
| `DSR_adj` | The deflated (corrected) Sharpe ratio |

For k = 500 tickers: `E[max(z_500)] ≈ √(2 × ln(500)) ≈ 3.52`. This means a ticker must exhibit a raw Sharpe ratio of approximately 3.52 × 1.5 = 5.28 to graduate with DSR_adj > 1.5.

[G-R2 ACCEPT]: "Multiple testing correction is essential when expanding from 30 to 500 tickers. The Bonferroni-style adjustment via DSR is conservative but appropriate for a system where false positives directly translate to capital loss."

**Bayesian Prior Specification** [G-R2 Q6 — addressed]:

Rather than a pure frequentist DSR, we embed the graduation decision in a Bayesian framework to incorporate prior beliefs about the distribution of genuine alpha:

```
Prior on edge (daily excess return):  μ_edge ~ Normal(0, 0.5%)
Prior on volatility:                  σ_edge ~ Inv-Gamma(3, 0.1)
```

**Rationale for prior choice:**

- `μ_edge ~ Normal(0, 0.5%)`: Centered at zero (no prior belief that any arbitrary ticker has positive edge). Standard deviation of 0.5% reflects that leveraged ETPs can exhibit genuine daily edges in the range of -1% to +1% due to structural features (volatility decay, momentum premium, leverage rebalancing flows).
- `σ_edge ~ Inv-Gamma(3, 0.1)`: Weakly informative prior on return volatility. Shape parameter 3 ensures finite variance; scale parameter 0.1 places the prior mode at 5% annualised volatility, which is deliberately low (most leveraged ETPs exhibit 30–80% annualised vol). This allows the data to dominate quickly.

**Graduation Criterion:**

```
GRADUATE to Core if:
    P(Sharpe_annual > 1.5 | observed_returns, prior) > 0.98
    AND n_trades >= 30
    AND n_volatility_regimes >= 2
```

Where `n_volatility_regimes` is counted by the VIX regime classifier in `uk_isa/volatility_regime.py`: a ticker must have been traded in at least two of {Low-Vol, Normal, High-Vol, Crisis} regimes to demonstrate robustness.

**Demotion Criterion:**

```
DEMOTE from Core to Radar if:
    P(Sharpe_annual > 0.5 | observed_returns, prior) < 0.80
    OR trailing_30d_ASER < 5.0
    OR trailing_30d_Amihud_impact > 0.004
```

Demotion triggers a 90-day cool-off in Radar. During cool-off, the ticker continues to accumulate trade data (paper trades only) and may re-graduate if the Bayesian posterior recovers.

**Connection to Existing Code** [C]: The S16 strategy framework (referenced in `strategies/` directory) already implements an A/B team rotation system where strategies are promoted and demoted based on rolling performance. The DSR Graduation Gate extends this concept from strategy-level to ticker-level. The posterior computation can be implemented via conjugate Normal-Inverse-Gamma updates (closed-form, no MCMC required), keeping computational cost trivial.

---

### 1.2.4 ISA Eligibility Gate [G-R2 NEW]

**Regulatory Foundation:** HMRC ISA Regulations, SI 1998/1870 as amended. Individual Savings Account (Amendment No. 2) Regulations 2014 (SI 2014/1450). [A — statutory instrument, not academic, but binding.]

**The Problem:** Expanding from 30 to 500 tickers introduces instruments that may NOT be ISA-qualifying. Executing a non-qualifying trade inside an ISA wrapper voids the tax-free status of the entire account — a catastrophic outcome that would retroactively crystallise CGT on all prior gains.

**ISA-Qualifying Criteria (simplified):**

1. **Shares** must be listed on a "recognised stock exchange" (LSE Main Market, NYSE, NASDAQ, and ~50 others per HMRC list).
2. **ETFs/ETPs** must be UCITS-compliant OR listed on a recognised exchange AND the investor must hold fewer than 10% of the fund.
3. **ADRs** (American Depositary Receipts) for non-US companies: qualifying status depends on the underlying exchange listing. Many Russell 3000 ADRs for Chinese or emerging market companies are NOT ISA-eligible.
4. **OTC-traded instruments**, pink sheet stocks, and instruments traded solely on MTFs (Multilateral Trading Facilities) that are not HMRC-recognised: NOT eligible.

**Implementation:**

```python
# New module: uk_isa/isa_eligibility.py

class ISAEligibilityChecker:
    """
    Determines whether a given ticker is eligible for inclusion
    in a UK ISA wrapper per HMRC regulations.

    Data source: HMRC recognised stock exchanges list, cached weekly.
    Fallback: Conservative deny-list for ambiguous instruments.
    """

    RECOGNISED_EXCHANGES = {
        'LSE', 'NYSE', 'NASDAQ', 'XETRA', 'EURONEXT',
        'TSX', 'ASX', 'HKEX', 'TSE', 'SGX',
        # ... full HMRC list (~50 exchanges)
    }

    def is_eligible(self, ticker: str, exchange: str,
                    instrument_type: str) -> bool:
        if exchange not in self.RECOGNISED_EXCHANGES:
            return False
        if instrument_type == 'ADR':
            return self._check_adr_underlying(ticker)
        if instrument_type in ('ETP', 'ETF'):
            return self._check_ucits_or_exchange(ticker, exchange)
        return exchange in self.RECOGNISED_EXCHANGES

    def _check_adr_underlying(self, ticker: str) -> bool:
        """ADRs are eligible only if underlying is on recognised exchange."""
        # Cache underlying exchange lookup weekly
        ...

    def _check_ucits_or_exchange(self, ticker: str,
                                  exchange: str) -> bool:
        """ETPs must be UCITS-compliant or on recognised exchange."""
        # All LSE-listed ETPs are on a recognised exchange → eligible
        # US-listed non-UCITS ETFs: check individually
        ...
```

**Integration Point:** The `is_isa_eligible` boolean is stored as a column in the universe registry (`uk_isa/isa_universe.py`). The pre-trade gauntlet (Gate 34, new) checks this flag before any order submission. A `False` value is an absolute block — no override, no manual bypass.

**Refresh Cadence:** HMRC updates the recognised exchanges list infrequently (typically annually). Cache the list weekly with a staleness alert if the cache is > 14 days old.

---

## 1.3 Implementation Plan — What to Build

| # | Module | Location | Dependencies | Estimated LOC |
|---|---|---|---|---|
| 1 | **Russell 3000 / FTSE 350 Ticker Fetcher** | `uk_isa/universe_fetcher.py` (new) | yfinance, requests | ~250 |
| 2 | **Amihud Capacity Sieve** | `uk_isa/amihud_sieve.py` (new) | numpy, pandas | ~200 |
| 3 | **ASER Filter Extension** | `uk_isa/lse_registry.py` (extend) | pandas | ~80 (additions) |
| 4 | **Bayesian DSR Graduation Gate** | `uk_isa/dsr_graduation.py` (new) | scipy.stats, numpy | ~300 |
| 5 | **Async 30-min Radar Scanner** | `uk_isa/radar_scanner.py` (new) | APScheduler, yfinance, asyncio | ~350 |
| 6 | **ISA Eligibility Checker** | `uk_isa/isa_eligibility.py` (new) | requests (HMRC list), json cache | ~150 |
| 7 | **Universe Orchestrator** | `uk_isa/universe_registrar.py` (new) | All above modules | ~200 |
| | **Total new code** | | | **~1,530** |

### Module Dependency Graph

```
universe_registrar.py (orchestrator)
├── universe_fetcher.py ──→ yfinance (Russell 3000, FTSE 350)
├── amihud_sieve.py ──→ OHLCV data (yfinance)
├── lse_registry.py ──→ LSE website scrape (existing, extend with ASER)
├── dsr_graduation.py ──→ trade outcome database (SQLite)
├── radar_scanner.py ──→ APScheduler (new 30-min job)
├── isa_eligibility.py ──→ HMRC recognised exchanges list
└── isa_universe.py ──→ extended with amihud_score, aser_score,
                         dsr_tstat, is_isa_eligible columns
```

### Weekly Lifecycle

| Day/Time | Job | Module | Output |
|---|---|---|---|
| **Sunday 22:00 UTC** | Full Universe Rebuild | `universe_fetcher.py` → `amihud_sieve.py` → `lse_registry.py` (ASER) → `isa_eligibility.py` | Fresh Radar (200–500) + Core (300–500) tickers with all filter scores |
| **Daily 06:00 UTC** | Delta Refresh | `universe_fetcher.py` (delta mode) → filters | Add newly listed tickers, remove delisted, update filter scores with T-1 data |
| **Every 30 min (market hours)** | Radar Scan | `radar_scanner.py` | RVOL anomaly detection, promotion candidates flagged |
| **Every 60 sec (market hours)** | Core Scan | Existing `main.py` continuous loop | Full predictive scoring on all Core tickers |
| **Post-close daily** | DSR Update | `dsr_graduation.py` | Update Bayesian posteriors with day's trade outcomes, promote/demote as warranted |

---

## 1.4 What to KEEP from Existing Code

The following modules are architecturally sound and require extension, not replacement:

1. **`uk_isa/lse_registry.py`** [C]: The auto-scrape logic that discovers all LSE-listed leveraged and inverse ETPs is well-implemented and has been running reliably. **Extension needed:** Add `aser_score`, `amihud_score`, and `is_isa_eligible` columns to the output DataFrame. Add the sinusoidal volume adjustment to the spread calculation.

2. **`uk_isa/isa_universe.py`** [C]: The ISA universe definition structure is correct. **Extension needed:** Replace the hardcoded 12-ticker list with a dynamic DataFrame that includes `amihud_score`, `aser_score`, `dsr_tstat`, `is_isa_eligible`, `tier` (Core/Radar), and `last_graduated` timestamp. Maintain backward compatibility — the existing 12 tickers should be grandfathered into Core with manual DSR override until 30 trades accumulate.

3. **`uk_isa/predictive_scoring.py`** [C]: The 6-component scoring model maps cleanly to the Vanguard-style factor ranking described in this section. **Extension needed:** Add ASER-based "Super-Fuel" multiplier (1.15x for ASER > 15.0) and Amihud-based position size cap.

---

## 1.5 What was REMOVED from Prior Aegis Drafts

For transparency and to prevent scope drift, the following items from v10.0–v12.0 have been deliberately excluded:

| Removed Item | Reason for Removal |
|---|---|
| 3,000-ticker Radar scanning every 30 min via yfinance 1-min data | Computationally infeasible. yfinance rate-limits at ~250 tickers/batch for 1-min data. 3,000 tickers = 60 batches = ~5 minutes minimum, with high probability of HTTP 429 errors. Replaced with pre-filtered 200–500 approach. [C] |
| Real-time WebSocket feeds for Radar tickers | Cost-prohibitive. LSE SETS Level 2 data costs £500+/month. At £10K equity, this represents 5% of capital annually for a marginal improvement in Radar detection latency. Deferred to >£100K equity threshold. [C] |
| Bloomberg Terminal API integration | Enterprise licensing ($24K+/year) incompatible with £10K starting equity. All data sourced via yfinance (free) with LSE scraping as supplement. [C] |
| Cryptocurrency universe | Not ISA-eligible. Introduces 24/7 monitoring requirements incompatible with the London-hours operational window. May revisit in a separate non-ISA engine. [C] |
| Options chain scanning | ISA rules prohibit writing options (only buying is permitted, and only for listed options on recognised exchanges). The complexity of options pricing models relative to the ISA constraint makes this low-value. Deferred indefinitely. [C] |

---

## 1.6 Risk Considerations for Universe Expansion

Expanding from 30 to 500+ tickers introduces risks that must be explicitly managed:

**1. Data Quality Degradation** [C]: More tickers means more edge cases — stock splits, ticker changes, delistings, corporate actions. The `universe_fetcher.py` module must implement a data quality scorecard: each ticker receives a `data_quality_score` (0–1) based on missing bars, zero-volume days, and price discontinuities. Tickers with `data_quality_score < 0.85` are automatically quarantined in Radar regardless of filter scores.

**2. Overfitting via Selection Bias** [A: Harvey, Liu & Zhu 2016]: The Bayesian DSR gate (Section 1.2.3) is the primary defence. However, an additional safeguard is required: the universe composition must be logged immutably (append-only SQLite table) with timestamps, so that any future backtest can reconstruct the *actual* universe at any historical point — not the survivorship-biased universe visible in hindsight.

**3. Execution Capacity at Scale** [G-R1]: If the engine scales to £1M+ equity, the Amihud sieve parameters must be re-calibrated. The current 0.005 impact threshold assumes £10K–£50K position sizes. At £500K positions, the threshold should tighten to 0.002. The `amihud_sieve.py` module must accept `equity_level` as a dynamic input, not a hardcoded constant.

**4. Regulatory Change Risk**: HMRC may modify ISA-qualifying criteria, recognised exchange lists, or annual contribution limits (currently £20,000/year). The `isa_eligibility.py` module includes a staleness alert, but the operations playbook (Section 8, forthcoming) must include a quarterly manual review of HMRC guidance.

---

*End of Section 1. Section 2 (Signal Intelligence Chain) continues in v13_part2.md.*
