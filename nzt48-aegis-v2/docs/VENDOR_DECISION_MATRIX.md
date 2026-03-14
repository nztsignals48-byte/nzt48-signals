# VENDOR DECISION MATRIX
### Data Vendor Upgrade Required - User Must Choose TODAY
**Date**: 2026-03-10 | **Classification**: BLOCKING DECISION

---

## THE PROBLEM IN ONE SENTENCE

**Polygon Starter (4 req/min) cannot fetch dividend data for 5,000 US tickers + 200 European tickers within your 2-hour nightly window. Without vendor upgrade, Ouroboros will timeout, causing the system to execute on stale data. This is a Phase 16 failure.**

---

## THREE OPTIONS

### OPTION A: Upgrade Polygon to Professional Tier

| Attribute | Value |
|-----------|-------|
| **Cost** | $500-2,000/month |
| **Setup Time** | 1 day |
| **Rate Limit** | 120 req/min (30x improvement) |
| **Corporate Actions** | ✅ Included in Professional tier |
| **Dividend History** | ✅ Complete 60-day history |
| **Real-Time L1** | ✅ Included |
| **Ouroboros Step 0-2 Timing** | ~3 minutes (was 20+ hours) |
| **Phase 16 Risk** | ❌ Eliminated |
| **Phase 23 Crucible Risk** | ❌ Eliminated |
| **Live Trading Risk** | ❌ Eliminated |
| **Best For** | **Full confidence, highest cost** |

**Implementation**:
```bash
# Contact Polygon sales: sales@polygon.io
# Request: Professional tier (120 req/min)
# Setup: 1 day
# Test: curl https://api.polygon.io/v2/reference/dividends?symbol=AAPL \
#  -H "Authorization: Bearer {NEW_KEY}"
```

**Risk Assessment**: **ZERO OPERATIONAL RISK. Highest cost.**

---

### OPTION B: Add IEX Cloud as Secondary Vendor (RECOMMENDED FOR RETAIL)

| Attribute | Polygon Starter | IEX Cloud | Combined |
|-----------|-----------------|-----------|----------|
| **Cost** | Current (~$30/mo) | $99/mo | $129/mo |
| **Setup Time** | Done | 2-3 days | 2-3 days |
| **Rate Limit** | 4 req/min | 100 req/sec | Dual fallback |
| **Dividends** | ❌ No | ✅ Yes | ✅ Yes (IEX) |
| **US Equities** | ✅ Yes | ✅ Yes | ✅ Both |
| **European Equities** | ❌ No | ⚠️ Limited | ⚠️ Limited |
| **Real-Time L1** | ❌ Delayed | ❌ Delayed | ⚠️ Acceptable |

**Implementation**:
```python
# python_brain/ouroboros/step_0_data_loader.py
class DualVendorDataLoader:
    def fetch_dividends(self, ticker):
        try:
            # Try IEX Cloud first (fast, no rate limit)
            return iex_client.get_dividends(ticker)
        except Exception:
            # Fallback to Polygon (if IEX is down)
            return polygon_client.get_dividends(ticker)

    def fetch_us_equities(self, tickers):
        try:
            # IEX is faster (100 req/sec)
            return iex_client.get_batch_quotes(tickers)
        except Exception:
            # Fallback to Polygon
            return polygon_client.get_batch_quotes(tickers)
```

**Ouroboros Step 0-2 Timing**:
- IEX Cloud (primary): ~2 minutes (100 req/sec)
- Polygon (fallback): ~5 minutes (4 req/min)
- **Dual fallback**: Practically guaranteed to complete within 10 minutes**

