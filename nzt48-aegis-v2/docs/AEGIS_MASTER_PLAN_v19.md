# AEGIS V2 — MASTER PLAN v19
### AEGIS Adaptive Global Execution & Intelligence System
**Version**: 19.0 | **Date**: 2026-03-09 | **Status**: APPROVED — IMPLEMENTATION READY

> This document is the canonical master plan. It supersedes v18. It incorporates all v18 content plus 6 hidden implementation trap fixes identified by Gemini's final adversarial review. Fixes are marked **[v19-FIX-N]** for traceability.

---

## v19 DELTA — 6 IMPLEMENTATION TRAP FIXES

These are the specific changes from v18 to v19. Each fix addresses a hidden trap that would have caused a real implementation failure.

| Fix | Trap | What was wrong in v18 | What v19 does |
|-----|------|-----------------------|---------------|
| **v19-FIX-1** | Dust guard ambiguity | SC-06 said "partial fill remainder < £500" — ambiguous whether "remainder" = unfilled or filled portion | SC-06 now explicitly checks the **FILLED portion**: if `filled_gbp < 500.0` → immediately market-sell the filled portion to liquidate dust; the unfilled portion is cancelled separately |
| **v19-FIX-2** | Symbology translation nightmare | No IBKR→Polygon symbol mapping existed; IBKR uses `BRK B` (space), Polygon uses `BRK.B` (dot); LSE tickers IBKR:`NVD3.L` vs Polygon:`LSE:NVD3` | New `symbology_mapper.py` added as SC-12; maps IBKR canonical format → Polygon format for all data_fetch.py calls |
| **v19-FIX-3** | Crossbeam buffer too small | SC-09 capacity=1,000. US open generates ~10,000 ticks/sec across 100 subscribed lines. Buffer drains in 100ms. Overflow → data loss. | SC-09 capacity: 1,000 → **50,000**. 50,000 ÷ 10,000 ticks/sec = 5 seconds of burst buffer. Overflow still drops oldest tick + logs counter. |
| **v19-FIX-4** | Telegram webhook DevSecOps overhead | Phase 17 mandated webhook requiring SSL cert + domain + NGINX reverse proxy. Adds 8h DevSecOps work, introduces cert expiry failure mode. Achieves no meaningful latency gain over async polling. | Phase 17: **async long-polling** on a dedicated Python thread. `python-telegram-bot` `application.run_polling()` on thread. HALT latency < 100ms natively. Zero infra overhead. |
| **v19-FIX-5** | Router's data paradox | SmartRouter compares ETP vs direct cost but direct equity spread needs a real-time quote. Fetching a streaming line just for cost comparison would burn the line budget. | SmartRouter uses `reqMktData` with `snapshot=True` (IBKR one-shot snapshot mode). **Does NOT open a persistent streaming subscription.** Consumes 0 of the 100 IBKR market data lines. Snapshot fetched once at signal-fire time. |
| **v19-FIX-6** | Corporate action API limitation | Phase 12 used `reqContractDetails` for corporate action calendar. IBKR only populates this 24-48h before ex-date for declared dividends. Spin-offs, mergers, special dividends often NOT in IBKR calendar. ISA voidance risk still present. | Corporate action blocklist moved to Ouroboros pipeline (Step 2a). `data_fetch.py` fetches corporate action calendar from Polygon.io (`/v3/reference/dividends`, `/v3/reference/splits`) and yfinance `actions` as fallback. Writes `calibration/corp_action_blocklist.json` nightly. RiskGate reads this file on load. `reqContractDetails` demoted to secondary confirmation only. |

---

## PART 1 — SYSTEM AUDIT

### 1.1 V1 Python Codebase — Full Status

| Component | Status | Critical Issues |
|-----------|--------|----------------|
| **S15 daily_target.py** | ACTIVE | T-01→T-08 timing tuning applied; 0% win rate on 52 paper trades — root cause is execution timing, not signal quality |
| **S3 mean_reversion.py** | DORMANT | Hard ETP veto (Avellaneda 2010) is correct code; V2.1 reactivation comment contradicts it — remove comment |
| **chandelier_exit.py** | ACTIVE | VirtualTrader inline ladders disabled; Le Beau 5-rung working; Redis-persisted (7-day TTL) |
| **cross_asset_macro.py** | ACTIVE | C-06 fixed: weekly HMM refit + 3-tick confirmation buffer; VIX 5-min cache (not 30-min) |
| **ml_meta_model.py** | DISABLED | AEGIS 0-05: regime encoding always -1; confidence leaked as input feature (circular feedback); 43.7% training data fabricated |
| **uk_isa/ (15 files)** | ACTIVE | 12 leveraged ETPs active; lse_registry auto-scraper; correlation_engine; predictive_scoring |
| **sprint6_live_gate.py** | NOT MET | 0% WR / 52 paper trades; all 10 Romano-Wolf criteria fail; need 63+ MTRL days |
| **state_manager.py** | ACTIVE | Redis SSOT V8.0 with Lua atomicity |
| **startup_gate.py** | ACTIVE | 8 pre-flight checks (H-01) |
| **invariant_enforcer.py** | ACTIVE | 12 runtime invariants (H-02) |
| **profit_ladder.py** | SUPERSEDED | Chandelier is sole exit authority; V3 inline ladders disabled |
| **quant_math/ (14 files)** | DORMANT | Almgren-Chriss, Hawkes, fractional diff — Phase Q2+ |

**V1 Critical Bugs (must fix before or during V2 migration):**

| ID | Severity | Module | Issue | Fix |
|----|----------|--------|-------|-----|
| **AEGIS 0-05** | CRITICAL | ml_meta_model.py | Regime encoding always returns -1; confidence circular feedback; 43.7% fabricated data | Disable entirely until J-01/J-02 fixed and N ≥ 200 real trades |
| **J-02** | CRITICAL | ml_meta_model.py | Regime map uses fictional keys ("bull"/"bear") not RegimeState enum | Remap to actual RegimeState enum values |
| **J-01** | CRITICAL | ml_meta_model.py | Confidence leaked as ML input feature | Remove confidence; replace with raw_indicator_count, spread_bps, time_since_regime_change |
| **S3 Contradiction** | LOW | mean_reversion.py | Hard veto on leveraged ETPs (correct) contradicted by V2.1 reactivation comment (wrong) | Remove reactivation comment in SC-07 |

---

### 1.2 V2 Rust Engine — Complete Module Inventory

**Status: Phases 1-7 COMPLETE. ~9,000 LOC. 147+ tests. All 98 P0+P1 stop-ship items resolved.**

| Module | LOC | Tests | Status |
|--------|-----|-------|--------|
| engine.rs | 700+ | 12 | COMPLETE |
| ibkr_broker.rs | 400+ | 9 | COMPLETE |
| paper_broker.rs | 400+ | implicit | COMPLETE |
| risk_arbiter.rs | 255 | 22 | COMPLETE — 22-check gate, 4-regime hierarchy, fail-closed |
| exit_engine.rs | 303 | 22 | COMPLETE — 5-rung Chandelier, shadow stops (H67), ratchet enforcer |
| portfolio.rs | 251 | 9 | COMPLETE — position tracking, heat calc, sector/inverse metadata |
| python_bridge.rs | 204 | implicit | COMPLETE — JSON-lines subprocess IPC |
| wal_writer.rs + wal_replay.rs | 480+ | 36 | COMPLETE — CRC32, disk-space check (H25), dead-letter |
| reconciler.rs | 245+ | implicit | COMPLETE — orphan detection, position matching |
| universe.rs | 200+ | 14 | COMPLETE — Vanguard/Apex routing, filter chain |
| clock.rs | 250+ | implicit | COMPLETE — London time, market hours, auction periods |
| config_loader.rs | 370+ | implicit | COMPLETE — 4-TOML load, validated at startup |
| types/ (4 files) | 1000+ | 4 | COMPLETE — 10 enums, 2 newtypes, MarketTick, OrderIntent |
| ouroboros_loader.rs | 225+ | implicit | COMPLETE — nightly artifact load |
| channel.rs | 150+ | implicit | COMPLETE — tick backpressure, circular buffer |
| **TOTAL** | **~9,000** | **147+** | **COMPLETE** |

**V2 Confirmed Facts:**
- Paper mode hardcoded: `IS_LIVE = false` in main.rs:26
- IB Gateway port: **4004** (NOT 4002)
- client_id = 101 (V1 uses 100)
- Ouroboros nightly via Supercronic crontab in container
- IBKR reconnect + BackoffState (5 attempts, exponential) implemented
- Zero panics: `#![deny(clippy::unwrap_used)]` + `#![deny(warnings)]`

**V2 Deferred by Design (new phase assignments):**

| Component | Phase |
|-----------|-------|
| tokio async reactor | Phase 8+ |
| Multi-ticker SubscriptionManager + line budget | Phase 11 |
| 5-mode clock + ModeController | Phase 11 |
| Infinite Chandelier 8-multiplier | Phase 14 |
| ADV-bounded execution + U-shaped TWAP | Phase 14 |
| RiskGate 31-veto expansion | Phase 15 |
| CVaR heat with Cornish-Fisher | Phase 15 |
| Polars Ouroboros pipeline | Phase 16 |
| External bulk EOD data provider | Phase 16 |
| Telegram long-polling + PyMuPDF PDFs | Phase 17 |
| European exchange profiles (15 exchanges) | Phase 18 |
| Asian exchange profiles + KRX VI + NZX | Phase 19 |
| Overnight carry state machine + HALTED state | Phase 20 |

---

## PART 2 — GEMINI ADVERSARIAL AUDIT — TRIAGED

