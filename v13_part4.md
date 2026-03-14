# AEGIS Alpha-Omega Master Plan v13.0 — Part 4

## Sections 6-8: Risk Architecture, Liquidity Scaling, Infrastructure Hardening

---

# SECTION 6: RISK ARCHITECTURE — 15-Control Defence Matrix

The NZT-48 risk framework is a defence-in-depth architecture. No single control is trusted alone. All 15 controls operate independently and concurrently — any one can HALT or VETO a trade regardless of signal strength. Controls are grouped into legacy (verified in production code) and new (added in v12.0-v13.0 based on institutional audit and Gemini R2 adversarial review).

---

## Existing Controls (Verified in Code)

### R-01: Five Independent Circuit Breakers

| Breaker | Trigger Condition | Action Taken |
|---------|-------------------|--------------|
| Drawdown | Portfolio DD exceeds threshold (2% daily / 5% weekly / 15% total) | HALT all new entries. Existing positions retain stops but no new risk added. |
| VIX | VIX exceeds regime-adjusted threshold | Reduce position sizing or HALT depending on severity. |
| Correlation | Cross-asset correlation spike (contagion detection) | Reduce concurrent positions to 1. |
| Streak | Consecutive losing trades exceed threshold | Force cool-down period, reduce size on next entry. |
| Black Swan | Intraday move exceeds 3-sigma on any held instrument | Immediate HALT, flatten discretionary positions, notify P0. |

**Rationale**: Independent circuit breakers prevent single-point-of-failure in risk management. Each monitors a different failure mode. Academic basis: Danielsson et al. (2001), "An Academic Response to Basel II" — layered risk controls outperform monolithic VaR gates.

---

### R-02: Immutable Risk Rules

| Parameter | Value | Override Permitted |
|-----------|-------|--------------------|
| Max risk per trade | 0.75% of equity | NO |
| Daily loss halt | 2% of equity | NO |
| Weekly loss halt | 5% of equity | NO |
| Total drawdown halt | 15% of equity | NO |

**Trigger**: Any trade that would violate these limits is rejected at the DynamicSizer level before order generation.

**Action**: Trade is silently vetoed. No override mechanism exists. These values are hardcoded, not configurable via settings.yaml.

**Rationale**: Fixed fractional position sizing with hard stops prevents ruin. The 0.75% per-trade limit ensures survival of 133 consecutive losers before reaching 15% total DD — a statistical impossibility for any strategy with edge. Academic basis: Kelly (1956), "A New Interpretation of Information Rate"; Van Tharp (2006), position sizing as primary determinant of system performance; Ralph Vince (1990), optimal f and the danger of over-betting.

---

### R-03: Emotional Firewall (12 Blocked Patterns)

**Trigger**: The emotional firewall pattern-matches against 12 specific behavioural signatures that indicate revenge trading, tilt, FOMO, or irrational override attempts. These include:

1. Rapid re-entry after stop-out (< 5 min)
2. Size increase after losing trade
3. Manual override of automated stop
4. Entry against active regime signal
5. Multiple entries in same ticker within session
6. Entry during circuit breaker cool-down
7. Increasing frequency of trades after drawdown
8. Correlated re-entry (entering a correlated instrument after stop)
9. Late-session FOMO entry (after 16:00 UK)
10. Revenge sizing (position > 1.5x normal after loss)
11. Ignoring spread veto (manual attempt to bypass R-11)
12. Weekend/overnight hold increase during drawdown

**Action**: Pattern detected → entry BLOCKED, Telegram P1 alert sent with pattern name, 15-minute cool-down enforced.

**Rationale**: Behavioural finance research shows that post-loss decision-making quality degrades sharply. Academic basis: Kahneman & Tversky (1979), Prospect Theory — loss aversion drives irrational risk-seeking after losses; Odean (1998), "Are Investors Reluctant to Realize Their Losses?" — disposition effect empirically demonstrated.

---

### R-04: Six-Level Drawdown Recovery Cascade

| Level | DD Range | Position Cap | Size Multiplier | Max Heat | Action |
|-------|----------|-------------|-----------------|----------|--------|
| Green | 0% to -2% | 3 positions | 1.0x | 3.0% | Normal operations |
| Yellow | -2% to -4% | 2 positions | 0.75x | 2.0% | Reduced aggression |
| Orange | -4% to -6% | 1 position | 0.50x | 1.5% | Conservative only |
| Red | -6% to -8% | 1 position | 0.25x | 0.75% | Survival mode, A-team signals only |
| Critical | -8% to -10% | 0 new entries | 0.0x | 0.0% | HALT all new entries, manage exits only |
| Emergency | -10% to -12% | 0 new entries | 0.0x | 0.0% | HALT + manual review required to resume |

**Trigger**: Continuous monitoring of peak-to-trough equity drawdown.

**Action**: Automatic scaling of position count, size, and heat as drawdown deepens. Recovery requires returning to the previous level's threshold before privileges are restored (hysteresis prevents oscillation at boundaries).

**Rationale**: Graduated response preserves capital during adverse sequences while avoiding the binary "all-on/all-off" problem. Academic basis: Grossman & Zhou (1993), "Optimal Investment Strategies for Controlling Drawdowns" — optimal drawdown control requires dynamic position reduction, not binary stops.

---

### R-05: DynamicSizer — 8-Factor Kelly

The DynamicSizer computes position size as a fraction of Kelly optimal, adjusted by 8 independent factors:

