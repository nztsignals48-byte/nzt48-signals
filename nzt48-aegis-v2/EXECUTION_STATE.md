# EXECUTION STATE — v6.0
## Directive: SHIP IT — validate, wire, bound, deploy, verify (ALL IN ONE SESSION)
## Budget: $200/month (IBKR realtime active, EC2 c7i-flex.large, EBS 50GB)
## Last Updated: 11 March 2026

## 5 Orders
1. **Validate Phase 2A** — Ralph Wiggum on exit sell code (engine.rs:494-556)
2. **Wire DynamicWeights** — chandelier_atr_mult → ChandelierStrategy, regime_scales → RiskArbiter, kelly_fractions → RiskArbiter
3. **Bound WAL** — crossbeam unbounded → bounded(50,000) at wal_actor.rs:89
4. **Deploy EC2** — rsync + docker compose up --build → ubuntu@3.230.44.22
5. **Verify live** — 3/3 containers, ticks arriving, weights applied, no errors

## Key Wiring Points (exact lines)
- main.rs:80 → DW loaded, main.rs:207 → engine constructed, **gap between 80 and 207 = DW never applied**
- engine.rs:267 → ExitEngine::with_default_chandelier() ignores chandelier_atr_mult
- risk_arbiter.rs:274-277 → adjusted_size hardcodes Reduce=0.5, ignores regime_scales
- wal_actor.rs:89 → crossbeam::unbounded() can OOM 4GB server

## Already Fixed (DO NOT TOUCH)
- OrderSide { Buy, Sell } in all adapters ✅
- TickerId extraction from BrokerEvent::Fill ✅
- MarketDataType::Realtime with fallback ✅
- Phantom fallback removed ✅
- PythonSubprocessManager wired ✅
- bridge.py error handling ✅
- Regime persistence to WAL ✅
- FillEvent written to WAL ✅
- V1 killed, IB Gateway self-contained ✅

## Compiler Constraints
- `#![deny(warnings)]` at lib.rs:5 — zero unused anything
- `#![deny(clippy::unwrap_used)]` at lib.rs:4 — no .unwrap()
- `cargo test --no-default-features --lib` on macOS (PyO3 gotcha)
- Edition 2024, crate = rust_core

## Definition of Done
Engine on EC2 paper trading with realtime data, functional entries AND exits, Ouroboros weights applied, bounded WAL. Accumulating trades toward 100-trade Crucible gate (WR ≥ 40%, Sharpe > 0, max DD < 8%).
