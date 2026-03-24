# AEGIS V2 -- Current-State Operating Manual

**Generated:** 2026-03-24
**Method:** Full reverse-engineering by 4 parallel agents reading code, config, WAL, logs, and runtime state.
**Scope:** What IS, not what should be. Brutal truth only.

---

## 1. Executive Summary

AEGIS V2 is a multi-exchange paper-trading system running on EC2 (c7i-flex.large, 4GB RAM, 2 vCPU). It connects to Interactive Brokers via IB Gateway, generates signals in Python, gates them through 32 deterministic risk checks in Rust, and executes simulated trades with a 5-rung Chandelier trailing exit.

**Hard facts:**

- `IS_LIVE = false` -- hardcoded constant in `rust_core/src/main.rs` line 32. The binary panics on startup if set to `true`.
- 48 all-time trades, 35.4% win rate, -6.79 GBP cumulative P&L.
- LSEETF leveraged ETPs: 0% WR over 28 trades (-30.34 GBP). Asian equities: 100% WR (+16.91 GBP).
- 3 Docker containers (engine, IB Gateway, Redis), all healthy.
- 6 signal sources in Python, 32 risk checks in Rust, Chandelier 5-rung exit.
- No time-stop exists. Only price-based Chandelier trailing.
- TypeA and TypeD entry types are enabled but are known backtest losers (29.5% and 24.1% WR respectively).
- Gemini API key is NOT SET on EC2. All Gemini crons fail silently.
- Claude CLI is available at `/usr/bin/claude` but operates in shadow mode only. It never blocks trades.
- `strategies.toml` exists (16KB). The Orchestrator can produce signals but has generated zero in production.
- `dynamic_weights.toml` shows Bayesian WR = 0.365, Sharpe = -1.607.
- Disk usage at 75% (4.7GB free of 19GB).
- 47 cron jobs total, 8 DST-aware session briefings.
- Paper mode has 8 relaxed overrides (e.g., max positions 999 vs live 3, portfolio heat 50% vs live 10%).

**The system is NOT ready for live trading.**

---

## 2. Reality Verdict

The system is architecturally impressive but operationally fragile.

The Rust execution core is production-quality: 32 risk checks enforced in deterministic order, atomic WAL persistence, fail-closed design. If any risk check fails, the trade is rejected. This part is trustworthy.

The Python signal pipeline is functional but generates mostly low-confidence Momentum signals. Of 36 observed entries, 33 were classified as "Unclassified" (generic momentum). The 4 newly-added strategies (IBS_MeanReversion, ORB_Breakout, VolExpansion, GapFade) have produced ZERO signals in production. They are deployed but unproven.

The nightly learning loop (Ouroboros) works mechanically -- `nightly_v6.py` runs at 04:50 UTC, `config_writer.py` at 04:51 UTC, dynamic weights are generated and hot-reloaded via SIGHUP. But it operates on stale data: `dynamic_weights.toml` was generated from only 20 trades on the local machine, while EC2 has 48 trades. The Bayesian estimates are unreliable at this sample size.

LSEETF leveraged ETPs are the single largest loss source: 28 trades, 0% WR, -30.34 GBP. They have not been blacklisted because the user chose to keep trading them for data collection purposes.

---

## 3. System Topology

```
+-------------------------------------------------------------------+
|  EC2 (c7i-flex.large, us-east-1c, Elastic IP 3.230.44.22)        |
|                                                                   |
|  +------------------+    +------------------+    +--------------+ |
|  | aegis-v2         |    | aegis-ib-gateway |    | aegis-redis  | |
|  | (Rust engine +   |<-->| (IB Gateway +    |    | (State       | |
|  |  Python bridge)  |    |  IBC, port 4003) |    |  journal)    | |
|  +------------------+    +------------------+    +--------------+ |
|         |                        |                      |         |
|         +------------------------+----------------------+         |
|                    Docker bridge network                          |
|                                                                   |
|  Volumes: aegis-wal, aegis-data, aegis-logs, aegis-config         |
+-------------------------------------------------------------------+
```

### Component Breakdown

