# NZT-48 — 110% Improvements Plan
## Beyond Requirements — Implemented + Deferred

---

## A) Data: Truth + Validation

### IMPLEMENTED TODAY
- **DataReliabilityScore (0-1)**: aggregate metric across pass rate, RVOL availability, and reliability penalty. Written to `reliability.json` every session.
- **Staleness detection**: SystemWatchdog checks last data fetch age, transitions to DEGRADED if >5 min stale.
- **RVOL N/A enforcement**: QualityGate catches any RVOL=0.00 placeholder errors, flags as violation.

### DEFERRED (Safe to implement later)
- Secondary data source (Alpha Vantage, TwelveData) cross-validation for price verification
- Unit scaling guards for GBX→GBP conversion (partial — PRICE_SCALE gate exists)
- Vendor disagreement metrics (when multiple sources available)

---

## B) Engine: Regime Unification + Strategy Router

### IMPLEMENTED TODAY
- **QualityGate module**: blocks NO-GO+TRADE contradictions (LONG in RISK_OFF blocked)
- **Regime contradiction check**: run on every signal batch before output
- **ExecutionPlan required for TRADE**: enforced in quality layer

### DEFERRED
- Unified regime classifier across all modules (currently tick_loop and engine have separate logic)
- Strategy router tightening (consistent stop/target policy per strategy tag)
- Consistent regime_confidence derivation from actual data (not hardcoded 0.65)

---

## C) Execution: Paper Fill Simulation

### IMPLEMENTED TODAY
- **ExecutionPlan per signal**: includes order_type, spread_proxy_bps, max_slippage_bps, cancel_conditions
- **Spread gating**: PASS/WATCH/VETO based on spread_proxy_bps thresholds
- **Net-of-cost R:R**: computed in TickerFeatures.compute_levels() after spread + slippage

### DEFERRED
- Time-of-day liquidity model (spreads wider at open/close)
- Fill-quality scoring (track actual fill vs. estimated)
- Position-size-based slippage scaling

---

## D) Risk: Constitution + Kill Switch

### IMPLEMENTED TODAY
- **SystemWatchdog constitution checks**: max daily loss (3%), max consecutive losses (5), max positions (3)
- **Kill switch verified**: 3 methods all functional (Telegram, file, process signal)
- **Risk governance artifacts**: system_state.json, risk_officer.json written per session
- **Halted state enforcement**: SystemState.HALTED blocks all new signals

### DEFERRED
- Correlation regime detection (cross-asset stress detection)
- Stress-mode throttles (automatic position reduction in stress)
- Incident drills (simulated loss scenarios)

---

## E) Monitoring: OK/DEGRADED/HALTED Banner

### IMPLEMENTED TODAY
- **SystemState machine**: OK → DEGRADED → HALTED with reason codes
- **system_state.json**: written per session with all diagnostics
- **Watchdog checks**: tick staleness, data freshness, memory, constitution violations
- **readiness.json**: comprehensive readiness assessment (paper_launch_ready boolean)

### DEFERRED
- Real-time alerting on SLA drift (PDF not generated within window)
- Grafana/Prometheus metrics export
- Signal drought trending (is drought getting worse?)

---

## F) Change Management: Run Stamping

### IMPLEMENTED TODAY
- **Git hash**: captured in system_state.json
- **Config hash**: SHA-256 of settings.yaml captured in system_state.json
- **run_id**: UUID per pipeline run
- **Atomic artifact writes**: tmp → rename pattern for all JSON files
- **readiness.json**: captures full config fingerprint

### DEFERRED
- Immutable artifact archive (S3/GCS backup)
- Rollback plan documentation
- Model version stamping (when AI models are used)

---

## G) UX: Operating Mode + Glossary

### IMPLEMENTED TODAY
- **SystemState banner**: visible in /api/state response
- **Rich Telegram captions**: date, session, SystemState, TRADE/WATCH/INTEL counts

### Glossary (30+ Terms)

