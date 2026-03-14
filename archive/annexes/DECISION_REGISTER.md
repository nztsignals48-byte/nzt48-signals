# NZT-48 DECISION REGISTER

**Document ID:** NZT48-ANNEX-DRG-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** LIVING DOCUMENT -- Updated as decisions are proposed, debated, and resolved
**Scope:** All architectural, risk, data, operational, governance, compliance, and execution decisions for the NZT-48 trading system IC/PM Approval Pack
**Classification:** Internal / Decision Authority

---

## PURPOSE

This register is the single source of truth for all material decisions affecting the NZT-48 trading system. Every decision that changes architecture, risk posture, data dependencies, operational procedures, or execution mechanics MUST be recorded here with full traceability.

**Governance Rule:** No OPEN decision may block paper-to-live migration unless explicitly marked as a BLOCKER. All OPEN decisions have a SAFEST DEFAULT that the system operates under until formal resolution.

---

## DECISION FORMAT

Each entry follows this structure:

| Field | Description |
|-------|-------------|
| **DEC-ID** | Sequential identifier (DEC-001 through DEC-NNN) |
| **Title** | Concise decision statement |
| **Status** | `RESOLVED` / `OPEN` / `DEFERRED` |
| **Category** | `ARCHITECTURE` / `RISK` / `DATA` / `OPERATIONS` / `GOVERNANCE` / `COMPLIANCE` / `EXECUTION` |
| **Decision Owner** | Role responsible for final call |
| **Options Considered** | Minimum two alternatives evaluated |
| **Decision Taken** | The chosen option, or `PENDING` if OPEN |
| **Safest Default** | What the system does if OPEN (conservative fallback) |
| **Rationale** | Why this option was selected or why the default is safest |
| **Dependencies** | Other decisions, documents, or external factors this depends on |
| **Date Resolved** | Actual or target resolution date |

---

## STATUS SUMMARY

| Status | Count | IDs |
|--------|-------|-----|
| RESOLVED | 5 | DEC-001 through DEC-005 |
| OPEN | 22 | DEC-006 through DEC-027 |
| DEFERRED | 3 | DEC-028 through DEC-030 |
| **TOTAL** | **30** | |

---

## RESOLVED DECISIONS

---

### DEC-001: Regime Classification Taxonomy

| Field | Value |
|-------|-------|
| **Status** | RESOLVED |
| **Category** | ARCHITECTURE |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) Single unified regime taxonomy with 8-13 states. (B) Retain dual taxonomy -- 5-state per-ticker volatility regime + 8-state market-wide directional regime -- with strict layer separation and cross-layer consistency checks. (C) Replace both with a single HMM-based probabilistic regime model. |
| **Decision Taken** | **Option B** -- Hybrid dual-layer regime model. Layer 1 (market_regime): 8-state system-wide directional classification from `config/settings.yaml` Section 7. Layer 2 (vol_regime): 5-state per-ticker volatility classification from `uk_isa/volatility_regime.py`. Both layers retained with mandatory cross-layer consistency matrix. |
| **Safest Default** | N/A (resolved) |
| **Rationale** | The two taxonomies serve fundamentally different purposes: market_regime drives bot activation and system-wide gating; vol_regime drives ticker-level scoring and opportunity detection. Collapsing them would lose granularity. The bare word "regime" without a prefix is FORBIDDEN in new code. See `REGIME_DROUGHT_SPEC.md` Section 3 for binding specification. |
| **Dependencies** | REGIME_DROUGHT_SPEC.md (binding), FORENSICS_MAP.md P0-4 (root cause) |
| **Date Resolved** | 2026-02-27 |

---

### DEC-002: Soft Gate Fail-Open Behaviour

| Field | Value |
|-------|-------|
| **Status** | RESOLVED |
| **Category** | RISK |
| **Decision Owner** | Head of Risk |
| **Options Considered** | (A) HARD gates everywhere -- any component failure blocks all signals (fail-closed). (B) SOFT gates -- component failures are flagged visibly but do not block signal flow (fail-open with visibility). (C) Tiered approach -- some gates hard, some soft based on severity classification. |
| **Decision Taken** | **Option B** -- SOFT gates keep the failure flag visible in all outputs (Telegram, PDF, War Room) but do not block signal delivery. |
| **Safest Default** | N/A (resolved) |
| **Rationale** | In paper trading mode, maximising signal volume for learning loop data collection is more valuable than perfect gating. The visibility requirement ensures every consumer of the signal knows the confidence adjustment was skipped. For live trading, this decision will be revisited (see DEC-018 dependency). The confluence scorer, AI enhancement, and dynamic sizer are all SOFT gates per FORENSICS_MAP.md Section 2.2. |
| **Dependencies** | FORENSICS_MAP.md Section 2.2, SANITY_GATE_SPEC.md (hard gates remain hard) |
| **Date Resolved** | 2026-02-27 |

---

### DEC-003: Drought Threshold Definition

| Field | Value |
|-------|-------|
| **Status** | RESOLVED |
| **Category** | ARCHITECTURE |
| **Decision Owner** | PM |
| **Options Considered** | (A) 10 consecutive scan cycles with zero qualifying signals. (B) 20 consecutive scan cycles with zero qualifying signals. (C) Time-based: 30 minutes of wall-clock time with zero qualifying signals. |
| **Decision Taken** | **Option B** -- Drought threshold = 20 consecutive scan cycles (at 60-second intervals, this equals ~20 minutes of no qualifying signals). |
| **Safest Default** | N/A (resolved) |
| **Rationale** | 10 cycles is too sensitive -- normal low-volatility lunch hours would trigger drought alerts constantly. 20 cycles provides a meaningful signal that something is genuinely absent from the market, not just a quiet patch. The time-based option (C) was rejected because scan cycles are the atomic unit of the system, not wall-clock time. See REGIME_DROUGHT_SPEC.md Section 5 for escalation tiers. |
| **Dependencies** | REGIME_DROUGHT_SPEC.md (binding), DEC-001 (drought-regime contradiction detection) |
| **Date Resolved** | 2026-02-27 |