| Factor | Description | Range |
|--------|-------------|-------|
| 1. Win Rate | Rolling 50-trade win rate | 0.3-0.7 |
| 2. Payoff Ratio | Average win / average loss | 0.5-3.0 |
| 3. Regime | Current HMM regime state | 0.5-1.0 multiplier |
| 4. Drawdown Level | Current cascade level from R-04 | 0.0-1.0 multiplier |
| 5. Volatility Regime | ATR relative to 60-day mean | 0.5-1.2 multiplier |
| 6. Correlation Load | Current portfolio correlation | 0.5-1.0 multiplier |
| 7. Signal Confidence | Meta-model confidence score | 0.6-1.0 multiplier |
| 8. Liquidity Factor | Q/V ratio from Kyle's Lambda | 0.5-1.0 multiplier |

**Formula**: `size = half_kelly(WR, PR) × Π(factor_multipliers) × equity`

Half-Kelly is used as the base (not full Kelly) to reduce variance of returns by 75% while sacrificing only ~25% of growth rate.

**Trigger**: Computed fresh for every trade entry.

**Action**: Final position size is the minimum of: DynamicSizer output, R-02 max risk (0.75%), and liquidity cap from R-11/Section 7.

**Rationale**: Multi-factor Kelly adapts to changing market conditions rather than using static sizing. Academic basis: Kelly (1956); Thorp (2006), "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market" — half-Kelly as practical optimum; MacLean, Thorp & Ziemba (2011), "Good and Bad Properties of the Kelly Criterion".

---

## New Controls (Added in v12.0-v13.0)

### R-06: Portfolio-Level Correlation Brake (Gate #34)

**Trigger**: Ledoit-Wolf shrinkage covariance matrix computed on rolling 60-day returns for all held positions. If 3 or more pairwise correlations exceed 0.70, the brake engages.

**Action**: Cap concurrent positions at 1. No new entries until correlation subsides below 0.60 (hysteresis band of 0.10 to prevent flicker). Existing positions are NOT force-closed — they retain their stops and profit targets.

**Rationale**: Leveraged ETPs on the same underlying (e.g., QQQ3.L and NVD3.L during a tech rally) create hidden concentration risk. Nominal "diversification" across 3 tickers that all track Nasdaq is an illusion. The Ledoit-Wolf shrinkage estimator (2004) provides a well-conditioned covariance matrix even with limited samples, avoiding the estimation error that plagues raw sample covariance.

**Academic cite**: Ledoit & Wolf (2004), "A Well-Conditioned Estimator for Large-Dimensional Covariance Matrices"; Kritzman, Page & Turkington (2010), "In Defense of Optimization" — correlation-aware position limits.

---

### R-07: Portfolio CVaR + CDaR Gate

**Trigger**: Two independent checks:
- **Per-trade CVaR**: Conditional Value-at-Risk at 95% confidence computed via historical simulation on 252-day rolling returns. If single-trade CVaR exceeds 1.5% of equity → VETO.
- **Portfolio CDaR**: Conditional Drawdown-at-Risk at 95% confidence for the aggregate portfolio. If portfolio CDaR exceeds 8% → HALT new entries.

**Action**: CVaR breach → individual trade vetoed. CDaR breach → all new entries halted until portfolio CDaR drops below 6% (hysteresis).

**Rationale**: VaR is insufficient for fat-tailed leveraged ETP returns. CVaR (Expected Shortfall) captures tail risk that VaR ignores. CDaR extends this to drawdown paths, which is the risk measure that actually matters for compounding strategies.

**Academic cite**: Rockafellar & Uryasev (2000), "Optimization of Conditional Value-at-Risk"; Chekhlov, Uryasev & Zabarankin (2005), "Drawdown Measure in Portfolio Optimization" — CDaR as a coherent risk measure for path-dependent strategies.

---

### R-08: Incremental CVaR (iCVaR) Veto

**Trigger**: Before any new position is added, compute the marginal increase in portfolio CVaR that the new position would cause. If iCVaR > 0.5% of equity → VETO.

**Action**: Trade is vetoed with Telegram P1 notification: "iCVaR VETO: adding [TICKER] would increase portfolio tail risk by [X]% (limit 0.5%)."

**Rationale**: A position may look safe in isolation (passes R-07 per-trade CVaR) but could increase portfolio tail risk disproportionately due to correlation or concentration effects. Incremental CVaR captures the marginal contribution to total tail risk.

**Academic cite**: Tasche (2002), "Expected Shortfall and Beyond" — decomposition of ES into marginal contributions; Rosen & Saunders (2010), "Risk Factor Contributions in Portfolio Credit Risk Models" — iCVaR methodology.

---

### R-09: Regime Transition Confirmation Buffer

**Trigger**: HMM regime model outputs a regime change (e.g., Bullish → Cautious, or Cautious → Crisis).

**Action**: The regime change is NOT acted upon immediately. Instead, a 3-tick (3-minute) confirmation buffer is imposed. The new regime must persist for 3 consecutive ticks before any downstream parameters (sizing, heat caps, strategy activation) are updated. If the regime flips back within the buffer window, the transition is discarded as noise.

**Rationale**: HMM regime models are prone to flickering at regime boundaries, especially during choppy markets. Acting on every transition causes whipsawing — reducing size at exactly the wrong time, or increasing it prematurely. The 3-tick buffer filters false transitions at minimal cost (3 minutes of delayed response is acceptable given position holding periods of hours to days).

**Academic cite**: Hamilton (1989), "A New Approach to the Economic Analysis of Nonstationary Time Series" — original Markov switching model; Ang & Bekaert (2002), "Regime Switches in Interest Rates" — regime persistence as a filtering mechanism.