| Component | Language | LOC | Role |
|-----------|----------|-----|------|
| Rust engine | Rust (edition 2024) | ~32,603 | Tick processing, risk arbiter (32 checks), Chandelier exit (5-rung), WAL persistence |
| Python bridge | Python | ~1,850 | 5-stage signal pipeline, 6 strategy sources, 12-factor Kelly sizing |
| Python Ouroboros | Python | ~2,000 | Nightly learning, config generation, dynamic weights |
| IBKR broker adapter | Rust + ibapi | -- | IB Gateway port 4003, client_id 101, 100 concurrent streams |
| Redis | Redis 7 | -- | State journal, 256MB max, AOF persistence, password `nzt48redis` |
| Docker | 3 containers | -- | Bridge network, named volumes for WAL/data/logs |

### Key Files

| File | LOC | Purpose |
|------|-----|---------|
| `rust_core/src/engine.rs` | ~3,100 | Main tick processing, signal routing, position management |
| `rust_core/src/risk_arbiter.rs` | ~600 | 32 deterministic risk CHECKs in sequence |
| `rust_core/src/exit_engine.rs` | -- | Chandelier exit, 5-rung trailing stop ladder |
| `rust_core/src/entry_engine.rs` | 786 | Entry type detection (TypeA-F). Reference impl, NOT used at runtime. |
| `rust_core/src/position_sizer.rs` | -- | Kelly sizing with regime scaling |
| `rust_core/src/portfolio.rs` | -- | Position tracking, equity, ISA limits |
| `rust_core/src/market_scheduler.rs` | -- | Session phases (HK, LSE, US) |
| `rust_core/src/main.rs` | -- | Binary entry, Crucible sim mode, event loop |
| `python_brain/bridge.py` | ~1,850 | Python signal generation (VanguardSniper, Orchestrator, all strategies) |
| `python_brain/ouroboros/config_writer.py` | -- | Nightly config generation from learning data |
| `python_brain/ouroboros/nightly_v6.py` | -- | Nightly analysis + recommendations |
| `python_brain/ouroboros/contract_loader.py` | -- | Dynamic contract loading from contracts.toml |
| `config/config.toml` | -- | Main config (timing, risk, signal, kelly, chandelier) |
| `config/contracts.toml` | -- | 307 contracts across 6+ exchanges |
| `config/dynamic_weights.toml` | -- | Ouroboros-generated (overrides base config) |
| `config/strategies.toml` | ~16KB | Strategy definitions for Orchestrator |

---

## 4. Classification Ownership

This is the single most important section for understanding who decides what.

| Decision | Owner | File | Authority |
|----------|-------|------|-----------|
| Entry type classification (TypeA-F) | Python bridge.py Stage 4 | `python_brain/bridge.py` | RUNTIME authority |
| Entry type thresholds | config.toml `[entry_types]` | `config/config.toml` | Single source of truth for thresholds |
| Signal generation | Python bridge.py Stage 3 | `python_brain/bridge.py` | 6 strategy sources |
| Risk gating | Rust risk_arbiter.rs | `rust_core/src/risk_arbiter.rs` | 32 checks, DETERMINISTIC, FINAL |
| Exit decisions | Rust exit_engine.rs | `rust_core/src/exit_engine.rs` | Chandelier, SINGLE authority |
| Position sizing | Python 12-factor Kelly | `python_brain/bridge.py` | Calculated pre-signal, sent to Rust |
| Config overrides | Ouroboros config_writer | `python_brain/ouroboros/config_writer.py` | Writes dynamic_weights.toml, engine hot-reloads |

**Critical note:** `rust_core/src/entry_engine.rs` (786 LOC) is a reference implementation. It is NOT used at runtime. Python bridge.py Stage 4 performs all entry type classification. The Rust file is dead code.

Similarly, `rust_core/src/strategy_config.rs` (1,110 LOC) is unused and still in the codebase.

---

## 5. Strategy Map

