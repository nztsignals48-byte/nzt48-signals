# 00 — CANONICAL RULES
# AEGIS V2 — Institutional Trading Engine
# Version: 1.0 | Status: SPEC LOCK
# These rules are IMMUTABLE during the Crucible phase.
# Any code that violates a rule listed here fails review automatically.

---

## 1. SIGNAL FILTERING

| Rule | Threshold | Action on Breach |
|------|-----------|------------------|
| Confidence Floor | 65 | Discard OrderIntent silently (log DEBUG) |
| Outlier Win Cap | 3% single-trade return | Cap at 3% when computing Kelly average payout (H62) |
| Gap Detection | >2% open gap against trend | 15-minute no-trade cool-down for price discovery (H66) |
| Erroneous Tick | >5% deviation from 1s MA | Filter tick, do not trigger stop-loss (H77) |
| Price Spike Filter | 1-tick anomaly (10% drop + instant bounce) | Verify Bid/Ask midpoint before triggering stop (H71) |

---

## 2. POSITION LIMITS

| Rule | Threshold | Action on Breach |
|------|-----------|------------------|
| Max Simultaneous Positions | 3 (filled + pending combined, H34) | Reject new OrderIntent |
| Portfolio Heat Limit | Total risk across all positions < 6% | Reject new OrderIntent |
| Sector Heat Cap | No single sector > 33% of total equity (H30) | Reject new OrderIntent |
| Cash Buffer | Available_Cash < Total_Equity × 10% (H31) | Reject new OrderIntent |
| ISA Annual Limit | £20,000 per tax year | Reject if cumulative investment would exceed |

---

## 3. ISA SAFETY INVARIANTS (P0 — VIOLATION = SYSTEM HALT)

| Rule | Threshold | Action on Breach |
|------|-----------|------------------|
| No Short Selling | position_qty <= 0 AND side == SELL | REJECT + log CRITICAL |
| No Margin | Never request margin from IBKR | Enforced by account config |
| Inverse Mutual Exclusion | QQQ3.L open → QQQS.L blocked; 3LUS.L open → 3USS.L blocked (H32) | REJECT with VetoReason::InverseMutualExclusion |

---

## 4. RISK ARBITER STATE TRANSITIONS

| State | Trigger | Action | Recovery |
|-------|---------|--------|----------|
| **HALT** | Data stale >120s (IBKR timestamp); Broker disconnected; WAL unavailable; Queue depth = 50,000; ISA safety violation; 3 IBKR rejections in 1 min (H88); 3 consecutive stop-losses in 1 day (H38) | Cancel ALL pending orders, market-sell ALL positions | Manual human approval required |
| **FLATTEN** | Daily loss >2% from intraday high-water mark (H29); Orphaned order detected; Position reconciliation mismatch >0 | No new entries; exit existing at best available | Automatic after all positions closed + reconciliation clean |
| **REDUCE** | Tick drops >100/sec; Queue depth >80% (40,000); Python batch latency >2,000ms; VIX >30 | Allow new entries at 50% of normal Kelly sizing | Automatic after trigger conditions clear for 5 min |
| **NORMAL** | All systems nominal | Full Kelly sizing, all strategies active | Default state |

**Precedence: HALT > FLATTEN > REDUCE > NORMAL (unconditional)**

---

## 5. ENTRY TIMING RULES

| Rule | Threshold | Action on Breach |
|------|-----------|------------------|
| Time-of-Day Cutoff | No new entries after 15:45 LSE (H35) | REJECT with VetoReason::TooLateInSession |
| Auction Avoidance | No orders during 07:50-08:00 or 16:30-16:35 | REJECT with VetoReason::AuctionPeriod |
| Spread Veto | Real-time spread > 0.5% at order time (H36) | REJECT with VetoReason::SpreadTooWide |
| Velocity Check | 5+ identical intents in 1 second (H37) | Accept first, drop remaining 4, log WARNING |

---

## 6. POSITION SIZING (KELLY)

| Rule | Threshold | Notes |
|------|-----------|-------|
| Kelly Fraction Cap | 0.5 (half-Kelly) | Never bet more than half the mathematical Kelly |
| Kelly Clamp | Max output = 0.20 (20% of capital, H57) | Separate from half-Kelly cap — whichever is smaller wins |
| Volatility Drag | 3x ETP: variance × 9; 5x ETP: variance × 25 (H59) | Applied in Kelly calculation |
| Bayesian Shrinkage | Beta prior: W_adj = (W×N + 0.5×Prior)/(N + Prior) (H58) | Laplace smoothing for small sample sizes |
| Drawdown Scaling | Reduce size proportionally as daily loss increases | Factor 5 in 12-factor Kelly |
| Slippage Assumption | 1% worst-case on capital sufficiency check (H33) | Applied before checking cash sufficiency |

