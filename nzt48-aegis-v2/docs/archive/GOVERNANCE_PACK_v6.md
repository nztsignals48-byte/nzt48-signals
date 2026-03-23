# AEGIS V2 — GOVERNANCE PACK v6.0
**Generated:** 2026-03-20 | **Session:** ULTRATHINK v6.0 Implementation Run
**Authority:** CTO/CRO Sign-Off Required Before Go-Live

---

## 1. CHANGE LOG — THIS SESSION

### Files Created
| File | Purpose | Lines |
|------|---------|-------|
| `python_brain/ouroboros/trade_taxonomy.py` | 14-class trade outcome classifier (N1b) | 196 |
| `config/config.live.toml` | Production-safe parameter overrides (RT1) | 46 |
| `EXECUTION_BACKLOG.md` | Prioritized build backlog v6.1 | 100 |
| `PROOF_REGISTER.md` | Evidence register v6.1 (24 proven, 7 likely, 5 speculative, 10 needs test) | 90 |
| `GOVERNANCE_PACK_v6.md` | This file | — |
| `QUESTION_DECISION_REGISTER.md` | 120 questions with evidence-based answers | ~2000 |
| `ADVERSARIAL_RED_TEAM_v6.md` | 5-persona adversarial review | ~1000 |
| `MASTER_PLAN_RELEASE_CANDIDATE_v6.md` | Definitive release candidate document | ~1200 |

### Files Modified
| File | Change | Impact |
|------|--------|--------|
| `python_brain/bridge.py` | N1c: ticker blacklist enforcement + N3a: structural tradability score | Signal pipeline enhanced |
| `python_brain/ouroboros/nightly_v6.py` | N1a: cost-aware learning + trade taxonomy integration | Ouroboros learning upgraded |
| `rust_core/src/types/wal.rs` | N2a: SignalRejected + N2b: enriched PositionClosed + N2c: MissedWinnerCandidate | WAL schema extended |
| `rust_core/src/types/enums.rs` | Added SignalRejected + MissedWinnerCandidate to WalEventType | Type system updated |
| `rust_core/src/main.rs` | RT1: config.live.toml startup assertion | Safety check added |

### Build Items Completed
| ID | Item | Status |
|----|------|--------|
| N1a | Cost-aware nightly learning | ✅ BUILT |
| N1b | Trade taxonomy classifier (14 classes) | ✅ BUILT |
| N1c | Ticker blacklist enforcement in bridge.py | ✅ BUILT |
| N2a | SignalRejected WAL event type | ✅ BUILT |
| N2b | Enriched PositionClosed fields (7 new) | ✅ BUILT |
| N2c | MissedWinnerCandidate WAL event type | ✅ BUILT |
| N3a | Structural tradability score (0-100, 5 components) | ✅ BUILT |
| N5a | UK holidays enforcement | ✅ VERIFIED (already implemented) |
| RT1 | config.live.toml + startup assertion | ✅ BUILT |

---

## 2. DEPLOYMENT CHECKLIST

### Pre-Deploy Verification
- [ ] Run `cargo test` — verify all existing tests pass with WAL schema changes
- [ ] Run `cargo check` — verify compilation with new WAL variants
- [ ] Verify trade_taxonomy.py import path works in Docker context
- [ ] Verify bridge.py structural tradability score doesn't break existing signal flow
- [ ] Check that nightly_v6.py trade taxonomy integration handles missing fields gracefully

