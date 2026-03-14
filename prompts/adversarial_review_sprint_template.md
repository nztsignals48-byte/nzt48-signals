# NZT-48 ADVERSARIAL SPRINT REVIEW PROMPT

> **REUSABLE TEMPLATE**: This prompt is used after every sprint. To reuse: replace the "SPRINT UNDER REVIEW" section with the new sprint's fixes, update the "PRIOR SPRINTS" section to include all previously completed sprints, and update any architecture facts that changed. Everything else stays the same.

---

## SYSTEM CONTEXT

**NZT-48** is an automated UK ISA trading engine running on AWS EC2 (c7i-flex.large, 4GB RAM, us-east-1). It trades 12 LSE-listed leveraged ETPs (3x and 5x) in paper mode with £10,000 starting equity.

**Infrastructure**: Docker Compose with 3 containers:
- `nzt48` — Python engine + FastAPI (port 8000), APScheduler running continuous 60-second scan cycle 24/7
- `ib-gateway` — Interactive Brokers Gateway + IBC (port 4002, paper trading)
- `nzt48-redis` — Redis for state persistence (internal Docker network only, password-protected)

**Core Strategy — S15 "2% Daily Target"**: The compounding machine. Scans all 12 ISA tickers every 60 seconds during LSE market hours. Computes its own confidence score: `confidence = 40.0 + (total_score * 55.0)` producing a range of 40-95. This BYPASSES the 5-layer ConfidenceScorer used by other strategies. S15 is the only active strategy; all others are dormant.

**12 ISA Tickers**: QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L

**Key Architecture**:
- **Data pipeline**: IBKR primary source → yfinance silent fallback, orchestrated by DataHub
- **IndicatorSnapshot**: Each ticker's data snapshot includes a `timestamp: datetime` field used for freshness checking
- **Signal model**: Every signal gets a unique 12-character ID via `id=str(uuid.uuid4())[:12]`
- **SessionProtection staircase** (cumulative daily P&L thresholds):
  - +0.5% → require confidence ≥ 70
  - +1.0% → require confidence ≥ 75, max position size 0.75
  - +1.5% → require confidence ≥ 85, max position size 0.5, max 1 trade
  - +2.0% → HALT all trading for the day
- **RegimeClassifier** (VIX-based):
  - VIX > 45 → SHOCK (position size multiplier 0.0, EMERGENCY FLATTEN all positions)
  - VIX > 35 → RISK_OFF (position size multiplier 0.25)
  - VIX > 25 → ELEVATED (reduced sizing)
  - VIX ≤ 25 → NORMAL (full sizing)
- **Codebase**: `main.py` is ~7,700 lines. `config/settings.yaml` is ~993 lines. All configuration is YAML-driven.
- **Deployment**: `bash scripts/deploy_to_ec2.sh` rebuilds and deploys. Logs via `docker logs nzt48 --tail N`.

---

## PRIOR SPRINTS (COMPLETED)

### Sprint 0 — 7 fixes deployed
1. **VIX fail-closed default**: Changed from 0.0 (treated as calm, allowed max risk) to 99.0 (treated as extreme shock). Intent: fail-closed on VIX data loss. Later adjusted in Sprint 0.5.
2. **Strategy confidence threshold**: Lowered from 75 to 65 to allow more signals through (S15 was being filtered too aggressively).
3. **Risk sizer confidence threshold**: Raised from 60 to 65 to align with strategy threshold (was allowing signals the strategy layer would reject).
4. **Daily signal cap**: Changed from boolean flag (1 signal then block) to count-based system (max 3 signals per day).
5. **+1.5% halt behavior**: Changed from full halt to tightened trading (position size 0.5, confidence requirement 85, max 1 trade). Full halt now at +2.0%.
6. **Timezone correction**: Changed from US/Eastern to Europe/London. System trades LSE, not NYSE.
7. **List mutation fix**: Was modifying a list while iterating over it. Fixed to collect items first, remove after loop.

---

## SPRINT UNDER REVIEW: Sprint 0.5

Sprint 0.5 was triggered by triaging findings from 3 independent adversarial reviews (Claude, Gemini, ChatGPT) of Sprint 0. Seven fixes were identified, implemented, deployed to the running EC2 container, and verified.

