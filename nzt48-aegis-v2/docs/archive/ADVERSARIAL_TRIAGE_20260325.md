# AEGIS V2 — ADVERSARIAL TRIAGE TABLE
## 2026-03-25 03:15 UTC
## Evidence-classified. Deduplicated. Sorted by execution priority.

### Evidence Classes
- **A — Runtime proven**: Observable in deployed config, code grep, or reproducible execution
- **B — Code-proven**: Clearly reachable from current code paths, not yet runtime-observed
- **C — Plausible but unproven**: Reasonable concern, needs direct reproduction
- **D — Interpretive/architectural**: Good judgment, not a bug proof

---

## TIER 0 — ACCOUNT SURVIVAL (Fix before ANY live capital)

| ID | Finding | File:Line | Class | Root Cause | Fix | Can Fix Now? |
|----|---------|-----------|-------|------------|-----|-------------|
| T0-1 | **ask=0 → 4.29B shares**: `(trade_value_gbp / 0.0).max(1.0) as u32` = u32::MAX. `is_valid()` explicitly allows ask=0. | engine.rs:1886, structs.rs:81 | **A** | No zero-price guard before division | Add `if tick.ask <= 0.0 { return; }` before line 1884 | YES — 1 line |
| T0-2 | **GBX boundary bomb**: LSE ticker crossing 500p threshold causes phantom 98.9% crash. Engine goes from £480 to £5.01. Triggers stop, HALT, corrupts GARCH. | engine.rs:971-977 | **B** | Pence detection via crude price threshold, not per-instrument metadata | Add `price_magnifier` field to contracts.toml. Delete >500 heuristic. | YES — 30 min |
| T0-3 | **Bridge zombie**: `needs_respawn` set on crash but NEVER checked by main.rs. Bridge stays as `Some(broken)` forever. 5s timeout per tick on dead pipe. | python_bridge.rs:360, main.rs:728 | **A** | main.rs only checks `is_none()`, never `needs_respawn` | Add `\|\| python_bridge.as_ref().map_or(false, \|b\| b.needs_respawn)` to line 728 | YES — 1 line |
| T0-4 | **Disk-full trades blind**: WAL write fails but engine CONTINUES TRADING. No HALT escalation. Crash = total state loss. | wal_writer.rs:81-86, engine.rs | **B** | WAL error logged but not escalated | On DiskSpaceLow, set `self.arbiter.regime = RiskRegime::Halt` | YES — 5 lines |
| T0-5 | **Ghost positions after crash**: Mid-trade crash → fill arrives at IBKR → engine restarts → fill is "untracked" → position has NO stop-loss, NO management. `request_positions()` returns empty cache. | ibkr_broker.rs:1066-1072, 1269-1273 | **B** | `request_positions()` returns `cached_positions` (empty Vec), not live IBKR API call | Wire `reqPositions()` API call on reconnect. Until then: document as live-blocker. | NO — needs IBKR API work |
| T0-6 | **Terraform: SSH + IB Gateway open to 0.0.0.0/0** | terraform/main_simple.tf:71-84 | **A** | Security groups allow all inbound on ports 22 and 4003 | Restrict to specific IP. Remove port 4003 rule entirely. | YES — 10 min |
| T0-7 | **Deploy destroys all data**: `docker compose down -v` removes all named volumes (WAL, Redis, logs) | deploy/deploy_to_ec2.sh:170 | **A** | `-v` flag in deploy script | Delete `-v` from the command | YES — 1 second |

---

## TIER 1 — REMOVE FALSE SOPHISTICATION

