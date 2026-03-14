# NZT-48 Paper Launch Audit — 2026-02-26
## Institutional Go-Live Pre-Flight Inspection

---

## A) Data Integrity & Reliability

### Current State
- **DataHealthGate** (`uk_isa/data_health.py`): validates OHLCV for each ticker before engine run
- **PRICE_SCALE gate** (`signal_engine/gates.py`): detects pence-vs-pounds miscoding for .L tickers
- **Adaptive windowing**: SHORT_WINDOW mode (7-13 bars) with reliability_penalty (0.05 per missing bar)
- **yfinance** is primary data source; no secondary validation source wired
- **RVOL**: correctly returns None (mapped to N/A) when volume data is unreliable

### Gaps / Risks
- **CRITICAL**: No second data source cross-validation — single yfinance dependency
- **HIGH**: No staleness detection — if yfinance returns cached/stale data, system won't know
- **HIGH**: Volume data for .L tickers from yfinance often unreliable or zero; RVOL could be misleading
- **MED**: No atomic data snapshot — each ticker fetched independently, slight clock skew possible
- **MED**: No data freshness timestamp comparison (data age vs wall clock)

### Fixes Implemented
- SystemState watchdog detects stale data (>5 min since last successful fetch)
- RVOL forced to N/A when volume is zero or unreliable (already in engine.py)
- DataReliabilityScore 0-1 added to quality artifacts
- Staleness check in tick loop

### Acceptance Tests
- `test_signal_guarantee.py` passes with data health gate enforced
- RVOL shows N/A (not 0.00) for tickers with zero volume
- system_state.json shows data_freshness_seconds

---

## B) Signal Engine Correctness & Consistency

### Current State
- **Two-layer architecture**: STRICT mode → 4-step FALLBACK → SignalDroughtReport
- **9 gates** in funnel: 4 HARD (DATA_HEALTH, PRICE_SCALE, MIN_BARS, TRADABILITY), 5 SOFT
- **Stop/target logic**: setup-type-specific ATR fractions (continuation: 0.40x, breakout: 0.35x, default: 0.50x)
- **Net R:R**: computed after spread + slippage deduction
- **Strategy Router**: enriches plays with strategy context, weight, allocation
- **RiskOfficer**: post-router governance (APPROVE/DOWNSIZE/VETO)

### Gaps / Risks
- **HIGH**: No contradiction check — a signal could theoretically pass RISK_OFF regime with LONG if gates are relaxed via fallback
- **HIGH**: RVOL=0.00 could slip through if the N/A check fails at scoring layer
- **MED**: regime_confidence is hardcoded to 0.65 default in engine.run() — not always derived from data
- **MED**: Closest-misses table only partially implemented in drought report

### Fixes Implemented
- QualityGate module added: blocks NO-GO+TRADE contradictions, RVOL=0.00 placeholder errors
- regime_confidence checked against threshold before WIN_RATE mode decisions
- Drought report enhanced with closest_misses and recommended_knobs

### Acceptance Tests
- No TRADE signal should have RVOL exactly 0.00 (must be N/A or actual value)
- No LONG TRADE in RISK_OFF regime
- Drought report contains closest_misses when no signals generated

---

## C) Execution Realism & Paper Fill Simulation

### Current State
- **ExecutionPlan** generated per signal card (order_type, spread_proxy, slippage, cancel_conditions)
- **VirtualTrader** exists for paper position tracking
- **Spread proxies** hardcoded per ticker in `_SPREAD_BPS` dict
- **Net-of-cost R:R** computed in `TickerFeatures.compute_levels()`

### Gaps / Risks
- **HIGH**: No fill probability model — all trades assumed 100% fill
- **HIGH**: No time-to-fill simulation — instant fills unrealistic for .L tickers
- **MED**: Spread proxies are static — real spreads vary by time of day and volume
- **MED**: No slippage scaling with position size

### Fixes Implemented
- Paper fill simulation added: fill_probability, slippage_estimate, time_to_fill_seconds
- ExecutionPlan required for TRADE — enforced in QualityGate
- Spread gating logic (PASS/WATCH/VETO) already exists

### Acceptance Tests
- Every TRADE signal has non-empty execution_plan
- fill_probability < 1.0 for low-volume tickers
- No TRADE with spread_gate_result = "VETO"

---

## D) Risk Governance & Constitution Enforcement

### Current State
- **17 Immutable Rules** defined in settings.yaml (risk_per_trade 0.75%, max_daily_loss 3%, etc.)
- **RiskOfficer** with 6 rules: VolShock, Liquidity, Correlation, Drawdown, EventWindow, DataReliability
- **KillSwitch** with 3 methods: Telegram /kill ALL, file-based, SIGTERM/SIGINT
- **EmotionalFirewall**: 12 blocked patterns (revenge trading, overtrading, FOMO, etc.)
- **CircuitBreakerSystem** exists
- **SessionProtection** and **DrawdownRecovery** configured

### Gaps / Risks
- **HIGH**: Constitution enforcement not centralized — spread across multiple modules
- **HIGH**: Max consecutive losses check exists in config but enforcement path unclear in paper mode
- **MED**: No kill switch test — never verified in paper mode
- **MED**: Portfolio heat max (3%) not enforced at engine level — only displayed in War Room

### Fixes Implemented
- Centralized constitution enforcement in QualityGate
- Kill switch verified operational (file-based + in-memory)
- Max concurrent positions enforced
- risk_governance.json artifact written per session

### Acceptance Tests
- Kill switch file creation halts all signals
- Max 3 concurrent positions enforced
- Daily loss > 3% triggers halt

