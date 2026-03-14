# AEGIS 0-01: Outcomes Data Pipeline Diagnosis

**Date:** 2026-03-08
**Status:** DIAGNOSIS COMPLETE
**Verdict:** The "0% WR / 98.5% NULL P&L" claim is a DATA PIPELINE BUG, not a strategy failure.

---

## 1. Data Composition (outcomes.jsonl = 2,327 records)

| Schema | Count | % | Source | Outcome Labels | P&L Field | Resolution |
|--------|-------|---|--------|----------------|-----------|------------|
| NZT-* | 35 | 1.5% | Live outcome engine (`OutcomeEngine.resolve_all_pending()`) | HIT_TARGET, HIT_STOP, TIME_STOP | `pnl_r_gross`, `pnl_r_net` | `resolution_method: PATH_BASED` |
| SIG-BF-* | 1,016 | 43.7% | Backfill script (`scripts/backfill_extended.py`) | TARGET, STOP, TIME_STOP, BREAKEVEN | `r_multiple`, `net_pnl` | No `resolution_method` field |
| SIM-S16-* | 1,276 | 54.8% | S16 prefill data (`source: prefill_s16_us_stocks`) | WIN, LOSS | `pnl_dollars`, `r_multiple` | No `resolution_method` field |

**Key finding:** Three incompatible schemas coexist in the same file. Only 35 records (1.5%) use the canonical `OutcomeRecord` schema that the learning system expects.

---

## 2. Root Cause: Schema Mismatch Cascade

### Problem 1: Outcome label mismatch
The `OutcomeRecord` schema (in `learning/schemas.py:99`) expects outcomes: `HIT_TARGET | HIT_STOP | TIME_STOP`.

- SIG-BF records use: `TARGET`, `STOP`, `BREAKEVEN` (no `HIT_` prefix)
- SIM-S16 records use: `WIN`, `LOSS`

The EdgeLedger (`learning/edge_ledger.py:71`) filters: `if r.outcome not in ("HIT_TARGET", "HIT_STOP", "TIME_STOP"): continue`

**Result:** 2,292 of 2,327 records (98.5%) are silently discarded by every downstream consumer.

### Problem 2: P&L field mismatch
The `OutcomeRecord` dataclass has fields `pnl_r_gross` and `pnl_r_net`. The `from_dict()` method (line 118-122) only takes matching fields:
```python
known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
```

- SIG-BF records have `r_multiple` and `net_pnl` -- these are **ignored** by `from_dict()`, defaulting `pnl_r_gross=0.0` and `pnl_r_net=0.0`.
- SIM-S16 records have `pnl_dollars` and `r_multiple` -- also ignored, defaulting to 0.0.

**Result:** Even if the outcome filter passed, P&L values would read as 0.0 for 98.5% of records.

### Problem 3: No `resolution_method` field
Only the 35 NZT-* records have `resolution_method: PATH_BASED`. The other 2,292 records have no `resolution_method` field, but the OutcomeRecord defaults it to `"PATH_BASED"` in the schema, masking the difference.

---

## 3. S15 Specific Diagnosis

### S15 in outcomes.jsonl
- **918 records** tagged `strategy_tag: S15`, all from the SIG-BF backfill schema
- **0 records** from the live outcome engine (no NZT-* records with S15)
- Backfill WR: 185 TARGET / 918 total = **20.2% raw WR** (not 0%)
- But the edge_ledger sees **0 wins** because it filters for `HIT_TARGET` and these records say `TARGET`

### S15 in signal_log.jsonl
- **0 records** with strategy_tag S15
- signal_log.jsonl has only 37 entries total, all `TREND_MOMENTUM`, all `RESOLVED`
- This means S15 signals have **never been logged** to the signal pipeline in production

### S15 execution pipeline
The S15 priority path (`main.py:3941-4470`) generates signals and passes them to `virtual_trader.open_position()` via the TickLoop (`command_center/tick_loop.py:787`). However:

1. `signal_logger.log_plays()` is called at line 2181-2188 for ALL `raw_signals` before the S15/non-S15 split
2. S15 signals reach the signal_logger, but `signal_log.jsonl` has 0 S15 entries, suggesting either:
   - S15 strategy never generated signals in the live engine, OR
   - The signal_log was rotated/cleared since last run

---

## 4. Pipeline Architecture (Two Disconnected Paths)

