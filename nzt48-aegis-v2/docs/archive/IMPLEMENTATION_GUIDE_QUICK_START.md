# AEGIS V2 IMPLEMENTATION GUIDE — QUICK START

**Document Location**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/AEGIS_V2_COMPLETE_IMPLEMENTATION_GUIDE.md`

**Line Count**: 2,855 lines — complete implementation specifications for all 25 phases

---

## TODAY (14.5 HOURS)

### Phase 3-6: Wiring (4.5 hours)
Execute and deploy:
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core

# 1. Add ApexSnapshot to lib.rs (lines ~1-100)
# 2. Add ModeBPlus to types/enums.rs
# 3. Add try_rotate to subscription_manager.rs
# 4. Run tests
cargo test phase36_acceptance -- --nocapture

# Expected: 12 new tests passing, 600+ total
cargo test --lib 2>&1 | tail -5
```

### Phase 24: Quantum Apex (10 hours)
```bash
# 1. Add quantum_apex.rs (DQN + Hawkes)
# 2. Add quantum_apex_dqn.rs (DQN trainer)
# 3. Add quantum_apex_hawkes.rs (Hawkes predictor)
# 4. Run tests
cargo test quantum_apex -- --nocapture
cargo test dqn_trainer_tests -- --nocapture
cargo test hawkes_tests -- --nocapture

# Expected: 15 new tests passing, 605+ total
cargo test --lib 2>&1 | tail -5
```

### Deploy to EC2
```bash
bash /Users/rr/nzt48-signals/nzt48-aegis-v2/scripts/deploy_to_ec2.sh
# Verify: docker logs nzt48_aegis_1 | grep "AEGIS running"
```

---

## WEEK 2 (Phase 7: 15 HOURS)

Implement full 20k ticker rotation:
- Load complete regional ticker universes
- 5-second rotation intervals
- 200+ cycles per day per region
- Expected: 610+ tests

---

## WEEKS 3-4 (Phase 8: 77 HOURS)

Pre-condition gates + 33 module templates:
- VIX/DXY/credit spread filters
- Equity, volatility, macro, order flow gates
- All 33 modules with signals
- Expected: 630+ tests

---

## WEEK 5 (Phase 9: 20 HOURS)

Cross-asset macro integration:
- VIX, DXY, credit, Fear & Greed feeds
- Regime detection (trend/mean-revert/crisis/range)
- Expected: 640+ tests

---

## WEEKS 6-10 (Phases 10-15: 120 HOURS)

Implement all 33 trading modules:
- Phase 10: Momentum (6 modules)
- Phase 11: Mean-reversion (6 modules)
- Phase 12: Volatility (6 modules)
- Phase 13: Order flow + macro (10 modules)
- Phase 14: Cross-asset + pairs (5 modules)
- Phase 15: Advanced + regime (4 modules)
- Expected: 720+ tests

---

## WEEKS 11-12 (Phase 16: 52 HOURS)

Ouroboros nightly learning pipeline:
- 10-step ML training
- 2-hour deadline enforcement
- Model validation + deployment
- Expected: 760+ tests

---

## WEEK 13 (Phase 17: 18 HOURS)

Telemetry dashboard:
- WebSocket real-time updates
- REST API endpoints
- Expected: 780+ tests

---

## WEEKS 14-18 (Phases 18-21: 80 HOURS)

Multi-exchange integration:
- TSE (Japan)
- HKEX (Hong Kong)
- ASX (Australia)
- Euronext (Europe)
- Expected: 790+ tests

---

## WEEKS 19-20 (Phase 22: 47 HOURS)

Institutional hardening:
- PnL reporting to pence
- 100% audit trail
- Kill switch <100ms
- Circuit breaker (2% daily halt)
- Expected: 800+ tests

---

## WEEK 21 (Phase 25: 20 HOURS)

Live capital deployment:
- £1,000 → £2,500 → £5,000 → £10,000 scaling
- Daily profitability verification
- Compound growth monitoring
- Expected: 820+ tests

---

## KEY FILES TO MODIFY

| Phase | Files | Hours |
|-------|-------|-------|
| 3-6 | lib.rs, types/enums.rs, subscription_manager.rs | 4.5 |
| 24 | quantum_apex.rs, quantum_apex_dqn.rs, quantum_apex_hawkes.rs | 10 |
| 7 | subscription_manager_v2.rs | 15 |
| 8 | preconditions.rs, modules/mod.rs | 77 |
| 9 | cross_asset_macro.rs (expand) | 20 |
| 10-15 | modules/module_*.rs (33 files) | 120 |
| 16 | ouroboros/mod.rs | 52 |
| 17 | telemetry.rs (expand) | 18 |
| 18-21 | exchanges/*.rs (4 files) | 80 |
| 22 | compliance/pnl_tracker.rs, audit_trail.rs, kill_switch.rs | 47 |
| 25 | deployment_manager.rs | 20 |

---

## TESTING TARGETS

| Milestone | Target | Status |
|-----------|--------|--------|
| Now | 588 → 600+ | Execute Phase 3-6 |
| Now | 600 → 605+ | Execute Phase 24 |
| Week 2 | 605 → 610+ | Phase 7 |
| Weeks 3-4 | 610 → 630+ | Phase 8 |
| Week 5 | 630 → 640+ | Phase 9 |
| Weeks 6-10 | 640 → 720+ | Phases 10-15 |
| Weeks 11-12 | 720 → 760+ | Phase 16 |
| Week 13 | 760 → 780+ | Phase 17 |
| Weeks 14-18 | 780 → 790+ | Phases 18-21 |
| Weeks 19-20 | 790 → 800+ | Phase 22 |
| Week 21 | 800 → 820+ | Phase 25 |

---

## SUCCESS METRICS (END OF PHASE 25)

- ✅ 0.3-0.8% daily returns (£3-8 on £10k)
- ✅ 145-348% annualized
- ✅ Win rate 45%+
- ✅ Sharpe ratio 1.5+
- ✅ Max drawdown <8%
- ✅ Zero missed trades
- ✅ 100% audit compliance
- ✅ 820+ tests passing

---

## EXECUTION RULES

1. **No skipping phases** — Each phase gates the next
2. **Tests first** — Write tests before code
3. **Deploy after each phase** — Verify EC2 stability
4. **Document as you go** — Update EXECUTION_STATE.md daily
5. **Rollback on failure** — Git revert to last stable, then debug
6. **No deviations** — Follow the plan exactly as written

---

**Total effort**: 643.5 hours over 21 weeks

**Status**: Ready to execute. Start with Phase 3-6 today.

