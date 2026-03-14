# UNIVERSE GOVERNANCE PLAN

**Document ID:** NZT48-ANNEX-UGV-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** DRAFT — Requires sign-off before enforcement
**Scope:** Rules, procedures, and controls governing the addition, removal, tiering, and monitoring of all tickers in the NZT-48 trading universe

---

## 1. OBJECTIVE

Formalize a governance framework for the NZT-48 trading universe that ensures every ticker is classified, monitored, and subject to explicit approval/removal procedures. The universe must be stable enough for the learning loop to build statistical edge, yet flexible enough to capture new opportunities and shed decaying instruments. No ticker may enter or leave the active universe without a documented, auditable decision.

---

## 2. CURRENT STATE

### 2.1 Universe Inventory (as of 2026-02-27)

| Tier | Count | Tickers | Refresh Rate | Feature Set |
|------|-------|---------|-------------|-------------|
| **CORE ISA** | 12 | QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L | 60s | Full |
| **EXTENDED ISA** | 10 | AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3OIL.L, 3SIL.L, LLY3.L | 60s | Full |
| **EXPANSION v2** | 20 | SPXL.L, SEMI.L, 3SNV.L, 3OIL.L, PHAG.L, 3HCL.L, SOXL.L, 3LTS.L, 3STS.L, 3SIL.L, 3LNV.L, MAGS.L, AAPS.L, PHAU.L, 3GOL.L, XLKS.L, XLFS.L, XLES.L, XLUS.L, NVDS.L | TBD | TBD |
| **EXPANSION v3** | 20 | 3LNG.L, SILV.L, NGAS.L, SLV3.L, WSLV.L, GDX3.L, WGLD.L, GLD3.L, 3GOS.L, AVGS.L, CRM3.L, SOXS.L, DIS3.L, TSMS.L, 3SMS.L, UKDV.L, BATT.L, JPGL.L, ETHE.L, BTCE.L | TBD | TBD |
| **US Bot B** | 18 | NVDA, TSLA, MU, SNDK, AMD, AVGO, MRVL, ARM, TSM, ASML, SMCI, VRT, CRDO, ANET, QCOM, LRCX, KLAC, ON | 60s | Full |
| **CONTEXT** (not traded) | 8 | QQQ, SMH, SPY, SOXX, VIX, TLT, DXY, GLD | 60s | Read-only |
| **DELISTED** | 9 | (historical, no refresh) | None | Archived |
| **TOTAL ACTIVE** | **~88** | | | |

### 2.2 Current Problems

1. **No formal tier definitions.** CORE, EXTENDED, EXPANSION are ad hoc labels without defined compute budgets or feature sets.
2. **No approval process.** Tickers have been added in bulk batches (v2: 20, v3: 20) without per-ticker approval documentation.
3. **No removal process.** Delisted tickers are discovered when yfinance returns empty data; there is no proactive monitoring.
4. **No liquidity governance.** Some tickers (XLUS.L: 351 avg vol, NVDS.L: 95 avg vol) are flagged as very low liquidity but remain in the active universe with no automated position sizing adjustment enforced.
5. **No monthly audit.** Universe composition drifts without review.

---

## 3. TIER DEFINITIONS

### 3.1 Tier Architecture

```
+============================================================+
|                    TIER 1: CORE                              |
|  Max: 25 tickers  |  Refresh: 60s  |  Compute: 70%         |
|  Feature Set: FULL (all indicators, all timeframes,         |
|    multiframe analytics, predictive scoring, correlation)   |
|  Signal generation: YES  |  Position sizing: FULL           |
+============================================================+
|                    TIER 2: PEER                               |
|  Max: 50% of CORE count  |  Refresh: 180s  |  Compute: 20% |
|  Feature Set: MID (daily indicators, single timeframe,      |
|    basic scoring, no correlation engine)                     |
|  Signal generation: YES (reduced confidence weight -10)      |
|  Position sizing: 75% of CORE equivalent                    |
+============================================================+
|                    TIER 3: FULL_SCAN                          |
|  Max: 60 tickers  |  Refresh: 600s  |  Compute: 10%        |
|  Feature Set: LITE (daily close only, volume, basic trend)  |
|  Signal generation: YES (reduced confidence weight -20)      |
|  Position sizing: 50% of CORE equivalent                    |
+============================================================+
|                    TIER 4: WATCHLIST                          |
|  Max: unlimited  |  Refresh: daily  |  Compute: 0%         |
|  Feature Set: NONE (data collection only for research)      |
|  Signal generation: NO                                      |
|  Position sizing: N/A                                       |
+============================================================+
|                    QUARANTINE                                 |
|  Tickers pending removal. 30-day hold before archive.       |
|  Refresh: 600s  |  Signal generation: NO                    |
+============================================================+
```

