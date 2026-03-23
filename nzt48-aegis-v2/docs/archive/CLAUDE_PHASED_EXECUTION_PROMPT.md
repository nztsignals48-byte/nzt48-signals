# AEGIS V2 — FINAL DEPLOYMENT ORDER

You are the engineer deploying a £10,000 automated leveraged ETP trading engine to production paper trading. The Rust codebase is built — 41 modules, ~17,000 LOC, 405+ tests, zero warnings. It's live on EC2 right now, watching prices, doing nothing. The exit path hasn't been validated, the learning layer isn't connected, and the WAL can OOM. You will fix all three and deploy. In one session. No stopping.

---

## THE RALPH WIGGUM LOOP (Build Gate)

You run this after EVERY code change. No exceptions. No skipping. Not once.

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/rust_core
cargo check 2>&1
cargo clippy -- -D warnings 2>&1
cargo test --no-default-features --lib 2>&1
```

All three must return **zero errors, zero warnings, all tests green**.

- `lib.rs` line 5: `#![deny(warnings)]` — the compiler REFUSES to build if you leave unused imports, dead code, or unused variables. This means every variable you declare must be used immediately. Every import must be consumed. No "add now wire later."
- `lib.rs` line 4: `#![deny(clippy::unwrap_used)]` — no `.unwrap()` anywhere. Use `unwrap_or`, `unwrap_or_else`, `?`, or `if let`.
- **macOS PyO3 gotcha**: `cargo test` (full) fails due to PyO3 linking. `--no-default-features --lib` is the correct flag. Full integration tests work in Docker on EC2.
- Cargo.toml: crate = `rust_core`, edition = `2024`, PyO3 0.24, crossbeam-channel 0.5, uuid v1 (v7 feature).

After passing, print: `"RALPH WIGGUM PASS — cargo check ✅ clippy ✅ test ✅ (N tests)"`

If Ralph Wiggum fails, you fix it. You do not move on. You do not touch other files. You fix the failure, re-run, and only proceed when all three are green. If you fail 20 consecutive times on the same issue, STOP and report what's broken and why.

---

## THE CODEBASE (Exact locations — no searching required)

**Root**: `/Users/rr/nzt48-signals/nzt48-aegis-v2/`
**Rust source**: `rust_core/src/`

### Key Structs & Traits (with exact line numbers)

**`engine.rs`** — The engine:
- `struct Engine<B: BrokerAdapter>` — line 230 (23 fields)
- `Engine::new()` — line 256 (constructs ExitEngine at line 267 via `ExitEngine::with_default_chandelier()`)
- `process_tick_with_signal()` — line 431 (main tick loop, 237 lines)
- **Phase 2A exit sell code** — lines 494-556 (ALREADY WRITTEN — your Order 1 validates this)
- `process_broker_event()` — line 714

**`exit_engine.rs`** — Exit system:
- `trait ExitStrategy: Send` — line 16 (2 methods: `compute_stop`, `compute_rung`)
- `struct ChandelierStrategy` — line 26 (fields: `rung_thresholds: [f64;5]`, `rung_stops: [f64;4]`, `rung5_trail_atr: f64`)
- `ChandelierStrategy::default()` — line 35 (rung5_trail_atr = **1.5** — this is what Ouroboros wants to override to 3.0)
- `struct ExitEngine` — line 112 (fields: `config: ExitConfig`, `strategy: Box<dyn ExitStrategy>`)
- `ExitEngine::new(config, strategy: Box<dyn ExitStrategy>)` — line 118
- `ExitEngine::with_default_chandelier()` — line 122 (creates default ChandelierStrategy)
- `struct InfiniteChandelier` — line 434 (dead code, DO NOT wire now)
- `struct Executioner` — line 526 (dead code, DO NOT wire now)

**`risk_arbiter.rs`** — 31-check risk gate:
- `struct RiskArbiter` — line 59 (fields: `regime: RiskRegime`, `config: RiskConfig`, `velocity_log`)
- `RiskArbiter::new(config)` — line 66
- CHECK 27: Kelly floor (line 264) — rejects if `kelly_fraction_raw > 0 && < 0.005`
- `adjusted_size` calculation — lines 270-278:
  ```rust
  let kelly_ramp = (self.config.kelly_ramp_trades as f64 / 250.0).clamp(0.1, 1.0);
  let ramped_kelly = intent_kelly * kelly_ramp;
  let size = ramped_kelly * portfolio.equity;
  let adjusted_size = if self.regime == RiskRegime::Reduce { size * 0.5 } else { size };
  ```
  **THIS** is where you wire `regime_scales` — replace the hardcoded 0.5 with `regime_scales.get(&regime_name)`.

