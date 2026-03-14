# NZT-48 IMPROVEMENTS ONLY AUDIT
> Generated: 2026-02-26
> Scope: Critical blockers preventing reliable signals, PDFs, and continuous intel

---

## 1. CRITICAL MISSING WIRE-UPS

### 1.1 Signal Logger Never Called from main.py
- **File**: `main.py` (no call to `learning.signal_logger`)
- **Impact**: `signal_log.jsonl` is never populated. Outcome resolver has nothing to resolve.
- **Fix**: After `_run_engine_and_write_artifact()` returns, call `get_signal_logger().log_plays()`.

### 1.2 Outcome Resolver Not Scheduled
- **File**: `main.py` scheduler (lines 1938-2236)
- **Impact**: Signals stay `outcome=PENDING` forever. No learning feedback loop.
- **Fix**: Add a 30-minute scheduled job calling `outcomes_engine.resolve_pending()`.

### 1.3 Engine Result Not Fed to War Room State
- **File**: `main.py` — `run_scan()` returns signals but does NOT update `command_center.state`
- **Impact**: War Room shows stale/empty data. No live plays, no drought cockpit.
- **Fix**: After engine runs in PDF jobs and continuous scan, push `EngineResult` to `get_state()`.

---

## 2. SCHEDULER GAPS

### 2.1 No Preview PDF Jobs
- Only scheduled PDF jobs exist (07:00, 13:30, 22:00, 22:30 UK).
- No way to generate "early preview" PDFs on demand.
- **Fix**: Add preview PDF generation methods + on-demand triggers.

### 2.2 Continuous Scan Doesn't Call Signal Engine
- The 60-second continuous scan calls `self.run_scan()` (main.py scan cycle).
- This is the OLD strategy-based pipeline, NOT `SignalEngine.run()`.
- The two pipelines are completely separate and disconnected.
- **Impact**: The institutional signal engine only runs when PDF jobs fire (3x daily).
- **Fix**: Wire the continuous scan to also call `SignalEngine` or unify the pipelines.

---

## 3. ARTIFACT INCONSISTENCIES

### 3.1 Artifacts Directory Doesn't Exist
- `artifacts/` dir does not exist yet. Engine will create it on first run.
- PDFs that read artifacts will fail if run before engine has written any.

### 3.2 Report Output Paths Inconsistent
- PDFs write to `data/reports/` with timestamped filenames.
- Spec requires `reports/YYYY-MM-DD/NZT48_*.pdf` structure.
- **Fix**: Standardize to `reports/YYYY-MM-DD/` path pattern.

### 3.3 Session Status Written to `data/session_status.json`
- Single flat file overwritten per session, not per-artifact-dir.
- Should be written alongside artifacts for auditability.

---

## 4. FAILURE MODES CAUSING "0 SIGNALS"

### 4.1 RVOL Coerced to 1.0 When None
- **File**: `learning/signal_logger.py:64` — `rvol = float(g("rvol", 1.0) or 1.0)`
- Masks zero-volume tickers as NORMAL liquidity.
- **Gate impact**: `gates.py:37` STRICT_MIN_RVOL=0.8 — None rvol would silently pass.

### 4.2 All-Ticker Data Health Failure = Silent 0 Signals
- If yfinance returns empty for all .L tickers (common during off-hours/weekends):
  - Engine returns drought but only logs a warning.
  - No loud alert, no telegram notification, no War Room update.

### 4.3 Session Boundary Gate Kills All Entries Silently
- `main.py:614` — if `session_phase["allow_new_entries"] == False`, scan returns [].
- No logging of WHY. No drought report. Just empty.

### 4.4 Circuit Breaker Silent Block
- `main.py:608` — if circuit breaker blocks entries, returns [].
- No artifact written. No drought report. Just silent 0.

---

## 5. PDF GENERATION GAPS

### 5.1 All PDFs Make Network Calls (yfinance)
- `pdf_v2_momentum.py:33` — `import yfinance as yf`
- `pdf_v2_risk.py:35` — `import yfinance as yf`
- `pdf_v2_daily_review.py:38` — `import yfinance as yf`
- **Violation**: PDF render should be artifact-first, no network calls.
- **Risk**: yfinance rate limit / timeout hangs the PDF job.

### 5.2 No Preview PDF Support
- No methods for PREVIEW_PRE_LSE, PREVIEW_PRE_NYSE, PREVIEW_EOD.
- No `reports/YYYY-MM-DD/NZT48_PREVIEW_*.pdf` output paths.

### 5.3 Mega PDF Very Large (149KB code)
- `mega_report.py` is 150KB of code. Could hang on multi-pass TOC.

---

## 6. EXTENDED INTEL UNIVERSE GAPS

### 6.1 Extended Universe Exists But No Intel Cards
- `uk_isa/isa_universe.py` defines EXTENDED_UNIVERSE (21 tickers).
- No "Intel Card" model exists anywhere.
- No separate intel artifact (`intel.json`).
- No War Room "INTEL FEED" tab/section.

### 6.2 No Context Instruments in Signal Engine
- Config lists context instruments (QQQ, SMH, SPY, SOXX, VIX, TLT, DXY, GLD).
- These are used by strategies but not surfaced as intel items.

---

## 7. SUMMARY: TOP 5 BLOCKERS FOR TOMORROW

| # | Blocker | Impact | Fix Complexity |
|---|---------|--------|----------------|
| 1 | Signal Engine only runs during PDF jobs (3x/day) | 0 signals between PDFs | Medium |
| 2 | Signal Logger never called | No learning, no outcome tracking | Easy |
| 3 | War Room not updated with engine results | Dashboard always empty | Easy |
| 4 | PDFs make network calls (can hang) | PDF generation unreliable | Medium |
| 5 | No preview PDFs / no on-demand generation | No early outputs | Medium |

---

## 8. RECOMMENDED FIX ORDER

1. Wire signal logger to PDF job outputs (Phase 1)
2. Add engine run to continuous scan OR add more frequent engine runs (Phase 1)
3. Implement drought loud-fail with Telegram alert (Phase 1)
4. Add preview PDF methods (Phase 3)
5. Create intel.json artifact + Intel Cards (Phase 2)
6. Add War Room intel feed + drought cockpit updates (Phase 4)
7. Make PDFs artifact-first (refactor network calls out) (Phase 3)