| ID | Finding | File:Line | Class | Root Cause | Fix | Can Fix Now? |
|----|---------|-----------|-------|------------|-----|-------------|
| T1-1 | **GARCH→EVT→CVaR pipeline is dead**: GarchRegistry::empty() at boot, load_garch_params() never called by main.rs, step_0_garch_calibration.py in dead root ouroboros/ | engine.rs:580, main.rs (absent), ouroboros/step_0_garch_calibration.py | **A** | Calibration module left behind in dead package. Loader exists but never called. | Quarantine: comment out garch_registry and evt_registry from Engine struct. Add TODO to wire when data pipeline exists. | YES — 15 min |
| T1-2 | **SmartRouter ETP mappings never populated**: register_etp() never called. find_etp() always returns None. All routing is Direct. | engine.rs:584, smart_router.rs:94-96 | **A** | SmartRouter created with empty etp_mappings Vec | Quarantine: annotate as PHANTOM in code. Do not rely on ETP routing. | YES — 5 min |
| T1-3 | **Redis "source of truth" is a doc lie**: CLAUDE.md says "Redis State Journal is the source of truth." NO Redis connection in Rust engine. | CLAUDE.md, engine.rs, main.rs | **A** | Documentation written from architectural intent, not runtime reality | Fix docs: "State is WAL-based. Redis is used for Python-side locking and Sheets queue only." | YES — 5 min |
| T1-4 | **nightly_output.json NEVER WRITTEN**: 7 modules read it, 0 modules write it. nightly_v6 writes ouroboros_recommendations.json instead. | nightly_v6.py, config_writer.py:54, challenger.py:48, +5 more | **A** | File naming mismatch during restructuring | Either: rename nightly_v6 output to nightly_output.json, OR symlink, OR update all 7 readers | YES — 10 min |
| T1-5 | **max_correlated_positions is dead code**: Defined in config.rs:98, loaded from config.toml, NEVER checked in risk_arbiter or engine. | config.rs:98, risk_arbiter.rs (absent) | **A** | Config field exists but no runtime check implemented | Add CHECK to risk_arbiter: reject if portfolio has N+ positions with same sector/underlying | YES — 30 min |
| T1-6 | **Thompson output write-only**: engine.rs writes thompson_top_k.json, ticker_ranker.py has TODO comment to read it but never does. | engine.rs:2490, ticker_ranker.py:518 | **A** | Write implemented, read never wired | Either wire the read in ticker_selector, or remove the write | YES — 10 min |
| T1-7 | **CHECK 24 (CVaR Heat) phantom**: ctx.volatilities is empty HashMap. CVaR never computed because GARCH pipeline is dead (T1-1). | engine.rs (EvalContext::default), risk_arbiter.rs | **A** | Consequence of T1-1 | Resolves with T1-1 quarantine |  |
| T1-8 | **HayashiYoshida output never read**: Records ticks on every cycle, covariance output consumed by nothing. | engine.rs:1078 | **A** | Compute wired, consumer never wired | Disable: remove hy_engine.record_tick() call. Re-enable when portfolio has 5+ concurrent positions. | YES — 1 line |

---

## TIER 2 — COMPOUNDING TRUTH