Gemini produced four rounds of analysis: (1) 200-bullet adversarial audit + top-10 priority fixes, (2) binding architectural mandates (Polars, crossbeam, IBKR pacing), (3) fatal flaw corrections (pacing paradox, scanner conservation, tier nomenclature), (4) **6 hidden implementation traps** (v19 delta above). All triaged below.

### 2.1 Top-10 Priority Fixes

| ID | Finding | Severity | Phase |
|----|---------|----------|-------|
| G-01 | Ouroboros OOM: Pandas on 5,000+ tickers crashes EC2 8GB RAM | CRITICAL | Phase 16 — Polars mandate |
| G-02 | SubscriptionManager silence heuristic (2s no-tick) fails on illiquid assets | CRITICAL | Phase 8 — SC-02, SC-11 |
| G-03 | Frozen Chandelier during CARRIED = unlimited gap-down risk | HIGH | Phase 20 — reqPnL subscription |
| G-04 | Corporate action spin-offs can void ISA tax wrapper | HIGH | Phase 16 — corp_action_blocklist.json **(v19-FIX-6)** |
| G-05 | Ouroboros failure allows trading on stale parameters | HIGH | Phase 16 — Yellow tier escalation |
| G-06 | Log AUM taper arbitrarily suppresses geometric growth | MEDIUM | Phase 16 — removed; ADV+CVaR sufficient |
| G-07 | VPIN static 50-bucket fails on illiquid European equities | MEDIUM | Phase 18 — adaptive bucket |
| G-08 | OFI 200-tick window destroys time-domain signal intensity | MEDIUM | Phase 13 — 5s time-decay EWMA |
| G-09 | reqPositions cost basis wrong for same-day trades (T+2 lag) | MEDIUM | Phase 8 — SC-10 |
| G-10 | Telegram polling: 2s HALT latency during flash crashes | MEDIUM | Phase 17 — async long-polling **(v19-FIX-4)** |

### 2.2 Binding Architectural Mandates

| ID | Mandate | Phase |
|----|---------|-------|
| **GEM-A1** | **Ban Pandas.** Use Polars `LazyFrame` + Arrow zero-copy. 500-ticker batches. RSS ceiling 3.5GB. | Phase 16 |
| **GEM-A2** | **Lock-free ring buffer.** `crossbeam-channel` bounded (capacity=**50,000** **(v19-FIX-3)**) between Rust ticks and Python bridge. `TrySendError::Full` → drop oldest tick. | Phase 8 |
| **GEM-A3** | **IBKR Pacing Paradox fix.** IBKR `reqHistoricalData` token bucket (60 req/10min) for active ~100 tickers ONLY. Nightly universe screening of 5,000+ tickers → external bulk EOD provider (Polygon.io / Databento). Math: 360 req/hr × 2h = 720 max IBKR pulls < 5,000 needed. | Phase 8 + 16 |
| **GEM-A4** | **Scanner Conservation Rule.** Underlying equities subscribed ONLY when live position exists. HotScanner/RotationScanner candidates do NOT get underlyings tracked. | Phase 11 |
| **GEM-A5** | **Drawdown tier nomenclature.** Yellow = Kelly × 0.5, no new entries (partial restriction). Orange = close all positions. Red = full halt. Ouroboros failure → Yellow (not Orange). | Phase 16 |

### 2.3 Phase-by-Phase Gemini Injections

**Phase 8:** SC-09 crossbeam buffer (capacity=50,000), SC-10 cost-basis tracker, SC-11 AtomicUsize line counter, SC-04 two-tier data architecture, **SC-12 symbology mapper (v19-FIX-2)**

**Phase 11:** Scanner Conservation Rule as named deliverable; illiquid-ticker proptest (no ticks for 10s); 2-5s mode transition blackout documented as accepted

**Phase 12:** SmartRouter cost comparison uses `reqMktData snapshot=True` **(v19-FIX-5)**; corporate action veto reads `corp_action_blocklist.json` (Ouroboros-populated) **(v19-FIX-6)**

**Phase 13:** OFI 5s time-decay EWMA (not 200-tick); CUSUM h floor at `max(h_adaptive, 2 × bid_ask_spread)`

**Phase 14:** Chandelier floor ≥ 1.5× bid_ask_spread; TWAP U-shaped (60-day median volume curve, not flat); partial fill **FILLED** portion < £500 → immediate market-sell **(v19-FIX-1)**

**Phase 15:** CVaR uses Cornish-Fisher expansion (not normal); DCC-GARCH veto computed async, cached 5-min TTL

**Phase 16:** Polars mandate; remove log AUM taper; Yellow tier escalation on Ouroboros failure; 500-ticker batch processing; **Ouroboros step 2a: corporate action blocklist from Polygon + yfinance (v19-FIX-6)**

**Phase 17:** **Long-polling on dedicated Python thread (v19-FIX-4)**; Rust-side HALT priority interrupt; heartbeat every 30 min; 4000-char message truncation

**Phase 18:** Adaptive VPIN bucket; FTT intraday exemption; XETRA cutoff at T-5=15:25 UTC

**Phase 19:** NZX ENABLED — subscriptions begin at 23:00 UTC (MODE A open); ASX SYCOM excluded; KRX VI (10%/1min = 120s halt); HKD = 80% USD concentration weight

**Phase 20:** reqPnL subscription (not polling) for carry positions; holiday carry → MONITORED state

**Phase 22:** Chaos tests — Python bridge crash, IBKR 04:45 UTC disconnect, Redis OOM-kill, WAL disk-full; Polars RSS ≤ 3.5GB verified

### 2.4 Deferred (Post-Crucible)

| Finding | Reason |
|---------|--------|
| Multi-level OFI (5-level depth) | Requires IBKR Level 2 subscription |
| Garman-Klass volatility estimator | Enhancement, not blocking |
| Gaussian Process Regressor for ranking | Phase Q2+ ML |
| Dynamic Time Warping cross-timezone | Phase Q2+ signal research |
| EVT (Extreme Value Theory) CVaR | Enhancement over Cornish-Fisher |
| Contextual Bandits Allocator | Phase Q2+ |
| QuestDB/InfluxDB tick storage | Post-live infrastructure |
| HSMM (Semi-Markov) regimes | Phase Q2+ |
| LULD pause handling (US equities) | Phase 14 follow-up |
| Wash trade AML filter | Compliance review needed |

---

## PART 3 — PHASE PLAN

### Numbering Convention

- **Phases 1-7**: COMPLETE (V2 Rust core)
- **Phase 8**: Pre-conditions and P0 hardening (NEXT)
- **Phases 9-10**: Reserved for future use
- **Phases 11-23**: Granular build phases (multi-universe expansion + Crucible)

---

### ██ PHASE 8 — Pre-Conditions & P0 Hardening
**Hours**: 34h | **Status**: NEXT — must complete before Phase 11

**Rationale**: SC-01 to SC-12 are blocking items. None of Phases 11+ can proceed without them. Phase 8 injects the 4 Gemini binding mandates (GEM-A2/A3 + SC-10/11 precision fixes + SC-12 symbology mapper).

**Deliverables:**

| SC | Item | File | Fix |
|----|------|------|-----|
| SC-01 | SIGTERM handler: flatten → 30s fill wait → WAL shutdown event → exit | main.rs | — |
| SC-02 | SubscriptionManager skeleton: Mutex-guarded, deterministic cancel→ACK→subscribe (NOT 2s silence) | subscription_manager.rs | G-02 |
| SC-03 | LineBudget struct `{carry, active, scan}` with `assert!(carry + active + scan <= 100)` | subscription_manager.rs | — |
| SC-04 | Two-tier data: IBKR token bucket (60 req/10min, 6 concurrent, Error 162 backoff) for active ~100 tickers; Polygon.io/Databento for nightly 5,000+ universe screening | ibkr_broker.rs + ouroboros/data_fetch.py | GEM-A3 |
| SC-05 | `MINIMUM_ENTRY_GBP: f64 = 1500.0` pre-entry gate in risk_arbiter.rs | risk_arbiter.rs | — |
| SC-06 | Dust guard in exit_engine.rs: **if `filled_gbp < 500.0` → submit market-sell on the FILLED portion; cancel unfilled remainder separately** **(v19-FIX-1)** | exit_engine.rs | v19-FIX-1 |
| SC-07 | Fix V1 S3 contradiction: remove conflicting reactivation comment from mean_reversion.py | mean_reversion.py | — |
| SC-08 | APScheduler timezone audit: all pre-LSE jobs use `timezone="Europe/London"` | main.py | — |
| SC-09 | `crossbeam-channel` bounded ring buffer **(capacity=50,000)** between Rust ticks and Python bridge; `TrySendError::Full` → drop oldest, log overflow counter **(v19-FIX-3)** | python_bridge.rs | GEM-A2 + v19-FIX-3 |
| SC-10 | Internal cost-basis tracker in portfolio.rs: `HashMap<TickerId, CostBasisEntry>` updated on `OrderFilled` WAL events; overrides `reqPositions` for same-day trades | portfolio.rs | G-09 |
| SC-11 | SubscriptionManager `active_line_count: AtomicUsize`; increment on `reqMktData` ACK, decrement on `cancelMktData` ACK; `assert!(count <= 100)` before every new subscription | subscription_manager.rs | G-02 |
| **SC-12** | **`symbology_mapper.py` (NEW):** maps IBKR canonical symbol → Polygon format. Rules: (a) IBKR space → Polygon dot: `"BRK B"` → `"BRK.B"`; (b) IBKR LSE suffix → Polygon prefix: `"NVD3.L"` → `"LSE:NVD3"`; (c) IBKR `XETRA:NVD3` → Polygon `XETRA:NVD3` (pass-through); (d) all data_fetch.py Polygon API calls go through `SymbologyMapper.to_polygon(ibkr_symbol)` **(v19-FIX-2)** | ouroboros/symbology_mapper.py | v19-FIX-2 |