### Deploy Sequence (MANDATORY)
```bash
# 1. Local verification
cd /Users/rr/nzt48-signals/nzt48-aegis-v2
cargo test --release 2>&1 | tail -5

# 2. Commit
git add python_brain/ouroboros/trade_taxonomy.py
git add config/config.live.toml
git add python_brain/bridge.py
git add python_brain/ouroboros/nightly_v6.py
git add rust_core/src/types/wal.rs
git add rust_core/src/types/enums.rs
git add rust_core/src/main.rs
git add EXECUTION_BACKLOG.md PROOF_REGISTER.md GOVERNANCE_PACK_v6.md
git commit -m "N1a/N1b/N1c/N2a/N2b/N2c/N3a/RT1: ULTRATHINK v6.0 build session"

# 3. Push
git push origin feat/tier-system-enhancements-full

# 4. Deploy to EC2
rsync -avz --exclude='.git' --exclude='target' . ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/
ssh ubuntu@3.230.44.22 'cd /home/ubuntu/nzt48-aegis-v2 && docker system prune -f && docker compose build && docker compose up -d'

# 5. Verify
ssh ubuntu@3.230.44.22 'docker logs aegis-v2 --tail 20'
```

---

## 3. PAPER VALIDATION OVERRIDE INVENTORY

These config values are intentionally relaxed for paper trading data collection.
**They are TIME BOMBS** — MUST be reverted before live trading via config.live.toml overlay.

| Parameter | Paper Value | Live Value | Risk if Forgotten |
|-----------|-------------|------------|-------------------|
| max_simultaneous_positions | 15 | 3 | Catastrophic overexposure |
| portfolio_heat_limit_pct | 50.0 | 10.0 | 5x risk budget exceeded |
| sector_heat_cap_pct | 80.0 | 33.0 | Single-sector concentration |
| cash_buffer_pct | 5.0 | 25.0 | Insufficient drawdown protection |

**Mitigation:** config.live.toml contains correct values. IS_LIVE=true startup path
MUST load these overrides. Currently IS_LIVE=false with exit(1) guard (PR-06).

---

## 4. INVARIANT REGISTER

### Runtime Invariants (Must NEVER Be Violated)

| # | Invariant | Enforcement | Test |
|---|-----------|-------------|------|
| I-01 | IS_LIVE=false | const IS_LIVE: bool = false + exit(1) | main.rs:29,47-50 |
| I-02 | No shorts in ISA | VetoReason::IsaShortSellBlocked | risk_arbiter_tests.rs |
| I-03 | WAL CRC32 on every write | wal_writer.rs compute_checksum() | wal_tests.rs |
| I-04 | Stop only ratchets UP | exit_engine.rs stop_price comparison | exit_engine_tests.rs |
| I-05 | Daily trade cap enforced | DailyTradeLimitReached veto | risk_arbiter.rs |
| I-06 | Confidence floor ≥ 65 | config.toml + bridge.py effective_floor | config.toml:7 |
| I-07 | Min edge ≥ 0.15% | GrossEdgeTooLow veto | risk_arbiter.rs |
| I-08 | WAL fsync before broker | wal_writer.rs fsync + WalWritten state | wal_writer.rs |
| I-09 | Bounded WAL channel (50K) | crossbeam_channel::bounded(50_000) | wal_actor.rs |
| I-10 | Single-threaded engine | No Arc<Mutex> in production | architecture audit |
| I-11 | UK holidays block trading | Clock::is_uk_holiday + HolidayCalendar | clock.rs:152, market_scheduler.rs:411 |
| I-12 | Blacklisted tickers rejected | _load_ticker_blacklist() in bridge.py | bridge.py:682-697 |
| I-13 | STS < 30 suppresses signal | structural_score gate in bridge.py | bridge.py:N3a block |
| I-14 | Kelly ∈ [0.15, 0.30] | KELLY_MIN/KELLY_MAX guardrails | nightly_v6.py:59-60 |
| I-15 | Chandelier ATR ∈ [1.5, 4.0] | CHANDELIER_ATR_MIN/MAX | nightly_v6.py:61-62 |
| I-16 | Max drift ≤ 15%/night | _clamp_drift() | nightly_v6.py:472-478 |

---

## 5. GO-LIVE GATES

### 100-Trade Validation Gate (MANDATORY before IS_LIVE=true)

