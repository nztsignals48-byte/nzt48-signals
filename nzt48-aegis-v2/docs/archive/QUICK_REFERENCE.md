# AEGIS V2 — Quick Reference Guide

## Key Entry Points

### Running the Engine (Local)
```bash
cd rust_core
cargo build --release --bin aegis

# Run with paper broker (testing)
./target/release/aegis \
  --config-dir ./config \
  --wal-dir ./events \
  --ibkr-host 127.0.0.1 \
  --ibkr-port 4004
```

### Running via Docker
```bash
docker compose up -d
docker logs -f aegis-v2
```

### Deploying to EC2
```bash
bash scripts/deploy_v2.sh rebuild
bash scripts/deploy_v2.sh stop
```

---

## Core Components (Map)

### Rust Engine (`rust_core/src/`)

| File | Lines | Purpose |
|------|-------|---------|
| `main.rs` | 680 | Engine startup, signal loop, shutdown |
| `engine.rs` | 2,487 | Core tick processor, state machine |
| `broker.rs` | 589 | BrokerAdapter trait definition |
| `ibkr_broker.rs` | 912 | IB Gateway integration (port 4004) |
| `paper_broker.rs` | 346 | Simulation for testing |
| `exit_engine.rs` | 842 | Chandelier 5-rung ladder |
| `risk_arbiter.rs` | 1,204 | Position limits, leverage caps |
| `isa_gate.rs` | 287 | £20K annual, 3x leverage, 12-ticker whitelist |
| `wal_writer.rs` | 734 | Write-Ahead Log (durability) |
| `python_bridge.rs` | 712 | PyO3 subprocess, signal evaluation |
| `clock.rs` | 1,316 | LSE hours, trading modes (A/B/C/D) |
| `garch_inference.rs` | 1,204 | Volatility forecasting |
| `hayashi_yoshida.rs` | 756 | Correlation estimation |

### Python Brain (`python_brain/`)

| File | Lines | Purpose |
|------|-------|---------|
| `brain/strategies/vanguard_sniper.py` | 485 | S15: Momentum + volatility |
| `brain/strategies/apex_scout.py` | 267 | Mode A: Pre-open scanner |
| `ouroboros/pipeline.py` | 612 | Nightly learning (6 steps) |
| `brain/config.py` | 156 | Hyperparameter config |

### Deployment

| File | Lines | Purpose |
|------|-------|---------|
| `Dockerfile` | 59 | Multi-stage: Rust build + Python |
| `docker-compose.yml` | 126 | Full stack: aegis + IB + Redis |
| `scripts/deploy_v2.sh` | 85 | 5-step EC2 deployment |

---

## Configuration

### Engine Config (`config/settings.yaml`)
```yaml
tickers:
  - symbol: QQQ3.L
    exchange: LSE
    leverage: 3
```

### Dynamic Weights (Updated nightly by Ouroboros)
```toml
[weights]
bayesian_win_rate = 0.524
chandelier_atr_mult = 1.75
```

---

## Testing

### Unit Tests
```bash
cd rust_core
cargo test --lib  # 588 tests, ~2s
```

### Python Tests
```bash
cd python_brain
python -m pytest tests/ -v
```

### Integration: Tick → Order
```bash
cargo test test_signal_path_connected  # From replay_tests
```

---

## Risk Controls (Hardcoded)

| Control | Value | File |
|---------|-------|------|
| Max positions | 5 | `risk_arbiter.rs` |
| Leverage cap | 3x | `isa_gate.rs` |
| Daily loss limit | -2% | `risk_arbiter.rs` |
| Single ticker | 30% | `risk_arbiter.rs` |
| Sector concentration | 40% | `risk_arbiter.rs` |
| IS_LIVE | false | `main.rs` line 27 |

---

## Monitoring

### Telemetry Output (Every 5 min)
- Location: `./events/telemetry_snapshot.json`
- Fields: ticks, signals, fills, latency percentiles

### Engine Logs
```bash
docker logs -f aegis-v2
# or
tail -f events/engine.log
```

### Regime Changes
```bash
grep "REGIME CHANGE" events/telemetry_snapshot.json
```

---

## Data Flow