| Term | Definition |
|------|-----------|
| STRICT | Signal that passes all gates at institutional thresholds |
| FALLBACK | Signal admitted via relaxed soft gates, clearly labelled |
| TRADE | Signal eligible for execution (paper mode) |
| WATCH | Signal for monitoring only, not execution |
| INTEL | Full-scan discovery, informational only |
| CORE | Primary ISA universe (12 tickers), TRADE eligible |
| PEER | Correlated instruments, WATCH only |
| FULL_SCAN | Extended universe, INTEL classification |
| DROUGHT | No signals generated despite active session |
| RVOL | Relative Volume — current vs. average; N/A when unreliable |
| ATR | Average True Range — price movement magnitude |
| ATR% | ATR as percentage of price — tradability measure |
| R:R | Reward-to-risk ratio net of costs |
| HARD GATE | Must pass, never relaxed (DATA_HEALTH, PRICE_SCALE, MIN_BARS, TRADABILITY) |
| SOFT GATE | Score-based, relaxed stepwise in fallback (RVOL, R:R, MOMENTUM, REGIME_FIT, FACTOR_CAP) |
| DataHealthGate | Validates OHLCV integrity per ticker |
| PRICE_SCALE | Detects pence-vs-pounds miscoding for .L tickers |
| SHORT_WINDOW | 7-13 bars available — indicators computed with adaptive window, penalized |
| RiskOfficer | Post-router governance (APPROVE/DOWNSIZE/VETO) |
| APPROVE | Signal passes all risk rules |
| DOWNSIZE | Signal admitted but position sized reduced |
| VETO | Signal blocked by risk officer |
| KillSwitch | 3-method emergency halt (Telegram, file, process signal) |
| SystemState | OK / DEGRADED / HALTED — current system health |
| regime | Market classification (RISK_ON, NEUTRAL, RISK_OFF, etc.) |
| factor_group | Cluster of correlated instruments (semiconductor, index_leverage, etc.) |
| setup_type | Signal classification: continuation, breakout, mean_revert, default |
| ETP | Exchange-Traded Product (leveraged instruments) |
| ISA | Individual Savings Account (UK tax-free wrapper) |
| Paper mode | Simulated trading — no real orders |
| Compounding target | 2% daily × 252 days = £10K → £1,485,757 |
| ExecutionPlan | Order details: type, spread, slippage, cancel conditions |
| DataReliabilityScore | 0-1 aggregate data quality metric |
| QualityGate | Post-engine checks blocking contradictions |
| Readiness | Combined assessment: SystemState + reliability + quality = paper_launch_ready |

---

## H) Reports: Closest Misses + Evidence Stamps

### IMPLEMENTED TODAY
- **Closest-misses**: in DroughtPackage via signal_card.build_drought_package()
- **telegram_delivery.json**: tracks each PDF send with status, caption, timestamps
- **Evidence stamps**: every artifact includes generated_at timestamp and run_id

### DEFERRED
- Learning scan ("plays we could've made") in EOD report
- Win/loss attribution by gate that would have changed outcome
- Per-PDF evidence footer with artifact hashes

---

## I) Performance: Timeouts + Caching + Bounded Scans

### IMPLEMENTED TODAY
- **CORE scan priority**: always runs first, PEER/FULL_SCAN after
- **Compute time tracking**: per-tier millisecond counters in TieredPipelineResult
- **Bounded scans**: full_scan processes only INTEL_UNIVERSE list (not infinite)

### DEFERRED
- Per-ticker data fetch timeout (30s limit per yfinance call)
- Data caching layer (avoid re-fetching same ticker within 5 min)
- Async parallel data fetching (currently sequential)

---

## J) Security: Env Vars + Redaction + Artifacts

### IMPLEMENTED TODAY
- **Paper mode verified**: NZT48_MODE=PAPER checked at startup
- **No secrets in artifacts**: API keys never written to JSON files
- **.env excluded from Docker**: via .dockerignore

### DEFERRED
- Log redaction for API key patterns
- Secrets scanning in CI/CD
- Per-session audit trail with operator identity

---

## Summary

| Category | Implemented | Deferred |
|----------|-----------|----------|
| A) Data | 3 | 3 |
| B) Engine | 3 | 3 |
| C) Execution | 3 | 3 |
| D) Risk | 4 | 3 |
| E) Monitoring | 4 | 3 |
| F) Change Mgmt | 5 | 3 |
| G) UX | 3 (+ 35-term glossary) | 0 |
| H) Reports | 3 | 3 |
| I) Performance | 3 | 3 |
| J) Security | 3 | 3 |
| **TOTAL** | **34 implemented** | **27 deferred** |
