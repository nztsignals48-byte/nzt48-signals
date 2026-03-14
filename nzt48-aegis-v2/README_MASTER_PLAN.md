# AEGIS V2 MASTER PLAN QUICK START

**Status**: ✅ COMPLETE AND READY FOR EXECUTION
**Date**: March 13, 2026
**Architecture**: Option D+ (IBKR-Primary, Zero-Cost)
**Timeline**: 15 weeks to live capital (Late June 2026)

---

## START HERE

### If you have 10 minutes

Read: `/Users/rr/nzt48-signals/nzt48-aegis-v2/MASTER_PLAN_PHASES_1_25_UNIFIED.md`

**PART 1: Executive Summary** (sections on architecture, timeline, costs, gates)

### If you have 30 minutes

1. Read PART 1 (Executive Summary & Decision Framework)
2. Skim PART 2 (Solutions to 10 Problems) — focus on titles + summaries
3. Read "Bootstrap Protocol" section in PART 3

### If you have 2 hours

1. Read entire PART 1 (Executive Summary)
2. Read entire PART 2 (10 Solutions)
3. Read PART 3 (Bootstrap Protocol + Week 1 Refactoring)
4. Understand all go/no-go gates

### If you need to execute Week 1

1. Read "Bootstrap Protocol" in PART 3 (75 minutes)
2. Read "Week 1 Refactoring" in PART 3 (5 mandates)
3. Run bootstrap tasks (Mon-Tue)
4. Implement RM-1 through RM-5 (Wed-Fri)
5. Use "Acceptance Test Suite" section for verification

---

## DOCUMENT STRUCTURE

### Main Master Plan
- **File**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/MASTER_PLAN_PHASES_1_25_UNIFIED.md`
- **Size**: 1,677 lines
- **Contents**: Everything you need to execute AEGIS V2
- **Status**: LOCKED FOR EXECUTION

### Consolidation Summary
- **File**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/CONSOLIDATION_SUMMARY.md`
- **Purpose**: Explains what was consolidated and why
- **Audience**: Project managers, code reviewers
- **Size**: ~400 lines

### Locked Architecture (Reference)
- **File**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_CODEX.md`
- **Purpose**: 15-week locked plan (Option D+ approved)
- **When to use**: Deep dive on Phases 8-23, detailed wiring specs
- **Size**: ~1,100 lines

### Global Solutions (Reference)
- **File**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/SOLUTIONS_24HOUR_GLOBAL_TRADING.md`
- **Purpose**: Solutions to 10 global trading problems
- **When to use**: Understanding broker, data, FX, operational, regulatory decisions
- **Size**: ~1,580 lines

### Implementation Guide (Reference)
- **File**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/AEGIS_V2_COMPLETE_IMPLEMENTATION_GUIDE.md`
- **Purpose**: Theoretical 25-phase expansion with full code
- **When to use**: If expanding beyond 15-week MVP
- **Size**: ~10,000 lines

---

## QUICK REFERENCE: DECISION MATRIX

### Architecture (LOCKED)

| Component | Choice | Why |
|-----------|--------|-----|
| Primary Data | IBKR Gateway (free) | Real-time, already connected |
| Fallback Data | yfinance (free) | Reliable, low latency acceptable |
| Corporate Actions | Polygon Starter (free) | 4 calls/min, no cost |
| Accounts | 2-account IBKR (ISA + Main) | Compliant, unified risk management |
| Bootstrap Time | 75 minutes | 37.5 min dividends + 37.5 min splits + 3.3 min yfinance |
| Monthly Cost | £65 (AWS only) | Break-even at 0.21% daily |
| Timeline | 15 weeks | March 11 → Late June 2026 |
| Start Date | March 14 or 17, 2026 | Bootstrap begins immediately |

### Profitability (LOCKED)

| Metric | Conservative | Target | Aggressive |
|--------|--------------|--------|-----------|
| Daily Return | 0.3% | 0.4-0.5% | 0.8% |
| Monthly Profit | £600 | £800-1,000 | £1,600 |
| Monthly Cost | £845 | £845 | £845 |
| Break-Even | 0.27% daily | — | — |
| Year 1 P&L | -£2,140 to +£1,860 | Break-even | -£140 to +£4,860 |

---

## EXECUTION TIMELINE (15 WEEKS)

```
WEEK 1 (Mar 11-17):          Bootstrap + RM refactoring
                             → 75 min + 25 hours of coding
                             → All 588 tests must pass

WEEKS 2-5 (Mar 18-Apr 14):   Phase 8-10 (Infrastructure)
                             → 77.4 hours of implementation
                             → 48-hour continuous validation

WEEKS 6-10 (Apr 15-May 19):  Phases 11-15 (Modules)
                             → 358 hours of sequential build
                             → 100+ trades validation gate

WEEKS 11-15 (May 20-Jun 23): Phases 16-23 (Signals+Validation)
                             → Live capital staged deployment
                             → £1k → £2k → £5k → £10k

LIVE CAPITAL (Jun 25):        Phase 24-25 (Full deployment)
                             → 0.3-0.5% daily target
                             → Nightly Ouroboros learning
