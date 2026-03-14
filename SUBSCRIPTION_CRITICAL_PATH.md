# Subscription Critical Path: Phase 8 → Live Capital
**Executive Snapshot**
**Date**: 2026-03-10

---

## ONE-PAGE SUMMARY

**Question**: Does AEGIS V2 need new subscriptions to proceed to live trading after Eleventh-Order amendments?

**Answer**: **NO**. All 7 amendments require only code changes. No new vendor relationships needed.

---

## The 7 Amendments: Cost Impact Matrix

| # | Amendment | Vendor | Cost Change | Blocking? |
|---|-----------|--------|-------------|-----------|
| 1 | Polygon Grouped Endpoint | Polygon.io | $0 (Starter+ confirmed) | ❌ NO |
| 2 | YFinance Parallel Fetch (5 threads) | YFinance | $0 (already coded) | ❌ NO |
| 3 | EBS 100GB gp3 Upgrade | AWS | +$8/mo | ❌ NO (planned) |
| 4 | GARCH WAL Serialization | None (internal) | $0 | ❌ NO |
| 5 | Bounded Channel + try_send() | None (internal) | $0 | ❌ NO |
| 6 | Python Emergency Freeze Logic | None (internal) | $0 | ❌ NO |
| 7 | Permit Sweeper (reconciliation) | None (internal) | $0 | ❌ NO |

---

## Current Subscription Status

| Vendor | Tier | Cost | Status | Required? |
|--------|------|------|--------|-----------|
| **Interactive Brokers** | Paper mode | Free | ✅ Active | YES (live data) |
| **YFinance** | Free | $0/mo | ✅ Active | YES (primary LSE) |
| **Polygon.io** | Starter+ | TBD, <$30 | ✅ Tested & working | NO (fallback) |
| **TwelveData** | Undisclosed starter | TBD, ~$9-30 | ✅ Rate-limited 2026-03-10 | NO (backup) |
| **Alpha Vantage** | Free (5 req/min) | $0/mo | ✅ Active | NO (final fallback) |
| **AWS EC2** | c7i-flex.large | Free (FT) → $55/mo | ✅ Running | YES |
| **AWS EBS** | 50GB → 100GB | Free (FT) → $10/mo | ⏳ Upgrade today | YES |
| **Refinitiv/Bloomberg** | — | $15K+/year | ❌ Not started | NO (skip) |
| **Datadog/New Relic** | — | $45-200/mo | ❌ Not started | NO (post-live) |

---

## Critical Decision Points

### For Phase 8 (48-hour continuous paper run)
✅ **NO NEW SUBSCRIPTIONS REQUIRED**

- IB Gateway: ✅ Paper mode active
- YFinance: ✅ Free, no rate limits
- AWS: ✅ Free tier covers 48h run
- TwelveData: ✅ 800 calls/day limit fixed 2026-03-10

### For Phase 23 (100-trade validation)
✅ **NO NEW SUBSCRIPTIONS REQUIRED**

- All data sources tested in Phases 8-22
- Rate limits monitored during 63-day gauntlet
- Decision point: TwelveData upgrade IF call count > 800/day

### For Live Capital Deployment
⚠️ **TIER VALIDATION REQUIRED** (not new subscriptions)

| Vendor | Decision | Timeline |
|--------|----------|----------|
| **TwelveData** | Monitor during Phase 21-22 → May need "Grow" plan ($79/mo) if EU expansion | Before Phase 23 |
| **Polygon.io** | Monitor usage → Upgrade if added US equities | Before Phase 23 |
| **IB Gateway** | Switch from paper to live account (same API, no new cost) | At Phase 23 gate |
| **AWS** | Plan budget transition ($0 → $60-70/mo) | Between Phase 23 & Q2 |

---

## Blocking Issues: ZERO

| Phase | Vendor Blocker? | Data Blocker? | Cost Blocker? |
|-------|-----------------|---------------|---------------|
| Phase 8 | ❌ NO | ❌ NO | ❌ NO |
| Phase 11-22 | ❌ NO | ❌ NO | ❌ NO |
| Phase 23 (Crucible) | ❌ NO | ❌ NO | ❌ NO |
| Live Capital | ⚠️ Maybe (TwelveData) | ❌ NO | ❌ NO |

---

## Cost Timeline

| Phase | AWS EC2 | AWS EBS | TwelveData | Polygon | Total/Mo |
|-------|---------|---------|-----------|---------|----------|
| **Phase 8 (NOW)** | $0 (FT) | +$8 (100GB) | $0 | $0 | ~$8/mo |
| **Phases 11-22** | $0 (FT) | $8 | $0 | $0 | ~$8/mo |
| **Phase 23 Go** | $0 (FT) | $8 | $0-79 | $0-30 | $8-117/mo |
| **Live Capital** | $55 | $10 | $0-79 | $0-30 | $65-174/mo |

---

## Recommendation

### IMMEDIATE (TODAY)
1. ✅ Resize AWS EBS from 50GB → 100GB
2. ✅ Verify TwelveData call count <800/day (fixed 2026-03-10)
3. ✅ Confirm IB Gateway paper trading active

### PHASE 8 (Week 2)
- ✅ No vendor action required
- Monitor AWS free-tier balance (should have plenty)

### PHASE 21-22 (Week 10-12)
- Monitor TwelveData calls under live load
- Decision: Upgrade to "Grow" plan if needed for EU coverage

### PHASE 23 → LIVE (Week 15)
- Validate all data feeds working under 100+ concurrent positions
- Switch IB Gateway to live account (no new cost)
- Budget transition: $8/mo (paper) → $65-174/mo (live)

---

## FAQ

**Q: Do we need Refinitiv Eikon for live trading compliance?**
A: NO. Retail compliance via IB + YFinance. Refinitiv is institutional only ($15K+/year).

**Q: Should we subscribe to Datadog for monitoring?**
A: NO (not before live capital). DIY monitoring via Telegram + SQLite sufficient. Defer to Phase Q2.

**Q: Will TwelveData free tier limit us?**
A: Possibly for EU expansion. Monitor during Phase 21-22. Upgrade threshold: >800 calls/day.

**Q: Is AWS free tier truly free for 48 hours continuous?**
A: YES. c7i-flex.large is free-tier eligible. 48h run = minimal compute usage vs. 730h free allowance/month.

**Q: What if Polygon.io blocks LSE .L tickers (US-only)?**
A: Expected behavior. YFinance is primary for LSE; Polygon is fallback for US screening only.

---

## VERDICT

**AEGIS V2 can proceed to live capital with NO new vendor subscriptions.**

All 7 amendments are pure infrastructure improvements:
- Code changes: 6 (Polygon, YFinance, GARCH, channels, freeze, sweeper)
- Infrastructure changes: 1 (EBS resize, already planned)

**Cost increase**: ~$8-10/mo (Phase 8) → $65-174/mo (live), purely AWS + optional upgrades.

**Blocking items**: ZERO.

---

**Next milestone**: Phase 8 implementation ready. EBS resize TODAY. Start coding Monday.
