# NZT-48 War Room -- Manager Usability Standard

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-WRMS-001           |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- IC/PM UX Standard  |
| Related         | WAR_ROOM_REQUIREMENTS_SPEC.md (NZT48-ANNEX-003) -- 37-panel technical specification |

---

## 1. PURPOSE

WAR_ROOM_REQUIREMENTS_SPEC.md defines the 37-panel technical specification, API schemas, and wiring checks. This document defines the **manager usability standard**: what a fund manager or operator should be able to accomplish with the War Room, how quickly, and with what level of clarity.

The War Room is the operator's primary control surface during trading hours. It must be operable under stress, unambiguous in its presentation, and comprehensive in a single screen view.

---

## 2. ONE-SCREEN OPERATING MODE

### 2.1 Design Principle

The operator MUST be able to assess the complete system state without scrolling. On a standard 1920x1080 display at normal browser zoom, the following information is visible simultaneously:

| Zone | Position | Content | Update Frequency |
|------|----------|---------|-----------------|
| Header Bar | Top (fixed, 60px) | Engine status, WebSocket status, Market Regime badge, System Mode badge, Drought state, Kill Switch status, Clock (UTC + UK) | Real-time / 5s |
| System Wiring Bar | Below header (fixed, 50px) | 7 health indicators: DataHub, Engine, Artifacts, Telegram, PDF, Learning, Scheduler | 15s |
| Left Column (40%) | Main area left | Signal Feed (live), Virtual Positions, Opportunity Lane, Exit Scores | 5-15s |
| Center Column (30%) | Main area center | Regime panel, Performance aggregate, Profit Ladder, Equity chart | 15-60s |
| Right Column (30%) | Main area right | Scan Health, Telegram Desk Tape, Consistency Check, Drawdown Status | 5-15s |
| Footer Bar | Bottom (fixed, 30px) | Auto-refresh countdown, Go-Live Gate summary (pass/fail count), Last PDF QA status | 30s |

### 2.2 Critical Information Priority

At any moment, the operator's eyes should find the answer to these questions in under 3 seconds:

| Question | Where to Look | Visual Indicator |
|----------|--------------|-----------------|
| Is the system alive? | Header: Engine status | Green dot = alive; Red = dead |
| What regime are we in? | Header: Regime badge | Colour-coded badge with text |
| Are we in a drought? | Header: Drought badge | Grey = no drought; Yellow/Orange/Red = escalating |
| Is the kill switch on? | Header: Kill switch | No indicator = off; Red pulsing skull = on |
| Any open positions? | Left: Virtual Positions | Position cards with P&L |
| Current P&L today? | Center: Performance | Large number, green/red |
| Any active signals? | Left: Opportunity Lane | Candidate cards ranked by score |
| Any system problems? | Wiring Bar | Green/Amber/Red per subsystem |

---

## 3. GLOSSARY

A minimum of 30 terms that appear in the War Room, documented for operator training.

