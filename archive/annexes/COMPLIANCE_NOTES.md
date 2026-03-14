# NZT-48 Compliance Notes

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-CN-001             |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **DRAFT**                      |
| Classification  | Internal -- Legal/Compliance   |
| Review Required | Legal counsel before live trading |

---

## 1. PURPOSE

This document records the compliance considerations for the NZT-48 Leveraged ISA Intraday Trading System. It covers data provider licensing, ISA wrapper rules, leveraged ETP suitability, and regulatory considerations. This is a working document that must be reviewed by qualified legal counsel before any transition to live trading.

**This document does NOT constitute legal advice.** It is a record of considerations and open questions for review.

---

## 2. DATA PROVIDER LICENSING

### 2.1 Provider Inventory

| Provider | Plan | Monthly Cost | Commercial Use Allowed | LSE Leveraged ETP Coverage | Status |
|----------|------|-------------|----------------------|---------------------------|--------|
| yfinance | Free (unofficial Yahoo Finance API) | 0 | **NO** -- no commercial license; acceptable for personal/research use | Partial -- most `.L` tickers work; some delisted ETPs return empty data | ACTIVE (paper mode) |
| Polygon.io | Stocks Starter ($29/mo) | $29 | **YES** -- commercial use permitted under Starter plan | **TO VERIFY** -- check if LSE leveraged ETPs (QQQ3.L, NVD3.L etc.) are included in Starter tier | APPROVED for evaluation |
| IBKR Market Data | Requires IBKR account | Varies (data subscription fees) | **YES** -- permitted for algorithmic trading with IBKR account | **YES** -- real-time LSE data available with LSE market data subscription | NOT STARTED |
| TradingView | N/A | N/A | **N/A** -- NOT used for data feeds | N/A | NOT USED |

### 2.2 yfinance Usage Assessment

| Aspect | Assessment |
|--------|-----------|
| **Current Use** | Primary data source for all market data (OHLCV, futures, VIX) |
| **License Status** | yfinance is an unofficial, community-maintained library that scrapes Yahoo Finance. Yahoo Finance does not provide a formal API or commercial license for this usage |
| **Paper Mode Risk** | LOW -- personal/research use for paper trading is generally acceptable |
| **Live Mode Risk** | **HIGH** -- using yfinance for live trading decisions with real capital is legally uncertain. Yahoo Finance terms of service may prohibit automated data retrieval |
| **Migration Plan** | Migrate to Polygon.io as primary data source before live trading (see DATA_VENDOR_MIGRATION_PLAN.md) |
| **Fallback Role** | After migration, yfinance may remain as a fallback data source with `FALLBACK_DATA` flag |

### 2.3 Polygon.io Assessment

| Aspect | Assessment |
|--------|-----------|
| **Plan** | Stocks Starter ($29/month) -- approved |
| **Commercial Use** | Permitted under Starter plan terms |
| **LSE Coverage** | **OPEN QUESTION** -- verify that the Starter plan includes LSE-listed leveraged ETPs. Polygon primarily covers US markets. May require Stocks Developer ($79/mo) or additional add-ons for international coverage |
| **API Limits** | Starter: 5 API calls/minute. May be insufficient for 60s scan cycle across 12+ tickers. Evaluate rate limit impact |
| **Action Items** | 1. Verify LSE ETP coverage on Starter plan. 2. Test API rate limits with production scan cycle. 3. Confirm data freshness (real-time vs 15-min delayed for LSE) |

### 2.4 IBKR Data Assessment

| Aspect | Assessment |
|--------|-----------|
| **Account Requirement** | IBKR account required; data subscriptions are add-on services |
| **LSE Real-Time** | Available with "LSE Level 1" market data subscription |
| **Algorithmic Trading** | Permitted -- IBKR explicitly supports algorithmic trading via API |
| **Integration** | IBKR TWS API or IB Gateway required. Adds infrastructure complexity |
| **Future State** | IBKR becomes the natural data source when the system moves to IBKR for execution (Gate 3+) |

### 2.5 TradingView Clarification

TradingView is **NOT** used as a data source for the NZT-48 system. No scraping, API calls, or data extraction from TradingView exists in the codebase. If any TradingView scraping is discovered, it must be removed immediately due to clear terms-of-service violations.