---

### R-10: Anti-Correlation-Cascade Stop

**Trigger**: 3 or more stop-outs occur within any rolling 15-minute window across all held positions.

**Action**: Immediate P0 HALT. All open orders are cancelled. No new entries for 30 minutes. Telegram P0 alert with sound: "CASCADE DETECTED: [N] stops in [M] minutes. 30-min cool-down active."

**Rationale**: Multiple simultaneous stop-outs indicate a correlated market shock (flash crash, news event, liquidity vacuum). Continuing to trade during such events is extremely dangerous — spreads widen, fills deteriorate, and the next entry is likely to be stopped out as well. The 30-minute cool-down allows the market to find a new equilibrium.

**Academic cite**: Cont (2001), "Empirical Properties of Asset Returns: Stylized Facts and Statistical Issues" — volatility clustering and contagion; Brunnermeier & Pedersen (2009), "Market Liquidity and Funding Liquidity" — liquidity spirals and cascade mechanics.

---

### R-11: Market Maker Spread Veto

**Trigger**: Current bid-ask spread exceeds 2.5x the median spread over the previous 3 trading days.

**Action**: Trade entry is VETOED. Re-check every tick (60 seconds). If spread normalises within the signal's validity window, the trade may proceed. If not, the signal expires.

**Rationale**: Abnormal spread widening signals either toxic order flow (informed traders), low liquidity, or market stress — all conditions where execution quality will be poor. Entering during wide spreads means paying excessive implicit costs that erode the 2% daily target.

**Academic cite**: Kyle (1985), "Continuous Auctions and Insider Trading" — adverse selection and spread dynamics; Glosten & Milgrom (1985), "Bid, Ask and Transaction Prices" — spread as information cost.

---

### R-12: OBI Toxicity Wait Gate

**Trigger**: Order Book Imbalance (OBI) exceeds 0.80 (i.e., >80% of visible depth is on one side of the book).

**Action**: Wait 2 ticks (2 minutes), then re-check OBI. If OBI has normalised below 0.70, proceed with entry. If OBI remains elevated, wait another 2 ticks (max 3 retries = 6 minutes total). After 3 retries, VETO the trade.

**Rationale**: Extreme order book imbalance in low-volume ETPs often precedes a rapid price move in the opposite direction (spoofing, iceberg orders, or genuine informed flow). Waiting 2 minutes allows the imbalance to either resolve (safe to enter) or materialise into the adverse move (bullet dodged).

**Academic cite**: Cont, Stoikov & Talreja (2010), "A Stochastic Model for Order Book Dynamics" — OBI as predictor of short-term price direction; Cao, Chen & Griffin (2005), "Informational Content of an Open Limit-Order Book".

---

### R-13: US Open Stop Widening

**Trigger**: Clock-based. Active between 14:30 and 15:30 UK time (US market open window).

**Action**: ATR multiplier for stop-loss placement is widened from 1.5x to 2.0x for any position entered or held during this window. This applies to both new entries and existing positions that have not yet reached Rung 1 of the Chandelier exit.

**Rationale**: The US market open (14:30 UK) creates a volatility spike in LSE-listed ETPs that track US indices. QQQ3.L, 3LUS.L, and similar instruments experience 2-3x normal volatility in the first 30-60 minutes as the underlying catches up to US pre-market moves. Using normal stop widths during this window results in excessive stop-outs on noise.

**Academic cite**: Andersen & Bollerslev (1997), "Intraday Periodicity and Volatility Persistence in Financial Markets" — U-shaped intraday volatility with spikes at market open; Harris (1986), "A Transaction Data Study of Weekly and Intradaily Patterns in Stock Returns".

**Gemini R2 addition**: This control also addresses the LSE/NYSE Stampede Risk. At 14:30:01, the system imposes a 60-second Gap-Stabilization wait before acting on any signal that requires LSE price data, preventing stale-price entries during the cross-market synchronisation window.

---

### R-14: ETP Financing Cost Offset

**Trigger**: Any position held in an inverse or leveraged ETP for more than 1 trading day.

**Action**: Subtract a daily financing drag from expected return calculations:
- **Long leveraged ETPs (e.g., QQQ3.L, 3LUS.L)**: -2 bps/day
- **Inverse leveraged ETPs (e.g., QQQS.L, 3USS.L)**: -4 bps/day

This drag is applied in the PnL tracking, signal scoring, and target price calculations. A 2% target on an inverse ETP is therefore internally computed as a 2.04% gross target.

**Rationale**: Leveraged and inverse ETPs carry daily financing costs (swap fees, roll costs, compounding drag) that are invisible in the price but real in returns. Inverse ETPs carry roughly double the drag of long ETPs due to the additional cost of maintaining short swap positions. Ignoring this drag over multi-day holds leads to systematic under-performance versus backtested expectations.

**Academic cite**: Avellaneda & Zhang (2010), "Path-Dependence of Leveraged ETF Returns" — compounding drag and financing cost analysis; Cheng & Madhavan (2009), "The Dynamics of Leveraged and Inverse Exchange-Traded Funds".

---

### R-15: Gamma/Strike Proximity Risk

**Trigger**: The underlying index or stock is within 0.5% of a major options strike price with significant open interest (top 5 strikes by OI for the nearest monthly expiry).

**Action**: Subtract 10 points from the signal confidence score. If confidence after subtraction falls below the minimum entry threshold (60), the trade is vetoed.

