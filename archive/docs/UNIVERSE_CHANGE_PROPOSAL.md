# NZT-48 Universe Governance Framework & Change Proposal

**Phase 7 Institutional Audit Deliverable**
**Proposal Date:** 2026-02-27
**Status:** DRAFT -- REQUIRES APPROVAL BEFORE ANY CHANGES
**Author:** Institutional Audit (Phase 7)

---

## A. CURRENT STATE

### A.1 CORE Universe (12 Tickers)

The CORE universe is the sole set of instruments eligible for TRADE signals. All 12 are LSE-listed leveraged ETPs tradable within a UK ISA via Trading 212 (zero commission, zero FX fee on .L instruments).

| # | Ticker | Full Name | Underlying | Leverage | Direction | Sector | Provider | Spread (bps) |
|---|--------|-----------|-----------|----------|-----------|--------|----------|--------------|
| 1 | QQQ3.L | WisdomTree NASDAQ 100 3x Daily ETP | QQQ / NDX | 3x | LONG | Technology / Broad | WisdomTree | 8 |
| 2 | 3LUS.L | WisdomTree S&P 500 3x Daily ETP | SPY / SPX | 3x | LONG | Broad Market | WisdomTree | 8 |
| 3 | 3SEM.L | WisdomTree Semiconductors 3x Daily ETP | SOX / SMH | 3x | LONG | Semiconductors | WisdomTree | 12 |
| 4 | GPT3.L | WisdomTree US AI 3x Daily ETP | AI / Tech | 3x | LONG | AI & Technology | WisdomTree / Leverage Shares | 12 |
| 5 | NVD3.L | GraniteShares NVIDIA 3x Long Daily ETP | NVDA | 3x | LONG | Semiconductors | GraniteShares | 12 |
| 6 | TSL3.L | GraniteShares Tesla 3x Long Daily ETP | TSLA | 3x | LONG | EV / Tech | GraniteShares | 15 |
| 7 | TSM3.L | GraniteShares TSMC 3x Long Daily ETP | TSM | 3x | LONG | Semiconductors | GraniteShares | 10 |
| 8 | MU2.L | GraniteShares Micron 2x Long Daily ETP | MU | 2x | LONG | Semiconductors | GraniteShares | 10 |
| 9 | QQQS.L | WisdomTree NASDAQ 100 3x Daily Short ETP | QQQ / NDX | -3x | SHORT | Technology / Broad | WisdomTree | 10 |
| 10 | 3USS.L | WisdomTree S&P 500 3x Daily Short ETP | SPY / SPX | -3x | SHORT | Broad Market | WisdomTree | 10 |
| 11 | QQQ5.L | WisdomTree NASDAQ 100 5x Daily ETP | QQQ / NDX | 5x | LONG | Technology / Broad | WisdomTree | 10 |
| 12 | SP5L.L | WisdomTree S&P 500 5x Daily ETP | SPY / SPX | 5x | LONG | Broad Market | WisdomTree | 8 |

**CORE composition profile:**
- 10 LONG, 2 SHORT (QQQS.L, 3USS.L)
- Leverage mix: 1x 2x, 8x 3x, 1x -3x (x2), 2x 5x
- Single-stock: 3 (NVD3.L, TSL3.L, TSM3.L + MU2.L)
- Index-based: 9 (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L + 3SEM.L is sector index)
- All confirmed ISA-eligible via Trading 212
- Zero commission, zero FX fee (GBP-settled .L instruments)

### A.2 Current PEER Universe (6 Tickers)

As of 2026-02-26, the active peer selection (from `artifacts/2026-02-26/universe/peers.json`):

| # | Ticker | Name | Underlying | Leverage | Direction | Factor Theme |
|---|--------|------|-----------|----------|-----------|--------------|
| 1 | 3LDE.L | WisdomTree DAX 3x Daily ETP | DAX | 3x | LONG | europe_index |
| 2 | 3LEU.L | WisdomTree Euro Stoxx 50 3x Daily ETP | EuroStoxx50 | 3x | LONG | europe_index |
| 3 | AMD3.L | GraniteShares AMD 3x Long Daily ETP | AMD | 3x | LONG | semiconductors |
| 4 | ARM3.L | GraniteShares ARM 3x Long Daily ETP | ARM | 3x | LONG | semiconductors |
| 5 | NVDS.L | GraniteShares NVIDIA -3x Short Daily ETP | NVDA | -3x | SHORT | (inverse NVD3) |
| 6 | TSLS.L | GraniteShares Tesla -3x Short Daily ETP | TSLA | -3x | SHORT | (inverse TSL3) |