---

## 7. ORDER EXECUTION RULES

| Rule | Threshold | Notes |
|------|-----------|-------|
| Marketable Limit Orders | Ask + 0.1%, never raw Market orders (H49) | Protects against flash crashes |
| Market-to-Limit for Exits | MTL for emergency exits instead of pure Market (H117) | Prevents fills at £0.01 |
| Fractional Shares | math.floor(Capital / Price) only (H64) | LSE ETPs cannot be fractional on IBKR UK |
| Tick Size Rounding | £0.001 under £1, £0.01 over £1 (H65) | IBKR rejects invalid decimals |
| TIF: Entry | DAY (H69) | Never GTC (H123) |
| TIF: Emergency Exit | IOC — Immediate or Cancel (H69) | For HALT sells only |
| Stop Trigger Method | Last Price (method 1, H50) | Prevents triggering on wide Bid/Ask spreads |
| OUTSIDE_RTH | false (H51) | Never execute during pre-market |

---

## 8. DATA INTEGRITY RULES

| Rule | Threshold | Notes |
|------|-----------|-------|
| Stale-Data Threshold | 120 seconds (IBKR last-tick timestamp) | HALT if breached |
| Tick Dropping | Oldest-first from crossbeam channel | Log every drop; >100/sec → REDUCE |
| Queue Depth Monitor | 40,000 (80%) → REDUCE; 50,000 → tick dropping (oldest-first) | Channel capacity is 50,000. Burst at open is expected. |
| Backpressure | Python batch >500ms → WARNING; >2,000ms → REDUCE | GIL Thread monitors |
| Clock Source | IBKR reqCurrentTime(), NOT system clock | Offset >2s → use IBKR time only |
| Synthetic Halt Detection | No ticks for 30s on specific ETP while market active (H122) | Per-ticker Limp Mode: block entries, allow exits, re-subscribe. 120s → full ticker HALT |
| Alpha Decay Ticker Lock | IC decay threshold breached for 3 consecutive days | Ticker LOCKED in universe_classification.toml. Manual review required to unlock |
| Reverse Split Detection | Price moves >500% overnight (H76) | HALT ticker pending manual review |

---

## 9. WAL INTEGRITY RULES

| Rule | Threshold | Notes |
|------|-----------|-------|
| WAL is God | Redis disagrees with WAL → WAL wins | Redis is cache only |
| Schema Version | Every ndjson line includes "schema_version": 1 (H21) | Future-proof replay |
| Event IDs | UUIDv7, time-ordered, sortable (H22) | Enables cross-system correlation |
| Dual Timestamps | event_time + write_time per event (H23) | Monitor IO lag |
| Checksum | CRC32 per ndjson line (H24) | Detect partial disk writes |
| Disk Space | <5% remaining → FLATTEN and HALT (H25) | Never trade without logging |
| Corruption Policy | Non-last-line corruption → panic!, refuse to trade (H27) | Last-line corruption → skip with WARNING |
| Immutable Borrows | WAL writer takes &Event, never &mut (H26) | Cannot alter logged state |

---

## 10. RECONCILIATION RULES

| Rule | Threshold | Notes |
|------|-----------|-------|
| Reconciliation Interval | Every 5 minutes during trading | reqPositions() + reqOpenOrders() |
| Mismatch Action | Any mismatch → log CRITICAL, trust broker, update local | Then trigger FLATTEN |
| Orphan Resolution | Block ALL new orders until orphans resolved | Startup-critical |
| Commission Tracking | Add commission to cost basis on receipt (H53) | commissionReport events |
| FIFO Accounting | First-In-First-Out for PnL calculation (H87) | Matches HMRC UK ISA regulations |

---

## 11. REJECT-TO-HALT ESCALATION

| Rule | Threshold | Notes |
|------|-----------|-------|
| Reject-to-HALT | 3 IBKR rejections in 1 minute (H88) | Assume systemic logic error |
| Consecutive Loss Breaker | 3 stop-losses in 1 day (H38) | HALT for remainder of day |
| Pacing Violation (Code 321) | IBKR pacing penalty (H46) | Back off 5 seconds |
| Error 1100 | IBKR disconnect (H43) | Immediate HALT |
| Error 1102 | IBKR reconnect (H44) | Run orphan reconciliation before NORMAL |

---

