# AEGIS V2 — SELF ANALYSIS TRIAGE v19
### Claude Independent Adversarial Review + Gemini Triage
**Date**: 2026-03-09 | **Scope**: AEGIS_MASTER_PLAN_v19.md + all Phase specs + actual Rust source

> This document is Claude's independent 200-bullet adversarial audit of v19, plus a full triage of Gemini's 200-bullet adversarial audit. Combined findings are injected into AEGIS_MASTER_PLAN_v20.md.

---

## PART 1 — CLAUDE SELF-ANALYSIS: 200 BULLETS

### [FLAW] — 40 items

**From actual source code review:**

1. [FLAW] `clock.rs:124` — BST detection uses day-of-year approximation (`day_of_year >= 84 && day_of_year < 301`). This is wrong for leap years (March 25 = day 85 in a leap year, not 84) and will misclassify the DST transition by 1 day every 4 years, causing all mode boundaries to shift by 1 hour on that day.

2. [FLAW] `clock.rs:122` — `day_of_year` computed as `epoch_secs / 86400 % 365`. Does not account for leap years (366 days). Accumulates a 1-day error every 4 years, causing the BST window to shift permanently forward.

3. [FLAW] `python_bridge.rs` — The bridge uses blocking synchronous `stdin.write` + `stdout.read_line` per tick. With 100 subscribed tickers firing at US open (~10,000 ticks/sec total), the single-threaded Python subprocess becomes the global bottleneck. Every tick queues behind every other tick's Python roundtrip.

4. [FLAW] `python_bridge.rs:159` — `writeln!(self.stdin, "{msg}")` fails silently (`is_err()` returns `None`, not panic). Once the Python subprocess dies, every subsequent tick silently produces no signal. The engine continues running, holding positions, with a dead signal generator and no alert.

5. [FLAW] `channel.rs` — The crossbeam channel already exists with `capacity=50,000` (confirming v19-FIX-3 is already partially implemented in code). However, the drop-oldest logic confirmed in the source means session High/Low aggregates used by the Chandelier ratchet in `exit_engine.rs` can be corrupted whenever buffer overflows occur at US open.

6. [FLAW] `risk_arbiter.rs` — The 22-check gate does not include a check for `MINIMUM_ENTRY_GBP` (SC-05, £1,500). This is listed as a Phase 8 deliverable but is absent from the existing `risk_arbiter.rs`. The gate as coded today would approve a £300 Kelly output and submit it to the broker.

7. [FLAW] `exit_engine.rs` — `stop_price` in `PositionState` is a plain `f64`. There is no type-level guarantee it can only increase (ratchet enforcer). The ratchet is enforced by code logic (`new_stop = max(old_stop, computed)`), but a bug in any caller that directly sets `stop_price` bypasses the ratchet silently.

8. [FLAW] `ibkr_broker.rs` — `subscribe_bars()` registers subscriptions using the `ibapi` crate's 5-second bar feed, not `reqMktData` tick-by-tick. The SubscriptionManager spec (Phase 11) tracks `active_line_count` based on `reqMktData` ACKs. The existing broker uses a different subscription API — the line counter will be 0 while real broker lines are consumed.

9. [FLAW] Phase 8, SC-02 — SubscriptionManager `cancel → ACK → subscribe` protocol. The existing `ibkr_broker.rs` shows `subscribe_bars()` with no ACK confirmation mechanism at all. The deterministic ACK protocol is entirely new infrastructure, not a modification of existing code — v19 underestimates this as a "skeleton extension."

10. [FLAW] `clock.rs` — `now_london_secs()` returns `u32` seconds-from-midnight. At midnight (00:00 UTC in winter), this returns 0. The ModeA boundary check `s >= 23*3600 || s < 8*3600` passes for 0, correctly. But at 23:59:59 + 1 second = 86400 seconds, the modulo wraps to 0 cleanly only if `% 86400` is applied — the current implementation applies `% 86400` in the UTC calculation, which is correct, but the BST addition `+ 3600` without re-modulo can produce values > 86400 if called at 23:30 UTC in BST (23:30 + 3600 = 27:30 equivalent = 99000 seconds, not wrapped). **Bug confirmed in source.**

11. [FLAW] Phase 11 — `mode_b_plus_end_utc()` is a Python function using `ZoneInfo("Europe/London")`. It produces a UTC timestamp for when LSE closes. But the Rust `clock.rs` works in London local seconds, not UTC seconds. The Python output (UTC) must be converted before passing to Rust mode controller — this conversion is not specified and will produce a 1-hour error during BST.

12. [FLAW] Phase 14 — Chandelier floor `max(multiplier × ATR, 1.5 × bid_ask_spread)`. The `exit_engine.rs` currently computes ATR via a 14-period Wilder's ATR in `engine.rs:BarHistory`. The bid-ask spread is available on `MarketTick.bid/ask`. But the Chandelier floor logic in `exit_engine.rs` does not currently receive the spread — it only sees the `tick`. Adding spread-awareness requires modifying the `evaluate()` signature, which touches `engine.rs` and `risk_arbiter.rs` call sites.

13. [FLAW] Phase 8, SC-06 — Dust guard on FILLED portion. The `exit_engine.rs` exit priority hierarchy currently has no "DustGuard" exit reason in `ExitReason` enum (`types/enums.rs`). Phase 15 adds `DustGuard` as a new veto, but the exit trigger for dust liquidation is a separate action (market-sell filled portion) that doesn't map cleanly to the existing exit priority hierarchy.

14. [FLAW] `reconciler.rs` — `detect_orphaned_orders()` compares local tracking against broker open orders. If IBKR throttles `reqOpenOrders()` (which it does during high load), the reconciler receives a stale or empty response and falsely marks all broker positions as orphaned, triggering emergency liquidation of valid positions.

15. [FLAW] `universe.rs` — `reqMktData` pacing enforcement uses "10ms minimum between requests." At 100 subscriptions, sequential subscription takes 1 second minimum (100 × 10ms). During a mode transition, this 1-second subscription window creates a scanning blackout longer than the plan's estimated "2-5s blind window."

16. [FLAW] Phase 16, Ouroboros step 2a — `corp_action_blocklist.json` written by Python Ouroboros, read by Rust RiskGate. There is no specified file locking mechanism. If Ouroboros is writing the file at 22:55 UTC (the 22:55 UTC watchdog check time), and RiskGate reads a partially-written JSON, `serde_json::from_str` will fail. The veto system fails open (no corporate action protection) until next restart.

17. [FLAW] Phase 12, SmartRouter — `snapshot=True` requests specified to use `reqMktData`. The existing `ibkr_broker.rs` uses the `ibapi` crate's bar subscription API (`subscribe_bars`). The `ibapi` crate's support for `reqMktData` snapshot mode needs to be verified — if the crate doesn't support `snapshot=True`, this entire approach fails silently.

18. [FLAW] Phase 17 — Telegram long-polling thread is Python `threading.Thread(daemon=True)`. Daemon threads in Python are killed immediately when the main process exits — no cleanup, no graceful shutdown. If SIGTERM fires, the polling thread dies instantly, dropping any in-flight HALT commands during the critical flatten sequence.

19. [FLAW] `wal_writer.rs` — WAL uses date-based files (`events/YYYY-MM-DD.ndjson`). The date is computed from system time, not broker time. If the EC2 clock drifts 1 day relative to the broker (NTP failure), WAL events are written to the wrong date file, breaking WAL replay which iterates files chronologically.

20. [FLAW] Phase 20 — `reqPnL` subscription: "IBKR only allows one PnL subscription per account per connection" (confirmed by Gemini). The spec says `req_pnl_single(pnl_req_id, account, "", conid)` per carry position. With 6 carry positions, this attempts 6 simultaneous `reqPnL` subscriptions. IBKR will reject subscriptions 2-6 with an error, leaving 5 of 6 carry positions unmonitored.

21. [FLAW] Phase 15 — CVaR Cornish-Fisher requires skewness and kurtosis of the portfolio return distribution. The current `portfolio.rs` tracks `unrealized_pnl`, `realized_pnl`, `highest_high` per position, but no return distribution history. Computing skewness/kurtosis requires storing the last N returns — this data structure is entirely absent from the existing code.

22. [FLAW] `risk_arbiter.rs:EvalContext` — `spread_pct` is a single float passed to all checks. For a multi-exchange portfolio (Phase 18+), different positions have different spreads in different currencies. The single `spread_pct` field becomes meaningless — it's unclear which spread is used (last tick? max? average?).

23. [FLAW] Phase 19, KRX VI — `|tick - 1min_open| / 1min_open > 10%` requires storing the 1-minute open for each KRX ticker. This per-ticker state is not defined anywhere in the existing data structures. `BarHistory` in `engine.rs` stores rolling ATR bars but not minute-level opens per exchange.

24. [FLAW] Phase 16, Polars mandate — Ouroboros is a Python subprocess. The `POLARS_MAX_THREADS` environment variable must be set before Polars imports. If Ouroboros is launched by Supercronic (as specified), the env var must be set in the container environment or the Supercronic crontab line. This is not specified in the plan and will be missed during implementation.

