# NZT-48 Risk Constitution

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-RC-001             |
| Version         | 1.0                            |
| Status          | **BINDING**                    |
| Classification  | Internal -- Investment Committee |
| Effective Date  | 2026-02-27                     |
| Review Cadence  | Quarterly or after any L3 event |
| Owner           | PM / Risk Officer              |

---

## 0. System Context

The NZT-48 Leveraged ISA Intraday Trading System operates under the following parameters:

| Parameter               | Value                                                                    |
|--------------------------|--------------------------------------------------------------------------|
| Mode                     | Paper trading                                                            |
| Starting equity          | £10,000                                                                  |
| Instrument universe      | Leveraged ETPs on the London Stock Exchange (3x/5x products)             |
| Active tickers           | QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L |
| Daily target             | 2% equity growth (compounding)                                          |
| Circuit breaker levels   | L1 = 1.5%, L2 = 2.5%, L3 = 4.0% daily drawdown                         |
| Max concurrent positions | 2 (paper), 3 (limited live)                                             |
| Kill switch              | Telegram, file-based, API                                               |
| ISA constraint           | No direct shorting; inverse ETPs (QQQS.L, 3USS.L) permitted as long positions |

---

## 1. Purpose and Authority

### 1.1 Supremacy Clause

This Risk Constitution is the **supreme risk authority** for the NZT-48 system. It supersedes all other configuration, code logic, operator instruction, and learning-engine output. Where any conflict exists between this document and any other system component, this document prevails without exception.

### 1.2 Scope

Every rule enumerated herein applies to:

- All execution code paths (live, paper, backtest replay).
- All configuration files (`settings.yaml`, environment variables, runtime overrides).
- All learning and meta-learning engines.
- All operator actions, whether manual or automated.
- All third-party integrations (broker API, data feeds, Telegram bot).

### 1.3 Violation Consequences

Any violation of a rule marked **NON-NEGOTIABLE** triggers:

1. Immediate system HALT.
2. Automated incident report generation.
3. PM/IC notification within 15 minutes.
4. Mandatory root cause analysis before restart.
5. Permanent entry in the incident library.

No "soft" override, "temporary" waiver, or "one-time" exception mechanism exists for rules in this document.

---

## 2. Position Limits (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R1** | Max concurrent positions | **2** in PAPER mode. **3** in LIMITED LIVE mode. Any attempt to open a position beyond this limit MUST be blocked at the execution layer. |
| **R2** | Max risk per trade | **2% of current equity**. Calculated as: `(entry price - stop price) x position size <= 0.02 x equity`. If the computed size would breach this, the position sizer MUST reduce size until compliant. |
| **R3** | Max notional per position | **10% of current equity**. No single position may have notional exposure exceeding this threshold. |
| **R4** | Max total deployment | **40% of current equity** across all open positions (sum of notional values). |
| **R5** | No overnight holds | All positions MUST be closed by **16:25 UK time** for LSE-listed tickers. The time-decay close procedure initiates at 16:00 UK. No exception for "almost at target" or "stop not yet hit". |

### 2.1 Position Limit Rationale

Leveraged ETPs amplify both gains and losses. R1-R4 collectively ensure that a simultaneous adverse move across all positions cannot breach L3 in a single tick sequence. R5 eliminates gap risk from overnight holds of leveraged products, where 3x-5x gap moves can be catastrophic.

---

## 3. Drawdown Circuit Breakers (NON-NEGOTIABLE)

### 3.1 Daily Drawdown Levels

| Level | Trigger | System Response |
|-------|---------|-----------------|
| **L1** | Daily P&L <= **-1.5%** of SOD equity | Reduce all new position sizing by **50%**. Alert operator via Telegram. Log event. Continue trading with reduced size. |
| **L2** | Daily P&L <= **-2.5%** of SOD equity | **No new entries permitted**. System enters EXIT-ONLY mode. All pending signals suppressed. Alert operator via Telegram with urgency flag. |
| **L3** | Daily P&L <= **-4.0%** of SOD equity | **Flatten all positions immediately** (market orders). System enters HALT state. Require **manual restart** with reason logged. Alert operator and PM. |

SOD = Start of Day. Daily drawdown is measured from the equity high-water mark at market open (08:00 UK), not from the previous close.

### 3.2 Weekly Drawdown Limit

| Threshold | Response |
|-----------|----------|
| Weekly P&L <= **-8.0%** of Monday SOD equity | System HALT for the remainder of the trading week. No restart permitted until the following Monday. Operator notification required. |

### 3.3 Monthly Drawdown Limit