## 12. CONSTANTS REGISTRY

All magic numbers extracted. Code MUST reference these constants,
not literal values (H109).

```toml
[signal]
confidence_floor = 65
outlier_win_cap_pct = 3.0
gap_detection_pct = 2.0
erroneous_tick_deviation_pct = 5.0
velocity_check_window_secs = 1
velocity_check_max_intents = 5

[position]
max_simultaneous_positions = 3    # Crucible override: 1
# Portfolio heat = Σ((entry_price - stop_price) × qty / total_equity)
portfolio_heat_limit_pct = 6.0
sector_heat_cap_pct = 33.0
cash_buffer_pct = 10.0
isa_annual_limit_gbp = 20000      # Tracked in WAL, enforced by RiskArbiter
isa_tax_year_start = "04-06"      # 6 April UK tax year start

[kelly]
fraction_cap = 0.5
clamp_max = 0.20
volatility_drag_3x = 9
volatility_drag_5x = 25

[timing]
# ALL LSE times are in LONDON LOCAL TIME (Europe/London timezone).
# During GMT (Nov-Mar): these are UTC. During BST (Mar-Oct): UTC = local - 1h.
# Use chrono-tz crate, NEVER hardcode UTC offsets.
stale_data_threshold_secs = 120
entry_cutoff_london = "15:45"     # London local time
lse_open_london = "08:00"         # London local time
lse_close_london = "16:30"        # London local time
auction_open_start = "07:50"      # London local time
auction_open_end = "08:00"        # London local time
auction_close_start = "16:30"     # London local time
auction_close_end = "16:35"       # London local time
eod_flatten_time = "16:25"        # London local time
eod_flatten_phase1 = "15:55"      # T-35: passive limit at mid+1tick
eod_flatten_phase2 = "16:15"      # T-15: limit at mid
eod_flatten_phase3 = "16:25"      # T-5: MTL emergency
gap_cooldown_mins = 15
synthetic_halt_limp_secs = 30     # Per-ticker Limp Mode threshold
synthetic_halt_full_secs = 120    # Per-ticker full HALT threshold

[volatility]
# Yang-Zhang (2000) estimator — mandatory for Moreira-Muir scaling
# The ONLY estimator that handles overnight gaps in leveraged ETPs
yz_rolling_window = 10            # 10-bar rolling for real-time
yz_nightly_window = 20            # 20-bar for nightly recalibration
vol_target_annual_pct = 15.0      # Portfolio vol target
vol_high_threshold = 1.5          # > 1.5× historical → reduce 30%
vol_extreme_threshold = 2.0       # > 2.0× historical → reduce 60%

[risk]
daily_drawdown_pct = 2.0
spread_veto_pct = 0.5
slippage_assumption_pct = 1.0
consecutive_loss_halt = 3
reject_to_halt_count = 3
reject_to_halt_window_secs = 60

[execution]
marketable_limit_buffer_pct = 0.1
tick_size_under_1 = 0.001
tick_size_over_1 = 0.01

[channel]
capacity = 50000
reduce_threshold = 40000
halt_threshold = 50000
tick_drop_alert_per_sec = 100

[backpressure]
warning_ms = 500
reduce_ms = 2000

[reconciliation]
interval_secs = 300
orphan_ack_timeout_secs = 5

[ibkr]
client_id_executioner = 100
client_id_ouroboros = 200
reconnect_backoff_secs = [1, 2, 4, 8, 16, 32, 60]
rate_limit_msgs_per_sec = 50
reqmktdata_pacing_ms = 10
historical_data_max_per_10min = 60
max_simultaneous_lines = 100        # Free IBKR default, no Quote Boosters

[rotation]
# Dynamic subscription rotation — 100 free lines across 1,000 tickers
tier1_permanent_lines = 50           # Top 50 Vanguard, always subscribed
tier2_rotating_lines = 50            # Shared between Vanguard warm + Apex
tier2_rotation_secs = 60             # Rotate every 60 seconds
tier2_vanguard_batches = 5           # 250 Vanguard warm in 5 batches
tier3_apex_batches = 14              # 700 Apex in 14 batches
tier1_promotion_confidence = 80      # Promote warm→hot if signal > 80
full_vanguard_scan_mins = 5          # Complete Vanguard warm scan
full_apex_scan_mins = 15             # Complete Apex scan
open_position_always_tier1 = true    # Tickers with positions = permanent

[wal]
schema_version = 1
```

---

## RULE COUNT VERIFICATION

Total canonical rules: 55
Total constants: 48
Any code path that bypasses a canonical rule is a P0 build failure.
