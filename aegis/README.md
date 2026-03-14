# AEGIS Master Plan v16.2 — Document Index

> **UK ISA Momentum-Volatility Intelligence Engine**
> NZT-48 Leveraged ETP Compounding Machine
>
> Split from monolithic `AEGIS_MASTER_PLAN_v15_MERGED.md` (2,414 lines) into focused documents.
> The original file is preserved as the canonical archive.

---

## Quick Status

| Metric | Value |
|--------|-------|
| **Stop-ship items** | 98 (40 P0 + 58 P1) |
| **Items fixed in code** | 0 |
| **S15 win rate** | 0% (0/52 paper trades) |
| **Root cause** | Execution timing (T-01 to T-08) |
| **Next action** | CODE. T-08+SK-04 first, then T-01 through T-07. |
| **Instance** | c7i-flex.large (4GB RAM), Elastic IP 3.230.44.22 |
| **Data feed** | IBKR primary, yfinance fallback |

---

## Documents

| # | File | Sections | What's In It |
|---|------|----------|-------------|
| 01 | [Stop-Ship Register](01_STOP_SHIP.md) | 0, 0.1, 0.2 | P0/P1 items, unified thresholds, scenario tables |
| 02 | [Strategy](02_STRATEGY.md) | 1, 1B, 2, 3 | Universe registrar, fatal flaws, S15 engine, Apex Scout |
| 03 | [Execution Timing](03_EXECUTION_TIMING.md) | 2B, 2C, 2D | **THE #1 PRIORITY.** 11 timing defects (T-01 to T-11) |
| 04 | [Hardening](04_HARDENING.md) | 2E, 2G, 2H, 2I | Async hardening, forensic audit, 12 invariants, complexity reduction |
| 05 | [Quantum Apex](05_QUANTUM_APEX.md) | 2F, 2J | Q3/Q4 future state. **DO NOT implement until Q1 validates.** |
| 06 | [Executioner + ML](06_EXECUTIONER.md) | 4, 5, 5B | Execution pipeline, profit ladder, ML learning loop |
| 07 | [Risk](07_RISK.md) | 6, 6B, 6C, 6D | 15-control defence matrix, 10 commandments, constitution, regime controls |
| 08 | [Infrastructure](08_INFRASTRUCTURE.md) | 7, 8, 8B, 8C | Liquidity scaling, deployment, startup gate, daily ops |
| 09 | [Implementation](09_IMPLEMENTATION.md) | 9, 9B, 10 | Phase timeline, go-live gate, parameter tables |
| 10 | [Appendix](10_APPENDIX.md) | 11, 12 | Revision history, math derivations, glossary |

---

## Implementation Order (from Section 2I.6)

| Step | Items | Hours | Gate |
|------|-------|-------|------|
| 1 | T-08+SK-04 (coupled), T-01, T-02, T-04, **T-05**, T-06, T-07, T-03, T-10 | 24h | -- |
| 2 | **100-TRADE VALIDATION GATE (RK-01 + M-06)** | 0h code, 2-4 weeks paper | **WR >= 40% AND median ETS < 0.50** |
| 3 | SK-01, SK-02, SK-03 | 5h | -- |
| 4 | R21-19 (ISA gate) + RI-01 (IMAGE_PARITY) | 10h | -- |
| 5 | R21-42, R21-12, R21-16, R21-01 | 5h | -- |
| 6 | AB-02, R21-04, R21-06, R21-13/14, GQ-01, GQ-02 | 10h | -- |
| 7 | R21-18, RO-01, RO-02, RO-03 | 9h | -- |
| 8 | **63-Day Paper Gauntlet** | 0h code, 63 days | Go/No-Go criteria |
| 9 | P1 items (non-deferred) | ~40h | -- |
| 10 | **PHASE Q2**: WebSocket, PostgreSQL, microstructure | ~150h | Only after Q1 passes |

---

## Combined Document & PDF

| File | Description |
|------|-------------|
| [AEGIS_COMPLETE.md](AEGIS_COMPLETE.md) | All 10 sections combined into one document (auto-generated) |
| [AEGIS_COMPLETE.pdf](AEGIS_COMPLETE.pdf) | PDF version (76 pages, A4, auto-generated) |

**DO NOT edit these files directly.** Edit the individual section files, then run:

```bash
bash aegis/sync.sh         # Rebuild .md + .pdf
bash aegis/sync.sh --no-pdf  # Rebuild .md only (faster)
```

---

## Rules

1. **Plan is FROZEN at v16.2.** Next action = CODE.
2. **No new review rounds** until T-01 through T-08 are implemented and 100-trade gate passes.
3. **Sections 2C-2F (Quantum Apex)** are deferred to Q2+. Do not implement.
4. **ML remains in BYPASS mode** until N > 500 trades.
5. **After editing any section file**, run `bash aegis/sync.sh` to regenerate the combined doc + PDF.