### Fix 1: VIX Default Value — 99.0 → 35.0
**Problem**: Sprint 0 set VIX fail-closed to 99.0. But VIX=99 triggers SHOCK regime (VIX>45), which sets position size to 0.0 and triggers EMERGENCY FLATTEN on all open positions. Any momentary VIX data hiccup (API timeout, yfinance rate limit, network blip) would cause the system to liquidate every position at market — a self-inflicted denial-of-service.
**Fix**: Default changed to 35.0. This triggers RISK_OFF regime (VIX>35), which reduces position size to 0.25x. The system remains cautious but tradeable. No emergency flattening.
**Trade-off**: If VIX data is truly unavailable during an actual market crash (real VIX > 45), the system would trade at 0.25x sizing instead of flattening. Accepted risk: 0.25x of a bad trade is survivable; flattening on every data hiccup is not.

### Fix 2: Signal Veto — Ticker-Based → Signal.ID-Based
**Problem**: The daily signal veto system tracked which tickers had already generated signals. If S15 emitted a signal for QQQ3.L, the veto blocked ALL future signals for QQQ3.L that day — including from different strategies (if they were active) or different S15 scoring conditions. This caused collateral damage.
**Fix**: Veto now tracks `signal.id` (the unique 12-character UUID) instead of `signal.ticker`. Each signal is vetoed individually. Multiple strategies CAN emit signals for the same ticker. The daily signal count cap (max 3) still applies as a separate gate.
**Architecture note**: Signal IDs are generated as `str(uuid.uuid4())[:12]` — 12 hex characters from a v4 UUID, giving ~2.8 × 10^14 possible IDs. Collision probability is negligible for the ~10-50 signals per day this system generates.

### Fix 3: reqMarketDataType(1) — Explicit Real-Time Data Request
**Problem**: IBKR Gateway defaults to delayed data (15-minute delay) unless explicitly told to use real-time. The system was not specifying data type, so it may have been receiving delayed quotes without knowing it. For a 60-second scan cycle trading leveraged 3x/5x ETPs, 15-minute-old data is catastrophic.
**Fix**: Added `reqMarketDataType(1)` (1 = real-time streaming) to both IBKR connection points: `ibkr_source.py` (the data source) and `ibkr_gateway.py` (the order gateway). Called immediately after connection establishment.
**Caveat**: reqMarketDataType(1) requires active market data subscriptions in the IBKR account. If subscriptions lapse, IBKR will reject the request silently and may fall back to delayed data. The system does not currently verify that real-time data was actually granted.

### Fix 4: Data Freshness Gate in S15
**Problem**: S15 scanned all 12 tickers every 60 seconds but never checked whether the data was actually fresh. A stale IndicatorSnapshot (from a failed update, frozen feed, or weekend cache) would be scored as if it were live data, potentially generating signals based on hours-old or days-old prices.
**Fix**: Two-level freshness gate added to S15's scan loop:
- **Global gate**: Before scanning, check if ALL tickers are stale (timestamp > 120 seconds old). If so, refuse the entire scan cycle and log a warning. This catches systemic data pipeline failures.
- **Per-ticker gate**: During scoring, skip any individual ticker whose IndicatorSnapshot timestamp is > 120 seconds old. Log which tickers were skipped. Continue scoring the remaining tickers.
**Threshold**: 120 seconds chosen because: scan cycle is 60s, so data should be at most ~60s old. 120s gives a 2x buffer for transient delays. Anything older indicates a genuine data problem.

### Fix 5: VIX Default Source Field — "default_failsafe"
**Problem**: When the VIX default (35.0) is used because real VIX data is unavailable, downstream systems had no way to distinguish between "VIX is actually 35" and "VIX data failed and we're using the fallback." This matters for logging, alerting, and any future logic that might treat synthetic vs. real data differently.
**Fix**: Added a `source` field to the VIX data object. When real VIX data is available, source = the actual data provider (e.g., "ibkr", "yfinance"). When the default is used, source = "default_failsafe". Downstream systems can now filter, alert, or adjust behavior based on whether VIX data is real or synthetic.