| ID | Finding | File:Line | Class | Root Cause | Fix | Can Fix Now? |
|----|---------|-----------|-------|------------|-----|-------------|
| T2-1 | **Confidence floor is a quadruple contradiction**: config.toml=50, dynamic_weights=45 (#[allow(dead_code)]), config.py=65, strategies.toml=65 | 4 files | **A** | Multiple sources, no single authority | Pick ONE source. Delete others. Recommend: config.toml is authoritative, bridge.py reads it. | YES — 20 min |
| T2-2 | **Breakeven counted as loss**: persistent_memory.py:67 `if pnl > 0: wins else: losses`. Systematically deflates WR → wrong Kelly → wrong sizing. | persistent_memory.py:67-68 | **A** | Missing `elif pnl == 0` branch | Add breakeven counter. `if pnl > 0: wins elif pnl < 0: losses else: breakeven` | YES — 3 lines |
| T2-3 | **Kelly cap only works in extreme drawdown**: bridge.py:1796 `if cap < 0.05` — only fires for 0.05 cap. Caps of 0.10 and 0.20 never apply. | bridge.py:1796 | **B** | Wrong comparison operator | Change to: `if cap is not None and best["kelly_fraction"] > cap` | YES — 1 line |
| T2-4 | **TypeF (OBVDivergence) is dead**: volume_divergence() returns bool, bridge checks `vol_div < -0.5`. Bool is never < -0.5. | bridge.py:1253, volume_analytics.py:169 | **A** | Return type mismatch (bool vs expected float) | Fix volume_divergence() to return float divergence metric, or fix TypeF check to use bool | YES — 10 min |
| T2-5 | **VWAP double-update for orchestrator ticks**: Updated in _evaluate_orchestrator AND _compute_indicators. Double-counts volume. | bridge.py:1128, 1298 | **B** | Same VWAP calculator updated twice per tick when orchestrator path fires | Guard: only update in _compute_indicators. Skip if already updated for this tick. | YES — 5 lines |
| T2-6 | **Watchlist frozen by +5.0 hysteresis on 0-1 scale**: Previously-selected tickers get 500% bonus. New high-scoring tickers can never displace incumbents. | ticker_selector.py:1001 | **A** | Hysteresis bonus grossly outsizes the score range | Reduce to +0.05 or +0.10 on the 0-1 scale | YES — 1 line |
| T2-7 | **Paper mode skips ~9 risk checks**: Simulation bypasses position limits, cash buffer, portfolio heat, sector heat, ISA, daily/weekly/peak drawdown. Paper data doesn't predict live behavior. | risk_arbiter.rs:178-180, 307-317 | **A** | Intentional but undocumented consequence | Document explicitly. Consider enabling subset (heat + drawdown) even in sim. | YES — documentation + optional code |
| T2-8 | **Overnight carry stop ratchet violation**: reactivate() uses .min() which can LOWER stop on gap-down. Violates ratchet-up invariant. | overnight_carry.rs:74 | **B** | Wrong comparison operator | Change `.min()` to `.max()` to preserve ratchet-up | YES — 1 character |
| T2-9 | **Crontab flock syntax broken**: `flock -n /tmp/x.lock cd /app && python3 ...` — flock locks `cd`, python runs unlocked. | crontab:33-34,92,96 | **A** | Shell parsing: flock gets `cd` as its command, `&&` chains outside lock | Wrap in `bash -c '...'` | YES — 5 min |
| T2-10 | **daily_drawdown_pct == peak_drawdown_pct**: Both use same `high_water_mark`. Peak DD halt (15%) is dead code — daily DD (4%) always fires first. | portfolio.rs:227,243 | **A** | Missing separate daily HWM tracking | Add `daily_high_water_mark` field, reset on DailyReset WAL event | YES — 20 min |
| T2-11 | **4,636 contracts but only ~110 tradeable**: 3,569 US/SMART stocks loaded but ISA-blocked. Bloats TickerId space, misaligns universe_classification tier IDs. | contracts.toml header vs content | **A** | Universe expansion didn't remove ISA-ineligible contracts | Delete non-ISA-eligible contracts from contracts.toml. Or split into contracts_isa.toml and contracts_research.toml | YES — 15 min |
| T2-12 | **max_simultaneous_lines=200 but IBKR paper limit is 100**: Half of subscriptions silently fail with error 10190. | config.toml:235 | **C** | Config value exceeds IBKR paper account limit | Set to 100. Verify via IBKR docs. | YES — 1 line |
| T2-13 | **FX rates 6 days stale**: fx_rates.toml last updated 2026-03-19. Staleness logged but not acted upon. | fx_rates.toml, currency.rs:233 | **A** | FX refresh cron may have failed. Staleness = log only. | 1. Verify cron is running. 2. Escalate to REDUCE on stale FX (>24h). | YES — 10 min |
| T2-14 | **Flash crash: no price sanity gate**: A tick with bid=ask=last=0.01 passes is_valid(), triggers liquidation at 99.98% loss. Spike filter threshold is 50% — a 99% drop passes it. | structs.rs:78, exit_engine.rs:468 | **B** | is_valid() only checks >= 0, not price continuity. Spike filter catches moderate drops, not crashes. | Add: reject tick if `\|tick.last / prev_price - 1.0\| > 0.50` (50% single-tick move). | YES — 10 lines |

---

## TIER 3 — DEAD CODE / QUARANTINE

| ID | Finding | File:Line | Class | Action |
|----|---------|-----------|-------|--------|
| T3-1 | Root ouroboros/ package (17 files, ~1500 LOC) | ouroboros/ | **A** | DELETE — never imported by production code |
| T3-2 | 5 stale shell scripts at root | AEGIS_*.sh, PHASE_*.sh, EXECUTE_*.sh, THE_MASTER_COMMAND.sh | **A** | DELETE |
| T3-3 | ~28 orphaned Python files in python_brain/ | See Python import audit | **A** | DELETE after confirming zero callers |
| T3-4 | Dead Rust modules: live_readiness.rs, entry_engine detectors | live_readiness.rs, entry_engine.rs quarantined structs | **A** | DELETE live_readiness. Strip quarantined detectors from entry_engine (keep types). |
| T3-5 | KellyCalculator (Rust) — instantiated, never called | engine.rs | **A** | REMOVE from Engine struct |
| T3-6 | RotationScanner — self-documented dead code | engine.rs:1473 | **A** | REMOVE from Engine struct |
| T3-7 | LineBudget — has tests, never used at runtime | engine.rs | **A** | DELETE struct + tests |
| T3-8 | WalActor — replaced by synchronous WalWriter | wal_actor.rs | **A** | KEEP in lib.rs (needed for compilation), annotate UNUSED |
| T3-9 | ~60 stale docs in docs/ not in docs/archive/ | docs/ | **A** | ARCHIVE |
| T3-10 | Stale root .txt files (9 files) | CODEBASE_DUMP_FOR_GEMINI.txt, etc. | **A** | DELETE |

---

## FINDINGS DOWNGRADED FROM PREVIOUS ROUNDS (Overstated or Duplicate)

| ID | Original Claim | Why Downgraded |
|----|---------------|----------------|
| D-1 | "GPD xi sign flip = wrong tail risk" | Moot — entire GARCH→EVT pipeline is dead (T1-1). Fix the sign when wiring the pipeline, not now. |
| D-2 | "IBKR fill remaining_qty/commission always 0" | Class B — only matters for live IBKR, not paper. Fix when wiring live broker. |
| D-3 | "Paper broker overwrites position qty" | Class B — real but only affects paper mode simulation accuracy. Lower priority than account-survival items. |
| D-4 | "110 commits in 11 days = dangerous" | Class D — interpretive. Git velocity is a process concern, not a runtime bug. |
| D-5 | "Blacklist cleared = proven losers re-enabled" | Class D — judgment call. The blacklist may have been too aggressive. Needs runtime data, not code audit. |
| D-6 | "All 326+ issues equally urgent" | Overstated — deduplicated to ~40 unique root causes above. |

---

## EXECUTION ORDER

**Today (30 minutes)**:
1. T0-1: ask=0 guard (1 line)
2. T0-3: bridge zombie fix (1 line)
3. T0-7: remove -v from deploy (1 second)
4. T0-6: lock Terraform security groups (10 min)
5. T2-9: fix crontab flock syntax (5 min)

**This week (4 hours)**:
6. T0-2: price_magnifier in contracts.toml (30 min)
7. T1-1: quarantine GARCH/EVT pipeline (15 min)
8. T1-4: fix nightly_output.json filename (10 min)
9. T2-1: consolidate confidence floor to ONE source (20 min)
10. T2-2: fix breakeven counting (3 lines)
11. T2-3: fix Kelly cap comparison (1 line)
12. T0-4: disk-full HALT (5 lines)
13. T1-3: fix Redis doc lie (5 min)
14. T2-11: remove non-ISA contracts from contracts.toml (15 min)
15. T3-1 through T3-10: mass delete dead code (30 min)

**Before live capital**:
16. T0-5: wire live position reconciliation
17. T2-7: document/fix sim-mode risk bypass
18. T2-14: flash crash price sanity gate
19. T1-5: wire max_correlated_positions check
20. T2-10: separate daily vs peak drawdown HWM