**Gate**: All 12 items coded + unit tested; `cargo test` passes; `docker build` passes; crossbeam buffer stress-tested at 10,000 ticks/sec synthetic load; symbology mapper tested against known IBKR/Polygon pairs

---

### ██ PHASE 11 — 5-Mode Clock & SubscriptionManager
**Hours**: 20h | **Depends on**: Phase 8

**Rationale**: The temporal spine. All subsequent phases depend on knowing which mode the system is in and safely managing the 100-line IBKR budget.

**Deliverables:**

- `clock.rs` EXTENDED:
  - `TradingMode` enum: `{ModeA, ModeB, ModeBPlus, ModeC, Dark}`
  - `from_utc_secs(s: u32) -> TradingMode` with correct ModeA arm: `s >= 23*3600 || s < 8*3600`
  - `mode_b_plus_end_utc(date: NaiveDate) -> u32` using `ZoneInfo("Europe/London")` for DST-correct LSE close
  - `NZX_OPEN_UTC_SECS: u32 = 23 * 3600` (MODE A open — Gemini resolution)
  - `NZX_CLOSE_UTC_SECS: u32 = 5 * 3600 + 45 * 60`

- `subscription_manager.rs` (NEW, extends SC-02/SC-03/SC-11 skeleton):
  - Full Mutex-guarded state machine: `cancel → wait for ACK via AtomicUsize → subscribe`
  - Proptest: randomly order 500 subscribe/cancel sequences, assert `active_line_count <= 100` invariant never violated
  - Illiquid-ticker test: 10s silence must NOT be treated as cancellation confirmation
  - **Scanner Conservation Rule (GEM-A4 — explicit deliverable):** `LineBudget::underlying_lines` increments only on position open, decrements on position close. HotScanner/RotationScanner candidates NEVER trigger underlying subscription. Enforced by `subscription_manager.rs::subscribe_underlying(position_id)` — only callable from engine position-open path.

- `mode_controller.rs` (NEW):
  - State machine driving mode transitions
  - Publishes `ModeChange { from, to, utc_ts }` events to engine
  - Documents and logs 2-5s scanning blind window during cancel→subscribe cycle (accepted behavior, not a bug)

**Acceptance Tests (AT-01 to AT-16):**
- AT-01: ModeA boundary midnight wrap (23:59 UTC = ModeA, 00:01 UTC = ModeA)
- AT-02: ModeA → ModeB transition at 08:00 UTC
- AT-03: ModeB+ end DST: BST date = 15:30 UTC, GMT date = 16:30 UTC
- AT-04: DARK enforcement (21:00 UTC = DARK, 22:59 UTC = DARK)
- AT-05: NZX open at 23:00 UTC (MODE A start), not 21:00 UTC
- AT-06: Line budget proptest (1000 random sequences, invariant holds)
- AT-07: Illiquid ticker — 10s no-ticks — SubscriptionManager must NOT proceed with subscribe
- AT-08: Scanner Conservation Rule — 40 HotScanner tickers → `underlying_lines = 0`
- AT-09: Position open → underlying subscribed → `underlying_lines = 1`
- AT-10: Position close → underlying unsubscribed → `underlying_lines = 0`
- AT-11: Carry position → underlying stays subscribed during DARK mode
- AT-12: Mode transition cancel→subscribe → line count never exceeds 100 during transition
- AT-13: 5s blind window logged as `ModeTransitionBlind` event (not error)
- AT-14: ModeC → DARK: all scanner lines cancelled, carry lines retained
- AT-15: DARK → ModeA: scanner lines reestablished within 10s
- AT-16: DST spring-forward: ModeB+ end shifts 1h, verified by `mode_b_plus_end_utc()`

**Gate**: 16 tests pass; ModeA boundary proptest 1000 cases; DST flip manual test (set system time to BST/GMT boundary); scanner conservation proptest 500 cases

---

### ██ PHASE 12 — Smart Router & ISA Gate
**Hours**: 15h | **Depends on**: Phase 11

**Rationale**: Routing logic determines which instrument we trade. **v19-FIX-5** resolves the data paradox: SmartRouter now uses a one-shot snapshot query (zero streaming lines consumed). **v19-FIX-6** moves corporate action safety to Ouroboros-maintained blocklist.

**Deliverables:**

- `smart_router.rs` (NEW):
  - ETP-first principle: ETP wins unless no ETP exists OR (direct_cost < etp_cost × 0.9 AND health passes)
  - **Cost comparison with snapshot=True (v19-FIX-5):** To compare direct equity spread vs ETP cost, SmartRouter calls `reqMktData(conid, snapshot=True)`. This is a one-shot request — IBKR returns a single quote snapshot and does NOT open a persistent streaming subscription. Consumes **0 of the 100 IBKR market data lines**. This resolves the data paradox where fetching a quote for comparison would consume the line we're trying to avoid using.
  - Full cost model: FX drag + FTT + IBKR commission + stamp duty
  - Min-lot enforcement per exchange
  - `route(ticker, mode, portfolio_state) -> RouteResult`

- `isa_gate.rs` (NEW):
  - Hard-blocks: Taiwan (TWSE/GTSM), China (SSE/SZSE), India (BSE/NSE) — no exceptions
  - ISA annual limit check (£20k per tax year)
  - ETP classification: Tier 1 priority routing

- FTT market-cap gate: `effective_rate_bps(market_cap_eur: f64) -> f64`
  - France: 0.3% if market_cap > €1B, else 0.0
  - Italy: 0.1% if market_cap > €500M, else 0.0
  - Spain: 0.2% (no threshold)

- `MINIMUM_ENTRY_GBP = 1500.0` wired into router pre-Kelly-submission check

- **Corporate Action Veto (v19-FIX-6 — Ouroboros-backed):**
  - RiskGate reads `calibration/corp_action_blocklist.json` on startup and refreshes on Ouroboros completion
  - Structure: `{ "AAPL": { "action": "spin-off", "ex_date": "2026-03-15", "hours_until": 144 }, ... }`
  - `CorporateActionVeto` fires if ticker in blocklist AND `hours_until < 48`
  - `reqContractDetails` used only as secondary confirmation (IBKR calendar unreliable for spin-offs/mergers)
  - Prevents non-ISA-eligible spin-off landing in ISA wrapper (would void tax status)

**Acceptance Tests (AT-17 to AT-30):**
- AT-17: ETP preferred over direct when costs equal
- AT-18: Direct preferred when 10% cheaper and health passes
- AT-19: ISA gate blocks TWSE ticker (Taiwan)
- AT-20: ISA gate blocks SSE ticker (China)
- AT-21: ISA gate blocks BSE ticker (India)
- AT-22: FTT France 0.3% for market_cap = €2B
- AT-23: FTT France 0.0% for market_cap = €500M (below threshold)
- AT-24: FTT Italy 0.1% for market_cap = €600M
- AT-25: FTT Italy 0.0% for market_cap = €400M
- AT-26: Min-lot enforcement (TSE lot=100, order for 50 shares → rejected)
- AT-27: ISA annual limit: £19,500 used + £600 order → rejected
- AT-28: Corporate action veto fires for ticker in blocklist with ex_date in 24h
- AT-29: Corporate action veto clears for ticker with ex_date > 48h away
- AT-30: Cost comparison uses snapshot=True (verified: no persistent subscription opened, `active_line_count` unchanged after SmartRouter call)

**Gate**: 14 tests pass; FTT gate verified both France + Italy thresholds; ISA triple-gate verified; snapshot=True cost comparison verified (line count unchanged); corporate action blocklist veto verified with synthetic JSON

---

### ██ PHASE 13 — HotScanner & RotationScanner Signal Stack
**Hours**: 20h | **Depends on**: Phase 12

**Rationale**: The signal layer that identifies trading opportunities. Gemini found 2 critical flaws in OFI and CUSUM — fixed here.

**Deliverables:**

- `hot_scanner.rs` (NEW):
  - Per-mode dispatch (ModeB/B+/C get continuous ticks; ModeA gets Asian ticks)
  - **OFI scoring (Gemini G-08 fix):** 5-second time-decay EWMA (not 200-tick rolling): `ofi_ewma = α × ofi_t + (1-α) × ofi_ewma_prev` where α = `1 - exp(-dt/5.0)` and dt is seconds since last tick
  - **CUSUM filter (Gemini):** threshold h floor: `h = max(h_adaptive, 2.0 × bid_ask_spread_bps)` prevents bid-ask bounce false triggers
  - Kalman price filter for trend estimation
  - Meta-label gate (threshold 0.55)

- `rotation_scanner.rs` (NEW):
  - Thompson Sampling multi-arm bandit (Beta-Bernoulli)
  - 60-second OHLCV snapshots for rotation candidates
  - Per-ticker `(alpha: f64, beta: f64)` win/loss posterior
  - Score: `(trend_velocity × 0.4) + (ofi_signal × 0.3) + (thompson_sample × 0.3)`
  - Promotion threshold: score > 0.70 → move to HotScanner slot

- `universe_scanner.rs` (NEW):
  - US equity discovery via IBKR `reqContractDetails` (batched, respects 50 req/s limit)
  - ADV filter: minimum £50k daily average volume
  - RVOL calculation: real-time vs 20-day average
  - All scanner candidates respect 100-line budget from SubscriptionManager