**PEER status:** WATCH/INTEL only. `allow_trade_from_peers: false` in universe.yaml. Not eligible for TRADE signals.

### A.3 Current FULL_SCAN Universe (29 Tickers)

The FULL_SCAN tier contains US benchmark ETFs, volatility indicators, bonds/FX/commodities, and the underlying single names for leveraged ETPs. These are purely intel instruments for regime detection, sector rotation analysis, and correlation monitoring. Never tradable.

Full list: QQQ, SPY, SMH, SOXX, IWM, DIA, XLK, XLF, XLE, XLV, ^VIX, TLT, GLD, USO, DX-Y.NYB, BTC-USD, NVDA, TSLA, TSM, MU, AMD, AVGO, ARM, AMZN, MSFT, META, PLTR, GOOG, AAPL.

### A.4 How the Universe is Currently Configured

The universe is defined in four locations, all of which must remain consistent:

1. **`config/universe.yaml`** -- Primary config file loaded by `UniverseManager`. Contains `core_list`, `peer_candidates`, `full_scan_list`, factor themes, compute budgets, scan cadences.

2. **`config/settings.yaml`** (section `v2_engine.isa_tickers_v2`) -- V2 engine ticker lists. Contains `core` and `extended` sub-lists.

3. **`uk_isa/isa_universe.py`** -- Python module canonical source of truth. Defines `CORE_UNIVERSE`, `EXTENDED_UNIVERSE`, `INTEL_UNIVERSE`, `ISA_FACTOR_GROUPS`, `LEVERAGE_MAP`, `EXPECTED_PRICE_RANGES`, `SLIPPAGE_MODEL`.

4. **`uk_isa/universe_manager.py`** -- Thread-safe singleton `UniverseManager` that loads from `universe.yaml` at startup. Falls back to hardcoded defaults matching `isa_universe.py`.

**Architecture flow:** `universe.yaml` -> `UniverseManager.__post_init__()` -> `PeerFinder.find_peers()` selects top-6 peers -> `write_universe_artifacts()` writes daily JSON snapshots to `artifacts/YYYY-MM-DD/universe/`.

**Compute budget:** CORE 70%, PEER 20%, FULL_SCAN 10%.
**Scan cadence:** CORE every 60s, PEER every 180s, FULL_SCAN every 600s.

---

## B. GOVERNANCE FRAMEWORK

### B.1 Tier Definitions

| Tier | Scope | Tradable | Signal Types | Objective | Scan Frequency |
|------|-------|----------|-------------|-----------|----------------|
| CORE | 12 primary ISA leveraged ETPs | YES | TRADE, WATCH, INTEL | MAX_INTRADAY_GAINS | Every 60s |
| PEER | ~6 similar instruments (50% of CORE count) | NO | WATCH, INTEL only | 2% framing, intel-only | Every 180s |
| FULL_SCAN | Broad market benchmarks + underlyings | NO | INTEL only | Context, regime detection | Every 600s |

### B.2 Criteria for CORE Inclusion

A ticker must meet ALL of the following criteria to be eligible for CORE status:

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| ISA Eligibility | Must be available on T212 ISA | Legal requirement for tax-free wrapper |
| LSE Listing | Must trade as a .L ticker on LSE | GBP settlement, no FX fee |
| Leverage Factor | 2x to 5x (absolute) | Sub-2x insufficient for 2% daily target; above 5x = excessive decay |
| Direction | LONG or SHORT (leveraged/inverse) | Must provide directional exposure |
| Average Daily Volume (20d) | >= 50,000 shares | Minimum liquidity for entry/exit without slippage |
| Bid-Ask Spread | <= 20 bps (half-spread) | Trading cost must not erode edge |
| Tracking Error | < 1.5% annualised vs underlying leverage target | Must reliably deliver stated leverage |
| Provider Credibility | WisdomTree, GraniteShares, Leverage Shares, or equivalent regulated issuer | Counterparty/structural risk |
| Active Trading History | >= 90 days of continuous quoting | New listings need burn-in period |
| yfinance Data Availability | Must return valid OHLCV via yfinance `.L` ticker | System dependency |
| Underlying Relevance | Must track an underlying in the AI/Semi/Tech/Broad-Market theme or provide portfolio hedging | Thematic coherence |
| Max CORE Exposure per Underlying | No more than 3 CORE tickers on the same underlying | Diversification |

