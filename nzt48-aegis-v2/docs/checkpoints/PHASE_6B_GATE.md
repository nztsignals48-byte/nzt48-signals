# PHASE 6B GATE — QUANTUM BRAIN: PYTHON STRATEGIES
# Status: PENDING REVIEW

---

## Acceptance Criteria Checklist

- [x] pytest passes with ZERO failures (60/60)
- [x] Vanguard Sniper: feed identical tick batches twice → identical OrderIntent output (deterministic)
- [x] Apex Scout: feed identical snapshots twice → identical OrderIntent output (deterministic)
- [x] Empty tick list → returns None (no crash, no error)
- [x] Single tick → valid processing (no crash)
- [x] Confidence floor: signal with confidence < 65 → filtered (returns None)
- [x] Moreira-Muir: higher realized volatility → smaller position size (inverse scaling)
- [x] Pure function verification:
  - [x] No imports of ib_insync, ibapi, or any broker library
  - [x] No global variables (AST-verified: only imports, function defs, docstrings)
  - [x] No file I/O, no network I/O, no database queries
  - [x] No state mutation outside of local function scope
  - [x] No asyncio, no threading, no concurrent.futures (H07)
- [x] No .apply() or iterrows() in any Pandas code (H60) — AST-verified
- [x] Zero-division guards: np.where(denom == 0, 1e-9, denom) on ALL divisions (H61)
- [x] All logging via callback (simulating PyO3 channel to Rust, H08)
- [x] Error masking ban: no `except Exception as e: pass` anywhere (H108)
- [x] No magic numbers: all constants reference brain.config (H109)
- [x] Correlation on log returns, not raw prices (H63)

## File Line Counts

```
 36 python_brain/brain/config.py
202 python_brain/brain/strategies/vanguard_sniper.py
126 python_brain/brain/strategies/apex_scout.py
393 python_brain/tests/test_strategies.py
  2 python_brain/brain/__init__.py
  2 python_brain/brain/strategies/__init__.py
  2 python_brain/brain/sizing/__init__.py
  2 python_brain/brain/tests/__init__.py
  2 python_brain/tests/__init__.py
```

## Test Output (60/60 Python)

```
python_brain/tests/test_ffi_roundtrip.py ... 28 passed
python_brain/tests/test_strategies.py ... 32 passed
60 passed in 0.08s
```

## Rust Regression: 121/121 passed (unchanged)

## Architecture Summary

### New Modules (Phase 6B)
- `brain/config.py` (36 lines) — All shared constants (H109)
- `brain/strategies/vanguard_sniper.py` (202 lines) — Momentum strategy, pure function
- `brain/strategies/apex_scout.py` (126 lines) — RVOL anomaly scanner, pure function
- `tests/test_strategies.py` (393 lines) — 32 acceptance tests

### Vanguard Sniper (Top 300 Ultra-Liquid)
- **Inputs**: Batched ticks (chronological, max 500 bars)
- **Signals**: ADX(14) momentum + EMA(20) trend + Volume breakout
- **Sizing**: Moreira-Muir (2017) inverse vol scaling
- **Output**: `{confidence, kelly_fraction, features}` or None
- **Deterministic**: Identical inputs → identical outputs

### Apex Scout (Remaining 700, 60s Snapshots)
- **Inputs**: 60-second OHLCV snapshots (chronological)
- **Signals**: RVOL anomaly detection (threshold 2.0x) + momentum
- **Sizing**: Moreira-Muir inverse vol scaling
- **Output**: `{confidence, kelly_fraction, features}` or None
- **Deterministic**: Identical inputs → identical outputs

### Pure Function Constraints (Verified by AST Analysis)
- No broker imports (ib_insync, ibapi)
- No global mutable state
- No file/network/database I/O
- No threading/asyncio/multiprocessing (H07)
- No .apply() or .iterrows() (H60)
- Zero-division guards via np.where (H61)
- Log returns for correlation (H63)
- Logging via callback, not file I/O (H08)
- All constants from config module (H109)

---

**PHASE 6B COMPLETE — AWAITING HUMAN REVIEW**