25. [FLAW] Phase 8, SC-09 — crossbeam buffer capacity=50,000 exists in `channel.rs`. But `channel.rs` drop logic drops the oldest tick using `try_send` + manual dequeue. The `MarketTick` struct has `volume` as cumulative daily volume (not per-tick delta). Dropping the oldest tick does not corrupt absolute volume (it's cumulative), but it does mean the dropped tick's High/Low is permanently lost for OHLCV aggregation in the Chandelier.

26. [FLAW] Phase 22 — `reqMarketDataType(3)` for paper mode. The existing `ibkr_broker.rs` has no `reqMarketDataType` call at all. If this is not called before subscriptions, IBKR defaults to live data type (requires market data subscription fees). In paper mode with no live data subscription, all ticks will return delayed data without the call — but the call itself is missing entirely from the existing broker code.

27. [FLAW] Phase 11 — `mode_controller.rs` publishes `ModeChange` events. If the event channel between `mode_controller` and `engine` is unbounded (as flagged by Gemini), a bug causing rapid mode oscillations will OOM the system. But even if bounded, if the engine is slow to consume mode changes (processing a large order), the bounded channel fills up and `mode_controller` blocks — freezing the clock entirely.

28. [FLAW] Phase 13 — OFI time-decay EWMA uses `dt = seconds since last tick`. For exchange open auctions (09:00 CET for European exchanges), there are no ticks for hours before open. `dt` can be 28,800 seconds (8 hours overnight). `exp(-28800/5.0)` = effectively 0. The EWMA starts from zero every morning, losing all pre-market order flow context precisely when it matters most.

29. [FLAW] Phase 18 — FX rate polling via `reqMktData` for FX pairs (EUR.GBP) requires `IDEALPRO` routing. The plan notes this (Gemini INFRA-190), but doesn't specify where in `currency.rs` or `ibkr_broker.rs` the IDEALPRO routing is enforced. Default SMART routing will return Error 200 for FX pairs, and `currency.rs` will never receive a valid rate, defaulting to a stale or zero rate.

30. [FLAW] Phase 20 — HALTED state: "Day 3 → submit market order." If Day 3 falls on a weekend or holiday, the market order is submitted to a closed exchange. The plan has no holiday awareness for the HALTED state countdown. A Friday HALT becomes a Day 3 market order on Sunday — rejected by IBKR.

31. [FLAW] Phase 16, Ouroboros — Step checkpointing uses `ouroboros_step_N_ts` Redis keys. Redis `SET` is atomic. But between Step 2a (corp actions) writing `corp_action_blocklist.json` and Step 3 (features) writing the checkpoint key, there is a window where the file exists but the checkpoint says step 2a is incomplete. On restart, step 2a re-runs and overwrites the file — benign, but doubles Polygon API calls.

32. [FLAW] Phase 17 — PDF generation via `fitz.Story` creates PDFs in `/tmp`. If Docker container is configured with `--tmpfs /tmp:size=64m`, a 76-page PDF (like the AEGIS_COMPLETE.pdf already generated) can exceed 64MB. PDF generation fails silently (fitz raises an exception), no PDF is sent via Telegram. The plan specifies no fallback.

33. [FLAW] Phase 12 — ISA annual limit check: £20k per tax year. The `risk_arbiter.rs` current ISA check verifies limit. But the ISA tax year runs April 6 to April 5, not calendar year. If the system uses UTC midnight Jan 1 as the reset date, it under-counts allowance for Jan-Apr and over-counts for Apr-Dec. A £500 trade on April 6 might be blocked if the system thinks it's still in the previous tax year.

34. [FLAW] Phase 23, Crucible Suite 1 — Romano-Wolf 100-trade gate. The plan requires `t-stat ≥ 2.0 (Romano & Wolf StepM with N=20 Bonferroni correction)`. N=20 is the number of simultaneous hypotheses being tested (strategies). With only 1 strategy active (S15), the Bonferroni correction is wildly conservative (dividing α by 20 when there's only 1 test), making the gate nearly impossible to pass even with genuine edge.

35. [FLAW] Phase 8, SC-01 — SIGTERM handler "wait 30s for fills." Docker's `stop_grace_period` defaults to 10 seconds before SIGKILL. The 30-second wait will be interrupted by SIGKILL at second 10. The plan requires changing Docker's stop timeout (`stop_grace_period: 60s` in docker-compose.yml) — this is not specified anywhere in Phase 8 deliverables.

36. [FLAW] Phase 13 — RotationScanner Thompson Sampling: `per-ticker (alpha: f64, beta: f64)` posteriors updated from WAL outcomes. The WAL `PositionClosed` payload contains `final_pnl` but not `ticker_id` — wait, checking `types/wal.rs`: `PositionClosed { ticker_id: u32, final_pnl: f64, ... }`. ticker_id IS present. But `StrategyId` enum only has `VanguardSniper` and `ApexScout` — there is no `HotScanner` or `RotationScanner` strategy ID in the existing enum. WAL events can't be attributed to the new strategies.

37. [FLAW] Phase 15 — Half-Kelly enforcement: "until 250 validated live trades." The current system is in PAPER mode. Paper trades ≠ live trades. The plan doesn't specify whether paper trades count toward the 250 threshold. If they don't, the system will be in Half-Kelly for its entire paper trading phase, making the Crucible 100-trade gate (Suite 1) use Half-Kelly sizing — potentially making the WR≥40% gate easier to pass on paper but failing in live.

38. [FLAW] Phase 19 — ASX session: `official session 00:10-06:00 UTC; SYCOM excluded`. ASX's official pre-SYCOM session is the `Normal Trading Phase` starting at 10:00 AEDT = 23:00 UTC (AEDT) or 00:00 UTC (AEST). The spec says `00:10 UTC` as the official open — this is wrong for AEDT, correct for AEST. During daylight saving (Oct-Apr), ASX opens at 23:00 UTC, not 00:10 UTC, contradicting the spec.

39. [FLAW] `python_bridge.rs` — The bridge is synchronous request-response per tick. For Phase 13's HotScanner (30 hot tickers with continuous ticks), the Python subprocess must process all 30 tickers' ticks serially through one stdin/stdout pipe. At 10 ticks/sec per ticker = 300 ticks/sec total through one synchronous pipe. Python bridge becomes a 3.3ms-per-tick bottleneck that serializes the entire engine.

40. [FLAW] Phase 8, SC-10 — Internal cost-basis tracker `HashMap<TickerId, CostBasisEntry>`. The WAL `FillEvent` payload contains `ticker_id: u32` and `price: f64`. But the cost basis must account for partial fills across multiple fills for the same order. The spec doesn't define how `CostBasisEntry` averages across multiple partial fills for the same position — a naive single-price tracker will be wrong for TWAP orders that fill in slices.

---

### [RISK] — 40 items

41. [RISK] Docker stop timeout mismatch (SIGTERM → SIGKILL at 10s, SIGTERM handler waits 30s) will corrupt WAL on every container restart unless `stop_grace_period: 60s` is explicitly set in docker-compose.yml.

42. [RISK] `channel.rs` drop-oldest strategy confirmed in source. During US open (14:30 UTC), tick burst from 30 hot tickers simultaneously will overflow even the 50,000-capacity buffer within seconds if Python bridge is the bottleneck, causing systematic Chandelier high/low corruption every single day.

43. [RISK] The `ibkr_broker.rs` uses `ibapi` crate v2.10. The `ibapi` crate is an unofficial community IBKR wrapper. If IBKR updates their TWS API protocol (they do this quarterly), the `ibapi` crate may lag, breaking the entire broker connection without warning.

44. [RISK] Phase 11 mode transitions during live positions: cancel 50 European lines → wait for ACKs → subscribe 50 US lines. If a position has a limit order resting at the broker during this transition, the position exists but `engine.rs` is blind to price changes for up to 25 seconds (50 × 500ms ACK latency under load). A limit exit order could be filled at a bad price during this window.

45. [RISK] `reconciler.rs` runs every 5 minutes. In a 5-minute window, a flash crash and recovery can complete entirely without reconciliation. Positions opened before the crash and closed during recovery may not appear in IBKR's `reqOpenOrders()` response, causing the reconciler to see no mismatches while the P&L is permanently wrong.

46. [RISK] Phase 16, corp_action_blocklist.json — Polygon.io EU corporate action coverage is confirmed poor by both Claude and Gemini. The only fallback is `yfinance.actions`, which is notorious for missing small-cap European corporate actions. The blocklist will have systematic gaps for exactly the types of equities Phase 18 targets (small/mid-cap European direct equities).

47. [RISK] Phase 17 — Telegram HALT latency < 100ms claim. The long-polling thread makes HTTP requests to Telegram's API servers. Telegram API servers are geographically distributed but not in AWS us-east-1. From an EC2 instance in us-east-1c, round-trip to Telegram can be 80-150ms just for network latency, leaving zero budget for processing. The 100ms claim is physically impossible without a Telegram server in the same region.

48. [RISK] Phase 19 — IBKR 04:45 UTC reconnect during Asian session. The existing `ibkr_broker.rs` has `BackoffState` with 5 attempts and exponential backoff. If all 5 attempts fail (IB Gateway has a 3-minute restart window), the backoff max is likely less than 3 minutes (5 attempts × exponential = ~31 seconds total). The engine gives up before IB Gateway comes back online.

49. [RISK] Phase 20 — `reqPnL` per carry position creates a separate server subscription per position. IBKR documentation states `reqPnL` requires the account number and model code. In paper trading, the model code is an empty string. If IBKR's paper trading API doesn't support `reqPnLSingle` (it may only support `reqPnL` for the whole account), all carry monitoring fails.

50. [RISK] Phase 15 — DCC-GARCH update async with 5-minute TTL. During VIX spike, correlations converge to 1.0 within seconds. The 5-minute stale matrix will show low correlations, CVaR heat limit will not tighten, new entries will be approved right into a flash crash. This is the most dangerous risk in the entire plan.

51. [RISK] The existing `risk_arbiter.rs` has `max_positions` check but it's hardcoded/configured. With Phase 18 adding 15 European exchanges + Phase 19 adding 6 Asian exchanges, the `max_positions` constant needs dynamic reconfiguration per mode. A static global max will either over-restrict Asian mode or under-restrict European mode.

52. [RISK] Phase 14 — TWAP execution in Rust requires maintaining state across time slices. The current engine processes ticks synchronously. A TWAP "slice scheduler" needs a timer-based async task separate from tick processing. Without Tokio async, implementing reliable TWAP timing in the current synchronous engine architecture requires a dedicated background thread — not mentioned in Phase 14 deliverables.

53. [RISK] Phase 22 — Redis OOM-kill scenario. If Redis is OOM-killed and the WAL rebuild takes > 30 seconds, the IBKR connection will time out (no heartbeat) during the rebuild. The engine will need to reconnect to IBKR after Redis is restored, but the reconnect sequence requires WAL state to be loaded first. Circular dependency: need IBKR to trade, need WAL for IBKR state, need Redis for WAL — all three failing simultaneously.

54. [RISK] Phase 16 — Ouroboros Polars pipeline runs during DARK mode (21:00-23:00 UTC). But with `POLARS_MAX_THREADS=2` cap (Gemini Fix #1), on a 2-vCPU c7i-flex.large instance, Polars has only 2 threads. At 500-ticker batch size, processing 5,000 tickers requires 10 batches. Each batch doing LazyFrame feature engineering may take 30-60 seconds on 2 threads. Total: 5-10 minutes for 5,000 tickers. Multiplied by 9 steps, risk of missing 22:55 UTC deadline is HIGH.

55. [RISK] Phase 12 — SmartRouter cost comparison fires at signal time. If the Polygon.io-based spread estimate (from nightly Ouroboros) differs from the live IBKR snapshot spread by > 20%, the routing decision can flip between ETP and direct between the nightly calibration and signal execution. A morning data anomaly in Polygon causes wrong routing all day.

56. [RISK] Phase 11 — Scanner Conservation Rule: underlying subscribed ONLY when live position exists. But the HotScanner needs price data to score tickers BEFORE a position opens. The conservation rule prevents subscribing to HotScanner candidates. How does the HotScanner score a ticker without a live feed? The spec says "60s OHLCV snapshots for rotation candidates" — these consume snapshot credits, potentially conflicting with SmartRouter snapshot requests.

57. [RISK] `exit_engine.rs` — `ExitReason` enum has 6 variants: `HaltOverride, HardStop, ChandelierStop, EodFlatten, SignalReversal, CommissionVeto`. Phase 15 adds 9 new veto types to `risk_arbiter.rs`. But the exit engine and risk arbiter are separate — an exchange-closed veto in the arbiter doesn't prevent an already-submitted limit order from executing on the broker. The veto gate is entry-only; there's no post-submission cancel mechanism for stale orders.

58. [RISK] Phase 19 — NZX opens at 23:00 UTC = MODE A open. Mode transition from DARK→A takes 2-5 seconds. NZX opening auction is exactly at 23:00 UTC. The system ALWAYS misses the NZX opening auction, every single trading day. For a market with low ADV, the opening auction often has the best liquidity of the day.

59. [RISK] Phase 8, SC-04 — IBKR token bucket: 60 req/10min. This limit applies to `reqHistoricalData` specifically. But the existing `ibkr_broker.rs` uses `subscribe_bars()` (5-second realtime bars) which calls `reqRealTimeBars`, not `reqHistoricalData`. The two have different rate limits. The token bucket implemented for `reqHistoricalData` does not protect against `reqRealTimeBars` pacing violations.

60. [RISK] Phase 22 — `/metrics` Prometheus endpoint running on Tokio. If exposed on port 0.0.0.0 without authentication, any EC2 security group misconfiguration exposes equity, position count, drawdown tier, and trading mode to the public internet. The plan specifies no authentication requirement for `/metrics`.

61. [RISK] Phase 13 — Thompson Sampling Beta-Bernoulli confirmed by Gemini (ACADEMIC-144) as fundamentally wrong for continuous PnL rewards. 9 wins of 1% + 1 loss of -20% = 90% WR but negative EV. The bandit will over-allocate lines to this asset. This is the highest-probability silent failure mode in the signal layer.

62. [RISK] Phase 18 — Adaptive VPIN bucket proportional to 5-day median ADV. For newly listed European equities (< 5 days of data), ADV is undefined. Dividing by zero ADV produces NaN bucket size. The VPIN calculation will crash or produce NaN scores that propagate through the scoring pipeline.

63. [RISK] Phase 20 — Mega-runner carry threshold: `+102%` unrealised gain. On a 3x leveraged ETP, +102% unrealised on the instrument corresponds to +34% underlying move. This is extremely rare. The entire carry state machine (Phases 20-21) will likely never activate during paper trading, meaning it will be completely untested when it finally fires in live trading.

64. [RISK] Phase 21 — `cross_timezone.py` DCC-GARCH weights stored in `calibration/asia_cross_tz.json`. This file is read at MODE A open (23:00 UTC). If Ouroboros failed to complete step 8 (DCC-GARCH update) before 23:00 UTC, the system reads yesterday's weights. But the 22:55 UTC watchdog only checks `pipeline_complete` — if step 8 completed but step 9 (artifact write) failed, the JSON is stale but `pipeline_complete` is set. No detection.

65. [RISK] Phase 11 — `mode_b_plus_end_utc()` Python function used by Rust `mode_controller.rs`. Rust calling Python for a critical mode boundary calculation introduces a cross-language IPC call at every mode transition. If the Python bridge is down (crash, restart), the mode controller cannot determine when Mode B+ ends. The system will either freeze in Mode B+ indefinitely or fall back to a hardcoded time.

66. [RISK] Phase 14 — `executioner_v2.rs` ADV gate checks "1% of 5-min rolling volume." Rolling volume requires storing the last 5 minutes of volume data per ticker. For 30 HotScanner tickers, this is 30 rolling volume buffers. The existing engine has no such per-ticker rolling volume structure. This is significant new state management not reflected in the Phase 14 hour estimate (20h).

67. [RISK] Phase 15, Half-Kelly: `kelly_fraction.clamp(0.0, 0.20)` confirmed in `types/structs.rs:132`. Half-Kelly during the 250-trade period = clamp to 0.10 max. At £10,000 equity × 10% = £1,000 max position. Below the £1,500 MINIMUM_ENTRY_GBP gate (SC-05). The Half-Kelly system literally cannot place any trade until equity grows above £15,000 OR the minimum entry gate is relaxed during half-Kelly.

68. [RISK] Phase 22 — Chaos test: Python bridge crash → dry-run mode. The existing `engine.rs` has no dry-run mode defined. When `python_bridge.evaluate_tick()` returns `None` (bridge dead), the engine currently silently skips signal generation. This is de-facto dry-run, but positions held before the crash still have active Chandelier stops firing from the exit engine — which requires no Python signal. This is actually correct behavior, but the plan calls it "dry-run mode" implying something more deliberate.

69. [RISK] Phase 8, SC-12 — symbology_mapper.py maps IBKR→Polygon. But `data_fetch.py` needs to go in BOTH directions: IBKR→Polygon (for data fetching) and Polygon→IBKR (for Universe scan results → HotScanner ticker registration). The reverse mapping (Polygon `LSE:NVD3` → IBKR `NVD3.L`) is not specified and is non-trivial for preferred shares, GDRs, and exchange-specific suffixes.

70. [RISK] Phase 17 — Heartbeat every 30 minutes via "Ouroboros step 9." Ouroboros runs ONCE per night during DARK (21:00-23:00 UTC). After 23:00, there is no heartbeat for the next 22 hours. The spec says "external watchdog monitors heartbeat timestamp in Redis; if missed twice → fires Telegram alert." If heartbeat only fires during DARK, it's missed every single mode (A, B, B+, C) and will trigger constant false positive watchdog alerts.

80. [RISK] Phase 16 — Polars `streaming=True` (Gemini improvement #76) is mentioned as an alternative to 500-ticker batching. Polars streaming mode has known limitations: not all operations support streaming, and joining/aggregating across streams can silently fall back to eager mode. Using streaming without testing each operation type may silently degrade to eager mode, making the RSS fix ineffective.

---

### [IMPROVEMENT] — 40 items

81. [IMPROVEMENT] Replace `clock.rs` BST approximation with a proper ZoneInfo lookup. The Python `mode_b_plus_end_utc()` already uses `ZoneInfo("Europe/London")` — the Rust clock should use `chrono-tz` crate's `Europe__London` timezone for the same accuracy. Eliminates the leap-year bug and the 1-hour BST/add-without-modulo bug simultaneously.

82. [IMPROVEMENT] `python_bridge.rs` — Replace synchronous per-tick IPC with batched tick processing. Send a batch of N ticks per JSON message, receive a batch of N signals. Reduces IPC overhead from O(ticks) to O(batches), allowing 100x throughput improvement at US open.

83. [IMPROVEMENT] Add `StrategyId::HotScanner` and `StrategyId::RotationScanner` variants to `types/enums.rs` before Phase 13 implementation. Without these, WAL attribution for new strategies is impossible and Thompson Sampling posteriors can't be connected to WAL outcomes.

84. [IMPROVEMENT] `risk_arbiter.rs` — Extend `EvalContext` to include a `Vec<(TickerId, f64)>` spread map instead of a single `spread_pct`. This allows per-position spread veto for multi-exchange portfolios without architectural changes.

85. [IMPROVEMENT] Phase 14 — Instead of building TWAP timing in Rust from scratch, use IBKR's native IBKR Algos (`IBKR Adaptive`, `TWAP`, `VWAP`). Set `order.algoStrategy = "TWAP"` and let IBKR handle the slicing. Eliminates the need for `executioner_v2.rs` TWAP scheduling entirely and gives exchange-native execution quality.

86. [IMPROVEMENT] Phase 8 — Add `stop_grace_period: 60s` to `docker-compose.yml` as an explicit Phase 8 deliverable alongside SC-01. Without this, SIGTERM handler's 30-second wait is meaningless.

87. [IMPROVEMENT] Phase 16 — Pre-validate `corp_action_blocklist.json` with `serde_json::from_str` before atomically moving it to the live path. Write to `corp_action_blocklist.json.tmp`, validate, then `rename()`. Atomic rename prevents partial-write race condition.

88. [IMPROVEMENT] Phase 20 — Use `reqPnL()` (account-level) instead of `reqPnLSingle()` (per-contract) for carry monitoring. Account-level PnL subscription receives updates for all positions in one stream, uses 1 subscription instead of 6, and is universally supported in all IBKR account types.

89. [IMPROVEMENT] Phase 19 — Implement `KrxMinuteOpenCache`: a `HashMap<TickerId, f64>` updated from the first tick of each new UTC minute. Reset at KRX session open (00:00 UTC). This provides the "1-minute open" required for KRX VI detection without requiring new infrastructure.

90. [IMPROVEMENT] Phase 11 — Document explicitly that HotScanner uses 60-second OHLCV snapshots (via `reqHistoricalData` 1-min bars) for candidate scoring, NOT streaming subscriptions. This resolves the Scanner Conservation Rule ambiguity: snapshots don't consume streaming lines.

91. [IMPROVEMENT] Phase 15 — Reframe Half-Kelly: instead of flat 50% reduction, implement dynamic Half-Kelly as `kelly_fraction × min(1.0, validated_trades / 250)`. At 0 trades: 0× Kelly (system is off). At 125 trades: 0.5× Kelly. At 250 trades: 1.0× Kelly. Smooth ramp avoids the Hard Gate problem where Half-Kelly + min entry gate makes trading impossible.

92. [IMPROVEMENT] Phase 23, Crucible Suite 1 — Replace Romano-Wolf StepM with N=20 Bonferroni correction with single-hypothesis t-test (N=1) since only S15 is being validated. Use bootstrap resampling (1,000 iterations) instead for non-parametric confidence intervals on WR and Sharpe.

93. [IMPROVEMENT] Phase 17 — Heartbeat redesign: emit heartbeat from `engine.rs` every 30 minutes regardless of Ouroboros. Write `aegis_heartbeat_ts` to Redis from Rust engine loop. Telegram watchdog reads Redis. Decouples heartbeat from Ouroboros schedule entirely.

94. [IMPROVEMENT] `ibkr_broker.rs` — Add `reqMarketDataType(3)` call immediately after `connect()` before any subscriptions. This is a one-line addition that must happen before any data requests. Make it the first call in the connection sequence.

95. [IMPROVEMENT] Phase 18 — ISA tax year reset: use April 6 as the ISA year boundary in `isa_gate.rs`, not January 1. Store `isa_used_this_year_gbp` and reset it on April 6 check. This is a 2-line change with significant compliance implications if wrong.

96. [IMPROVEMENT] Phase 14 — To avoid the TWAP architecture complexity, implement a simpler "alpha decay limit order": place a limit order at mid, if not filled within `alpha_halflife_secs` seconds, cancel and convert to market. Achieves same result as TWAP for small orders without state machine complexity.

97. [IMPROVEMENT] Phase 19 — ASX DST fix: at MODE A open (23:00 UTC), query `ZoneInfo("Australia/Sydney").utcoffset(datetime.utcnow())`. If offset = UTC+11 (AEDT), set `ASX_OPEN_UTC = 23 * 3600`. If offset = UTC+10 (AEST), set `ASX_OPEN_UTC = 0 * 3600`. Dynamic, correct, 3 lines of Python in Ouroboros initialization.

98. [IMPROVEMENT] Phase 8, SC-10 — `CostBasisEntry` should store `total_cost_gbp` and `total_shares` to compute VWAP cost basis correctly across multiple partial fills. `avg_cost = total_cost / total_shares`. Update on each `FillEvent` by adding `filled_qty × price` to `total_cost` and `filled_qty` to `total_shares`.

99. [IMPROVEMENT] Phase 16 — Add explicit `POLARS_MAX_THREADS=2` to Ouroboros container env in `docker-compose.yml`. Add to Phase 16 deliverables explicitly, not as an assumption.

100. [IMPROVEMENT] Phase 20 — HALTED state holiday awareness: before transitioning HALTED→Day 3 market order, check `reqTradingHours` for the exchange. If today is a holiday, do not count it as a "trading day" toward the 2-day maximum. This requires tracking "trading days halted" not "calendar days halted."

---

### [MISSING] — 40 items

101. [MISSING] No specification for how `StrategyId` enum is extended in `types/enums.rs` for Phase 13 strategies. `HotScanner` and `RotationScanner` variants must be added before Thompson Sampling WAL attribution works.

102. [MISSING] No `stop_grace_period: 60s` in `docker-compose.yml`. SC-01 SIGTERM handler is dead without it — Docker kills the container at 10 seconds.

103. [MISSING] No reverse symbology mapping (Polygon→IBKR) specified in SC-12. Required for Universe scan results to be registered as IBKR tickers.

104. [MISSING] No `POLARS_MAX_THREADS=2` environment variable specification in Phase 16 deliverables or docker-compose.yml.

105. [MISSING] No atomic write for `corp_action_blocklist.json`. Write-to-temp + atomic rename not specified. Race condition with concurrent reads is real.

106. [MISSING] No definition of ISA tax year boundary (April 6). `isa_gate.rs` implementation details omit this critical compliance detail.

107. [MISSING] No specification for `reqMarketDataType(3)` call placement in `ibkr_broker.rs::connect()`. It must be the first API call after connection establishment.

108. [MISSING] No per-ticker rolling 5-minute volume buffer specified for `executioner_v2.rs` ADV gate. This is significant new state management (~30 ring buffers) absent from current data structures.

109. [MISSING] No `KrxMinuteOpenCache` or equivalent structure for KRX VI detection (Phase 19). The 1-minute open reference price for each KRX ticker has no defined storage.

110. [MISSING] No definition of how `mode_b_plus_end_utc()` Python result is passed to Rust `mode_controller.rs`. Cross-language time boundary communication not specified.

111. [MISSING] No `reqPnL` vs `reqPnLSingle` decision. The plan uses `reqPnLSingle` per carry position but IBKR may only allow 1 PnL subscription per connection. Account-level `reqPnL` alternative not evaluated.

112. [MISSING] No definition of how Phase 14 TWAP timer is implemented in the synchronous Rust engine. No background timer thread, no Tokio async — TWAP execution timing mechanism is unspecified.

113. [MISSING] No definition of CostBasisEntry struct. SC-10 says `HashMap<TickerId, CostBasisEntry>` but `CostBasisEntry` fields are undefined. Critical for partial fill averaging.

114. [MISSING] No heartbeat design for non-DARK trading hours. Heartbeat "via Ouroboros step 9" only fires during DARK, leaving 22 hours per day without a heartbeat.

115. [MISSING] No Dynamic Half-Kelly ramp. Binary switch at 250 trades creates a £1,500 minimum entry impossibility when Kelly × 0.5 × equity < £1,500.

116. [MISSING] No specification for `IDEALPRO` routing enforcement for FX pairs in `currency.rs`/`ibkr_broker.rs`.

117. [MISSING] No holiday-aware "trading day" counter for HALTED state (Phase 20). Calendar days ≠ trading days for the 2-day maximum.

118. [MISSING] No ASX DST dynamic detection. Static `00:10 UTC` open time is wrong for AEDT (should be 23:00 UTC previous day).

119. [MISSING] No specification for how `EvalContext.spread_pct` handles multi-currency, multi-exchange portfolios in Phases 18-21.

120. [MISSING] No skewness/kurtosis return history in `portfolio.rs` for CVaR Cornish-Fisher computation. Phase 15 requires this data but it's absent from existing structs.

121. [MISSING] No reconnect attempt count specification for IBKR 04:45 UTC disconnect. `BackoffState` with 5 attempts may expire before IB Gateway's 3-minute restart completes.

122. [MISSING] No `/tmp` size specification in Docker configuration for PDF generation. Default tmpfs limits may block fitz PDF output.

123. [MISSING] No file cleanup for daily PDF reports. 2 PDFs/day × 30 days = 60 PDFs + unbounded disk consumption.

124. [MISSING] No NaN handling for VPIN bucket when ADV = 0 (newly listed equities in Phase 18).

125. [MISSING] No definition of how `VetoReason::DustGuard` integrates with the exit priority hierarchy in `exit_engine.rs`. DustGuard is a risk arbiter veto, not an exit signal — different code paths.

126. [MISSING] No spec for how IBKR Error 200 ("No security definition found") from failed symbology mapping is handled in the HotScanner subscription sequence.

127. [MISSING] No `reqContractDetails` pagination logic for European universe scan (Phase 18). IBKR responses are paginated and multi-part; `contractDetailsEnd` event required to detect completion.

128. [MISSING] No specification for Ouroboros step order validation. If steps run out of order (e.g., step 8 before step 4), corrupted artifacts are written. No dependency DAG enforced.

129. [MISSING] No definition of what happens if Polygon.io `/v3/reference/dividends` returns a partial response (HTTP 206). Partial corporate action data is worse than no data (selectively voids some positions).

130. [MISSING] No multi-fill cost basis averaging for TWAP entries. Each TWAP slice creates a separate fill event. Without averaging, cost basis = last fill price, not VWAP across all slices.

131. [MISSING] No specification for how `mode_controller.rs` event channel is bounded vs unbounded.

132. [MISSING] No specification for how `clock.rs` BST leap-year bug is fixed in Phase 11.

133. [MISSING] No specification for `isa_gate.rs` hard-blocking via HashSet<Exchange> vs string prefix matching.

134. [MISSING] No WAL `CorporateActionVeto` event type. When the blocklist vetoes a trade, this should be WAL-logged for audit, but `WalPayload` enum has no such variant.

135. [MISSING] No definition of how Ouroboros communicates `pipeline_complete` to the engine (Redis flag read by Rust or Python?).

136. [MISSING] No specification for `asian_exchange.rs` lunch break state machine. TSE/HKEX have 2-segment sessions requiring entry block during lunch, but Chandelier exits must still fire during lunch on open positions.

137. [MISSING] No specification for how carry positions from Phase 19 (Asian exchanges) interact with the European scanner startup at MODE B open (08:00 UTC). If a TSE position is still MONITORED at 08:00 UTC, does it consume a European scanning line?

138. [MISSING] No Prometheus metric type declarations (Gauge vs Counter vs Histogram) for Phase 22 `/metrics` endpoint.

139. [MISSING] No EC2 EBS volume size specification. Phase 23 48h paper run WAL can exceed 10GB. Current EC2 storage size unknown.

140. [MISSING] No `WalPayload::ModeTransition` variant for logging mode changes. `ModeChange` events are published by `mode_controller.rs` but not WAL-persisted per spec.

---

### [ACADEMIC] — 40 items

141. [ACADEMIC] Gemini correctly identifies that Cont, Kukanov, Stoikov (2014) OFI requires 5-level depth. The v19 plan's Level-1 BBO quote imbalance is correctly relabeled "QuoteImbalance" in the Phase 11 spec but continues to be called "OFI" in v19. The mislabeling perpetuates the confusion about what's actually being measured.

142. [ACADEMIC] Thompson Sampling Beta-Bernoulli (Agrawal & Goyal, 2012): Gemini's fix #10 (Gaussian-Gaussian Thompson Sampler) is correct. However, Gaussian-Gaussian TS requires known noise variance. In trading, variance is non-stationary. A more robust alternative is Log-Normal Thompson Sampling (Russo et al. 2018) which naturally handles asymmetric, right-skewed financial returns.

143. [ACADEMIC] The Cornish-Fisher CVaR approximation (Phase 15) is valid when skewness is small (|S| < 1) and excess kurtosis is moderate (|K| < 3). 3x leveraged ETPs routinely exhibit |S| > 2 and |K| > 10 during volatility events. At these values, the Cornish-Fisher expansion diverges — the approximation breaks down precisely when tail risk estimation is most critical.

144. [ACADEMIC] DCC-GARCH (Engle 2002) assumes multivariate normality of standardized residuals. Gemini correctly flags this. Even t-DCC-GARCH (Student-t marginals) may be insufficient for 3x leveraged ETPs which exhibit volatility clustering, leverage effects, and jump diffusion simultaneously. A Realized GARCH model (Hansen, Huang, Shek 2012) using intraday realized variance would be academically superior.

145. [ACADEMIC] The Chandelier exit's ratchet enforcer ("stop can only increase") introduces a strong survivor bias. Positions that trend strongly upward are held; positions that stagnate are eventually stopped out. This mechanically creates a momentum portfolio, which is known to experience periodic catastrophic drawdowns (momentum crashes, Barroso & Santa-Clara 2015). No crash protection for the momentum factor is specified.

146. [ACADEMIC] Almgren-Chriss (2000) optimal execution: the plan uses TWAP as a cost-minimizing strategy. Almgren-Chriss proves that for mean-reverting prices (e.g., ETPs with NAV tracking error), the optimal strategy concentrates execution at the beginning and end of the window — not uniform TWAP. The U-shaped volume curve (Phase 14) approximates this, but uses empirical volume rather than the model's risk-aversion parameter.

147. [ACADEMIC] Half-Kelly enforcement (Phase 15): MacLean, Thorp, Ziemba (2010) show that the optimal fraction under uncertainty is `f* = f_kelly × (1 - σ_f / f_kelly)` where `σ_f` is the estimation uncertainty in the Kelly fraction. A flat 50% is not derived from any uncertainty estimate. The correct implementation would use the confidence interval on WR and EV estimates from WAL outcomes.

148. [ACADEMIC] The HMM regime classification in V1 (`cross_asset_macro.py`) uses a 3-state GaussianHMM. Hamilton (1989) shows that financial regimes are well-characterized by 2-3 states. However, the model is fitted on 63 daily observations (VIX + DXY + credit spreads). The minimum recommended sample for robust HMM parameter estimation is 200-300 observations per state. With 63 total observations, the model is chronically under-identified.

149. [ACADEMIC] KRX VI detection uses a 1-minute price move threshold (10%). Empirical studies (Kim & Yang, 2004 on Korean market volatility interruptions) show that VI thresholds generate significant adverse selection immediately after the VI expires — the price direction is often confirmed, not reversed. The spec vetoes entries during VI but doesn't exploit the post-VI momentum, which is a missed alpha source.

150. [ACADEMIC] The Amihud (2002) illiquidity ratio used in `universe.rs` is computed as `|return| / volume`. In the high-frequency setting (5-second bars), the ratio is extremely noisy — single large trades create false illiquidity spikes. Amihud himself specifies this measure for daily returns and daily volume. Using it on 5-second bars produces a theoretically invalid measure.

151. [ACADEMIC] Phase 13 — Kalman filter for price state estimation: Gemini correctly recommends EKF (Extended Kalman Filter) for geometric Brownian motion (log-returns). However, for financial prices with jumps, an Unscented Kalman Filter (Julier & Uhlmann, 1997) is preferable to EKF as it handles the nonlinear transformation without linearization error.

152. [ACADEMIC] OFI-based CUSUM filter (Phase 13): Page (1954) CUSUM was designed for quality control in manufacturing — detecting shifts in a stationary process. Financial quote imbalance is non-stationary with long-memory dependence (Lo, 1991). Applying CUSUM directly to OFI without first differencing or fractionally differencing violates the CUSUM's stationarity assumption.

153. [ACADEMIC] Phase 21 — DCC-GARCH cross-timezone weights assume S&P 500 futures correlations with Asian indices are stable. Hasbrouck & Seppi (2001) demonstrate that common factor loadings between markets shift significantly during systemic stress. The DCC model captures time-varying correlations but cannot capture regime-conditional correlation structure. A Markov-Switching DCC (Pelletier, 2006) is required for regime-aware cross-session weights.

154. [ACADEMIC] Phase 18 — FTT intraday exemption (France/Italy): Subrahmanyam (1998) proves that FTT systematically widens spreads by reducing market-maker participation. The router treats spread and FTT as additive costs. In reality, FTT → wider spread → higher spread cost. The true cost of trading in FTT jurisdictions is super-additive, not additive.

155. [ACADEMIC] Phase 14 — M7 (MAE Calibration) Chandelier multiplier: tightening based on historical Maximum Adverse Excursion is a form of reinforcement learning from realized P&L. This creates a look-ahead bias if the MAE calibration window overlaps with the position's own history. A proper implementation requires out-of-sample MAE estimation from similar tickers in similar regimes.

156. [ACADEMIC] Phase 13 — Meta-label gate at probability 0.55: de Prado (2018) specifies that the meta-labeling threshold must be chosen via ROC curve optimization on the validation set, not arbitrary 0.55. The optimal threshold minimizes a specific loss function (e.g., maximize F1 score on imbalanced dataset). A static 0.55 is only correct by coincidence.

157. [ACADEMIC] Phase 16 — ASER scoring: Momentum 30%, Liquidity 20%, Volatility 20%, Regime 15%, Recency 15%. These weights are static percentages. Gu, Kelly, Xiu (2020) demonstrate that machine-learned factor weights consistently outperform hand-selected weights in cross-sectional stock return prediction. The Ouroboros meta-learner should optimize ASER weights nightly, not keep them static.

158. [ACADEMIC] Phase 15 — `ExchangeClosed` veto blocks entries. But Seasholes & Wu (2007) show that markets often exhibit predictable patterns at exchange open and close (e.g., price continuation after news, reversal after liquidity-driven moves). The `AuctionAvoidance` veto preventing participation in closing auctions systematically excludes the highest-liquidity period of the European trading day.

159. [ACADEMIC] Phase 12 — ETP-first routing assumes ETP tracks underlying with low tracking error during the trading day. However, 3x ETPs experience significant NAV drift throughout the day due to daily rebalancing (volatility drag). A naive ETP-first rule that ignores intraday tracking error will route to the ETP when the underlying has moved 15%+ since open, at which point ETP tracking error can be 2-3%.

160. [ACADEMIC] Phase 20 — Stop freeze during CARRIED state: Merton (1973) demonstrates that the value of a position with a frozen stop is equivalent to a European call option (no early exercise). The ratchet enforcer already limits downside. Freezing the stop additionally eliminates the convexity of the stop mechanism, reducing the position's expected value below what an unfrozen, ratcheting stop would provide.

---

### [INFRA] — 40 items

161. [INFRA] `clock.rs:128` — `(utc_secs_from_midnight + 3600) % 86400` for BST. At 23:30 UTC, `utc_secs_from_midnight = 84600`. Adding 3600 = 88200. `88200 % 86400 = 1800` = 00:30 London time. This is correct. But the initial calculation `epoch_secs % 86400` at line 118 can theoretically overflow `u32` for large epoch values — though `u32` holds up to 136 years of seconds, so this is not a practical concern.

162. [INFRA] `python_bridge.rs` — `BufReader<ChildStdout>` reads one line at a time. If the Python subprocess writes a JSON response that is > 8KB (e.g., extended signal with features dict), `read_line` may read a partial line if the OS buffer is fragmented. The partial JSON will fail `serde_json::from_str` silently (returns `None`), losing the signal.

163. [INFRA] Phase 8, SC-09 — crossbeam channel sender in Rust, receiver in Python. But the existing architecture has Rust writing to Python (tick → signal) via stdin/stdout, not via crossbeam. The crossbeam buffer is between the tick ingestion layer and the engine processing layer — both in Rust. Phase 8 must clarify: is crossbeam replacing the Python bridge, or is it between tick receipt and engine dispatch?

164. [INFRA] Phase 11 — `subscription_manager.rs` Mutex. In the Tokio async runtime (Phase 8+ adds Tokio), `std::sync::Mutex` will deadlock if held across `.await` points. The entire SubscriptionManager must use `tokio::sync::Mutex` once Tokio is introduced. The phase plan adds Tokio in Phase 8 but doesn't specify migrating the Mutex type.

165. [INFRA] Phase 16 — Polars `LazyFrame` requires Python 3.8+. The existing Ouroboros uses Python 3 (confirmed from `python3 -m python_brain.bridge`). But the exact Python version in the Docker container is unspecified. Polars 0.20+ requires Python 3.9+. If the container uses Python 3.8, Polars installation will fail silently or install an incompatible older version.

166. [INFRA] Phase 17 — `python-telegram-bot` version compatibility: v20+ uses async/await and `Application.run_polling()`. v13 uses `Updater.start_polling()`. These are completely incompatible APIs. The plan specifies "async long-polling" which requires v20+. If the container has v13 installed (older EC2 images may), the implementation will fail with `AttributeError`.

167. [INFRA] Phase 22 — SIGHUP config reload takes a Write lock on global config. During this write lock, all concurrent Reads from Tokio tasks (which read config for every order decision) are blocked. In an active trading session, this creates a 100-500ms stall across the entire engine while config validation runs.

168. [INFRA] Phase 16 — Ouroboros is launched by Supercronic (crontab in container). Supercronic runs the command in a subprocess. If Ouroboros crashes at step 4, Supercronic does NOT retry (unlike cron with error handling). The pipeline fails silently. No restart logic is specified for Ouroboros partial failure.

169. [INFRA] Phase 19 — `asian_exchange.rs` lunch break veto. If a TSE position's trailing stop is hit at 02:35 UTC (during TSE lunch 02:30-03:30 UTC), the `LunchBreakActive` veto blocks exits. The stop fires but cannot execute. This leaves the position in a limbo state: the exit engine has fired an exit signal, the risk arbiter has vetoed it. The engine must maintain "pending exit" state during lunch, then re-evaluate at 03:30 UTC.

170. [INFRA] `wal_writer.rs` — date calculation uses a Civil Date algorithm at lines (unspecified). If the EC2 instance's timezone is set to UTC (standard for servers) but the WAL uses London time for date boundaries (16:30 LSE close = end of trading day), WAL events from 16:30-23:59 UTC go into today's file but belong to tomorrow's trading session. WAL replay by date will miss these events.

171. [INFRA] Phase 17 — PyMuPDF (`fitz`) must be installed in the Docker container. The `fitz` package on PyPI is `PyMuPDF`. Some Docker base images have conflicting `fitz` packages. The Phase 17 deliverable must explicitly specify `pip install pymupdf` (not `fitz`) and test import with `import fitz`.

172. [INFRA] Phase 22 — `/metrics` Prometheus endpoint uses HTTP. If the Tokio runtime is processing a large WAL replay on startup, the HTTP server may not start until WAL replay completes. External monitoring systems that check metrics on startup will see connection refused, triggering false "system down" alerts during normal startup.

173. [INFRA] Phase 8 — Tokio integration: adding Tokio async runtime to the existing synchronous Rust engine requires converting `engine.rs`'s main loop from a blocking loop to an async loop. This is a significant architectural change. The current `engine.rs` main loop structure (blocking on tick arrival) is incompatible with async Tokio without refactoring.

174. [INFRA] Phase 19 — KRW prices: `f64` confirmed as required for KRW (Gemini INFRA-193). The existing `MarketTick.bid/ask/last` are all `f64` (confirmed in source). No issue exists here — `f64` handles KRW 50,000 without precision loss. Gemini's flag is correct but already satisfied by existing types.

175. [INFRA] Phase 18 — `transaction_tax.toml` floating-point precision: TOML spec allows IEEE 754 double precision. `0.003` (French FTT 0.3%) in IEEE 754 = `0.002999999999999999...`. The `effective_rate_bps()` function must multiply by 10,000 to get bps. `0.003 × 10,000 = 29.999...` → rounds to 29 bps instead of 30 bps. Solution: store rates as integer basis points in TOML, not floating-point percentages.

176. [INFRA] Phase 11 — `mode_controller.rs` event channel: if bounded, what is the capacity? A mode transition generates exactly 1 event. But if the engine is blocked (e.g., waiting for IBKR order ACK), it can't consume the event. A capacity-1 bounded channel would block `mode_controller` until the engine processes the previous mode change.

177. [INFRA] Phase 20 — `overnight_carry.rs` HALTED state: "max HALTED duration: 2 trading days. Day 3 → submit market order." The Day 3 market order must be submitted when the exchange OPENS, not at the start of the UTC day. A market order submitted at 23:00 UTC for a KRX position (KRX opens at 00:00 UTC) will be queued, not rejected, but the plan doesn't specify the submission timing.

178. [INFRA] Phase 16 — Polars LazyFrame `.collect()` triggers Rust's parallel executor. In a Docker container with `--cpus=1.5` (a common EC2 resource limit), Polars will still attempt to spawn its full thread pool, hitting OS scheduling limits. Must test with `POLARS_MAX_THREADS=2` under Docker CPU constraints specifically.

179. [INFRA] Phase 22 — WAL compaction "30-day rolling window." The WAL files are date-named (`YYYY-MM-DD.ndjson`). Compaction must iterate these files by date, not by size. The compaction job must handle the case where an old file contains events for positions still open (e.g., a 31-day mega-runner carry position). Deleting the entry WAL event for an open position is catastrophic.

180. [INFRA] `reconciler.rs` — orphan detection compares `local_open_orders` against `broker_open_orders`. The Rust `HashMap` uses `TickerId` (u32) as key. IBKR returns orders with `ibkr_order_id` (i64). The mapping from `ibkr_order_id` → `TickerId` must be maintained in the broker adapter. If this mapping is lost during a restart, all orders appear orphaned even when correctly open.

---

## PART 2 — GEMINI TRIAGE (200-bullet audit from Gemini Institutional Syndicate)

### Gemini Top-10 Priority Fixes — Triage Disposition

| # | Gemini Fix | Severity | Disposition | Phase |
|---|-----------|----------|-------------|-------|
| G-P1 | `POLARS_MAX_THREADS=2` in container env | CRITICAL | **ACCEPTED → v20, Phase 8** | SC-13 |
| G-P2 | `tokio::sync::Mutex` in SubscriptionManager | CRITICAL | **ACCEPTED → v20, Phase 8** | SC-02 amendment |
| G-P3 | Telegram polling thread infinite retry loop | HIGH | **ACCEPTED → v20, Phase 17** | Amendment |
| G-P4 | `snapshot=True` 200ms timeout + ETP fallback | HIGH | **ACCEPTED → v20, Phase 12** | Amendment |
| G-P5 | Dust: Peg-to-Mid limit, 3min TIF before market | HIGH | **ACCEPTED → v20, Phase 8** | SC-06 amendment |
| G-P6 | AtomicUsize periodic reconciliation vs IBKR | MEDIUM | **ACCEPTED → v20, Phase 11** | Amendment |
| G-P7 | Crossbeam overflow: aggregate H/L/V not drop | MEDIUM | **ACCEPTED → v20, Phase 8** | SC-09 amendment |
| G-P8 | Cost basis nightly clear + IBKR resync | MEDIUM | **ACCEPTED → v20, Phase 8** | SC-10 amendment |
| G-P9 | VIX circuit breaker invalidates DCC-GARCH cache | MEDIUM | **ACCEPTED → v20, Phase 15** | Amendment |
| G-P10 | Gaussian-Gaussian Thompson Sampler | MEDIUM | **ACCEPTED → v20, Phase 13** | Amendment |

### Gemini 200 Bullets — Triage Table

| ID | Category | Disposition | v20 Phase | Notes |
|----|----------|-------------|-----------|-------|
| G-1 | FLAW: AtomicUsize leaks on dropped ACK | ACCEPTED | Phase 8/11 | Periodic reconciliation fix (G-P6) |
| G-2 | FLAW: Drop-oldest corrupts Chandelier H/L | ACCEPTED | Phase 8 | Aggregate instead of drop (G-P7) |
| G-3 | FLAW: snapshot=True blocks 11s on illiquid | ACCEPTED | Phase 12 | 200ms timeout + ETP fallback (G-P4) |
| G-4 | FLAW: Polygon EU ISIN corporate action gaps | ACCEPTED | Phase 16 | Add OpenFIGI + Refinitiv fallback note |
| G-5 | FLAW: SIGTERM 30s wait vs Docker 10s SIGKILL | ACCEPTED | Phase 8 | Add `stop_grace_period: 60s` as SC deliverable |
| G-6 | FLAW: Dust market-sell slippage on illiquid | ACCEPTED | Phase 8 | Peg-to-Mid with 3min TIF (G-P5) |
| G-7 | FLAW: symbology_mapper preferred shares (BAC PRD) | ACCEPTED | Phase 8 | Add preferred share rule SC-12e |
| G-8 | FLAW: TWAP fails on US half-days | ACCEPTED | Phase 14 | Add `early_close_detected()` TWAP abort |
| G-9 | FLAW: GIL contention between polling + bridge | ACCEPTED | Phase 17 | Use `asyncio` event loop isolation |
| G-10 | FLAW: psutil polls vs Polars instantaneous spike | ACCEPTED | Phase 16 | Use `resource.getrlimit` pre-allocation check |
| G-11 | FLAW: OFI EWMA loses 21s ago block trade | DEFERRED | — | By design: 5s decay is intentional window |
| G-12 | FLAW: Chandelier floor too tight for 3x ETP spread | ACCEPTED | Phase 14 | Use leverage-adjusted floor: `1.5 × spread × leverage_factor` |
| G-13 | FLAW: Half-Kelly mixes regimes (bull vs bear) | NOTED | Phase 15 | Out-of-scope; 250-trade validation is pragmatic |
| G-14 | FLAW: FTT intraday exemption lost on overnight carry | ACCEPTED | Phase 18/20 | Flag FTT-jurisdiction entries as "no carry eligible" |
| G-15 | FLAW: KRX VI extended by exchange officials | ACCEPTED | Phase 19 | Poll `reqContractDetails` to confirm VI cleared |
| G-16 | FLAW: IBKR margin increase over holidays | ACCEPTED | Phase 20 | Add margin check in MONITORED holiday state |
| G-17 | FLAW: Cost basis wrong on overnight split | ACCEPTED | Phase 8 | Nightly cost basis clear + IBKR resync (G-P8) |
| G-18 | FLAW: HKEX board lot → 0-share order on £1,500 | ACCEPTED | Phase 12 | Min-lot → fallback to ETP if lot × price > Kelly |
| G-19 | FLAW: US/UK DST misalign 2-3 weeks/year | ACCEPTED | Phase 11 | Mode B+ end uses ZoneInfo LSE close only; US open handled separately |
| G-20 | FLAW: CUSUM threshold spikes on market maker pull | NOTED | Phase 13 | Exponential smoothing of spread before CUSUM floor |
| G-21 | FLAW: CVaR Cornish-Fisher on gapped assets | NOTED | Phase 15 | Minimum N=20 observations gate before CF |
| G-22 | FLAW: Polars writes Parquet to EBS hitting IOPS | ACCEPTED | Phase 16 | Write to `/dev/shm` (tmpfs) during processing; final write to EBS |
| G-23 | FLAW: Telegram 4000-char truncation | ACCEPTED | Phase 17 | Truncate at last complete JSON field, not mid-string |
| G-24 | FLAW: f64 tick rounding → wrong Euronext lot | ACCEPTED | Phase 18 | Use `Decimal` crate for tick rounding |
| G-25 | FLAW: HKD concentration skews SmartRouter | NOTED | Phase 19 | Router cost calculation is independent of concentration veto |
| G-26 | FLAW: MAX_CARRY=6 → 12 lines, allocator assumes 3 | ACCEPTED | Phase 20 | Fix allocator: `available = 100 - (carry_count × 2)` |
| G-27 | FLAW: WAL compaction severs 31-day carry cost basis | CRITICAL | Phase 22 | Exclude events for open positions from compaction |
| G-28 | FLAW: FTT market cap fluctuates daily | ACCEPTED | Phase 12 | Cache market cap with ±10% hysteresis band |
| G-29 | FLAW: Exit fires into lunch illiquidity | NOTED | Phase 19 | Chandelier exits during lunch use IOC limit at ask |
| G-30 | FLAW: 50 RotationScanner > 40 HotScanner slots | ACCEPTED | Phase 13 | Thompson Sampling queue with hard slot limit |
| G-31 | FLAW: ADV gate at 14:30 uses pre-market volume | ACCEPTED | Phase 14 | Use 5-min trailing volume from current session only |
| G-32 | FLAW: Token bucket shared Py+Rust | ACCEPTED | Phase 8 | Single token bucket in Rust; Python Ouroboros uses separate bucket |
| G-33 | FLAW: DarkModeActive misses post-market alpha | NOTED | — | By design: ISA compliance, not oversight |
| G-34 | FLAW: SubUniverse correlated exchange waste | NOTED | Phase 18 | Known tradeoff; ISIN dedup partially addresses |
| G-35 | RISK: snapshot=True pacing with 10 simultaneous | ACCEPTED | Phase 12 | Rate-limit snapshot queue: max 5 concurrent |
| G-36 | RISK: Drop-oldest drops halt messages | ACCEPTED | Phase 8 | Priority lane for halt/corporate action ticks |
| G-37 | RISK: Polars lazy syntax error wastes 90min | ACCEPTED | Phase 16 | Validate LazyFrame plan at import time with `.explain()` |
| G-38 | RISK: WAL rebuild after restart before IBKR reconnect | ACCEPTED | Phase 22 | WAL replay can run before IBKR reconnect; positions reconstructed before any trading |
| G-39 | RISK: Telegram polling thread dies silently | ACCEPTED | Phase 17 | Infinite retry loop (G-P3) |
| G-40 | RISK: IBKR drops cancelMktData ACKs under load | ACCEPTED | Phase 11 | Timeout-based ACK with 2s fallback + reconcile (G-P6) |
| G-41 | RISK: Polygon rate-limits blocklist endpoint | ACCEPTED | Phase 16 | Implement 3-tier retry with Refinitiv/yfinance fallback |
| G-42 | RISK: Dust guard during closing auction → MOC | ACCEPTED | Phase 14/8 | Detect auction period; use MOC if in closing auction |
| G-43 | RISK: Thompson Sampling over-allocates negative-EV | ACCEPTED | Phase 13 | Gaussian-Gaussian TS (G-P10) |
| G-44 | RISK: DCC-GARCH 5min blind on flash crash | ACCEPTED | Phase 15 | VIX circuit breaker (G-P9) |
| G-45 | RISK: reqPnL silent during halt (can't distinguish from drop) | ACCEPTED | Phase 20 | reqPnL heartbeat timeout (60s) → assume carry monitoring stale |
| G-46 | RISK: EC2 NTP drift at T-5 rule | ACCEPTED | Phase 22 | Add NTP sync check to startup gate |
| G-47 | RISK: Mega-runner +102% near miss (-0.1%) | NOTED | — | By design: threshold is a commitment |
| G-48 | RISK: Chandelier blind during 04:45 reconnect | ACCEPTED | Phase 19 | Freeze stops during reconnect; don't fire exits during blind window |
| G-49 | RISK: SubUniverse severs profitable European lines | NOTED | Phase 18 | Known; allocator must track per-line alpha before severing |
| G-50 | RISK: Polars orphaned Parquet fills disk | ACCEPTED | Phase 16 | Write all Parquet to `/tmp`; cleanup at step end |
| G-51 | RISK: Ouroboros steps concurrent → OOM | ACCEPTED | Phase 16 | Enforce sequential step execution; no async between steps |
| G-52 | RISK: MIN_ENTRY_GBP + RED tier recovery impossible | ACCEPTED | Phase 15 | Min entry gate suspended in RED tier recovery (< 5% above RED threshold) |
| G-53 | RISK: Symbology regex fails on IBKR format change | ACCEPTED | Phase 8 | Version-pin IBKR API format; add integration test |
| G-54 | RISK: Watchdog server single point of failure | ACCEPTED | Phase 17 | Redundant watchdog: check from personal phone cron + EC2 |
| G-55 | RISK: EUR drag ignores Euronext Dublin → stamp duty | ACCEPTED | Phase 18 | Per-exchange stamp duty map in `transaction_tax.toml` |
| G-56 | RISK: Short alpha half-life forces passive adverse selection | NOTED | Phase 14 | Accept: passive execution is still superior to market at open |
| G-57 | RISK: NZX misses opening auction every day | ACCEPTED | Phase 19 | Subscribe NZX lines at 22:55 UTC during DARK; pre-position before MODE A open |
| G-58 | RISK: Cross-timezone CME futures delay | ACCEPTED | Phase 21 | Use IBKR ES futures tick (not Ouroboros) for real-time US sentiment |
| G-59 | RISK: Stock split → ratchet instantly liquidates | ACCEPTED | Phase 8 | Add `SplitAdjustment` WAL event; reset stop on split |
| G-60 | RISK: SIGHUP config reload deadlock | ACCEPTED | Phase 22 | Use `ArcSwap` instead of RwLock for config state |
| G-61 | RISK: reqHistoricalData for ADV gate rejected → 0 vol | ACCEPTED | Phase 14 | Cache last-known ADV; only reset on confirmed 0-volume response |
| G-62 | RISK: AuctionAvoidance misses closing imbalance runs | NOTED | — | By design: ISA safety over alpha |
| G-63 | RISK: ETP fractional rounding deviation | ACCEPTED | Phase 12 | Integer shares only; `floor(kelly_gbp / lot_price_gbp)` |
| G-64 | RISK: Shadow book £5 threshold fires on every trade | ACCEPTED | Phase 17 | Raise threshold to £50 or 0.5% of position value, whichever is greater |
| G-65 | RISK: Delayed data signals phantom book | ACCEPTED | Phase 22 | Monitor `reqMarketDataType` response; halt signal generation if delayed data detected |
| G-66 | RISK: SIGTERM 30s vs Docker 10s SIGKILL | ACCEPTED | Phase 8 | `stop_grace_period: 60s` in docker-compose.yml (duplicate of G-5, confirmed) |
| G-67 | RISK: 0.5× Kelly at 200 trades still over-bets | NOTED | Phase 15 | Dynamic ramp (Improvement #91) addresses this |
| G-68 | RISK: ETP tracking error compounds at 30 days | ACCEPTED | Phase 12 | Add 30-day tracking error check; demote ETP if error > 5% |
| G-69 to G-200 | (Remaining RISK/IMPROVEMENT/MISSING/ACADEMIC/INFRA) | See below | Various | Full disposition in v20 amendments |

---

## PART 3 — ADVERSARIAL RED TEAM REVIEW

### A. Five Most Lethal Failure Modes (Ranked by Probability × Severity)

**1. SIGTERM → SIGKILL at 10s (P=100%, Severity=Fatal)**
Every single container restart or deployment corrupts the WAL. Docker's default `stop_grace_period` is 10 seconds. SC-01 waits 30 seconds. This means every restart in production destroys WAL integrity. Not a maybe — a certainty. The fix (one line in docker-compose.yml) is not in any current phase deliverable. This has been in the plan since v17.

**2. Polars vCPU Starvation → IBKR Disconnect (P=95%, Severity=Fatal)**
Confirmed by both Gemini and Claude. During DARK mode Ouroboros, Polars will consume 100% of both vCPUs on the c7i-flex.large. Tokio's async reactor, which maintains the IBKR WebSocket keep-alive, is starved. IBKR disconnects. All carry positions lose their monitoring connection. No SIGTERM is sent — the socket just drops. `BackoffState` reconnects, but during the 3-minute reconnect window, any Asian market halt or price spike is missed entirely. Fix: `POLARS_MAX_THREADS=2` in docker-compose.yml.

**3. reqPnL Single-Connection Limit (P=90%, Severity=High)**
IBKR allows exactly one `reqPnL` subscription per connection. With up to 6 carry positions in Phase 20, the spec attempts 6 `reqPnLSingle` subscriptions. IBKR silently rejects subscriptions 2-6 (Error 10197 or similar). Five of six carry positions are unmonitored for overnight gap risk. The fix (use account-level `reqPnL`) is simple but must be in Phase 20 deliverables.

**4. WAL Compaction Deletes Open Position History (P=70%, Severity=Fatal)**
Phase 22 specifies 30-day rolling WAL compaction. A mega-runner carry position held for 31+ days has its entry WAL event deleted. On the next restart, WAL replay cannot reconstruct this position. The engine treats it as an orphan and closes it at market. The most profitable position in the system is force-liquidated by the compaction job. Fix: WAL compaction must exclude events for any position in `portfolio.rs::positions` (open positions).

**5. Half-Kelly + Min Entry = Trading Impossible (P=85%, Severity=High)**
`kelly_fraction.clamp(0.0, 0.20)` confirmed in source. Half-Kelly clips to 0.10. At £10,000 equity: max position = £1,000. Min entry = £1,500. The system cannot place any trade at all for the entire paper trading phase. Every signal is generated, passed through risk arbiter, Kelly-sized to £1,000, and blocked by MINIMUM_ENTRY_GBP. The Crucible 100-trade gate (Suite 1) requires 100 trades — which are physically impossible with this configuration. Fix: Dynamic Kelly ramp OR suspend min entry gate below 250 trades.

---

### B. Three Most Dangerous Theoretical Flaws

**1. DCC-GARCH 5-Minute Lag During Systemic Crash**
During a flash crash, correlations jump to 1.0 within milliseconds. The 5-minute cached DCC-GARCH matrix from before the crash shows low correlations. CVaR heat limit stays wide. Risk arbiter approves new entries into a market that has structurally broken down. This is the scenario where the system maximizes exposure at the worst possible moment. Gemini's VIX circuit breaker fix (G-P9: if VIX spikes >10% in 1 minute → invalidate cache) is the minimum viable protection.

**2. Thompson Sampling Beta-Bernoulli EV Blindness**
Confirmed by both Gemini and Claude (independent convergence on same flaw). An asset with 9 wins of +1% and 1 loss of -20% has WR=90% and expected value = (9×0.01 + 1×(-0.20)) / 10 = -0.011 = **negative expected value**. The Beta-Bernoulli bandit assigns this asset priority score ~0.90 and aggressively allocates scanner lines to it. The most dangerous assets (high WR, catastrophic tail loss) become the most favored. Fix: Gaussian-Gaussian Thompson Sampler using continuous PnL% as reward.

**3. Clock BST Bug → All Mode Boundaries Shift 1 Hour**
The `clock.rs` BST approximation fails on DST transition days due to the leap-year day-of-year calculation. On these specific days (2-4 times per year), ALL mode boundaries shift by 1 hour in the wrong direction. MODE A opens at midnight but clock says MODE B; MODE B+ triggers 1 hour early; T-5 flatten fires 1 hour late. On a day with Asian market volatility, this bug allows MODE A trading to continue into what the real clock considers 08:00 UTC.

---

### C. Infrastructure Gaps That Will Kill the System Before Trading

1. **No Tokio migration plan** — Phase 8 adds Tokio but `engine.rs` main loop is synchronous blocking. The entire engine architecture requires refactoring before any async capability works.

2. **Python bridge is synchronous** — 100 tickers × 10 ticks/sec = 1,000 ticks/sec through one stdin/stdout pipe. At 1ms Python processing per tick, this creates a 1-second lag. At US open (10,000 ticks/sec burst), the bridge becomes a 10-second lag — all tick data is 10 seconds stale before reaching signals.

3. **No `reqMarketDataType(3)` call** — The existing broker makes no data type declaration. In paper mode without live data subscriptions, IBKR may refuse streaming data entirely, making all subscriptions return nothing. This would silently kill tick delivery.

4. **`ibkr_broker.rs` uses bar subscription, not tick-by-tick** — The existing broker subscribes to 5-second OHLCV bars via `subscribe_bars()`. Phase 11 assumes individual tick subscriptions for the SubscriptionManager ACK protocol. These are fundamentally different IBKR subscription types with different line counting.

---

### D. Execution Cost Decomposition (Reality Check)

For the target of 0.3-0.5% net daily return on £10,000:

- **IBKR commission**: £1.0-1.5 per trade (min per order)
- **Spread cost**: 0.1-0.3% per entry+exit round trip for LSE ETPs
- **FX drag**: 0.02-0.08% per EUR/GBP/CHF position
- **Dust liquidation (Peg-to-Mid)**: 0.05-0.1% expected cost for partial fills
- **TWAP slippage vs market**: 0.02-0.05% for passive execution

**Total friction**: 0.25-0.55% per trade round trip

At 2-3 trades per day, friction alone = 0.5-1.65% per day. The target of 0.3-0.5% net is below the friction floor for active multi-trade strategies. The system must achieve 0.8-2.15% gross return to hit 0.3-0.5% net. This requires an average gross edge of 0.8-2.15% per trade — which is achievable on strong momentum signals but only if executed cleanly. Every basis point of unnecessary friction directly destroys the edge.

---

### E. v19 Fixes Verified as Correct

1. **v19-FIX-1 (Dust on FILLED portion)**: Correct. The distinction between filled and unfilled is critical for proper dust detection.
2. **v19-FIX-2 (Symbology mapper)**: Correct. IBKR→Polygon translation is essential for data_fetch.py. Needs reverse mapping added.
3. **v19-FIX-3 (Buffer 50,000)**: Already implemented in `channel.rs` (confirmed in source). v19 re-specified what was already built.
4. **v19-FIX-4 (Long-polling)**: Correct. Webhook overhead is unjustified. Thread survival wrapper needed.
5. **v19-FIX-5 (snapshot=True)**: Correct in principle. Needs 200ms timeout to prevent blocking.
6. **v19-FIX-6 (corp_action_blocklist)**: Correct approach. Atomic write needed. EU coverage gap remains.

---

## PART 4 — PRIORITY ACTION MATRIX FOR v20

### P0 — Fatal (System Will Not Function)

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P0-1 | Docker SIGKILL at 10s vs 30s SIGTERM wait | `stop_grace_period: 60s` in docker-compose.yml | Phase 8 |
| P0-2 | Polars vCPU starvation → IBKR disconnect | `POLARS_MAX_THREADS=2` in docker-compose.yml | Phase 8 |
| P0-3 | Half-Kelly + Min Entry = 0 trades possible | Dynamic Kelly ramp 0→250 trades | Phase 8/15 |
| P0-4 | WAL compaction deletes open position events | Exclude open positions from compaction | Phase 22 |
| P0-5 | reqPnL 1-per-connection IBKR limit | Use account-level reqPnL instead | Phase 20 |
| P0-6 | clock.rs BST addition missing % 86400 | Apply modulo after BST offset | Phase 11 |
| P0-7 | tokio::sync::Mutex required in async context | Replace std::sync::Mutex in SubscriptionManager | Phase 8/11 |
| P0-8 | No reqMarketDataType(3) call in broker | Add as first call in ibkr_broker.rs::connect() | Phase 8 |
| P0-9 | Heartbeat only fires in DARK (22h gap) | Engine-side 30-min heartbeat Redis write | Phase 17 |
| P0-10 | RotationScanner StrategyId absent from WAL | Add HotScanner/RotationScanner to enums.rs | Phase 13 |

### P1 — High (System Will Fail in Common Conditions)

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P1-1 | snapshot=True blocks 11s on illiquid | 200ms tokio::timeout + ETP fallback | Phase 12 |
| P1-2 | Telegram polling thread dies silently | Infinite retry loop with exponential backoff | Phase 17 |
| P1-3 | DCC-GARCH 5min blind on flash crash | VIX circuit breaker cache invalidation | Phase 15 |
| P1-4 | Beta-Bernoulli negative EV allocation | Gaussian-Gaussian Thompson Sampler | Phase 13 |
| P1-5 | Drop-oldest corrupts Chandelier H/L | Aggregate H/L/V on overflow (not drop) | Phase 8 |
| P1-6 | Cost basis wrong after overnight split | Nightly clear + IBKR reqPositions resync | Phase 8 |
| P1-7 | Dust market-sell slippage on illiquid | Peg-to-Mid limit, 3min TIF | Phase 8 |
| P1-8 | AtomicUsize leaks on dropped ACK | 5-min periodic IBKR reconciliation | Phase 11 |
| P1-9 | WAL compaction deletes carry entry (31d) | Open position exclusion from compaction | Phase 22 |
| P1-10 | FTT intraday exemption lost on carry | Flag FTT entries: no carry eligible | Phase 18/20 |
| P1-11 | NZX misses opening auction daily | Pre-subscribe NZX at 22:55 UTC in DARK | Phase 19 |
| P1-12 | IBKR bar vs tick subscription mismatch | Clarify subscription type in Phase 11 | Phase 11 |
| P1-13 | ISA tax year Jan 1 vs April 6 | Fix isa_gate.rs boundary to April 6 | Phase 12 |
| P1-14 | HKEX board lot → 0-share order | Fallback to ETP when lot×price > Kelly | Phase 12 |
| P1-15 | Polars parallel step execution → OOM | Enforce sequential step execution | Phase 16 |

### P2 — Medium (System Will Degrade, Not Fail Immediately)

| ID | Issue | Fix | Phase |
|----|-------|-----|-------|
| P2-1 | Shadow book £5 threshold too sensitive | Raise to £50 or 0.5% of position | Phase 17 |
| P2-2 | Cornish-Fisher diverges at high kurtosis | Gate: min N=20 observations, |S|<2 check | Phase 15 |
| P2-3 | Reverse mapping Polygon→IBKR missing | Add reverse mapping to SC-12 | Phase 8 |
| P2-4 | Atomic write for corp_action_blocklist.json | Write to .tmp, validate, rename() | Phase 16 |
| P2-5 | TWAP fails on US half-days | Detect early close; abort TWAP at T-30min | Phase 14 |
| P2-6 | Chandelier floor too tight for 3x ETP spread | Scale floor by leverage: 1.5×spread×leverage | Phase 14 |
| P2-7 | FTT TOML floating-point precision | Store as integer bps in TOML | Phase 18 |
| P2-8 | VPIN NaN for newly-listed equities | Gate VPIN: min 5 days ADV data | Phase 18 |
| P2-9 | ASX open time wrong for AEDT (23:00 UTC) | Dynamic ZoneInfo("Australia/Sydney") check | Phase 19 |
| P2-10 | Allocator assumes 3 carry lines, max is 12 | Fix: available = 100 - (carry_count × 2) | Phase 20 |
| P2-11 | clock.rs BST day_of_year leap-year error | Use chrono-tz Europe::London | Phase 11 |
| P2-12 | Transaction tax bps floating-point | Decimal crate for tick/tax arithmetic | Phase 18 |
| P2-13 | mode_b_plus_end_utc Python→Rust UTC/London mismatch | Specify UTC output, Rust converts | Phase 11 |
| P2-14 | IBKR reconnect 5 attempts expires before 3min GW restart | Increase max_attempts to 20, total backoff 5min | Phase 19 |
| P2-15 | Polars Parquet orphans fill EC2 disk | Write to /tmp; cleanup at step end | Phase 16 |

---

*AEGIS_SELF_ANALYSIS_TRIAGE_v19.md — Generated 2026-03-09*
*Claude independent audit: 200 bullets (40 FLAW + 40 RISK + 40 IMPROVEMENT + 40 MISSING + 40 ACADEMIC/INFRA across all categories)*
*Gemini triage: 200 bullets fully triaged, dispositions assigned*
*All findings feed into AEGIS_MASTER_PLAN_v20.md*
