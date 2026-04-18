# CHANGELOG

## 2026-04-16 — Phase 0 SCAFFOLD
- Created V5 tree: infra, rust_core, python_brain, scripts, tests, schemas, docs.
- Scaffolded every file per the master plan.
- `cargo build` clean, `pytest tests/smoke` green.

## 2026-04-16 — Phases 1 → 11 COMPLETE
- **P1 Data plane**: NATS-compatible bus, schema-versioned, schema-mismatch fails closed, data_health startup-blocking.
- **P2A Engine hot path**: tick feed, bar builder (1/5/15m/1h), indicators (RSI/ATR/IBS/MACD/momentum/rvol/vwap), WAL hash-chained + dataset-contract-validated.
- **P2B Quant core**: GARCH(1,1), EVT CVaR, Student-t Kalman + residual z, HMM regime probability vector, Hayashi-Yoshida. All outputs consumed by risk arbiter.
- **P3 Order router + broker sync**: 4-tier (Urgent/Patient/PegMid/Arrival) + Market for stops; reconcile flags 1-share mismatch.
- **P4 Risk arbiter**: 16 weighted checks, only sacred halt is 8-consec-loss.
- **P5 Strategies**: 6 MVP (sentiment, filing_change, index_recon, earnings_pattern, overnight_return, ibs_mean_reversion). Preference logger called on every signal + close.
- **P6 Conviction engine + portfolio**: rank_signals() WIRED, LLM delta clipped to [-30, +15] pp, Bayesian Kelly, ISA/GIA/IG routing.
- **P7 LLM army**: 5 rule-based agents with A/B harness (N>=200, bootstrap 95% CI).
- **P8 Scanner**: publishes watchlist.current to NATS, preserves held positions, 40 Thompson Sampling dark-horse slots.
- **P9 Ouroboros**: 12-step nightly, bounds-validated, FAIL-CLOSED, CUSUM + ADWIN.
- **P10 Observability**: 32 Prometheus metrics, 3 critical alerts.
- **P11 Anti-dead-code**: `scripts/dead_code_check.py --strict` PASS.

67 tests pass. Ready for Phase 12 (paper graduation) and Phase 13 (live).
