# Syndicate Triage Delta — 2026-03-24
**Input**: New ChatGPT section-level suggestions (30 items)
**Diffed against**: `syndicate_triage_20260324.md` (20 items triaged) + `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md` (22 sections)
**System state**: ~66 trades, 1 producing strategy, 35.4% WR, -6.79 P&L, paper mode

---

## SECTION-LEVEL CHANGES (Items 1-12)

### 1. Section 1 (Exec Summary): Add "Current capital core: VanguardSniper", "Primary objective is net expectancy per approved trade"

**ALREADY COVERED**

- Triage C2 already marked VanguardSniper as capital core = ALREADY DONE.
- Plan Section 5 explicitly says "Core. Only proven producer."
- "Net expectancy per approved trade" as primary objective is new phrasing but functionally identical to what the plan already tracks (WR, PF, cumulative P&L). The plan Section 19 validation gates ARE net expectancy proxies.
- Adding a narrative sentence to the exec summary is a documentation tweak, not an engineering item. Not worth a sprint.

**SKIP** — cosmetic rewording of what the plan and triage already say.

---

### 2. Section 2 (System State): Add net expectancy per approved trade, per-strategy trade count, per-strategy net P&L, signal approval rate by source, capital concentration score

**PARTIALLY COVERED, ONE NEW ITEM**

Already covered:
- Net expectancy per strategy: Triage C12 = ACCEPT LATER (Sprint S11). Plan Sprint S11 explicitly adds this.
- Per-strategy trade count: Plan Section 5 already has a "Trades" column (33, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3).
- Per-strategy net P&L: Same as C12 / Sprint S11.

Genuinely new:
- **Signal approval rate by source**: The ratio of signals generated vs. signals that survived the 33 risk CHECKs, broken out by strategy. This is NOT in the triage or plan. It IS useful — tells you which strategies generate noise vs. signal.
- **Capital concentration score**: Measures what % of capital exposure comes from one strategy. With 1 strategy, this is trivially 100%. Useless until 2+ strategies produce trades.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Net expectancy per strategy | ALREADY COVERED (C12, Sprint S11) | Skip |
| Per-strategy trade count | ALREADY IN PLAN (Section 5) | Skip |
| Per-strategy net P&L | ALREADY COVERED (C12, Sprint S11) | Skip |
| Signal approval rate by source | **NEW + USEFUL** | gate_vetoes.ndjson already logs rejections. A nightly step counting approved/rejected per strategy is ~20 lines in nightly_v6.py. Useful NOW to understand why 5 strategies have 0 trades. |
| Capital concentration score | NEW but PREMATURE | Trivially 100% with 1 strategy. Useful post-Sprint S4 when TypeB fires. |

**ACTION**: Add "signal approval rate by source" to Sprint S4 scope (it directly helps the TypeB investigation — if TypeB generates signals that all get vetoed, the problem is risk checks, not trigger logic).

---

### 3. Section 3 (Source of Truth): Add "classifier labels are not executable strategies unless fully wired end-to-end"

**ALREADY COVERED**

- Plan Section 17 (Dead-Code Quarantine) explicitly documents that `entry_engine.rs` (the Rust TypeA-F detector) compiles but is NOT called at runtime.
- Plan Section 6 Contradiction C03 states: "Looks active (786 LOC, compiles)" vs "NOT used at runtime (Python classifies)".
- Triage C11 already covered the strategy inventory having runtime roles.

This is a restatement of what we already documented as a contradiction. Adding a footnote to Section 3 adds nothing.

**SKIP** — already documented in Sections 6 and 17 of the plan.

---

### 4. Section 5 (Strategy Inventory): Replace status with live-producing/live-dormant/shadow/classifier-only/disabled/removed. Add columns for orthogonality, proven-after-costs, regime-fit, promotion criteria, kill criteria

**PARTIALLY COVERED, PARTIALLY NEW, MOSTLY PREMATURE**