| # | Term | Definition | Where Shown |
|---|------|------------|-------------|
| 1 | **TRENDING_UP_STRONG** | Market regime: strong uptrend across multiple timeframes. Momentum strategies favoured. | Regime badge |
| 2 | **TRENDING_UP_MOD** | Market regime: moderate uptrend. Momentum strategies active with tighter stops. | Regime badge |
| 3 | **TRENDING_DOWN_STRONG** | Market regime: strong downtrend. Inverse ETPs favoured; long entries restricted. | Regime badge |
| 4 | **TRENDING_DOWN_MOD** | Market regime: moderate downtrend. Caution on longs; inverse ETPs watchlist. | Regime badge |
| 5 | **RANGE_BOUND** | Market regime: no clear trend. Mean-reversion strategies dormant (S3). Fewer opportunities expected. | Regime badge |
| 6 | **HIGH_VOLATILITY** | Market regime: elevated volatility across the universe. Wider stops required; position sizing reduced. | Regime badge |
| 7 | **RISK_OFF** | Market regime: systemic risk event detected. All new entries suspended. Existing positions reviewed for exit. | Regime badge (pulsing red) |
| 8 | **SHOCK** | Market regime: extreme dislocation (flash crash, black swan). All trading halted. Kill switch auto-activated. | Regime badge (pulsing animated red) |
| 9 | **COMPRESSION** | Vol regime: low volatility, narrow Bollinger Bands. Precedes potential breakout. Watchlist mode. | Ticker vol badge |
| 10 | **EXPANSION** | Vol regime: rising volatility, widening BBs. Active trading conditions. Signals more likely. | Ticker vol badge |
| 11 | **BLOW_OFF** | Vol regime: extreme volatility spike. Typically unsustainable. Caution on new entries. | Ticker vol badge |
| 12 | **EXHAUSTION** | Vol regime: high volatility decelerating. Potential regime transition imminent. | Ticker vol badge |
| 13 | **BREAKDOWN** | Vol regime: volatility collapse after blow-off. Risk of gap moves. Defensive positioning. | Ticker vol badge |
| 14 | **DROUGHT_NONE** | No signal drought. System generating candidates normally. | Header drought badge |
| 15 | **DROUGHT_WATCH** | Early drought: no qualifying signals for N ticks. Monitoring. No action required yet. | Header drought badge (yellow) |
| 16 | **DROUGHT_ACTIVE** | Active drought: extended period without qualifying signals. Check data quality and regime. | Header drought badge (orange) |
| 17 | **DROUGHT_CRITICAL** | Critical drought: prolonged no-signal state. Investigate immediately. Possible system fault. | Header drought badge (red) |
| 18 | **DROUGHT_CLEARED** | Drought broken: a qualifying signal was generated. Transitioning back to NORMAL. | Header drought badge (green flash) |
| 19 | **Composite Score** | Final weighted score (0-100) assigned to a signal by the multi-factor scoring engine. Higher = stronger conviction. | Signal cards, Opportunity Lane |
| 20 | **Confidence** | Separate from score: the qualification engine's confidence in the signal (0-100). Threshold for Telegram delivery: 60. | Signal cards |
| 21 | **Feasibility Score** | Opportunity Lane metric: how likely a candidate is to achieve the 2% target based on ATR, RVOL, and regime. | Opportunity Lane |
| 22 | **Exit Score** | Urgency score for closing an open position (0-100). 0-30=HOLD, 31-50=HOLD/MONITOR, 51-70=REDUCE, 71-85=EXIT, 86-100=URGENT_EXIT. | Exit Scores panel |
| 23 | **R-Multiple** | Profit/loss expressed as multiples of risk. 1R = target achieved. 2R = doubled target. -1R = stopped out. | Trade performance |
| 24 | **MFE** | Maximum Favourable Excursion: the best unrealised P&L a trade achieved before closing. Measures trade quality. | Trade autopsy |
| 25 | **MAE** | Maximum Adverse Excursion: the worst unrealised P&L a trade experienced. Measures heat taken. | Trade autopsy |
| 26 | **RVOL** | Relative Volume: today's volume divided by 20-day average volume. >1.5 = elevated interest. >2.0 = unusual activity. | Ticker data |
| 27 | **Circuit Breaker L1** | 1.5% daily drawdown threshold. Triggers: position sizing reduced, alert sent, monitoring increased. | Drawdown panel |
| 28 | **Circuit Breaker L2** | 2.5% daily drawdown threshold. Triggers: new entries suspended, existing positions reviewed, PM notified. | Drawdown panel |
| 29 | **Circuit Breaker L3** | 4.0% daily drawdown threshold. Triggers: kill switch auto-activated, all positions reviewed for exit, PM and IC notified. | Drawdown panel |
| 30 | **Kill Switch** | Master safety control. When active: all signal generation halted, no new positions, existing stops remain active. Three activation methods: Telegram, file, API. | Header (rightmost) |
| 31 | **LKG** | Last Known Good: a tagged snapshot of code, config, and Docker image that is known to work correctly. Rollback target. | System admin |
| 32 | **DEGRADED** | System operating mode when non-critical checks fail. Signal delivery restricted to [SYSTEM] and [CRITICAL] messages only. | Header mode badge |
| 33 | **HALTED** | System operating mode when critical checks fail. Only [CRITICAL] messages delivered. Requires manual intervention. | Header mode badge |
| 34 | **S15** | Strategy 15: "2% Daily Target". Scores all tickers by 2% reachability, selects the single best candidate per day. The compounding machine. | S15 panel |
| 35 | **Profit Ladder** | Stepped take-profit levels (rungs) for managing an open position. Each rung defines a partial exit at a predefined R-multiple. | Profit Ladder panel |

