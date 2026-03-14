# 02 — STATE MACHINE
# AEGIS V2 — Order Lifecycle State Machine
# Version: 1.0 | Status: SPEC LOCK
# Every order follows this exact state machine. No shortcuts.
# Invalid state transitions are compile-time errors (Rust typestate, H83).

---

## STATE DIAGRAM (ASCII)

```
                    ┌─────────────────────┐
                    │  INTENT_GENERATED    │  ← Python outputs OrderIntent
                    │  (Python Brain)      │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  RISK_CHECKED        │  ← Executioner RiskArbiter
                    │  (synchronous gate)  │     evaluates ALL canonical rules
                    └──────┬──────┬───────┘
                           │      │
                    APPROVED│      │REJECTED
                           │      │
                           │      ▼
                           │  ┌──────────┐
                           │  │ REJECTED  │  ← Terminal state
                           │  │ (logged)  │     VetoReason recorded
                           │  └──────────┘
                           │
                           ▼
                    ┌─────────────────────┐
                    │  WAL_WRITTEN         │  ← RoutedOrder appended to
                    │  (fsync'd to disk)   │     events/YYYY-MM-DD.ndjson
                    └──────────┬──────────┘     CRC32 checksum + UUIDv7
                               │
                               ▼
                    ┌─────────────────────┐
                    │  SUBMITTED           │  ← Order sent to broker via
                    │  (async broker trait)│     async submit_order()
                    └──────┬──────┬───────┘
                           │      │
                    ACK    │      │NO ACK (5s timeout)
                    received│      │
                           │      ▼
                           │  ┌──────────────┐
                           │  │  ORPHANED     │  ← See ORPHAN RECOVERY below
                           │  │  (5s timeout) │
                           │  └──────────────┘
                           │
                           ├─── IBKR REJECTS ──▶ ┌────────────────┐
                           │                      │ BROKER_REJECTED │ ← Terminal
                           │                      │ (WAL updated)   │    state
                           │                      └────────────────┘
                           ▼
                    ┌─────────────────────┐
                    │  ACKNOWLEDGED        │  ← BrokerAck received with
                    │  (broker confirms)   │     ibkr_order_id assigned
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  PARTIALLY_FILLED    │  ← 0 or more partial fills
                    │  (accumulating)      │     Each fill: update VWAP,
                    └──────────┬──────────┘     adjust stop, update qty
                               │
                               │ remaining_qty == 0
                               ▼
                    ┌─────────────────────┐
                    │  FILLED              │  ← Position fully established
                    │  (complete)          │     Final VWAP entry calculated
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  EXIT_REGISTERED     │  ← Stop-loss + trailing stop
                    │  (exit engine armed) │     registered in Exit Engine
                    └──────────┬──────────┘
                               │
                               │ Exit condition fires
                               ▼
                    ┌─────────────────────┐
                    │  EXIT_TRIGGERED      │  ← Highest-priority exit wins
                    │  (exit signal fired) │     (see EXIT PRIORITY below)
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ EXIT_ORDER_SUBMITTED │  ← Exit order sent to broker
                    │ (sell order out)     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  EXIT_FILLED         │  ← Broker confirms exit fill
                    │  (shares sold)       │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  POSITION_CLOSED     │  ← Terminal state
                    │  (final PnL calc'd)  │     PositionClosed event to WAL
                    └─────────────────────┘
```

---

## STATE DESCRIPTIONS

### INTENT_GENERATED
- **Who creates it:** Python Brain (Vanguard Sniper or Apex Scout)
- **What it contains:** OrderIntent struct with ticker_id, side, confidence, strategy, kelly_fraction, features
- **Transition out:** Immediately to RISK_CHECKED (synchronous, <1ms)
- **Error handling:** If Python raises an exception, Rust catches via BrainError enum (H05). Intent is discarded. Log WARNING.

### RISK_CHECKED
- **Who creates it:** Executioner RiskArbiter
- **What happens:** ALL canonical rules evaluated synchronously:
  - Confidence >= 65?
  - Max positions (filled + pending) < 3?
  - Portfolio heat < 6%?
  - Sector heat < 33%?
  - Cash buffer > 10%?
  - Daily drawdown < 2%?
  - Data fresh (< 120s)?
  - Broker connected?
  - WAL available?
  - ISA safety (no short)?
  - Inverse mutual exclusion?
  - Spread < 0.5%?
  - Time < 15:45 LSE?
  - Not in auction period?
  - Velocity check passed?
  - No gap detected?
  - Risk regime allows entry?
  - Consecutive loss breaker not triggered?
- **Transition out:** APPROVED → WAL_WRITTEN | REJECTED → REJECTED (terminal)
- **Critical:** State is FROZEN during evaluation. No concurrent modifications.