**Acceptance Tests (AT-31 to AT-47):**
- AT-31: OFI 5s time-decay EWMA — fast market (200 ticks in 3s) gives higher intensity than slow market (200 ticks in 1h) for same tick imbalance
- AT-32: OFI EWMA decay verified — value halves in 5 seconds after last tick
- AT-33: CUSUM h floor: spread=0.5%, h_adaptive=0.3% → h used = 0.5%
- AT-34: CUSUM h floor: spread=0.1%, h_adaptive=0.5% → h used = 0.5%
- AT-35: Kalman filter converges within 50 ticks of synthetic sine-wave price
- AT-36: Thompson Sampling — 100 simulated trades → arm with higher WR gets more allocation
- AT-37: Thompson Sampling — arm with 0 trades gets exploration allocation
- AT-38: RotationScanner promotion at score > 0.70
- AT-39: RotationScanner demotion at score < 0.40
- AT-40: ADV filter blocks ticker with £30k daily volume
- AT-41: ADV filter passes ticker with £80k daily volume
- AT-42: `reqContractDetails` batching sends ≤ 50 requests per second
- AT-43: HotScanner dispatches MODE A tickers only during ModeA
- AT-44: HotScanner dispatches US tickers only during ModeB+/ModeC
- AT-45: Meta-label gate blocks signal with probability 0.45
- AT-46: Meta-label gate passes signal with probability 0.60
- AT-47: Total scanner lines ≤ available from SubscriptionManager line budget

**Gate**: 17 tests pass; Thompson Sampling arm-selection verified over 500 simulated rounds; OFI time-decay vs count-based comparison test

---

### ██ PHASE 14 — Infinite Chandelier + Executioner V2
**Hours**: 20h | **Depends on**: Phase 13

**Rationale**: The exit and execution engine. Gemini found 3 critical flaws — Chandelier floor whipsaw, flat TWAP, dust after partial fills. **v19-FIX-1** clarifies that the dust check is on the FILLED portion.

**Deliverables:**

- `exit_engine.rs` EXTENDED — Infinite Chandelier with 8 adaptive multipliers:
  - M1: ATR scale (geometric decay: `M1 = 3.0 × 0.85^(rung-1)`)
  - M2: Time-of-day (tighter near close)
  - M3: Regime (BEAR_VOLATILE tightens multiplier)
  - M4: Profit scale (widens as position grows)
  - M5: Momentum decay (tightens as momentum fades)
  - M6: ATR percentile (widens in high-vol)
  - M7: MAE calibration (tightens based on historical max adverse excursion)
  - M8: Correlation contagion (tightens if correlated asset drops)
  - **Chandelier floor (Gemini G-14):** `stop_distance = max(multiplier × ATR, 1.5 × bid_ask_spread)` — prevents immediate whipsaw
  - Ratchet enforcer: `new_stop = max(old_stop, computed_stop)` — stop can ONLY increase

- `executioner_v2.rs` (NEW):
  - ADV execution gate: `order_size ≤ 1% of 5-min rolling volume`
  - **U-shaped TWAP (Gemini G-08):** slice sizes follow 60-day median volume curve; more volume at 08:00-10:00 and 15:00-16:30, less at 12:00-13:00
  - `AdVExecutionGate::check(order_gbp, rolling_vol_5min) -> bool`
  - **Partial fill dust check on FILLED portion (v19-FIX-1):** After partial fill cancel, if `filled_gbp < 500.0` → immediately submit market-sell on the filled shares. The unfilled remainder is simply cancelled (not submitted). This is distinct from the unfilled remainder which is just cancelled regardless of size.
  - Alpha half-life TWAP: slices spread across `alpha_halflife_secs` seconds

- `spread_veto.rs` (NEW):
  - U-shaped intraday spread tolerance: tight at open/close, wide at lunch
  - `spread_veto_threshold_bps(time_of_day: f32) -> f64`

**Acceptance Tests (AT-48 to AT-64):**
- AT-48: All 8 multiplier outputs in range [0.5, 5.0] (bounded)
- AT-49: Ratchet proptest — 1000 random price sequences — stop never decreases
- AT-50: Chandelier floor: ATR=0.2%, spread=0.4% → stop_distance = 0.6% (≥ 1.5×spread)
- AT-51: Chandelier floor: ATR=0.5%, spread=0.1% → stop_distance = 0.5% (ATR dominates)
- AT-52: ADV gate blocks order >1% of 5-min rolling volume
- AT-53: ADV gate passes order ≤1% of 5-min rolling volume
- AT-54: TWAP U-shape: open slice > midday slice (volume curve respected)
- AT-55: TWAP total slices fill 100% of order over alpha_halflife window
- AT-56: Partial fill FILLED portion £400 → immediate market-sell (dust liquidated) **(v19-FIX-1)**
- AT-57: Partial fill FILLED portion £600 → no dust action (above £500 threshold)
- AT-58: M3 regime tightening: BEAR_VOLATILE × multiplier < BULL_QUIET × multiplier
- AT-59: M8 correlation contagion: correlated asset -3% → multiplier tightened
- AT-60: Spread veto: 09:30 UTC threshold < 12:00 UTC threshold (tighter at open)
- AT-61: Spread veto: 15:30 UTC threshold < 12:00 UTC threshold (tighter at close)
- AT-62: Mega-runner carry eligibility at +102% unrealised gain
- AT-63: Mega-runner carry NOT triggered at +99% (below threshold)
- AT-64: Exit urgency score drives market vs passive order type selection

**Gate**: 17 tests pass; ratchet proptest 1000 cases; Chandelier floor verified at low-ATR high-spread condition; TWAP U-shape distribution test; dust test verifies market-sell on FILLED portion (not unfilled)

---

### ██ PHASE 15 — RiskGate 31 Vetoes + CVaR Heat
**Hours**: 15h | **Depends on**: Phase 14

**Rationale**: Expand from 22 to 31 vetoes. Add CVaR with Cornish-Fisher (not normal assumption). DCC-GARCH computed async (not on critical path).

**Deliverables:**

- `risk_arbiter.rs` EXTENDED — 9 new veto checks added to existing 22:
  1. `ExchangeClosed` — outside exchange trading hours
  2. `AuctionAvoidance` — within 5 min of opening/closing auction
  3. `DarkModeActive` — fires FIRST during 21:00-23:00 UTC (pre-veto)
  4. `LunchBreakActive` — TSE/HKEX lunch suppression
  5. `DailyPriceLimitActive` — TSE ±20%, KRX ±30%
  6. `DustGuard` — position < £500 FILLED after partial fill
  7. `MinimumEntryGate` — order < £1500 GBP equivalent
  8. `CVaRExceeded` — portfolio CVaR above floating limit
  9. `HMMLimitExceeded` — signal score below HMM regime floor

- `cvar_heat.rs` (NEW):
  - **Cornish-Fisher CVaR (Gemini G-15):** `CVaR_CF = μ - σ × (z_α + (z_α² - 1)×S/6 + ...)` where S = skewness, K = excess kurtosis. Significantly diverges from normal assumption in tail — verified in tests.
  - CVaR limit floats: `heat_limit = base_heat × hmm_regime_factor × vix_factor`
  - BEAR_VOLATILE + VIX=40: `6% × 0.40 × 0.45 = 1.08%` max portfolio heat
  - Full 6-bucket table: 3 regimes × 2 VIX bands

- DCC-GARCH veto: computed asynchronously (off critical path), cached with 5-min TTL, injected into RiskGate via `Arc<RwLock<GarchState>>`

- Half-Kelly enforcement: until 250 validated live trades, all Kelly outputs × 0.5

**Acceptance Tests (AT-65 to AT-78):**
- AT-65: DarkModeActive veto fires before any other check during DARK window
- AT-66: ExchangeClosed veto fires for TSE at 10:00 UTC (mid-lunch)
- AT-67: LunchBreakActive veto fires for TSE at 02:45 UTC
- AT-68: LunchBreakActive veto fires for HKEX at 04:30 UTC
- AT-69: DailyPriceLimitActive fires for KRX at ±30% move
- AT-70: DailyPriceLimitActive fires for TSE at ±20% move
- AT-71: MinimumEntryGate blocks £1,400 order
- AT-72: MinimumEntryGate passes £1,600 order
- AT-73: CVaR Cornish-Fisher diverges from normal at tail (S=−0.5, K=2.0 → CF > Normal by ≥10%)
- AT-74: CVaR heat limit table: BEAR_VOLATILE + VIX=40 → 1.08%
- AT-75: CVaR heat limit table: BULL_QUIET + VIX=15 → full 6% base
- AT-76: Half-Kelly: 200 trades → Kelly × 0.5 applied
- AT-77: Half-Kelly gates off at 250 trades
- AT-78: DCC-GARCH result cached — verified NOT recomputed on each OrderIntent

**Gate**: 14 tests pass; 31 total vetoes confirmed; CVaR Cornish-Fisher vs normal divergence test at tail; DCC-GARCH async path verified

---

### ██ PHASE 16 — Ouroboros Upgrades + Scaling
**Hours**: 20h | **Depends on**: Phase 15

**Rationale**: The nightly intelligence pipeline. Gemini mandates: Polars or OOM kill, no log AUM taper, Yellow tier on failure. Pacing paradox requires external data source for universe screening. **v19-FIX-6** moves corporate action safety here.

**Deliverables:**

- `ouroboros/` EXTENDED — 9-step pipeline with new Step 2a:
  1. Data fetch (external bulk EOD + IBKR active tickers)
  **2a. Corporate action blocklist (v19-FIX-6):** `data_fetch.py` calls Polygon.io `/v3/reference/dividends?ex_dividend_date.gte={today}&ex_dividend_date.lte={today+7d}` and `/v3/reference/splits`. Uses yfinance `.actions` as fallback. Writes `calibration/corp_action_blocklist.json`: `{ ticker: { action, ex_date, hours_until_ex } }`. This file is the authoritative source for RiskGate corporate action vetoes. `reqContractDetails` used only as secondary confirmation.
  2. Universe discovery (5,000+ ticker screen using Polars)
  3. Feature engineering (Polars LazyFrame, Arrow zero-copy)
  4. Scoring (ASER: momentum 30%, liquidity 20%, volatility 20%, regime 15%, recency 15%)
  5. Meta-label training (Logistic Regression / LightGBM fallback)
  6. Chandelier calibration (ATR, MAE/MFE profiling)
  7. Thompson Sampling update (alpha/beta posteriors from WAL outcomes)
  8. DCC-GARCH update (cross-asset correlation matrix)
  9. PDF generation + artifact write (calibration/weights.json, calibration/asia_cross_tz.json)