---

### DEC-004: Primary Data Vendor Selection

| Field | Value |
|-------|-------|
| **Status** | RESOLVED |
| **Category** | DATA |
| **Decision Owner** | PM |
| **Options Considered** | (A) Remain on yfinance (free, scraping, ToS violation risk, no SLA). (B) Polygon.io Stocks Starter plan at $29/month (licensed API, 15-min delayed US data or real-time with upgrade, REST + WebSocket, documented SLA). (C) Alpaca Market Data at $99/month (real-time US, limited international). (D) IBKR Market Data via TWS API (bundled with broker, real-time LSE + US, requires IBKR account). |
| **Decision Taken** | **Option B** -- Polygon.io at $29/month approved as primary US data provider. yfinance demoted to last-resort fallback. IBKR retained as primary for LSE `.L` tickers (see DEC-017). |
| **Safest Default** | N/A (resolved) |
| **Rationale** | Polygon.io provides licensed, SLA-backed data at an acceptable cost. The $29/month Stocks Starter plan covers all 18 US equities. It eliminates the yfinance ToS violation risk identified in DATA_VENDOR_MIGRATION_PLAN.md Section 2.2. Real-time LSE coverage is NOT included in Polygon.io at this tier. |
| **Dependencies** | DATA_VENDOR_MIGRATION_PLAN.md (binding), DEC-017 (LSE coverage gap) |
| **Date Resolved** | 2026-02-27 |

---

### DEC-005: US Equities Bot B Compute Priority

| Field | Value |
|-------|-------|
| **Status** | RESOLVED |
| **Category** | ARCHITECTURE |
| **Decision Owner** | PM |
| **Options Considered** | (A) Equal compute allocation between ISA universe and US equities (50/50). (B) ISA-primary: 90% compute to ISA, 10% to US Bot B. (C) Fully disable Bot B until ISA paper-to-live is complete. |
| **Decision Taken** | **Option B** -- Bot B deprioritised to 10% compute allocation. ISA CORE universe receives 70% compute, ISA PEER receives 20%. Bot B is not disabled but runs at reduced frequency and feature depth. |
| **Safest Default** | N/A (resolved) |
| **Rationale** | The core mission is ISA leveraged ETP trading. Bot B provides contextual intelligence (US semiconductor moves drive ISA ETP underlyings) but should not compete for resources. Full disable (Option C) was rejected because the learning loop benefits from cross-market correlation data even at reduced resolution. See UNIVERSE_GOVERNANCE_PLAN.md Section 3 for tier compute budgets. |
| **Dependencies** | UNIVERSE_GOVERNANCE_PLAN.md (tier definitions), SCOPE_ALIGNMENT_AUDIT.md |
| **Date Resolved** | 2026-02-27 |

---

## OPEN DECISIONS

---

### DEC-006: Slippage Model for LSE Leveraged ETPs

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | RISK |
| **Decision Owner** | Head of Risk |
| **Options Considered** | (A) Fixed 0.1% slippage assumption (optimistic, suitable for large-cap equities). (B) Fixed 0.2% slippage assumption (moderate, accounts for leveraged ETP spread widening). (C) Fixed 0.3% slippage assumption (conservative, accounts for low-liquidity instruments like XLUS.L at 351 avg daily volume). (D) Tiered model: 0.1% for CORE tier, 0.2% for PEER tier, 0.3% for WATCH tier, based on average daily volume bands. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- 0.3% flat slippage.** When modelling unknown friction costs, overestimating is always safer than underestimating. If paper trading PnL is positive after 0.3% deduction, live results will be equal or better. A model that flatters paper results with 0.1% slippage creates a dangerous illusion of profitability. |
| **Rationale** | LSE leveraged ETPs have materially wider spreads than US large-cap equities. UNIVERSE_GOVERNANCE_PLAN.md Section 2.2 flags multiple tickers with very low average volume. The execution_quality_model.py module in the learning engine will collect empirical slippage data during paper trading, enabling migration to Option D (tiered) once sufficient data exists. |
| **Dependencies** | DEC-025 (ISA broker selection affects actual slippage), DEC-018 (capital allocation for live gate), execution_quality_model.py empirical data collection |
| **Date Resolved** | Target: after 60 paper trading days with empirical fill data |

---