Already covered:
- Kill criteria: Triage C10 = ALREADY DONE (strategy_registry.json has disable mechanism).
- Promotion criteria: Triage C10 + plan Section 19 (validation gates = promotion criteria).
- The plan Section 5 already has Status + Observed + Trades + Verdict columns that accomplish the same segmentation.

New taxonomy analysis:
- **live-producing / live-dormant**: Useful distinction. Plan Section 5 currently marks everything as "LIVE" even when 0 trades. Splitting into "live-producing" (VanguardSniper, TypeE) vs "live-dormant" (everything else with 0 trades) is clearer than a Verdict column.
- **classifier-only**: Useful for TypeA-F which are Python labels, not standalone strategies. Plan documents this in Section 17 but Section 5 treats them as full strategies.
- **orthogonality column**: Measures how uncorrelated a strategy's signals are to others. Requires 2+ strategies with trades to compute. PREMATURE.
- **proven-after-costs column**: Same as net expectancy (Sprint S11). ALREADY COVERED.
- **regime-fit column**: strategy_registry.json already has regime_allowed/blocked/reduced. ALREADY EXISTS.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Status taxonomy refinement | **NEW + USEFUL** | Low effort. Better than pretending 0-trade strategies are "LIVE". |
| Orthogonality column | NEW but PREMATURE | Need 2+ producing strategies. |
| Proven-after-costs | ALREADY COVERED (C12, Sprint S11) | Skip |
| Regime-fit | ALREADY EXISTS (strategy_registry.json) | Skip |
| Promotion criteria | ALREADY COVERED (Section 19 gates) | Skip |
| Kill criteria | ALREADY DONE (C10) | Skip |

**ACTION**: Update Section 5 status labels when next editing the plan. Documentation change, not an engineering sprint.

---

### 5. Section 7 (Pre-Live Blockers): Split into "blocker to live" vs "blocker to compounding" vs "blocker to credible research". Move "paper fill realism" to closed.

**PARTIALLY COVERED, PARTIALLY NEW**

Already covered:
- "Move paper fill realism to closed": Plan Section 7 blocker #7 says "Paper fill realism unverified" but Sprint S1 in Section 9 says "COMPLETED". This is a stale entry that should be updated. Good catch, but it is a documentation lag, not a new idea.
- Triage C13 already proposed "compounding-specific blocker prioritization" = REJECTED as relabeling.

What is new:
- The 3-tier split (live / compounding / credible research) is actually a sharper framing than what C13 proposed. C13 was "compounding-specific" which I rejected as relabeling. This version adds a third tier: "blocker to credible research" which catches things like cost-honest backtests that block strategy evaluation but not trading.

Is it useful NOW?
- Marginally. The real problem is that ALL 6 gates fail. The distinction between "blocks live" and "blocks compounding" is moot when you cannot pass the first gate. This becomes useful when you pass 4 of 6 gates and need to triage which remaining blockers matter most.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Move paper fill to closed | **NEW + USEFUL** | Housekeeping. Section 7 #7 should be marked CLOSED. |
| 3-tier blocker split | NEW but PREMATURE | All gates fail. Tier distinction adds no value until some gates pass. |

**ACTION**: Mark Section 7 #7 as CLOSED (S1 audit complete). Do not restructure the section yet.

---

### 6. Section 10 (Daily Workflow): Add top 10 signals, top 10 veto reasons, spread drift by session, strategy contribution table

**PARTIALLY NEW, PARTIALLY COVERED**

Already covered:
- Strategy contribution table: Plan Section 5 already tracks trades per strategy. The nightly pipeline (Section 15) computes per-strategy WR and PF.
- Spread drift: Plan Section 20 identifies spread cost as a variable. Sprint S7 adds spread-at-fill tracking.