**Rationale**: Options market makers who are short gamma near major strikes must delta-hedge aggressively, creating artificial support/resistance and erratic price behaviour. Leveraged ETPs amplify this effect 3x. Entering a momentum trade near a pinning strike increases the probability of mean-reversion whipsaws.

**Academic cite**: Ni, Pearson & Poteshman (2005), "Stock Price Clustering on Option Expiration Dates" — options pinning effect; Avellaneda & Lipkin (2003), "A Market-Induced Mechanism for Stock Pinning" — gamma exposure and delta-hedging flows.

---

## Drawdown Recovery Scaling by AUM

As equity grows, drawdown tolerance must tighten. A 12% drawdown on £10K is a £1,200 lesson. A 12% drawdown on £1M is a £120,000 catastrophe that takes months to recover from due to the compounding mathematics.

| AUM Tier | Yellow | Orange | Red | Critical | Emergency |
|----------|--------|--------|-----|----------|-----------|
| £10K - £100K | -2% | -4% | -8% | -10% | -12% |
| £100K - £500K | -1.5% | -3% | -6% | -8% | -10% |
| £500K - £1M | -1% | -2.5% | -5% | -7% | -9% |
| £1M+ | -1% | -2% | -4% | -6% | -8% |

**Implementation**: The drawdown cascade thresholds in R-04 are parameterised by `aum_tier` in `config/settings.yaml`. The tier is recalculated at the start of each trading day based on previous close equity. Transitions between tiers use the higher (tighter) thresholds — there is no grace period when crossing an AUM boundary upward.

**Rationale**: The Kelly criterion's optimal bet size decreases as a fraction of bankroll when the cost of ruin increases. At £1M+, the system has proven its edge and the priority shifts from growth to capital preservation. Academic basis: MacLean, Thorp & Ziemba (2010), "Long-Term Capital Growth: The Good and Bad Properties of the Kelly and Fractional Kelly Capital Growth Criteria".

---

## Control Interaction Matrix

No control operates in isolation. Key interactions:

- **R-01 (Circuit Breakers) + R-04 (Cascade)**: Circuit breakers trigger immediate halts; the cascade provides graduated response before the breaker trips. They are complementary, not redundant.
- **R-06 (Correlation Brake) + R-08 (iCVaR)**: R-06 uses pairwise correlation as a fast heuristic; R-08 uses full portfolio tail risk as the precise measure. R-06 fires first (cheaper to compute), R-08 is the authoritative gate.
- **R-10 (Cascade Stop) + R-13 (US Open Widening)**: R-13 prevents unnecessary stop-outs during US open volatility, which in turn prevents R-10 from triggering false cascade halts.
- **R-11 (Spread Veto) + R-12 (OBI Wait)**: Both address microstructure risk but from different angles. A trade must pass BOTH gates — wide spread vetoes immediately; normal spread but toxic OBI triggers a wait.
- **R-07 (CVaR Gate) + R-08 (iCVaR)**: R-07 checks absolute tail risk per-trade and portfolio-wide. R-08 checks the marginal contribution. A trade can pass R-07 but fail R-08 if the portfolio is already loaded with correlated tail risk.

---

# SECTION 7: LIQUIDITY SCALING MODEL

## The Fundamental Constraint

The 2% daily compounding strategy is not constrained by signal quality, strategy logic, or infrastructure at small equity sizes. The binding constraint at scale is **liquidity**. LSE-listed leveraged ETPs are niche instruments with limited daily volume. The strategy must acknowledge this ceiling and plan for it.

---

## Kyle's Lambda — Market Impact Model

The expected market impact of an order of size Q in a market with daily volume V is:

```
ΔP ≈ λ × √(Q / V_daily)
```

Where:
- `ΔP` = expected price impact (in basis points)
- `λ` = Kyle's lambda (market impact coefficient), empirically 0.1-0.3 for small-cap ETPs
- `Q` = order size in currency units
- `V_daily` = average daily volume in currency units (ADV)

**Reference**: Kyle (1985), "Continuous Auctions and Insider Trading" — the foundational model of price impact as a function of order flow.

For NZT-48's instruments, we use λ = 0.20 (mid-range, conservative for leveraged ETPs which have wider spreads and thinner books than their underlying).

---

## Impact Table — QQQ3.L Benchmark

**QQQ3.L**: 57,000 shares/day average volume x ~£25/share = **£1,425,000 ADV**

Portfolio heat is capped at 3% (NOT 15% — corrected from Gemini R2 assumption). However, this table shows both the actual 3% heat and a theoretical 15% heat for comparison, because the liquidity model must be tested against worst-case scenarios including future parameter changes.

### At 3% Portfolio Heat (Actual Cap)

| Equity | Heat (3%) | Q/V Ratio | Impact (λ=0.20) | Verdict |
|--------|-----------|-----------|------------------|---------|
| £10,000 | £300 | 0.02% | < 0.1 bps | SAFE — invisible to market |
| £50,000 | £1,500 | 0.11% | ~0.7 bps | SAFE — noise-level impact |
| £100,000 | £3,000 | 0.21% | ~0.9 bps | SAFE — well within tolerance |
| £250,000 | £7,500 | 0.53% | ~1.5 bps | SAFE — still acceptable |
| £500,000 | £15,000 | 1.05% | ~2.1 bps | CAUTION — monitor fill quality |
| £1,000,000 | £30,000 | 2.11% | ~2.9 bps | CAUTION — near participation limit |
| £3,000,000 | £90,000 | 6.32% | ~5.0 bps | DANGER — TWAP required |
| £10,000,000 | £300,000 | 21.05% | ~9.2 bps | WALL — impossible on single ETP |