```
PATH A: Virtual Trader (execution tracking)
  Strategy.scan() -> raw_signals -> S15 priority path -> virtual_trader.open_position()
  -> VirtualPosition (in-memory + SQLite) -> close_position() -> VirtualTrade
  -> _on_trade_closed() callback -> LearningEngine, Sheets, Telegram, B-Team
  DESTINATION: SQLite virtual_trades table + Google Sheets + Telegram
  NOTE: Does NOT write to outcomes.jsonl

PATH B: Outcome Engine (signal resolution)
  Strategy.scan() -> raw_signals -> signal_logger.log_plays() -> signal_log.jsonl
  -> OutcomeEngine.resolve_all_pending() -> fetch 1m bars -> path-based resolution
  -> outcomes.jsonl (with OutcomeRecord schema)
  DESTINATION: outcomes.jsonl -> edge_ledger, drift_detector, meta_learner

PATH C: Backfill Scripts (historical seeding)
  scripts/backfill_extended.py -> yfinance historical bars -> simulated trades
  -> outcomes.jsonl (with SIG-BF schema, INCOMPATIBLE with OutcomeRecord)
  -> learning_engine.record_trade() (direct, bypasses outcome engine)
```

**Critical gap:** Path A (virtual trader) and Path B (outcome engine) are completely disconnected. Virtual trades do NOT flow into outcomes.jsonl. The `_on_trade_closed()` callback feeds the LearningEngine directly but never writes OutcomeRecords.

---

## 5. The "52 Paper Trades" Mystery

The AEGIS plan references "52 paper trades" for S15. These are likely in the SQLite `virtual_trades` table (Path A), not in outcomes.jsonl (Path B). The "0% WR" claim probably came from querying outcomes.jsonl (which has 0 NZT-schema S15 records) rather than the SQLite database where actual virtual trades are stored.

---

## 6. Summary of Bugs Found

| # | Bug | Severity | Impact |
|---|-----|----------|--------|
| B1 | SIG-BF outcome labels (TARGET/STOP) incompatible with OutcomeRecord (HIT_TARGET/HIT_STOP) | **P0** | 1,016 records invisible to edge_ledger, drift, meta_learner |
| B2 | SIM-S16 outcome labels (WIN/LOSS) incompatible with OutcomeRecord | **P0** | 1,276 records invisible to all learning systems |
| B3 | SIG-BF P&L field names (r_multiple, net_pnl) don't match OutcomeRecord (pnl_r_gross, pnl_r_net) | **P0** | Even if B1 fixed, P&L reads as 0.0 |
| B4 | SIM-S16 P&L field names (pnl_dollars, r_multiple) don't match OutcomeRecord | **P0** | Same as B3 |
| B5 | VirtualTrader closed trades never written to outcomes.jsonl | **P0** | Live execution results never reach the learning feedback loop |
| B6 | outcomes_index.json does not exist | **P1** | OutcomeEngine dedup check (`load_index()`) returns empty dict every time |
| B7 | signal_log.jsonl has only 37 entries, all RESOLVED | **P1** | OutcomeEngine.load_pending() always returns empty list, so resolve_all_pending() is a no-op |

---

## 7. Recommended Fixes (for subsequent implementation tickets)

1. **Normalize outcomes.jsonl** -- Write a migration script to normalize all 2,327 records into the canonical OutcomeRecord schema:
   - `TARGET` -> `HIT_TARGET`, `STOP` -> `HIT_STOP`, `WIN` -> `HIT_TARGET`, `LOSS` -> `HIT_STOP`, `BREAKEVEN` -> `TIME_STOP`
   - Map `r_multiple` -> `pnl_r_gross` + `pnl_r_net`, `pnl_dollars` -> compute `pnl_r_gross`
   - Add `resolution_method: BACKFILL` for SIG-BF, `resolution_method: PREFILL_SIM` for SIM-S16

2. **Bridge VirtualTrader -> outcomes.jsonl** -- In `_on_trade_closed()`, write an OutcomeRecord to outcomes.jsonl so live executions feed the learning loop.

3. **Fix backfill_extended.py** -- Make `build_outcomes()` write records in OutcomeRecord schema with correct field names and outcome labels.

4. **Rebuild signal_log.jsonl** -- Either backfill from virtual_trades SQLite or ensure signal_logger is capturing S15 signals going forward.

5. **Rebuild downstream indices** -- After normalization: `edge_ledger.rebuild()`, `drift_detector.rebuild()`, `meta_learner.rebuild()`.

---

## 8. Answer to the Core Question

> Does S15's "0% WR" mean (a) trades executed and lost, or (b) trades never executed?

**Answer: Neither (a) nor (b) in the way assumed.**

- S15 trades were likely executed via VirtualTrader (Path A) and tracked in SQLite
- S15 trades were NEVER written to outcomes.jsonl (Path B) by the live engine
- The 918 "S15" records in outcomes.jsonl are ALL from the backfill script, using incompatible schema
- The edge_ledger reads 0 wins for S15 because backfill uses `TARGET` not `HIT_TARGET`
- The "0% WR" is a **schema mismatch artifact**, not a real strategy result

**The actual S15 win rate from backfill data is ~20.2%** (185 TARGET / 918 total), which is still low but not 0%. The real live performance can only be determined by querying the SQLite `virtual_trades` table.