---

## 3. ISA WRAPPER RULES

### 3.1 Permitted Activities Within an ISA

| Activity | Permitted | Notes |
|----------|-----------|-------|
| Buying shares/ETPs listed on recognised exchanges | YES | LSE-listed leveraged ETPs qualify |
| Selling shares/ETPs previously purchased | YES | Standard sell orders |
| Day trading (buy and sell same instrument same day) | YES | Unusual but not prohibited. HMRC may scrutinise if income appears to constitute a trade |
| Short selling | **NO** | ISA wrappers do not support direct short positions |
| Using margin / leverage on the account | **NO** | ISA accounts are cash-only; no margin borrowing |
| Holding leveraged ETPs (3x, 5x) | YES | The product itself is leveraged; the ISA account is not. This is permitted |
| Holding inverse ETPs (QQQS.L, 3USS.L) | YES | Purchased as long positions. The inverse exposure is embedded in the product |
| CFDs, spread bets, options | **NO** | Not ISA-eligible instruments |
| Transferring in/out | YES | ISA-to-ISA transfers permitted |

### 3.2 ISA Constraints for NZT-48

| Constraint | System Enforcement |
|-----------|-------------------|
| No direct short selling | System only generates BUY signals. Short exposure achieved via inverse ETPs (QQQS.L, 3USS.L) held as long positions |
| No margin | Position sizing based on available cash balance only. No borrowing |
| Cash-only settlement | Virtual trader assumes T+2 settlement. No unsettled-funds trading |
| Annual contribution limit | Current ISA limit: 20,000 per tax year. System starting equity: 10,000. Headroom exists |

---

## 4. LEVERAGED ETP SUITABILITY

### 4.1 ISA Eligibility

Leveraged ETPs are ISA-eligible if they are:
- Listed on a recognised exchange (LSE qualifies)
- Classified as transferable securities
- Not derivatives (leveraged ETPs are structured notes, not derivatives per se)

**All 12 active tickers are LSE-listed and should be ISA-eligible. Confirm with ISA provider.**

### 4.2 Product-Level Verification

| Ticker | Product | Leverage | Type | ISA Eligible | Verified |
|--------|---------|----------|------|-------------|----------|
| QQQ3.L | WisdomTree Nasdaq 100 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| 3LUS.L | WisdomTree S&P 500 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| 3SEM.L | WisdomTree Semiconductors 3x | 3x Long | ETP | Expected YES | [ ] |
| GPT3.L | WisdomTree AI 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| NVD3.L | GraniteShares Nvidia 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| TSL3.L | GraniteShares Tesla 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| TSM3.L | GraniteShares TSMC 3x Daily | 3x Long | ETP | Expected YES | [ ] |
| MU2.L | GraniteShares Micron 2x Daily | 2x Long | ETP | Expected YES | [ ] |
| QQQS.L | WisdomTree Nasdaq 100 3x Short | 3x Inverse | ETP | Expected YES | [ ] |
| 3USS.L | WisdomTree S&P 500 3x Short | 3x Inverse | ETP | Expected YES | [ ] |
| QQQ5.L | Leverage Shares Nasdaq 100 5x | 5x Long | ETP | Expected YES | [ ] |
| SP5L.L | Leverage Shares S&P 500 5x | 5x Long | ETP | Expected YES | [ ] |

**ACTION REQUIRED:** Verify ISA eligibility of each product with the chosen ISA provider before live trading.

### 4.3 Leveraged Product Risks

The following risks are inherent to leveraged ETPs and must be understood by the operator:

| Risk | Description | Mitigation |
|------|-------------|-----------|
| Volatility decay | Daily reset causes long-term value erosion in volatile markets | Intraday holding period. No overnight positions (by design). S15 targets single-day 2% moves |
| Gap risk | Leveraged products amplify overnight gaps | No overnight positions. Premarket sanity gate filters impossible gaps |
| Liquidity risk | Some leveraged ETPs have low trading volumes | Universe governance (W10) with low-liquidity handling. RVOL monitoring |
| Tracking error | Product may not perfectly track its stated leverage | Accepted risk. Monitoring via daily review (P3 PDF) |
| Counterparty risk | ETP issuer (WisdomTree, GraniteShares, Leverage Shares) could default | Diversification across 3 issuers. Monitor issuer credit |
| Delisting risk | Products may be delisted with limited notice | LSE registry (V2) monitors for delistings. Universe governance removes delisted tickers |

