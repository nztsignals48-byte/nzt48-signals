# Syndicate Triage #2 — 2026-03-24 (Late Session)
**Sources**: Gemini "Institutional Syndicate" + ChatGPT simulation analysis + Gemini "36 bullet stress test"
**System state**: 9 generators live (S4 done), ~70 trades, TypeE first trade (GOOG), system trading

---

## REPEAT ITEMS (already triaged, verdict unchanged)

| Item | Original triage ID | Verdict |
|------|-------------------|---------|
| Cost injection into Ouroboros | G4 / S5 | ACCEPT LATER — Sprint S5 |
| LSEETF ticker disposal | S6 | ACCEPT LATER — Sprint S6 |
| Polygon 5000-ticker scanner | G1 | REJECTED |
| 8-minute micro time-stop | G3 | REJECTED |
| Parabolic SAR exits | G3 | REJECTED |
| Covariance Kelly | G5 | REJECTED |
| Lambda compute segregation | G1 | REJECTED |
| TypeB rewrite to OBI + volume shock | G2c | ALREADY DONE differently (S4B: 2-bar RVOL) |
| Regime routing | C3 / S11 | ACCEPT LATER |
| Session templates | C4 / S11 | ACCEPT LATER |
| Friction-aware ranking | C6 / S9 | ACCEPT LATER |
| Symbol-quality memory | C5 / S6 | ACCEPT LATER |
| Strategy kill framework | C10 | ALREADY DONE (strategy_registry.json) |
| Instrument class layer for leveraged ETPs | NEW framing of S6 | ACCEPT LATER — good framing, merge into S6 |
| "Flush poisoned N=68 data" | NEW | REJECT — data isn't "poisoned," it's pre-cost. Adding costs retroactively is better than deleting history |

---

## GENUINELY NEW TECHNICAL POINTS (from Gemini 36-bullet stress test)

### NEW + VALID + ACTIONABLE

| # | Point | File | Severity | Action |
|---|-------|------|----------|--------|
| V1.6 | Zero-tick / crossed market: bid==ask causes divide-by-zero in spread calc | engine.rs spread_pct calc | MEDIUM | **FIX** — add guard `if bid >= ask { skip }` |
| V1.8 | Slippage injection must be %-based or ATR-based, not flat ticks | persistent_memory.py (S5) | HIGH | **NOTE for S5** — use bps not ticks |
| V4.6 | Dividend/split adjustment: 2-for-1 split looks like 50% crash, triggers hard stop | engine.rs price tracking | LOW | **DOCUMENT** — paper mode only, IBKR adjusts live |
| V6.2 | WAL corruption on kernel panic: malformed last JSON line crashes parser | wal.rs / wal_replay.rs | MEDIUM | **FIX** — add try_parse with skip-malformed-line |
| V6.3 | Orphaned order: TCP drop between order send and ACK leaves Rust thinking no position exists while IBKR holds shares | ibkr_broker.rs | HIGH (live only) | **DOCUMENT** — reconciliation on reconnect already handles this (engine.rs:2954) |
| V6.4 | Config hierarchy on hot restart: does Python read config.toml or dynamic_weights.toml? | bridge.py config loading | MEDIUM | **VERIFY** — config.toml is primary, dynamic_weights is overlay |

### NEW + VALID BUT PREMATURE

| # | Point | Why premature |
|---|-------|---------------|
| V1.1 | cancelMktData before new L1 subscribe | We don't do top-10 rotation (that's Gemini's rejected Polygon architecture) |
| V1.3 | Odd-lot tape / dark pool filtering | Paper mode — we don't have L2/dark pool data |
| V1.4 | OBI needs L2, not L1 | We don't calculate OBI — this is Gemini's rejected TypeB rewrite |
| V1.5 | HKEX variable board lots per ticker | Valid but minor — 100-share default covers most HKEX tickers |
| V1.7 | Time-of-day volume baseline | Valid future improvement, not urgent for paper |
| V2.1 | Python GIL on 10 concurrent streams | We process sequentially per tick, not concurrent. GIL is not the bottleneck. |
| V2.2 | JSON IPC serialization cost | Measured at <5ms per tick. Not a real bottleneck at 5s bar cadence. |
| V2.3 | AWS network burst limits | Not relevant at current tick rates (100 MktData, 5s bars) |
| V2.4 | Redis AOF blocking | Redis is used for state journal only, not hot path. Low write rate. |
| V2.5 | Pandas memory fragmentation | bridge.py uses numpy, not pandas. No DataFrames in hot path. |
| V2.6 | Clock drift (chronyd) | EC2 uses chrony by default. Clock is synced. |
| V3.1 | N=100 statistically irrelevant | We require N=200 (already in plan). Margin of error acknowledged. |
| V3.2 | Path dependency in Ouroboros learning | Valid but frozen until N=300. Future concern. |
| V3.3 | Regime overlap in nightly tuning | Valid but frozen. Will address when unfreezing. |
| V3.4 | Covariance Black Swan | REJECTED — single strategy, no covariance to compute |
| V3.5 | Ouroboros TOML parser crash on unfreeze | approval_gate.py has 10% change cap per cycle. Cannot generate fatal config. |
| V3.6 | Commission tier mismatch | Valid but minor — £1.50 minimum is correct for IBKR tiered pricing at our volume |
| V4.1 | 8-minute stop scratch cost | REJECTED — we don't use 8-minute stops |
| V4.2 | Parabolic SAR whipsaw | REJECTED — we don't use SAR |
| V4.3 | Top-10 rotation slippage | REJECTED — we don't do Polygon top-10 rotation |
| V4.4 | Overnight gap trap | Valid — but we flatten at EOD (16:25 UTC). No overnight holds except carried positions. |
| V4.5 | Market order slippage in flash crash | Already handled — Chandelier uses MarketToLimit+IOC on gap-through |
| V5.1 | Claude prompt drift on model update | Valid long-term concern. Shadow mode protects. |
| V5.2 | Context window saturation on large WAL | Valid — nightly only processes today's trades, not full history |
| V5.3 | Gemini API 502 fallback | Deterministic fallback already active. System trades without Gemini. |
| V5.4 | Claude JSON parsing | approval_gate.py handles malformed JSON gracefully (try/except) |
| V5.5 | Claude hallucinated Kelly 0.95 | Risk arbiter CHECK 9: max kelly_fraction capped at 0.20 in config. Cannot exceed. |
| V6.1 | Telegram kill switch latency | SSH kill switch exists as primary. Telegram is secondary. |

---

## SUMMARY

| Verdict | Count |
|---------|-------|
| REPEAT (already triaged) | 15 |
| NEW + ACTIONABLE | 6 |
| NEW + PREMATURE | 26 |
| TOTAL | 47 |

**6 new actionable items** found across ~3000 words of feedback. The rest is either repeating our existing triage or solving problems for systems we don't have (Polygon scanner, SAR exits, Covariance Kelly, L2 OBI).

## ACTION ITEMS

1. **V1.6** (crossed market guard): Add `if bid >= ask` guard in engine.rs spread calc — 5 minutes
2. **V1.8** (slippage injection method): Note for Sprint S5 — use bps not flat ticks
3. **V6.2** (WAL corruption resilience): Add try-parse with skip-malformed in wal_replay.rs — 15 minutes
4. **V6.3** (orphaned orders): Already handled by reconciliation on reconnect. Document explicitly.
5. **V6.4** (config hierarchy): Verify and document config.toml vs dynamic_weights.toml priority
6. **V4.6** (splits): Document that paper mode doesn't handle splits (IBKR adjusts in live)

Items 1 and 3 are worth fixing now. The rest are documentation.