What is new:
- **Top 10 signals of the day**: Not in the plan or triage. Daily "best signals" visibility would be useful for understanding what the system is evaluating, but this is a monitoring/reporting feature, not a trading feature.
- **Top 10 veto reasons of the day**: Not in the plan or triage. gate_vetoes.ndjson captures this data already. Summarizing it daily would directly help Sprint S4 (TypeB investigation) by showing which risk CHECKs block which strategies.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Top 10 signals | NEW but LOW PRIORITY | Monitoring nice-to-have. Not blocking anything. |
| Top 10 veto reasons | **NEW + USEFUL** | Directly serves Sprint S4. Same data as "signal approval rate by source" from item 2. |
| Spread drift by session | ALREADY COVERED (Sprint S7) | Skip |
| Strategy contribution table | ALREADY EXISTS (Section 5, nightly) | Skip |

**ACTION**: "Top 10 veto reasons" is the same need as "signal approval rate by source" from item 2. Both are a nightly veto summary. Add to Sprint S4 scope.

---

### 7. Section 12 (Final Verdict): Rewrite to distinguish live-readiness from compounding-readiness

**ALREADY COVERED (and already rejected)**

Triage C13 proposed compounding-specific blocker prioritization = REJECTED. This is the same idea applied to Section 12 instead of Section 7. The reasoning stands: with all 6 gates failing, the distinction between "ready for live" and "ready to compound" is academic.

**SKIP** — rehash of C13.

---

### 8. Section 15 (Nightly Pipeline): Add symbol-quality updates, friction decomposition, edge-density ranking

**PARTIALLY COVERED**

Already covered:
- Symbol-quality updates: Triage C5 = ACCEPT LATER (Sprint S11). Plan Section 15 step 4.5 already has `generate_ticker_scoreboard()`.
- Friction decomposition: Triage C6 = ACCEPT LATER (after S4+S7). Plan Sprint S7 adds spread tracking. Sprint S8 adds friction-aware ranking.

What is new:
- **Edge-density ranking**: Measures how many profitable signals per unit time a strategy produces. This is a signal-rate metric. With 1 strategy producing ~2 trades/day, this is trivially measurable by eye. It becomes useful when 3+ strategies compete for capital allocation.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Symbol-quality updates | ALREADY COVERED (C5, Sprint S11) | Skip |
| Friction decomposition | ALREADY COVERED (C6, Sprints S7+S8) | Skip |
| Edge-density ranking | NEW but PREMATURE | 1 strategy, ~2 trades/day. No ranking needed. |

**SKIP** — all items already covered or premature.

---

### 9. Section 18 (Paper vs Live): Add compounding penalty, hidden optimism penalty columns

**PARTIALLY NEW**

Already covered:
- Plan Section 18 already has an 8-row table of paper overrides with Impact and Risk columns. Sprint S6 creates config.live.toml to address these.

What is new:
- **Compounding penalty column**: Quantifies how much each paper override inflates P&L vs a compounding real account. For example, max_positions=999 means paper can compound across unlimited positions simultaneously, inflating returns vs. a 3-position live account. This is a valid analytical point.
- **Hidden optimism penalty**: Zero-slippage, zero-commission simulation overstates returns. Plan Section 20 already identifies this gap and Sprint S7 addresses it.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Compounding penalty column | NEW but PREMATURE | The penalty is real but irrelevant at -6.79 P&L. You are not compounding losses. Useful when P&L turns positive. |
| Hidden optimism penalty | ALREADY COVERED (Section 20, Sprint S7) | Skip |

**SKIP** — one item premature, one already covered.

---

### 10. Section 19 (Validation Gates): Add compounding gates (friction-adjusted expectancy positive, symbol-quality improving)

**PARTIALLY COVERED, PARTIALLY NEW**

Already covered:
- Friction-adjusted expectancy: Triage C6 + Sprint S7/S8. The gates in Section 19 are gross metrics; Sprint S7 will make them net.
- Symbol-quality improving: Triage C5 + Sprint S11.

What is new:
- The concept of a separate gate tier for compounding vs. going live. Current gates are binary: pass all 6 = ready. A compounding gate would add: "even after passing the 6, you also need friction-adjusted positive expectancy to start sizing up." This is the same live-vs-compound distinction from items 5 and 7.

**SKIP** — already covered by existing sprints (S7, S8, S11) or rehash of the compounding framing (rejected in C13).

---