### DEC-007: Spread Gating Threshold

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | RISK |
| **Decision Owner** | Head of Risk |
| **Options Considered** | (A) No spread gate -- allow entry regardless of spread width. (B) Block entry if live spread > 0.5% of mid-price. (C) Block entry if live spread > 1.0% of mid-price. (D) Tiered: block if spread > 0.3% for CORE, > 0.5% for PEER, > 1.0% for WATCH. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option B -- Block entry if spread exceeds 0.5% of mid-price.** A 0.5% spread on a 2% target trade consumes 25% of the expected return in friction alone (entry + exit = 1.0% round trip). This is the tightest practical threshold that does not excessively filter CORE instruments during normal trading hours. |
| **Rationale** | Without spread gating, the system can enter positions where the bid-ask alone exceeds the 2% daily target. A 1.0% threshold (Option C) is too loose -- it allows 50% of the target to be consumed by spread friction. Option A (no gate) is unacceptable for live trading. Spread data will be collected during paper trading to calibrate the final threshold. |
| **Dependencies** | DEC-006 (slippage model), DEC-025 (broker determines available spread data), DATA_VENDOR_MIGRATION_PLAN.md (real-time quote provider for spread calculation) |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-008: Order Type for Paper-to-Live Transition

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | EXECUTION |
| **Decision Owner** | PM |
| **Options Considered** | (A) MARKET orders -- immediate fill, maximum slippage exposure, simplest implementation. (B) LIMIT orders at mid-price with 30-second timeout -- controlled slippage, risk of missed fills. (C) LIMIT orders at ask (for buys) or bid (for sells) -- likely fill with some slippage control. (D) TWAP over 60 seconds -- minimal market impact, complex implementation, requires broker API support. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- LIMIT at ask (buy) / bid (sell) with 60-second timeout, cancel-and-resubmit if unfilled.** This guarantees the system never pays more than the displayed ask (no slippage beyond the quoted spread) while maintaining a high fill rate. MARKET orders (Option A) on low-liquidity ETPs could result in catastrophic fills. TWAP (Option D) adds implementation complexity disproportionate to the position sizes at GBP 10,000 capital. |
| **Rationale** | The ISA universe includes instruments with average daily volume under 1,000 shares. A MARKET order on these instruments could move the price by multiple percent. LIMIT at the current ask/bid provides a hard ceiling on execution cost. The 60-second timeout prevents stale orders from sitting in the book. |
| **Dependencies** | DEC-025 (broker API determines available order types), DEC-007 (spread gate pre-filters unacceptable spreads) |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-009: Maximum Time-in-Trade for Intraday Scalps

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | EXECUTION |
| **Decision Owner** | PM |
| **Options Considered** | (A) 30-minute hard cap -- forces quick resolution, may cut winners short. (B) 60-minute hard cap -- balanced between opportunity capture and risk exposure. (C) 120-minute hard cap -- allows larger moves to develop, increases drawdown risk. (D) No time cap -- position held until stop, target, or market close. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option B -- 60-minute hard cap for intraday scalps.** The S15 daily target strategy seeks a single 2% move. On 3x leveraged ETPs, a 2% move on the ETP requires a ~0.67% move on the underlying, which typically occurs within 30-60 minutes during active sessions if the setup is valid. A 120-minute cap (Option C) exposes the position to regime changes and lunchtime reversals. No time cap (Option D) is unsafe for leveraged instruments that decay with time. |
| **Rationale** | Time is the enemy of leveraged ETP positions due to volatility decay and compounding drag. The 60-minute cap also prevents the system from holding through scheduled macro events (FOMC, CPI) that may begin after entry. The learning engine's trade_autopsy.py module will track time-to-target to refine this parameter empirically. |
| **Dependencies** | DEC-006 (slippage on forced exit at time cap), trade_autopsy.py empirical data |
| **Date Resolved** | Target: after 90 paper trading days with time-in-trade distribution data |

---

### DEC-010: Learning Engine Promotion Pipeline

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | GOVERNANCE |
| **Decision Owner** | IC |
| **Options Considered** | (A) Fully automatic promotion: learning engine adjusts parameters when statistical thresholds are met, no human in the loop. (B) Semi-automatic: learning engine proposes adjustments, human (IC) must approve each before activation. (C) Manual only: learning engine logs recommendations, all changes require manual implementation by engineer + IC sign-off. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- Manual only.** The learning engine logs all recommendations to `data/learning_proposals.jsonl` with full statistical backing, but no parameter change takes effect without manual implementation and IC sign-off. This is the only option that prevents runaway self-modification. The existing guardrails (max_changes_per_week: 3, failures_to_lock: 3) are insufficient without human oversight because the guardrails themselves are parameters the meta-learner could eventually learn to circumvent. |
| **Rationale** | LEARNING_LOOP_PLAN.md Section 1 states the learning loop must not make "unsupervised changes." Option A directly violates this. Option B creates approval fatigue risk (human rubber-stamps proposals). Option C forces full deliberation. The cost is slower adaptation; the benefit is zero risk of autonomous parameter corruption. |
| **Dependencies** | DEC-011 (which knobs are adjustable at all), LEARNING_LOOP_PLAN.md (binding), SELF_HEALING_OPS_SPEC.md (boundary between auto and human actions) |
| **Date Resolved** | Target: before learning engine influences any live trading parameter |

---

### DEC-011: Meta-Learner Bounded Parameter Knobs

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | GOVERNANCE |
| **Decision Owner** | IC |
| **Options Considered** | (A) All parameters adjustable by meta-learner within bounds. (B) Whitelist: only indicator weights and confidence thresholds adjustable; risk parameters, universe, and execution parameters IC-locked. (C) Everything IC-locked; meta-learner is observation-only. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- Everything IC-locked; meta-learner is observation-only.** Until DEC-010 is resolved, the meta-learner operates in pure observation mode: it tracks indicator predictiveness, computes optimal weights, and logs proposals, but changes nothing. This aligns with `settings.yaml` existing `risk_rules_touchable: false` and prevents any automated parameter modification. |
| **Rationale** | The meta-learner's value during paper trading is in DATA COLLECTION, not in parameter adjustment. It should be learning what works, building statistical confidence, and generating a queue of proposals for IC review. Allowing any autonomous adjustment (even within bounds) before the system has been validated on live fills is premature. |
| **Dependencies** | DEC-010 (promotion pipeline), LEARNING_LOOP_PLAN.md, guardrails.py existing constraints |
| **Date Resolved** | Target: concurrent with DEC-010 |

---

