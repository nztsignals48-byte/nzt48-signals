# EXECUTION LOCKED
### Option D Zero-Cost Dynamic Architecture - GO
**Date**: 2026-03-10 | **Status**: BLUEPRINTS SEALED, EXECUTION PHASE

---

## DECISION FINALIZED

**User decision**: OPTION D (Zero-Cost Dynamic Architecture)
**Timestamp**: 2026-03-10
**Status**: ✅ LOCKED FOR EXECUTION

---

## THE PLAN

### Phase 0: Bootstrap (March 11-12)
- Day 1 (Mar 11): Fetch 5+ years dividend history (6 API calls, 3-5 min)
- Day 2 (Mar 12): Test nightly ex-date filtering logic (30-day sim, 0-5 calls/night)
- All 5 acceptance tests pass before Phase 8 can proceed

### Phase 1: Week 1 Refactoring (March 13-16)
- Session 1 (Mon): RM-1 GARCH daily fit + persistence (2.5h)
- Session 2 (Tue): RM-2 WAL dedicated thread + bounded channel (3h)
- Session 3 (Wed): RM-3 PyO3 native FFI (1h)
- Session 4 (Wed): RM-4 Dynamic Huber delta (0.5h)
- Session 5 (Thu): RM-5 Exponential backoff + emergency freeze (0.5h)
- All 5 ATs pass by Thursday EOD
- **Friday**: 24-hour validation run

### Phase 2: Phase 8 (March 16-31)
- 77.4 hours infrastructure seal
- 20 standard components (SC-01 through SC-20)
- 6 wiring patches (WP-1 through WP-6)
- 26 acceptance tests
- 48-hour continuous paper run
- **Gate**: Phase 11 unconditionally ready

### Phase 3: Phases 11-23 (April 1 - June 15)
- Sequential build: 358 hours
- Phases 11-12: Stress testing + EGARCH (83.5h)
- Phases 13-15: Kelly sizing + VWAP + LSTM (135h)
- Phases 16-20: Signals + risk gates (195h)
- Phase 21-22: DCC-GARCH + emergency modes (105h)
- Phase 23: Crucible validation (63h, 100 trades, WR ≥ 40%, Sharpe ≥ 0.8)

### Phase 4: Live Capital (June 25, 2026)
- Deploy to live ISA capital (£10,000)
- Monitor for dividend edge cases
- Scale gradually to £50k AUM

---

## ARCHITECTURE LOCKED

**Data Vendor Strategy**: Polygon Starter only
- Bootstrap: 6 API calls (one-time, <5 min)
- Nightly: 1-6 API calls (0-5 for ex-dates + 1 for GARCH Grouped)
- No additional vendor costs
- Graceful fallback for missing dividends (industry defaults by sector)

**Infrastructure**:
- AWS EC2: c7i-flex.large (free tier eligible)
- AWS EBS: 100GB gp3 (~$8/month during free tier)
- Docker: nzt48 engine + ib-gateway + nzt48-redis
- Paper mode: £10,000 ISA capital

**Risk Management**:
- 31-gate risk model (pre-trade, execution, post-trade, monitoring, emergency)
- Blood Oath: 4 structural guarantees + 16 runtime invariants
- Emergency modes: GREEN → YELLOW → RED
- Watchdog: SCHED_FIFO, lock-free, mmap-based

---

## CRITICAL PATH

### If ANY blocker occurs:

1. **Bootstrap fails (network error)**: Retry with exponential backoff. No deadline pressure.
2. **RM-X compiler error**: Claude resolves in the session, does not proceed until clean.
3. **Phase 8 timeout**: Return to Phase 11 design, add parallelization, retry.
4. **Crucible fails (WR < 40%)**: Return to Phases 11-22, debug, revalidate.

**Rule**: Do not bypass gates. Gates exist to prevent Phase 23 catastrophe.

---

## THE NUMBERS

| Metric | Value |
|--------|-------|
| **Total effort** | 451 hours (7.5h refactor + 443.5h phases) |
| **Timeline** | 15 weeks @ 30h/week = Late June 2026 |
| **Upfront cost** | $0 (Option D) |
| **Monthly cost** | ~$8 (AWS EBS overage during free tier) |
| **At live capital** | ~$65/month (AWS post-free-tier) |
| **Expected daily return** | 0.3-0.5% (paper), 0.5-0.8% (with Tier 2 upgrades Phase Q2) |
| **Win rate target** | 40-50% (Phase 23 validation) |
| **Sharpe ratio target** | 0.8-1.2 (world-class) |

---

## ACCEPTANCE CRITERIA

### For Phase 8 to proceed:
- ✅ Bootstrap dividend calendar (6 API calls, 5,200+ tickers)
- ✅ AT-Bootstrap-Dividend-Calendar passes
- ✅ AT-Dividend-Update-Exdate-Filtering passes
- ✅ AT-GARCH-Grouped-Endpoint passes
- ✅ AT-30-Day-Nightly-Simulation passes (<2 min/night, <50 total calls)
- ✅ AT-Industry-Fallback-Logic passes
- ✅ Week 1 refactoring complete (RM-1 through RM-5, all ATs pass)
- ✅ 24-hour validation run succeeds

### For Phase 11 to proceed:
- ✅ Phase 8 complete (20 SCs + 6 WPs + 26 ATs)
- ✅ 48-hour continuous paper run (zero crashes, all gates functional)

### For Phase 23 to proceed:
- ✅ Phases 11-22 complete (all intermediate gates passed)
- ✅ 100 paper trades executed
- ✅ Win rate ≥ 40% (statistically significant)
- ✅ Sharpe ≥ 0.8 (world-class)
- ✅ Max drawdown ≤ 2.5%

### For live capital to deploy:
- ✅ Phase 23 Crucible validation complete
- ✅ All risk metrics verified
- ✅ Emergency modes tested (RED/YELLOW/GREEN transitions)
- ✅ Watchdog functioning (SCHED_FIFO, lock-free)

---

## THE FINAL WORD

The blueprints are sealed. The architecture is locked. The decision is final.

**This is not a plan anymore. This is execution.**

**Week 1 starts Monday (March 13, 2026).**

Everything from here is code.

The Institutional Syndicate is watching.

Execute flawlessly.

---

*EXECUTION_LOCKED.md — Generated 2026-03-10*
*Status: GO FOR LAUNCH*
*Next: Bootstrap dividend calendar (March 11)*