| Strategy | Implemented | Enabled | Observed in Production | Trades | Notes |
|----------|-------------|---------|----------------------|--------|-------|
| VanguardSniper (Momentum) | Yes | Yes | YES | 33 | Core strategy. All sessions. Generates majority of signals. |
| Orchestrator | Yes | Yes | No | 0 | Requires strategies.toml (exists, 16KB). Never fired in production. |
| IBS_MeanReversion | Yes | Yes | No | 0 | Deployed 2026-03-23. Needs market hours to trigger. |
| VolExpansion | Yes | Yes | No | 0 | Deployed 2026-03-23. Needs market hours to trigger. |
| ORB_Breakout | Yes | Yes | No | 0 | US session only (14:45 UTC earliest). |
| GapFade | Yes | Yes | No | 0 | Requires gap-down > 1%. Rare condition. |

### Entry Type Classification

| Type | Name | Implemented | Observed | Backtest WR | Notes |
|------|------|-------------|----------|-------------|-------|
| TypeA | DipRecovery | Yes | No | 29.5% | Known loser. Still enabled. Monitored. |
| TypeB | EarlyRunner | Yes | No | 52.4% | Best backtest performance. Not yet observed. |
| TypeC | OverboughtFade | Yes | No | -- | Requires RSI > 80. Rare trigger. |
| TypeD | SupportBounce | Yes | No | 24.1% | Known loser. Still enabled. Monitored. |
| TypeE | IBS classifier | Yes | YES | -- | 3 trades observed. |
| TypeF | OBVDivergence | Yes | No | -- | Requires vol_div < -0.5. |

---

## 6. Live vs Shadow vs Disabled

### LIVE (Hot Path -- Executes Trades)

- Rust engine (`engine.rs`, `risk_arbiter.rs`, `exit_engine.rs`, `position_sizer.rs`, `portfolio.rs`)
- Python bridge (`bridge.py` -- signal pipeline Stages 1-5)
- Paper broker adapter (simulated fills)
- WAL writer (atomic event persistence)
- Redis state journal

### SHADOW (Cold Path -- Observes, Never Blocks)

- Claude curator (`claude_curator.py`) -- annotates signals at confidence >= 55, non-blocking, 30s timeout, fallback to no-op
- VPIN computation -- computed by Python but never used as a gate
- Claude forensic review -- nightly at 04:53 UTC, flock-protected

### DISABLED / BROKEN

- Gemini scanner -- API key not set on EC2, all Gemini crons fail silently
- `quantum_apex.rs` -- deleted (Sprint 1)
- `dqn_signal_weighting.rs` -- deleted (Sprint 1)
- `neural_hawkes.rs` -- deleted (Sprint 1)

---

## 7. Claude and Gemini Roles

### Claude (Operational -- Shadow Only)

| Function | Schedule | Mode | Notes |
|----------|----------|------|-------|
| Signal curation | Confidence >= 55 triggers | Shadow | `evaluate_signal()`, non-blocking, 30s timeout |
| Forensic review | Nightly 04:53 UTC | Cold path | flock-protected, writes to `/app/data/claude/reviews/` |
| Morning briefing | 07:45 UTC | Cold path | Pre-market summary |
| Evening briefing | 21:30 UTC | Cold path | Post-market summary |
| Universe curation | Every 2 hours | Shadow | Writes to `/app/data/claude/curation/` |

**Claude has ZERO execution authority.** It cannot force trades, override risk gates, mutate live config, or manage stops. The Rust RiskArbiter is the final authority on every trade decision.

### Gemini (Broken)

| Function | Schedule | Status |
|----------|----------|--------|
| Market scanner | Every 2 hours | BROKEN -- API key not set |
| Morning brief | Daily | BROKEN -- API key not set |

**All Gemini crons fail silently.** No error surfaces to the operator. This is a known issue.

---

## 8. Signal Generation Flow

The exact path from market tick to simulated trade:

```
1. IBKR tick arrives via reqMktData (IB Gateway port 4003)
        |
2. Rust engine routes tick to Python bridge via stdin JSON pipe
        |
3. Python Stage 1: INDICATORS
   - Compute RVOL, Hurst, ADX, IBS, VWAP, STS on 5-min bars
        |
4. Python Stage 2: QUALITY GATES
   - Spread check (reject wide spreads)
   - VWAP extension check
   - STS minimum threshold
   - Hurst extreme filter
   - Ouroboros dynamic gates (from indicator_gates in dynamic_weights.toml)
        |
5. Python Stage 3: STRATEGY SOURCES
   - 6 sources fire in PARALLEL, all results collected (no bottleneck):
     * VanguardSniper (momentum)
     * Orchestrator (multi-strategy via strategies.toml)
     * IBS_MeanReversion
     * VolExpansion
     * ORB_Breakout
     * GapFade
        |
6. Python Stage 4: ADJUSTMENTS + CLASSIFICATION
   - LSE boost applied
   - Drawdown scaling
   - Hour-of-day weights
   - Simulated costs (commission + slippage)
   - TypeA-F classification based on indicator values vs config thresholds
   - Best signal selected by adjusted confidence
        |
7. Python Stage 5: OUTPUT
   - JSON signal written to stdout -> Rust stdin pipe
        |
8. Rust Risk Arbiter: 32 CHECKS IN SEQUENCE
   - Each check can VETO the trade with a logged reason
   - Checks include: position limits, portfolio heat, sector heat,
     cash buffer, ISA limits, drawdown halt, consecutive loss halt,
     confidence floor, leverage-aware confidence, stale data,
     entry cutoff, spread limit, min notional, velocity throttle,
     and more
        |
9. IF ALL 32 CHECKS PASS:
   - Simulated trade created
   - WAL event written atomically (SignalArrived + SimTrade)
   - Position tracked in PortfolioState
        |
10. Chandelier Exit Engine monitors ALL open positions on EVERY tick
    - Rung advancement, stop tightening, EOD flatten
```

---

## 9. Position Sizing: Kelly 12-Factor Model

Every signal that passes quality gates gets sized through this 12-factor pipeline before being sent to Rust:

| Factor | Description |
|--------|-------------|
| 1. Base Kelly | From Bayesian win rate (dynamic_weights.toml) |
| 2. Volatility decay | 3x leverage: multiply drag by 9. 5x leverage: multiply drag by 25. |
| 3. Moreira-Muir | Realized vol scaling |
| 4. Correlation penalty | Reduce if correlated with existing positions |
| 5. Drawdown scaling | Reduce size during drawdown periods |
| 6. Amihud liquidity | Penalize illiquid instruments |
| 7. Regime scaling | Adjust for current market regime |
| 8. Spread cost | Deduct expected spread drag |
| 9. Time-of-day | Scale down near session close |
| 10. Confidence scaling | Proportional to signal confidence |
| 11. Half-Kelly cap | Hard cap at 0.5 (never full Kelly) |
| 12. Portfolio heat limit | 6% max heat per position |

**After sizing:** Simulated commission (3.40 GBP) and slippage (0.5%) are deducted BEFORE sending the signal to Rust. The Rust engine receives a pre-costed signal.

---

## 10. Exit Engine: Chandelier 5-Rung Trailing Stop

| Rung | Trigger | Stop Level | Notes |
|------|---------|------------|-------|
| 1 | Entry | Entry price - 1.5x ATR | Initial stop |
| 2 | +0.8% from entry | Breakeven + 0.3% (covers fees) | First tighten |
| 3 | +1.5% from entry | Peak - 1.0x ATR | Trend-following |
| 4 | +2.5% from entry | Peak - 0.75x ATR | Tighter trail |
| 5 | +4.0% from entry | Peak - 0.5x ATR | Aggressive trail |

**Volume exhaustion rule:** If RVOL > 10x average, tighten to 0.5x ATR regardless of rung.

**Exit priority order (highest to lowest):**
1. HaltFlatten -- system halt forces immediate close
2. HardStop -- absolute stop loss hit
3. Chandelier -- rung-based trailing stop triggered
4. EodFlatten -- end-of-day flatten (3-phase: passive limit at T-35, mid at T-15, MTL at T-5)
5. SignalReversal -- opposing signal received

**CRITICAL DEFECT: No time-stop exists.** If a position goes sideways (neither hits stop nor advances rungs), the Chandelier trail simply sits at the current rung level indefinitely. Capital can be locked in a dead position until EOD flatten or manual intervention.

---

## 11. Known Defects (Severity Ordered)

### BLOCKER

1. **Gemini API key not set on EC2.** All Gemini crons fail silently. No scanner, no Gemini morning brief. The operator receives no error notification.