### Fix 6: Blocking time.sleep() Fixes
**Problem**: Three files contained `run_in_executor(None, time.sleep, N)` WITHOUT `await`. This was intended to be a non-blocking sleep in an async context, but without `await`, the `run_in_executor` call was fire-and-forget — it returned a coroutine/future that was never awaited, so the sleep never actually happened. The code continued immediately, defeating the purpose of the delay (rate limiting, retry backoff, etc.).
**Fix**: Since the surrounding code was synchronous (not async), replaced with direct `time.sleep(N)` calls. The `run_in_executor` pattern is only useful in async contexts with `await`. In sync code, `time.sleep()` is the correct blocking call.
**Files affected**: 3 files (specific files identified during implementation).

### Fix 7: Deployment Verification
**All 6 fixes above deployed to running EC2 container and verified**:
- Container rebuilt and restarted
- Logs confirmed VIX default 35.0 active
- Logs confirmed reqMarketDataType(1) called on connection
- Logs confirmed data freshness checks running
- No errors in container logs post-deployment

---

## YOUR TASK: 8-SECTION ADVERSARIAL REVIEW

You are reviewing Sprint 0.5 of the NZT-48 trading system. Your job is to be brutally honest, technically rigorous, and adversarial. Do not be polite. Do not be encouraging. Find every flaw, gap, risk, and failure mode. The goal is to make this system survive real money.

---

### SECTION 1: FOUR-PERSONA REVIEW

Adopt each of the following 4 personas IN SEQUENCE. Each persona must:

**A) Verdict Table** — For each of the 7 Sprint 0.5 fixes, give one of:
- ✅ **KEEP** — Fix is correct, complete, and production-ready
- ⚠️ **ADJUST** — Fix is directionally correct but needs modification (specify what)
- 🔧 **INCOMPLETE** — Fix addresses the symptom but misses the root cause or has gaps (specify what's missing)
- ❌ **REVERT** — Fix introduces more risk than it solves (explain why)

**B) Interaction Analysis** — Identify dangerous interactions BETWEEN fixes. Example: Fix 1 (VIX=35) + Fix 4 (freshness gate) — if VIX data is stale AND ticker data is stale simultaneously, what happens? Map all pairwise interactions.

**C) Missing Items** — What should have been in Sprint 0.5 but wasn't? What blind spots do all 7 fixes share?

---

#### Persona 1: Chief Quantitative Strategist (CQ)
**Background**: 20 years at Renaissance Technologies, Two Sigma, DE Shaw. PhD in stochastic calculus. Has built and blown up multiple systematic trading systems. Thinks in terms of edge decay, alpha extraction, transaction costs, and statistical significance. Despises backtesting without walk-forward validation. Will challenge every parameter choice with "where's the evidence?"

**Focus areas**: Is S15's confidence formula sound? Are the thresholds (120s freshness, VIX=35 default, max 3 signals) empirically justified or arbitrary? What does the current architecture imply about expected Sharpe ratio? Are there hidden correlations between the 12 leveraged ETPs that the system ignores?

---

#### Persona 2: Lead Systems Architect (SA)
**Background**: 15 years building low-latency trading infrastructure at Jump Trading, Citadel Securities, and Jane Street. Expert in distributed systems, failure modes, race conditions, and observability. Has been woken up at 3am by production incidents more times than they can count. Thinks in terms of failure domains, blast radius, and graceful degradation.

**Focus areas**: Are the fixes actually deployed correctly? What happens during container restarts, network partitions, Redis failures? Is the 60-second scan cycle + 120-second freshness threshold creating a race condition? What's the observability story — can you actually tell if something is wrong from the logs alone? Are there single points of failure?

---

#### Persona 3: Chief Risk Officer (CRO)
**Background**: 25 years in risk management at Goldman Sachs, Morgan Stanley, and the Bank of England. Has survived the 2008 financial crisis, the 2010 Flash Crash, the 2015 CHF depeg, the 2020 COVID crash, and the 2021 meme stock saga. Thinks in terms of tail risk, maximum drawdown, correlation breakdown, and "what kills us." Will not accept any risk that isn't explicitly bounded.

