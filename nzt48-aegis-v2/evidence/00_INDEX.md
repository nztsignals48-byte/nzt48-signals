# AEGIS V2 — Evidence Package Index

**Session 22 | 2026-04-04 | Branch: `feat/tier-system-enhancements-full`**

---

## Document Manifest

| # | Document | Description |
|---|----------|-------------|
| 00 | INDEX.md | This file — master index |
| 01 | [SYSTEM_SCOPE.md](01_SYSTEM_SCOPE.md) | Architecture, codebase scale, deployment target |
| 02 | [DATA_PROVENANCE.md](02_DATA_PROVENANCE.md) | Data sources, live vs backtest, integrity controls |
| 03 | [UNIVERSE_MANIFEST.csv](03_UNIVERSE_MANIFEST.csv) | All 4,636 contracts with exchange, currency, leverage |
| 04 | [STRATEGY_MANIFEST.md](04_STRATEGY_MANIFEST.md) | All 34 signal generators, disabled strategies with evidence |
| 05 | [RISK_GATE_AUDIT.md](05_RISK_GATE_AUDIT.md) | All 39 Rust checks + 25 Python pre-signal gates |
| 06 | [BACKTEST_RUNBOOK.md](06_BACKTEST_RUNBOOK.md) | Rerunnable commands (fast + world-class), pipeline stages |
| 07 | [RESULTS_SUMMARY.md](07_RESULTS_SUMMARY.md) | Session 22 backtest results and analysis |
| 08 | [KNOWN_LIMITATIONS.md](08_KNOWN_LIMITATIONS.md) | 10 documented limitations with mitigation |
| 09 | [EXECUTION_PATH.md](09_EXECUTION_PATH.md) | Signal-to-order-to-fill: 17-step execution pipeline |
| 10 | [NIGHTLY_PIPELINE.md](10_NIGHTLY_PIPELINE.md) | Ouroboros nightly learning loop |
| 11 | [SIZING_METHODOLOGY.md](11_SIZING_METHODOLOGY.md) | 12-factor Kelly, rolling Kelly, drawdown staging |
| 12 | [VALIDATION_INFRASTRUCTURE.md](12_VALIDATION_INFRASTRUCTURE.md) | 7 anti-overfitting gates, DSR, PBO, SPRT |
| 13 | [LIVE_VS_PAPER_CONFIG.md](13_LIVE_VS_PAPER_CONFIG.md) | Configuration differences and transition checklist |
| 14 | [BOOK_REFERENCES.md](14_BOOK_REFERENCES.md) | Academic sources for every strategy and model |
| 15 | [EXECUTIVE_SUMMARY.md](15_EXECUTIVE_SUMMARY.md) | High-level summary for CTO/fund manager audience |
| 16 | [ADVERSARIAL_AUDIT.md](16_ADVERSARIAL_AUDIT.md) | 9 critical findings and fixes applied |
| -- | TRADE_LEDGER.csv | Per-trade CSV from Session 22 backtest (auto-generated) |

## How to Read This Package

**For CTOs:** Start with 01_SYSTEM_SCOPE.md (architecture) and 09_EXECUTION_PATH.md (how signals become orders). Then 05_RISK_GATE_AUDIT.md (39-check safety).

**For Fund Managers:** Start with 15_EXECUTIVE_SUMMARY.md, then 07_RESULTS_SUMMARY.md (backtest results), 08_KNOWN_LIMITATIONS.md (what we don't claim), and 11_SIZING_METHODOLOGY.md (how capital is allocated).

**For Verification:** Run the command in 06_BACKTEST_RUNBOOK.md to reproduce results. Cross-reference with 03_UNIVERSE_MANIFEST.csv and TRADE_LEDGER.csv.

## Audit Methodology

1. **Rust engine audit:** Read all 78 source files (~36K lines). Cataloged 39 risk checks, execution pipeline, IPC protocol, broker integration.
2. **Python brain audit:** Read all 353 source files (~160K lines). Verified all 34 signal generators are real implementations (no stubs). Confirmed all 40 ML modules are numpy-only.
3. **Backtest infrastructure audit:** Mapped the gap between backfill_simulator.py (10 entry types) / world_class_backtest.py (14 entry types) and bridge.py (34 generators). Documented all missing pre-signal gates, overlays, and risk arbiter limitations.
4. **Config audit:** Compared paper vs live config. Documented all TOML files and their interaction.
5. **Session 22 backtest:** Added 7 new entry types to reach 14 total. Built `world_class_backtest.py` with real risk arbiter, per-exchange spreads, walk-forward IS/OOS, and strategy attribution. Ran fast validation backtest (730-day, 4,635-ticker, TRADE_LEDGER.csv export).
6. **Adversarial audit:** 9 critical findings (entry cooldowns, fail-closed gates, edge-weighted ranking, realistic slippage, Tier 3 disable, confidence calibration, Ouroboros guardrails, real EvalContext data, IBKR reconnection fix).

## Codebase Integrity

| Check | Result |
|-------|--------|
| Stubs detected | **0** — all modules contain real logic |
| ML frameworks required | **None** — numpy-only |
| External API dependencies | yfinance (backtest), IBKR (live) |
| Compile-time safety | IS_LIVE = false (cannot accidentally deploy live) |
| WAL persistence | Every event logged before action |
| Fail-closed risk | Any check failure = immediate rejection |