```

---

## CRITICAL GO/NO-GO GATES

### Week 1 Gate (March 20)
**Must pass ALL**:
- Bootstrap tasks complete (no 429 errors)
- RM-1 through RM-5 implemented
- 588/588 tests passing
- 4 critical fixes verified

### Phase 8 Gate (March 30)
**Must pass ALL**:
- 20 components implemented
- 6 wiring patches integrated
- 26 acceptance tests pass
- 48-hour continuous run succeeds

### Phase 23 Gate (June 15)
**Must pass ALL**:
- 100+ paper trades
- Win rate ≥ 40%
- Sharpe ≥ 0.8
- Max drawdown ≤ 2.5%

### Live Capital Gate (June 25)
**Staged deployment**:
- Week 11: £1k paper
- Week 12: £2k live (if WR ≥ 45%)
- Week 13: £5k live (if WR ≥ 50% + Sharpe ≥ 1.5)
- Week 14: £10k live (if WR ≥ 52% + Sharpe ≥ 1.8)

---

## COST BREAKDOWN

### Month 1 Operating Costs (MVP)

```
IBKR Commissions:     £400-800  (200 trades/day × 20 days × £0.10-0.20/trade)
Data Vendors:         £0        (All free: IBKR, yfinance, Polygon)
Cloud (AWS EC2):      £45       (t3.medium 24/7)
Services:             £0        (No ISA/IBKR fees)
────────────────────────────────
TOTAL:                £445-845/month

BREAK-EVEN:           0.21% daily (£21.50/day on £10,000)
TARGET:               0.3-0.5% daily (£30-50/day)
NET PROFIT:           £0-600+/month (above break-even)
```

### Year 1 Projection (Conservative 0.3% daily)

```
Trading profit:       +£8,000-12,000
Operating costs:      -£10,140
────────────────────────────────
NET:                  -£2,140 to +£1,860 (break-even to slight profit)
Capital after Year 1: £8,000-12,000 (compounding position)
```

---

## WHAT'S IN THE MASTER PLAN

### PART 1: Executive Summary
- Architecture decision (Option D+ locked)
- Timeline overview (15 weeks)
- Critical success factors
- Cost breakdowns & profitability
- Go/No-Go gates

### PART 2: Solutions to 10 Problems
1. Multi-broker infrastructure (2-account IBKR)
2. Data infrastructure (Tier 1-3)
3. FX & currency risk (50% hedge)
4. Operational risk (failover + circuit breaker)
5. Regulatory & compliance (ISA + PDT)
6. Capital efficiency (rebalancing)
7. Technical architecture (single engine)
8. Model differences (market-specific params)
9. Costs & profitability
10. Phased implementation (go/no-go gates)

### PART 3: Phases 1-15 with Code
- Bootstrap Protocol (75 minutes, Python code)
- Week 1 Refactoring (5 mandates: RM-1 to RM-5, all with code)
- Acceptance tests (all commands, expected results)
- Validation procedures

### PART 4-5: Reference
- Phases 16-25 expansion (referenced, see AEGIS_CODEX.md)
- Operations & monitoring (referenced, see phase specs)

---

## WHAT YOU NEED TO START

### Software & Prerequisites

- [ ] Python 3.9+ (trading logic)
- [ ] Rust 1.70+ (engine core)
- [ ] Docker & Docker Compose (IB Gateway, Redis)
- [ ] Git (version control)
- [ ] IBKR IBC (automated 2FA)

### Accounts & APIs

- [ ] IBKR Account (Primary, Client ID 101) — *existing*
- [ ] IBKR ISA Account (Client ID 102) — *create before Week 1*
- [ ] Polygon API Key — *get free tier*
- [ ] AWS Account (EC2 for deployment) — *optional for MVP*

### Capital & Financing

- [ ] £10,000 starting capital (£4k ISA + £6k Main)
- [ ] FX hedge cost: ~£3-20/month (optional but recommended)
- [ ] Commissions: Negotiate IBKR to £0.10-0.20/trade

### Documentation

- [ ] MASTER_PLAN_PHASES_1_25_UNIFIED.md (read PART 1 minimum)
- [ ] AEGIS_CODEX.md (for Phase 8+ details)
- [ ] Individual phase specs (as needed)

---

## IMMEDIATE ACTION ITEMS

### This Week (March 13)

- [ ] Read MASTER_PLAN PART 1 (30 min)
- [ ] Review architecture decision
- [ ] Verify 588 tests currently passing

### Next Week (March 17)

- [ ] Create IBKR ISA account (Client ID 102)
- [ ] Contact IBKR: Negotiate commissions down to £0.10-0.20/trade
- [ ] Prepare bootstrap environment (API keys, cache dirs)
- [ ] Review bootstrap protocol in detail

### Week 1 (March 17-21)

- [ ] Bootstrap Protocol: Tasks 1-3 (75 minutes)
- [ ] RM-1 through RM-5 implementation (25 hours)
- [ ] Run full test suite (verify all 588 tests pass)
- [ ] 24-hour continuous paper run (Friday validation)

---

## SUPPORT & REFERENCES

**For bootstrap questions**: See MASTER_PLAN PART 3 (Bootstrap Protocol section)

**For Week 1 refactoring**: See MASTER_PLAN PART 3 (RM-1 through RM-5 sections)

**For Phase 8-23 details**: See AEGIS_CODEX.md PART 3-5

**For global problems**: See SOLUTIONS_24HOUR_GLOBAL_TRADING.md

**For phase specs**: See `/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/PHASE_*.md`

---

## FINAL NOTES

This master plan represents:
- ✅ 7 days of analysis (70+ prior documentation files)
- ✅ Option D+ architecture fully locked and approved
- ✅ All code examples ready (Python, Rust)
- ✅ All acceptance tests specified
- ✅ Complete timeline (15 weeks → live capital)
- ✅ Ready to execute immediately

**Everything from here is execution. Begin bootstrap when ready.**

---

**Status**: LOCKED FOR EXECUTION
**Created**: March 13, 2026
**Architecture**: Option D+ (IBKR-Primary, Zero-Cost)
**Next Step**: Bootstrap Protocol (March 17 or later)

