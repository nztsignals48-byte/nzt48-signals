# NZT-48 Stakeholder One-Pager

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-SOP-001            |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **ACTIVE**                     |
| Classification  | Internal -- Executive Summary  |

---

## WHAT THIS SYSTEM IS

NZT-48 is an automated paper trading system for leveraged ISA ETPs on the London Stock Exchange. It targets 2% daily equity growth through one high-quality trade per day, exploiting the compounding law: 10,000 at 2% daily over 252 trading days compounds to approximately 1,485,757.

The system scans 12 leveraged ETPs (3x and 5x products from WisdomTree, GraniteShares, and Leverage Shares) every 60 seconds during LSE hours. It identifies momentum and mean-reversion setups using multi-factor scoring across volatility regimes, then executes paper trades with institutional-grade risk controls including circuit breakers, kill switches, and fail-closed output gates.

---

## WHAT THIS SYSTEM IS NOT

- **Not a black box.** Every signal is explainable: strategy, confidence score, gate verdicts, and risk assessment are logged and visible in the War Room dashboard.
- **Not unsupervised.** A human operator monitors the system during trading hours with daily check-ins at 08:00, 12:00, and 16:30 UK. The operator has a kill switch available 24/7 via Telegram, file, or API.
- **Not live trading (yet).** The system is in paper mode with 10,000 virtual equity. No real capital is at risk. Real money trading requires explicit gate approvals (see PAPER_TO_LIMITED_LIVE_GATES.md).
- **Not managing client money.** This is a personal ISA trading system. No third-party capital, no regulatory obligations beyond standard ISA rules.
- **Not a hedge fund (yet).** Single operator, single account, single strategy universe. No multi-strategy allocation, no prime brokerage, no investor reporting.

---

## HOW IT WORKS (3-Sentence Version)

The engine scans 12+ leveraged ETPs every 60 seconds during LSE hours (08:00-16:30 UK), computing multi-factor scores using price action, volatility regimes, relative volume, and momentum indicators. Signals that pass a 7-gate qualification pipeline (score floor, confidence threshold, magnitude check, regime consistency, data freshness, rate limits, deduplication) are delivered to the operator via Telegram, PDF reports, and a real-time War Room dashboard. A virtual trader executes paper trades with predefined stop losses and stepped profit targets (profit ladder), tracking performance against the 2% daily compounding target.

---

## CURRENT STATUS

| Dimension | Status |
|-----------|--------|
| **Mode** | Paper trading -- 10,000 virtual equity |
| **Infrastructure** | EC2 (AWS) running Docker containers: trading engine (FastAPI :8000) + dashboard (Next.js :3001) |
| **Trades Executed** | 0 (pipeline disconnect being fixed -- signal generation works but virtual trader integration pending) |
| **Signals Generated** | Signal pipeline operational; signals reaching Telegram |
| **Workstreams** | 13 remediation workstreams defined (W0-W12); deployment (W0) in progress |
| **Documentation** | 30+ binding annexes covering all system aspects |
| **Test Coverage** | 242 tests defined in TEST_PLAN.md; execution pending |

---

## TARGET: PAPER TO LIVE PROGRESSION

```
GATE 0: Pipeline Operational
  All workstreams deployed, signals flowing, virtual trader active
       |
GATE 1: Paper Stable (30 sessions)
  95% uptime, zero impossible signals, all Go-Live checks pass
       |
GATE 2: Paper Ready (60 sessions from Gate 1)
  40% win rate, Sharpe >= 0.5, max DD <= 10%, 100+ resolved trades
       |
GATE 3: Limited Live (10% capital = 1,000)
  IC sign-off, broker integration, daily loss limit 10
       |
GATE 4: Full Live (future -- TBD after Gate 3 data)
  90+ live sessions, consistent profitability, enhanced monitoring
```

---

## OPERATOR EXPECTATIONS

| Checkpoint | Time (UK) | Actions |
|-----------|-----------|---------|
| Morning check | 08:00 | Review overnight risk PDF (P5). Check War Room Go-Live Gate. Verify engine running. Review premarket brief (P1). Confirm system mode (NORMAL/DEGRADED/HALTED). |
| Midday check | 12:00 | Review open positions. Check P&L. Verify no drought conditions. Check system health indicators. |
| Close check | 16:30 | Review mid-session risk PDF (P6). Verify all positions closed or stops in place. Check daily P&L. Review EOD report (P3) when generated at 22:00. |
| Kill switch | 24/7 | Available via Telegram (`/kill ALL`), file (`touch data/KILL_SWITCH`), or API (`POST /api/kill`). Latency: <5 seconds (Telegram), <60 seconds (file), immediate (API). |
| Weekly | Friday PM | Review IC memo: weekly P&L, win rate, signal quality, system incidents, action items. |
| Monthly | Last Friday | Performance review against 2% compounding target. Sharpe ratio, drawdown analysis, strategy-level breakdown. Gate progression assessment. |

---

## KEY RISKS

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **Leveraged product volatility** | HIGH | Circuit breakers at 1.5%/2.5%/4% daily drawdown. Intraday holding period only. ATR-based stop placement. |
| **Data feed reliability** | MEDIUM | DataHub abstraction with yfinance fallback. Provenance tracking with TTL validation. Staleness gate on all outputs. |
| **Learning engine drift** | MEDIUM | Drift detection with 35%/25% thresholds. DEFENSIVE mode on drift. Readiness gates requiring 100+ outcomes. Feature flag to disable entirely. |
| **Single operator dependency** | HIGH | Comprehensive documentation (30+ annexes). Automated monitoring and alerting. Kill switch accessible without system access. Fail-closed defaults. |
| **Impossible signal delivery** | LOW (post-W1) | 8-gate fail-closed output policy. Magnitude filter. Regime consistency check. Score floor. Pre-send quality gate. |
| **Deployment integrity** | LOW | Docker containerisation. Parity checks. LKG tagging. No host-only code policy. |

---

## KEY CONTROLS

| Control | Type | Description |
|---------|------|------------|
| **Circuit Breakers** | Automated | L1 (1.5%): reduce sizing. L2 (2.5%): suspend entries. L3 (4.0%): kill switch + PM/IC notification |
| **Risk Officer Veto** | Manual | PM can override any system decision via kill switch or feature flags |
| **Kill Switch** | Hybrid | 3 activation methods (Telegram, file, API). Persists across restarts. Immediate signal halt. |
| **Feature Flags** | Automated | 13 flags, all default false. Any feature can be disabled in <60 seconds without restart. |
| **LKG Rollback** | Manual | Full system rollback to last known good state in <5 minutes. Git tags + Docker image tags. |
| **Fail-Closed Default** | Automated | When in doubt, suppress output. Never send on uncertainty. 8 gates, each independently blocking. |
| **Go-Live Gate** | Automated | 8-check readiness gate verified every 30 seconds. All checks must pass for system to be considered operational. |
| **Audit Trail** | Automated | Every output decision logged with gate results, delivery status, content hash, and run ID. Append-only override audit log. |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial stakeholder one-pager |

---

*End of Document NZT48-ANNEX-SOP-001*