| Threshold | Response |
|-----------|----------|
| Monthly P&L <= **-15.0%** of month-start equity | System HALT. **IC review required** before any restart. Written approval memo must be filed. Post-mortem analysis of all trades in the drawdown period is mandatory. |

### 3.4 Circuit Breaker State Machine

```
NORMAL → [L1 breach] → REDUCED_SIZE
REDUCED_SIZE → [L2 breach] → EXIT_ONLY
EXIT_ONLY → [L3 breach] → HALTED
HALTED → [manual restart + reason logged] → NORMAL

Any state → [weekly/monthly limit breach] → HALTED
HALTED (weekly) → [new week starts + operator ack] → NORMAL
HALTED (monthly) → [IC review + written approval] → NORMAL
```

Circuit breaker state MUST persist to disk. A system restart does not reset the circuit breaker level.

---

## 4. Leverage Rules (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R6** | Leverage-once assertion | The system MUST NEVER apply its own leverage or margin calculation to an already-leveraged ETP. The product's inherent leverage (3x, 5x) IS the leverage. Any code path that multiplies position size by a leverage factor MUST assert that the instrument is not already leveraged. Violation of this rule is an **automatic L3 event**. |
| **R7** | 5x product size reduction | All 5x products (QQQ5.L, SP5L.L) receive a **50% position size reduction** relative to the size that would be computed for a 3x product with equivalent characteristics. |
| **R8** | Volatility regime reduction | During `HIGH_VOLATILITY` or `SHOCK` regime classification, ALL leveraged products receive a **50% position size reduction**. This stacks multiplicatively with R7: a 5x product in HIGH_VOL regime has its size reduced by **75%** (50% x 50% = 25% of baseline). |

### 4.1 Leverage Lookup Table

| Product Type | Normal Regime | HIGH_VOL / SHOCK Regime |
|-------------|---------------|------------------------|
| 3x ETP      | 100% of computed size | 50% of computed size |
| 5x ETP      | 50% of computed size  | 25% of computed size |

---

## 5. Data Quality Rules (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R9**  | Staleness gate | No entry permitted if the most recent price data for the target instrument is older than **120 seconds**. Staleness is measured as `now() - last_tick_timestamp`. |
| **R10** | Spread gate | No entry if bid-ask spread exceeds **0.5%** for 3x products or **0.8%** for 5x products. Spread = `(ask - bid) / mid`. |
| **R11** | Coverage gate | No entry if real-time data coverage is below **80%** of the active ISA universe. If fewer than 10 of the 12 tickers have fresh data, the system MUST suppress all new entries until coverage is restored. |
| **R12** | Opening exclusion | No entry during the first **5 minutes** of LSE open (**08:00 - 08:05 UK time**). Opening auction volatility and wide spreads make this window unsuitable for leveraged ETP entry. |

### 5.1 Data Quality Failure Response

When any data quality rule blocks an entry:

1. Log the specific rule triggered (R9/R10/R11/R12) with full context.
2. Suppress the signal but retain it in the candidate queue.
3. Re-evaluate the signal on the next tick cycle.
4. If the data quality issue persists for > 10 minutes, escalate to operator via Telegram.

---

## 6. Signal Quality Rules (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R13** | Minimum composite score | Score >= **65** required for execution. Signals scoring **55-64** may be displayed on the Telegram feed for operator awareness but MUST NOT be executed. Signals scoring < 55 are discarded. |
| **R14** | Minimum reward-to-risk ratio | R:R >= **1.2** for any entry. Calculated as `(target - entry) / (entry - stop)`. A signal with R:R < 1.2 is blocked regardless of composite score. |
| **R15** | Risk Officer VETO | The Risk Officer module has **absolute veto authority**. When the Risk Officer vetoes a signal, no override mechanism exists. The veto is logged with rationale and the signal is killed. |
| **R16** | RVOL liquidity gate | Relative volume (RVOL) must be >= **0.4x** the rolling average for the time of day. This ensures minimum liquidity for leveraged ETP execution. |

### 6.1 Signal Quality Matrix

| Composite Score | R:R >= 1.2 | RVOL >= 0.4x | Risk Officer OK | Action |
|----------------|-----------|-------------|-----------------|--------|
| >= 65          | Yes       | Yes         | Yes             | **EXECUTE** |
| >= 65          | Yes       | Yes         | VETO            | **BLOCKED** |
| >= 65          | No        | Yes         | Yes             | **BLOCKED** (R:R) |
| >= 65          | Yes       | No          | Yes             | **BLOCKED** (RVOL) |
| 55-64          | Any       | Any         | Any             | **DISPLAY ONLY** |
| < 55           | Any       | Any         | Any             | **DISCARD** |