- Step checkpointing: `ouroboros_step_N_ts` Redis key after each step; on restart, resume from last successful step

- **Polars mandate (GEM-A1):** `import pandas` banned in Ouroboros. ALL data processing via `polars.LazyFrame`. 500-ticker batch processing: `for batch in chunked(tickers, 500): process(batch); del df; gc.collect()`. RSS monitored via `psutil`; abort + Telegram alert if RSS > 3.5GB.

- **External bulk EOD provider (GEM-A3):** `ouroboros/data_fetch.py` uses Polygon.io as primary source for nightly universe data. Databento as fallback. Throttled yfinance proxy as last resort (rate-limited to 5 req/s, never in hot path). IBKR `reqHistoricalData` used ONLY for the ~100 active HotScanner/RotationScanner tickers.

- **Remove log AUM taper (GEM-A6):** No `aum_scaler.rs`. Kelly sizing governed exclusively by ADV 1% cap (Phase 14) + CVaR heat (Phase 15). No arbitrary penalty for account growth.

- **Ouroboros failure escalation (GEM-A5):** 22:55 UTC watchdog checks `pipeline_complete` Redis flag. If not set → `DrawdownTier::Yellow` (Kelly × 0.5, no new entries, existing positions managed normally) → Telegram 🔄 `SYSTEM SHIFT: Ouroboros incomplete — Yellow tier enforced` → await manual `RESUME` command via Telegram.

- `reconciler.rs` EXTENDED: Daily shadow book reconciliation, mismatch logged to WAL as `ShadowBookDivergence` event

**Acceptance Tests (AT-79 to AT-94):**
- AT-79: Polars LazyFrame processes 500-ticker batch without OOM (RSS ≤ 3.5GB)
- AT-80: Memory cleared between batches (RSS drops after `del df; gc.collect()`)
- AT-81: Pipeline checkpoint resume from step 5 (steps 1-4 skipped on restart)
- AT-82: WAL calibration read: avg_win/avg_loss correctly parsed from last 100 trades
- AT-83: Ouroboros failure at 22:55 UTC → DrawdownTier::Yellow set in engine
- AT-84: Yellow tier: new entry rejected, existing position managed normally
- AT-85: Yellow tier cleared on manual RESUME Telegram command
- AT-86: Telegram SYSTEM SHIFT fires on Yellow escalation
- AT-87: ADV 1% cap governs sizing at £50k AUM (no log taper applied)
- AT-88: ADV 1% cap governs sizing at £100k AUM (same result — no arbitrary penalty)
- AT-89: External bulk EOD fetch returns 5,000+ tickers without IBKR Error 162
- AT-90: IBKR `reqHistoricalData` used for ≤ 100 active tickers only
- AT-91: DCC-GARCH correlation matrix positive semi-definite
- AT-92: Thompson Sampling posteriors updated from WAL trade outcomes
- AT-93: Shadow book divergence > £5 → WAL event logged
- AT-94: Corporate action blocklist written to `calibration/corp_action_blocklist.json` with correct structure **(v19-FIX-6)**

**Gate**: 16 tests pass; Polars run verified ≤ 3.5GB RSS on 1000-ticker test; checkpoint resume from step 5 verified; Yellow escalation + Telegram alert end-to-end verified; corp_action_blocklist.json verified with Polygon API response

---

### ██ PHASE 17 — Telemetry Stack
**Hours**: 12h | **Depends on**: Phase 16

**Rationale**: Operational visibility. **v19-FIX-4** replaces webhook with async long-polling — achieves <100ms HALT latency with zero DevSecOps overhead (no SSL cert, no domain, no NGINX). Heartbeat prevents silent death.

**Deliverables:**

- `telegram_reporter.py` (NEW): `AegisTelegramReporter` async class
  - Exactly 4 alert types:
    - 🟢 **TARGET ACQUIRED**: `[MODE] [TICKER] [VEHICLE] Fill: {price} ({slippage}bps) Alloc: {pct}% Heat: {heat}%`
    - 🔵 **CHANDELIER SEVERED**: `[MODE] [TICKER] Exit: {price} Net: {pnl}% Duration: {mins}min Trigger: {reason}`
    - 🌟 **MEGA-RUNNER CARRY**: `[TICKER] +{pct}% → Carrying to {next_session}. 50% harvested. Stop frozen.`
    - 🔄 **SYSTEM SHIFT**: `[SHIFT_TYPE]: {description}` (regime change, drawdown tier, mode change)
  - Rate limiter: max 1 msg/5s (except HALT — bypasses limiter)
  - Message length: truncated at 4000 chars (Telegram API limit = 4096)

- **Long-polling architecture (v19-FIX-4):** `python-telegram-bot` async long-polling on a **dedicated Python thread** (`threading.Thread(target=run_polling, daemon=True)`). `application.run_polling(poll_interval=0.0)` — zero-delay polling achieves <100ms latency natively. Rust-side priority interrupt channel: HALT commands received via polling handler → write to `mpsc::Sender<HaltCommand>` → engine reads within 1 event loop tick. **No SSL cert. No domain. No NGINX. No webhook server to maintain.**

- **Heartbeat (Gemini):** `🟡 AEGIS ALIVE [MODE] Equity: £{equity} Positions: {n}` every 30 minutes via Ouroboros step 9. External watchdog monitors heartbeat timestamp in Redis; if missed twice → fires Telegram alert from watchdog account.

- `pdf_generator.py` (NEW): PyMuPDF `fitz.Story` — NO system dependencies (fitz wheel only)
  - **PDF1 — Post-Mortem** (generated 21:00 UTC, Ouroboros step 9):
    - Page 1: Global Scorecard + Shadow Book
    - Page 2: Execution slippage scatter plots
    - Page 3: Ouroboros AI prescriptions
  - **PDF2 — Morning Primer** (generated 22:05 UTC in DARK, delivered 07:00 UTC):
    - Page 1: Macro weather + ISA discoveries
    - Page 2: HotScanner draft (promotions/demotions)
    - Page 3: Smart Router execution preferences

- `shadow_book.py` (NEW): Virtual position tracker for paper-vs-real reconciliation; divergence > £5 logged as `ShadowBookDivergence` WAL event

**Acceptance Tests (AT-95 to AT-109):**
- AT-95: TARGET ACQUIRED fires on position open
- AT-96: CHANDELIER SEVERED fires on exit via trailing stop
- AT-97: MEGA-RUNNER CARRY fires at +102% unrealised gain
- AT-98: SYSTEM SHIFT fires on HMM regime change
- AT-99: HALT command via Telegram → engine receives within 100ms **(long-polling, not webhook)**
- AT-100: HALT bypasses 1msg/5s rate limiter
- AT-101: Non-HALT messages respect 1msg/5s rate limit
- AT-102: Heartbeat fires every 30 min (verified over 90 min)
- AT-103: Missed heartbeat × 2 → watchdog fires Telegram alert
- AT-104: PDF1 output is valid PDF bytes (fitz can open it)
- AT-105: PDF2 output is valid PDF bytes
- AT-106: Message > 4000 chars truncated cleanly at word boundary
- AT-107: Shadow book divergence £6 → WAL event logged
- AT-108: Shadow book divergence £4 → no WAL event
- AT-109: Telegram 429 (rate limit) → exponential backoff, message delivered within 30s

**Gate**: 15 tests pass; manually verify Telegram message arrives in channel within 100ms for HALT; both PDFs open in viewer; heartbeat verified over 2h with missed heartbeat simulation; **verify no SSL cert / webhook server required**

---

### ██ PHASE 18 — European Equities Extension
**Hours**: 18h | **Depends on**: Phase 17

**Rationale**: 15 European exchanges, multi-currency routing, FTT market-cap gates, dual-listing dedup. Adaptive VPIN replaces static bucket.

**Deliverables:**

- `currency.rs` (NEW): `FxRateTable` — 6 currencies (EUR/CHF/SEK/NOK/DKK/PLN), stale-rate detection (> 4h → HALT new positions in that currency), FX drag included in all Kelly sizing

- `exchange_profile.rs` (NEW): 15 European exchange profiles:
  - Euronext Paris, Amsterdam, Brussels, Dublin, Lisbon
  - XETRA (Frankfurt) — closing auction cutoff T-5 = 15:25 UTC (Gemini)
  - SIX Swiss, OMX Stockholm, Helsinki, Copenhagen
  - Borsa Italiana, BME Madrid, Oslo Børs, Warsaw, Athens
  - Per profile: tick sizes (MiFID II variable bands), trading hours, auction buffers, board lots

- `transaction_tax.rs` (NEW):
  - `effective_rate_bps(market_cap_eur, is_intraday) -> f64`
  - France: 0.3% (>€1B) + FTT = 0 if intraday (Gemini)
  - Italy: 0.1% (>€500M) + FTT = 0 if intraday
  - Spain: 0.2%, Switzerland: 0.075%, Greece: 0.2%, Belgium: 0.12%, Ireland: 1.0%

- `sub_universe_allocator.rs` (NEW):
  - Thompson Sampling for MODE B/B+ splits: ETP sub-universe vs European direct equity sub-universe
  - `active_min_fraction(now_utc, exchange_profiles) -> f64` — scales min allocation with fraction of exchanges currently open
  - **Adaptive VPIN (Gemini G-07):** `vpin_bucket_threshold_v_star(ticker_5d_adv) -> f64` — bucket volume proportional to 5-day median ADV, not static 50/session