---

## 5. MIFID II CONSIDERATIONS

| Consideration | Assessment |
|---------------|-----------|
| **Retail Investor Classification** | Operator is assumed to be a retail investor under MiFID II. Leveraged products carry enhanced risk warnings |
| **Product Governance** | Leveraged ETPs are classified as complex products. ISA providers may require a suitability assessment before allowing trading |
| **Best Execution** | When the system moves to live trading, the broker must provide best execution. IBKR's SMART routing satisfies this |
| **Transaction Reporting** | Broker handles MiFID II transaction reporting. No additional obligation for the operator in paper mode |
| **Record Keeping** | MiFID II requires 5-year record retention. The system's artifact archival and audit logs should be preserved accordingly |

---

## 6. HMRC ISA RULES

| Rule | Assessment |
|------|-----------|
| **Annual Allowance** | 20,000 per tax year (2025-26). System starting equity 10,000 leaves 10,000 headroom |
| **Stocks and Shares ISA** | System instruments qualify for a Stocks and Shares ISA |
| **Day Trading in ISA** | Permitted. HMRC does not prohibit frequent trading within an ISA. However, if trading activity becomes so extensive that it constitutes a "trade" (in the tax sense), HMRC could potentially challenge the ISA status. This is very rare for personal accounts |
| **Tax Treatment** | All gains and income within an ISA are tax-free. No CGT, no income tax on dividends |
| **Withdrawals** | Flexible ISA: withdrawn amounts can be replaced within the same tax year without affecting the annual allowance (provider-dependent) |
| **Transfer Rules** | ISA can be transferred between providers without losing tax-free status |

---

## 7. COMPLIANCE CHECKLIST

This checklist must be completed before each gate transition:

### Gate 1: Paper Stable

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 1 | Data sources are appropriate for paper trading | [ ] | yfinance acceptable for paper mode |
| 2 | No commercial data licensing violations in paper mode | [ ] | Confirmed |
| 3 | System does not interact with any broker API | [ ] | Paper mode only |
| 4 | Audit logs are being generated and retained | [ ] | Verify log files exist |

### Gate 2: Paper Ready

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 5 | Data vendor migration plan exists | [ ] | DATA_VENDOR_MIGRATION_PLAN.md |
| 6 | ISA eligibility of all instruments verified | [ ] | Per-product verification with ISA provider |
| 7 | Record retention policy documented | [ ] | 5-year retention per MiFID II |
| 8 | Leveraged product risk warnings documented | [ ] | Section 4.3 of this document |

### Gate 3: Limited Live

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 9 | Commercial data provider active (Polygon.io or IBKR) | [ ] | yfinance not acceptable for live |
| 10 | ISA provider confirms eligibility of all traded instruments | [ ] | Written confirmation required |
| 11 | Broker API integration tested in paper mode on broker | [ ] | IBKR paper trading account |
| 12 | Legal review of automated trading in ISA wrapper completed | [ ] | Counsel consulted |
| 13 | MiFID II suitability assessment completed with broker | [ ] | If required by broker |
| 14 | Tax adviser consulted on ISA day-trading implications | [ ] | Precautionary measure |

---

## OPEN QUESTIONS

| # | Question | Owner | Due Date | Resolution |
|---|----------|-------|----------|-----------|
| 1 | Does Polygon.io Starter plan cover LSE leveraged ETPs? | Operator | Before Gate 2 | |
| 2 | Does the ISA provider (TBD) allow all 12 instruments? | Operator | Before Gate 3 | |
| 3 | Is frequent ISA trading a risk for HMRC "trading" classification? | Tax adviser | Before Gate 3 | |
| 4 | Does IBKR require MiFID II suitability for leveraged ETPs? | Operator | Before Gate 3 | |
| 5 | What is the data latency for Polygon LSE data (real-time vs 15-min)? | Operator | Before Gate 2 | |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial compliance notes (DRAFT) |

---

*End of Document NZT48-ANNEX-CN-001*