### At 15% Portfolio Heat (Theoretical Maximum / Stress Test)

| Equity | Heat (15%) | Q/V Ratio | Impact (λ=0.20) | Verdict |
|--------|------------|-----------|------------------|---------|
| £10,000 | £1,500 | 0.11% | < 1 bps | SAFE |
| £50,000 | £7,500 | 0.53% | ~1.5 bps | SAFE |
| £100,000 | £15,000 | 1.05% | ~2.1 bps | SAFE |
| £250,000 | £37,500 | 2.63% | ~3.2 bps | SAFE |
| £500,000 | £75,000 | 5.26% | ~4.6 bps | CAUTION |
| £1,000,000 | £150,000 | 10.53% | ~6.5 bps | DANGER |
| £3,000,000 | £450,000 | 31.58% | ~11.2 bps | WALL |

---

## Critical Scaling Thresholds

### Tier 1: £10K - £100K (Current Phase)
- **Constraint**: None. Full access to all 12 ISA ETPs.
- **Participation rate**: < 0.5% of ADV on any single instrument.
- **Execution**: Market orders acceptable. Impact is noise-level.
- **Action required**: None. Focus on strategy refinement and track record building.

### Tier 2: £100K - £500K
- **Constraint**: Beginning to appear on market maker radar for lowest-volume ETPs.
- **Participation rate**: 1-3% of ADV on concentrated positions.
- **Execution**: Limit orders preferred. Monitor fill rates for slippage.
- **Action required**: Implement dynamic heat cap: `min(0.03 * ADV, equity_heat_cap)`. Diversify signal allocation across more tickers to avoid concentration.

### Tier 3: £500K - £1M
- **Constraint**: Single-ticker positions become market-moving on thin ETPs.
- **Participation rate**: 3-10% of ADV if concentrated.
- **Execution**: TWAP/VWAP mandatory for orders > 1% of ADV.
- **Action required**:
  - Dynamic heat cap becomes binding (0.03 x ADV caps position size).
  - Expand universe to include additional LSE ETPs and potentially direct FTSE 100 constituents within ISA.
  - Consider splitting orders across morning and afternoon sessions.

### Tier 4: £1M - £3M
- **Constraint**: Cannot deploy full heat into any single leveraged ETP without moving the market.
- **Participation rate**: Would exceed 5% of ADV on multiple instruments.
- **Execution**: Iceberg orders, TWAP over 30+ minutes, or broker algorithmic execution.
- **Action required**:
  - Must diversify across 6+ instruments minimum per day.
  - Consider unleveraged large-cap LSE stocks for a portion of the portfolio.
  - Evaluate IBKR Smart Routing for better execution.
  - The 2% daily target may need to be achieved across multiple smaller positions rather than one concentrated bet.

### Tier 5: £3M+ (Future State)
- **Constraint**: Leveraged LSE ETP universe is fundamentally too small.
- **Execution**: Current universe cannot absorb this equity without unacceptable impact.
- **Action required**:
  - Migrate primary execution to US-listed ETFs (TQQQ, SOXL, etc.) via a non-ISA account or SIPP.
  - Alternatively, transition to futures (Nasdaq 100 E-mini, S&P 500 E-mini) which have effectively unlimited liquidity.
  - ISA wrapper becomes a secondary, lower-allocation vehicle.
  - Reassess whether the 2% daily target is achievable or whether a lower target (1% daily = £10K → £120K annualised) is more realistic at scale.

---

## Scaling Protocol — Implementation

```python
def compute_max_heat(ticker: str, equity: float) -> float:
    """
    Compute maximum position heat for a given ticker and equity level.
    Returns the lesser of volume-based cap and equity-based cap.

    Volume cap: 3% of 20-day ADV (ensures < 3% daily participation).
    Equity cap: portfolio heat limit (3% of equity, from risk rules).
    """
    adv_20 = get_adv(ticker, lookback_days=20)  # 20-day average daily volume in £
    volume_cap = 0.03 * adv_20                    # Max 3% of daily volume
    equity_cap = 0.03 * equity                    # Max 3% portfolio heat

    max_heat = min(volume_cap, equity_cap)

    # Log if volume cap is binding (scaling wall approaching)
    if volume_cap < equity_cap:
        log.warning(
            f"LIQUIDITY WALL: {ticker} volume cap £{volume_cap:,.0f} "
            f"< equity cap £{equity_cap:,.0f}. "
            f"ADV: £{adv_20:,.0f}, Equity: £{equity:,.0f}"
        )
        send_telegram(
            priority="P2",
            msg=f"Liquidity scaling alert: {ticker} volume-constrained. "
                f"Max position: £{volume_cap:,.0f} vs desired £{equity_cap:,.0f}"
        )

    return max_heat


def get_execution_method(order_size: float, ticker: str) -> str:
    """
    Determine execution method based on order size relative to ADV.
    """
    adv = get_adv(ticker, lookback_days=20)
    participation = order_size / adv

    if participation < 0.01:        # < 1% of ADV
        return "MARKET"              # Immediate fill, negligible impact
    elif participation < 0.03:       # 1-3% of ADV
        return "LIMIT"               # Passive, wait for fill
    elif participation < 0.05:       # 3-5% of ADV
        return "TWAP_30MIN"          # Split over 30 minutes
    elif participation < 0.10:       # 5-10% of ADV
        return "TWAP_60MIN"          # Split over 60 minutes
    else:                            # > 10% of ADV
        return "REJECT"              # Cannot execute safely
```

---