**Focus areas**: What is the maximum possible loss in a single day under the current configuration? What happens if 3 leveraged ETPs gap down 30% simultaneously at market open? Is the SessionProtection staircase sufficient? What happens if the system generates 3 signals in rapid succession, all on correlated tickers, all before any P&L update? Is the VIX=35 default actually safe, or does it create a false sense of security?

---

#### Persona 4: Academic Reviewer (AR)
**Background**: Professor of Quantitative Finance at MIT Sloan, editor of the Journal of Financial Economics, referee for Quantitative Finance and the Journal of Portfolio Management. Has published 50+ papers on market microstructure, execution algorithms, and systematic trading. Reviews papers for mathematical rigor, statistical validity, and proper citation of prior work. Will reject any claim without a reference.

**Focus areas**: Are any of the design choices backed by peer-reviewed literature? Is the 120-second freshness threshold justified by any study on data latency in leveraged ETP markets? Is the VIX regime classification (25/35/45 thresholds) supported by empirical research? What does the academic literature say about the viability of momentum/mean-reversion strategies on 3x/5x leveraged products? Are there known results about UUID collision probability that are relevant to the signal ID design?

---

### SECTION 2: GENERAL FEEDBACK

Provide an honest, unvarnished assessment of Sprint 0.5 as a batch of changes. Address:

1. **Overall quality**: Are these fixes the right priority? Did Sprint 0.5 fix the most dangerous issues, or did it cherry-pick easy wins?
2. **Pace judgment**: Is the project moving too fast (shipping bugs), too slow (analysis paralysis), or about right?
3. **Architectural coherence**: Do the fixes make the system more or less coherent? Are they creating technical debt?
4. **Production readiness**: On a scale of 1-10, how close is this system to being trusted with real money? What's the single biggest blocker?
5. **Team assessment**: Based on the quality of these fixes, what is your assessment of the engineering team's capabilities and blind spots?

---

### SECTION 3: ADVERSARIAL ATTACK VECTORS

For EACH of the 7 Sprint 0.5 fixes, answer:

**A) How would you break this fix?** Describe a specific, realistic scenario that would cause this fix to fail or produce worse outcomes than the original behavior.

**B) Compound attack path**: Describe a scenario where 2 or more fixes fail simultaneously, creating a cascading failure. What is the worst-case outcome?

**C) Exploit timeline**: If an adversary (market conditions, infrastructure failure, data provider outage) wanted to cause maximum damage, what sequence of events would they trigger, and in what order?

---

### SECTION 4: ACADEMIC RIGOR ASSESSMENT

For the Sprint 0.5 fixes specifically:

1. **Citation validity**: Are any design choices referencing or implying academic work? If so, are the citations correct and applied properly? Are they being used to justify decisions they don't actually support?
2. **Missing literature**: What relevant academic papers SHOULD have been consulted for each fix? Provide specific paper titles, authors, and years where possible.
3. **Statistical validity**: Are any of the thresholds (120s, 35.0 VIX, 3 signal cap) statistically justified? What tests should have been run before choosing these values?
4. **Methodological gaps**: What experimental methodology is missing? What should have been measured before and after each fix to verify it actually improved the system?
5. **Known results**: Are there known impossibility results or theoretical limitations that apply to this system's approach?

---

### SECTION 5: 200 POSITIVES WITH IMPROVEMENTS

List exactly 200 positive aspects of Sprint 0.5 (design choices, fixes, architecture decisions, defensive patterns, etc.). For EACH positive, provide a concrete improvement that would make it even better.

Format:
```
[Number]. POSITIVE: [What was done well]
IMPROVEMENT: [Specific, actionable enhancement]
```

Be creative and thorough. Cover: code quality, risk management, operational safety, architecture, testing, deployment, monitoring, documentation, parameter choices, failure handling, data integrity, signal processing, order management, and system design.

---

### SECTION 6: 200 NEGATIVES WITH FIXES

List exactly 200 negative aspects of Sprint 0.5 (bugs, gaps, risks, design flaws, missing features, incorrect assumptions, etc.). For EACH negative, provide a concrete fix.

Format:
```
[Number]. NEGATIVE: [What is wrong or missing]
FIX: [Specific, actionable solution]
```