### DEC-012: Monitoring Stack Selection

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) AWS CloudWatch -- native to EC2, minimal setup, $3-5/month estimated, limited custom dashboards. (B) Prometheus + Grafana -- self-hosted on EC2, zero incremental cost, full customisation, operational overhead of maintaining two more containers. (C) Datadog -- SaaS, richest features, $15-23/month for infrastructure monitoring, zero maintenance. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- CloudWatch.** The system already runs on EC2. CloudWatch requires zero additional containers, zero additional attack surface, and zero additional maintenance. Custom metrics can be pushed via the CloudWatch agent or boto3. While Grafana (Option B) offers richer dashboards, the War Room dashboard already provides trading-specific visualisation. CloudWatch covers infrastructure monitoring (CPU, memory, disk, network) which is the gap the War Room does not fill. |
| **Rationale** | At the current scale (single EC2 instance, 2 Docker containers), the operational overhead of self-hosted Prometheus+Grafana (Option B) or the cost of Datadog (Option C) is not justified. CloudWatch's 5-minute default granularity is sufficient for infrastructure monitoring. The War Room provides 5-second trading telemetry. Upgrade to Prometheus+Grafana if/when the system scales to multiple instances. |
| **Dependencies** | EC2_DOCKER_DRIFT_GUARDS.md, WAR_ROOM_REQUIREMENTS_SPEC.md (trading telemetry already covered) |
| **Date Resolved** | Target: 2026-03-15 |

---

### DEC-013: On-Call Rotation Model

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Head of Ops |
| **Options Considered** | (A) Single operator (system owner) is sole on-call, 24/7 during trading hours. (B) Two-person rotation -- primary and secondary, weekly rotation. (C) No formal on-call; operator monitors during business hours, system self-heals overnight. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Single operator.** The NZT-48 system has one operator (the PM/system owner). Formal rotation (Option B) requires a second trained operator who does not exist. Option C (no on-call) is unsafe for live trading. During paper trading, Option A is sufficient because the kill switch (`data/KILL_SWITCH` file) and circuit breakers provide automated protection outside of active monitoring hours. |
| **Rationale** | This is a single-person operation at present. Creating artificial processes for a non-existent team adds bureaucratic overhead without safety benefit. The self-healing operations spec (SELF_HEALING_OPS_SPEC.md) covers automated recovery for the scenarios most likely to occur outside active monitoring. For live trading, a second trained operator should be recruited BEFORE migration (prerequisite for DEC-018). |
| **Dependencies** | DEC-014 (SEV-1 SLA), DEC-018 (live migration), SELF_HEALING_OPS_SPEC.md |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-014: SEV-1 Incident Response SLA

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Head of Ops |
| **Options Considered** | (A) 5-minute acknowledgement, 15-minute resolution or kill switch activation. (B) 15-minute acknowledgement, 30-minute resolution or kill switch activation. (C) 30-minute acknowledgement, 60-minute resolution or kill switch activation. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option B -- 15-minute ack / 30-minute resolution-or-kill.** A 5-minute SLA (Option A) is unrealistic for a single operator who may be asleep, commuting, or otherwise unreachable. A 30-minute ack (Option C) allows too much time for a live position to deteriorate on leveraged instruments. 15 minutes is the maximum acceptable time between a SEV-1 alert firing and an operator acknowledging it. If resolution is not achievable within 30 minutes, the kill switch MUST be activated -- an unresolved SEV-1 with open positions is categorically unacceptable. |
| **Rationale** | On 3x leveraged ETPs, a 30-minute adverse move in a SEV-1 scenario (e.g., data feed failure + stale prices) could represent 5-10% capital at risk. The 15/30 SLA balances human availability constraints against capital protection. For paper trading, the SLA is aspirational. For live trading, it is binding and must be met 100% of the time -- failure to meet SLA triggers mandatory review per OPS_GOVERNANCE_PLAN.md Section 4. |
| **Dependencies** | DEC-013 (on-call model), DEC-020 (kill switch SLA), OPS_GOVERNANCE_PLAN.md |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-015: Container Image Tagging Strategy

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) Git SHA-based tags (e.g., `nzt48:abc1234`) -- unique, traceable, not human-readable. (B) Semantic versioning (e.g., `nzt48:2.1.3`) -- human-readable, requires version bump discipline. (C) Date-based tags (e.g., `nzt48:20260227-143000`) -- sortable, traceable to deployment time, no version semantics. (D) Hybrid: semver for releases + git SHA for every build + `latest` as mutable pointer. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option D -- Hybrid (semver + git SHA + latest).** Every Docker build is tagged with both the git SHA (for traceability) and a semver tag (for human communication). The `latest` tag always points to the most recent build. This ensures the run manifest (`environment.docker_sha` and `environment.git_commit`) can always be correlated to the exact image. Semver provides the human-friendly label for changelogs and rollback communication. |
| **Rationale** | Git SHA alone (Option A) is unambiguous but poor for communication ("roll back to abc1234" vs "roll back to v2.1.2"). Semver alone (Option B) requires manual discipline. Date-based (Option C) provides no semantic information about what changed. The hybrid approach costs nothing extra and provides all three benefits. ROLLBACK_PLAN.md already references `lkg-*` git tags, which aligns with this approach. |
| **Dependencies** | DEC-026 (Docker registry), ROLLBACK_PLAN.md (LKG tag convention) |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-016: TradingView Data Usage Legality

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | COMPLIANCE |
| **Decision Owner** | PM |
| **Options Considered** | (A) Continue scraping TradingView (current state, if applicable). (B) Use TradingView's official API/widget embedding (licensed). (C) Remove TradingView entirely and rely on licensed providers (Polygon.io, IBKR). |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- Remove entirely.** DATA_VENDOR_MIGRATION_PLAN.md Section 2.2 identifies scraping as a critical vulnerability. Any web scraping of TradingView violates their Terms of Service and creates legal exposure. TradingView's official API is not available at retail scale. All data needs are served by Polygon.io (US) and IBKR (LSE) after DEC-004 and DEC-017 are implemented. There is no data gap that requires TradingView. |
| **Rationale** | Zero legal risk tolerance for data sourcing in a system managing real capital. Every scraping dependency is a ticking legal and operational liability. The migration to licensed providers (Polygon.io + IBKR) closes all gaps that TradingView might have filled. |
| **Dependencies** | DEC-004 (Polygon.io approved), DEC-017 (LSE coverage), DATA_VENDOR_MIGRATION_PLAN.md |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-017: Polygon.io LSE Coverage and Fallback

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | DATA |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) Polygon.io covers LSE at current tier -- confirmed via API testing. (B) Polygon.io does NOT cover LSE at $29/month tier -- upgrade to Global tier (estimated $199-299/month). (C) Polygon.io does NOT cover LSE -- use IBKR TWS API as primary for all `.L` tickers, Polygon.io for US only. (D) Polygon.io does NOT cover LSE -- use yfinance as temporary LSE bridge while IBKR integration is built. |
| **Decision Taken** | PENDING -- requires API testing to confirm Polygon.io LSE coverage at the Stocks Starter tier |
| **Safest Default** | **Option C -- IBKR as primary for LSE, Polygon.io for US only.** Polygon.io's Stocks Starter plan ($29/month) is documented as covering US exchanges. LSE coverage is unconfirmed and likely requires a higher tier. IBKR provides real-time LSE data as part of the brokerage relationship (market data subscription ~$4.50/month for LSE Level 1). This is the lowest-cost, lowest-risk path that guarantees LSE coverage. yfinance (Option D) is unacceptable as anything other than last-resort fallback per DATA_VENDOR_MIGRATION_PLAN.md. |
| **Rationale** | The ISA universe is 100% LSE instruments. If Polygon.io cannot serve LSE data, the entire core mission depends on a different provider. IBKR is the natural choice because the live trading broker (DEC-025) will likely be IBKR anyway, bundling data and execution. |
| **Dependencies** | DEC-004 (Polygon.io approved for US), DEC-025 (broker selection), DATA_VENDOR_MIGRATION_PLAN.md |
| **Date Resolved** | Target: 2026-03-07 (requires Polygon.io API test) |

