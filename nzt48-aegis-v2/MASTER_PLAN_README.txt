================================================================================
AEGIS V2 COMPLETE MASTER PLAN — 1000 HOUR ROADMAP
================================================================================

You now have 4 comprehensive documents guiding the entire 1,043-hour build:

1. COMPLETE_MASTER_PLAN_1000H.md (2,655 lines)
   ├─ Executive summary
   ├─ Phases 3-6 + Phase 24 (detailed with full code)
   ├─ Phases 7-22 + Phase 25 (detailed with full code)
   ├─ 5-10 test cases per phase
   ├─ Gate criteria for completion
   ├─ Realistic hour estimates
   └─ All implementation code (copy-paste ready)

2. MASTER_PLAN_QUICK_START.md (255 lines)
   ├─ Quick navigation of all phases
   ├─ Key file locations
   ├─ Test structure by phase
   ├─ Weekly execution checklist
   ├─ Weekly burn rate & timeline
   └─ Success criteria (0.3-0.8% daily)

3. PHASE_DEPENDENCIES_AND_GATES.md (500+ lines)
   ├─ Phase dependency graph (visual)
   ├─ Detailed gate criteria per phase
   ├─ Blocking/non-blocking dependencies
   ├─ Parallel work opportunities
   ├─ Risk mitigation strategies
   └─ Phase completion sign-off template

4. MASTER_PLAN_README.txt (this file)
   └─ Quick reference guide

================================================================================
EXECUTIVE SUMMARY
================================================================================

WHAT: A global 22-hour trading robot trading 20,000+ tickers across 6 exchanges
      with 33 independent modules + Quantum Apex neural weighting + Ouroboros
      nightly ML learning.

WHY: 0.3-0.8% daily returns (145-348% annualized) on £10,000 capital.

WHEN: 21 weeks at 20 hours/week (or compress to 4-5 months at 40h/week).

HOW: 25 phases, each with complete code, tests, and gate criteria.

================================================================================
QUICK START (START HERE)
================================================================================

1. Read MASTER_PLAN_QUICK_START.md (10 minutes)
   - Understand the phase structure
   - See the timeline
   - Check success criteria