---

## 4. "WHAT TO DO NOW" PER METRIC

For each key metric displayed in the War Room, the operator has clear guidance based on colour state.

### 4.1 Colour-Action Matrix

| Metric | GREEN (No Action) | AMBER (Monitor) | RED (Act Now) |
|--------|-------------------|-----------------|---------------|
| **Engine Status** | Connected, tick loop active | Heartbeat >120s, investigate soon | Engine down or heartbeat >300s. SSH and restart. `docker-compose restart nzt48` |
| **WebSocket** | Connected | N/A (binary state) | Disconnected. Refresh browser. If persistent, check API server. |
| **Regime** | Valid regime displayed | N/A | "UNKNOWN" or null. Check regime engine logs. Likely data feed issue. |
| **Drought** | DROUGHT_NONE | DROUGHT_WATCH: No action yet, just awareness | DROUGHT_ACTIVE/CRITICAL: Check data feeds, check if market is genuinely quiet, verify scan engine is running |
| **Kill Switch** | Not displayed (inactive) | N/A | Active (red pulsing). Intentional? If not, deactivate: `/resume ALL` or `rm data/KILL_SWITCH` |
| **Data Health** | All tickers >= 80% | 1-2 tickers 50-79% | Any ticker < 50%. Check yfinance. Likely rate limited or ticker delisted. |
| **Drawdown** | < 1.5% daily loss | 1.5-2.5% (L1 breached). Position sizing reduced. | > 2.5% (L2/L3). No new entries. Review open positions for exit. |
| **Signal Quality** | Score > 30 average | Score 10-30 average (weak conviction) | Zero signals all session. If drought, investigate per drought guidance. |
| **Telegram Delivery** | 0 failures | 1-2 suppressed. Check dedupe stats. | >2 failures. Check bot token. Check rate limits. Check connectivity. |
| **PDF QA** | Last QA PASS | Last QA had WARN checks | Last QA FAIL. Review QA failure report. Check data sources. |
| **Scan SLA** | P95 cycle < 45s | P95 45-55s. Approaching budget. | P95 > 55s or tick loop dead. Performance investigation required. |
| **Consistency** | All 6 checks PASS | Any check WARN | Any check FAIL. Specific guidance depends on which check failed: see consistency panel detail. |

---

## 5. DRILLDOWN NAVIGATION

Every panel in the War Room supports click-to-expand detail views.

| Panel | Click Action | Detail View Contents |
|-------|-------------|---------------------|
| Signal Feed | Click signal card | Full signal detail: all scoring factors, gate verdicts, strategy parameters, OHLCV data |
| Virtual Positions | Click position card | Entry details, current P&L, MFE/MAE, exit score breakdown, profit ladder state |
| Opportunity Lane | Click candidate card | Full scoring breakdown, gate pass/fail detail, closest miss reason (if rejected) |
| Regime Panel | Click regime badge | Regime history (last 24h), transition timestamps, contributing factors (VIX, breadth, momentum) |
| Drawdown Status | Click drawdown number | Equity curve (intraday), per-trade contribution, circuit breaker history |
| Scan Health | Click any metric | Scan cycle timing distribution, per-tick error counts, strategy execution times |
| Telegram Desk Tape | Click event row | Full message content, gate results, dedupe decision, delivery timestamp |
| Consistency Check | Click check row | Check detail: source data values compared, expected vs actual, remediation guidance |
| System Wiring | Click indicator | Subsystem detail: last 10 health checks, trend, error log excerpt |

---

## 6. COLOUR CODING STANDARD