---

### DEC-018: Capital Allocation for LIMITED LIVE Gate

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | RISK |
| **Decision Owner** | IC |
| **Options Considered** | (A) 5% of capital (GBP 500) allocated to limited live trading. (B) 10% of capital (GBP 1,000) allocated to limited live trading. (C) 20% of capital (GBP 2,000) allocated to limited live trading. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- 5% of capital (GBP 500).** The purpose of LIMITED LIVE is to validate execution quality, slippage assumptions, and broker integration -- NOT to generate returns. GBP 500 is sufficient to execute meaningful trades on most CORE ISA ETPs (share prices range from GBP 5 to GBP 150) while limiting maximum loss to a level that does not impair the full capital deployment. If 5% performs as expected, escalation to 10% then 20% follows a defined progression. |
| **Rationale** | OPS_GOVERNANCE_PLAN.md Section 3 defines the paper-to-live migration gates. The LIMITED LIVE stage exists to validate assumptions, not to trade profitably. Every paper trading assumption (slippage, fill rate, spread behaviour) must be confirmed with real fills before full capital deployment. Starting with the minimum viable allocation protects against the scenario where paper-to-live assumptions are materially wrong. |
| **Dependencies** | DEC-006 (slippage model validation), DEC-007 (spread gating), DEC-008 (order type), DEC-025 (broker), OPS_GOVERNANCE_PLAN.md |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-019: Maximum Concurrent Positions

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | RISK |
| **Decision Owner** | Head of Risk |
| **Options Considered** | (A) Maximum 2 concurrent positions. (B) Maximum 3 concurrent positions. (C) Maximum 5 concurrent positions. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Maximum 2 concurrent positions.** With GBP 10,000 starting capital, concentration risk is the dominant threat. Two concurrent positions at 50% allocation each provide meaningful diversification without spreading capital too thin for position sizing to be effective. On leveraged ETPs, two correlated positions (e.g., QQQ3.L and NVD3.L) can produce combined drawdowns exceeding the underlying move. The S15 strategy is designed to find ONE best trade per day -- multiple concurrent positions suggest the system is over-trading. |
| **Rationale** | The 2% daily compounding thesis requires ONE high-conviction trade per day, not a portfolio of mediocre ones. Allowing 5 concurrent positions (Option C) at GBP 10,000 means GBP 2,000 per position, which may be below the minimum effective size for some ETPs when accounting for spread + slippage + commission. The learning engine's correlation_tracker.py will monitor cross-position correlation to inform future adjustment. |
| **Dependencies** | DEC-018 (capital allocation for live), DEC-006 (slippage per position), UNIVERSE_GOVERNANCE_PLAN.md (correlation constraints) |
| **Date Resolved** | Target: before paper-to-live migration gate |

---