### 11. Section 20 (Commission/Slippage): Expand into spread/slippage/latency/stale-signal/session-specific/symbol-specific cost buckets + stress model

**PARTIALLY COVERED, PARTIALLY NEW**

Already covered:
- Spread and slippage: Plan Section 20 + Sprint S7.
- Commission: Plan Section 20 has the IBKR model.
- Triage G4 (synthetic cost injection) = ACCEPT LATER.
- Triage C6 (friction-aware ranking) = ACCEPT LATER.

What is new:
- **Latency cost**: Measures time between signal generation and fill, attributing P&L loss to execution delay. Not in the plan or triage. Requires tick-level timestamping in WAL (signal_time vs fill_time). Useful for live but meaningless in sim mode (fills are instant).
- **Stale-signal cost**: Related to latency — measures how much edge decays between signal generation and execution. Not in the plan. Same caveat: sim fills are instant so staleness is zero.
- **Session-specific cost buckets**: Spread costs differ between Asia open (wide), LSE mid-session (tight), US open (tight). Not explicitly in the plan but Sprint S7 spread-at-fill tracking would capture this data implicitly.
- **Symbol-specific cost buckets**: Per-ticker cost profiles. Sprint S11 (symbol-quality memory) already covers this.
- **Stress model**: Simulating cost blowup under high-volatility or low-liquidity conditions. Not in the plan. Premature for paper mode.

| Sub-item | Verdict | Reasoning |
|----------|---------|-----------|
| Latency cost | NEW but PREMATURE | Zero latency in sim mode. Live-only concern. |
| Stale-signal cost | NEW but PREMATURE | Zero staleness in sim mode. Live-only concern. |
| Session-specific cost | PARTIALLY COVERED (Sprint S7 implicitly) | Skip |
| Symbol-specific cost | ALREADY COVERED (Sprint S11) | Skip |
| Stress model | NEW but PREMATURE | Paper mode, 10k equity, no real fills. |

**SKIP** — all new items are live-only concerns, premature for paper mode.

---

### 12. Section 22 (Stop-State): Add capital-core status, highest compounding blocker, biggest unresolved contradiction

**PARTIALLY COVERED**

Already covered:
- Capital-core status: VanguardSniper is the capital core. Already in plan Section 5 and triage C2.
- Biggest unresolved contradiction: Plan Section 6 has a full Contradiction Register.

What is new:
- **Highest compounding blocker**: Again the compounding framing from C13, items 5, 7, 10. Rejected.
- Having the stop-state handoff explicitly call out the #1 contradiction is a documentation practice improvement, not an engineering item. Marginally useful for session handoffs.

**SKIP** — compounding framing rejected, contradiction register already exists.

---

## NEW SECTIONS PROPOSED (Items 23-30)

### 23. Capital Core Doctrine (new section)

**ALREADY COVERED**

- Triage C2 = ALREADY DONE (VanguardSniper is capital core).
- Plan Section 5 = "Core. Only proven producer."
- A separate "doctrine" section formalizing that VanguardSniper is the capital core is a documentation structure preference. The information already exists.

**SKIP** — renaming existing information into a new section adds nothing.

---

### 24. Regime Routing Layer (new section)

**ALREADY COVERED**

- Triage C3 = PARTIALLY DONE + ACCEPT LATER (Sprint S10).
- Plan Sprint S10 = "Regime + Session Enforcement (2 hours)".
- strategy_registry.json already has regime_allowed/blocked/reduced fields.
- A dedicated plan section documenting the regime layer architecture could be useful when Sprint S10 is implemented. Not before.

**SKIP** — Sprint S10 already planned. Write the section when implementing, not before.

---

### 25. Session Template Layer (new section)

**ALREADY COVERED**

- Triage C4 = PARTIALLY DONE + ACCEPT LATER (Sprint S10).
- Plan Sprint S10 covers session enforcement alongside regime enforcement.
- strategy_registry.json already has session_allowed/blocked fields.

**SKIP** — same as item 24.

---

### 26. Symbol Quality Memory (new section)

