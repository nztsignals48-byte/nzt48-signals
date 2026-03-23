# RUNTIME_ARTIFACT_MAP.md — AEGIS V2
# SPREAD-ECONOMICS RE-AUDIT
**Generated:** 2026-03-19 | **Re-audit:** 2026-03-20 | **Version:** 2.0 (Spread-first rewrite)
**Board:** CTO, CRO, CIO, Head of Quant Research, Head of Execution, Head of Production/SRE, Head of Autonomous Intelligence Design

---

## Persistent State Files

| File | Location | Written By | Read By | Cadence | Format | Cost-Aware? |
|------|----------|-----------|---------|---------|--------|-------------|
| system_memory.json | /app/data/ | nightly_v6 | config_writer, nightly_v6 | Nightly | JSON | ❌ No cost fields |
| ouroboros_recommendations.json | /app/data/ | nightly_v6 | config_writer | Nightly | JSON | ❌ Gross-based |
| indicator_intelligence.json | /app/data/ | indicator_intelligence | config_writer | Nightly | JSON | ❌ No cost gates |
| gate_vetoes.ndjson | /app/data/ | bridge.py | nightly_v6 | Real-time | NDJSON | ⚠ Logs spread veto but no cost context |
| dynamic_weights.toml | /app/config/ | config_writer | engine (SIGHUP) | Nightly + boot | TOML | ⚠ Spread from commission proxy |
| active_watchlist.json | /app/config/ | ticker_selector | engine | Every 15 min | JSON | ❌ No spread ranking |
| initial_universe.toml | /app/config/ | ticker_selector | config_loader | Every 15 min | TOML | ❌ No cost filter |
| spread_cache.toml | /app/config/ | config_writer | engine | Nightly | TOML | ⚠ Based on commission proxy, not real bid-ask |
| universe_classification.toml | /app/config/ | config_writer | config_loader | Nightly | TOML | ❌ No cost tier |
| fx_rates.toml | /app/config/ | fx_refresh | currency.rs | Every 6h | TOML | ✅ FX rates for cost conversion |
| price_cache.json | /app/data/universe_cache/ | ticker_selector | ticker_selector | Weekly | JSON | ❌ No spread data |

### Cost Data Gap Summary
- **spread_cache.toml** is the ONLY persistent spread data — and it uses commission as a proxy, not real bid-ask spreads
- **ouroboros_recommendations.json** optimizes for gross performance, not net
- **system_memory.json** has no cost categorization (L5 Spread Victim invisible)
- **No file tracks daily trade count** — frequency management impossible without telemetry

---

## WAL (Write-Ahead Log)