- `universe.rs` EXTENDED:
  - European universe crawl (15 ISA-eligible exchanges via Polygon.io nightly)
  - ISIN-based dual-listing dedup: if ASML on both Euronext and XETRA → subscribe highest-ADV venue only
  - ETP overlay: ASML → ASL3.L if available on LSE

- `risk_arbiter.rs` EXTENDED:
  - Per-exchange EOD flatten: each exchange uses its own close time (not global LSE close)
  - `AuctionAvoidance` extended: 5-min buffer before each exchange's closing auction

- Config files: `european_exchange_profiles.toml`, `european_routing_table.toml`, `transaction_tax.toml`

**Acceptance Tests (AT-110 to AT-130):**
- AT-110: EUR FX drag included in Kelly sizing for Euronext positions
- AT-111: CHF FX drag included for SIX Swiss positions
- AT-112: FX stale rate (>4h): no new positions in that currency
- AT-113: FTT France: €2B market cap → 0.3% applied
- AT-114: FTT France: €500M market cap → 0.0% (below threshold)
- AT-115: FTT France intraday: buy+sell same day → 0.0%
- AT-116: FTT Italy: €600M → 0.1% applied
- AT-117: FTT Italy: €400M → 0.0%
- AT-118: XETRA EOD flatten triggered at 15:25 UTC (not 16:30 UTC)
- AT-119: Dual-listing ISIN dedup: ASML on Euronext + XETRA → only highest-ADV venue subscribed
- AT-120: ETP overlay: ASML position → routed to ASL3.L on LSE
- AT-121: Thompson Sampling: ETP vs direct allocation adapts to win rates
- AT-122: Adaptive VPIN bucket: low-ADV ticker → smaller bucket than high-ADV ticker
- AT-123: Static VPIN vs adaptive: low-volume afternoon fills bucket in 3h (static) vs 15min (adaptive)
- AT-124: Per-exchange EOD flatten: Borsa Italiana flattened at 15:30 UTC (not 16:30 UTC LSE close)
- AT-125: Tick size rounding: Euronext order for €51.23 → rounded to €51.25 (0.05 tick)
- AT-126: SubUniverseAllocator min_fraction: only 3/15 exchanges open → min_fraction × 0.2
- AT-127: SubUniverseAllocator min_fraction: all 15 exchanges open → full min_fraction
- AT-128: Spain FTT 0.2% applied regardless of market cap
- AT-129: Ireland stamp duty 1.0% applied
- AT-130: Germany (XETRA): no FTT, stamp duty = 0

**Gate**: 21 tests pass; 5 paper trading days with European tickers active; FX rate refresh verified (update within 4h); FTT intraday exemption verified on paper trade

---

### ██ PHASE 19 — Asia-Pacific: MODE A Infrastructure
**Hours**: 18h | **Depends on**: Phase 18

**Rationale**: MODE A clock (23:00-08:00 UTC), 6 Asian exchange profiles, IBKR 04:45 UTC reconnect. NZX ENABLED (Gemini resolution). KRX VI added.

**Deliverables:**

- `asian_exchange.rs` (NEW): 6 exchange profiles:
  - **TSE** (Tokyo): 00:00-06:00 UTC; lunch 02:30-03:30 UTC; board lot lookup via `TseBoardLotRegistry` (default 100, some ETFs = 1); daily limit ±20%
  - **HKEX** (Hong Kong): 01:30-08:00 UTC; lunch 04:00-05:00 UTC; board lots: most = 100; HKD drag = 0.0002
  - **ASX** (Sydney): official session 00:10-06:00 UTC; **SYCOM excluded** (`sycom_active = false`); AEDT: open=00:00 UTC; AEST: open=23:00 UTC (DST comment, static config)
  - **SGX** (Singapore): 01:00-09:00 UTC; no lunch break
  - **KRX** (Seoul): 00:00-06:30 UTC; no lunch; daily limit ±30%; **VI (Volatility Interruption):** `|tick - 1min_open| / 1min_open > 10%` → `VetoReason::VolatilityInterruptionActive` for 120s
  - **NZX** (Auckland): **ENABLED** (Gemini G-19 resolution): `NZX_OPEN_UTC_SECS = 23 * 3600`, `NZX_CLOSE_UTC_SECS = 5 * 3600 + 45 * 60`. NZX is a MODE A exchange. Subscriptions begin at 23:00 UTC when MODE A opens. No DARK-mode conflict.

- `clock.rs` EXTENDED:
  - MODE A boundary proptest (midnight wrap: 23:59 UTC → 00:01 UTC both ModeA)
  - 04:45 UTC IBKR reconnect handler: suspend tick delivery → reconnect (max 3 min) → resume; TSE + KRX in lunch at 04:45 UTC = minimal trading impact during reconnect

- ISA triple-gate hard-coded: `BLOCKED_EXCHANGES = ["TWSE", "GTSM", "SSE", "SZSE", "BSE", "NSE"]`

- **HKD concentration (Gemini G-20):** `Currency::HKD.usd_concentration_weight() = 0.8` (HKMA 7.75-7.85 band at 3× leverage = ~3% unhedged variance). RiskGate concentration check uses USD equivalent not face value.

- Asian FX drag: JPY 0.0006, HKD 0.0002, AUD 0.0005, SGD 0.0004, KRW 0.0008, NZD 0.0006

- Config: `asian_exchange_profiles.toml`, `asian_routing_table.toml`

**Acceptance Tests (AT-131 to AT-149):**
- AT-131: ModeA boundaries: 22:59 UTC = ModeC, 23:01 UTC = ModeA
- AT-132: ModeA midnight wrap: 23:59 UTC = ModeA, 00:01 UTC = ModeA
- AT-133: ModeA → Dark: 21:01 UTC = DARK
- AT-134: TSE lunch: order blocked at 02:45 UTC
- AT-135: TSE lunch: order passes at 04:00 UTC
- AT-136: HKEX lunch: order blocked at 04:30 UTC
- AT-137: KRX daily limit ±30%: order blocked when limit active
- AT-138: KRX VI: 10% price move in 1 minute → VetoReason::VolatilityInterruptionActive for 120s
- AT-139: KRX VI: 9% price move in 1 minute → no VI trigger
- AT-140: KRX VI: clears after 120s
- AT-141: ISA gate blocks TWSE ticker
- AT-142: ISA gate passes HKEX ticker (eligible)
- AT-143: ISA gate: TSMC → blocked via TWSE (TSMC direct); allowed via TSM3.L (ETP on LSE)
- AT-144: ASX official session: order passes at 00:15 UTC
- AT-145: ASX SYCOM: order blocked before 00:10 UTC
- AT-146: NZX: subscriptions begin at 23:00 UTC (not 21:00 UTC)
- AT-147: NZX: order passes at 23:30 UTC (MODE A)
- AT-148: IBKR 04:45 UTC disconnect: reconnect within 3 minutes
- AT-149: HKD concentration: £10k HKD position counted as £8k USD exposure

**Gate**: 19 tests pass; NZX opens correctly at 23:00 UTC (manual test); ASX SYCOM blocked before 00:10 UTC; KRX VI 10% trigger verified; 04:45 UTC reconnect simulated

---

### ██ PHASE 20 — Asia-Pacific: Overnight Carry State Machine
**Hours**: 20h | **Depends on**: Phase 19

**Rationale**: Carry positions crossing timezone sessions. reqPnL subscription replaces polling (Gemini G-03). Holiday calendar integration. HALTED state for circuit breakers.

**Deliverables:**

- `overnight_carry.rs` (NEW):
  - Full state machine: `LIVE → CARRIED → MONITORED → REACTIVATED → CLOSED`
  - HALTED branch: `MONITORED → HALTED (circuit breaker) → MONITORED (resolved)`
  - `MAX_CARRY_POSITIONS: usize = 6` (12 lines locked = 88 for scanning)
  - `try_carry(position) -> Result<(), CarryError::CapReached>` — 7th position triggers flatten at mode close
  - Mega-runner threshold: `+102%` unrealised gain (adaptive per-ticker 99th percentile MFE if higher)
  - Stop freeze: Chandelier stop frozen in CARRIED + MONITORED states; reactivated on exchange reopen

- **reqPnL subscription (Gemini G-03):** carry positions monitored via `ibkr.req_pnl_single(pnl_req_id, account, "", conid)`. IBKR pushes real-time PnL updates → `CarryPosition::on_pnl_update()`. No polling. No pacing violation. Consumes 0 market data lines.

- **Holiday carry (Gemini G-04):** On MODE C → DARK transition, Ouroboros checks `reqTradingHours` for each carry position's exchange. If next MODE A day is a public holiday → transition to MONITORED. Recheck each DARK cycle. When exchange reopens → REACTIVATED.

- HALTED state rules:
  - Triggered by: KRX ±30% daily limit, TSE Dynamic Circuit Breaker, HKEX Volatility Control Mechanism
  - No orders submitted during HALT
  - reqPnL subscription continues
  - Chandelier stop frozen at last computed level
  - Max HALTED duration: 2 trading days. Day 3 → submit market order.
  - Detection: IBKR Error 201 (Order rejected, market halted) or `reqContractDetails` showing current time outside continuous trading

- Telegram: `🚨 HALT: [TICKER] exchange circuit breaker active` on HALTED transition

- `exit_engine.rs` EXTENDED: stop freeze logic; reactivation check on MODE B open

- `risk_arbiter.rs` EXTENDED: `IsaExchangeBlocked`, `VolatilityInterruptionActive` (KRX), carry position count gate