**ALREADY COVERED**

- Triage C5 = ACCEPT LATER (Sprint S11).
- Plan Sprint S11 = "Symbol-Quality Memory + Net Expectancy Metrics (1 hour)".
- Nightly pipeline step 4.5 already has `generate_ticker_scoreboard()`.

**SKIP** — Sprint S11 already planned.

---

### 27. Friction-Aware Ranking and Sizing (new section)

**ALREADY COVERED**

- Triage C6 = ACCEPT LATER (after S4+S7).
- Plan Sprint S8 = "Friction-Aware Signal Ranking (1 hour)".

**SKIP** — Sprint S8 already planned.

---

### 28. Portfolio Heat and Correlation Governance (new section)

**ALREADY COVERED (and already rejected)**

- Triage C7 = REJECT (heat cap exists at 50% paper / 10% live; correlation clustering premature with 1 strategy).
- Plan Section 18 covers paper-vs-live config including max_heat_pct.

**SKIP** — rejected in original triage, reasoning still applies.

---

### 29. Strategy Kill Framework (new section)

**ALREADY COVERED**

- Triage C10 = ALREADY DONE.
- strategy_registry.json is the kill framework. TypeA and TypeD already disabled.

**SKIP** — already implemented.

---

### 30. Compounding Blockers Register (new section)

**ALREADY COVERED (and already rejected)**

- Triage C13 = REJECT (relabeling existing priorities).
- This is the compounding framing proposed for items 5, 7, 10, 12, now as a standalone section.

**SKIP** — rejected reasoning still holds. All gates fail; tiering them does not help.

---

## DELTA SUMMARY TABLE

| # | Suggestion | Verdict | Action |
|---|-----------|---------|--------|
| 1 | Exec summary: VanguardSniper as capital core | ALREADY COVERED (C2, Section 5) | Skip |
| 2a | Section 2: net expectancy per strategy | ALREADY COVERED (C12, Sprint S11) | Skip |
| 2b | Section 2: per-strategy trade count | ALREADY IN PLAN (Section 5) | Skip |
| 2c | Section 2: per-strategy net P&L | ALREADY COVERED (C12, Sprint S11) | Skip |
| 2d | Section 2: signal approval rate by source | **NEW + USEFUL NOW** | Add to Sprint S4 |
| 2e | Section 2: capital concentration score | NEW but PREMATURE | Skip |
| 3 | Section 3: classifier labels caveat | ALREADY COVERED (Sections 6+17) | Skip |
| 4a | Section 5: refined status taxonomy | **NEW + USEFUL** (low effort) | Doc update when editing plan |
| 4b | Section 5: orthogonality column | NEW but PREMATURE | Skip |
| 4c | Section 5: proven-after-costs | ALREADY COVERED (C12, Sprint S11) | Skip |
| 4d | Section 5: regime-fit | ALREADY EXISTS (registry) | Skip |
| 4e | Section 5: promotion/kill criteria | ALREADY COVERED (C10, Section 19) | Skip |
| 5a | Section 7: move paper fill to closed | **NEW + USEFUL** (housekeeping) | Mark Section 7 #7 CLOSED |
| 5b | Section 7: 3-tier blocker split | NEW but PREMATURE | Skip |
| 6a | Section 10: top 10 signals | NEW but LOW PRIORITY | Skip |
| 6b | Section 10: top 10 veto reasons | **NEW + USEFUL NOW** | Add to Sprint S4 (same as 2d) |
| 6c | Section 10: spread drift by session | ALREADY COVERED (Sprint S7) | Skip |
| 6d | Section 10: strategy contribution table | ALREADY EXISTS | Skip |
| 7 | Section 12: live vs compounding readiness | ALREADY COVERED + REJECTED (C13) | Skip |
| 8a | Section 15: symbol-quality updates | ALREADY COVERED (C5, Sprint S11) | Skip |
| 8b | Section 15: friction decomposition | ALREADY COVERED (C6, Sprints S7+S8) | Skip |
| 8c | Section 15: edge-density ranking | NEW but PREMATURE | Skip |
| 9a | Section 18: compounding penalty column | NEW but PREMATURE | Skip |
| 9b | Section 18: hidden optimism penalty | ALREADY COVERED (Section 20, Sprint S7) | Skip |
| 10 | Section 19: compounding gates | ALREADY COVERED (Sprints S7+S8+S11) | Skip |
| 11a | Section 20: latency cost | NEW but PREMATURE (sim fills instant) | Skip |
| 11b | Section 20: stale-signal cost | NEW but PREMATURE (sim fills instant) | Skip |
| 11c | Section 20: session-specific cost | PARTIALLY COVERED (Sprint S7) | Skip |
| 11d | Section 20: symbol-specific cost | ALREADY COVERED (Sprint S11) | Skip |
| 11e | Section 20: stress model | NEW but PREMATURE | Skip |
| 12a | Section 22: capital-core status | ALREADY COVERED (C2, Section 5) | Skip |
| 12b | Section 22: highest compounding blocker | REJECTED (C13 rehash) | Skip |
| 12c | Section 22: biggest unresolved contradiction | ALREADY EXISTS (Section 6) | Skip |
| 23 | New: Capital Core Doctrine | ALREADY COVERED (C2) | Skip |
| 24 | New: Regime Routing Layer | ALREADY COVERED (C3, Sprint S10) | Skip |
| 25 | New: Session Template Layer | ALREADY COVERED (C4, Sprint S10) | Skip |
| 26 | New: Symbol Quality Memory | ALREADY COVERED (C5, Sprint S11) | Skip |
| 27 | New: Friction-Aware Ranking | ALREADY COVERED (C6, Sprint S8) | Skip |
| 28 | New: Portfolio Heat + Correlation | REJECTED (C7) | Skip |
| 29 | New: Strategy Kill Framework | ALREADY DONE (C10) | Skip |
| 30 | New: Compounding Blockers Register | REJECTED (C13) | Skip |

