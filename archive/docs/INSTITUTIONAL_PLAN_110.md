# NZT-48 INSTITUTIONAL PLAN 110/100

**Version**: 1.0
**Date**: 2026-02-27
**Author**: Institutional Desk Engineering Team
**Status**: APPROVAL-READY
**Mode**: PAPER (£10,000 UK ISA)
**Objective**: Transform NZT-48 from a functional paper trading engine into an institutional-grade desk operating system that emits multiple high-quality intraday signals, with War Room + Telegram + PDFs perfectly aligned.

---

## TABLE OF CONTENTS

1. [Executive Summary](#1-executive-summary)
2. [Architecture Principles](#2-architecture-principles)
3. [A: Single Source of Truth](#3-a-single-source-of-truth)
4. [B: Universe Architecture + CORE Expansion](#4-b-universe-architecture--core-expansion)
5. [C: Opportunity Scanner](#5-c-opportunity-scanner)
6. [D: CORE Exit Engine + Batch Sell](#6-d-core-exit-engine--batch-sell)
7. [E: Real-Time Emission Reliability](#7-e-real-time-emission-reliability)
8. [F: Telegram Desk Tape](#8-f-telegram-desk-tape)
9. [G: War Room Manager Cockpit](#9-g-war-room-manager-cockpit)
10. [H: PDF Desk Notes + Daily PDF Set](#10-h-pdf-desk-notes--daily-pdf-set)
11. [I: Ops Runbook 92→100](#11-i-ops-runbook-92100)
12. [J: Auto-Open PDF](#12-j-auto-open-pdf)
13. [110/100 Additions](#13-110100-additions)
14. [Acceptance Tests](#14-acceptance-tests)
15. [Rollback Plan](#15-rollback-plan)
16. [Proof Artifacts](#16-proof-artifacts)

---

## 1. EXECUTIVE SUMMARY

### Current State
- 75,000+ line Python/Next.js codebase with 15 strategies, 3 bots, 12 CORE ISA ETPs
- Paper mode functional: signals fire, PDFs generate, Telegram sends, War Room renders
- 30/30 GO_LIVE_CHECKLIST items PASS; 34/61 improvements implemented

### Target State (110/100)
- **CORE-first** multi-signal intraday desk with MAX_INTRADAY_GAINS objective
- **OPPORTUNITY lane** banking +2% net after fees daily
- **FULL_SCAN** as INTEL-only with strict promotion gates
- **Single truth source** (DataHub → Regime → DataHealth) consumed identically by War Room, Telegram, PDFs
- **War Room** as fund-manager-ready single operational cockpit
- **6 daily PDFs** (Overnight Risk, PRE_LSE, PRE_NYSE, Mid-Session, EOD, Master Spec)
- **Deterministic replay**, incident drills, cost calibration, edge gating
- **Zero internal contradictions** across all delivery surfaces

### Key Deliverables
1. `docs/INSTITUTIONAL_PLAN_110.md` — This document
2. Code changes across ~30 files
3. `reports/FINAL_AUDIT_READY_FOR_MORNING.md` — Post-execution audit
4. `reports/DELIVERY_BATCH_PROOF.md` — 3 preview PDFs with checksums
5. `docs/OPS_PUSH_92_TO_100.md` — Operational runbook
6. Updated test suite with institutional coverage

---

## 2. ARCHITECTURE PRINCIPLES

### CORE-First Focus
- **CORE objective**: MAX_INTRADAY_GAINS (uncapped) using exit intelligence, trailing stops, partial exits
- **OPPORTUNITY objective**: BANK +2% NET AFTER FEES on volatile daily candidates (long OR short via inverse ETPs)
- **FULL_SCAN**: INTEL-only; cannot satisfy CORE/OPPORTUNITY quotas; promotion requires explicit gates

### Single Truth Artifact Chain
```
DataHub.get_bars()
    ↓
SignalEngine.run() → plays.json (CORE) + peers_intel.json (PEER) + full_scan.json (INTEL)
    ↓
artifacts/{date}/{session}/ ← SINGLE WRITE POINT
    ↓
┌──────────────┬──────────────┬──────────────┐
│   War Room   │   Telegram   │     PDFs     │
│ (reads JSON) │ (reads JSON) │ (reads JSON) │
└──────────────┴──────────────┴──────────────┘
```

**Rule**: War Room, Telegram, and PDFs MUST consume the SAME artifact files. No recomputation. No separate data paths.

### Lane Separation
| Lane | Tier | Objective | Decision | Tradeable |
|------|------|-----------|----------|-----------|
| CORE | A | MAX_INTRADAY_GAINS | TRADE | Yes |
| CORE_EXPANSION | B | MAX_INTRADAY_GAINS | TRADE (after validation) | Yes |
| OPPORTUNITY | C | +2% NET AFTER FEES | WATCH→TRADE | Yes (with gates) |
| FULL_SCAN | D | INTEL | INTEL only | No (promotion required) |

### No Silent Failures
Every failure must:
1. Have a reason code (human-readable + machine-parseable)
2. Have an operator action recommendation
3. Be logged to artifacts
4. Appear in War Room diagnostics
5. Fire Telegram HEALTH alert if severity ≥ WARN

---

## 3. A: SINGLE SOURCE OF TRUTH

### 3.1 DataHub as Only Market Data Layer

**Current state**: `signal_engine/engine.py` calls `yf.download()` directly (line ~549). PDFs also fetch independently.

**Change**: Route ALL market data through `data_hub/hub.py`:

**Files to modify**:
- `signal_engine/engine.py` — Replace direct yfinance calls with `DataHub.get_bars()`
- `delivery/pdf_v2_momentum.py` — Replace yfinance calls with DataHub
- `delivery/pdf_v2_risk.py` — Replace yfinance calls with DataHub
- `delivery/pdf_v2_daily_review.py` — Replace yfinance calls with DataHub
- `delivery/mega_report.py` — Replace yfinance calls with DataHub
- `command_center/server.py` — Replace VIX/SPY yfinance cache with DataHub

**DataHub enhancements**:
- Add `get_bars_batch(tickers, period, interval)` for efficient multi-ticker fetch
- Add retry logic: 3 attempts with exponential backoff (1s, 2s, 4s)
- Add `get_last_fetch_time()` for staleness tracking
- Add `get_source_health()` returning per-source availability + latency

**Acceptance test**: `grep -r "yf.download\|yfinance.download" --include="*.py" | grep -v "data_hub\|venv\|test"` returns 0 results outside DataHub.

### 3.2 Unified RegimeState

**Current state**: Multiple regime sources (feeds/regime_classifier.py, uk_isa/volatility_regime.py, command_center tick_loop regime detection). "UNKNOWN" appears when modules disagree.

**Change**: Single `RegimeState` computed once per tick, stored in shared state:

**New file**: `core/regime_provider.py`
```python
class RegimeProvider:
    """Single source of regime truth. Computed once per tick."""

    def get_regime(self) -> RegimeState:
        """Returns current regime. Never UNKNOWN unless SystemState is DEGRADED/HALTED."""

    def get_regime_with_evidence(self) -> RegimeEvidence:
        """Returns regime + all contributing indicators + confidence."""
```

**Files to modify**:
- `command_center/tick_loop.py` — Use RegimeProvider instead of inline detection
- `signal_engine/engine.py` — Accept regime from RegimeProvider
- `scheduled_jobs.py` — Pass RegimeProvider regime to pipeline

**Rule**: `UNKNOWN` regime is only valid when `SystemState != OK`. If system is OK, regime MUST resolve to one of 8 states.

### 3.3 Unified DataHealth Verdict

**Current state**: `uk_isa/data_health.py` and `signal_engine/gates.py` both run health checks independently. Can produce different verdicts.

**Change**: Single `DataHealthProvider` that runs once, caches result:

**New file**: `core/data_health_provider.py`
```python
class DataHealthProvider:
    """Runs data health checks once per tick cycle, caches results."""

    def check_all(self, tickers: list[str]) -> DataHealthSummary:
        """Run 8-check validation on all tickers. Cache for current tick."""

    def get_ticker_health(self, ticker: str) -> DataHealthResult:
        """Return cached result for single ticker."""
```

**Rule**: No module may run its own data health checks. All consumers call DataHealthProvider.

### 3.4 Canonical Schemas with Validation

**New file**: `core/schemas.py`

Define Pydantic-validated canonical schemas for:
- `SignalRecord` — Full signal with all 15 blocks from SIGNAL_TRUTH_TABLE
- `PlayCard` — Rendered play for War Room / Telegram / PDF
- `DroughtReport` — Closest misses, recommended knobs, blockers
- `DataHealthReport` — Per-ticker + aggregate health
- `RegimeSnapshot` — Regime tag + confidence + evidence
- `SystemStateReport` — OK/DEGRADED/HALTED + reasons
- `TelegramEvent` — Message type, content hash, delivery status
- `ScanHealth` — tick_count, engine_runs, signals_emitted, last_success_ts

**Validation**: All artifact writes go through schema validation. Invalid data raises `SchemaValidationError` (logged, not silently dropped).

**Acceptance test**: All artifact JSON files pass schema validation. `python -m core.schemas --validate artifacts/` returns 0 errors.

---

## 4. B: UNIVERSE ARCHITECTURE + CORE EXPANSION

### 4.1 Tiered Universe

| Tier | Name | Size | Scan Cadence | Purpose |
|------|------|------|-------------|---------|
| A | CORE | 12 | Every tick (30s) | Primary trade candidates |
| B | CORE_EXPANSION | 20 | Every 5 min | Validated expansion (LONG + SHORT) |
| C | OPPORTUNITY | Top 20 daily | Every 15 min | 2% net target candidates |
| D | FULL_SCAN | ~100+ | Hourly | INTEL only |

### 4.2 CORE Expansion: +20 Verified ISA-Eligible Leveraged Funds

**Method**: Research existing LSE leveraged ETPs, verify via yfinance `.L` suffix, check liquidity proxies (volume, spread estimates).

**Proposed additions (10 LONG + 10 SHORT/INVERSE)**:

**LONG leveraged (3x-5x)**:
| Ticker | Leverage | Underlying | Rationale |
|--------|----------|-----------|-----------|
| AMD3.L | 3x | AMD | Semiconductor peer to NVD3.L |
| ARM3.L | 3x | ARM Holdings | AI chip designer |
| AVGO3.L | 3x | Broadcom | Networking + AI infra |
| PLTR3.L | 3x | Palantir | AI/data analytics |
| META3.L | 3x | Meta | Mag7 + AI capex |
| AMZN3.L | 3x | Amazon | Cloud + retail |
| MSFT3.L | 3x | Microsoft | AI + enterprise |
| AAPL3.L | 3x | Apple | Mag7 anchor |
| 3LDE.L | 3x | DAX | European exposure |
| 3LIT.L | 3x | FTSE MIB | European diversification |

**SHORT/INVERSE leveraged (3x)**:
| Ticker | Leverage | Underlying | Rationale |
|--------|----------|-----------|-----------|
| NVDS.L | -3x | NVIDIA | Inverse of NVD3.L |
| TSLS.L | -3x | Tesla | Inverse of TSL3.L |
| AMDS.L | -3x | AMD | Inverse of AMD3.L |
| ARMS.L | -3x | ARM Holdings | Inverse of ARM3.L |
| 3SUS.L | -3x | S&P 500 | Broader US short |
| SC3S.L | -3x | Semiconductors | Sector inverse |
| MG3S.L | -3x | Mag7 | Mag7 inverse |
| GPTS.L | -3x | AI Index | AI inverse |
| 3SDE.L | -3x | DAX | European inverse |
| 3SIT.L | -3x | FTSE MIB | European inverse |

**Verification process**:
1. Attempt `yf.download(ticker, period="5d")` for each
2. Check: data returned? volume > 0? price > 0?
3. Mark as VERIFIED (data OK), PROPOSED (no data, needs manual check), or DELISTED (empty/error)
4. Output verification report to `artifacts/universe/core_expansion_verification.json`

**Governance**: Universe changes require:
1. Verification report generated
2. Prompt user for approval before adding to active scan
3. New tickers start in CORE_EXPANSION (Tier B), promoted to CORE (Tier A) after 5 days of clean data

**Files to modify**:
- `uk_isa/isa_universe.py` — Add CORE_EXPANSION tier
- `uk_isa/universe_manager.py` — Add Tier B management
- `config/settings.yaml` — Add core_expansion section
- `config/universe.yaml` — Add new tickers

### 4.3 Universe Governance

**New file**: `core/universe_governance.py`
- Proposal workflow: generate proposal → validate → prompt user → apply
- Auto-delist: 3 consecutive days of empty data → move to SUSPENDED
- Auto-promote: 5 days clean data in CORE_EXPANSION → eligible for CORE

---

## 5. C: OPPORTUNITY SCANNER

### 5.1 Daily Top 20 Candidates

**New file**: `strategies/opportunity_scanner.py`

```python
class OpportunityScanner:
    """Scans full universe for +2% NET AFTER FEES candidates."""

    OBJECTIVE = "+2% NET AFTER FEES"
    MAX_CANDIDATES = 20
    DEFAULT_DECISION = "WATCH"  # Never auto-TRADE

    def scan(self, bars_batch: dict, regime: RegimeState) -> list[OpportunityCandidate]:
        """
        For each ticker:
        1. Compute ATR% (must be >= 3% for 2% net feasibility)
        2. Compute spread + slippage cost (from CostModel)
        3. Compute net_target = 2.0% + round_trip_cost_pct
        4. Compute feasibility_score = f(ATR%, momentum, regime_fit, liquidity)
        5. Compute ExpectedNetR = P(target) × net_reward - P(stop) × risk
        6. Decision: WATCH (default) or TRADE (only if ExpectedNetR > 0 AND uncertainty < 0.3)
        """
```

**OpportunityCandidate schema**:
```python
@dataclass
class OpportunityCandidate:
    ticker: str
    direction: str  # LONG or SHORT (via inverse ETP)
    atr_pct: float
    spread_bps: float
    round_trip_cost_pct: float
    net_target_pct: float  # 2.0 + cost
    feasibility_score: float  # 0-100
    expected_net_r: float
    p_target: float
    uncertainty: float
    decision: str  # WATCH or TRADE
    execution_plan: dict
    why: str  # Plain English reasoning
```

**Integration**:
- Called from `scheduled_jobs.py` during each session
- Results written to `artifacts/{date}/{session}/opportunity.json`
- Consumed by War Room OPPORTUNITY tab, Telegram, PDFs

### 5.2 Cost-Aware Feasibility

The scanner uses `execution/cost_model.py` to compute actual round-trip costs:
```
net_target = 2.0% + (spread_bps + 2×slippage_bps + 2×platform_fee_bps) / 100
```

A ticker with 20bps spread needs to move 2.34% gross to net 2.0%.

---

## 6. D: CORE EXIT ENGINE + BATCH SELL

### 6.1 Exit Scoring

**New file**: `execution/exit_engine.py`

```python
class ExitEngine:
    """Track-aware exit scoring for open positions."""

    def score_exits(self, positions: list[VirtualPosition]) -> list[ExitScore]:
        """
        For each position:
        1. Current R-multiple
        2. Time in trade vs expected duration
        3. Regime alignment (still favorable?)
        4. Momentum degradation (RSI divergence, MACD cross)
        5. Kill conditions (stop hit, regime flip, factor overload)
        """

    def batch_sell_plan(self, sell_intents: list[SellIntent]) -> BatchSellPlan:
        """
        Group sells by:
        - Liquidity bucket (most liquid first)
        - Correlation (don't dump correlated at same time)
        - Market impact (stagger if > 5% of ADV)
        """
```

**ExitScore schema**:
```python
@dataclass
class ExitScore:
    position_id: str
    ticker: str
    current_r: float
    exit_score: float  # 0-100 (100 = sell now)
    kill_conditions: list[str]
    sell_intent: str  # HOLD, TRAIL, PARTIAL, EXIT_NOW
    reasoning: str
```

### 6.2 Integration with War Room

War Room Sell/Exit panel shows:
- All open positions with ExitScore
- Kill conditions highlighted in red
- Sell intents as action buttons
- Batch sell plan if multiple exits pending

---

## 7. E: REAL-TIME EMISSION RELIABILITY

### 7.1 ASAP Emission from Tick Loop

**Current state**: Signals computed in tick loop but only pushed to API state. Telegram alerts are diff-based (may miss signals if diff engine fails).

**Change**: Add explicit signal emission step in tick loop:

**Modify**: `command_center/tick_loop.py`
```python
async def _emit_signals(self, new_plays: list[PlayScore]):
    """Emit signals to all delivery surfaces ASAP."""
    # 1. Write to signal_log.jsonl (append-only)
    # 2. Push to Telegram (async, non-blocking)
    # 3. Update War Room state
    # 4. Update scan_health metrics
```

### 7.2 Signal Quotas

| Lane | Min Daily | Drought Threshold | Action on Drought |
|------|----------|-------------------|-------------------|
| CORE | 3 signals | < 3 by 14:00 UK | Drought report + closest misses |
| OPPORTUNITY | 5 candidates | < 5 by 14:00 UK | Relax ATR% gate to 2.5% |

### 7.3 Scan Health Heartbeat

**New file**: `core/scan_health.py`

```python
@dataclass
class ScanHealth:
    tick_count: int
    engine_runs: int
    signals_emitted: int
    signals_logged: int
    last_success_ts: str  # ISO timestamp
    last_error_ts: str | None
    last_error_msg: str | None
    state: str  # OK, DEGRADED, HALTED
    uptime_seconds: float
```

Written to `data/scan_health.json` every tick. Consumed by War Room cockpit.

### 7.4 Watchdog States

```
OK ─────→ All ticks < 120s, data < 5min, no errors
  ↓
DEGRADED → Tick stale OR data stale OR scan error
  ↓
HALTED ──→ Kill switch OR max daily loss OR 5 consecutive failures
```

Transition fires Telegram HEALTH alert with reason code and operator action.

---

## 8. F: TELEGRAM DESK TAPE

### 8.1 Message Labels

Every Telegram message gets a prefix label:

| Label | Meaning | Gate |
|-------|---------|------|
| `🟢 TRADE` | Actionable CORE signal | Score ≥ 70, all gates pass |
| `👀 WATCH` | Monitor candidate | Score 50-69 OR soft gate relaxed |
| `📊 INTEL` | Information only | FULL_SCAN tier |
| `🏥 HEALTH` | System status | State change or degradation |
| `📈 OPPORTUNITY` | 2% target candidate | Feasibility > 60 |
| `🚪 EXIT` | Sell/exit signal | ExitScore > 80 |

### 8.2 Hard Gates (No 0/None Score)

**Modify**: `delivery/telegram_bot.py`

Before sending any TRADE/WATCH message:
```python
def _validate_telegram_signal(self, play: PlayCard) -> bool:
    """Hard gate: no messages with score=0, score=None, or empty fields."""
    if play.composite_score is None or play.composite_score == 0:
        return False
    if play.ticker is None or play.direction is None:
        return False
    if play.entry is None or play.stop is None:
        return False
    return True
```

### 8.3 Dedupe

```python
class TelegramDedupe:
    """Prevent duplicate messages within time window."""

    _sent: dict[str, float]  # content_hash → timestamp
    DEDUPE_WINDOW = 300  # 5 minutes

    def should_send(self, content_hash: str) -> bool:
        """Return False if same hash sent within window."""
```

### 8.4 Rate Limiting + Spam Kill Switch

```python
MAX_MESSAGES_PER_MINUTE = 5
MAX_MESSAGES_PER_HOUR = 30
SPAM_KILL_THRESHOLD = 10  # per minute → auto-pause Telegram for 15min
```

### 8.5 Debug Log

All Telegram messages (sent + suppressed) written to `data/telegram_debug.jsonl`:
```json
{
    "ts": "2026-02-27T10:15:30Z",
    "action": "SENT|SUPPRESSED|RATE_LIMITED|DEDUPED|GATE_FAILED",
    "label": "TRADE",
    "ticker": "QQQ3.L",
    "content_hash": "abc123",
    "reason": null
}
```

---

## 9. G: WAR ROOM MANAGER COCKPIT

### 9.1 Feature Checklist (ALL MUST WORK)

#### Tab 1: LIVE COCKPIT (CORE-FIRST)

| Feature | Endpoint | Status |
|---------|----------|--------|
| Regime banner (tag + confidence + evidence) | `/api/regime` enhanced | NEW |
| SystemState (OK/DEGRADED/HALTED) + reasons | `/api/state` → systemState | WIRE |
| Scan SLA (tick_count, engine_runs, signals_emitted, last_success_ts) | `/api/scan_health` | NEW |
| CORE Top Trades (TRADE decisions only) | `/api/core_plays` | WIRE |
| CORE Watches (WATCH decisions) | `/api/core_plays?decision=WATCH` | NEW |
| Sell/Exit panel (ExitScore, kill conditions, sell intents) | `/api/exits` | NEW |

#### Tab 2: OPPORTUNITY LANE

| Feature | Endpoint | Status |
|---------|----------|--------|
| Top 20 candidates with 2% net feasibility | `/api/opportunity` | NEW |
| Execution plan per candidate | `/api/opportunity` (nested) | NEW |
| Decision WATCH/TRADE with reasoning | `/api/opportunity` (nested) | NEW |
| Clear "+2% NET AFTER FEES" label | Frontend label | NEW |

#### Tab 3: INTEL / FULL SCAN

| Feature | Endpoint | Status |
|---------|----------|--------|
| Broad scan results labeled INTEL | `/api/full_scan` | EXISTS |
| Clear "INTEL ONLY" badge | Frontend | WIRE |
| Promotion gate (strict requirements) | `/api/full_scan` (promotion_eligible field) | NEW |

#### Tab 4: GATES & DIAGNOSTICS

| Feature | Endpoint | Status |
|---------|----------|--------|
| Gate funnel counts + top blockers | `/api/funnel` | EXISTS |
| Closest misses with delta-to-pass | `/api/drought` | EXISTS |
| "Why not this ticker?" drilldown | `/api/ticker/{symbol}` enhanced | ENHANCE |

#### Tab 5: DATA HEALTH & PROVENANCE

| Feature | Endpoint | Status |
|---------|----------|--------|
| Unified DataHealth PASS/FAIL | `/api/health` enhanced | ENHANCE |
| Failed tickers + reasons | `/api/health` (per_ticker) | ENHANCE |
| Provider used + data_as_of + staleness | `/api/health` enhanced | NEW |
| Vendor disagreement panel | `/api/health` (validator_comparison) | NEW |

#### Tab 6: TELEGRAM + PDF CONSISTENCY

| Feature | Endpoint | Status |
|---------|----------|--------|
| Latest Telegram events + dedupe status | `/api/telegram/events` | NEW |
| Latest PDFs generated + checksums + paths | `/api/reports` enhanced | ENHANCE |
| Consistency check (same artifact source) | `/api/consistency` | NEW |

#### Tab 7: LEARNING / EDGE / DRIFT

| Feature | Endpoint | Status |
|---------|----------|--------|
| Outcome SLA panel | `/api/ai/signal_log_stats` enhanced | ENHANCE |
| Edge ledger readiness by bucket | `/api/ai/edge_map` enhanced | ENHANCE |
| Drift reports + defensive mode banner | `/api/ai/drift` | EXISTS |

#### Manager UX

| Feature | Status |
|---------|--------|
| One-Screen Operating Mode (60-second view) | NEW |
| Guided Mode + Glossary (≥30 terms) | EXISTS (in CC server.py) |
| Clickable rows → Trade Card modal | NEW |
| Trade Card: WHY + KILL + EXECUTION PLAN | NEW |
| Filters: lane, strategy, track, decision, confidence | NEW |
| No broken buttons, no JS errors | VERIFY |

### 9.2 Implementation Approach

**Backend** (command_center/server.py + dashboard/api.py):
- Add 8 new API endpoints
- Enhance 6 existing endpoints
- Add WebSocket event types for new data

**Frontend** (dashboard/frontend/):
- Restructure tabs: COCKPIT, OPPORTUNITY, INTEL, GATES, DATA, DELIVERY, LEARNING
- Add Trade Card modal component
- Add filter bar component
- Add one-screen summary mode
- Ensure all endpoints return 200 with valid data

### 9.3 War Room QA

After implementation:
1. Hit every endpoint, verify 200 response
2. Load frontend, verify no console errors
3. Click every tab, verify data renders
4. Click every row, verify Trade Card modal opens
5. Test all filters
6. Verify WebSocket receives updates
7. Generate QA report with screenshots

---

## 10. H: PDF DESK NOTES + DAILY PDF SET

### 10.1 New Daily PDF Schedule

| Time (UK) | PDF | Content |
|-----------|-----|---------|
| 06:30 | Overnight Risk & Macro Tape | Overnight futures, Asia close, macro events, VIX term structure |
| 07:00 | PRE_LSE Desk Notes | CORE plays, regime, data health, opportunity candidates |
| 13:30 | PRE_NYSE Desk Notes | Updated CORE plays, US pre-market, opportunity scanner |
| 16:40 | Mid-Session Risk Check | Open position review, exit scores, regime shift check |
| 22:00 | EOD Institutional Review | Full session review, P&L, strategy performance, learning |
| 00:00 | Master Spec of the Day | Complete system state, all artifacts, truth manifest |

### 10.2 New PDF Types

**New file**: `delivery/pdf_overnight_risk.py`
- Overnight futures (ES, NQ, FTSE)
- Asia close (Nikkei, HSI)
- Macro calendar for today
- VIX term structure (spot vs futures)
- Risk-on/risk-off assessment

**New file**: `delivery/pdf_mid_session.py`
- Open position status with exit scores
- Regime shift detection since morning
- P&L snapshot
- Remaining opportunity window

**New file**: `delivery/pdf_master_spec.py`
- Complete system state dump
- All artifacts inventory with checksums
- Configuration snapshot (hash + diff from baseline)
- Truth manifest: exact list of plays/signals that were displayed in War Room + sent via Telegram
- Academic bibliography appendix (mark "citation pending" if offline)

### 10.3 Fix Contradictions

**Rule**: All PDFs read from the SAME `artifacts/{date}/{session}/` directory. No independent data fetching for signal/play data.

**Modify all PDF generators** to accept pre-loaded artifact data:
```python
class MomentumPDFReport:
    def generate(self, session: str, artifacts: ArtifactBundle) -> str:
        """Generate PDF from pre-loaded artifacts. No independent data fetch for plays."""
```

### 10.4 Scheduled Jobs Update

**Modify**: `scheduled_jobs.py`

Add new scheduled windows:
```python
# 06:30 UK — Overnight Risk
scheduler.add_job(run_overnight_risk, CronTrigger(hour=6, minute=30, timezone="Europe/London"))

# 16:40 UK — Mid-Session Risk
scheduler.add_job(run_mid_session_risk, CronTrigger(hour=16, minute=40, timezone="Europe/London"))

# 00:00 UK — Master Spec
scheduler.add_job(run_master_spec, CronTrigger(hour=0, minute=0, timezone="Europe/London"))
```

### 10.5 Truth Manifest in Every PDF + Telegram

Every PDF includes a footer section:
```
─── TRUTH MANIFEST ───
Artifacts source: artifacts/2026-02-27/PRE_LSE/
plays.json hash: sha256:abc123...
Config hash: sha256:def456...
Engine version: 10.1
Run ID: RUN-20260227-070000
Generated: 2026-02-27T07:00:15Z
```

Every Telegram message includes truncated manifest:
```
📋 Run RUN-20260227-070000 | plays:abc1 | cfg:def4
```

---

## 11. I: OPS RUNBOOK 92→100

**Output**: `docs/OPS_PUSH_92_TO_100.md`

### Go/No-Go Gates for PAPER → LIMITED LIVE

| Gate | Criteria | Measurement |
|------|----------|-------------|
| G1: Data Reliability | 30 consecutive days score ≥ 0.85 | DataHealth daily average |
| G2: Signal Quality | Win rate ≥ 45% over 100+ resolved outcomes | Edge ledger aggregate |
| G3: System Uptime | 99%+ uptime over 30 days | ScanHealth metrics |
| G4: No HALTED Events | 0 HALTED states in last 14 days | SystemState log |
| G5: Cost Model Calibrated | Paper fills within 20% of expected slippage | Execution quality log |
| G6: Edge Stability | No strategy with stability < 0.5 (n≥20) | Edge ledger weekly delta |
| G7: Drawdown Recovery | Recovered from at least 1 YELLOW event | Drawdown history |
| G8: Kill Switch Tested | 3 successful kill switch tests (all methods) | Test log |
| G9: PDF Consistency | 0 contradictions in last 7 days of PDFs | PDF audit log |
| G10: Telegram Reliability | 99%+ delivery rate, 0 false TRADEs | Telegram debug log |

### Operational Steps

1. **Week 1-2**: Paper mode baseline (current)
2. **Week 3-4**: Tune gates, accumulate outcomes
3. **Week 5-8**: Edge ledger calibration (100+ outcomes per bucket)
4. **Week 9-10**: Simulated live drills (kill switch, feed outage, spam)
5. **Week 11-12**: Go/No-Go committee review
6. **Week 13+**: LIMITED LIVE (£1,000 max, 1 position max)

---

## 12. J: AUTO-OPEN PDF

**Modify**: `signal_engine/pipeline_runner.py`

After PDF generation:
```python
import subprocess, sys

def auto_open_pdf(pdf_path: str):
    """Best-effort open PDF locally."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", pdf_path])
        elif sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", pdf_path])
        else:
            logger.info(f"Auto-open not supported on {sys.platform}")
    except Exception as e:
        logger.warning(f"Auto-open failed: {e}")
```

---

## 13. 110/100 ADDITIONS

### 13.1 Deterministic Replay Mode

**New file**: `core/replay.py`

```python
class ReplayEngine:
    """Reproduce a session exactly from artifacts."""

    def replay(self, date: str, session: str) -> ReplayResult:
        """
        1. Load artifacts/{date}/{session}/
        2. Re-run engine with frozen inputs (bars, regime, config)
        3. Compare output plays with stored plays.json
        4. Report: MATCH (deterministic) or DIVERGE (non-determinism detected)
        """
```

**Key**: Engine must be deterministic given same inputs. Random seeds must be fixed. Timestamps must be replayable.

### 13.2 Incident Drill Scripts

**New file**: `scripts/incident_drills.py`

Three drill scenarios:
1. **Feed Outage**: Simulate yfinance returning empty data for 10 minutes
   - Expected: SystemState → DEGRADED, Telegram HEALTH alert, War Room banner
   - Verify: No signals emitted during outage, recovery after data returns

2. **Latency Spike**: Simulate 30s fetch latency
   - Expected: Tick loop extends, ScanHealth shows degraded
   - Verify: No duplicate signals, eventual recovery

3. **Telegram Spam**: Simulate 50 signals in 1 minute
   - Expected: Rate limiter kicks in, SPAM_KILL activates
   - Verify: Max 5 messages sent, rest queued/dropped

### 13.3 Cost Model Calibration Hooks

**Modify**: `execution/cost_model.py`

Add calibration interface:
```python
class CostModelCalibrator:
    """Compare paper fills vs expected costs."""

    def record_fill(self, ticker: str, expected_cost_bps: float, actual_slippage_bps: float):
        """Record actual vs expected for calibration."""

    def get_calibration_report(self) -> dict:
        """Return per-ticker calibration accuracy."""
```

### 13.4 Bucket Readiness Gating

**Modify**: `learning/edge_ledger.py`

Enforce: strategies can only receive full allocation if their edge bucket is ACTIONABLE (n≥20, stability≥0.5):

```python
def get_allocation_gate(self, strategy_tag: str, regime: str) -> float:
    """Return allocation multiplier based on edge readiness."""
    bucket = self.get_bucket(strategy_tag, regime)
    if bucket.status == "ACTIONABLE":
        return 1.0
    elif bucket.status == "CALIBRATION_READY":
        return 0.5  # Half allocation
    else:
        return 0.25  # Minimal allocation
```

### 13.5 One Truth Table Manifest

Every PDF, Telegram message, and War Room state includes:
```json
{
    "truth_manifest": {
        "run_id": "RUN-20260227-070000",
        "plays_hash": "sha256:...",
        "config_hash": "sha256:...",
        "engine_version": "10.1",
        "artifact_dir": "artifacts/2026-02-27/PRE_LSE/",
        "generated_at": "2026-02-27T07:00:15Z"
    }
}
```

### 13.6 Hard Guarantee: Same Artifacts

**Implementation**: `core/artifact_loader.py`

```python
class ArtifactLoader:
    """Load artifacts once, serve to all consumers."""

    _cache: dict[str, dict] = {}

    def load_session(self, date: str, session: str) -> ArtifactBundle:
        """Load all artifacts for a session. Cached."""

    def get_plays(self) -> list[PlayCard]:
        """Return plays from cached bundle."""

    def get_manifest(self) -> TruthManifest:
        """Return truth manifest for current bundle."""
```

**Rule**: War Room reads from ArtifactLoader. Telegram reads from ArtifactLoader. PDFs read from ArtifactLoader. No other data path exists for signal/play data.

### 13.7 Additional 110/100 Improvements

1. **Config Version Stamping**: Every artifact includes `config_hash` computed from settings.yaml
2. **Run Manifest**: Every engine run produces `run_manifest.json` with inputs, outputs, timing
3. **As-Of Timestamps**: Every data point includes `data_as_of` timestamp
4. **Provenance Chain**: Each artifact links to its input artifacts
5. **Execution Realism**: All signals include explicit execution plan (order type, max slippage, spread gate, time limit)
6. **Tradeability Scoring**: Separate score for "can this actually be executed profitably" vs "is the signal good"

---

## 14. ACCEPTANCE TESTS

### Unit Tests (New)

| Test | Validates |
|------|-----------|
| `test_datahub_only` | No yfinance calls outside DataHub |
| `test_regime_no_unknown` | RegimeProvider never returns UNKNOWN when system OK |
| `test_data_health_single_source` | DataHealthProvider is only health checker |
| `test_schema_validation` | All artifact writes pass schema validation |
| `test_opportunity_scanner` | Top 20 candidates with valid fields |
| `test_exit_engine` | Exit scores for mock positions |
| `test_telegram_dedupe` | Duplicate messages suppressed |
| `test_telegram_rate_limit` | Rate limiting enforced |
| `test_telegram_hard_gates` | No 0/None score messages |
| `test_scan_health_heartbeat` | ScanHealth updates every tick |
| `test_signal_quotas` | Drought triggered when below minimum |
| `test_truth_manifest` | Manifest present in artifacts |
| `test_artifact_consistency` | War Room/Telegram/PDF read same files |
| `test_core_expansion_verification` | New tickers validated |
| `test_cost_model_populated` | Spread BPS used in execution plans |

### Integration Tests (New)

| Test | Validates |
|------|-----------|
| `test_full_pipeline_no_contradiction` | End-to-end: scan → emit → artifacts → PDF |
| `test_replay_determinism` | Same inputs → same outputs |
| `test_incident_feed_outage` | Graceful degradation on feed failure |
| `test_pdf_reads_artifacts` | PDFs only read from artifact dir |
| `test_war_room_endpoints_200` | All War Room endpoints return 200 |

### QA Tests (War Room)

- Playwright click-through of every tab
- Screenshot capture of every tab and modal
- Console error capture
- Network error capture
- P0/P1/P2 defect report

---

## 15. ROLLBACK PLAN

### Pre-Execution Backup
```bash
cd /Users/rr/nzt48-signals
git add -A && git commit -m "PRE-110 BACKUP: $(date +%Y%m%d_%H%M%S)"
```

### Rollback Steps
1. `git stash` current changes
2. `git checkout pre-110-backup` (the backup commit)
3. Restart engine: `docker-compose restart nzt48`
4. Verify: `curl http://localhost:8000/api/health`

### Partial Rollback
Each module change is independent. Can revert individual files:
- DataHub routing: revert `signal_engine/engine.py` to direct yfinance
- War Room tabs: revert `command_center/server.py` HTML
- Telegram changes: revert `delivery/telegram_bot.py`

---

## 16. PROOF ARTIFACTS

After execution, the following must exist:

### Code Artifacts
- [ ] `core/schemas.py` — Canonical schemas
- [ ] `core/scan_health.py` — Scan health heartbeat
- [ ] `core/regime_provider.py` — Single regime source
- [ ] `core/data_health_provider.py` — Single health source
- [ ] `core/artifact_loader.py` — Single artifact consumer
- [ ] `core/replay.py` — Deterministic replay
- [ ] `core/universe_governance.py` — Universe change governance
- [ ] `strategies/opportunity_scanner.py` — 2% net scanner
- [ ] `execution/exit_engine.py` — Exit scoring + batch sell
- [ ] `delivery/pdf_overnight_risk.py` — 06:30 PDF
- [ ] `delivery/pdf_mid_session.py` — 16:40 PDF
- [ ] `delivery/pdf_master_spec.py` — 00:00 PDF
- [ ] `scripts/incident_drills.py` — Drill scripts

### Documentation Artifacts
- [ ] `docs/INSTITUTIONAL_PLAN_110.md` — This plan
- [ ] `docs/OPS_PUSH_92_TO_100.md` — Ops runbook
- [ ] `reports/FINAL_AUDIT_READY_FOR_MORNING.md` — Final audit
- [ ] `reports/DELIVERY_BATCH_PROOF.md` — PDF batch proof

### Test Artifacts
- [ ] 15+ new unit tests passing
- [ ] 5+ new integration tests passing
- [ ] War Room QA report with screenshots

### Runtime Artifacts
- [ ] `data/scan_health.json` — Updated every tick
- [ ] `data/telegram_debug.jsonl` — All Telegram events
- [ ] `artifacts/universe/core_expansion_verification.json` — 20 new tickers verified
- [ ] 3 preview PDFs (PRE_LSE, PRE_NYSE, EOD) with checksums

---

## EXECUTION ORDER

1. **Backup** current codebase (git commit)
2. **Create** `core/` module with schemas, providers, health, replay
3. **Modify** DataHub routing (engine, PDFs, server)
4. **Add** universe expansion + verification
5. **Add** opportunity scanner
6. **Add** exit engine
7. **Upgrade** emission reliability + scan health
8. **Upgrade** Telegram (labels, dedupe, gates, debug log)
9. **Upgrade** War Room (new endpoints, new tabs, Trade Card modal)
10. **Add** new PDF types + fix contradiction path
11. **Add** 110/100 additions (replay, drills, calibration, manifest)
12. **Write** ops runbook
13. **Run** test suite
14. **Run** War Room QA
15. **Run** final audit → `reports/FINAL_AUDIT_READY_FOR_MORNING.md`
16. **Generate** 3 preview PDFs → `reports/DELIVERY_BATCH_PROOF.md`
17. **Auto-open** PDFs locally

---

*END OF INSTITUTIONAL PLAN 110/100*
*Prepared for institutional controls committee review.*
*All changes are PAPER MODE only. No live trading risk.*
