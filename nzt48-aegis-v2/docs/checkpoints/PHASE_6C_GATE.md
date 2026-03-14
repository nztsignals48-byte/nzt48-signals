# PHASE 6C GATE — KELLY SIZING + FFI WIRING
## Status: PASS
## Date: 2026-03-09
## Tests: 128 Rust + 83 Python = 211 total

---

### Acceptance Criteria

- [x] `cargo check` passes with ZERO warnings
- [x] `cargo test` passes with ZERO failures (128 passed)
- [x] pytest passes with ZERO failures (83 passed)
- [x] 12-factor Kelly implemented with all factors documented:
  1. Base Kelly from Bayesian WR (`f01_base_kelly`)
  2. Volatility decay 3x: ×9, 5x: ×25 (`f02_vol_decay`)
  3. Moreira-Muir realized vol scaling (`f03_moreira_muir`)
  4. Correlation penalty (`f04_correlation`)
  5. Drawdown scaling (`f05_drawdown`)
  6. Amihud liquidity scaling (`f06_amihud`)
  7. Regime scaling (`f07_regime`)
  8. Spread cost adjustment (`f08_spread`)
  9. Time-of-day scaling (`f09_time_of_day`)
  10. Confidence scaling (`f10_confidence`)
  11. Half-Kelly cap 0.5 (`f11_half_kelly_cap`)
  12. Portfolio heat limit 6% (`f12_portfolio_heat`)
- [x] Kelly determinism: identical inputs → identical output (`TestKellyDeterminism`)
- [x] Kelly cap: high confidence → capped at half-Kelly 0.5 (`TestKellyCap`)
- [x] Kelly clamp: even with cap=0.5, output ≤ 0.20 H57 (`TestKellyClamp`)
- [x] Portfolio heat: 3 positions at 2.1% each → new order rejected >6% (`TestPortfolioHeat`)
- [x] Volatility drag: 3x ETP → variance × 9 in calculation H59 (`TestVolatilityDrag`)
- [x] Bayesian shrinkage: W=60% over 10 trades → adjusted downward H58 (`TestBayesianShrinkage`)
- [x] Outlier win cap: single trade at 5% → capped at 3% for Kelly avg H62 (`TestOutlierWinCap`)
- [x] Fractional shares: always math.floor(), never round() H64 (`TestFractionalShares`)
- [x] Full pipeline end-to-end: tick → channel → universe → risk → broker → exit (`test_full_pipeline_end_to_end`)
- [x] GIL isolation verified: all pipeline components work without Python::with_gil() (`test_gil_isolation_structural`)
- [x] Batch FFI: 200 ticks per batch, not individual ticks (`test_batch_ffi_200_ticks`)
- [x] Backpressure: threshold logic tested with warning/reduce/halt levels (`test_backpressure_thresholds`)

### Files Created/Modified

| File | Lines | Purpose |
|------|-------|---------|
| `python_brain/brain/sizing/kelly_12factor.py` | 191 | 12-factor Kelly with Bayesian shrinkage, vol drag, all 12 factors |
| `python_brain/brain/sizing/__init__.py` | 1 | Package init (Phase 6B) |
| `python_brain/tests/test_kelly.py` | 250 | 23 Kelly acceptance tests |
| `rust_core/src/pipeline_tests.rs` | 289 | 7 pipeline integration tests |
| `rust_core/src/lib.rs` | 35 | Added pipeline_tests module |

### Line Count Compliance

All files under 400-line limit (Rust) / 300-line limit (Python strategies):
- `kelly_12factor.py`: 191 lines (under 300)
- `test_kelly.py`: 250 lines (test file, no limit)
- `pipeline_tests.rs`: 289 lines (under 400)

### Key Design Decisions

1. **Pure function Kelly**: No I/O, no state, no threading — mirrors strategy purity contract
2. **Multiplicative factor chain**: All 12 factors multiply sequentially, making each independently testable
3. **Bayesian Laplace smoothing**: Prior strength = 10 trades, prior WR = 50% — small samples shrunk conservatively
4. **Pipeline tests in Rust only**: Proves GIL isolation — entire tick→exit pipeline runs without Python
5. **Shadow stops**: Exit engine uses internal Rust stops, not native IBKR trailing stops (H67)

### Regression

- FFI round-trip: 28/28 passed (no breakage from pipeline_tests addition)
- Strategy tests: 32/32 passed
- Kelly tests: 23/23 passed
- All prior Rust tests: 128/128 passed