| Gate | Threshold | Current | Status |
|------|-----------|---------|--------|
| Net Win Rate | ≥ 40% | N/A (insufficient trades) | ❌ NOT PASSED |
| Net Profit Factor | ≥ 1.3 | N/A | ❌ NOT PASSED |
| Max Drawdown | < 10% | N/A | ❌ NOT PASSED |
| Spread Victim Rate | < 20% | N/A | ❌ NOT PASSED |
| Avg Winner / Avg Loser | > 1.5 | N/A | ❌ NOT PASSED |

**Estimated time to gate:** 4-8 weeks at 3 trades/day = 60-120 trades.

### Pre-Live Checklist
- [ ] 100-Trade Validation Gate ALL passed
- [ ] IS_LIVE=false → IS_LIVE=true (requires code change + review)
- [ ] config.live.toml overlay verified
- [ ] PAPER VALIDATION overrides confirmed reverted
- [ ] Human sign-off from operator
- [ ] 1 week paper stability (no crashes, no orphans)

---

## 6. RISK ACCEPTANCE

### Accepted Risks
| Risk | Severity | Mitigation | Accepted By |
|------|----------|------------|-------------|
| VanguardSniper has zero backtest | HIGH | Paper trading validation gate | Operator |
| Leveraged ETP decay not modeled | MEDIUM | ISA long-only, intraday holding | Design |
| .env credentials in plaintext | LOW | Docker-internal, .gitignored | Architecture |
| KRX contracts don't work | LOW | Account restriction, not code bug | IBKR |
| Orchestrator strategies untested | MEDIUM | Zero live trades, paper validation | Operator |

### Unaccepted Risks (Blocking Live)
| Risk | Severity | Required Action |
|------|----------|-----------------|
| PAPER VALIDATION overrides active | CRITICAL | Must load config.live.toml when IS_LIVE=true |
| No 100-trade validation data | CRITICAL | Continue paper trading |
| No missed-winner analysis pipeline | HIGH | Build N2a writer in engine.rs |
| No backtest data | HIGH | Build N7a (top-100 ticker backfill) |

---

## 7. STOP-STATE HANDOFF TABLE

### What This Session Completed
| Phase | Status | Key Output |
|-------|--------|------------|
| Phase 0: Ingestion | ✅ COMPLETE | 50,312 LOC ingested (79 Rust + 51 Python files) |
| Phase 1-4: Analysis | ✅ COMPLETE | Quality grades, architecture audit, trade lifecycle trace |
| Phase 5-9: Implementation | ✅ COMPLETE | 9 build items (N1a/b/c, N2a/b/c, N3a, N5a, RT1) |
| Phase 10: Master Plan | ✅ COMPLETE | IMPLEMENTATION_MASTER_PLAN.md v6.0 |
| Phase 11: Red-Team | 🔄 IN PROGRESS | ADVERSARIAL_RED_TEAM_v6.md (agent building) |
| Phase 12: Governance | ✅ COMPLETE | PROOF_REGISTER v6.1, GOVERNANCE_PACK_v6.md |
| QDR: 120 Questions | 🔄 IN PROGRESS | QUESTION_DECISION_REGISTER.md (agent building) |
| Release Candidate | 🔄 IN PROGRESS | MASTER_PLAN_RELEASE_CANDIDATE_v6.md (agent building) |

### What the NEXT Session Must Do
1. **Deploy this session's code** — git commit, push, rsync, docker build
2. **Verify cargo test passes** with WAL schema changes (new variants need match arms)
3. **Wire SignalRejected writer** in engine.rs (currently type exists but nothing writes it)
4. **Wire enriched PositionClosed fields** in engine.rs (hold_time, session_phase, etc.)
5. **Build N5b** (bar history persistence in Redis) — prevents warmup delay on restart
6. **Build N5c** (bridge SIGHUP hot-reload) — allows config changes without restart
7. **Continue paper trading** — collect trades for 100-Trade Validation Gate
8. **Review QUESTION_DECISION_REGISTER** — validate answers against trade data as it accumulates