### B.3 Criteria for PEER Selection

PEER tickers are selected algorithmically by `PeerFinder` (50% correlation, 30% factor/theme, 20% momentum/vol similarity), but candidates must first pass these manual curation gates:

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| LSE Listing | Must be .L ticker | Consistent data feed |
| Leverage Factor | 2x to 5x | Same risk profile as CORE |
| yfinance Data | Must return >= 10 days of data | PeerFinder requires 10+ observations |
| Not Delisted | Must pass `lse_registry` active check | No dead tickers in candidate pool |
| Distinct from CORE | Must not duplicate a CORE ticker | Peers must add information, not duplicate |
| Diversity Cap | Max 2 peers per `factor_group` | Prevent overconcentration |
| Min Similarity Score | >= 0.40 combined score | Must be meaningfully related to CORE |

**PEER target count:** `ceil(0.50 * len(CORE)) = 6` peers.

### B.4 FULL_SCAN Scope and Constraints

| Constraint | Rule |
|------------|------|
| Max Size | 500 tickers (hard cap in config) |
| Tradability | NEVER -- `tradable: false` enforced by UniverseManager |
| Signal Output | INTEL cards only, never TRADE signals |
| Cannot Override CORE | FULL_SCAN instruments cannot substitute for or modify CORE output quotas |
| Cannot Override PEER | FULL_SCAN cannot promote itself to PEER tier |
| Content | US benchmark ETFs, volatility indices, bonds, FX, commodities, and underlying single names |
| Compute Budget | 10% of total scan compute |

### B.5 Approval Workflow

```
 PROPOSAL          REVIEW           TEST             APPROVE          DEPLOY
 ---------         ------           ----             -------          ------
 Author drafts     Peer review      Paper-trade      Operator signs   Config files
 change doc  --->  of rationale --> new universe -->  off on data  --> updated across
 with full         + risk check     for 5 trading    quality &        all 4 sources
 rationale                          days minimum     P&L impact       (atomic commit)
```

**Step 1: Proposal** -- Author creates a document (like this one) specifying exact changes, rationale, risk assessment, and expected impact.

**Step 2: Review** -- Manual review of:
- Liquidity data (20-day average volume)
- Spread measurements (bid-ask from live quotes)
- Tracking error analysis (1M, 3M realized vs expected)
- Correlation impact on existing portfolio risk
- Data availability confirmation (yfinance test)

**Step 3: Test** -- Run parallel paper-trading with the proposed universe for minimum 5 trading days. Compare signal quality, data health, and simulated P&L.

**Step 4: Approve** -- Operator gives explicit written approval with date stamp.

**Step 5: Deploy** -- Atomic update of ALL four configuration sources:
1. `config/universe.yaml`
2. `config/settings.yaml` (v2_engine.isa_tickers_v2)
3. `uk_isa/isa_universe.py` (CORE_UNIVERSE, EXTENDED_UNIVERSE, LEVERAGE_MAP, etc.)
4. Verify `uk_isa/universe_manager.py` defaults match

### B.6 Quarterly Review Cadence

| Review | Timing | Scope |
|--------|--------|-------|
| Q1 Review | First week of January | Full universe audit: CORE + PEER + FULL_SCAN |
| Q2 Review | First week of April | CORE health check + PEER rotation |
| Q3 Review | First week of July | Full universe audit |
| Q4 Review | First week of October | CORE health check + PEER rotation + year-ahead planning |

**Quarterly review checklist:**
- [ ] Verify all CORE tickers still active and liquid on LSE
- [ ] Check for new LSE leveraged ETP listings (via `lse_registry.py` refresh)
- [ ] Review CORE ticker volume trends (declining volume = warning)
- [ ] Assess tracking error drift
- [ ] Run PeerFinder with fresh data and review candidate pool
- [ ] Check for delistings or provider changes
- [ ] Review FULL_SCAN for missing macro benchmarks
- [ ] Document any changes in this governance framework

---

## C. PEER EXPANSION PROPOSAL

### C.1 Current State

The current peer candidate pool in `universe.yaml` contains 10 tickers:
AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L, LLY3.L.

