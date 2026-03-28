# STRATEGY HABITAT MAP
## Phase 4 — System 1 Shadow Testing + Legacy Forensics
### Date: 2026-03-28

---

## LEGACY-ADX FORENSIC VERDICT

**NO EDGE.** Institutional backtest (production-parity, 2,336 trades):
- Overall: 14.7% WR, PF 0.122, -£3,329 total P&L, 42.7% max DD
- TypeA: 3.9% WR, PF 0.007 → ABANDON
- TypeB: 20.1% WR, PF 0.201 → ABANDON (was 52.4% in synthetic — 32pp degradation)
- TypeC: 50.0% WR, PF 1.059 → INSUFFICIENT DATA (16 trades)
- TypeD: 13.9% WR, PF 0.093 → ABANDON
- TypeE: 16.7% WR, PF 0.189 → ABANDON
- TypeF: 12.7% WR, PF 0.101 → ABANDON

**Root cause:** Synthetic backtests used unrealistic fills, underestimated costs, and likely had look-ahead bias. The 32pp WR degradation from synthetic to production proves overfitting.

---

## SYSTEM 1: MICROSTRUCTURE MOMENTUM

### Signal Logic
Price-based order flow proxy (no L2 data required):

| Indicator | Computation | Bullish Threshold |
|-----------|------------|-------------------|
| TMR (Trade-to-Mid Ratio) | (last - mid) / spread | > 0.3 |
| VPIN | Volume-sync informed trading | > 0.6 |
| Spread Compression | current_spread < 0.8 * avg_spread | true |
| Tick Momentum | up_ticks / (up + down) over 20 ticks | > 0.6 |

**Entry rule:** 3+ of 4 indicators bullish AND ADX > 15.

### Confidence Graduation
| Condition | Points |
|-----------|--------|
| Base (3/4 aligned + ADX > 15) | 55 |
| 4/4 alignment | +5 |
| ADX > 25 (strong trend) | +10 |
| RVOL > 1.5 (volume confirmation) | +5 |
| TMR > 0.6 (strong buy pressure) | +5 |
| **Maximum** | **80** |

### Habitat
- **Instruments:** All Tier 1 ETPs (leveraged 3x/5x on LSE, US equivalents)
- **Regime:** Normal, Caution (disabled in Stress/Crisis by macro gate)
- **Session:** LSE 08:00-15:45, US 14:30-20:00 London time
- **Minimum ticks:** 20 (for reliable TMR/momentum calculation)

### Why This Should Work Where Legacy Failed
1. **Microstructure > Technical:** TMR/VPIN measure actual order flow, not lagging indicators (ADX, RSI)
2. **No look-ahead:** All inputs are from current or past ticks, never future
3. **Cost-aware:** 3+ indicator alignment raises confidence only when conviction is high → fewer trades
4. **Spread-aware:** Spread compression check ensures we only enter when execution is favorable
5. **Production-realistic:** Slippage model (0.5%) already active from Phase 3

### Shadow Mode
System 1 competes with all other signal generators on raw confidence. It does NOT get priority. If its signals win (highest confidence), trades happen. If not, other strategies win. This is natural A/B testing.

### Kill Criteria (Per Master Plan)
| Criterion | Threshold | Action |
|-----------|-----------|--------|
| Consecutive losses | >=8 | Halt system |
| Rolling 30-day PF | <0.8 | Reduce allocation to 50% |
| Rolling 30-day PF | <0.6 for 2 months | Kill system |
| Max drawdown from peak | >15% | Halt system |
| Negative edge duration | >60 days | Kill system |

---

## REMAINING LEGACY STRATEGIES (Retained for Data)

| Strategy | Status | Reason |
|----------|--------|--------|
| VanguardSniper (Momentum) | ACTIVE | Legacy primary — data collection continues |
| Orchestrator | ACTIVE | Multi-strategy routing — data collection |
| IBS Mean Reversion | ACTIVE | TypeE: highest walk-forward stability |
| VolExpansion | ACTIVE | Volume structure confirmation |
| ORB Breakout | ACTIVE | US session only |
| GapFade | ACTIVE | Liquidity gap filling |

All legacy strategies remain active. System 1 competes alongside them. If S1 consistently wins on confidence, it naturally displaces legacy signals.

---

## VALIDATION PATH (Per Book 6)

| Stage | Min Trades | WR Gate | PF Gate | Sharpe | MC 5th > 0 |
|-------|-----------|---------|---------|--------|------------|
| 1 (Backtest) | 500 | >=40% | >=1.2 | >=0.5 | YES |
| 2 (Paper) | 100 | >=42% | >=1.2 | >=0.4 | YES |

**Current status:** Stage 2 (paper shadow testing). Need 100 S1 signals to evaluate.