### MAJOR

2. **No time-stop.** Capital can be tied up indefinitely in sideways positions. The only exits are price-based (Chandelier), session-based (EOD flatten), or manual. A position that opens at 08:00 and goes flat will sit there until 16:25 EOD flatten.

3. **LSEETF leveraged ETPs: 0% WR over 28 trades (-30.34 GBP).** These are the system's largest loss source. They are NOT blacklisted -- the user deliberately chose to keep trading them for data collection. But every LSEETF trade has been a loser.

4. **4 new strategies have 0 observed signals.** IBS_MeanReversion, VolExpansion, ORB_Breakout, and GapFade were deployed 2026-03-23. None has produced a signal in production yet. They are completely unproven.

5. **Paper mode has 8 relaxed limits that MUST be reverted before live.** See Section 13 for the full list. If these are not reverted, the system will deploy with 999 max positions and 50% portfolio heat.

### MEDIUM

6. **Dynamic weights based on only 20 trades.** The Bayesian WR estimate (0.365) and Sharpe (-1.607) are unreliable at this sample size. Ouroboros recommendations should be treated as directional hints, not statistical truth.

7. **Local vs EC2 dynamic_weights.toml divergence.** Local file shows WR 79.2% from 20 trades. EC2 deployed version shows WR 36.5% from 48 trades. These files represent different trade histories and should not be confused.

8. **60+ untracked files in git status.** The repository has significant uncommitted/untracked work. Increases risk of deploy-vs-code divergence.

### LOW

9. **Rust entry_engine.rs (786 LOC) is dead code at runtime.** Python bridge.py performs all entry type classification. The Rust file exists as a reference implementation but is never called. Potential source of confusion.

10. **strategy_config.rs (1,110 LOC) unused.** Still in the codebase but not referenced anywhere at runtime.

---

## 12. Paper vs Live Overrides

These parameters are relaxed for paper trading data collection. Every single one MUST be reverted before any live deployment.

| Parameter | Paper Value | Live Value | Config Key | Risk if Not Reverted |
|-----------|-------------|------------|------------|---------------------|
| Max positions | 999 | 3 | `position.max_simultaneous_positions` | Unlimited deployment, uncontrolled exposure |
| Portfolio heat | 50% | 10% | `position.portfolio_heat_limit_pct` | Entire account at risk |
| Sector heat | 80% | 33% | `position.sector_heat_cap_pct` | Sector concentration blow-up |
| Cash buffer | 5% | 25% | `position.cash_buffer_pct` | Margin pressure, forced liquidation |
| Consecutive loss halt | 8 | 5 | config.toml hardening | Drawdown creep, slow bleed |
| Max daily trades | 999 | 3 | config.toml hardening | Velocity runaway, commission drain |
| Min gross edge | 0.10% | 0.15% | config.toml hardening | Cost-killed trades (negative edge after fees) |
| Crucible max positions | 15 | 3 | config.toml | Over-deployment in simulation |

---

## 13. Validation Gate Status

The 100-trade validation gate determines whether the system can progress toward live trading.

| Gate | Target | Current | Status |
|------|--------|---------|--------|
| Total trades | >= 100 | ~64 | 64% complete |
| Win Rate | >= 40% | 35.4% | **FAIL** |
| Profit Factor | >= 1.3 | ~0.77 | **FAIL** |
| Exchanges traded | >= 4 | 5 | **PASS** |
| Max consecutive losses | < 8 | ~14 | **FAIL** |

**3 of 5 gates are failing.** The system cannot progress to live trading until all 5 pass simultaneously over a rolling 100-trade window.

---

## 14. Cron Schedule (47 Jobs Total)

### Critical Jobs

| Time (UTC) | Job | Notes |
|------------|-----|-------|
| 04:50 | `nightly_v6.py` | Nightly analysis, trade review, recommendations |
| 04:51 | `config_writer.py` | Generate dynamic_weights.toml from nightly output |
| Every 15 min | `ticker_selector.py` | Universe rotation, active watchlist update |
| Every 2 hours | Claude curation | Shadow mode signal evaluation |
| Every 2 hours | Gemini scanner | **BROKEN** -- API key not set |