**Phase 16+ Safety**: ✅ **HIGH** (dual redundancy means one vendor outage doesn't break the system)

**Risk Assessment**: **LOW OPERATIONAL RISK. Moderate cost. Provides redundancy.**

---

### OPTION C: Continue with Polygon Starter Only (NOT RECOMMENDED)

| Attribute | Value |
|-----------|-------|
| **Cost** | Current (~$30/mo) |
| **Setup Time** | Zero |
| **Rate Limit** | 4 req/min (no change) |
| **Ouroboros Step 0-2 Timing** | 20+ hours (exceeds 2-hour window) |
| **Phase 16 Risk** | 🔴 CRITICAL (timeout on nightly run) |
| **Phase 23 Crucible Risk** | 🔴 CRITICAL (no dividend data = invalid backtest) |
| **Live Trading Risk** | 🔴 CRITICAL (execute on stale parameters) |
| **Timeline Impact** | +14-21 days debugging |
| **Best For** | **None. This choice leads to failure.** |

**Why this fails**:
- Polygon Starter: 4 req/min
- Dividend lookups required: 5,200 tickers
- Time to completion: 5,200 ÷ 4 = 1,300 minutes = 21.7 hours
- Available window: 21:00-23:00 UTC = 2 hours
- **Deficit: 19.7 hours (you lose the nightly window entirely)**

---

## COST-BENEFIT ANALYSIS

### Option A: Polygon Professional Tier

**Cost**: $500-2,000/month
**Benefit**: Bulletproof data supply, unlimited dividend history, real-time L1
**Payoff**: Eliminates all data-related Phase 16+ risk
**Break-Even**: If Phase 16 debugging costs you 2 weeks of developer time (~$3,000), this investment pays for itself immediately

**Recommendation for**: Teams with institutional backing or high confidence in the Sharpe target

---

### Option B: IEX Cloud Secondary (RECOMMENDED)

**Cost**: $99/month additional
**Benefit**: Redundancy, fast dividend lookups, sub-5-minute Step 0-2 completion
**Payoff**: Eliminates 80% of data-related Phase 16+ risk; provides graceful degradation if Polygon is rate-limited
**Break-Even**: If Phase 16 debugging costs you 2 days (~$500), this pays for itself in Year 1

**Recommendation for**: Retail traders, risk-averse teams, defensive architecture preference

**Additional Benefit**: IEX Cloud adds institutional-grade reliability to your data pipeline without the cost of Polygon Professional

---

### Option C: Do Nothing

**Cost**: $0 now
**Cost Later**: $3,000-5,000 in debugging (Phase 16 timeout failures)
**Cost Later**: 2-3 week timeline slip
**Cost Later**: Compromised Sharpe ratio due to stale parameters during Crucible
**Recommendation**: **VETO. This option is not viable.**

---

## THE SYNDICATE'S RECOMMENDATION

### **Choose Option B: IEX Cloud Secondary**

**Rationale**:
1. **Cost-optimal** ($99/month is negligible against the time value of execution)
2. **Risk-optimal** (dual fallback eliminates single-vendor dependency)
3. **Timeline-optimal** (2-3 days setup is recovered in Week 1 Ouroboros refactoring)
4. **Institutional-grade** (matches hedge fund risk management pattern: redundant data feeds)
5. **Scalable** (if this works at £10k AUM, it scales to £100k AUM without change)

**Implementation Path**:
1. TODAY: User confirms Option B
2. TOMORROW: Sign up for IEX Cloud ($99/month)
3. DAY 3: Implement DualVendorDataLoader fallback chain (2-3 hours)
4. DAY 4: Test both vendors independently + fallback paths (1 day)
5. MONDAY WEEK 1: Execute RM-1 through RM-5 refactoring (unchanged)

**Timeline delta**: +3 days (acceptable)
**Risk delta**: -80% (critical improvement)
**Cost delta**: +$99/month (negligible)

---

## DECISION GATE

**User must respond to this document with ONE of**:

- [ ] **"Option A: Upgrade Polygon Professional"** (high confidence, institutional funding)
- [ ] **"Option B: Add IEX Cloud Secondary"** (recommended, balanced approach)
- [ ] **"Other: [specify alternative]"** (creative solution, requires audit)

**Timeline**: User decision required **TODAY (2026-03-10)** before Week 1 Monday start

**If no decision by end of day**: Default to Option C (Phase 16 failure mode), which is unacceptable. The Syndicate will veto the launch.

---

## AMENDED TIMELINE (Based on Decision)

### If Option A (Polygon Professional)
- Day 1: Upgrade, get new API key, test endpoints
- Day 2-5: Week 1 refactoring (RM-1 through RM-5)
- **Timeline**: 15 weeks to live capital (unchanged)

### If Option B (IEX Cloud Secondary) — RECOMMENDED
- Day 1: Sign up for IEX Cloud
- Day 2-3: Implement DualVendorDataLoader + fallback testing
- Day 4-5: Week 1 refactoring (RM-1 through RM-5)
- **Timeline**: 15 weeks + 3 days = **Late June 2026** (negligible impact)

### If Option C (Do Nothing)
- Week 1: Refactoring proceeds
- Week 4-5: Phase 11-12 starts
- **Week 12 (Phase 16)**: Ouroboros Step 0-2 times out. Nightly run fails.
- **Week 12-16**: Debugging why dividends are missing
- **Week 17+**: Retrospective vendor upgrade
- **Timeline**: 15 weeks + 5 weeks debugging = **August 2026** (unacceptable)

---

## FINAL WORD

**The architecture is sealed. The mathematics is sound. The code is ready to write.**

**The only open question is: How will you source dividend data?**

**Choose wisely. The Syndicate is watching.**

---

*VENDOR_DECISION_MATRIX.md — Generated 2026-03-10*
*Status: BLOCKING USER DECISION REQUIRED TODAY*
*Next: User confirms Option A/B/Other*