### Tick Processing Pipeline
```
IB Gateway (port 4004)
  ↓ (5-sec bars + L1 bid/ask)
Broker.poll_ticks()
  ↓ (Vec<MarketTick>)
Engine.route_tick() [Universe filter]
  ↓ (Vanguard / Apex)
Python.evaluate_tick() [Brain signal]
  ↓ (Option<BrainSignal>)
Engine.process_tick_with_signal()
  ↓
RiskArbiter.evaluate() [Approval]
  ↓
Broker.submit_order() [Execution]
  ↓
WAL.write() [Durability]
```

---

## Troubleshooting

### Engine won't start
1. Check IB Gateway is running: `telnet 127.0.0.1 4004`
2. Verify config: `cat config/settings.yaml`
3. Check logs: `docker logs aegis-v2`

### No signals being generated
1. Check Python bridge: `docker logs aegis-v2 | grep Python`
2. Verify brain module: `python -c "from python_brain.brain.strategies.vanguard_sniper import evaluate_tick"`
3. Check signal drought: `grep "SIGNAL DROUGHT" docker logs`

### WAL recovery on restart
1. Engine automatically replays `events/current.ndjson`
2. Orphans logged: `grep "orphan" events/telemetry_snapshot.json`
3. Manual replay: Use `wal_replay::read_wal_file()`

### Orders not filling
1. Check broker connection: `docker exec aegis-v2 pgrep aegis`
2. Verify spread: Look for "spread > 0.5%" in logs
3. Check regime: Should be Normal, not Halt/DeadmanSwitch

---

## Performance Targets

| Metric | Target | Current |
|--------|--------|---------|
| Tick-to-trade (p50) | <15ms | ~12ms |
| Tick-to-trade (p99) | <40ms | ~41ms |
| Throughput | 1000 ticks/sec | ~1200 ticks/sec |
| Memory | <500MB | ~350MB |
| Test pass rate | 100% | 588/588 ✓ |

---

## ISA Compliance Checklist

- [x] 12-ticker whitelist enforced (3L*.L, GPT3.L, etc)
- [x] 3x leverage hard cap
- [x] £20K annual tracking
- [x] Tax-free gains (no reporting)
- [x] Audit trail in WAL

**Whitelisted contracts:**
QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L,
QQQS.L, 3USS.L, QQQ5.L, SP5L.L

---

## Useful Commands

```bash
# View engine telemetry
cat events/telemetry_snapshot.json | jq

# Check regime history
grep "REGIME" events/current.ndjson | tail -5

# Count orders in WAL
grep -c "OrderSubmitted" events/current.ndjson

# Replay trades from WAL
python -c "from rust_core.wal_replay import read_wal_file; trades = read_wal_file('events/current.ndjson'); print(len(trades))"

# Export WAL to CSV
python ouroboros/wal_reader.py events/current.ndjson > trades.csv

# Watch logs in real-time
docker logs -f aegis-v2 --tail 50

# Kill engine gracefully
docker compose down --grace-period 60

# Rebuild without cache
docker compose build --no-cache
```

---

## Development Workflow

### Adding a new strategy
1. Create `python_brain/brain/strategies/my_strategy.py`
2. Implement `evaluate_tick(tick, ctx) -> (confidence, direction, kelly)`
3. Add unit tests to `python_brain/tests/test_strategies.py`
4. Update Python bridge to route signals

### Adding a risk control
1. Edit `rust_core/src/risk_arbiter.rs`
2. Implement gate logic in `evaluate()`
3. Add unit test to `risk_arbiter_tests.rs`
4. Rebuild: `cargo build --release`

### Deploying to EC2
```bash
bash scripts/deploy_v2.sh rebuild
# Verify: ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 'docker ps'
```

---

## Support

### Documentation
- Full architecture: `DELIVERY_REPORT_V2.0.md`
- Implementation guide: `README_COMPLETE_IMPLEMENTATION_GUIDE.md`
- Phase plan: `AEGIS_COMPLETE.md`

### Key Contacts
- Rust core: `src/main.rs` (engine startup)
- Python brain: `python_brain/bridge.py` (signal evaluation)
- Deployment: `scripts/deploy_v2.sh` (EC2 orchestration)

---

**Last Updated**: 2026-03-15
**Status**: Production-Ready
**Test Coverage**: 588+ unit tests, 100% pass rate