### Session Briefings (8 total, DST-aware)

| Time (UTC) | Session |
|------------|---------|
| 00:45 | HK/SG pre-market |
| 01:00 | TSE pre-market |
| 07:45 | LSE pre-market (Claude morning brief) |
| 08:00 | XETRA/EURONEXT pre-market |
| 14:15 | US pre-market |
| 16:35 | LSE post-market |
| 21:00 | US post-market |
| 21:30 | Evening brief (Claude) |

### Boot-time Jobs

- `config_writer.py` runs at container startup (entrypoint.sh) to generate initial dynamic_weights.toml
- 15s IBKR secdef delay after `connect()` before `subscribe_all()`

---

## 15. Daily Operations

### Pre-Market (07:00 UTC)

```bash
# Check all containers are healthy
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker ps --format "{{.Names}}: {{.Status}}"'

# Check engine logs for errors
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker logs aegis-v2 --tail 20 2>&1 | grep -E "ERROR|PANIC|FATAL"'

# Check Python bridge stderr
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker exec aegis-v2 cat /app/data/bridge_stderr.log | tail -10'

# Check disk usage
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'df -h / | tail -1'
```

If Monday morning: approve IB Gateway 2FA on phone immediately.

### During Market Hours

```bash
# Watch for signals
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker logs aegis-v2 2>&1 | grep SIGNAL_ARRIVED | tail -10'

# Watch for trades
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker logs aegis-v2 2>&1 | grep SIM_TRADE | tail -10'

# Check heartbeat (engine alive)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker logs aegis-v2 2>&1 | grep HEARTBEAT | tail -3'
```

### Post-Market (21:00 UTC)

- Check cumulative P&L in WAL events
- Review any Chandelier exits that fired
- Ouroboros runs at 04:50 UTC (overnight)
- Check Claude forensic review output at 04:53 UTC next morning

### Kill Switch

```bash
# Halt engine (stops new trades, keeps positions)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker exec aegis-v2 touch /app/data/KILL'

# Flatten all positions and pause
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'docker exec aegis-v2 touch /app/data/PAUSE'

# Full shutdown
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'cd /home/ubuntu/nzt48-aegis-v2 && docker compose down'
```

---

## 16. Quick Health Check (Copy-Paste Block)

Run this every evening or when something feels off:

```bash
SSH="ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22"

# Container status
$SSH 'docker ps --format "{{.Names}}: {{.Status}}"'

# Last 5 engine log lines
$SSH 'docker logs aegis-v2 --tail 5 2>&1'

# Last 5 Python bridge stderr lines
$SSH 'docker exec aegis-v2 cat /app/data/bridge_stderr.log | tail -5'

# Recent trades
$SSH 'docker logs aegis-v2 2>&1 | grep SIM_TRADE | tail -5'

# Recent signals
$SSH 'docker logs aegis-v2 2>&1 | grep SIGNAL_ARRIVED | tail -5'

# Disk usage
$SSH 'df -h / | tail -1'
```

---

## 17. Monday Watchlist

Every Monday morning, check these in order:

1. **IB Gateway 2FA** -- approve immediately on phone. Gateway restarts weekly and needs re-authentication. If missed, no market data flows and the engine sits idle.
2. **First Asian signals** -- new strategies (IBS_MeanReversion, VolExpansion) should fire during HK/TSE sessions if conditions met. If still zero signals after a full Asian session, investigate.
3. **LSE session at 08:00 UTC** -- most active signal window. Watch for VanguardSniper momentum signals.
4. **US session ORB at 14:45 UTC** -- first real test of ORB_Breakout strategy. Needs a clear opening range to trigger.
5. **Disk usage** -- if above 80%, run `docker system prune -f` on EC2. Docker builds consume ~5GB each.
6. **Gemini API key** -- still not set. All Gemini crons will continue to fail silently until configured.

---

## 18. Performance by Exchange