**Acceptance Tests (AT-150 to AT-169):**
- AT-150: LIVE → CARRIED on MODE C close (mega-runner threshold reached)
- AT-151: CARRIED → MONITORED on exchange open (MODE A)
- AT-152: MONITORED → REACTIVATED on exchange tick received
- AT-153: REACTIVATED → CLOSED on Chandelier exit
- AT-154: Carry cap: 6th position accepted
- AT-155: Carry cap: 7th position → CarryError::CapReached, position flattened at mode close
- AT-156: CARRY_CAP_REACHED logged to WAL
- AT-157: MONITORED → HALTED on KRX ±30% daily limit
- AT-158: HALTED → no orders submitted
- AT-159: HALTED → MONITORED on exchange resume
- AT-160: HALTED Day 3 → market order submitted
- AT-161: Mega-runner +102% threshold: carry triggered
- AT-162: Below threshold +99%: no carry triggered
- AT-163: Stop frozen in CARRIED state (no Chandelier updates)
- AT-164: Stop unfrozen in REACTIVATED state (Chandelier resumes)
- AT-165: Holiday carry: HKEX holiday → MONITORED, recheck next DARK
- AT-166: Holiday resolved: exchange reopens → REACTIVATED
- AT-167: reqPnL subscription: no pacing violation over 24h (verify IBKR Error 162 never fires)
- AT-168: reqPnL subscription: position close → `cancel_pnl_single` called
- AT-169: HKD concentration 80% applied in carry position risk calculation

**Gate**: 20 tests pass; carry state machine proptest (100 random event sequences, invariants hold); HALTED Day 3 market order verified; reqPnL subscription tested over 2h paper session

---

### ██ PHASE 21 — Asia-Pacific: Cross-Timezone Intelligence
**Hours**: 12h | **Depends on**: Phase 20

**Rationale**: Dynamic cross-session correlation weights. No hardcoded 0.45/0.35/0.20.

**Deliverables:**

- `cross_timezone.py` (NEW):
  - DCC-GARCH derived sentiment weights (updated nightly by Ouroboros step 8):
    ```python
    weights = {
        "HKEX": abs(corr_matrix["HKEX_SP500"]) / total,   # 20-day rolling
        "Nikkei": abs(corr_matrix["NKY_SP500"]) / total,
        "ASX": abs(corr_matrix["ASX_SP500"]) / total,
    }
    ```
  - Stored in `calibration/asia_cross_tz.json`, loaded at MODE A open
  - No hardcoded 0.45/0.35/0.20 values

- `asia_universe.py` (NEW):
  - Asia-Pacific universe scanner (Ouroboros step 1 extension)
  - ISA eligibility check (Taiwan/China/India blocked)
  - Asian ADV filter: minimum volume thresholds per exchange
  - ETP/GDR overlay: TSM3.L for TSMC, BAB3.L for Alibaba, SMSN.IL for Samsung

- `pdf_generator.py` EXTENDED:
  - PDF1: Asia-Pacific section with overnight carry summary table
  - PDF2: Asian session risk metrics and DCC-GARCH weight changes

- `telegram_reporter.py` EXTENDED: 🌟 MEGA-RUNNER CARRY fires correctly for Asia carry positions

**Acceptance Tests (AT-170 to AT-179):**
- AT-170: DCC-GARCH weights sum to 1.0
- AT-171: Weights change between high-correlation regime (VIX=30) and low-correlation regime (VIX=12)
- AT-172: Cross-session correlation matrix is positive semi-definite
- AT-173: Asia universe scan returns HKEX tickers (ISA eligible)
- AT-174: Asia universe scan excludes TWSE tickers (ISA blocked)
- AT-175: TSM3.L returned for TSMC exposure (not TWSE direct)
- AT-176: MEGA-RUNNER CARRY fires for Asia carry position at +102%
- AT-177: PDF1 includes overnight carry table with carry state + P&L per position
- AT-178: DCC-GARCH weights loaded from `calibration/asia_cross_tz.json` at MODE A open
- AT-179: Weights refresh each night (stale weights trigger Telegram SYSTEM SHIFT)

**Gate**: 10 tests pass; 5 paper trading days with Asia session active (23:00 UTC); carry log shows correct state transitions; DCC-GARCH weights verified as adaptive (not static)

---

### ██ PHASE 22 — Institutional Hardening
**Hours**: 25h | **Depends on**: Phase 21

**Rationale**: Pre-Crucible production hardening. Gemini chaos tests. Memory audit. Rate limiter verification.

**Deliverables:**

- **SIGTERM end-to-end drill**: container kill → positions flatten → WAL write → restart → positions recovered from WAL (must work perfectly end-to-end, not just unit-tested)

- **WAL compaction**: 30-day rolling compaction; `compaction_metrics.json` written; dead-letter count alerted via Telegram if > 0

- **Rate limiter audit**: all IBKR calls verified within 50 req/s; token bucket synthetic stress test at 100 req/s (must throttle correctly, no Error 100 pacing violation)

- **Chaos suite (Gemini)**:
  - Python bridge crash → engine enters dry-run mode (no crash, no loss of position state)
  - IBKR disconnect at 04:45 UTC → reconnect within 3 min; carry positions reconciled
  - Redis OOM-kill → engine survives; position state rebuilt from WAL on restart
  - WAL disk-full → engine halts gracefully (does NOT trade without WAL); Telegram alert fires

- **Memory audit**: 24h paper run with RSS monitoring (via `/metrics` endpoint or `psutil`); `max_rss_growth_pct` must be ≤ 5% over 24h

- **`reqMarketDataType(3)` audit**: verify all paper account IBKR data feeds call correct market data type (delayed permissioned)

- **Config hot-reload**: SIGHUP handler for safe config reload (no position impact; reload validated before applying)

- **`/metrics` endpoint**: Prometheus-style metrics for EC2 monitoring (equity, positions, mode, drawdown_tier, ouroboros_complete, rss_mb)

**Acceptance Tests (AT-180 to AT-194):**
- AT-180: SIGTERM drill — container killed mid-position → WAL written → restart → position recovered
- AT-181: WAL compaction runs; events > 30 days removed; active events preserved
- AT-182: Rate limiter: 100 req/s synthetic → throttled to 50 req/s (no Error 100)
- AT-183: Python bridge crash → engine logs `PythonBridgeFailed` → enters dry-run → no new orders
- AT-184: Dry-run mode: existing positions managed by Chandelier (exits still execute)
- AT-185: IBKR disconnect at 04:45 UTC → reconnect within 180s
- AT-186: Redis OOM-kill → engine restart → positions rebuilt from WAL (verified vs pre-kill state)
- AT-187: WAL disk-full → engine emits `WalDiskFull` event → halts new trading → Telegram alert
- AT-188: Polars Ouroboros: 5,000-ticker run ≤ 3.5GB RSS peak
- AT-189: 24h paper run RSS growth ≤ 5% (no memory leak)
- AT-190: `reqMarketDataType(3)` called for all paper account subscriptions
- AT-191: SIGHUP config reload: new config applied, no position impact
- AT-192: `/metrics` endpoint returns valid Prometheus text format
- AT-193: Dead-letter WAL events > 0 → Telegram alert fires
- AT-194: Heartbeat interval maintained during chaos tests (no false watchdog alerts during reconnect)

**Gate**: 15 tests pass; 48h continuous paper run without HALT; WAL compacted size manageable; RSS stable over 24h; all chaos scenarios recovered

---

### ██ PHASE 23 — Crucible: 7-Suite Verification
**Hours**: 40h | **Depends on**: Phase 22

**Rationale**: Formal proof of correctness before any live capital. Nothing negotiable.

**Deliverables (7 test suites):**

1. **Suite 1 — Romano-Wolf 100-Trade Gate**
   - WR ≥ 40% on last 100 paper trades
   - t-stat ≥ 2.0 (Romano & Wolf StepM with N=20 Bonferroni correction)
   - Sharpe (cost-adjusted) > 0
   - Zero HALT events triggered by system errors (vs market conditions)
   - Max drawdown < 8%
   - ✓ Pass / ✗ Fail criteria documented; no partial credit

2. **Suite 2 — SIGTERM Flatten Drill**
   - Kill container mid-position (3 open positions)
   - Verify: flat on restart, WAL consistent, no orphan positions
   - Repeat 5 times (different position states)

3. **Suite 3 — 48h Paper Shadow Run**
   - Continuous paper trade for 48 hours (full MODE A→DARK→B→B+→C→DARK cycle ×2)
   - Shadow book vs broker: max divergence < £5 at any point
   - All 7 mode transitions logged with latency < 50ms

4. **Suite 4 — Chaos Engineering**
   - IBKR connection kill (mid-order) → recovery and WAL reconciliation
   - Python bridge kill (mid-signal) → dry-run mode, no loss of position state
   - Redis kill (mid-session) → WAL rebuild, positions intact on restart
   - All 3 chaos scenarios verified in sequence within 1 session

5. **Suite 5 — ISA Compliance Audit**
   - Generate 200 synthetic order intents (randomised tickers from all exchanges)
   - Verify: 0 short orders, 0 Taiwan/China/India tickers, 0 exceeding £20k annual limit
   - Verify: corporate action veto fires for synthetic spin-off ticker (via blocklist)
   - Generate audit report: `isa_compliance_audit.json`

6. **Suite 6 — Line Budget Stress Test**
   - proptest: 1,000 random subscription sequences with random mode transitions
   - Assert: `active_line_count <= 100` invariant NEVER violated
   - Assert: Scanner Conservation Rule holds (HotScanner candidates → 0 underlying lines)
   - Assert: Position open/close correctly triggers underlying subscribe/unsubscribe