| File | Location | Format | Rotation |
|------|----------|--------|----------|
| current.ndjson | /app/events/ | NDJSON (CRC32 + fsync) | On engine restart |
| archive/*.ndjson | /app/events/archive/ | NDJSON | 7-day retention, purged on startup |

### 17 WAL Event Types (verified from types/wal.rs)

| Event Type | Cost Fields | Gap |
|------------|-------------|-----|
| RoutedOrder | ❌ None | Needs: expected_spread, pre_trade_cost_estimate |
| BrokerAck | ❌ None | — |
| FillEvent | ❌ **No spread/slippage** | **N0f: Add spread_at_fill, slippage, side, symbol** |
| ExitSignal | ❌ None | — |
| PositionClosed | ⚠ Has total_commission only | **N0e: Add gross_pnl, spread_entry, spread_exit, slippage_entry, slippage_exit, cost_category** |
| RiskStateChange | ❌ None | — |
| RungAdvanced | ❌ None | — |
| DailyReset | ❌ None | Needs: daily_trade_count, daily_cost_total |
| StateSnapshot | ❌ None | — |
| SystemReady | ❌ None | — |
| NextValidId | ❌ None | — |
| OrphanResolved | ❌ None | — |
| ReconciliationDivergence | ❌ None | — |
| ReconciliationCleared | ❌ None | — |
| QuoteImbalanceInvalidated | ❌ None | — |
| SplitAdjustment | ❌ None | — |
| SystemShutdown | ❌ None | — |

### Critical WAL Cost Gaps (N0e + N0f)

**FillEvent (current):**
```
{ fill_id, order_id, price, quantity, commission, timestamp }
```

**FillEvent (needed — N0f):**
```
{ fill_id, order_id, price, quantity, commission, timestamp,
  spread_at_fill,     // bid-ask spread % at moment of fill
  slippage,           // limit_price - fill_price
  side,               // Buy/Sell
  symbol              // for cost attribution
}
```

**PositionClosed (current):**
```
{ symbol, entry_price, exit_price, pnl, hold_time, exit_reason, rung_at_exit }
```

**PositionClosed (needed — N0e):**
```
{ symbol, entry_price, exit_price,
  gross_pnl,           // before all costs
  net_pnl,             // after spread + commission
  total_commission,     // entry + exit commission
  spread_at_entry,     // bid-ask % when entered
  spread_at_exit,      // bid-ask % when exited
  slippage_entry,      // limit - fill
  slippage_exit,       // limit - fill
  cost_category,       // "W1-W5" or "L1-L7" (taxonomy code)
  hold_time, exit_reason, rung_at_exit, conviction
}
```

### Proposed New WAL Events (X10-X13)

| Event | Purpose | Priority |
|-------|---------|----------|
| SignalGenerated | Track signal before gate filtering | X10 |
| SignalRejected | Track WHY signals were killed (cost? confidence? regime?) | X11 |
| AnomalyDetected | Unusual spread/volume/correlation events | X12 |
| DailyTradeCount | Track trades/day for frequency management | NEW — implicit in DailyReset |

---

## Generated Reports

| Report | Location | Generator | Cadence | Cost-Aware? |
|--------|----------|-----------|---------|-------------|
| Daily report .txt | /app/data/ouroboros_reports/ | nightly_v6 | Nightly | ❌ Gross PnL only |
| Battle plan .txt | /app/data/ouroboros_reports/ | nightly_v6 | Nightly | ❌ No cost budget |
| Metrics .json | /app/data/ouroboros_reports/ | nightly_v6 | Nightly | ❌ No cost-adjusted metrics |
| Watchlist .json | /app/data/ouroboros_reports/ | ticker_selector | Nightly | ❌ No spread ranking |
| Session PDF | /app/data/session_reports/ | session_pdf | Session opens | ❌ No cost forecast |
| Sim report | /app/data/ouroboros_reports/ | daily_sim_report | Nightly | ❌ Cost-blind simulation |

### Report Cost Gaps

**Every report is cost-blind.** After N0 survival stack:
- Daily report must show: gross PnL, net PnL, daily cost, trades/day, spread victims
- Battle plan must include: cost budget, max allowed trades, min edge threshold
- Metrics must include: cost-adjusted WR, cost-adjusted PF, daily cost as % equity
- Session PDF must include: "Today's cost budget: X trades × £10 = £X"

---

## Redis State (aegis-redis)

| Key Pattern | Purpose | Persistence | Cost-Aware? |
|-------------|---------|-------------|-------------|
| position:* | Open position state backup | AOF | ⚠ Has commission, no spread |
| signal:queue | Sheets sync queue | AOF | ❌ |
| heartbeat | Engine alive signal | Volatile | ❌ |
| regime:current | Risk regime state | AOF | ❌ |
| **daily:trade_count** | **Daily trade counter** | **DOES NOT EXIST** | **❌ NEEDED** |
| **daily:cost_total** | **Running cost accumulator** | **DOES NOT EXIST** | **❌ NEEDED** |

---

## External Integrations

| Service | Protocol | Auth | Purpose | Cost Relevance |
|---------|----------|------|---------|----------------|
| IB Gateway | TCP :4003 | Client ID 101 | Market data + orders | Source of real bid-ask spreads |
| Google Sheets | REST API | Service account JSON | Position tracking | Cost_Dashboard tab needed (X14) |
| Telegram | REST API | Bot token | Alerts + heartbeats | Cost alerts needed |
| yfinance | HTTP | None | Market data (ticker_selector) | Offline only, NOT live fallback |
| TwelveData | REST API | API key | Supplemental data | Could provide historical spreads |
| FMP | REST API | API key | Fundamentals | No cost relevance |

---

## State Reconstruction on Restart

1. **WAL Replay** → positions, cash, equity, high_water_mark, regime
2. **config_writer pre-boot** → fresh dynamic_weights.toml (spread from commission proxy)
3. **Broker reconciliation** → verify positions match IB Gateway
4. **Orphan resolution** → cancel stale orders
5. **Bar history** → NOT persisted (rebuilt from incoming ticks, 2% ATR fallback)
6. **Daily trade count** → **NOT RECONSTRUCTED** ⚠ (counter resets, no WAL persistence)
7. **Cost accumulators** → **NOT RECONSTRUCTED** ⚠ (no daily cost state in WAL)

### Restart Cost Risk
If engine restarts mid-day, daily_trade_count (when implemented) must be reconstructable from WAL. Current WAL lacks this data. The DailyReset event should carry previous day's trade_count and cost_total for audit trail.

---

## Data Flow Diagram (Cost-Annotated)

```
┌─────────────────────────────────────────────────────────────────────┐
│                        REAL-TIME PATH                                │
│                                                                      │
│  IB Gateway ─tick─→ engine.rs ─spread calc─→ risk_arbiter.rs        │
│      │                  │                        │                   │
│      │                  │               spread_veto (0.3% live)      │
│      │                  │               ❌ NO daily_count check       │
│      │                  │               ❌ NO min_edge check          │
│      │                  │                        │                   │
│      │                  ├─→ bridge.py ─signal─→ entry_engine.rs      │
│      │                  │                        │                   │
│      │                  │                Kelly sizing (GROSS edge)    │
│      │                  │                ❌ Factor 8 decorative        │
│      │                  │                        │                   │
│      │                  ├─→ smart_router.rs ─CostBreakdown──┐       │
│      │                  │   (computed but ❌ NOT gating)      │       │
│      │                  │                                    │       │
│      │                  └─→ ibkr_broker.rs ─limit order──→ IB       │
│      │                           │                                   │
│      │                           ▼                                   │
│      │                    wal_writer.rs                               │
│      │                    ❌ FillEvent lacks spread                    │
│      │                    ❌ PositionClosed lacks gross_pnl            │
│                                                                      │
├─────────────────────────────────────────────────────────────────────┤
│                        NIGHTLY PATH                                  │
│                                                                      │
│  WAL events (❌ cost-incomplete)                                      │
│      │                                                               │
│      ▼                                                               │
│  nightly_v6.py ─analyze_trades()─→ RAW PnL ⚠                       │
│      │           ❌ No cost-adjusted WR/PF                            │
│      │           ❌ No L5 Spread Victim detection                     │
│      │           ❌ No trade frequency analysis                       │
│      │                                                               │
│      ├─→ config_writer.py                                            │
│      │       spread from commission proxy ⚠                         │
│      │       guardrails ±15%, Kelly [0.15, 0.30]                    │
│      │       ❌ No max_daily_trades recommendation                    │
│      │                                                               │
│      └─→ dynamic_weights.toml ─SIGHUP─→ engine hot-reload          │
│                                                                      │
│  ⚠ THE LEARNING LOOP IS COST-BLIND                                  │
│  CostBreakdown (smart_router.rs) is NEVER fed to nightly_v6.py     │
│  Ouroboros CANNOT learn from cost-induced losses                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Telemetry Completeness Scorecard

| Category | Items Tracked | Items Missing | Completeness |
|----------|--------------|---------------|-------------|
| Order execution | order_id, price, qty, commission | spread_at_fill, slippage | 60% |
| Position lifecycle | entry, exit, pnl, hold_time | gross_pnl, cost_category, spread_entry/exit | 50% |
| Risk state | regime, drawdown, heat | daily_trade_count, daily_cost | 70% |
| Learning inputs | WR, PF, Sharpe (gross) | cost-adjusted WR/PF/Sharpe, spread victims | 40% |
| Frequency management | NOTHING | daily_count, cost_budget, velocity | **0%** |
| **Overall** | | | **~45%** |

**Bottom line:** The system has excellent structural telemetry (WAL, regime, risk) but is almost completely blind to cost economics. This is the #1 telemetry priority.

---

**Document Version:** 2.0 — SPREAD-FIRST REWRITE
**Re-audit:** 2026-03-20
**Status:** Updated with cost telemetry gaps, data flow diagram, completeness scorecard
**Companion docs:** IMPLEMENTATION_MASTER_PLAN.md (v3.0), IMPLEMENTATION_MASTER_PLAN_RC1.md (RC5), REPO_MAP.md (v2.0)