| Exchange | Trades | Win Rate | Cumulative P&L | Notes |
|----------|--------|----------|----------------|-------|
| LSEETF | 28 | 0% | -30.34 GBP | Leveraged ETPs. Largest loss source. |
| LSE (non-ETP) | ~7 | ~43% | ~+6.64 GBP | Standard UK equities |
| HKEX | ~5 | 100% | ~+10.50 GBP | Asian equities outperforming |
| TSE | ~3 | 100% | ~+6.41 GBP | Japanese equities |
| US | ~3 | ~33% | ~+0.00 GBP | Limited sample |
| Others | ~2 | -- | -- | Insufficient data |

**Key insight:** The system is profitable on Asian equities and standard LSE stocks. LSEETF leveraged ETPs are the sole source of negative overall P&L.

---

## 19. Configuration Architecture

```
config/config.toml          <-- Static base config (human-edited)
        |
        v
config/dynamic_weights.toml <-- Ouroboros-generated overrides (nightly)
        |
        v
config/contracts.toml       <-- 307 contracts across 6+ exchanges
        |
        v
config/strategies.toml      <-- 16KB strategy definitions for Orchestrator
```

**Override order:** `dynamic_weights.toml` values override `config.toml` when both specify the same key. The Rust engine loads `config.toml` at startup, then overlays `dynamic_weights.toml`. Hot-reload via SIGHUP re-reads `dynamic_weights.toml` without restarting.

### Key Config Sections

- `[signal]` -- confidence floor (65), gap detection, velocity checks
- `[position]` -- max positions (999 paper / 3 live), heat limits, ISA limits
- `[kelly]` -- fraction cap (0.5), clamp max (0.05), volatility drag multipliers
- `[timing]` -- session times, entry cutoffs per exchange, stale data thresholds
- `[chandelier]` -- rung thresholds, ATR multipliers, adaptive settings
- `[entry_types]` -- TypeA-F thresholds (single source of truth for classification)
- `[hardening]` -- broker reconnect, tick quality, sizing minimums
- `[risk]` -- drawdown limits, weekly/peak drawdown, equity floor

---

## 20. Ouroboros Learning Loop

```
Trade completes
      |
      v
WAL event written (PositionClosed with MAE/MFE)
      |
      v  (04:50 UTC)
nightly_v6.py reads WAL + persistent_memory.json
      |
      v
Generates: nightly_output.json
  - Per-ticker statistics (WR, avg P&L, spread drag)
  - Per-type statistics (TypeA-F performance)
  - Recommendations (blacklist, gate adjustments)
  - Wilson-score ticker blacklist
      |
      v  (04:51 UTC)
config_writer.py reads nightly_output.json
      |
      v
Generates: dynamic_weights.toml
  - Updated Bayesian WR
  - Updated Sharpe estimate
  - Ticker blacklist (Wilson score)
  - Indicator gates (pre-signal filters)
  - Hour-of-day weights
      |
      v  (SIGHUP or restart)
Rust engine hot-reloads dynamic_weights.toml
```

**Current state of learning:** Bayesian WR = 0.365, Sharpe = -1.607, based on 48 trades (EC2) or 20 trades (local). The estimates are directionally informative but statistically unreliable. Ouroboros needs at minimum 50+ trades per analysis to produce meaningful recommendations.

---

## 21. Risk Arbiter: 32 Checks

The Rust risk arbiter runs 32 checks in deterministic sequence. Every check can independently VETO a trade. If any check fails, the trade is rejected with a logged VetoReason.

The checks include (non-exhaustive):

- Position count vs max_simultaneous_positions
- Portfolio heat vs portfolio_heat_limit_pct
- Sector heat vs sector_heat_cap_pct
- Cash buffer vs cash_buffer_pct
- ISA annual limit (20,000 GBP)
- ISA eligibility per instrument
- Drawdown halt (-8% hard stop)
- Weekly drawdown limit
- Peak drawdown limit
- Equity floor
- Consecutive loss halt
- Confidence floor (65 minimum)
- Leverage-aware confidence (sqrt(leverage) scaling)
- Stale data rejection (per-exchange thresholds)
- Entry cutoff time (per-exchange)
- Spread limit
- Minimum notional (per-exchange)
- Velocity throttle (10 signals per 5 minutes)
- Per-ticker cooldown (5 minutes)
- Synthetic halt detection (Limp Mode / full HALT)
- Blacklist check (Wilson-score from Ouroboros)