### DEC-020: Kill Switch Activation SLA

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | RISK |
| **Decision Owner** | Head of Risk |
| **Options Considered** | (A) Immediate (< 1 second) -- automated kill switch triggered by circuit breaker conditions, no human latency. (B) 30-second SLA -- automated detection + human confirmation before kill. (C) 60-second SLA -- human-initiated only after alert review. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Immediate automated kill switch (< 1 second).** When circuit breaker conditions are met (as defined in `config/settings.yaml` circuit_breakers section), the system MUST immediately: (1) cancel all open orders, (2) set position sizing to zero, (3) write `data/KILL_SWITCH` file, (4) send Telegram alert. No human confirmation is required for automated circuit breaker activation. Human-initiated kill switch (manual creation of `data/KILL_SWITCH` file) is a separate capability with no SLA -- the operator can trigger it at any time. |
| **Rationale** | On 3x leveraged instruments, every second of delay during a circuit-breaker event (e.g., VIX > 45, portfolio drawdown exceeding limit) costs real capital. Human confirmation adds 15-60 seconds of latency during the most dangerous moments. The automated kill switch is purely defensive -- it blocks new entries and cancels open orders. It does NOT close existing positions (which would require market orders during volatile conditions and could worsen outcomes). Position closure is a human decision per SELF_HEALING_OPS_SPEC.md Section 3. |
| **Dependencies** | DEC-014 (SEV-1 response SLA for post-kill human follow-up), SELF_HEALING_OPS_SPEC.md, settings.yaml circuit_breakers |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-021: PDF Delivery Channel

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | PM |
| **Options Considered** | (A) Telegram only -- current implementation, simple, mobile-friendly, limited formatting. (B) Email only -- richer formatting, archivable, slower delivery. (C) Both Telegram and email -- redundancy, different use cases (Telegram for alerts, email for archive). |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Telegram only.** The dual PDF (Momentum + Risk) is already delivered via Telegram per PDF_DESK_NOTES_SPEC.md and TELEGRAM_TAPE_SPEC.md. Adding email (Options B/C) introduces a new infrastructure dependency (SMTP server or SES), a new failure mode, and a new delivery latency. The PDF files are also written to `data/pdf_archive/` for local archival. Telegram provides immediate delivery with read receipts. |
| **Rationale** | Complexity is the enemy of reliability. Telegram delivery is working, tested, and integrated. Email adds value only when a second person needs to receive the PDFs (e.g., a compliance reviewer). Until that need arises, the additional infrastructure is not justified. |
| **Dependencies** | TELEGRAM_TAPE_SPEC.md, PDF_DESK_NOTES_SPEC.md |
| **Date Resolved** | Target: 2026-03-15 |

---

### DEC-022: Post-Mortem Cadence

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | GOVERNANCE |
| **Decision Owner** | PM |
| **Options Considered** | (A) Per-incident post-mortem within 24 hours of every SEV-1 or SEV-2 incident. (B) Weekly digest summarising all incidents, near-misses, and learning proposals. (C) Both: per-incident for SEV-1, weekly digest for SEV-2 and below. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option C -- Per-incident for SEV-1, weekly digest for all others.** SEV-1 incidents (system down, data feed failure with open positions, circuit breaker activation) require immediate root cause analysis while context is fresh. Batching SEV-1 post-mortems into a weekly digest risks losing forensic detail and delays corrective action. SEV-2 and below (degraded performance, warning-level drift alerts, missed signals) are adequately served by weekly review because they do not represent immediate capital risk. |
| **Rationale** | OPS_GOVERNANCE_PLAN.md Section 4 defines incident severity levels. The per-incident requirement for SEV-1 ensures that every capital-threatening event produces a documented root cause, corrective action, and preventive measure within 24 hours. The weekly digest provides systematic review of lower-severity issues and learning engine proposals. |
| **Dependencies** | DEC-014 (SEV-1 SLA), OPS_GOVERNANCE_PLAN.md |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-023: Feature Flag Infrastructure

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | ARCHITECTURE |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) Config file -- feature flags as boolean entries in `config/settings.yaml`, requires restart to toggle. (B) Database -- feature flags in SQLite `feature_flags` table, toggled via War Room API, no restart required. (C) LaunchDarkly or similar SaaS -- remote feature flag management, A/B testing capability, $25+/month. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Config file (`settings.yaml`).** ROLLBACK_PLAN.md Section 3 already defines feature flags as entries in `settings.yaml`. The system already supports config hot-reload via the self-healing operations spec (HEAL-CFG action). Adding a database table (Option B) or SaaS dependency (Option C) adds complexity for a feature set that currently needs only ~15 boolean flags. A restart (or config reload) to toggle a flag is a 30-second operation on a single-instance system. |
| **Rationale** | The simplest solution that works. `settings.yaml` is version-controlled, diff-able, and already understood by every component. Database flags (Option B) would require a new API endpoint, War Room UI panel, and migration logic. LaunchDarkly (Option C) adds an external SaaS dependency to a trading system that should minimise external failure modes. If the system scales to multiple instances or requires instant flag toggling without config reload, upgrade to Option B. |
| **Dependencies** | ROLLBACK_PLAN.md (flag definitions), SELF_HEALING_OPS_SPEC.md (HEAL-CFG reload) |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-024: Backtest Framework Selection

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | ARCHITECTURE |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) vectorbt -- already installed (`vectorbt-0.28.4`), fast vectorised backtesting, good for parameter sweeps, limited event-driven support. (B) zipline -- Quantopian's framework, full event-driven simulation, requires data bundle management, heavier setup. (C) Custom framework -- purpose-built for NZT-48's specific signal pipeline, exact replication of live logic, highest development cost. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- vectorbt.** It is already installed in the project's virtual environment (`venv/lib/python3.12/site-packages/vectorbt-0.28.4`). It provides fast vectorised backtesting suitable for the primary use case: validating S15's 2% daily target across historical data with different parameter sets. The key requirement is not a full portfolio simulator but a fast engine for single-strategy parameter optimisation. vectorbt meets this need with zero additional dependencies. |
| **Rationale** | The backtest framework is used for strategy validation and parameter sensitivity analysis, not for full portfolio simulation. vectorbt's vectorised approach is well-suited for scanning large parameter spaces quickly. Custom (Option C) would provide the most accurate simulation of the live pipeline but requires significant development time with no incremental benefit over vectorbt for the current use case. zipline (Option B) adds unnecessary complexity for single-strategy backtesting. |
| **Dependencies** | HISTORICAL_BACKFILL_PLAN.md (data availability for backtests), TEST_PLAN.md |
| **Date Resolved** | Target: 2026-03-15 |

---

