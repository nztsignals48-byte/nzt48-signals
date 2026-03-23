# AEGIS V2 — Master Progress Ledger

## Purpose
Session recovery source of truth. Append-only. If a session stops unexpectedly, the next session resumes from here.

---

## Session: 2026-03-23/24 (Full System Audit + Deploy)

### Chunk 1: Codebase Audit (16:40 UTC, Mar 23)
- **Objective**: Map entire system from real code
- **Files Read**: 74 .rs files (32,603 LOC), 40+ .py files, config.toml, docker-compose.yml, .gitignore, 277 .md files
- **Findings**: 12 contradictions registered, 4 simplification items, 14 action items ranked by ROI
- **Key Discovery**: WAL persistence is CORRECT (Docker named volume `aegis-events`). EC2 data/ dir has reports, not WAL files.
- **Key Discovery**: TypeA/D are NET LOSERS in backtest (29.5%/24.1% WR, PF 0.04/0.03) — but kept active for live data collection

### Chunk 2: Sprint A — Infrastructure Fixes
- **EC2 Disk**: Pruned from 83% → 79% (reclaimed 785MB)
- **WAL Fix**: NOT NEEDED — already on persistent Docker volume
- **.env Fix**: NOT NEEDED — already in .gitignore

### Chunk 3: Sprint C — New Strategies
- **Files Changed**: `python_brain/bridge.py` (+140 lines)
- **New Strategies**:
  - IBS_MeanReversion: IBS < 0.2 + RSI(2) < 15 + RVOL > 0.7 (mean-reverting regimes)
  - VolExpansion: RVOL > 2.0 + ADX > 20 + 3+ up bars + price > EMA20
  - ORB_Breakout: US session 14:45-15:30 UTC, breaks opening range high with volume
- **Tests**: `py_compile bridge.py` — OK, `cargo check --release` — OK

### Chunk 4: TypeA/D Decision
- Initially disabled TypeA/D (commit `89ec4a8`)
- User requested keeping them active for paper data collection
- Reverted block (commit `596e695`) — TypeA/D active, Ouroboros tracks per-type WR

### Chunk 5: Deploy + Verify
- 3 deploys total (initial + TypeA/D revert)
- All 3 containers healthy after each deploy
- 100 reqMktData streams active (HKEX, XETRA, EURONEXT, SGX)
- Python Bridge started successfully, no errors

### Chunk 6: Documentation
- CANONICAL_SYSTEM_PLAN.md — written (reflects actual code truth)
- MASTER_PROGRESS_LEDGER.md — this file
- AEGIS_V2_OPERATING_MANUAL.pdf — generated (6 pages, PyMuPDF)

---

## Trade Report: All-Time (308 WAL Events)

### Summary
- **Total entries**: 35 (Long)
- **Total exits**: 15 (Sell)
- **Position closures**: 15
- **Open positions**: 0 (all flattened by EodFlatten/HaltFlatten)
- **Equity**: £10,000 (starting) — P&L tracking shows pnl=None (needs Rust fix)
- **Strategies**: Momentum (33), TypeE (2)
- **Entry types**: Unclassified (33), TypeE (2)
- **New strategies** (IBS/ORB/VolExpansion): 0 signals yet — need market open + 50-bar warmup

### Exit Reasons
- EodFlatten: 8 (end of day forced close)
- HaltFlatten: 7 (system halt forced close)

### Issue: P&L = None
WAL PositionClosed events have `pnl=None` — the Rust engine isn't calculating realized P&L on close. MFE/MAE are recorded correctly. This is a Rust-side issue to fix.

### Symbols Traded
LSE: BP..L, STAN.L, AAL.L, ADM.L(x2), BA..L, BATS.L, BEZ.L, BLND.L, BTRW.L, DCC.L, QQQS.L, 3USL.L, GLEN.L, AUTO.L(x2), BGEO.L, QQQ3.L, AZN.L, BAB.L(x2), BBOX.L(x2), BKG.L, BRBY.L, CCH.L, CPG.L, DPLM.L, EXPN.L, GAW.L
US: GOOG(x2), AMZN
EU: AI, SU

---

## Current System Status
- **Branch**: `feat/tier-system-enhancements-full`
- **Latest Commit**: `596e695`
- **EC2 Disk**: 79%
- **Containers**: 3, all healthy
- **Open Positions**: 0
- **Equity**: £10,000
- **Strategies Active**: VanguardSniper (Momentum), Orchestrator, IBS_MeanReversion, VolExpansion, ORB_Breakout, TypeA-F (all)
- **100-trade gate**: 35/100 complete

## Commits This Session
1. `89ec4a8`: Disable TypeA/D losers + add IBS/ORB/VolExpansion strategies
2. `5f6045d`: Add canonical system plan and progress ledger
3. `596e695`: Keep TypeA/D active for paper validation data collection

## Next Session
1. Verify new strategies (IBS/ORB/VolExpansion) generate signals during market hours
2. Fix P&L tracking in Rust PositionClosed events (pnl=None bug)
3. Continue 100-trade validation gate (65 trades remaining)
4. Archive 250+ stale docs