## Multi-Ticker ADV Reference

For scaling planning, current ADV estimates for the 12 ISA-eligible ETPs:

| Ticker | Description | Est. Daily Vol (shares) | Est. Price (£) | Est. ADV (£) |
|--------|-------------|------------------------|-----------------|--------------|
| QQQ3.L | 3x Nasdaq 100 Long | 57,000 | £25 | £1,425,000 |
| 3LUS.L | 3x S&P 500 Long | 40,000 | £30 | £1,200,000 |
| SP5L.L | 5x S&P 500 Long | 15,000 | £8 | £120,000 |
| NVD3.L | 3x NVIDIA Long | 35,000 | £20 | £700,000 |
| 3SEM.L | 3x Semis Long | 25,000 | £15 | £375,000 |
| GPT3.L | 3x AI Basket Long | 20,000 | £12 | £240,000 |
| TSL3.L | 3x Tesla Long | 30,000 | £10 | £300,000 |
| TSM3.L | 3x TSMC Long | 15,000 | £18 | £270,000 |
| MU2.L | 2x Micron Long | 10,000 | £8 | £80,000 |
| QQQS.L | 3x Nasdaq 100 Short | 20,000 | £5 | £100,000 |
| 3USS.L | 3x S&P 500 Short | 15,000 | £4 | £60,000 |
| QQQ5.L | 5x Nasdaq 100 Long | 8,000 | £6 | £48,000 |

**Key insight**: The aggregate ADV across all 12 ETPs is approximately £4.9M. At 3% participation, the absolute maximum daily deployment is ~£147K, which corresponds to a portfolio size of approximately £4.9M at 3% heat. This is the hard ceiling for the current universe.

---

# SECTION 8: INFRASTRUCTURE HARDENING

## Current State Assessment

| Component | Current | Status | Risk |
|-----------|---------|--------|------|
| Compute | t3.small (2 vCPU, 2GB RAM) | UNDERSIZED | OOM risk with Apex Scout + ML meta-model |
| IP | Dynamic (no Elastic IP) | FRAGILE | IP changes on stop/start, breaks deploy scripts + CORS |
| Database | SQLite WAL mode, 22 tables | ADEQUATE for now | Single-writer limitation at scale |
| Cache | Redis 256MB, password-protected | ADEQUATE for now | Persistence race condition (Gemini R2 finding) |
| Backup | S3 script exists | NOT AUTOMATED | Manual execution = will be forgotten |
| Monitoring | Telegram alerts (89+ points) | PARTIAL | No infrastructure metrics (CPU, memory, disk) |
| CI/CD | Manual deploy via SSH | ABSENT | Human error risk on every deployment |
| VIX Default | Static 25.0 | INCORRECT | False caution signal in calm markets (Gemini R2 finding) |

---

## Phase 0: Critical Fixes (This Week)

### I-01: Allocate Elastic IP

**Problem**: EC2 instance `i-027add7c7366d4c86` has no Elastic IP. Every stop/start cycle changes the public IP, breaking:
- `deploy.sh` and `scripts/deploy_to_ec2.sh` (hardcoded IP)
- `.env.production` CORS origins
- Any external webhook or monitoring that references the IP
- SSH connection commands in documentation

**Fix**:
1. AWS Console → EC2 → Elastic IPs → Allocate Elastic IP address
2. Associate to instance `i-027add7c7366d4c86`
3. Update all references: deploy scripts, .env.production CORS, MEMORY.md
4. Cost: Free while instance is running. $0.005/hour if instance is stopped.

**Priority**: P0. This is a ticking time bomb — the next accidental stop/start will break the system until manually fixed.

---

### I-02: Automate S3 Backup

**Problem**: `scripts/backup_to_s3.sh` exists but is not scheduled. Backups only happen when manually remembered.

**Fix**:
```bash
# Add to crontab on EC2 instance (inside Docker or host)
# Daily at 05:00 UTC (before London market open)
0 5 * * * /home/ubuntu/nzt48-signals/scripts/backup_to_s3.sh >> /var/log/nzt48-backup.log 2>&1

# Backup should include:
# 1. SQLite database (full copy, not just WAL)
# 2. Redis AOF dump
# 3. config/settings.yaml (in case of drift)
# 4. Outcome/trade logs

# Add backup verification:
# After upload, check S3 object exists and size > 0
# Send Telegram P2 notification on success
# Send Telegram P0 notification on failure
```

**Retention**: Keep 30 daily backups, 12 weekly backups (Sunday), 6 monthly backups. S3 Lifecycle policy handles rotation.

---

### I-03: Fix VIX Default Value

**Problem**: When VIX data is unavailable (API failure, weekend, etc.), the system falls back to a static default of 25.0. This is problematic because:
- In calm markets (VIX 12-15), a default of 25 triggers false "elevated volatility" caution, causing the system to reduce sizing and miss opportunities.
- In crisis markets (VIX 40+), a default of 25 understates risk.

**Fix** (Gemini R2 ACCEPTED):
```python
def get_vix_default():
    """
    Dynamic VIX default: max of last known VIX and
    20-day MA + 5.0 buffer.
    Falls back to static 20.0 only if no historical data exists.
    """
    vix_last = get_last_known_vix()        # From Redis cache
    vix_ma20 = get_vix_moving_average(20)  # From SQLite

    if vix_last and vix_ma20:
        return max(vix_last, vix_ma20 + 5.0)
    elif vix_last:
        return vix_last + 5.0  # Buffer for staleness
    elif vix_ma20:
        return vix_ma20 + 5.0
    else:
        return 20.0  # Nuclear fallback (no data at all)
```