---

## 7. Execution Rules (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R17** | Mandatory stop loss | Every position MUST have a stop loss order placed simultaneously with (or immediately after) entry. A position without a stop loss is a **critical violation** triggering immediate L3 protocol. |
| **R18** | Stop tightening only | After entry, the stop loss may only be **tightened** (moved closer to current price / in favour of the position). Widening a stop is prohibited. Any code path that attempts to widen a stop MUST be blocked and logged. |
| **R19** | Full exit on target | When the target price is hit, the position is exited in **full** immediately. No partial profit-taking, no "let it run" logic. In paper mode, this is a clean, binary outcome: stop hit or target hit. |
| **R20** | Time-decay close | If neither stop nor target is hit by **16:00 UK**, the position enters time-decay close protocol. The system MUST close the position by **16:25 UK** regardless of P&L. The time-decay close uses a linear urgency ramp: passive at 16:00, increasingly aggressive, market order at 16:20 if still open. |

### 7.1 Execution Lifecycle

```
SIGNAL_GENERATED
  → Quality gates (R13, R14, R15, R16)
  → Data gates (R9, R10, R11, R12)
  → Position limits (R1, R2, R3, R4)
  → Leverage adjustment (R6, R7, R8)
  → Circuit breaker check (L1/L2/L3)
  → ENTRY + STOP PLACED (R17)
  → MONITORING
    → Stop hit → EXIT (loss logged)
    → Target hit → EXIT (profit logged, R19)
    → 16:00 UK → TIME-DECAY CLOSE (R20)
    → Stop management → TIGHTEN ONLY (R18)
  → POST-TRADE LOGGING
```

---

## 8. Learning Engine Bounds (NON-NEGOTIABLE)

### 8.1 Permitted Adjustments

| Rule | Parameter | Permitted Range | Baseline |
|------|----------|----------------|----------|
| **R21a** | Score weights | Baseline **+/- 20%** | As defined in `settings.yaml` |
| **R21b** | RVOL threshold | **0.3x - 0.6x** | 0.4x |
| **R21c** | ATR threshold | **0.5% - 3.0%** | As defined in `settings.yaml` |

### 8.2 Prohibited Adjustments

| Rule | Constraint |
|------|-----------|
| **R22** | The meta-learner CANNOT adjust, propose adjustment to, or influence in any way: position limits (R1-R5), drawdown levels (L1/L2/L3), leverage rules (R6-R8), stop rules (R17-R18), execution timing (R19-R20), or any other rule in this Constitution. Attempted adjustment is logged and blocked. |

### 8.3 Drift Protection

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R23** | Parameter drift limit | If any learning-adjusted parameter drifts more than **15%** from its baseline value, the system enters **DEFENSIVE mode**: all learning-adjusted parameters revert to baseline defaults. Defensive mode persists until operator review. |
| **R24** | Minimum sample size | The learning engine requires a minimum of **100 resolved trade outcomes** (stop hit or target hit, not time-decay) before ANY parameter adjustment is permitted. Before this threshold, all parameters remain at baseline. |
| **R25** | IC review requirement | All learning adjustments MUST be documented in a **weekly IC review memo**. The memo includes: parameters adjusted, direction and magnitude of adjustment, supporting trade data, and drift distance from baseline. |

---

## 9. Kill Switch Rules (NON-NEGOTIABLE)

| Rule | Constraint | Detail |
|------|-----------|--------|
| **R26** | Flatten latency | Kill switch activation MUST flatten all open positions within **60 seconds**. If any position remains open after 60 seconds, escalate to emergency protocol (repeated market close attempts at 5-second intervals). |
| **R27** | State persistence | Kill switch state MUST persist to disk. A system crash, restart, container restart, or EC2 reboot MUST NOT clear the kill switch state. The kill switch file is checked on every startup and every tick cycle. |
| **R28** | Activation methods | Three independent activation methods MUST be operational at all times: (1) **Telegram command** (`/kill`), (2) **File-based trigger** (presence of `KILL_SWITCH` file in system root), (3) **API endpoint** (`POST /kill`). Any single method is sufficient. |
| **R29** | Manual restart requirement | After kill switch activation, the system MUST require **manual restart** with a logged reason. Auto-restart, scheduled restart, and watchdog restart are all prohibited while kill switch is active. The restart procedure requires: (1) remove kill switch file, (2) acknowledge via API or Telegram, (3) provide restart reason. |