### DEC-025: ISA Broker for Live Trading

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | EXECUTION |
| **Decision Owner** | PM |
| **Options Considered** | (A) Interactive Brokers (IBKR) -- institutional-grade API (TWS/IB Gateway), LSE access, ISA account available, complex setup, minimum activity fees may apply. (B) Trading 212 -- mobile-first, ISA account, free trading, limited API (unofficial/community), gamified UX. (C) IG -- spread betting and CFD focus, ISA via share dealing, API available, higher commissions on ETP trades. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- IBKR.** It is the only option with a documented, production-grade API suitable for algorithmic trading. Trading 212 (Option B) has no official API, meaning any integration depends on reverse-engineered endpoints that can break without notice. IG (Option C) is primarily a spread betting platform; its share dealing ISA has higher per-trade costs and a less mature API. IBKR's TWS API is the industry standard for retail algorithmic trading and is already referenced in DATA_VENDOR_MIGRATION_PLAN.md as the primary LSE data provider. |
| **Rationale** | The system requires: (1) ISA account eligibility, (2) LSE access for all CORE ETPs, (3) a production-grade API for order submission, (4) real-time market data for LSE, (5) reasonable commission structure at GBP 10,000 scale. Only IBKR satisfies all five requirements. The integration complexity is higher than Trading 212 but the reliability and feature set justify it for a system managing real capital. |
| **Dependencies** | DEC-008 (order types available via IBKR API), DEC-017 (IBKR as LSE data provider), DEC-018 (capital allocation) |
| **Date Resolved** | Target: 2026-03-15 |

---

### DEC-026: Docker Registry Selection

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) Amazon ECR -- native to AWS, private, $0.10/GB/month storage, IAM authentication, close to EC2. (B) Docker Hub -- free tier (1 private repo), familiar, public by default, rate limits on free tier. (C) Self-hosted registry on EC2 -- zero external dependency, adds operational overhead, consumes EC2 storage. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option A -- Amazon ECR.** The system runs on EC2. ECR is in the same AWS ecosystem, uses IAM for authentication (no additional credentials), and has negligible cost at the current scale (a single Docker image under 2GB = ~$0.20/month). Docker Hub (Option B) defaults to public repos (security risk for a trading system) and has pull rate limits that could block deployment during an incident. Self-hosted (Option C) adds another service to maintain on a single EC2 instance already running the trading engine and dashboard. |
| **Rationale** | ECR provides private-by-default, IAM-authenticated, low-latency image storage within the existing AWS account. The cost is effectively zero. It also enables image scanning for vulnerabilities, which is a free ECR feature. |
| **Dependencies** | DEC-015 (tagging strategy), EC2_DOCKER_DRIFT_GUARDS.md |
| **Date Resolved** | Target: 2026-03-10 |

---

### DEC-027: Secrets Management

| Field | Value |
|-------|-------|
| **Status** | OPEN |
| **Category** | OPERATIONS |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) AWS Secrets Manager -- managed, encrypted, API-accessible, $0.40/secret/month, automatic rotation. (B) `.env` file on EC2 -- current approach (implied by API key references in settings.yaml), simple, no rotation, stored on disk. (C) HashiCorp Vault -- enterprise-grade, self-hosted or SaaS, overkill for current scale. |
| **Decision Taken** | PENDING |
| **Safest Default** | **Option B -- `.env` file with restricted permissions (chmod 600, owned by root).** This is the current approach and it works for a single-instance system. The secrets involved are: Polygon.io API key, Finnhub API key, Twelve Data API key, FMP key, NewsAPI key, Telegram bot token, and (future) IBKR credentials. AWS Secrets Manager (Option A) adds $2-3/month and API call latency on every startup. Vault (Option C) is enterprise infrastructure for an enterprise problem that does not exist here. |
| **Rationale** | The threat model for a single-operator, single-instance trading system does not justify managed secrets infrastructure. The `.env` file is never committed to git (verified via `.gitignore`), resides on a private EC2 instance accessible only via SSH key, and is readable only by root. When the system scales to multiple instances or operators, upgrade to AWS Secrets Manager. The critical rule is: **no secret may appear in `settings.yaml`, `docker-compose.yml`, or any committed file.** |
| **Dependencies** | EC2_DOCKER_DRIFT_GUARDS.md, DEC-025 (IBKR credentials will be the most sensitive secret) |
| **Date Resolved** | Target: 2026-03-10 |

---

## DEFERRED DECISIONS

---

### DEC-028: Dashboard Authentication Model

| Field | Value |
|-------|-------|
| **Status** | DEFERRED |
| **Category** | OPERATIONS |
| **Decision Owner** | Lead Engineer |
| **Options Considered** | (A) No authentication -- current state, acceptable for paper trading on localhost/private EC2. (B) Basic auth (username/password via nginx reverse proxy). (C) SSO/OAuth via AWS Cognito or similar. |
| **Decision Taken** | DEFERRED -- Not required until live trading or external access is enabled. |
| **Safest Default** | **Option A -- No authentication (paper trading only).** WAR_ROOM_REQUIREMENTS_SPEC.md states that read endpoints are open (localhost only via middleware). The War Room is accessed via SSH tunnel to EC2 (port 3001 is not exposed publicly). State-mutating endpoints already require `X-API-Key`. This is sufficient for paper trading with a single operator. Authentication becomes a requirement ONLY when: (1) the dashboard is exposed to the internet, OR (2) live trading with real capital begins. |
| **Rationale** | Adding authentication to a localhost-only dashboard consumed via SSH tunnel adds friction without security benefit. The X-API-Key on mutating endpoints prevents accidental state changes. This decision is explicitly deferred until the trigger conditions above are met. |
| **Dependencies** | DEC-018 (live migration triggers authentication requirement), WAR_ROOM_REQUIREMENTS_SPEC.md |
| **Date Resolved** | Deferred. Trigger: live trading migration or public dashboard exposure. |

---

### DEC-029: Data Retention Policy