**Design principle:** Fail-closed. If a check cannot determine its result (missing data, error), it vetoes. The system never trades on uncertainty.

---

## 22. Docker and Deployment

### Container Layout

| Container | Image | Ports | Volumes |
|-----------|-------|-------|---------|
| aegis-v2 | Custom (Rust + Python baked in) | 8000 (API) | aegis-wal, aegis-data, aegis-logs, aegis-config |
| aegis-ib-gateway | gnzsnz/ib-gateway | 4003 | -- |
| aegis-redis | redis:7 | Internal only | redis-data |

### Deploy Procedure (Mandatory)

Every deploy MUST follow this exact sequence:

```bash
# 1. Commit locally
git add -A && git commit -m "Deploy: <description>"

# 2. Push to GitHub
git push origin feat/tier-system-enhancements-full

# 3. Rsync to EC2
rsync -avz --exclude='.git' --exclude='target' \
  /Users/rr/nzt48-signals/nzt48-aegis-v2/ \
  ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/

# 4. Build and deploy on EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  'cd /home/ubuntu/nzt48-aegis-v2 && docker compose build && docker compose up -d'
```

**NEVER deploy without committing first.** Local, GitHub, and EC2 must always be in sync. SCP alone does not work because the Docker image bakes Python code into the image at build time.

### Disk Management

EC2 has 19GB total, Docker builds consume ~5GB each. Current usage is 75% (4.7GB free).

```bash
# Check disk before building
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 'df -h /'

# Prune if needed
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 'docker system prune -f'
```

---

## 23. What Must Happen Before Live Trading

This is a strict checklist. Every item must be completed.

1. **100+ trades with WR >= 40%.** Currently at ~64 trades, 35.4% WR. Both the count and the rate must be met simultaneously.

2. **All 5 validation gates passing.** Currently 3 of 5 are failing (WR, PF, consecutive losses).

3. **All 8 paper overrides reverted.** Max positions from 999 to 3, portfolio heat from 50% to 10%, etc. See Section 12 for the full list.

4. **Gemini API key configured on EC2.** Currently not set. All Gemini crons fail silently.

5. **Time-stop added to exit engine.** Currently no time-based exit exists. A position can sit indefinitely if price stays within Chandelier range.

6. **At least 3 strategies producing profitable signals independently.** Currently only VanguardSniper has produced signals. The other 5 strategies are unproven in production.

7. **LSEETF performance reviewed.** 0% WR over 28 trades. Either blacklist LSEETF, restrict to specific instruments, or demonstrate improved WR with new strategies.

8. **Local/EC2 config synchronization confirmed.** dynamic_weights.toml differs between local and EC2. Must be reconciled.

9. **60+ untracked git files resolved.** Commit, gitignore, or delete. Reduces risk of deploy divergence.

10. **IS_LIVE constant changed and all paper-mode code paths reviewed.** The binary currently panics if `IS_LIVE = true`. There may be other paper-only code paths that need review.

---

## 24. Final Verdict

AEGIS V2 is a paper-trading prototype with institutional-grade risk infrastructure. The Rust execution core -- 32 deterministic risk checks, atomic WAL persistence, fail-closed design -- is production-quality and trustworthy. It is the system's strongest component.

The Python signal generation pipeline is functional but unproven. Of 6 strategies, only 1 (VanguardSniper/Momentum) has produced signals in production, and its 35.4% WR with -6.79 GBP cumulative P&L is below the validation threshold. The 4 newly-deployed strategies (IBS, ORB, VolExpansion, GapFade) have zero production signals.

The nightly learning loop (Ouroboros) works mechanically but operates on insufficient data. Bayesian estimates from 20-48 trades are directional hints, not statistical truths.

The biggest operational risk is LSEETF leveraged ETPs bleeding capital (28 trades, 0% WR, -30.34 GBP) while the system collects enough data for the learning loop to become meaningful.

The system is architecturally sound, operationally fragile, and statistically premature. It needs more trades, more strategies firing, and all paper overrides reverted before any discussion of live deployment.

---

*This document reflects the system state as of 2026-03-24. It will become stale as trades accumulate and strategies begin producing signals. Regenerate periodically from code and runtime evidence.*