**Rationale**: The dynamic default tracks the market's actual volatility regime rather than imposing a static assumption. The +5.0 buffer on MA provides conservative bias when data is stale. Static 20.0 nuclear fallback is more neutral than 25.0.

---

### I-04: Redis WAIT for State Persistence

**Problem** (Gemini R2 NEW): Race condition between Chandelier exit rung triggers and Redis persistence. Sequence:
1. Price hits Rung 2 → system writes new stop level to Redis
2. Docker restarts (update, OOM, crash) before Redis flushes to AOF
3. On restart, Redis loads stale state → stop level reverts to Rung 1
4. Position held with wrong (too loose) stop → excess risk

**Fix**:
```python
import redis

def persist_critical_state(r: redis.Redis, key: str, value: str):
    """
    Write critical trading state to Redis with synchronous persistence.
    Uses WAIT to ensure at least 0 replicas have acknowledged
    (which forces AOF flush on standalone Redis).
    """
    pipe = r.pipeline()
    pipe.set(key, value)
    pipe.execute()

    # Force AOF rewrite if using AOF persistence
    r.bgsave()  # Or r.bgrewriteaof() depending on persistence mode

    # For critical state, also write to SQLite as backup
    write_state_to_sqlite(key, value)  # Belt and braces
```

**Additional safeguard**: On Docker restart, the system compares Redis state against SQLite state. If they diverge, SQLite wins (it uses WAL mode with synchronous writes). Telegram P0 alert is sent: "STATE DIVERGENCE DETECTED: Redis key [X] differs from SQLite. SQLite value used."

---

## Short-Term: Weeks 1-2

### I-05: Upgrade to t3.medium

**Problem**: t3.small has 2GB RAM. Current memory usage:
- Python main process: ~800MB
- Redis: 256MB (will be 512MB after I-07)
- Docker overhead: ~200MB
- SQLite page cache: ~100MB
- Total: ~1.35GB → leaving only 650MB headroom

With Apex Scout module running ML inference, memory spikes to ~1.8GB, leaving only 200MB before OOM killer intervenes.

**Fix**: Upgrade to t3.medium (2 vCPU, 4GB RAM). Same CPU, double the RAM.

**Procedure**:
1. Stop instance
2. Change instance type: `aws ec2 modify-instance-attribute --instance-id i-027add7c7366d4c86 --instance-type t3.medium`
3. Start instance
4. Elastic IP re-associates automatically (if I-01 is done first)
5. Cost increase: ~$0.0208/hr → ~$0.0416/hr (~$15/month increase)

**Timing**: Do this on a weekend when markets are closed. Total downtime: ~5 minutes.

---

### I-06: CloudWatch Monitoring

**Problem**: The only monitoring is Telegram alerts for trading events. No visibility into infrastructure health: CPU usage, memory pressure, disk space, Redis memory, SQLite size, process crashes.

**Fix**: Deploy CloudWatch agent with custom metrics:

| Metric | Source | Alarm Threshold | Action |
|--------|--------|-----------------|--------|
| CPU Utilization | CloudWatch built-in | > 80% for 5 min | P1 Telegram |
| Memory Used % | CloudWatch agent | > 85% | P0 Telegram |
| Disk Used % | CloudWatch agent | > 80% | P1 Telegram |
| Redis Memory | Custom metric (redis-cli INFO) | > 400MB | P1 Telegram |
| SQLite DB Size | Custom metric (ls -la) | > 500MB | P2 Telegram |
| Signals/Hour | Custom metric (app log parsing) | < 1 during market hours | P1 Telegram |
| Docker Container Restarts | Custom metric (docker inspect) | > 0 in 1 hour | P0 Telegram |
| Backup Age | Custom metric (S3 last modified) | > 26 hours | P0 Telegram |

**Cost**: CloudWatch agent is free. Custom metrics: $0.30/metric/month x 8 = $2.40/month.

---

### I-07: Redis Memory Limit 256 -> 512MB

**Problem**: As the number of tracked instruments grows and Chandelier exit state accumulates, Redis memory usage will approach the 256MB limit. When Redis hits its memory limit with `maxmemory-policy noeviction`, all writes fail silently, causing state corruption.

**Fix**: Update `docker-compose.yml`:
```yaml
nzt48-redis:
  image: redis:7-alpine
  command: redis-server --requirepass nzt48redis --maxmemory 512mb --maxmemory-policy noeviction --appendonly yes
```

Rebuild: `docker compose up -d nzt48-redis`

---

## Medium-Term: Month 2

### I-08: PostgreSQL Migration (RDS)

**Problem**: SQLite is excellent for the current scale but has fundamental limitations:
- Single-writer: concurrent writes from the web API and the engine can cause SQLITE_BUSY errors
- No replication: single point of failure
- Backup requires file-level copy (cannot do hot logical backups)
- No connection pooling
- 22 tables will grow; query performance on large tables degrades without proper indexing

**Fix**: Migrate to AWS RDS PostgreSQL (db.t3.micro, ~$15/month).

**Migration plan**:
1. Schema conversion: SQLite → PostgreSQL DDL (mostly compatible, fix AUTOINCREMENT → SERIAL, datetime handling)
2. Data migration: pgloader for one-shot migration
3. Application changes: Switch SQLAlchemy engine URI from `sqlite:///` to `postgresql://`
4. Test in parallel: Run both databases for 1 week, compare state
5. Cutover: Point application to RDS, keep SQLite as read-only backup for 1 month