7. **Suite 7 — Full Mode Cycle**
   - 24h paper run covering: ModeA → DARK → ModeB → ModeB+ → ModeC → DARK
   - Verify: each mode transition logged + latency < 50ms
   - Verify: DST boundary handled correctly (tested on BST/GMT boundary date)
   - Verify: NZX subscriptions begin at 23:00 UTC (MODE A open)
   - Verify: Ouroboros completes all 9 steps within DARK window

**Gate**: All 7 suites pass with written sign-off. 100 validated paper trades on record. No P0 bugs open. Manual review of WAL completeness. **APPROVED FOR LIVE CAPITAL** stamp.

---

## PART 4 — SUMMARY

### Phase Summary Table

| Phase | Name | Hours | Status | Test Range |
|-------|------|--------|--------|-----------|
| 1-7 | V2 Core Engine | ~200h | **COMPLETE ✓** | 147+ (all passing) |
| **8** | Pre-Conditions + P0 + Gemini mandates (SC-01→SC-12) | 34h | **NEXT** | Unit tests per SC item |
| **11** | 5-Mode Clock + SubscriptionManager + Conservation Rule | 20h | NOT STARTED | AT-01→16 |
| **12** | Smart Router + ISA Gate + snapshot=True + blocklist veto | 15h | NOT STARTED | AT-17→30 |
| **13** | HotScanner + RotationScanner (5s time-EWMA OFI) | 20h | NOT STARTED | AT-31→47 |
| **14** | Infinite Chandelier (floor fix) + Executioner V2 (U-TWAP, filled dust) | 20h | NOT STARTED | AT-48→64 |
| **15** | RiskGate 31 Vetoes + CVaR Cornish-Fisher | 15h | NOT STARTED | AT-65→78 |
| **16** | Ouroboros + Polars + Yellow escalation + corp_action_blocklist | 20h | NOT STARTED | AT-79→94 |
| **17** | Telemetry Stack (long-polling + heartbeat + PDFs) | 12h | NOT STARTED | AT-95→109 |
| **18** | European Equities + FTT + adaptive VPIN | 18h | NOT STARTED | AT-110→130 (+5 paper days) |
| **19** | Asia-Pac MODE A + NZX enabled + KRX VI | 18h | NOT STARTED | AT-131→149 |
| **20** | Carry State Machine (reqPnL + HALTED + holiday) | 20h | NOT STARTED | AT-150→169 |
| **21** | Cross-Timezone Intelligence (DCC-GARCH adaptive) | 12h | NOT STARTED | AT-170→179 (+5 paper days) |
| **22** | Institutional Hardening (Polars RSS + chaos tests) | 25h | NOT STARTED | AT-180→194 (+48h run) |
| **23** | Crucible: 7-Suite Verification | 40h | NOT STARTED | 7 suites + 100 trades |
| **TOTAL REMAINING** | | **312h** | | **AT-01→AT-194 = 194 tests** |

**At 20h/week**: ~15.6 weeks to live capital
**At 40h/week**: ~7.8 weeks to live capital

*312h = 310h (v18 baseline) + 2h (SC-12 symbology mapper)*

---

### Drawdown Tier Reference (Nomenclature Fixed — Gemini GEM-A5)

| Tier | Kelly | New Entries | Existing Positions | Trigger |
|------|-------|-------------|-------------------|---------|
| NORMAL | 100% | ✓ Allowed | Managed normally | Default |
| **YELLOW** | 50% | ✗ Blocked | Managed normally | Ouroboros failure; drawdown -3% |
| **ORANGE** | 0% | ✗ Blocked | Close all positions | Drawdown -5% |
| **RED** | 0% | ✗ Blocked | Full halt (no exits) | Drawdown -8%; manual resume only |

---

### New Files Created in Phases 8-23

```
rust_core/src/
├── subscription_manager.rs    (Phase 8/11)
├── mode_controller.rs         (Phase 11)
├── smart_router.rs            (Phase 12)
├── isa_gate.rs                (Phase 12)
├── hot_scanner.rs             (Phase 13)
├── rotation_scanner.rs        (Phase 13)
├── universe_scanner.rs        (Phase 13)
├── executioner_v2.rs          (Phase 14)
├── spread_veto.rs             (Phase 14)
├── cvar_heat.rs               (Phase 15)
├── overnight_carry.rs         (Phase 20)
├── currency.rs                (Phase 18)
├── exchange_profile.rs        (Phase 18)
├── transaction_tax.rs         (Phase 18)
├── sub_universe_allocator.rs  (Phase 18)
└── asian_exchange.rs          (Phase 19)

python_brain/
├── ouroboros/data_fetch.py    (Phase 16)
├── ouroboros/symbology_mapper.py  (Phase 8 — NEW v19-FIX-2)
├── telegram_reporter.py       (Phase 17)
├── pdf_generator.py           (Phase 17)
├── shadow_book.py             (Phase 17)
├── cross_timezone.py          (Phase 21)
└── asia_universe.py           (Phase 21)

config/
├── european_exchange_profiles.toml  (Phase 18)
├── european_routing_table.toml      (Phase 18)
├── transaction_tax.toml             (Phase 18)
├── asian_exchange_profiles.toml     (Phase 19)
└── asian_routing_table.toml         (Phase 19)

calibration/
├── weights.json               (Ouroboros step 9)
├── asia_cross_tz.json         (Ouroboros step 8)
└── corp_action_blocklist.json (Ouroboros step 2a — NEW v19-FIX-6)
```

---

## TERMINAL KICKOFF PROMPT (Phase 8)

Paste this into a new Claude Code terminal session to begin Phase 8 implementation:

```
Begin Phase 8 of AEGIS_MASTER_PLAN_v19.md. Reference file:
/Users/rr/nzt48-signals/nzt48-aegis-v2/docs/AEGIS_MASTER_PLAN_v19.md

Implement all 12 SC items in order. Write unit tests for each. Run cargo test after each SC item before proceeding to the next.

SC-01: SIGTERM handler in main.rs — ctrlc crate, flatten positions → wait 30s for fills → write SystemShutdown WAL event → exit
SC-02: SubscriptionManager skeleton in subscription_manager.rs — Mutex-guarded, cancel→ACK→subscribe deterministic protocol; confirmation via AtomicUsize line counter NOT 2-second silence heuristic
SC-03: LineBudget struct {carry: usize, active: usize, scan: usize} with hard assert!(carry + active + scan <= 100) in subscription_manager.rs
SC-04: Two-tier data architecture: (a) ibkr_broker.rs token bucket 60 req/10min, max 6 concurrent, exponential backoff on Error 162 for active ~100 tickers; (b) ouroboros/data_fetch.py uses Polygon.io for nightly 5000+ ticker universe screening (IBKR cannot serve this — math: 720 max IBKR pulls per 2h DARK window < 5000 tickers needed)
SC-05: MINIMUM_ENTRY_GBP: f64 = 1500.0 pre-entry gate in risk_arbiter.rs — veto OrderIntent before Kelly submission if GBP equivalent < 1500
SC-06: Dust guard in exit_engine.rs — CHECK THE FILLED PORTION: if filled_gbp < 500.0 (the shares already purchased) → submit market-sell immediately to liquidate dust; cancel the unfilled remainder separately regardless of size; do NOT check the unfilled remainder for dust
SC-07: Fix V1 S3 contradiction — in /Users/rr/nzt48-signals/strategies/mean_reversion.py remove the V2.1 reactivation comment that contradicts the hard ETP veto; the veto is correct
SC-08: APScheduler audit in /Users/rr/nzt48-signals/main.py — find all APScheduler jobs that fire before LSE open and verify they use timezone="Europe/London" not timezone="UTC"
SC-09: crossbeam-channel bounded ring buffer (capacity=50000) in python_bridge.rs replacing blocking subprocess call per tick — Rust writes asynchronously via try_send; dedicated Python reader thread pulls batches; TrySendError::Full → drop oldest tick, increment overflow counter logged to WAL
SC-10: Internal cost-basis tracker in portfolio.rs — HashMap<TickerId, CostBasisEntry> updated on every OrderFilled WAL event; overrides reqPositions cost basis for same-day trades (IBKR T+2 settlement lag makes reqPositions unreliable for intraday cost basis)
SC-11: SubscriptionManager active_line_count: AtomicUsize — increment on reqMktData ACK receipt, decrement on cancelMktData ACK receipt; assert!(active_line_count.load(Ordering::SeqCst) < 100) before every new reqMktData call
SC-12: symbology_mapper.py in ouroboros/ — maps IBKR canonical symbol to Polygon.io format; rules: (a) IBKR space to Polygon dot: "BRK B" → "BRK.B"; (b) IBKR LSE suffix to Polygon prefix: "NVD3.L" → "LSE:NVD3"; (c) IBKR exchange-prefixed pass-through: "XETRA:NVD3" → "XETRA:NVD3"; (d) ALL data_fetch.py Polygon API calls go through SymbologyMapper.to_polygon(ibkr_symbol); add unit tests for all 3 rule types

After all 12 items have passing tests:
- Run cargo test (all tests must pass)
- Run docker build (must succeed)
- Run a 30-minute paper session to verify SC-01 SIGTERM drill end-to-end

Do NOT start Phase 11 until Phase 8 gate is fully signed off.
```

---

*AEGIS_MASTER_PLAN_v19.md — Generated 2026-03-09*
*Supersedes: AEGIS_MASTER_PLAN_v18.md*
*Sources: PHASE_11_DIRECT_EQUITY_SPEC.md, PHASE_12_EUROPEAN_EQUITY_SPEC.md, PHASE_13_ASIA_PACIFIC_SPEC.md, GEMINI_TRIAGE.md, AEGIS_SELF_ANALYSIS_TRIAGE.md*
*Gemini adversarial audit: 200 bullets triaged; 5 binding mandates injected; 3 fatal flaws fixed; 6 implementation traps fixed (v19)*