### 3.2 Current Tier Mapping

| Current Label | Proposed Tier | Rationale |
|---------------|---------------|-----------|
| CORE ISA (12) | TIER 1: CORE | Primary trading instruments |
| EXTENDED ISA (10) | TIER 1: CORE | Same refresh/feature requirements |
| US Bot B (18) | TIER 1: CORE | Primary US trading instruments (dormant during ISA-only mode but retain tier) |
| EXPANSION v2 (20) | TIER 3: FULL_SCAN | Newly added, insufficient track record for CORE |
| EXPANSION v3 (20) | TIER 3: FULL_SCAN | Newly added, insufficient track record for CORE |
| CONTEXT (8) | Separate: CONTEXT | Not traded, reference only (retains 60s refresh for signal context) |

### 3.3 Tier Promotion/Demotion Criteria

**Promotion from FULL_SCAN to PEER:**
- Minimum 30 trading days in FULL_SCAN
- Average daily volume > 5,000
- No data quality issues (gap rate < 2%)
- At least 5 signals generated with resolved outcomes
- Manual approval required

**Promotion from PEER to CORE:**
- Minimum 60 trading days in PEER
- Average daily volume > 10,000
- Win rate on signals >= universe average
- Edge ledger shows positive expectancy in at least one bucket
- Manual approval required

**Demotion from CORE to PEER:**
- Average daily volume drops below 3,000 for 20 consecutive trading days
- Win rate on signals < 30% over rolling 30 trades
- Manual review triggered; operator decides

**Demotion from PEER to FULL_SCAN:**
- Same criteria as CORE demotion, applied at PEER thresholds
- Or: no signal generated in 60 consecutive trading days

---

## 4. ADDING TICKERS

### 4.1 Approval Requirement

Adding any ticker to any active tier (CORE, PEER, FULL_SCAN) REQUIRES explicit operator approval. The system MUST NOT auto-add tickers discovered by the LSE Registry or any other automated scan. The LSE Registry may RECOMMEND tickers; only the operator may APPROVE them.

### 4.2 Ticker Proposal Template

Every proposed ticker addition must provide the following data:

```yaml
# TICKER ADDITION PROPOSAL
proposal_date: "YYYY-MM-DD"
ticker: "XXXX.L"
direction: "LONG | SHORT"
leverage_factor: "1x | 2x | 3x"
underlying_asset: "Description of underlying"
issuer: "WisdomTree | GraniteShares | Leverage Shares | etc."
sector: "Technology | Energy | Commodities | etc."
geography: "UK_LSE | US"
avg_daily_volume_5d: 12345
avg_daily_volume_20d: 11234
liquidity_tier: "HIGH | MEDIUM | LOW | VERY_LOW"
isa_eligible: true | false
isa_eligibility_proof: "URL or reference to ISA eligibility verification"
listing_date: "YYYY-MM-DD"
delisted: false
proposed_tier: "CORE | PEER | FULL_SCAN | WATCHLIST"
rationale: "Why this ticker should be added (strategy fit, sector coverage, hedge)"
data_verified: true | false
data_source_tested: "yfinance | polygon | ibkr"
yfinance_test_result: "OK | EMPTY | PARTIAL"
notes: "Any additional context"
```