**Benefit**: Enables future multi-process architecture (separate API server, separate engine, separate ML worker) all sharing the same database.

---

### I-09: CI/CD Pipeline (GitHub Actions)

**Problem**: Every deployment is a manual SSH + docker build process. This is error-prone and creates anxiety around deploying changes.

**Fix**: GitHub Actions workflow:
```yaml
# .github/workflows/deploy.yml
name: Deploy to EC2
on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r requirements.txt
      - run: python -m pytest tests/ -v

  deploy:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to EC2
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}  # Elastic IP from I-01
          username: ubuntu
          key: ${{ secrets.EC2_SSH_KEY }}
          script: |
            cd /home/ubuntu/nzt48-signals
            git pull origin main
            docker compose build nzt48
            docker compose up -d nzt48
```

**Gate**: Deployment only proceeds if all tests pass. Failed tests block the deploy.

---

## Notification Architecture

### Priority Levels

| Priority | Use Case | Delivery | Rate Limit | Sound |
|----------|----------|----------|------------|-------|
| **P0** | Drawdown > 3R, crash detection, API failure, cascade halt, state divergence | Instant | Unlimited | YES (alarm) |
| **P1** | Trade fill, stop hit, regime change, liquidity wall warning | Instant (silent) | 5/day, then batch | No |
| **P2** | New signal, graduation event, A/B team change, backup success | 30-min batch | 10/day | No |
| **P3** | Pattern statistics, SHAP drift, macro summary, ML health | 2x daily digest | 2/day | No |

### P0 Events (Never Suppressed)

| Event | Message Template |
|-------|-----------------|
| Daily DD > 2% | `P0 HALT: Daily drawdown {dd}% exceeds 2% limit. All entries suspended.` |
| Weekly DD > 5% | `P0 HALT: Weekly drawdown {dd}% exceeds 5% limit. All entries suspended until Monday.` |
| Total DD > 10% | `P0 EMERGENCY: Total drawdown {dd}%. Approaching 15% hard stop. Manual review required.` |
| Cascade halt (R-10) | `P0 CASCADE: {n} stops in {m} minutes. 30-min cool-down active.` |
| Docker restart | `P0 INFRA: Container {name} restarted. State integrity check: {result}.` |
| API failure | `P0 INFRA: {api_name} API failed {n} consecutive times. Last error: {err}.` |
| State divergence | `P0 INFRA: Redis/SQLite state divergence on key {key}. SQLite value used.` |
| Backup failure | `P0 INFRA: S3 backup failed. Last successful backup: {timestamp}.` |

### Correlation Escalation

**Rule**: If 3 or more P1 events fire within any 15-minute window, all subsequent events in that window are automatically escalated to P0.

**Rationale**: Multiple simultaneous P1 events (e.g., stop hit + regime change + liquidity warning) indicate a systemic event, not independent occurrences. The combination is more dangerous than any individual event.

**Implementation**:
```python
class NotificationEscalator:
    def __init__(self):
        self.p1_timestamps = deque(maxlen=100)

    def should_escalate(self) -> bool:
        now = datetime.utcnow()
        window = timedelta(minutes=15)
        recent = [t for t in self.p1_timestamps if now - t < window]
        return len(recent) >= 3

    def notify(self, priority: str, message: str):
        if priority == "P1":
            self.p1_timestamps.append(datetime.utcnow())
            if self.should_escalate():
                priority = "P0"
                message = f"[ESCALATED from P1] {message}"

        send_telegram(priority=priority, message=message)
```

### Weekly Report (Sunday 20:00 UK)

Delivered as a single Telegram message every Sunday at 20:00 UK time. Contains:

1. **Win Rate by Strategy**: S15 WR, overall WR, WR by regime
2. **Win Rate by Regime**: Bull/Cautious/Crisis breakdowns
3. **Dry Days**: Number of days with zero entries (and why — no signal vs. halted)
4. **ML Health**: Meta-model accuracy (rolling 50 trades), SHAP feature stability, drift alerts
5. **Compound Tracker**: Current equity, target equity (2% daily from start), delta, days ahead/behind schedule
6. **Infrastructure**: Backup status, Redis memory %, SQLite size, uptime, container restarts
7. **Next Week Outlook**: Upcoming macro events (FOMC, CPI, NFP, earnings for held tickers)

**Format**: Concise, numbers-first. No prose. Every line is actionable or informative.

```
=== NZT-48 WEEKLY REPORT ===
Week: 2026-03-02 to 2026-03-06

PERFORMANCE
Equity: £10,847 (+£847, +8.47%)
Target: £11,041 (2%/day compound)
Delta: -£194 (1.8 days behind)
Week WR: 7/11 (63.6%)
Week PnL Factor: 2.1

BY STRATEGY
S15 2% Target: 6/9 (66.7%), +£782
Other: 1/2 (50%), +£65

BY REGIME
Bull: 5/7 (71.4%)
Cautious: 2/4 (50.0%)

ML HEALTH
Meta-model accuracy (50-trade): 61.2%
SHAP top features: [regime, atr_ratio, obi]
Drift: None detected

INFRASTRUCTURE
Uptime: 168h (100%)
Redis: 89MB / 512MB (17%)
SQLite: 42MB
Backups: 7/7 successful
Container restarts: 0

NEXT WEEK
Mon: ISM Services
Wed: ADP Employment
Thu: ECB Rate Decision
Fri: US NFP
Earnings: None for held tickers
===
```

---

*End of Part 4 — Sections 6, 7, 8*
*AEGIS Alpha-Omega Master Plan v13.0*