Of these, the PeerFinder selects 6 daily. The most recent selection (2026-02-26) chose: 3LDE.L, 3LEU.L, AMD3.L, ARM3.L, NVDS.L, TSLS.L.

### C.2 Proposed Peer Tickers (6 Active Peers)

Based on analysis of the LSE registry seed catalog, the settings.yaml leveraged ETP listings, and the current CORE composition, the following 6 tickers are proposed as the formal PEER universe:

| # | Ticker | Name | Underlying | Leverage | Direction | Rationale | Risk | Expected Benefit |
|---|--------|------|-----------|----------|-----------|-----------|------|------------------|
| 1 | **AMD3.L** | GraniteShares AMD 3x Long Daily ETP | AMD | 3x | LONG | Direct semiconductor peer to 3SEM.L and NVD3.L. AMD is 2nd-largest GPU/AI chip maker. High correlation with CORE semi tickers. Adds single-stock alpha signal for AI/semi theme. | Lower liquidity than NVD3.L; wider spread on some sessions. AMD earnings can cause 15%+ single-day moves at 3x leverage. | Provides granular AMD signal when NVDA and AMD diverge. Catches AMD-specific catalysts (MI300, data centre wins). |
| 2 | **ARM3.L** | GraniteShares ARM 3x Long Daily ETP | ARM Holdings | 3x | LONG | ARM architecture underpins all mobile + AI edge chips. Unique exposure not available via any CORE ticker. Strong correlation with semiconductor sector. | ARM is UK-listed (dual), lower US trading hours liquidity. Spread may widen during LSE-only hours. Post-IPO price discovery still maturing. | Diversifies semi exposure beyond US GPU/memory. ARM IP licensing model has different risk profile from NVDA/AMD/MU. |
| 3 | **NVDS.L** | GraniteShares NVIDIA -3x Short Daily ETP | NVDA | -3x | SHORT | Direct inverse of NVD3.L (CORE). Provides hedging capability for NVIDIA-specific downside. Essential for risk-off regime signal generation. | Inverse ETPs suffer accelerated decay in trending-up markets. Can lose 90%+ of value in strong NVDA bull runs. | Enables NVIDIA-specific short signals. Complements QQQS.L/3USS.L (broad shorts) with single-stock precision. |
| 4 | **TSLS.L** | GraniteShares Tesla -3x Short Daily ETP | TSLA | -3x | SHORT | Direct inverse of TSL3.L (CORE). Tesla is highest-volatility CORE underlying. Provides downside hedging for EV/tech theme. | Same inverse decay risk as NVDS.L. Tesla's extreme volatility makes -3x particularly aggressive. | Enables Tesla-specific short signals. Tesla news events (earnings, Elon tweets, deliveries) create both-direction opportunities. |
| 5 | **3LDE.L** | WisdomTree DAX 3x Daily ETP | DAX Index | 3x | LONG | European index exposure diversifies away from 100% US underlyings. DAX is heavily weighted toward industrials, auto, chemicals -- different sector mix from Nasdaq/S&P. | European macro risk (ECB policy, EU regulation). Time zone disconnect: DAX trades 08:00-16:30 UK, no overlap with US afternoon session. | Provides non-US directional signal. Useful during US holidays or when US/EU markets diverge. Catches European-specific catalysts. |
| 6 | **3LEU.L** | WisdomTree Euro Stoxx 50 3x Daily ETP | Euro Stoxx 50 | 3x | LONG | Broader European exposure than DAX alone. Euro Stoxx 50 includes France (LVMH, TotalEnergies), Netherlands (ASML), Germany (SAP), etc. | Same European macro risk as 3LDE.L. Slightly lower liquidity than DAX 3x. | Adds pan-European breadth. ASML component creates indirect semiconductor linkage. Rotation signal when capital flows EU vs US. |

### C.3 Candidate Pool Composition Analysis

The proposed 6 peers provide:
- **2 semiconductor single-stock LONG** (AMD3.L, ARM3.L) -- deepens CORE semi theme
- **2 inverse/SHORT single-stock** (NVDS.L, TSLS.L) -- hedging + both-direction signals
- **2 European index LONG** (3LDE.L, 3LEU.L) -- geographic diversification

**Remaining candidates not selected** (still in `peer_candidates` pool for rotation):
- 3GOL.L (Gold 3x) -- commodity hedge, low correlation with tech CORE
- 3SIL.L (Silver 3x) -- commodity, even lower correlation
- 3OIL.L (Oil 3x) -- energy commodity, low tech correlation
- LLY3.L (Eli Lilly 3x) -- pharma/healthcare, low tech correlation