### REJECTED (Terminal)
- **What happens:** VetoReason is logged with specific threshold that was breached (H39). OrderIntent is discarded. No WAL entry for rejections (they didn't happen).
- **No transition out.** Dead end.

### WAL_WRITTEN
- **What happens:** RoutedOrder event is serialized to ndjson, appended to today's journal file, fsync'd. Includes: OrderIntent fields, RiskDecision, UUIDv7 event_id, CRC32 checksum, schema_version.
- **Why this matters:** After this point, we have permanent amnesia-proof memory. If we crash, WAL replay reconstructs that we intended to trade.
- **Transition out:** → SUBMITTED (immediately after fsync confirms)
- **Error handling:** If fsync fails → HALT. Never trade without logging.

### SUBMITTED
- **What happens:** Order is sent to IBKR via `BrokerAdapter::submit_order()`. The UUIDv7 is injected into IBKR OrderRef field (H116).
- **Transition out:**
  - BrokerAck received with status=Accepted → ACKNOWLEDGED
  - BrokerAck received with status=Rejected → BROKER_REJECTED
  - No response within 5 seconds → ORPHANED
- **Critical:** A 5-second timer starts on submission. If it fires before BrokerAck arrives, transition to ORPHANED.

### BROKER_REJECTED (Terminal)
- **What happens:** IBKR refused the order (invalid contract, pacing violation, insufficient funds, etc.). WAL is updated with BrokerAck event (status=Rejected). Position is unchanged.
- **Escalation:** If 3 rejections occur within 1 minute → HALT (H88).
- **No transition out.** Dead end.

### ACKNOWLEDGED
- **What happens:** IBKR accepted the order and assigned an ibkr_order_id. BrokerAck event written to WAL.
- **Transition out:** → PARTIALLY_FILLED (first fill arrives) or → FILLED (if single complete fill)

### ORPHANED
- **What happens:** No BrokerAck within 5 seconds of submission. This is a DANGEROUS state — we sent an order but don't know if IBKR received it.
- **Recovery:** See ORPHAN RECOVERY section below.
- **Critical:** ALL new order submissions are BLOCKED while any orphan exists.

### PARTIALLY_FILLED
- **What happens:** One or more FillEvent arrives but remaining_qty > 0. Each partial fill:
  1. Update filled_qty (cumulative)
  2. Recalculate VWAP entry price: `avg_entry = Σ(price × qty) / Σ(qty)`
  3. Update stop-loss for the filled quantity
  4. Add commission to total_commission (H53)
  5. Write FillEvent to WAL
- **Transition out:** → PARTIALLY_FILLED (more fills) or → FILLED (remaining_qty == 0)
- **Edge case:** A cancel request can be sent while partially filled. If cancel succeeds, remaining_qty is set to 0 and we manage the partial position.

### FILLED
- **What happens:** remaining_qty == 0. Full position is established.
  - Final VWAP entry price is calculated
  - PositionState is created with all fields populated
  - highest_high is set to entry price (initial value)
- **Transition out:** → EXIT_REGISTERED (immediately)

### EXIT_REGISTERED
- **What happens:** The Exit Engine arms all exit conditions for this position:
  1. Hard stop-loss at calculated price
  2. Chandelier trailing stop (starts at Rung 0)
  3. EOD flatten timer (16:25 LSE)
  4. Signal reversal listener
  5. HALT/FLATTEN override listener
- **Transition out:** → EXIT_TRIGGERED (when any exit condition fires)
- **On each tick:** Exit Engine evaluates ALL conditions. If none fire, stay in EXIT_REGISTERED.

### EXIT_TRIGGERED
- **What happens:** An exit condition fired. If multiple fire on same tick, highest ExitPriority wins. Lower-priority exits are logged but suppressed.
- **Priority hierarchy:**
  1. HALT/FLATTEN (market sell, IMMEDIATE)
  2. Hard stop-loss (limit at stop price)
  3. Chandelier trailing stop
  4. EOD flatten (16:25)
  5. Signal reversal
- **Special case:** If HALT fires, ALL pending exits for ALL positions are cancelled and replaced with market sells (ExitOrderType::MarketSell).
- **Transition out:** → EXIT_ORDER_SUBMITTED

### EXIT_ORDER_SUBMITTED
- **What happens:** Exit sell order sent to IBKR. Uses the appropriate ExitOrderType (MarketSell for HALT, MarketToLimit for emergency, LimitAtStop for normal stops).
- **Transition out:** → EXIT_FILLED (broker confirms fill)
- **Error handling:** If exit order is rejected by IBKR, immediately retry with MarketSell. Log CRITICAL.

### EXIT_FILLED
- **What happens:** Broker confirms the exit fill.
  - Calculate final PnL (FIFO accounting, H87)
  - Deduct round-trip commission
  - Update PortfolioState (remove position)
- **Transition out:** → POSITION_CLOSED

### POSITION_CLOSED (Terminal)
- **What happens:** PositionClosed event written to WAL with:
  - Final PnL (realized)
  - Entry timestamp, exit timestamp
  - Total commission paid
  - Exit reason
  - Strategy that originated the trade
- **No transition out.** Dead end.

---

## ORPHAN RECOVERY PROTOCOL

Orphaned orders are the most dangerous failure mode. The system MUST
resolve ALL orphans before resuming normal operation.

### When Orphans Are Created
1. **During live trading:** SUBMITTED → 5 second timeout → ORPHANED
2. **On startup/crash recovery:** WAL replay finds RoutedOrder event with no corresponding BrokerAck, FillEvent, or BROKER_REJECTED event

### Recovery Sequence (executed on broker reconnect or startup)

```
Step 1: DETECT
  - After WAL replay, scan all RoutedOrder events
  - For each RoutedOrder, check for matching BrokerAck OR FillEvent OR BROKER_REJECTED
  - Any unmatched RoutedOrder → mark as ORPHANED
  - Log: "ORPHAN DETECTED: order_id={}, ticker={}, submitted_at={}"

Step 2: QUERY BROKER
  - Call reqOpenOrders() → get all live IBKR orders
  - Call reqPositions() → get all live IBKR positions
  - Match using OrderRef field (contains our UUIDv7, H116)

Step 3: DIFF AND RESOLVE
  Case A: IBKR shows the order was FILLED
    → Synthesize FillEvent from IBKR data
    → Append FillEvent to WAL
    → Register stop-loss in Exit Engine
    → Log CRITICAL: "ORPHAN RESOLVED: phantom fill detected"
    → Transition: ORPHANED → FILLED → EXIT_REGISTERED

  Case B: IBKR shows the order is OPEN (not yet filled)
    → Decide: cancel it (safe) or let it ride (risky)
    → Default: CANCEL the orphaned order
    → Wait for Cancelled ack
    → Append OrphanResolved(cancelled) to WAL
    → Log WARNING: "ORPHAN RESOLVED: cancelled open order"

  Case C: IBKR shows NO record of the order
    → Order was never received by IBKR (network failure)
    → Append OrphanResolved(never_received) to WAL
    → Log INFO: "ORPHAN RESOLVED: order never reached broker"

  Case D: IBKR shows the order was CANCELLED
    → Already handled by IBKR
    → Append OrphanResolved(already_cancelled) to WAL
    → Log INFO: "ORPHAN RESOLVED: already cancelled by IBKR"

Step 4: GATE
  - Count remaining orphans
  - If orphan_count > 0 → REPEAT Step 2-3
  - If orphan_count == 0 → UNBLOCK new order submissions
  - Log: "ALL ORPHANS RESOLVED. Trading resuming."
```

### Invariants
- **BLOCKING:** No new orders may be submitted while orphan_count > 0
- **IDEMPOTENT:** Running orphan resolution twice produces the same state
- **LOGGED:** Every resolution decision is written to WAL

---

## PHANTOM FILL HANDLING (H55)

A phantom fill occurs when:
1. We send a cancel request for an order
2. IBKR receives the cancel
3. But a fill crossed in the network 50ms before the cancel arrived
4. We receive both: Cancelled ack AND a FillEvent

**Resolution:**
- The FillEvent takes precedence. We now own the shares.
- Accept the position. Register it in Exit Engine.
- Log CRITICAL: "PHANTOM FILL: received fill after cancel for order_id={}"
- The Cancelled ack is ignored for that specific order.

---

## PARTIAL FILL ACCUMULATION

Multiple FillEvents for a single order are expected (H124). A
1,000-share order might generate 100 individual 10-share fills.

### Per-Fill Update Sequence:
```
1. Receive FillEvent { filled_qty: 10, remaining_qty: 990, price: 10.50 }
2. Update cumulative:
   - total_filled += 10 → 10
   - vwap = (10.50 * 10) / 10 = 10.50
3. Register/update stop-loss for 10 shares at entry
4. Add commission to total_commission

... more fills ...

50. Receive FillEvent { filled_qty: 10, remaining_qty: 0, price: 10.55 }
51. Update cumulative:
   - total_filled += 10 → 1000
   - vwap = Σ(price_i * qty_i) / 1000 = 10.523
52. remaining_qty == 0 → Transition to FILLED
53. PositionState fully populated with final VWAP
```

### Deduplication:
- Each FillEvent has a unique exec_id from IBKR
- If we receive the same exec_id twice (duplicate), skip it and log WARNING
- WAL replay also deduplicates by exec_id

---

## STARTUP RECOVERY SEQUENCE

On process start (fresh boot or crash recovery):

```
Step 1: LOAD CONFIG
  - Parse settings, load universe_classification.toml, dynamic_weights.toml
  - If .toml files missing → use hardcoded defaults (safe fallback)

Step 2: CONNECT BROKER
  - Connect to IB Gateway (port 4002 paper, 4001 live)
  - reqCurrentTime() → compute clock offset (H03)
  - If abs(offset) > 2s → WARNING, use IBKR time only

Step 3: REPLAY WAL
  - Find latest StateSnapshot event in WAL
  - If found: load snapshot, replay only events after snapshot timestamp
  - If not found: replay ALL events from today's + yesterday's journal
  - Reconstruct PortfolioState from events
  - Verify CRC32 checksums (H24)
  - Hash PortfolioState, compare with last hourly hash in WAL (H85)

Step 4: DETECT ORPHANS
  - Scan for RoutedOrder events without corresponding terminal events
  - If orphans found → execute Orphan Recovery Protocol

Step 5: RECONCILE WITH IBKR
  - reqOpenOrders() → diff vs WAL-reconstructed open orders
  - reqPositions() → diff vs WAL-reconstructed positions
  - If mismatch → log CRITICAL, trust broker, update local state
  - If mismatch found → trigger FLATTEN

Step 6: BLOCK UNTIL READY
  - Reject ALL inputs until orphan_count == 0 AND reconciliation == clean
  - Write SystemReady event to WAL
  - Set RiskArbiter to NORMAL (or maintain HALT if unresolved issues)

Step 7: SUBSCRIBE MARKET DATA
  - reqMktData for all Universe tickers (paced at 10ms intervals, H42)
  - Wait for first tick to confirm data flow

Step 8: BEGIN TRADING
  - Start the Vanguard hot-path and Apex radar
  - System is live
```

---

## EXIT PRIORITY COLLISION MATRIX

When multiple exits fire on the SAME tick for the SAME position:

| Condition A | Condition B | Winner | Loser Action |
|-------------|-------------|--------|-------------|
| HALT/FLATTEN | Hard Stop | HALT/FLATTEN | Suppressed, logged |
| HALT/FLATTEN | Chandelier | HALT/FLATTEN | Suppressed, logged |
| HALT/FLATTEN | EOD Flatten | HALT/FLATTEN | Suppressed, logged |
| Hard Stop | Chandelier | Hard Stop | Suppressed, logged |
| Hard Stop | EOD Flatten | Hard Stop | Suppressed, logged |
| Hard Stop | Signal Rev. | Hard Stop | Suppressed, logged |
| Chandelier | EOD Flatten | Chandelier | Suppressed, logged |
| Chandelier | Signal Rev. | Chandelier | Suppressed, logged |
| EOD Flatten | Signal Rev. | EOD Flatten | Suppressed, logged |

**HALT Override Rule:** When HALT fires, ALL exits for ALL positions
become MarketSell. Individual exit conditions are cancelled entirely.

---

## RISK ARBITER STATE MACHINE

The RiskArbiter has its own internal state machine:

```
                    ┌─────────┐
                    │ NORMAL  │ ← Default startup state
                    └────┬────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
          ▼              ▼              ▼
     ┌─────────┐   ┌──────────┐   ┌──────┐
     │ REDUCE  │   │ FLATTEN  │   │ HALT │
     └────┬────┘   └────┬─────┘   └──┬───┘
          │              │            │
          │         auto │       manual│
          │         after│       human │
          │         close│       only  │
          │              │            │
          ▼              ▼            ▼
     ┌─────────┐   ┌──────────┐   ┌──────┐
     │ NORMAL  │   │ NORMAL   │   │NORMAL│
     └─────────┘   └──────────┘   └──────┘
```

**Transition Rules:**
- NORMAL → REDUCE: tick drops >100/s, queue >80%, Python >2000ms
- NORMAL → FLATTEN: daily loss >2%, orphan detected, recon mismatch
- NORMAL → HALT: stale data, broker DC, WAL fail, ISA violation, 3 rejects/min, 3 consecutive losses
- REDUCE → NORMAL: triggers clear for 5 minutes (auto)
- FLATTEN → NORMAL: all positions closed + reconciliation clean (auto)
- HALT → NORMAL: manual human approval ONLY
- Any state → HALT: HALT always wins (highest precedence)
- REDUCE + FLATTEN simultaneous: FLATTEN wins (higher precedence)

**Critical:** RiskStateChange events are written to WAL on every transition.

---

## STATE COUNT VERIFICATION

- Order lifecycle states: 15 (IntentGenerated through PositionClosed)
- Terminal states: 3 (Rejected, BrokerRejected, PositionClosed)
- Risk Arbiter states: 4 (Normal, Reduce, Flatten, Halt)
- Orphan resolution cases: 4 (filled, open→cancel, never_received, already_cancelled)
- Exit priority levels: 5 (HaltFlatten, HardStop, Chandelier, EodFlatten, SignalReversal)
