# GEMINI DEEP ANALYSIS PROMPT
# NZT-48 AEGIS V2 — Phases 11, 12, 13 Full Adversarial Review
#
# HOW TO USE:
#   1. Open Gemini Advanced (1.5 Pro or 2.0 Ultra) with "Deep Research" or "Think" mode enabled
#   2. Paste the prompt below in full
#   3. Attach all three spec files:
#        - docs/PHASE_11_DIRECT_EQUITY_SPEC.md  (2,294 lines)
#        - docs/PHASE_12_EUROPEAN_EQUITY_SPEC.md (1,149 lines)
#        - docs/PHASE_13_ASIA_PACIFIC_SPEC.md   (1,593 lines)
#   4. Send. Do NOT truncate. Read every section before responding.
#
# EXPECTED OUTPUT: ~6,000-10,000 tokens. Reject partial responses.
# ─────────────────────────────────────────────────────────────────────────────

---

## PROMPT START (paste everything below this line)

You are a senior quantitative systems analyst with the following backgrounds simultaneously active:

- **Quant finance**: microstructure theory, optimal execution, stochastic process modelling, market impact models, Kelly criterion and bankroll theory, factor modelling, regime detection, Bayesian methods
- **Institutional trading systems**: HFT infrastructure, execution latency budgets, broker API constraints, market data line limits, ISA/regulatory compliance, dark pool routing
- **Academic research**: proficient in de Prado (2018), Almgren-Chriss (2000), Easley/de Prado/O'Hara (2012), Cont/Kukanov/Stoikov (2014), Kyle (1985), Engle (2002), Sweeney (1996), Romano-Wolf multiple testing, Thompson Sampling, PELT, CUSUM, Kalman filtering, HMM, EWA forecasting, LambdaMART
- **Systems engineering**: distributed systems, failure modes, race conditions, state machine correctness, concurrency hazards, Rust borrow checker implications, Python async pitfalls
- **Adversarial thinking**: red team analysis, edge case enumeration, assumption stress-testing, "what kills this system" analysis

You have been given 3 specification documents for a live algorithmic trading system built in Rust + Python targeting UK ISA leveraged ETPs and global equities. The system trades 24/5 across 5 modes (MODE A Asia-Pac, MODE B Europe, MODE B+ Hybrid, MODE C Americas, DARK calibration). It uses Interactive Brokers (IBKR) paper trading with a 100 simultaneous market data line constraint. Target: 0.3-0.5% daily net return, £10,000 starting equity.

