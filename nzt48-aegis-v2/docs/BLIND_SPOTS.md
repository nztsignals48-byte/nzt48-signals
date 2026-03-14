# BLIND SPOTS — Known Unknowns (H107)
# Created: Phase 1
# Review: Phase 8 (when connecting to real IBKR Gateway)

---

## 1. IBKR TWS API reqMktData Tick Frequency on Paper Account

**Uncertainty:** The spec assumes ~5 ticks/second per ticker from IBKR paper
mode. Real paper accounts may deliver delayed snapshots (Type 3 data) at lower
frequency. The crossbeam channel sizing (50,000 capacity) and tick-drop logic
may need adjustment based on actual paper data rates.

**Risk:** If paper sends fewer ticks, Yang-Zhang volatility estimates will be
noisier with shorter rolling windows. If it sends more, channel overflow
thresholds may need tuning.

**Resolution plan:** Measure actual tick rates per ticker in first 24h of
Phase 8 paper connection. Adjust channel thresholds if needed.

---

## 2. PyO3 Memory Model with Long-Running GIL Thread

**Uncertainty:** The GIL Thread acquires the GIL once per batch (200 ticks /
10ms), calls Python, then releases. Over 8 hours of continuous trading, this
is ~48,000 GIL acquisitions. PyO3's memory management (reference counting
across the FFI boundary) under sustained load is less tested than short-lived
scripts.

**Risk:** Potential for slow memory growth if Python objects created during
batch processing aren't freed promptly. The `Py<T>` reference counting model
in PyO3 0.24 requires explicit attention to prevent leaks.

**Resolution plan:** Phase 7 memory stability test (1,000,000 ticks through
pipeline, assert memory flat). If leak detected, investigate PyO3 `.into_py()`
vs `.to_object()` patterns and ensure all `Bound<'py, T>` references are
properly scoped.

---

## 3. IBKR Dynamic Subscription Rotation Timing

**Uncertainty:** The dynamic rotation model (50 permanent + 50 rotating lines,
60-second rotation cycle) assumes reqMktData subscription changes are instant.
In practice, IBKR may impose pacing penalties beyond the documented 10ms
spacing (H42). Code 321 (pacing violation) may trigger more frequently than
expected during rotation cycles.

**Risk:** If rotation takes longer than expected, the full Vanguard warm scan
(5 batches x 60s = 5 min) or Apex scan (14 batches x 60s = 15 min) could
drift, leaving gaps in coverage. Tickers with open positions that get rotated
out could miss exit signals.

**Resolution plan:** Phase 8 will measure actual rotation latency. The
invariant "open positions are ALWAYS Tier 1" protects against the worst case.
If pacing is an issue, reduce rotation frequency or batch size.
