# AEGIS CODEX — START HERE

**Date**: 2026-03-10 | **Status**: ✅ CONSOLIDATED & READY

---

## WHAT IS THIS?

This folder now contains a **single unified planning document** that consolidates all execution blueprints, timelines, and critical corrections for the AEGIS V2 hedge fund trading system.

**NEW**: All planning has been merged into one master file.
**OLD**: 50+ documents with versions, audits, and analysis.

---

## THE ONE FILE YOU NEED

### **[AEGIS_CODEX.md](AEGIS_CODEX.md)**

**35 KB | 1,094 lines | Single source of truth**

This contains everything needed to execute:
- Executive summary (Decision: Option D, Cost: $0)
- Bootstrap protocol (2 days, 4 Fourteenth-Order corrections)
- Week 1 refactoring (7.5 hours, 5 mandates)
- Phase 8 infrastructure (77.4 hours)
- Phases 11-23 sequential build (358 hours)
- Live capital deployment (£10,000 ISA)
- Final timeline (15 weeks to late June 2026)

---

## QUICK NAVIGATION

### Want to understand the decision?
→ **AEGIS_CODEX.md, PART 1: EXECUTIVE SUMMARY**

### Ready to start bootstrap (March 11)?
→ **AEGIS_CODEX.md, PART 2: BOOTSTRAP PROTOCOL**

### Starting Week 1 refactoring (March 13)?
→ **AEGIS_CODEX.md, PART 3: WEEK 1 REFACTORING**

### Need to know the timeline?
→ **AEGIS_CODEX.md, FINAL TIMELINE** (table at end)

### Want to understand phase gates?
→ **AEGIS_CODEX.md, PART 7: DECISION FRAMEWORK**

---

## OTHER DOCUMENTS IN THIS FOLDER

### Keep These (Critical for execution)
- ✅ `00_CANONICAL_RULES.md` — Type definitions (needed for refactoring)
- ✅ `01_DATA_CONTRACTS.md` — Data vendor contracts
- ✅ `02_STATE_MACHINE.md` — State machine definitions
- ✅ `03_ACCEPTANCE_TESTS.md` — Acceptance test specifications
- ✅ `PHASE_11_DIRECT_EQUITY_SPEC.md` — Phase 11 specification
- ✅ `PHASE_12_EUROPEAN_EQUITY_SPEC.md` — Phase 12 specification
- ✅ `PHASE_13_ASIA_PACIFIC_SPEC.md` — Phase 13 specification
- ✅ `checkpoints/PHASE_*_GATE.md` — Phase checkpoints (reference)

### Archive These (Consolidated into CODEX)
See **[CONSOLIDATION_SUMMARY.md](CONSOLIDATION_SUMMARY.md)** for the complete list of 50+ documents that can be moved to `archive/` folder.

---

## THE CORE FACTS (TLDR)

| Metric | Value |
|--------|-------|
| **Decision** | Option D (zero-cost dynamic architecture) |
| **Cost** | $0/month (Polygon Starter only) |
| **Timeline** | 15 weeks (late June 2026) |
| **Bootstrap** | 2 days (March 11-12) |
| **Refactoring** | 1 week (March 13-16) |
| **Phase 8** | 2 weeks (March 16-31) |
| **Phases 11-23** | 10 weeks (April 1 - June 15) |
| **Go Live** | June 25, 2026 (£10,000 ISA) |
| **Daily Return Target** | 0.3-0.5% net (after costs) |
| **Sharpe Target** | 0.8+ (world-class) |

---

## FOUR CRITICAL FIXES (MANDATORY)

All four must be implemented before Week 1 refactoring:

1. **Polygon Pagination Reality**
   - 150 API calls with 15-sec delays (37.5 min, not 3-5 min)
   - File: `bootstrap_dividend_calendar.py`

2. **Stock Splits Bootstrap**
   - Parallel 150 API calls for splits (prevents 1000% Kalman spikes)
   - File: `bootstrap_splits_calendar.py`

3. **YFinance Throttling**
   - 0.5-1.5 second jitter, 2-worker sequential (prevents IP ban)
   - File: `step_0_yfinance_loader.py`

4. **Corporate Action Mutability Check**
   - Nightly validation against Polygon API
   - File: `step_0_corporate_action_audit.py`

All four are detailed in **AEGIS_CODEX.md, PART 2**.

---

## WEEK 1 REFACTORING (5 MANDATES)

All must pass acceptance tests before Phase 8 proceeds:

1. **RM-1**: GARCH Daily Fit + Real-Time Residuals (2.5h)
2. **RM-2**: WAL Dedicated Thread + Bounded Channel (3h)
3. **RM-3**: PyO3 Native FFI Conversions (1h)
4. **RM-4**: Dynamic Huber Delta (MAD-Based) (0.5h)
5. **RM-5**: Exponential Backoff + Emergency Freeze (0.5h)

All five detailed in **AEGIS_CODEX.md, PART 3**.

---

## PHASE 23 CRUCIBLE (LIVE CAPITAL GATE)

Must pass all criteria before £10,000 ISA deployment:

- ✅ 100 paper trades minimum
- ✅ Win rate ≥ 40% (statistically significant)
- ✅ Sharpe ≥ 0.8 (world-class)
- ✅ Max drawdown ≤ 2.5% (hard stop)
- ✅ Diversity metric: ≥4 uncorrelated sectors
- ✅ Walk-forward validation (10 windows)

Detailed in **AEGIS_CODEX.md, PART 5 (Phase 23 Crucible)**.

---

## START HERE

1. **First time?** Read **AEGIS_CODEX.md** top to bottom (30 min)
2. **Starting bootstrap (March 11)?** Jump to **PART 2** 
3. **Starting refactoring (March 13)?** Jump to **PART 3**
4. **Need to archive old docs?** See **CONSOLIDATION_SUMMARY.md**

---

## QUESTIONS?

All answers are in **AEGIS_CODEX.md**. Search for keywords:
- "bootstrap" → Part 2
- "refactoring" → Part 3
- "phase 8" → Part 4
- "phase 11-23" → Part 5
- "live capital" → Part 6
- "decision" → Part 7

---

**Status**: Ready for execution starting March 11, 2026.

*Consolidated: 2026-03-10*
