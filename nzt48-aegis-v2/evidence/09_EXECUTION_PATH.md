# AEGIS V2 — Execution Path: Signal to Order to Fill

**Audit Date:** 2026-04-04

---

## Live Execution Pipeline

```
Step 1: IBKR IB Gateway (localhost:4003 paper / 4001 live)
          │
          │  [5-second realtime bars + L1 tick-by-tick BidAsk]
          ▼
Step 2: IbkrBroker.poll_ticks() → drain_ticks() → Vec<MarketTick>
          │
          ▼
Step 3: Engine.route_tick() → Universe filter
          │
          ├── Filtered (Amihud/ASER/erroneous/split/halt/NaN) → discard
          ├── Apex(tick) → accumulate 60s OHLCV → evaluate_apex_snapshot
          └── Vanguard(tick) → Step 4
          │
          ▼
Step 4: Build TickContext (25 fields)
          │  win_rate, leverage, vol, correlation, drawdown_pct,
          │  amihud, regime, spread_pct, heat_pct, equity, vix,
          │  london_time_secs, gap_pct, open_positions, trades_today...
          ▼
Step 5: PythonBridge.evaluate_tick() → JSON over stdin
          │
          ▼
Step 6: Python bridge.py 5-stage pipeline:
          │  Ingest → Indicators → Quality Gates (25+)
          │  → Signal Generation (33+) → Adjustments (50+)
          │  → Bayesian aggregation → Return JSON signal
          ▼
Step 7: Parse BrainSignal
          │  direction, confidence, kelly_fraction, shares, strategy,
          │  entry_type, 20+ indicator/hint fields
          ▼
Step 8: Build EvalContext (35+ fields for risk arbiter)
          │
          ▼
Step 9: RiskArbiter.evaluate() → 39 synchronous checks in <1ms
          │
          ├── REJECTED: log veto reason, telemetry counter → done
          └── APPROVED: adjusted_size, regime, kelly
          │
          ▼
Step 10: Position sizing
          │  size = Kelly * equity * regime_scale
          │  shares = size / current_price
          │  Enforce min_lot_for_exchange() (TSE/HKEX/SGX = 100-lot)
          ▼
Step 11: WAL write: RoutedOrder event (pre-submission persistence)
          │
          ▼
Step 12: [LIVE] IbkrBroker.submit_order()
         [PAPER] SimulatedTrade, fill at current price, update portfolio
          │
          ▼
Step 13: Executioner.track_order(TrackedOrder) → lifecycle: Submitted
          │
          ▼
Step 14: Broker.poll_events() → BrokerEvent::Ack or BrokerEvent::Fill
          │
          ▼
Step 15: Engine.process_broker_event()
          │  - Ack: update lifecycle to Acknowledged
          │  - Fill: record fill, update PositionState, portfolio,
          │    set initial stop (entry - N*ATR), WAL write FillEvent
          ▼
Step 16: EXIT LOOP (runs every tick for each open position)
          │  ExitEngine.update_tracking(): highest_high, rung, ratchet stop
          │  ExitEngine.evaluate(): check exit conditions (priority order):
          │    HALT > HardStop > Chandelier > TimeStop > MaxHold
          │    > UrgencyRamp > EOD > Signal
          │  Partial laddering: Rung 3 (25%) + Rung 4 (25%)
          ▼
Step 17: On exit fill: WAL write PositionClosed
          │  Update P&L, send exit notification to Python
          │  Python Compounding Machine: Track/Score/Size/Kill
```

## Main Loop Cadence

The engine main loop runs at **100ms intervals**:

1. Poll broker ticks
2. Route through universe filter
3. Evaluate via Python bridge
4. Process signals through risk arbiter
5. Manage exits for open positions
6. Handle broker reconnection
7. Periodic reconciliation (every 5 minutes)
8. Telemetry dump
9. State hash (every 1 hour)
10. Kill/pause file check (every 1 second)

## Startup Sequence

1. Print banner (IS_LIVE status)
2. Validate config.live.toml exists
3. Parse CLI args (--config-dir, --wal-dir, --ibkr-host, --ibkr-port)
4. Load config (paper or live overlay)
5. Log config hash (SHA-256)
6. N8b Live assertions (max_positions<=5, heat<=20%, cash_buffer>=15%)
7. Load Ouroboros artifacts (dynamic_weights, universe, FX rates)
8. Create WAL writer + disk check + rotate stale WAL
9. Create IbkrBroker (register 1,250 contracts)
10. Connect IB Gateway (exponential backoff: 10 paper / infinite live)
11. Wait 15s for IBKR secdef farms
12. Subscribe market data (bars + L1 tick-by-tick)
13. Start Python Brain bridge subprocess
14. Create Engine (broker, config, wal, clock)
15. Apply Ouroboros weights
16. Register tickers in universe (Vanguard/Apex routing)
17. Load WAL events for replay
18. Run 8-step startup sequence (WAL replay, position reconciliation, clock sync)
19. Install signal handlers (SIGINT=shutdown, SIGHUP=hot-reload)
20. Enter main event loop

## Exit Engine: Chandelier 5-Rung Trailing Stop

```
Rung 0: Entry          ATR mult = 1.50   (initial stop)
Rung 1: +0.8% gain     ATR mult = 1.35   (tighten)
Rung 2: +1.5% gain     ATR mult = 1.125  (tighten)
Rung 3: +2.5% gain     ATR mult = 1.00   (partial: sell 25%)
Rung 4: +4.0% gain     ATR mult = 0.75   (partial: sell 25%)
```

Stop ratchets UP only. Never loosens. Max hold: configurable per-strategy (default 60 bars).

## InfiniteChandelier (Advanced Exit)

8 adaptive multipliers based on regime and volatility. Used for extended holds in trending regimes. Not exercised in backtest.