---

## FINAL SCORECARD

| Verdict | Count | % |
|---------|-------|---|
| NEW + USEFUL NOW | 2 | 5% |
| NEW + USEFUL (doc only) | 2 | 5% |
| NEW but PREMATURE | 8 | 21% |
| NEW but LOW PRIORITY | 1 | 3% |
| ALREADY COVERED | 20 | 53% |
| ALREADY REJECTED | 5 | 13% |
| **Total items evaluated** | **38** | 100% |

---

## ACTIONABLE ITEMS (only 2 engineering items, 2 doc items)

### Engineering (add to Sprint S4 scope)

**1. Signal approval rate by source + top veto reasons** (items 2d + 6b)
- Same underlying need: summarize gate_vetoes.ndjson by strategy source and veto reason.
- Directly serves the S4 investigation ("why does TypeB never fire?").
- Implementation: ~20 lines in nightly_v6.py OR a one-off analysis script.
- This is the ONLY genuinely new suggestion that helps our current state.

### Documentation (next time plan is edited)

**2. Refine Section 5 status labels** (item 4a)
- Replace all "LIVE" with "live-producing" / "live-dormant" / "classifier-only" / "disabled".
- Low effort, improves clarity.

**3. Mark Section 7 #7 as CLOSED** (item 5a)
- Paper fill realism blocker already resolved by Sprint S1.
- Stale entry.

---

## META-OBSERVATION

Of 30 items proposed, 25 (83%) were already in the existing triage or plan. This confirms the original triage was thorough. The new feedback is largely repackaging accepted-later items (Sprints S7-S12) into new section proposals.

The "compounding" framing appears 5 times (items 5b, 7, 10, 12b, 30) — it is ChatGPT's central thesis that we should distinguish "ready for live" from "ready to compound." This was rejected in the original triage (C13) and the reasoning has not changed: all 6 validation gates fail. Tiering the failures into "live blockers" vs "compounding blockers" is a distinction without a difference when you cannot pass either.

The one genuinely useful new item — signal approval rate by source — is small, directly actionable, and should be folded into Sprint S4.