**`wal_actor.rs`** — Write-Ahead Log:
- `WalHandle::append()` — line 40 (uses `try_send`, returns bool)
- `WalActor::spawn()` — line 84 (returns `(WalHandle, JoinHandle)`)
- **Line 89**: `let (tx, rx) = crossbeam_channel::unbounded();` ← THIS is what you change to `bounded(50_000)`
- Line 43-44: Already handles `TrySendError::Full` with eprintln — will work correctly once channel is bounded

**`ouroboros_loader.rs`** — Nightly weights:
- `struct DynamicWeights` — line 11 (10 fields):
  ```rust
  pub bayesian_win_rate: f64,       // line 12 — already wired to Python via TickContext
  pub trade_count: u32,             // line 13
  pub sharpe_ratio: f64,            // line 14
  pub dsr: f64,                     // line 15
  pub dsr_significant: bool,        // line 16
  pub chandelier_atr_mult: f64,     // line 17 — default 3.0, WIRE TO ChandelierStrategy.rung5_trail_atr
  pub regime_best: String,          // line 18
  pub regime_worst: String,         // line 19
  pub regime_scales: HashMap<String, f64>,  // line 20 — WIRE TO RiskArbiter adjusted_size
  pub kelly_fractions: HashMap<String, f64>, // line 21 — WIRE TO RiskArbiter per-ticker Kelly caps
  ```
- `DynamicWeights::default()` — line 24 (chandelier_atr_mult defaults to 3.0, HashMaps empty)
- `load_dynamic_weights(config_dir)` — line 102 (loads `dynamic_weights.toml`, falls back to defaults)

**`main.rs`** — Orchestrator:
- Line 80: `let dw = ouroboros_loader::load_dynamic_weights(&config_dir);` — DW loaded here
- Lines 83-89: Logs WR%, chandelier_mult, tier counts — then **dw is dropped. Never applied.**
- Line 207: `let mut engine = Engine::new(broker, config, Some(wal), clock);` — engine constructed here
- Line 267 (inside Engine::new): `exit_engine: ExitEngine::with_default_chandelier()` — hardcoded default, ignoring dw.chandelier_atr_mult
- **YOU MUST** apply DynamicWeights to the engine AFTER construction (line 207) and BEFORE the main loop (line 272)

### Infrastructure

**EC2**: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22` (c7i-flex.large, 4GB RAM, 2 vCPU)
**3 Docker containers** on `aegis-net` bridge network:
1. `aegis-v2` — Rust engine (1024M limit, 60s shutdown grace, shm 2GB)
2. `aegis-ib-gateway` — IB Gateway via gnzsnz/ib-gateway:stable (port **4004** paper, client_id=101)
3. `aegis-redis` — Redis 7 Alpine (password: `nzt48redis`, 256MB, AOF everysec)

**Mode**: Crucible (paper trading, max 1 position, £10,000 virtual equity)
**Universe**: 39+ tickers (12 core ISA + 27 extended LSE ETPs)

---

## YOUR 5 ORDERS (Execute sequentially. Complete all in this session.)

### ORDER 1: VALIDATE THE EXIT PATH

Phase 2A code is **already written** at `engine.rs:494-556`. You are NOT writing new code. You are running Ralph Wiggum to confirm it compiles, passes clippy, and all tests pass.

What the code does (read it, don't rewrite it):
- Exit trigger fires at line 494 → derives sell limit from `ExitResult.signal.order_type`
- `LimitAtStop` → uses `result.signal.limit_price` (line 498-501)
- `MarketSell`/`MarketToLimit` → `bid * 0.999` (10bps aggressive, line 506)
- Rounds to LSE tick size → writes `WalPayload::RoutedOrder` with `side: "Sell"` (line 511-519)
- Calls `self.broker.submit_order(OrderSide::Sell)` (line 522-528)
- If sell FAILS → position NOT removed, reconciler catches orphan next cycle (line 533-534)
- Drains broker fill events (line 543-546)
- Writes `WalPayload::PositionClosed` and removes position (line 548-556)

**Action**: Run Ralph Wiggum. Report pass/fail with test count. If it fails, debug and fix until green.

### ORDER 2: WIRE THE LEARNING LAYER (DynamicWeights → Engine)

Three things must be connected. The data is loaded at `main.rs:80` but never applied.

**2a. `chandelier_atr_mult` → ChandelierStrategy.rung5_trail_atr**

The problem: `ExitEngine` holds `strategy: Box<dyn ExitStrategy>`. You can't access `ChandelierStrategy.rung5_trail_atr` through the trait object.

Two solutions (pick whichever compiles cleaner under `#![deny(warnings)]`):