Be ruthless and comprehensive. Cover: code quality, risk management, operational safety, architecture, testing, deployment, monitoring, documentation, parameter choices, failure handling, data integrity, signal processing, order management, and system design.

---

### SECTION 7: TIMING ARCHITECTURE VERDICT

The core question: **Is the data timing architecture sound enough for Sprint 1?**

Address each of the following:

1. **120-second freshness threshold**: Is this the right number? Too tight (causes false rejections during normal market microbursts)? Too loose (allows trading on stale data)? What should it be, and what evidence supports that number?

2. **reqMarketDataType(1) sufficiency**: The fix requests real-time data, but does not verify it was granted. IBKR can silently downgrade to delayed data if subscriptions lapse. What verification mechanism is needed? How should the system detect and respond to silent downgrades?

3. **60-second scan cycle vs. freshness gate**: The scan runs every 60s. Freshness threshold is 120s. This means data can be up to 119 seconds old and still pass. For 3x/5x leveraged ETPs that can move 5-10% in 2 minutes, is this acceptable? What is the actual price risk of a 119-second-old quote on a 3x leveraged ETP?

4. **Clock synchronization**: The freshness check compares `IndicatorSnapshot.timestamp` against the current system clock. What if the system clock drifts? What if IBKR's timestamp and the system's timestamp are in different timezones or have different NTP sources? What clock drift tolerance is acceptable?

5. **Weekend/overnight data**: What happens when the market is closed? Do stale IndicatorSnapshots from Friday persist into Monday? Does the freshness gate correctly handle the 64-hour gap between Friday close and Monday open? What about bank holidays?

6. **Sprint 1 readiness verdict**: Given all of the above, is the timing architecture ready for Sprint 1, or does it need more work? What specific fixes are required before proceeding?

---

### SECTION 8: DATA INTEGRITY ASSESSMENT

The core question: **Can you trust the data this system trades on?**

Address each of the following:

1. **IBKR → yfinance fallback**: The system silently falls back to yfinance if IBKR data is unavailable. yfinance data is typically 1-15 minutes delayed for LSE tickers. The system does not currently distinguish between IBKR real-time data and yfinance delayed data in its scoring. Is this acceptable? What labeling is needed?

2. **VIX data chain**: VIX comes from IBKR or yfinance. If both fail, default is 35.0 with source="default_failsafe". But VIX is a US market indicator. During LSE trading hours (8:00-16:30 London), the US VIX is from the previous close (stale by 14+ hours) until US market opens (14:30 London). Is a 14-hour-old VIX meaningful for regime classification? Should the system use VFTSE (FTSE 100 volatility index) instead?

3. **Leveraged ETP pricing quirks**: 3x and 5x leveraged ETPs have daily rebalancing, contango/backwardation effects, and can have significant NAV-to-price dislocations. Does the system account for any of these? Are there known data quality issues with LSE-listed leveraged ETPs on IBKR or yfinance?

4. **Data pipeline single points of failure**: Map every point where data can be lost, corrupted, delayed, or duplicated between the IBKR feed and the final trading signal. For each point, state whether there is currently a check, and what the consequence of failure is.

5. **Historical data integrity**: The system presumably uses historical data for indicator calculations (moving averages, RSI, etc.). How is historical data sourced? Is it the same feed as live data? Can historical data be silently revised or corrected by IBKR, causing indicator values to change retroactively?

6. **Data integrity verdict**: Given all of the above, on a scale of 1-10, how trustworthy is the data pipeline? What are the top 3 fixes needed before this system can be trusted with real money?

---

## OUTPUT FORMAT

Your response must be structured EXACTLY as the 8 sections above. Use the section headers. Use the sub-headers within each section. Do not skip any section. Do not combine sections. Do not abbreviate.

For Section 5 (200 Positives) and Section 6 (200 Negatives), you MUST provide exactly 200 items each. Not 50. Not 100. Exactly 200. Each with a concrete improvement/fix.

Total expected output: 8,000-15,000 words.

Be adversarial. Be specific. Be useful. The goal is not to praise or condemn — it is to make this system survive contact with real markets.
