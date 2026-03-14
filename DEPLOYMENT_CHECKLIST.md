# AEGIS V2 DEPLOYMENT CHECKLIST

## Pre-Deployment Verification

### Infrastructure
- [ ] EC2 instance running (3.230.44.22)
- [ ] Docker Compose up (nzt48 + ib-gateway + nzt48-redis)
- [ ] IB Gateway connected to paper account (£10,000 ISA)
- [ ] PostgreSQL running with audit schema
- [ ] Redis password set (nzt48redis)
- [ ] Grafana accessible (:3000)

### Code & Configuration
- [ ] All 33 phase modules present in `/src/`
- [ ] Orchestrator compiled and tested
- [ ] Config files loaded (thresholds.yaml, assets.yaml, telegram.yaml)
- [ ] ISA-eligible asset list verified (12 LSE + EU + ASX + Japan)
- [ ] Telegram bot API key configured
- [ ] Database connection strings tested

### Data Feeds
- [ ] IB Gateway 5-second bars receiving
- [ ] Polygon.io fallback API working
- [ ] yfinance daily calibration functional
- [ ] Real-time VIX feed active
- [ ] Credit spread data available

### Safety Systems
- [ ] Kelly Criterion sizer: verified ruin prob <0.1%
- [ ] ISA auditor: 7-point checklist operational
- [ ] Circuit breaker: -4.0% configured
- [ ] Heat cap: GREEN/YELLOW/RED/BLACK levels set
- [ ] Ralph Wiggum checks: embedded in confidence scorer & position sizer

### Monitoring
- [ ] PostgreSQL tables created (trades, signals, positions, alerts, performance, audit)
- [ ] Redis keys initialized
- [ ] Grafana dashboards deployed (5 per-market + 1 global)
- [ ] Prometheus metrics scraping
- [ ] Telegram alerts configured
- [ ] Logging level set to INFO

## Deployment Steps

### Phase 1: Bootstrap (2 hours)
1. [ ] SSH into EC2
2. [ ] Clone/pull latest code from git
3. [ ] Verify all source files present
4. [ ] Run `python3 src/orchestrator.py` (sanity check)
5. [ ] Verify orchestrator executes test trade successfully

### Phase 2: Market Data (1 hour)
1. [ ] Verify IB Gateway connection (port 4004)
2. [ ] Subscribe to LSE 12-asset data feed
3. [ ] Check data latency (<5 seconds)
4. [ ] Verify yfinance daily close calibration
5. [ ] Monitor data feed health dashboard

### Phase 3: Live Paper Trading (24+ hours)
1. [ ] Gate 1: 100+ trades, Sharpe >0.3 → GO/NO-GO
2. [ ] Monitor ISA compliance (100% audit passing)
3. [ ] Track trade execution accuracy
4. [ ] Verify slippage vs model
5. [ ] Check position tracking accuracy

### Phase 4: Global Markets (48+ hours)
1. [ ] Activate Euronext data feed (Phase 30)
2. [ ] Place 50+ Euronext trades
3. [ ] Activate ASX data feed (Phase 31)
4. [ ] Place 50+ ASX trades
5. [ ] Verify multi-timezone orchestration

### Phase 5: Nightly Processes (7+ days)
1. [ ] Run Phase 23 (universe scan) nightly
2. [ ] Verify Phase 24 (threshold tuning) working
3. [ ] Monitor Phase 25 (edge durability) tracking
4. [ ] Check DSR calculations per regime

### Phase 6: Hybrid ML Training (14+ days)
1. [ ] Phase 27: DQN training loop active
2. [ ] Monitor loss curve (should decrease)
3. [ ] Phase 28: Transformer pattern recognition
4. [ ] Phase 29: Hybrid gate decision logic
5. [ ] Compare DQN vs 8-indicator performance

### Phase 7: Japan Capstone (Final)
1. [ ] Activate Japan data feed (Phase 33)
2. [ ] Place 50+ Nikkei trades (JST 09:00-15:00)
3. [ ] Verify timezone conversions (JST → UTC)
4. [ ] Verify 4-timezone orchestration (JST → CET → GMT → repeat)
5. [ ] Final validation: all 33 phases live

## Validation Gates

### Gate 1: After Phase 10 (End of Day 1)
**Criterion**: 100+ paper trades, Sharpe >0.3, win rate >30%
**Go Decision**: All checks pass → Proceed to Phase 11
**No-Go Decision**: Sharpe <0.3 → Debug signal quality, confidence thresholds

### Gate 2: After Phase 21 (End of Week 1)
**Criterion**: 600+ trades, ISA 100% audit passing, no critical bugs
**Go Decision**: All checks pass → Proceed to Phase 22
**No-Go Decision**: ISA violations → Fix compliance logic, redo G2

### Gate 3: After Phase 25 (End of Week 2)
**Criterion**: 800+ trades, Sharpe >0.5, nightly processes working
**Go Decision**: All checks pass → Proceed to Phase 26
**No-Go Decision**: Sharpe <0.5 → Review signal quality, check edge decay

### Gate 4: After Phase 29 (Week 3)
**Criterion**: 1,100+ trades, DQN trained, Sharpe ≥1.2 on validation
**Go Decision**: DQN superior to 8-indicator → Promote DQN to primary
**No-Go Decision**: DQN Sharpe <1.2 → Keep 8-indicator, retrain DQN

### Gate 5: After Phase 31 (Week 4)
**Criterion**: 1,600+ trades (500+ per market), 3-market sync verified
**Go Decision**: All markets firing, FX hedging working → Proceed to Japan
**No-Go Decision**: Spread/slippage issues → Debug per-market pricing

### Gate 6: After Phase 33 (Week 5)
**Criterion**: 1,800+ trades, Japan live, Sharpe ≥1.0, all 4 timezones operating
**Go Decision**: ALL checks pass → SYSTEM LIVE (paper mode)
**No-Go Decision**: Critical issue → Diagnose, fix, redo final validation

## Post-Deployment

### Daily Operations
- Monitor Grafana dashboards (5-minute check)
- Review Telegram alerts (morning, evening)
- Verify ISA compliance audit (should pass 100%)
- Track Sharpe ratio trend (should stay >0.8)
- Monitor data feed health

### Weekly Operations
- Review edge durability (DSR trend)
- Update universe scan results
- Recalibrate thresholds per regime
- Analyze trade attribution
- Check system health logs

### Monthly Operations
- Deep review of performance (win rate, Sharpe, etc.)
- Verify DSR is >1.0 (edge is real)
- Check ruin probability (should stay <0.1%)
- Review and update risk parameters
- Plan any feature improvements

## Success Criteria

✅ **System is considered LIVE when**:
1. All 33 phases implemented and integrated
2. All 6 validation gates passed
3. 1,800+ paper trades executed successfully
4. Sharpe ratio ≥1.0 across all markets
5. Win rate ≥40% per market/regime
6. ISA compliance 100% (all audits pass)
7. Max drawdown never exceeded -4.0%
8. All 4 timezones (JST, CET, GMT, plus UK) operating 24/7
9. Zero critical bugs in final 100 trades
10. System ready for immediate live trading deployment (when desired)

---

**Ready to Deploy**: March 13, 2026, 16:00 UTC
**All 33 Phases**: ✅ BUILT & TESTED
**Deployment Window**: 48-72 hours to live validation completion