---

## E) Monitoring/Alerting & System Health

### Current State
- **CommandCenterState**: singleton with panels for market, data_health, gate_funnel, portfolio, strategies
- **tick_count** and **last_tick** tracked
- **session_status.json** updated per pipeline run
- **WebSocket push** from FastAPI server every tick

### Gaps / Risks
- **CRITICAL**: No watchdog — if tick loop stalls, no alert is raised
- **HIGH**: No SystemState (OK/DEGRADED/HALTED) — system can be broken without operators knowing
- **HIGH**: No staleness detection on tick_count
- **MED**: No SLA monitoring (e.g., "PDF not generated within 10 min of scheduled time")

### Fixes Implemented
- SystemState machine: OK → DEGRADED → HALTED with reason codes
- Watchdog: detects tick stall (>120s), data staleness, memory growth
- system_state.json artifact written each tick
- OK/DEGRADED/HALTED banner in artifacts

### Acceptance Tests
- system_state.json exists and shows current state
- Watchdog would transition to DEGRADED after 120s of no ticks
- State transitions are logged

---

## F) Change Management & Reproducibility

### Current State
- **run_id** (UUID-based) generated per pipeline run
- **Artifacts** written with timestamps and session tags
- **Atomic writes** (tmp → rename) for all artifact JSON files
- **settings.yaml** version = "10.0"

### Gaps / Risks
- **MED**: No git hash stamped in artifacts — can't tie output to exact code version
- **MED**: No config hash — can't verify settings haven't changed between runs
- **LOW**: No rollback plan documented

### Fixes Implemented
- Run stamping: git hash + config hash in system_state.json
- readiness.json artifact captures full config fingerprint

### Acceptance Tests
- system_state.json includes git_hash and config_hash fields
- readiness.json present in artifacts

---

## G) War Room UX & Operator Workflow

### Current State
- **FastAPI** with HTML War Room, REST API, WebSocket push
- **5 tabs** in war room: plays, tape, health, funnel, strategies
- **Halt toggle** via POST /api/halt
- **Telegram commands**: /taken, /skipped, /positions, /close, /stats, /today, /pause, /kill, /bots, /overseer

### Gaps / Risks
- **MED**: No glossary of terms for operators
- **MED**: No one-screen dashboard summary
- **LOW**: No guided actions ("what should I do next?")

### Fixes Implemented
- Glossary included in IMPROVEMENTS_110_PERCENT.md (30+ terms)
- SystemState banner visible in API response

### Acceptance Tests
- /api/state returns all panels including system_state
- Glossary has 30+ entries

---

## H) Reporting Pipeline (Artifacts → PDFs → Telegram)

### Current State
- **3 PDF types**: Momentum (PDF1), Risk (PDF2), Daily Review (PDF3)
- **pipeline_runner.py**: unified pipeline (engine → artifacts → signal log → intel → drought alert)
- **Atomic artifact writes** with tmp → rename pattern
- **Telegram send_document()** method exists
- **Scheduled times**: 07:00/13:30/22:00 UK configured in settings.yaml
- **Preview PDF generation** functional

### Gaps / Risks
- **HIGH**: No scheduled PDF job wiring in main.py tick loop — PDFs only generated on-demand
- **HIGH**: No post-PDF Telegram send automation — manual only
- **MED**: telegram_delivery.json not written
- **MED**: PDF captions don't include SystemState or signal counts

### Fixes Implemented
- Scheduled PDF job runner added
- Telegram delivery after each PDF with rich caption
- telegram_delivery.json artifact written
- Preview PDFs labelled "PREVIEW"

### Acceptance Tests
- PDFs generated at scheduled times (or on-demand for today)
- telegram_delivery.json exists with delivery status
- PDF captions include date, session, SystemState, TRADE/WATCH/INTEL counts

---

## I) Performance/Latency & "Full Horsepower" Stability

### Current State
- **Tick loop**: 30s active / 120s inactive intervals
- **yfinance batch download** per ticker (sequential)
- **CORE + PEER + FULL_SCAN** tiered pipeline
- **Compute time tracking** in TieredPipelineResult

### Gaps / Risks
- **HIGH**: Full scan can block CORE processing if slow
- **HIGH**: No timeout on yfinance downloads — single slow ticker blocks entire tick
- **MED**: No memory monitoring — potential for leak over hours
- **MED**: No bounded scan guarantee (full_scan could take >15 min)

### Fixes Implemented
- CORE scan runs first, PEER/FULL_SCAN bounded and non-blocking
- Timeout on data fetches (30s per ticker)
- Memory monitoring in watchdog
- Full scan bounded to 10-15 min max

### Acceptance Tests
- CORE scan completes in <30s
- PEER scan completes in <120s
- FULL_SCAN completes in <15 min
- No memory growth >50MB over 1 hour

---

## J) Security/Secrets/Operational Safety

### Current State
- **API keys** in .env file (not in code)
- **.env.production** for EC2 deployment
- **.dockerignore** excludes .env files
- **Paper mode** enforced via NZT48_MODE=PAPER env var and config

### Gaps / Risks
- **HIGH**: API keys visible in .env file on disk — should be env-only on production
- **MED**: No redaction in log output — API keys could appear in error traces
- **MED**: No secrets detection in artifacts
- **LOW**: No rate-limit backoff for API calls

### Fixes Implemented
- Paper mode double-checked at startup
- No secrets written to artifacts
- Log redaction for known API key patterns

### Acceptance Tests
- grep for API keys in artifacts returns 0 hits
- NZT48_MODE=PAPER confirmed in startup log
- .env not included in Docker image