2. Read COMPLETE_MASTER_PLAN_1000H.md (60 minutes)
   - Focus on Phases 3-6 (today's work)
   - Skim Phases 7-25 for structure
   - Note key file locations

3. Read PHASE_DEPENDENCIES_AND_GATES.md (30 minutes)
   - Understand phase dependencies
   - See detailed gate criteria
   - Note what can run in parallel

4. Execute Phase 3-6 (4.5 hours TODAY)
   - Follow code examples in COMPLETE_MASTER_PLAN_1000H.md
   - Run cargo test
   - Deploy to EC2

5. Monitor success with gate criteria
   - 565+ tests passing
   - Python brain receiving apex_snapshot JSON
   - ModeBPlus enum working
   - Trading halting at 23:00 UTC

================================================================================
KEY PHASES & TIMELINE
================================================================================

TODAY (4.5h):
  Phase 3-6: Wiring (Python Brain, ModeBPlus, rotation logic, tests)
  Phase 24: Quantum Apex (C++ FFI, DQN, Neural Hawkes)

Week 2 (15h):
  Phase 7: SubscriptionManager Full Rotation (5-sec, 20k tickers, 3 regions)

Weeks 3-4 (77h):
  Phase 8: Pre-Conditions & Wiring (gates for all 33 modules)

Week 5 (20h):
  Phase 9: Cross-Asset Macro (VIX, DXY, credit, F&G integration)

Weeks 6-10 (120h):
  Phases 10-15: 33 Module Integration (4h each)
  - Momentum (6), Mean Reversion (6), Volatility (6)
  - Cross-Asset (6), ML (6), Order Flow (3)

Weeks 11-12 (52h):
  Phase 16: Ouroboros Nightly Learning (10-step ML pipeline)

Week 13 (18h):
  Phase 17: Telemetry Dashboard (WebSocket + REST API)

Weeks 14-18 (80h):
  Phases 18-21: Multi-Exchange (TSE, HKEX, ASX, Euronext, US)

Weeks 19-20 (47h):
  Phase 22: Institutional Hardening (PnL, audit, kill switch, compliance)

Week 21 (20h):
  Phase 25: Live Capital Deployment (£1k → £2.5k → £5k → £10k)

Total: ~1,043 hours ≈ 21 weeks at 20h/week ≈ 8 months

================================================================================
SUCCESS CRITERIA
================================================================================

✓ Performance: 0.3-0.8% daily (£3-8 on £10k)
✓ Win Rate: 45%+ across all trades
✓ Sharpe Ratio: > 1.5
✓ Max Drawdown: < 8%
✓ Annual Projection: £10k → £50-100k+

✓ Operational: 22-hour continuous, 20k tickers, 33 modules, learning, telemetry
✓ Safety: Kill switch (< 100ms), circuit breaker (2% halt), 100% audit trail
✓ Production: £10k deployed, 7+ days profitable, institutional grade

================================================================================
KEY FILES TO READ
================================================================================

MASTER PLAN DOCUMENTS:
  /Users/rr/nzt48-signals/nzt48-aegis-v2/COMPLETE_MASTER_PLAN_1000H.md
  /Users/rr/nzt48-signals/nzt48-aegis-v2/MASTER_PLAN_QUICK_START.md
  /Users/rr/nzt48-signals/nzt48-aegis-v2/PHASE_DEPENDENCIES_AND_GATES.md

IMPLEMENTATION LOCATIONS:
  rust_core/src/subscription_manager.rs     (Phase 7: rotation)
  rust_core/src/preconditions.rs            (Phase 8: gates)
  rust_core/src/macro_integrations.rs       (Phase 9: macro)
  rust_core/src/modules/                    (Phases 10-15: 33 modules)
  src/ouroboros/                            (Phase 16: ML learning)
  src/telemetry/                            (Phase 17: dashboard)
  src/exchanges/                            (Phases 18-21: multi-exchange)
  src/compliance/                           (Phase 22: hardening)
  src/main.rs                               (Engine orchestration)

================================================================================
QUICK REFERENCE: GATE CRITERIA
================================================================================

Phase 3-6 (TODAY):
  ✓ 565+ tests passing
  ✓ apex_snapshot enum working
  ✓ ModeBPlus session mode
  ✓ Trading halts at 23:00 UTC

Phase 7:
  ✓ 3 regions rotating independently
  ✓ 5-second interval (±100ms)
  ✓ 20,000 ticker universe covered
  ✓ 5+ rotation tests passing

Phase 8:
  ✓ All 33 modules registered
  ✓ Custom gates per module
  ✓ Price/volume/volatility/time/macro checks working
  ✓ 7+ pre-condition tests passing

Phase 9:
  ✓ VIX, DXY, credit spreads, Fear & Greed fetched
  ✓ Macro signal -1..1 computed correctly
  ✓ Signal modulates module outputs
  ✓ 4+ macro integration tests passing

Phases 10-15:
  ✓ 95%+ test coverage per module
  ✓ 165+ tests total (5+ per module)
  ✓ Pre-condition gates active
  ✓ Macro modulation working

Phase 16:
  ✓ 10-step pipeline completes in 2 hours
  ✓ DQN training converges (loss < 0.1)
  ✓ Daily batch: 50+ trades labeled
  ✓ A/B test vs previous models (no degradation > 2%)

Phase 17:
  ✓ HTTP GET /telemetry/latest working
  ✓ WebSocket /telemetry/ws streaming
  ✓ <100ms latency
  ✓ All 33 signals in snapshot

Phases 18-21:
  ✓ 22-hour continuous trading verified
  ✓ 20,000+ global ticker coverage
  ✓ Time-zone conversions accurate (±1 second)
  ✓ No overlapping subscriptions
  ✓ 80 integration tests (20 per exchange)

Phase 22:
  ✓ Daily PnL reports (CSV + JSON)
  ✓ 100% audit trail
  ✓ Kill switch < 100ms
  ✓ Circuit breaker (2% halt) functional
  ✓ 6+ hardening tests

Phase 25:
  ✓ £1,000 live trading (7 days)
  ✓ £2,500 live trading (7 days, profitable)
  ✓ £5,000 live trading (14 days, profitable)
  ✓ £10,000 live trading (indefinite, 0.3-0.8% daily)
  ✓ Sharpe > 1.5, max drawdown < 8%

================================================================================
DEPENDENCIES & PARALLELIZATION
================================================================================

SEQUENTIAL CRITICAL PATH:
  Phase 0-2 → Phase 3-6 → Phase 7 → Phase 8 → Phase 9 → Phase 25

CAN RUN IN PARALLEL:
  Phase 24 (Quantum Apex) - independent C++ work
  Phases 10-15 (33 modules) - each module separate after Phase 8
  Phases 18-21 (exchanges) - each exchange separate after Phase 7
  Phase 17 (telemetry) - can mock data from modules

GATE-BLOCKING DEPENDENCIES:
  Phase 7 blocks: All exchange implementations (18-21)
  Phase 8 blocks: All modules (10-15)
  Phase 9 blocks: Live trading (affects all subsequent phases)
  Phases 10-15 blocks: Phase 16 learning, Phase 25 deployment
  Phase 16 blocks: Live deployment (needs trained models)

See PHASE_DEPENDENCIES_AND_GATES.md for detailed dependency graph.

================================================================================
EXECUTION DISCIPLINE
================================================================================

1. DON'T SKIP GATE CRITERIA
   - Gates exist to prevent accumulation of bugs
   - If a phase doesn't pass gates, DON'T proceed to next phase
   - Fix in current phase, re-verify gates

2. TEST-DRIVEN DEVELOPMENT
   - Write tests FIRST, then code
   - Each phase: 5-10 unit tests minimum
   - Coverage: 95%+ per module
   - Gate: ALL tests passing before proceeding

3. DOCUMENT AS YOU GO
   - Phase completion: update status file
   - Issues found: log with resolution
   - Code comments: every non-obvious section
   - Tests: clear docstrings explaining what's tested

4. WEEKLY SIGN-OFFS
   - Each week: verify gates for completed phases
   - Document any blockers/issues
   - Plan next week's work
   - Update project status file

5. NO HEROICS
   - 20h/week is sustainable long-term
   - Don't overcommit and miss gates
   - Quality > speed
   - Better to ship in 21 weeks with high confidence than 10 weeks with bugs

================================================================================
SUPPORT & DEBUGGING
================================================================================

If you get stuck:

1. Check PHASE_DEPENDENCIES_AND_GATES.md
   - Is this phase ready to run?
   - What are the gate criteria?
   - What tests must pass?

2. Check COMPLETE_MASTER_PLAN_1000H.md
   - Copy-paste the example code
   - Follow the test structure
   - Verify file locations match

3. Check test structure
   - Are tests in the right file?
   - Do they compile?
   - What exactly is failing?

4. Run cargo with verbose output:
   cargo test --release -- --nocapture --test-threads=1

5. Check EC2 logs:
   docker logs nzt48 --tail 100

6. Review recent commits:
   git log --oneline -20

================================================================================
YOU HAVE EVERYTHING YOU NEED
================================================================================

This is a complete, executable roadmap with:
  ✓ All 1,043 hours broken into 25 phases
  ✓ Complete implementation code (copy-paste ready)
  ✓ 5-10 unit tests per phase
  ✓ Gate criteria for progression
  ✓ Realistic hour estimates
  ✓ Dependencies mapped out
  ✓ Parallel work opportunities identified

No more planning. No more design discussions.
Just execute phases in order.

Start with Phase 3-6 TODAY (4.5 hours).
Then Phase 7 next week (15 hours).
Continue for 21 weeks.

At the end: A world-class trading system with £10k deployed and
0.3-0.8% daily returns (145-348% annualized).

Let's build.

================================================================================