| Field | Value |
|-------|-------|
| **Status** | DEFERRED |
| **Category** | GOVERNANCE |
| **Decision Owner** | PM |
| **Options Considered** | (A) 30 days -- minimal storage, insufficient for seasonal analysis. (B) 90 days -- one quarter, adequate for short-term edge analysis. (C) 1 year -- full seasonal cycle, enables year-over-year comparison. (D) Unlimited -- keep everything, rely on storage scaling. |
| **Decision Taken** | DEFERRED -- Retain all data until storage becomes a constraint. |
| **Safest Default** | **Option D -- Unlimited retention.** At the current data generation rate (SQLite database, JSONL logs, PDF archives, run manifests), the system produces approximately 50-100 MB per month. EC2 gp3 EBS storage costs $0.08/GB/month. A full year of data costs less than $1.00 to store. There is no reason to delete data that the learning engine may need for long-horizon analysis. Implement a retention policy only when storage exceeds 50 GB or regulatory requirements mandate data purging. |
| **Rationale** | Data is the learning engine's fuel. Deleting it prematurely impairs the system's ability to detect seasonal patterns, long-duration edge decay, and regime cycle lengths. The storage cost is negligible. This decision is deferred because there is no urgency -- the safest action is to keep everything. |
| **Dependencies** | LEARNING_LOOP_PLAN.md (data requirements for edge ledger), EC2_DOCKER_DRIFT_GUARDS.md (disk monitoring) |
| **Date Resolved** | Deferred. Trigger: storage exceeds 50 GB or regulatory requirement emerges. |

---

### DEC-030: Inverse ETP Signal Handling

| Field | Value |
|-------|-------|
| **Status** | DEFERRED |
| **Category** | EXECUTION |
| **Decision Owner** | IC |
| **Options Considered** | (A) Allow SHORT signal type on regular (long) ETPs -- system can express bearish views by shorting long ETPs. (B) Only LONG on inverse ETPs -- bearish views expressed by buying inverse products (QQQS.L, 3USS.L). (C) Both SHORT on long ETPs AND LONG on inverse ETPs -- maximum flexibility, highest complexity. |
| **Decision Taken** | DEFERRED -- ISA accounts cannot short. Only Option B is legally possible within the ISA wrapper. |
| **Safest Default** | **Option B -- LONG-only on both regular and inverse ETPs.** UK ISA regulations prohibit short selling. Bearish views can only be expressed by purchasing inverse ETPs (e.g., QQQS.L for short Nasdaq, 3USS.L for 3x short S&P 500). SCOPE_ALIGNMENT_AUDIT.md confirms that S15 enforces `direction = LONG` at line 211 of `daily_target.py`. This is not a design choice but a regulatory constraint. |
| **Rationale** | This decision is deferred because there is only one legally permissible option within an ISA. If the system ever operates outside an ISA wrapper (e.g., a separate GIA or SIPP account), Options A and C become viable and this decision must be reopened. The inverse ETPs (QQQS.L, 3USS.L) are already in the CORE ISA universe, so bearish positioning capability exists today without short selling. |
| **Dependencies** | SCOPE_ALIGNMENT_AUDIT.md (LONG-only constraint), UNIVERSE_GOVERNANCE_PLAN.md (inverse ETPs in CORE tier), ISA regulations |
| **Date Resolved** | Deferred. Trigger: non-ISA account trading considered. |

---

## DECISION DEPENDENCY MAP

The following diagram shows critical-path dependencies between OPEN decisions. An arrow from A to B means "A must be resolved before B can be finalised."

```
DEC-025 (Broker) ──────┬──> DEC-008 (Order Type)
                        ├──> DEC-017 (LSE Data)
                        └──> DEC-027 (Secrets - IBKR creds)

DEC-017 (LSE Data) ────┬──> DEC-004 (Polygon scope confirmed)
                        └──> DEC-006 (Slippage - needs real feed)

DEC-006 (Slippage) ────┬──> DEC-007 (Spread Gate)
                        └──> DEC-018 (Capital Allocation)

DEC-018 (Capital) ─────┬──> DEC-019 (Max Positions)
                        └──> DEC-013 (On-Call - live requires)

DEC-010 (Promotion) ───┬──> DEC-011 (Bounded Knobs)
                        └──> (blocks learning engine live influence)

DEC-014 (SEV-1 SLA) ──┬──> DEC-020 (Kill Switch SLA)
                        └──> DEC-022 (Post-Mortem Cadence)

DEC-015 (Image Tags) ──┬──> DEC-026 (Registry)
                        └──> ROLLBACK_PLAN.md (LKG convention)
```

---

## PAPER-TO-LIVE MIGRATION BLOCKERS

The following OPEN decisions MUST be resolved before paper-to-live migration can proceed. They are listed in recommended resolution order:

| Priority | DEC-ID | Title | Reason It Blocks |
|----------|--------|-------|------------------|
| 1 | DEC-025 | ISA Broker Selection | Cannot execute live trades without a broker |
| 2 | DEC-017 | Polygon.io LSE Coverage | Cannot feed live prices without confirmed LSE data source |
| 3 | DEC-008 | Order Type | Cannot submit orders without defined execution method |
| 4 | DEC-006 | Slippage Model | Cannot validate paper PnL against live expectations |
| 5 | DEC-007 | Spread Gate | Cannot protect against adverse fills without spread filter |
| 6 | DEC-018 | Capital Allocation | Cannot size the limited live gate |
| 7 | DEC-019 | Max Concurrent Positions | Cannot enforce position limits without a defined cap |
| 8 | DEC-020 | Kill Switch SLA | Cannot operate live without automated capital protection |
| 9 | DEC-014 | SEV-1 SLA | Cannot operate live without defined incident response |
| 10 | DEC-010 | Learning Engine Promotion | Must be locked to manual-only before live capital at risk |

---

## REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | System Architecture Review | Initial register: 5 resolved, 22 open, 3 deferred |

---

*End of Decision Register*
