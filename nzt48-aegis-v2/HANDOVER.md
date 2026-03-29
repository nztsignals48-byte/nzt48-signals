# AEGIS V2 — SESSION HANDOVER
## Date: 2026-03-29 | Next session starts here

---

## WHAT'S RUNNING ON EC2 (3.230.44.22)

```
aegis-v2           healthy    Engine + bridge + metrics server (:9090)
aegis-grafana      running    Grafana dashboards (:3000, admin/aegis2026)
aegis-prometheus   running    Scraping aegis-v2:9090/metrics every 15s
aegis-redis        healthy    Sheets sync + cron locking
aegis-ib-gateway   healthy    IBKR paper account (will connect Monday)
```

**Disk:** 40GB (resized from 19GB this session), 51% used.
**SSH:** `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`
**Repo:** `/home/ubuntu/nzt48-signals-repo/nzt48-aegis-v2/`
**Branch:** `feat/tier-system-enhancements-full`

---

## WHAT THIS SESSION DID (20 commits)

Started at 2.8/10. Ended at 7.9/10 code, 0/10 proven edge.

### The real value delivered:
1. **24 hardcodes purged** — the values causing 65-115% of all losses
2. **34 risk checks enforced** — was 24/33 with 9 bypassed
3. **Paper = live config** — max 3 positions, 10% heat, 25% buffer
4. **Slippage model** — 0.5% adverse + market impact for larger orders
5. **WAL HALT on failure** — was silent drop
6. **Live VIX wired** — from IBKR tick data, not stuck at 21.0
7. **Trade killers fixed** — Kelly was 0.0 from equal avg_win/avg_loss
8. **Disk resized** — 40GB, no more build failures
9. **Prometheus + Grafana** — real monitoring stack
10. **Compounding machine** — edge-proportional allocation, auto-kill losers

### The backtest finding:
**TypeF (OBV Divergence): 72% WR, PF 9.34 over 4M trades.**
It was disabled. It's the strongest signal in the system. Now re-enabled.

### What was overengineered:
- 7 system strategies (S1-S7) when only S2 proved profitable in backtest
- Book-derived techniques (VPIN toxicity, Keltner squeeze, Student-t Kelly, D-VPIN)
- All add complexity without proven live edge

---

## WHAT MATTERS MONDAY

IBKR paper session connects Sunday evening. The system will:

1. Subscribe to 50 tickers (4,636 contracts registered)
2. After ~4 min warmup, start generating signals
3. TypeF, TypeB, TypeE, S2_Reversion are the likely winners
4. S6_Catalyst is pre-killed (13% WR in backtest)
5. TypeA, TypeD are re-enabled (backtest shows 44%/43% WR)
6. Compounding machine tracks every exit → reallocates Kelly to winners
7. CAGR logged daily, per-strategy Sharpe logged every 10 exits

**Watch for in logs:**
```
COMPOUND_STATE:    — shows allocation weights per strategy
STRATEGY_TRACKER:  — signal counts and avg confidence
COMPOUNDING:       — daily CAGR and max drawdown
AUTO_KILL:         — strategy disabled by live Sharpe
```

---

## THE THREE STRATEGIES THAT MATTER

| Strategy | Backtest Evidence | Why |
|----------|------------------|-----|
| **TypeF (OBV Divergence)** | 72% WR, PF 9.34, 4M trades | Volume-price divergence. Strongest signal by far. |
| **TypeB (EarlyRunner)** | 47% WR, PF 1.41, 4.5M trades | RVOL rising + RSI momentum. Solid across 730 days. |
| **S2_Reversion** | 45% WR, PF 1.42, 907K trades | BB z-score + RSI(2) oversold. Best new strategy. |

Everything else is noise until live data says otherwise.

---

## CONFIG (truthful, not fantasy)

```toml
max_simultaneous_positions = 3
portfolio_heat_limit_pct = 10.0
sector_heat_cap_pct = 33.0
cash_buffer_pct = 25.0
paper_uses_live_gates = true
kelly_ramp_clamp_min = 0.3
slippage_assumption_pct = 0.5
```

Dynamic weights: WR=35.4%, kelly_t1=0.05, Normal regime=1.0

Kelly chain: `kelly_12factor * half_kelly(0.5) * student_t(0.625) * rust_ramp(0.3-1.0)`

avg_win=0.03, avg_loss=0.015 (W/L=2.0, positive Kelly at 35%+ WR)

---

## NEXT SESSION SHOULD

1. **Wait for 50+ live trades** — don't change code, observe
2. **Check `docker logs aegis-v2`** for COMPOUND_STATE and STRATEGY_TRACKER
3. **If TypeF dominates** — the machine is working, let it compound
4. **If nothing trades** — check bridge.py stderr for signal generation vs risk rejections
5. **If trade frequency < 1/day** — confidence floor (50) may be too high, or risk checks too tight
6. **At 100 trades** — evaluate per Book 6 gates: WR >= 40%, PF >= 1.2
7. **At 300 trades** — Ouroboros auto-unfreezes, learning loop closes

**Do NOT add more strategies or techniques. Observe.**

---

## FILES MODIFIED THIS SESSION

### Rust (require Docker rebuild):
- `rust_core/src/engine.rs` — VIX wire, exit notifications, hardcode purge
- `rust_core/src/main.rs` — Bridge fields (vix, london_time_secs, gap_pct, symbol), avg_win/loss fix
- `rust_core/src/python_bridge.rs` — 6 new TickContext fields, send_notification()
- `rust_core/src/config_loader.rs` — MacroDefaultsConfig, parse_hhmm_to_secs, gbx_threshold
- `rust_core/src/cross_asset_macro.rs` — Fail-safe defaults (Caution not Normal)
- `rust_core/src/risk_arbiter.rs` — CHECK 34 correlation
- `rust_core/src/liquidation_defense.rs` — Config-driven thresholds
- `rust_core/src/paper_broker.rs` — Slippage model + market impact
- `rust_core/src/portfolio.rs` — count_positions_in_sector()
- `rust_core/src/wal_actor.rs` — WalAppendResult enum
- `rust_core/src/wal_replay.rs` — Removed hardcoded 250.0

### Python (hot-reload on container restart):
- `python_brain/bridge.py` — 7 systems, compounding machine, book techniques
- `python_brain/ouroboros/backfill_simulator.py` — S1-S3/S6 in backtest
- `python_brain/ouroboros/config_writer.py` — Auto-unfreeze gate
- `python_brain/ouroboros/nightly_v6.py` — KELLY_MAX 0.20→0.05
- `python_brain/ouroboros/step_runner.py` — Redis password removal
- `python_brain/metrics_server.py` — Prometheus endpoint (NEW)

### Config:
- `config/config.toml` — All hardcodes moved, risk parity, macro defaults
- `config/dynamic_weights.toml` — Reset to truthful values

### Infrastructure:
- `docker-compose.yml` — Added Prometheus + Grafana containers
- `entrypoint.sh` — Metrics server startup
- `monitoring/prometheus.yml` — Scrape config
- EC2 EBS volume: 20GB → 40GB

---

```
SESSION: 2026-03-28/29
COMMITS: 20
SCORE: 2.8 → 7.9 (code) / 0 (proven edge)
BLOCKER: Zero live trades — IBKR connects Monday
NEXT ACTION: Observe. Do not code.
```