### 4.3 Approval Workflow

1. Operator or LSE Registry generates a Ticker Proposal.
2. Proposal is saved to `data/universe_proposals/{ticker}_{date}.yaml`.
3. Operator reviews the proposal.
4. If approved: ticker is added to `settings.yaml` and moved from proposal to `data/universe_approved/`.
5. If rejected: proposal is moved to `data/universe_rejected/` with rejection reason.
6. All new tickers enter at FULL_SCAN tier unless the operator explicitly overrides to a higher tier (with documented justification).

### 4.4 Batch Addition Rules

- Maximum 20 tickers per expansion batch.
- Each ticker in the batch must have its own proposal file.
- Batch additions must be accompanied by a batch summary document listing all tickers, their liquidity tiers, and the rationale for the batch.
- After a batch addition, a 5-day observation period begins. During this period, no further batch additions are permitted.

---

## 5. REMOVING TICKERS

### 5.1 Immediate Removal (No Quarantine)

A ticker is IMMEDIATELY removed (no quarantine period) if:

1. **Delisted by exchange.** The LSE or NYSE confirms the instrument is delisted.
2. **Issuer announcement.** The ETP issuer (WisdomTree, etc.) announces product termination.
3. **Data provider returns empty for 5 consecutive trading days** and manual verification confirms delisting.
4. **Regulatory prohibition.** The ticker becomes ineligible for ISA (or relevant tax wrapper).

**Process:**
- Remove from `settings.yaml`.
- Move to `data/universe_delisted/` with delist date and reason.
- Archive all historical data (do not delete from `research.db`).
- Close any open positions (paper or live) immediately.
- Update learning engine to exclude future signals for this ticker.

### 5.2 Quarantine Removal (Active Tickers)

For removing an active ticker that is NOT delisted (e.g., persistently poor performance, low liquidity):

1. Move ticker to QUARANTINE tier.
2. QUARANTINE tickers:
   - Refresh rate: 600s
   - Signal generation: DISABLED
   - No new positions may be opened
   - Existing positions may be closed but not added to
3. Quarantine period: **30 calendar days**.
4. After 30 days, the operator must explicitly confirm one of:
   - **ARCHIVE:** Remove permanently. Move to `data/universe_archived/`.
   - **REINSTATE:** Return to previous tier. Document reason for reinstatement.
   - **EXTEND:** Extend quarantine by another 30 days (max 2 extensions = 90 days total).

### 5.3 Quarantine Triggers

A ticker enters quarantine automatically if ANY of the following occur:

| Trigger | Threshold | Detection |
|---------|-----------|-----------|
| Volume collapse | Avg daily volume drops below 100 for 20 consecutive days | Daily volume check |
| Persistent data failure | Data provider returns empty/error for 10 of last 20 trading days | Data quality monitor |
| Extreme negative edge | Win rate < 15% over 20+ resolved outcomes | Edge ledger review |
| Spread explosion | Bid-ask spread > 5% consistently for 10 trading days | IBKR Level 2 data |

---

## 6. LIQUIDITY GOVERNANCE

### 6.1 Liquidity Tiers

| Tier | Avg Daily Volume | Position Size Multiplier | Notes |
|------|-----------------|------------------------|-------|
| **HIGH** | > 50,000 | 1.00x (full) | Standard trading |
| **MEDIUM** | 10,000 - 50,000 | 0.75x | Slight size reduction |
| **LOW** | 1,000 - 10,000 | 0.50x | Significant size reduction; wider stops expected |
| **VERY_LOW** | < 1,000 | 0.25x | Minimum viable position only; must verify order book before entry |

### 6.2 Liquidity Assessment Frequency

- **CORE tickers:** Reassessed every 5 trading days.
- **PEER tickers:** Reassessed every 10 trading days.
- **FULL_SCAN tickers:** Reassessed every 20 trading days.
- Reassessment uses the trailing 20-day average daily volume.

### 6.3 Liquidity-Adjusted Signal Rules