**PHASE 11** — Core adaptive infrastructure: 5-mode architecture, Smart Router (ETP-first routing), Chandelier infinite adaptive ladder, RiskGate (31 vetoes), Ouroboros nightly calibration, HotScanner (tick-level signals: OFI, CUSUM, VPIN, Kalman, TIB), RotationScanner (Thompson Sampling priority queue), Executioner v2 (Almgren-Chriss, Kyle's Lambda, urgency scoring), AUM scaling, Telegram alerts, PDF telemetry.

**PHASE 12** — European equity extension: 15 European exchanges (Euronext Paris/Amsterdam/Brussels/Lisbon, XETRA, SIX Swiss, OMX Stockholm/Helsinki/Copenhagen, Borsa Italiana, BME Madrid, Oslo, Warsaw, Athens), FTT/stamp duty per country, multi-currency routing, sub-universe allocator for MODE B/B+.

**PHASE 13** — Asia-Pacific extension: MODE A (23:00-08:00 UTC), 6 Asian exchanges (TSE, HKEX, ASX, SGX, KRX, NZX), DARK mode (21:00-23:00 UTC) for Ouroboros, overnight carry state machine (LIVE→CARRIED→MONITORED→REACTIVATED→CLOSED), TSE lunch breaks, KRX daily price limits, FX drag per Asian currency, cross-timezone intelligence, GDR routing.

---

## YOUR TASK

Apply maximum depth of thinking across ALL THREE documents before writing a single word of response. Do not skim. Do not summarise. Do not repeat spec content back. Only generate analytical findings not already stated in the specs.

Produce output in EXACTLY this structure:

---

### PART 1 — 200 ANALYTICAL BULLET POINTS

Generate exactly 200 bullet points. Each bullet must be:
- A distinct, actionable finding (not a restatement of the spec)
- Categorised under one of the following headers (use the headers exactly):
  - **[FLAW]** — design error, theoretical mistake, or implementation gap
  - **[RISK]** — operational risk, edge case, or failure mode that could cause losses or system failure
  - **[IMPROVEMENT]** — something that could be better, more efficient, or more theoretically sound
  - **[MISSING]** — something not specified that must be addressed before this is production-ready
  - **[ACADEMIC]** — finding grounded in specific academic literature (cite author + year)
  - **[INFRA]** — infrastructure, latency, broker API, or systems engineering concern

Do not repeat the same point twice. Do not include trivial or obvious observations. Write like a quant hedge fund CTO doing pre-launch due diligence on a system they are about to stake real capital on.

Minimum distribution: at least 30 [FLAW], 30 [RISK], 30 [IMPROVEMENT], 20 [MISSING], 20 [ACADEMIC], 20 [INFRA].

---

### PART 2 — ADVERSARIAL RED TEAM REVIEW

Write a structured adversarial review as if you are a hostile reviewer who has been asked: *"What will kill this system in live trading?"*

Structure your adversarial review as follows:

**A. The Five Most Likely Failure Modes (ranked by probability × severity)**

For each: describe the failure, the trigger condition, how quickly capital is lost, whether it is recoverable, and what the spec currently does (if anything) to prevent it.

**B. The Three Most Dangerous Theoretical Flaws**

For each: state the specific theoretical assumption being violated, cite the academic paper or principle that exposes the flaw, and describe what happens when the assumption breaks in live markets.

**C. The Regime Change Stress Test**

Describe in detail what happens to this system during:
1. A VIX spike from 15 to 45 in a single session (e.g., August 2024 style)
2. A prolonged low-volatility grind (VIX 10-12 for 60+ trading days)
3. A flash crash in a specific 3x ETP (e.g., QQQ3.L drops 35% in 8 minutes)

For each scenario: walk through every component that touches it (signal generation → routing → sizing → execution → risk gate → exit) and identify where the spec is insufficient.

**D. The Execution Cost Reality Check**

The spec targets 0.3-0.5% daily net return. Perform a bottom-up cost stack analysis:
- Spread costs for LSE ETPs at realistic bid/ask widths
- 3x leverage daily compounding drag (volatility decay)
- Currency conversion friction for non-GBP positions
- IBKR commission structure for UK ISA accounts
- Slippage on entry + exit for typical position sizes
- Kyle's Lambda impact at stated position sizes
- Estimated break-even gross return needed before net target is achievable
- Honest assessment of whether 0.3-0.5% daily net is achievable given these costs

**E. The 100-Line Constraint Under Pressure**

The system is architecturally constrained to 100 IBKR market data lines. Perform a worst-case line budget analysis across all 3 phases simultaneously active:
- How many lines are consumed when MODE A is live with 3 carry positions + scanning
- What happens at MODE A → MODE B transition when carry positions haven't closed
- How underlying tracking (ETP → underlying pairs) affects the budget
- Whether the 100-line constraint makes Phases 11+12+13 simultaneously viable without fundamental architectural compromise

**F. The Ouroboros Single Point of Failure**

The Ouroboros pipeline runs nightly in DARK (21:00-23:00 UTC) and is responsible for calibrating every component. Analyse:
- What happens if Ouroboros runs long and doesn't complete before 23:00
- What happens if Ouroboros produces a corrupted calibration output
- What happens if the EC2 instance crashes at 22:30 during calibration
- What happens if IBKR data quality is poor during the calibration window
- Does the spec have sufficient fallback logic, or does the next trading day run on stale/wrong params?

**G. The ISA Compliance Exposure**

The system operates under HMRC ISA rules. Identify:
- Any operation described in the specs that might violate ISA regulations (short selling, derivatives, leveraged products, foreign exchange transactions)
- Whether the routing logic reliably enforces ISA eligibility in all edge cases
- The liability exposure if ISA compliance is breached due to a routing bug

---

### PART 3 — TOP 10 HIGHEST-PRIORITY FIXES

Based on your analysis, list exactly 10 fixes, ordered by priority (most critical first). For each fix:
- State the problem in one sentence
- State the fix in one sentence
- Assign a severity: CRITICAL / HIGH / MEDIUM
- Estimate implementation effort: hours

---

## ANALYTICAL STANDARDS

- Cite specific section numbers from the specs when referencing them (e.g., "Phase 11, Section 9")
- Cite academic papers with author, year, and specific claim being referenced
- Be quantitatively specific where possible (e.g., "a 35% intraday move in a 3x ETP corresponds to a ~105% move in the underlying, which is impossible in a single session, but a 10% underlying move → 30% ETP move is realistic and occurs approximately X times per year in QQQ")
- Do not hallucinate spec content. If something is not in the specs, say "not specified"
- Do not congratulate, soften, or hedge. Write as a hostile expert reviewer.
- Minimum response length: 6,000 tokens. Do not truncate.

## PROMPT END