These 4 remain available for future peer rotation if market regime shifts favour commodity hedges or healthcare diversification.

### C.4 Peer Candidates to Consider Adding to Pool

The following tickers exist in the LSE registry (`lse_registry.py`) but are NOT currently in the peer candidate pool. They could be added in a future proposal after verification:

| Ticker | Name | Underlying | Leverage | Status | Notes |
|--------|------|-----------|----------|--------|-------|
| 3LEN.L | WisdomTree Energy 3x Daily ETP | XLE/Energy | 3x LONG | In seed catalog | Energy sector exposure; useful if oil/energy bull market |
| 3LFI.L | WisdomTree Financials 3x Daily ETP | XLF/Finance | 3x LONG | In seed catalog | Financial sector; rate-sensitive diversifier |
| 3LHC.L | WisdomTree Healthcare 3x Daily ETP | XLV/Health | 3x LONG | In seed catalog | Healthcare sector; defensive rotation play |
| 3SSM.L | WisdomTree Semiconductors 3x Daily Short ETP | SOX/SMH | -3x SHORT | In seed catalog | Inverse of 3SEM.L; semiconductor hedging |
| SC3S.L | WisdomTree PHLX Semiconductor (-3x) | SOX | -3x SHORT | In settings.yaml | Alternative semi short (verify LSE availability) |
| SP5S.L | WisdomTree S&P 500 5x Daily Short ETP | SPY/SPX | -5x SHORT | In settings.yaml | Inverse of SP5L.L; 5x short for high-conviction bearish |

**Recommendation:** Add 3SSM.L (Semiconductors -3x Short) to the peer candidate pool. It is the direct inverse of 3SEM.L (CORE) and would provide semiconductor-specific downside hedging alongside the existing NVDS.L (NVIDIA-specific) and QQQS.L (broad Nasdaq). This adds sector-level short precision.

---

## D. CORE REVIEW

### D.1 CORE Health Assessment

| Ticker | Status | Concern Level | Notes |
|--------|--------|--------------|-------|
| QQQ3.L | HEALTHY | LOW | High liquidity, tight spreads, flagship WisdomTree product. No issues. |
| 3LUS.L | HEALTHY | LOW | Solid S&P 500 tracker. Good liquidity. No issues. |
| 3SEM.L | HEALTHY | LOW | Semiconductor sector index 3x. Good when semi cycle is active. |
| GPT3.L | MONITOR | MEDIUM | "AI/GPT" theme branding. Underlying is Solactive US AI index -- verify tracking composition regularly. AI theme indexes can be unstable in early years. Spread at 12 bps is acceptable. |
| NVD3.L | HEALTHY | LOW | NVIDIA single-stock 3x. Very popular product. Good liquidity. |
| TSL3.L | HEALTHY | LOW | Tesla single-stock 3x. Higher spread (15 bps) reflects Tesla volatility. Acceptable. |
| TSM3.L | HEALTHY | LOW | TSMC single-stock 3x. Geopolitical risk (Taiwan) is fundamental, not structural. |
| MU2.L | MONITOR | MEDIUM | Only 2x leverage (all others are 3x or 5x). This means MU2.L needs a 1% underlying move to deliver 2%, while 3x tickers need only 0.67%. May underperform on the 2% daily target relative to 3x peers. However, it provides lower-risk Micron exposure. |
| QQQS.L | HEALTHY | LOW | Inverse Nasdaq 100 3x. Essential for bearish regimes. Spread at 10 bps is fine. |
| 3USS.L | HEALTHY | LOW | Inverse S&P 500 3x. Essential for bearish regimes. |
| QQQ5.L | MONITOR | MEDIUM | 5x leverage carries significant volatility decay. Max hold capped at 3 days by immutable rules. Position sizing capped at 5% single / 15% total. Must be used only for highest-conviction calls. |
| SP5L.L | MONITOR | MEDIUM | Same 5x decay concerns as QQQ5.L. Same position limits apply. |

### D.2 Potential CORE Issues

**MU2.L (2x Leverage):**
MU2.L is the only sub-3x ticker in CORE. For the 2% daily compounding target, MU (Micron) needs to move +1.0% for MU2.L to deliver +2.0%, whereas 3x ETPs only need the underlying to move +0.67%. This is a structural disadvantage for the S15 daily target strategy. However, Micron is a high-beta memory stock that regularly delivers 2%+ intraday moves, and 2x leverage means less volatility decay on hold, making it safer for multi-day positions.