**Option A — Trait method:**
Add to `ExitStrategy` trait (exit_engine.rs:16):
```rust
fn set_trail_atr(&mut self, _mult: f64) {} // default no-op
```
Implement on `ChandelierStrategy`:
```rust
fn set_trail_atr(&mut self, mult: f64) { self.rung5_trail_atr = mult; }
```
Then in main.rs after engine construction:
```rust
engine.exit_engine.strategy_mut().set_trail_atr(dw.chandelier_atr_mult);
```
(You'll need to add `pub fn strategy_mut(&mut self) -> &mut dyn ExitStrategy` to ExitEngine.)

**Option B — Constructor with parameter:**
Add `ChandelierStrategy::with_trail_atr(mult: f64)` constructor.
Change `Engine::new()` to accept an optional `trail_atr: Option<f64>` parameter.
In main.rs, pass `Some(dw.chandelier_atr_mult)` when constructing the engine.

**2b. `regime_scales` → RiskArbiter adjusted_size**

At `risk_arbiter.rs:274-277`, the regime scaling is hardcoded:
```rust
let adjusted_size = if self.regime == RiskRegime::Reduce { size * 0.5 } else { size };
```

Replace with Ouroboros-calibrated scaling:
- Add `pub regime_scales: HashMap<String, f64>` field to `RiskArbiter` struct (line 59)
- In the `adjusted_size` calculation, look up `self.regime_scales.get(&regime_string)` and use that multiplier. Fall back to the existing hardcoded values (0.5 for Reduce, 1.0 for Normal) if the key doesn't exist.
- Wire from main.rs: `engine.arbiter.regime_scales = dw.regime_scales.clone();`

**2c. `kelly_fractions` → RiskArbiter per-ticker Kelly caps**

- Add `pub kelly_fractions: HashMap<String, f64>` field to `RiskArbiter` struct
- At CHECK 27 area (line 264), if `kelly_fractions` has a value for the current ticker, use it as the Kelly cap instead of the global max. Fall back to global if missing.
- Wire from main.rs: `engine.arbiter.kelly_fractions = dw.kelly_fractions.clone();`

**2d. `bayesian_win_rate`** — SKIP. Already wired to Python via `TickContext.win_rate`.

**Run Ralph Wiggum after ALL of Order 2. Report pass/fail with test count.**

### ORDER 3: BOUND THE WAL CHANNEL

`wal_actor.rs` line 89: `crossbeam_channel::unbounded()` → under tick burst this can OOM the 4GB server.

1. Change line 89 to: `let (tx, rx) = crossbeam_channel::bounded(50_000);`
2. `WalHandle::append()` (line 40) already handles `TrySendError::Full` at line 44 — it prints a warning and returns false. This will now actually trigger under real backpressure. **Verify the existing error message makes sense.** Change it from `"WAL: channel full (unexpected for unbounded)"` to `"WAL: channel full (50K capacity)"`.
3. Optional: if `crossbeam::bounded` supports `.len()`, add a log at >40,000 entries (80% capacity). Don't block on this — if it's not trivial, skip it.
4. Do NOT escalate WAL backpressure to HALT — that stops exit processing, making the problem worse.
5. Check all tests that write large event batches — they may need adjustment for bounded channels.

**Run Ralph Wiggum. Report pass/fail with test count.**

### ORDER 4: DEPLOY TO EC2

```bash
# From macOS:
rsync -avz --exclude '.git' --exclude 'target' --exclude '.venv' --exclude '__pycache__' \
  -e "ssh -i ~/.ssh/nzt48-key.pem" \
  /Users/rr/nzt48-signals/nzt48-aegis-v2/ ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/

ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "cd /home/ubuntu/nzt48-aegis-v2 && docker compose up -d --build"
```

Wait 90 seconds (Docker build compiles Rust on c7i-flex.large — it takes time). Then verify:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "docker ps --format 'table {{.Names}}\t{{.Status}}' && echo '---' && docker logs aegis-v2 2>&1 | tail -40"
```

**Required output**:
- 3/3 containers: `aegis-v2` (Up), `aegis-ib-gateway` (Up, healthy), `aegis-redis` (Up, healthy)
- Engine logs show: ticker subscriptions, Python bridge alive, Ouroboros weights loaded, no PANIC/ERROR

**Report**: `"DEPLOYED — 3/3 containers healthy. N tickers subscribed. Engine processing."`

If Docker build fails on EC2, read the build log. Common issues:
- Missing `crossbeam-channel` version → check Cargo.toml
- PyO3 linking errors → the Dockerfile should use `--no-default-features` for lib builds only; the binary (`aegis`) doesn't use PyO3 features
- Memory issues during compilation → `CARGO_BUILD_JOBS=1` in Dockerfile to limit parallel compile

### ORDER 5: VERIFY PAPER TRADING IS LIVE

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "docker logs aegis-v2 2>&1 | tail -200"
```

Confirm ALL of these in the logs:
- [ ] Realtime tick data arriving (NOT "delayed" or "frozen")
- [ ] Python brain evaluating ticks (you see `signal` or `no_signal` results)
- [ ] Ouroboros weights loaded: `"Ouroboros: WR=50.0%, chandelier_mult=3.00"` (or calibrated values)
- [ ] DynamicWeights APPLIED (chandelier_atr_mult, regime_scales, kelly_fractions wired to engine)
- [ ] WAL channel bounded (no "WAL channel full" warnings unless under extreme load)
- [ ] If a signal passes confidence ≥ 65, Risk Arbiter evaluates it (31 checks)
- [ ] No PANIC, no ERROR, no "thread panicked"

Check the WAL for trade records:
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 "docker exec aegis-v2 cat /app/events/current.ndjson | tail -20"
```

If ticks are arriving but no trades: that's FINE. Crucible mode (max 1 position) with 31 risk checks means trades are rare. The engine is working correctly if it's receiving ticks, evaluating signals, and applying risk checks — even if every signal gets vetoed.

---

## HARD RULES

1. **Ralph Wiggum after every Order.** Zero errors. Zero warnings. All tests green. Non-negotiable.

2. **`#![deny(warnings)]` + `#![deny(clippy::unwrap_used)]`** — the two denials mean:
   - Every variable must be used immediately after declaration
   - Every import must be consumed
   - No `.unwrap()` — use `unwrap_or`, `unwrap_or_else`, `if let`, or `?`
   - No dead code paths
   - You cannot "add a field now, wire it later" — the compiler rejects unused fields

3. **Wire, don't rewrite.** 3,500 LOC of dead code exists and has tests. These are already implemented — DO NOT wire them now, DO NOT delete them, DO NOT touch them:
   - `Executioner` (exit_engine.rs:526-635) — order lifecycle with retries
   - `InfiniteChandelier` (exit_engine.rs:434-479) — adaptive 8-multiplier trailing
   - `HotScanner` / `RotationScanner` (scanner.rs) — momentum/rotation scanners
   - `GarchRegistry` (garch_inference.rs) — O(1) per-tick volatility
   They exist under `#[allow(dead_code)]` or in test modules. Leave them alone.

4. **Do NOT build**: Dashboard, Telegram bot, PDF reports, health HTTP endpoints, new strategies, monitoring, alerts, or ANYTHING not in Orders 1-5. Zero scope creep. Zero.

5. **Known limitations — accept them, do not fix them**:
   - Synthetic spread (`ibkr_broker.rs:247`: `(high-low)*0.1`) — future fix, not blocking
   - Ouroboros runs at 23:50 ET (12h after LSE close) — acceptable latency
   - Kelly learning rate conservative (α=0.3) — acceptable for paper phase
   - GARCH 80% threshold blocks 5x ETPs — acceptable (3x ETPs work fine)
   - ISA annual limit counter never increments — paper mode, doesn't matter
   - Asian/European session modules exist but aren't wired — wire later, not now

6. **ExitStrategy is a trait object** (`Box<dyn ExitStrategy>`). You CANNOT downcast. You CANNOT access concrete struct fields through the Box. Use trait methods or constructors.

7. **If stuck 20 consecutive attempts on any compiler error, STOP.** Report: what file, what line, what error, what you've tried. Do not spiral.

8. **Do everything in one go.** Orders 1 through 5, sequentially, in this session. Do not stop between orders. Do not ask "should I continue?" — just continue.

---

## WHAT DONE LOOKS LIKE

When you've completed all 5 orders, print this:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AEGIS V2 — LIVE PAPER TRADING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Mode:       Crucible (max 1 pos, £10K)
  Data:       REALTIME via IBKR
  Universe:   39+ tickers
  Exit Path:  Chandelier → Sell → Broker  ✅
  Ouroboros:  Weights applied              ✅
  WAL:        Bounded 50K                  ✅
  Deployed:   EC2 3.230.44.22              ✅
  Status:     Accumulating toward 100-trade gate
              (WR ≥ 40%, Sharpe > 0, DD < 8%)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ralph Wiggum: PASSED (N tests)
  Containers:   3/3 healthy
  Paper equity: £10,000
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Then stop. The engine trades autonomously. Ouroboros recalibrates nightly at 23:50 ET. Trades accumulate toward the 100-trade Crucible validation gate.

**Execute Orders 1-5. All of them. Now. Go.**

---

*v6.0 — 11 March 2026 | Supersedes all prior directives*
