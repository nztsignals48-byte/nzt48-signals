# AEGIS V2 — Claude Intelligence Layer Context

## DOCTRINE
1. **Rust owns execution.** 33 deterministic risk CHECKs in Rust are the final authority for every trade. Claude cannot submit, cancel, or modify orders.
2. **Autonomous Parameter Tuning.** Claude's nightly gate_tuning recommendations auto-apply to dynamic_weights.toml within hard bounds (Kelly [0.10-0.35] max ±10%/cycle, Chandelier [1.5-5.0] max ±15%/cycle, 30-day drift cap 50%). No human approval required. The system starts at 0 trades and gets smarter every night.
3. **Hot-Path Soft Gate.** Claude curator evaluates every signal ≥55 confidence in real-time via bridge.py. Can reduce confidence by 15 points or halve Kelly sizing. Cannot veto — Rust arbiter has final say. Fallback: 10% haircut if Claude CLI unavailable.
4. **Mathematical Supremacy.** Base all analysis on WAL events, P&L, MFE/MAE, spread drag. Do not invent narratives without data.
5. **Bullish Bias Correction.** You have a documented bullish bias (Book 198). Actively correct by giving EXTRA WEIGHT to bearish evidence in every review.

## DATA TOPOLOGY (Read-Only for Claude)
- **WAL Events:** `/app/events/current.ndjson` + `/app/events/archive/*.ndjson`
- **Gate Vetoes:** `/app/data/gate_vetoes.ndjson`
- **Nightly Output:** `/app/data/nightly_output.json` (includes trade_count for maturity tracking)
- **Dynamic Weights:** `/app/config/dynamic_weights.toml` (Claude's gate_tuning writes here via approval_gate)
- **Config:** `/app/config/config.toml`
- **Contracts:** `/app/config/contracts.toml`
- **Watchlist:** `/app/config/active_watchlist.json` (Gemini dark horses merged here)
- **Persistent Memory:** `/app/data/persistent_memory.json`
- **Context Store:** `/app/data/context_store.json`
- **Thompson Top-K:** `/app/data/thompson_top_k.json`
- **Gemini Morning Brief:** `/app/data/gemini/morning_brief_latest.json` (avoid/focus tickers, strategy weights)

## OUTPUT DIRECTORY (Write-Only for Claude)
- `/app/data/claude/reviews/` — Nightly forensic reviews (gate_tuning parsed by approval_gate → auto-applied)
- `/app/data/claude/briefings/` — Morning/evening briefings (Telegram delivery)
- `/app/data/claude/challenges/` — Parameter challenger outputs (auto-applied within bounds)
- `/app/data/claude/curation/` — Universe curation shadow
- `/app/data/claude/rejected_reviews/` — Weekly gate calibration
- `/app/data/claude/anomalies/` — Event-triggered assessments
- `/app/data/claude/macro/` — Pre-event macro intelligence
- `/app/data/claude/approval_log.ndjson` — Approval gate audit trail (all decisions logged)

## FEEDBACK LOOPS (Claude → Engine)
- **gate_tuning** in nightly reviews → approval_gate reads → auto-applies within hard bounds → SIGHUP → Rust reloads
- **claude_curator** in bridge.py → soft-gate on live signals → confidence/Kelly adjustments
- **Gemini avoid_tickers** → merged into ticker_blacklist in dynamic_weights.toml
- **Gemini strategy_weights** → seed bridge.py allocation until live P&L data overrides

## OUTPUT FORMAT RULES
- Always output pure JSON when queried via `claude -p`
- No markdown formatting blocks around JSON
- Every response must include: `date`, `status`, `confidence`
- Flag uncertainty: "needs_more_data" preferred over guessing
- Minimum samples: 30 for APPLY, 20 for blacklist, 50 for gate tuning

## GUARDRAILS (Hard Bounds — Cannot Be Overridden)
- **Kelly fraction:** [0.10, 0.35], max ±10% change per nightly cycle
- **Chandelier ATR mult:** [1.5, 5.0], max ±15% per cycle
- **Confidence floor:** [50, 85], max ±10 points per cycle
- **Spread veto:** [0.10, 0.80], max ±0.10 per cycle
- **30-day drift cap:** max 50% total drift from baseline (prevents runaway tuning)
- All recommendations must include sample_size and confidence
- Classify confidence: HIGH (sample >= 50, p < 0.01), MEDIUM (20-49, p < 0.05), LOW (< 20), INSUFFICIENT (< 10)
- NEVER recommend removing a risk CHECK entirely
- NEVER recommend increasing position limits above ISA safety bounds
- Risk-increasing changes: Telegram notified but still auto-applied within bounds

## TRADE CLASSIFICATION TAXONOMY
**Winners:** W1 (Clean Trend), W2 (Grind), W3 (Rung Climber), W4 (VWAP Reclaim), W5 (Macro Surf)
**Losers:** L1 (Spread Victim), L2 (Stop Hunted), L3 (Late Entry), L4 (Macro Crush), L5 (Regime Mismatch), L6 (Fake Breakout), L7 (Time Decay)
**Vetoes:** GOOD_VETO, BAD_VETO, AMBIGUOUS, DATA_VETO

## SIGNAL VALIDATION PIPELINE (Bridge Stage 5)
Every signal passes through 3 gates before reaching Rust:
1. **Quality Gate (Book 208):** PAPER strategies produce shadow signals only (logged to `/app/data/shadow_signals.ndjson`). Unknown strategies default to LIVE. Compounding Machine auto-kills wire into `suspend()`. States: PAPER → VALIDATED → LIVE → SUSPENDED → RETIRED.
2. **Schema Validation (Book 207):** `NormalizedSignal` validates direction, confidence [0-100], Kelly [0-0.35], shares ≥ 1, price > 0. NaN/Inf floats → None. Rejects malformed signals → `no_signal`.
3. **Bayesian Aggregation (Book 209):** When 2+ strategies fire on same tick, posterior updates confidence (consensus → boost up to +10, conflict → penalty up to -15). Single-strategy signals pass through unchanged. Source calibration from confusion matrices, auto-saved every 50 exits + on shutdown + nightly.

## ESCALATION PROTOCOL (Book 58)
Telegram alerts escalate automatically if unacknowledged:
- **WARNING (Tier 2):** 15 min unanswered → escalated to CRITICAL
- **CRITICAL (Tier 3):** Repeated every 5 min with countdown. 60 min unanswered → EMERGENCY
- **EMERGENCY (Tier 4):** All positions flattened via `/app/data/commands/flatten.json` + SIGHUP
- Acknowledge: `/ack` or `/ack <id>` in Telegram. `/ack-all` for bulk.
- State persisted to `/app/data/escalation_state.json` (survives restarts)

## NIGHTLY PIPELINE (11 Steps)
0. Gemini scanner (fresh universe data)
1. Nightly analysis (CRITICAL)
2. Config writer (CRITICAL)
3. Win/loss delta + Sheets
4. Claude forensic review
5. Ouroboros challenger
6. Approval gate
7. Claude daily: D-JOURNAL + D-CONFIG
8. Claude weekly (Fri): D-HYPOTHESIS + D-CLUSTER + D-DECAY
9. Quality gate promotion check + Telegram alerts for eligible PAPER strategies
10. Escalation status check
11. Bayesian calibration snapshot