**Recommendation:** RETAIN MU2.L in CORE. The lower leverage is offset by Micron's higher underlying volatility. If a 3x Micron ETP becomes available on LSE, it should be evaluated as a potential replacement.

**GPT3.L (AI Theme Index):**
The "Solactive US AI" index underlying GPT3.L is relatively new. The index composition and rebalancing methodology should be monitored quarterly. If the index becomes overly concentrated or drifts from true AI exposure, consider replacing with a more established alternative.

**Recommendation:** RETAIN GPT3.L but add to quarterly review checklist for index composition monitoring.

### D.3 Missing CORE Candidates

| Potential Ticker | Underlying | Leverage | Case For | Case Against | Recommendation |
|-----------------|-----------|----------|----------|-------------|----------------|
| AMD3.L | AMD | 3x LONG | 2nd largest AI chip company. High correlation with NVDA. Frequently delivers 2%+ daily moves. | Already well-covered by 3SEM.L (sector index includes AMD). Adding single-stock increases concentration risk. Currently a PEER. | DEFER -- keep as PEER. Revisit if AMD decouples significantly from sector. |
| 3SSM.L | Semiconductors | -3x SHORT | Direct inverse of 3SEM.L. Would complete long/short pair for semi sector. | Only 2 SHORT tickers currently in CORE (QQQS, 3USS) -- both broad index. Adding sector short would increase SHORT exposure. | CONSIDER for next quarterly review. |
| SP5S.L | S&P 500 | -5x SHORT | Inverse of SP5L.L. Would complete 5x long/short pair. | 5x inverse is extremely aggressive. Decay is brutal. Rarely needed. | DO NOT ADD -- too risky for standard use. Available in settings.yaml for emergency deployment only. |

### D.4 Summary of CORE Recommendations

**No changes recommended to CORE at this time.** All 12 tickers are functioning and appropriate. The current composition provides good coverage across:
- Broad index (3x and 5x, long and short)
- Semiconductor sector (index and single-stock)
- AI theme
- Single-stock momentum plays (NVDA, TSLA, TSMC, MU)

---

## E. OBJECTIVE SPLIT

### E.1 CORE Objective: MAX_INTRADAY_GAINS

CORE tickers operate under the **MAX_INTRADAY_GAINS** objective:

- **S15 Daily Target Strategy** fires once per day, scores all 12 CORE tickers by "2% reachability"
- Best candidate gets the TRADE signal with full position sizing
- Stop = 1x ATR, Target = +2% exactly
- Profit ladder applies (7 rungs for stocks, faster 3x ETP ladder)
- Full confidence engine scoring (5 layers, 60-point floor)
- All immutable risk rules apply (0.75% risk per trade, 3% max daily loss, etc.)
- No cap on upside -- runners are trailed, not capped
- Compounding target: 2% daily = 14,757% annualised on the 2% daily compounding law

**CORE signal types:** TRADE (actionable), WATCH (near-threshold), INTEL (context)

### E.2 Non-CORE Objective: 2% Reporting Framing (Intel-Only)

PEER and FULL_SCAN tickers operate under strict constraints:

**PEER tickers (6):**
- `allow_trade_from_peers: false` -- hardcoded in `universe.yaml`
- Signal types: WATCH-INTEL and INTEL only -- never TRADE
- 2% framing: "Did this PEER ticker move 2%+ today?" is reported for context
- Cannot generate executable trade signals
- Cannot substitute for CORE output quotas
- Purpose: provide context on adjacent instruments, validate CORE signals, detect divergences
- Compute allocation: 20% of budget

**FULL_SCAN tickers (29):**
- `allow_trade_from_full_scan: false` -- hardcoded in `universe.yaml`
- Signal types: INTEL only
- Cannot generate TRADE or WATCH signals
- Cannot override CORE or substitute for CORE output quotas
- Purpose: regime detection, sector rotation analysis, macro context, underlying price tracking
- The 2% framing is applied only as informational context in daily reports
- Compute allocation: 10% of budget

### E.3 Enforcement Mechanisms

The objective split is enforced at multiple levels:

1. **UniverseManager.all_tradable** property returns ONLY `_core_list` (unless `allow_trade_from_peers` is explicitly set to `true`, which it is not)
2. **UniverseManager.get_tier()** classifies every ticker, and downstream signal generators check tier before emitting TRADE signals
3. **Daily artifacts** (`core.json`, `peers.json`, `full_scan.json`) explicitly tag `"tradable": true/false`
4. **Qualification pipeline stage 7** (ISA mapper) only maps CORE tickers to ISA execution instruments
5. **Confidence engine** only scores CORE tickers for trade qualification

### E.4 Promotion Path (PEER to CORE)

If a PEER ticker consistently demonstrates:
- 20-day average volume exceeding CORE minimum (50,000 shares)
- Spread within CORE maximum (20 bps)
- High similarity score (>= 0.70) with at least one CORE ticker
- Unique information value (not just a duplicate of existing CORE exposure)
- 90+ days of clean data history

Then a formal proposal can be submitted following the B.5 Approval Workflow to promote the ticker from PEER to CORE. This requires:
1. Written proposal with full rationale
2. 5-day parallel paper trading
3. Operator approval
4. Atomic config update across all 4 sources

---

## F. APPENDICES

### F.1 Configuration Source Cross-Reference

| Data Point | universe.yaml | settings.yaml | isa_universe.py | universe_manager.py |
|------------|--------------|---------------|-----------------|-------------------|
| CORE list | `universe.core_list` | `v2_engine.isa_tickers_v2.core` | `CORE_UNIVERSE` | `_DEFAULT_CORE` |
| Extended list | (core + peer_candidates) | `v2_engine.isa_tickers_v2.extended` | `EXTENDED_UNIVERSE` | `_DEFAULT_PEER_CANDIDATES` |
| FULL_SCAN | `universe.full_scan_list` | (not duplicated) | `INTEL_UNIVERSE` | `_DEFAULT_FULL_SCAN` |
| Factor themes | `universe.factor_themes` | (not duplicated) | `ISA_FACTOR_GROUPS` | `_DEFAULT_FACTOR_THEMES` |
| Leverage map | (not stored) | `bot_a_universe.leveraged_4x_5x` | `LEVERAGE_MAP` | (not stored) |
| Spreads | (not stored) | (not stored) | `SLIPPAGE_MODEL` | (not stored) |

### F.2 LSE Registry Seed Catalog Summary

The `lse_registry.py` seed catalog contains the following products (not all are in CORE or PEER):

**Broad Index Long (4):** QQQ3.L, 3LUS.L, QQQ5.L, SP5L.L, 3LDE.L, 3LEU.L, 3LJP.L, 3LHK.L
**Broad Index Short (6):** QQQS.L, 3USS.L, QQQE.L, SP5S.L, 3SDE.L, 3SEU.L
**Sector Long (5):** 3SEM.L, GPT3.L, 3LEN.L, 3LFI.L, 3LHC.L
**Sector Short (2):** 3SSM.L, 3SEN.L
**Single-Stock Long (17):** NVD3.L, TSL3.L, TSM3.L, MU2.L, MFAS.L, AMZL.L, MSFL.L, AAPLL.L, GOOGL3.L, AMD3.L, AVGO3.L, ARM3.L, PLTR3.L, COIN3.L, MSTRL.L, BAC3.L, GS3.L, XOM3.L, LLY3.L
**Single-Stock Short (5):** NVDS.L, TSLS.L, MFASS.L, AMZS.L, MSFS.L
**Commodity Leveraged (5):** 3GOL.L, 3SIL.L, 3OIL.L, GOIL.L, SOIL.L

Total: ~52 known LSE leveraged products in the registry.

### F.3 Dead/Delisted Tickers (Removed from Peer Candidates)

Per `universe.yaml` comment (verified 2026-02-26): AVGO3.L, PLTR3.L, AMZL.L, MSFL.L, COIN3.L, MSTRL.L, BAC3.L, GS3.L, MFAS.L were removed from the peer candidate pool as they returned no data from yfinance, indicating possible delisting or ticker change.

**Note:** These tickers remain in the `lse_registry.py` seed catalog and in `settings.yaml` mappings. The registry's daily refresh will mark them as `is_active: false` when data cannot be fetched. No code changes are needed for dead tickers -- they are handled gracefully.

---

**END OF PROPOSAL**

*This document is a proposal only. No configuration files, code, or ticker lists have been modified. Changes require explicit operator approval per section B.5.*