| Colour | Hex Code | Meaning | Usage |
|--------|----------|---------|-------|
| **Green** | #00E676 | Healthy / Pass / No action required | Status indicators, PASS badges, positive P&L |
| **Amber** | #FFA000 | Warning / Monitor / Approaching threshold | WARN badges, elevated metrics, approaching limits |
| **Red** | #FF5252 | Critical / Act now / Failure | FAIL badges, breached thresholds, errors, negative P&L |
| **Red (pulsing)** | #FF1744 | Emergency / Immediate action | Kill switch active, SHOCK regime, L3 circuit breaker |
| **Grey** | #9E9E9E | Inactive / Not applicable / Neutral | Disabled features, RANGE_BOUND regime, unavailable data |
| **Blue** | #448AFF | Informational / Link / Interactive | Clickable elements, hyperlinks, drilldown indicators |
| **White** | #FFFFFF | Default text on dark backgrounds | Standard content text |
| **Dark background** | #1A1A2E | Panel background | Default dark theme |

### Colour Usage Rules

1. Never use red and green adjacent without a shape differentiator (accessibility for colour-blind operators).
2. Pulsing animations are reserved for states requiring immediate human attention (kill switch, SHOCK, L3 breaker).
3. Grey means "no data" or "disabled" -- never "error" (that is red).
4. Amber means "approaching a threshold" -- the operator should be aware but no immediate action is required.

---

## 7. REFRESH REQUIREMENTS

| Update Method | Target Latency | Panels | Fallback |
|--------------|---------------|--------|----------|
| WebSocket push | < 2 seconds | Signal Feed, Virtual Positions, Scan Health (via `_internal/push_state`) | 5-second REST poll |
| REST poll (fast) | 5 seconds | Regime, Drawdown, Profit Ladder, Indicator Scores | 15-second if API slow |
| REST poll (medium) | 15-60 seconds | Performance, Kelly, PDT, Missed Trades, Autopsies, Firewall Events | Continue with stale data + indicator |
| REST poll (slow) | 60-300 seconds | Correlation Matrix, ISA Universe, Daily Summary, Premarket Briefs | Continue with cached data |
| On-demand | User-triggered | Operator Copilot (POST), Export functions | N/A |

### Refresh Failure Handling

- If a poll fails 3 consecutive times, the panel shows a yellow "Stale" indicator with time since last update.
- If a poll fails 10 consecutive times, the panel shows a red "Error" overlay with retry button.
- WebSocket disconnection triggers an exponential backoff reconnect (1s, 2s, 4s, 8s, max 30s).
- On WebSocket reconnect, all panels perform a burst refresh to re-sync state.

---

## 8. ACCEPTANCE TESTS

| Test ID | Scenario | Expected Result | Pass Criteria |
|---------|----------|-----------------|---------------|
| WRMS-T01 | Load War Room on 1920x1080 display; verify all critical information visible without scrolling | Header bar, wiring bar, three main columns, footer all visible | All zones from Section 2.1 visible simultaneously |
| WRMS-T02 | Identify current regime within 3 seconds of looking at screen | Regime badge prominently displayed with colour coding | Colour and text match the colour coding standard in Section 6 |
| WRMS-T03 | Click Signal Feed card; verify drilldown shows full scoring breakdown | Detail view opens with all scoring factors and gate verdicts | All fields from Section 5 drilldown specification present |
| WRMS-T04 | Trigger kill switch; verify red pulsing indicator appears in header within 5 seconds | Kill switch indicator visible and pulsing | Animation and colour match Section 6 pulsing red specification |
| WRMS-T05 | Disconnect WebSocket; verify "WS DISCONNECTED" indicator and auto-reconnect | Red broken chain icon appears; reconnect succeeds within 30 seconds | Per Section 7 refresh failure handling |
| WRMS-T06 | Verify all 35 glossary terms are accessible from the War Room (hover tooltips or help panel) | Hovering over any abbreviated term shows its definition | All terms from Section 3 accessible without leaving the War Room |
| WRMS-T07 | Verify colour-action matrix: set engine to stale (>120s heartbeat); confirm amber indicator and correct guidance | Engine status turns amber with "ENGINE STALE" text | Matches Section 4.1 amber guidance for Engine Status |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial manager usability standard |

---

*End of Document NZT48-ANNEX-WRMS-001*