1. A signal on a VERY_LOW_LIQUIDITY ticker MUST have confidence >= 80 to be actionable.
2. A signal on a LOW_LIQUIDITY ticker MUST have confidence >= 65 to be actionable.
3. If the expected position size (after liquidity multiplier) is < 5% of the ticker's average daily volume, the signal is SUPPRESSED with reason `INSUFFICIENT_LIQUIDITY`.
4. All liquidity-adjusted signals must include the liquidity tier in the Telegram notification.

### 6.4 Current Liquidity Concerns

The following tickers from expansion v2/v3 have flagged liquidity issues:

| Ticker | Avg Vol | Liquidity Tier | Concern |
|--------|---------|---------------|---------|
| XLUS.L | 351 | VERY_LOW | May be untradeable at any meaningful size |
| NVDS.L | 95 | VERY_LOW | Verify before every trade; consider removal |
| 3SMS.L | 696 | VERY_LOW | Samsung short; niche product |
| XLES.L | 2,028 | LOW | Energy sector short; limited demand |
| XLFS.L | 2,634 | LOW | Financial sector short; limited demand |
| BTCE.L | 1,493 | LOW | Bitcoin ETP; may improve as crypto grows |
| ETHE.L | 1,674 | LOW | Ethereum ETP; same as above |
| GLD3.L | 1,606 | LOW | 3x Gold; duplicate with 3GOL.L (7,957 vol) |

**Recommendation:** XLUS.L and NVDS.L should be moved to WATCHLIST tier immediately due to near-zero liquidity. 3SMS.L should be monitored for 30 days; if volume does not improve, quarantine.

---

## 7. EXPANSION POLICY

### 7.1 Maximum Universe Size

| Tier | Hard Cap | Current | Headroom |
|------|----------|---------|----------|
| CORE | 25 | 22 (12 CORE ISA + 10 EXTENDED) | 3 |
| PEER | 12 (50% of CORE cap) | 0 | 12 |
| FULL_SCAN | 60 | 40 (v2: 20, v3: 20) | 20 |
| WATCHLIST | Unlimited | 0 | N/A |
| US Bot B | 25 | 18 | 7 |
| CONTEXT | 15 | 8 | 7 |
| **TOTAL ACTIVE** | **~137** | **~88** | **~49** |

### 7.2 Expansion Eligibility Criteria

A ticker may only be added to the active universe if ALL of the following are true:

1. **ISA eligible** (for ISA-universe tickers). Verified via HMRC-approved ISA manager or broker documentation.
2. **Average daily volume > 1,000** over the trailing 20 trading days. Exception: WATCHLIST tier has no volume floor.
3. **Not delisted.** Verified via exchange and data provider.
4. **Leveraged ETP listed on LSE** (for ISA-universe). Verified via LSE instrument search.
5. **Data available** from at least one licensed provider (Polygon, IBKR) or yfinance.
6. **No duplicate underlying exposure.** If the same underlying is already covered at the same leverage by another ticker, a documented justification (e.g., hedging, better liquidity) is required.

### 7.3 Expansion Batch Governance

- **Batch frequency:** No more than one expansion batch per calendar month.
- **Batch size:** Maximum 20 tickers per batch.
- **Observation period:** 5 trading days after batch addition before the next batch.
- **Post-batch audit:** 30 days after addition, all tickers in the batch are reviewed for data quality, liquidity, and signal generation. Tickers that produced zero signals and have avg volume < 2,000 are moved to WATCHLIST.

---

## 8. MONTHLY UNIVERSE AUDIT

### 8.1 Audit Schedule

- **Frequency:** First trading day of each calendar month.
- **Responsible:** System operator.
- **Output:** `data/universe_audits/AUDIT_YYYY_MM.md`

### 8.2 Audit Checklist

For each ticker in the active universe:

- [ ] **Volume trend:** Compare current 20-day avg volume to previous month. Flag any decline > 50%.
- [ ] **Data quality:** Check gap rate in the last 30 days. Flag if > 3%.
- [ ] **Delist risk:** Check issuer announcements for any product termination notices.
- [ ] **Liquidity tier:** Recalculate and update if changed.
- [ ] **Signal activity:** Count signals generated. Flag tickers with zero signals in 30 days (FULL_SCAN tier) or 60 days (PEER/CORE).
- [ ] **Edge contribution:** Check edge ledger for this ticker. Flag negative expectancy over 20+ outcomes.
- [ ] **ISA eligibility:** Verify still ISA-eligible (annual check; monthly is best-effort).

### 8.3 Audit Actions

| Finding | Action |
|---------|--------|
| Volume decline > 50% | Review; consider demotion or quarantine |
| Data quality < 80% | Investigate data provider; switch source if needed |
| Issuer termination notice | Immediate removal (no quarantine) |
| Liquidity tier change | Update `settings.yaml`; adjust position size multiplier |
| Zero signals for 60+ days | Review strategy fit; consider demotion |
| Negative expectancy (20+ outcomes) | Review edge ledger; consider quarantine |
| ISA ineligible | Immediate removal for ISA-universe tickers |

---

## 9. FAILURE MODES

| # | Failure Mode | Impact | Mitigation |
|---|-------------|--------|------------|
| FM-1 | Ticker auto-added without approval | Untested ticker generates signals | LSE Registry RECOMMENDS only; `prompt-before-changes` flag enforced |
| FM-2 | Delisted ticker remains in active universe | Empty data, failed signals | Daily data quality check; auto-quarantine after 5 empty days |
| FM-3 | Low-liquidity ticker traded at full size | Slippage, inability to exit | Liquidity tier multiplier enforced in position sizing module |
| FM-4 | Universe grows beyond compute capacity | Slow scan cycles, missed signals | Hard caps enforced in `settings.yaml`; refuse additions above cap |
| FM-5 | Monthly audit not performed | Stale universe, decaying instruments | Calendar reminder; Telegram alert on first of month |
| FM-6 | Quarantine bypass (ticker re-added before 30 days) | Unstable universe | Quarantine state stored in DB with timestamp; code-enforced minimum |
| FM-7 | Duplicate underlying exposure | Correlated losses, concentrated risk | Expansion eligibility check #6 enforced |
| FM-8 | GBp pricing on newly added LSE ticker | 100x position size error | Sanity check on all new ticker prices within first 5 days |

---

## 10. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| A CORE ticker gets delisted by the exchange | Immediately remove the ticker from `settings.yaml` CORE tier. Close any open positions (paper or live). Move the ticker record to `data/universe_delisted/` with delist date and reason. Archive historical data in `research.db` (do NOT delete). Identify a replacement ticker with similar underlying exposure and leverage factor. Create a Ticker Proposal for the replacement and fast-track approval. Document the change in the monthly audit report and update the edge ledger to exclude future signals for the delisted ticker. |
| Liquidity drops below threshold for a CORE/PEER ticker (avg volume < 3,000 for 20 days) | Flag the ticker in the next monthly audit. Immediately reduce position size to the appropriate liquidity tier multiplier (LOW = 0.50x, VERY_LOW = 0.25x). If volume has collapsed to < 100, move directly to QUARANTINE. Otherwise, monitor for 10 additional trading days -- if volume recovers above 5,000, restore normal sizing. If volume continues to decline, initiate quarantine. Check if the low volume is market-wide (holiday period) or ticker-specific before making permanent changes. |
| Universe change proposal is rejected | Move the proposal file from `data/universe_proposals/` to `data/universe_rejected/` with the rejection reason documented. If the ticker was rejected for insufficient liquidity, it may be re-evaluated if volume improves -- set a calendar reminder for 30 days. If rejected for duplicate exposure, the proposal should not be resubmitted unless the existing covering ticker is removed. If rejected for data quality issues, re-evaluate once the data provider situation improves. |
| Scan time exceeds compute budget (60s cycle for CORE, 600s for FULL_SCAN) | Check total ticker count against tier hard caps (`CORE: 25, PEER: 12, FULL_SCAN: 60`). If caps are exceeded, demote the lowest-priority FULL_SCAN tickers to WATCHLIST (no compute cost). Verify compute allocation percentages (CORE: 70%, PEER: 20%, FULL_SCAN: 10%). Check for runaway indicator calculations or stuck API calls (`docker logs nzt48 --tail 200 | grep SLOW`). If the issue persists after trimming, consider increasing scan interval for FULL_SCAN tier from 600s to 900s. |
| A new leveraged ETP is listed on LSE | The LSE Registry should auto-detect the new listing during its daily scan. Verify the detection in `data/lse_registry/`. Evaluate the new ETP against expansion eligibility criteria (Section 7.2): ISA eligible, avg volume > 1,000, not a duplicate underlying. If qualified, create a Ticker Proposal and add to FULL_SCAN tier (never directly to CORE). Requires explicit operator approval -- the system must NOT auto-add. Allow 5 trading days of data collection before generating any signals. |