### 9.1 Kill Switch State Machine

```
ARMED (normal operation)
  → /kill OR KILL_SWITCH file OR POST /kill
  → TRIGGERED
    → Flatten all positions (R26, 60s max)
    → Persist state to disk (R27)
    → Alert operator + PM
  → KILLED (system halted)
    → Manual intervention required (R29)
    → Operator removes file + acknowledges + logs reason
  → ARMED
```

---

## 10. Enforcement Points

Every rule in this Constitution is enforced at a specific code location. The following table maps rules to their enforcement modules. Any refactoring that moves enforcement logic MUST update this table.

| Rule(s) | Enforcement Module | Responsibility |
|---------|-------------------|----------------|
| R1, R2, R3, R4 | `execution/position_sizer.py` | Compute compliant position size; block if limits breached |
| R1, R5, R17, R18, R19, R20 | `execution/virtual_trader.py` | Enforce position count, overnight rule, stop management, target/time exits |
| L1, L2, L3, Weekly, Monthly | `execution/circuit_breaker.py` | Monitor drawdown; trigger state transitions; persist state |
| R9, R10, R11, R12 | `feeds/data_validator.py`, `command_center/tick_loop.py` | Validate data freshness, spread, coverage, opening exclusion |
| R13, R14, R15, R16 | `signal_engine/engine.py`, `command_center/diff.py` | Enforce score threshold, R:R minimum, Risk Officer veto, RVOL gate |
| R6, R7, R8 | `execution/position_sizer.py` | Leverage-once assertion, 5x reduction, volatility reduction |
| R21, R22, R23, R24, R25 | `learning/meta_learner.py` | Bound adjustments, block prohibited changes, detect drift |
| R26, R27, R28, R29 | `core/kill_switch.py`, `main.py` | Kill switch activation, persistence, restart gating |

### 10.1 Enforcement Invariant

Every entry code path MUST pass through the following gate sequence in order:

1. Kill switch check (`core/kill_switch.py`)
2. Circuit breaker check (`execution/circuit_breaker.py`)
3. Data quality gates (`feeds/data_validator.py`)
4. Signal quality gates (`signal_engine/engine.py`)
5. Position limit check (`execution/position_sizer.py`)
6. Leverage adjustment (`execution/position_sizer.py`)
7. Execution with mandatory stop (`execution/virtual_trader.py`)

No entry may bypass any gate. No gate may be reordered. This sequence is the **critical path** and is verified by acceptance test RC-T01 through RC-T10.

---

## 11. Acceptance Tests

The following tests MUST pass before the system is approved for any mode (paper or live). They are run as part of the CI/CD pipeline and on every deployment.

| Test ID | Scenario | Expected Outcome | Rules Validated |
|---------|----------|-------------------|-----------------|
| **RC-T01** | Attempt to open a 3rd concurrent position (paper mode) | Entry **BLOCKED**. Error logged: "Position limit R1 exceeded." | R1 |
| **RC-T02** | Submit trade where `(entry - stop) x size = 3%` of equity | Position sizer **REDUCES** size until risk <= 2% of equity. Trade executes at reduced size. | R2 |
| **RC-T03** | Simulate daily P&L reaching -2.5% (L2 trigger) | System transitions to **EXIT-ONLY** mode. All pending signals suppressed. Telegram alert sent. | L2 |
| **RC-T04** | Simulate daily P&L reaching -4.0% (L3 trigger) | All positions **FLATTENED** immediately. System enters **HALT** state. Manual restart required. | L3 |
| **RC-T05** | Inject price data with timestamp > 120 seconds old | Entry **BLOCKED**. Log entry: "Staleness gate R9: data age {n}s exceeds 120s limit." | R9 |
| **RC-T06** | Generate signal with composite score 60, R:R = 1.0 | Entry **BLOCKED**. Two reasons logged: (1) score < 65 (R13), (2) R:R < 1.2 (R14). Signal displayed on Telegram as DISPLAY_ONLY. | R13, R14 |
| **RC-T07** | Risk Officer issues VETO on a qualifying signal (score 80, R:R 2.0) | Entry **BLOCKED**. No override path exists. Veto reason logged. | R15 |
| **RC-T08** | Meta-learner attempts to adjust `max_positions` parameter | Adjustment **BLOCKED**. Log entry: "R22 violation: meta-learner attempted to modify protected parameter." | R22 |
| **RC-T09** | Activate kill switch via Telegram `/kill` command | All open positions **FLATTENED** within 60 seconds. System enters KILLED state. Kill switch file persisted. Manual restart required. | R26, R27, R28, R29 |
| **RC-T10** | 5x product (QQQ5.L) entry during HIGH_VOLATILITY regime | Position size reduced by **75%** (50% from R7 x 50% from R8 = 25% of baseline). Trade executes at 25% size. | R7, R8 |