---

## 11. ACCEPTANCE TESTS

### AT-1: Tier Assignment

- [ ] Every ticker in `settings.yaml` has a documented tier assignment.
- [ ] No tier exceeds its hard cap.
- [ ] CORE tickers refresh at 60s; FULL_SCAN tickers refresh at 600s (verified via logging).

### AT-2: Addition Workflow

- [ ] Attempt to add a ticker via LSE Registry auto-discover with `auto_discover: true`. Verify the system RECOMMENDS but does NOT add without operator approval.
- [ ] Create a Ticker Proposal for a test ticker. Verify proposal file is created in `data/universe_proposals/`.
- [ ] Approve the proposal. Verify ticker appears in `settings.yaml` and `data/universe_approved/`.
- [ ] Reject a proposal. Verify ticker does NOT appear in `settings.yaml` and is in `data/universe_rejected/`.

### AT-3: Removal Workflow

- [ ] Simulate a delisted ticker (mock empty data for 5 days). Verify auto-quarantine triggers.
- [ ] Move a ticker to quarantine. Verify signal generation is disabled for that ticker.
- [ ] After 30 days in quarantine, verify the system prompts for ARCHIVE/REINSTATE/EXTEND decision.

### AT-4: Liquidity Governance

- [ ] For a VERY_LOW_LIQUIDITY ticker (avg vol < 1000), generate a signal with confidence 60. Verify it is SUPPRESSED.
- [ ] For the same ticker, generate a signal with confidence 85. Verify it is actionable but at 25% position size.
- [ ] Verify the Telegram notification includes the liquidity tier.

### AT-5: Monthly Audit

- [ ] Run the audit script. Verify it produces `data/universe_audits/AUDIT_YYYY_MM.md`.
- [ ] Verify the audit covers all tickers and flags at least the known low-volume concerns.

---

## 12. PROOF ARTIFACTS

| # | Artifact | Location | Purpose |
|---|----------|----------|---------|
| PA-1 | Universe governance config | New section in `config/settings.yaml` | Tier definitions, caps, refresh rates |
| PA-2 | Proposal templates | `data/universe_proposals/` | Pending ticker additions |
| PA-3 | Approved proposals | `data/universe_approved/` | Audit trail of additions |
| PA-4 | Rejected proposals | `data/universe_rejected/` | Audit trail of rejections |
| PA-5 | Quarantine log | `data/universe_quarantine/` | Tickers under review |
| PA-6 | Delisted archive | `data/universe_delisted/` | Historical removed tickers |
| PA-7 | Monthly audit reports | `data/universe_audits/AUDIT_YYYY_MM.md` | Recurring health checks |
| PA-8 | Liquidity tier module | Update to position sizing code | Enforced size multipliers |
| PA-9 | Universe dashboard | Dashboard tab showing tier membership, volume, health | Live visibility |
| PA-10 | Acceptance test suite | `tests/test_universe_governance.py` | Automated governance checks |

---

## 13. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Operator | | | |
| Universe Review Authority | | | |

**This plan must be signed off before any changes to the ticker addition/removal process are implemented.**