### 11.1 Test Execution Protocol

- Tests RC-T01 through RC-T10 run in an isolated paper-mode environment.
- Each test is atomic: setup, execute, assert, teardown.
- Test results are logged with timestamp and system state snapshot.
- Any test failure blocks deployment and triggers investigation.
- Tests are re-run after any code change to enforcement modules (Section 10).

---

## 12. Violation Response Protocol

### 12.1 Violation Classification

| Severity | Description | Examples |
|----------|------------|----------|
| **CRITICAL** | Direct breach of a NON-NEGOTIABLE rule | Position opened without stop (R17), leverage applied to leveraged ETP (R6), position held past 16:25 (R5) |
| **MAJOR** | System behaviour inconsistent with Constitution intent | Circuit breaker state not persisted, kill switch file not checked on startup, learning engine adjustment without sufficient samples |
| **MINOR** | Logging or alerting failure related to a rule | Telegram alert not sent on L1, enforcement log missing context fields |

### 12.2 Response Procedures

**CRITICAL Violation:**

1. Immediate system **HALT** (equivalent to L3 protocol).
2. All positions flattened.
3. Automated incident report generated within 60 seconds, containing:
   - Timestamp of violation.
   - Rule(s) violated.
   - System state at time of violation (equity, positions, drawdown, regime).
   - Code path that permitted the violation.
   - Data snapshot (prices, spreads, scores).
4. PM and IC notified within **15 minutes**.
5. Root cause analysis (RCA) document required before any restart.
6. Code fix deployed, acceptance tests re-run, IC sign-off obtained.
7. Entry added to permanent incident library.

**MAJOR Violation:**

1. System enters **DEFENSIVE** mode (baseline parameters, reduced sizing).
2. Incident report generated.
3. PM notified within **1 hour**.
4. Fix scheduled for next deployment cycle.
5. Entry added to incident library.

**MINOR Violation:**

1. Warning logged.
2. Fix added to engineering backlog.
3. Reviewed in weekly IC memo.

### 12.3 Incident Library

All violations are stored permanently in the incident library (`data/incidents/`). Each incident record contains:

- Incident ID (auto-generated, sequential).
- Timestamp (UTC).
- Severity (CRITICAL / MAJOR / MINOR).
- Rule(s) violated.
- System state snapshot.
- Root cause (populated post-RCA).
- Remediation action.
- IC sign-off (for CRITICAL).

The incident library is append-only. Records cannot be modified or deleted.

---

## Appendix A: Definitions

| Term | Definition |
|------|-----------|
| **SOD** | Start of Day. The equity value at 08:00 UK on the trading day. |
| **Equity** | Current account value including unrealised P&L on open positions. |
| **Notional** | `entry price x position size` -- the full market exposure of a position. |
| **RVOL** | Relative Volume. Current volume divided by the rolling average volume for the same time-of-day window. |
| **R:R** | Reward-to-Risk ratio. `(target - entry) / (entry - stop)` for long positions. |
| **ETP** | Exchange-Traded Product. Includes ETFs, ETNs, and leveraged/inverse products. |
| **ISA** | Individual Savings Account. UK tax-wrapper; no shorting permitted, no margin. |
| **IC** | Investment Committee. The governance body responsible for system oversight. |
| **PM** | Portfolio Manager. The individual responsible for daily system operation. |
| **Risk Officer** | Automated module that evaluates regime, correlation, and tail risk to issue vetoes. |
| **DEFENSIVE mode** | Fallback state where all learning-adjusted parameters revert to baseline defaults. |
| **HALT** | System state where no trading activity is permitted. Requires manual intervention to exit. |

## Appendix B: Amendment Procedure

1. Any proposed amendment to this Constitution MUST be submitted in writing to the IC.
2. The amendment MUST include: rationale, risk assessment, affected rules, updated acceptance tests.
3. IC review period: minimum **5 business days**.
4. Approval requires **unanimous** IC consent.
5. Approved amendments are versioned (this document's version is incremented).
6. All prior versions are retained in the document archive.
7. Code changes implementing the amendment MUST NOT be deployed until the amended document is signed off.

---

**END OF DOCUMENT**

*NZT48-ANNEX-RC-001 v1.0 -- This document is binding upon all system components, operators, and governance bodies from the effective date until superseded by a duly approved amendment.*
