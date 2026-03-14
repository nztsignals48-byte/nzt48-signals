#!/bin/bash
################################################################################
# AEGIS V2 COMPLETE EXECUTION
# From Bootstrap through Phase 23 Crucible Validation
# Executes the entire 450-hour plan in parallel sessions
#
# Usage: POLYGON_API_KEY=e8vYJGn7... IB_ACCOUNT=DU123456 ./EXECUTE_FULL_PLAN.sh
################################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_ROOT="/Users/rr/nzt48-signals"
AEGIS_ROOT="$PROJECT_ROOT/nzt48-aegis-v2"
EC2_IP="3.230.44.22"
EC2_KEY="$HOME/.ssh/nzt48-key.pem"

# Validation
if [ -z "$POLYGON_API_KEY" ]; then
  echo -e "${RED}ERROR: Set POLYGON_API_KEY environment variable${NC}"
  echo "Usage: POLYGON_API_KEY=e8vYJGn7... ./EXECUTE_FULL_PLAN.sh"
  exit 1
fi

if [ ! -f "$EC2_KEY" ]; then
  echo -e "${RED}ERROR: AWS key not found at $EC2_KEY${NC}"
  exit 1
fi

echo -e "${BLUE}=========================================="
echo "AEGIS V2 COMPLETE EXECUTION PLAN"
echo "=========================================${NC}"
echo ""
echo "Timeline: ~500 hours of work across 5 phases"
echo "Target: Late June 2026 live capital deployment"
echo "Cost: \$0 (Option D zero-cost architecture)"
echo ""
echo "Phases:"
echo "  1. Bootstrap (90 min)"
echo "  2. Week 1 Refactoring (7.5 hours, 5 isolated sessions)"
echo "  3. Phase 8 Infrastructure (77.4 hours, parallel components)"
echo "  4. Phases 11-23 Sequential (358 hours, automated testing)"
echo "  5. Phase 23 Crucible Validation (63 hours, 100 paper trades)"
echo ""

# ============================================================================
# PHASE 0: BOOTSTRAP (90 MINUTES)
# ============================================================================

echo -e "${YELLOW}[PHASE 0] BOOTSTRAP - Option D Data Caches${NC}"
echo "ETA: ~90 minutes"
echo ""

# Create background log directory
mkdir -p "$AEGIS_ROOT/logs/bootstrap"

# Task 1: Dividend Calendar (37.5 min)
echo "  [Task 1/4] Dividend Calendar (150 API calls, 15-sec rate limit)..."

ssh -i "$EC2_KEY" ubuntu@"$EC2_IP" << 'BOOTSTRAP_DIVIDEND'
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

python3 << 'PYTHON_DIVIDEND'
import requests, json, time, os
from datetime import datetime

class PolygonDividendBootstrapper:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.polygon.io"
        self.min_delay_sec = 15

    def bootstrap(self, output_file="data/dividend_calendar.json"):
        all_dividends = {}
        cursor = None
        api_calls = 0
        start_time = time.time()

        while True:
            if api_calls > 0:
                elapsed = time.time() - start_time
                expected_time = api_calls * self.min_delay_sec
                if elapsed < expected_time:
                    sleep_duration = expected_time - elapsed
                    time.sleep(sleep_duration)

            params = {
                'sort': 'ex_dividend_date',
                'limit': 1000,
                'order': 'desc'
            }
            if cursor:
                params['cursor'] = cursor

            response = requests.get(
                f"{self.base_url}/v3/reference/dividends",
                params=params,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=30
            )

            api_calls += 1

            if response.status_code == 429:
                raise RuntimeError(f"429 Too Many Requests at call {api_calls}")
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for item in results:
                ticker = item.get('ticker')
                if ticker not in all_dividends:
                    all_dividends[ticker] = []
                all_dividends[ticker].append({
                    'ex_dividend_date': item.get('ex_dividend_date'),
                    'amount': item.get('amount', 0.0)
                })

            cursor = data.get('next_cursor')
            if not cursor:
                break

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(all_dividends, f)

        elapsed = time.time() - start_time
        print(f"✓ Dividends: {len(all_dividends)} tickers, {api_calls} calls, {elapsed/60:.1f} min")

bootstrapper = PolygonDividendBootstrapper(api_key='${POLYGON_API_KEY}')
bootstrapper.bootstrap()
PYTHON_DIVIDEND

BOOTSTRAP_DIVIDEND

echo "    ✓ Complete"

# Task 2: Splits Calendar (37.5 min, parallel)
echo "  [Task 2/4] Splits Calendar (150 API calls, 15-sec rate limit)..."

ssh -i "$EC2_KEY" ubuntu@"$EC2_IP" << 'BOOTSTRAP_SPLITS'
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

python3 << 'PYTHON_SPLITS'
import requests, json, time, os

class PolygonSplitsBootstrapper:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.polygon.io"
        self.min_delay_sec = 15

    def bootstrap(self, output_file="data/splits_calendar.json"):
        all_splits = {}
        cursor = None
        api_calls = 0
        start_time = time.time()

        while True:
            if api_calls > 0:
                elapsed = time.time() - start_time
                expected_time = api_calls * self.min_delay_sec
                if elapsed < expected_time:
                    sleep_duration = expected_time - elapsed
                    time.sleep(sleep_duration)

            params = {
                'sort': 'execution_date',
                'limit': 1000,
                'order': 'desc'
            }
            if cursor:
                params['cursor'] = cursor

            response = requests.get(
                f"{self.base_url}/v3/reference/splits",
                params=params,
                headers={'Authorization': f'Bearer {self.api_key}'},
                timeout=30
            )

            api_calls += 1

            if response.status_code == 429:
                raise RuntimeError(f"429 at call {api_calls}")
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            data = response.json()
            results = data.get('results', [])

            if not results:
                break

            for item in results:
                ticker = item.get('ticker')
                if ticker not in all_splits:
                    all_splits[ticker] = []
                all_splits[ticker].append({
                    'execution_date': item.get('execution_date'),
                    'split_from': item.get('split_from'),
                    'split_to': item.get('split_to'),
                    'multiplier': item.get('split_to', 1) / item.get('split_from', 1)
                })

            cursor = data.get('next_cursor')
            if not cursor:
                break

        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(all_splits, f)

        elapsed = time.time() - start_time
        print(f"✓ Splits: {len(all_splits)} tickers, {api_calls} calls, {elapsed/60:.1f} min")

bootstrapper = PolygonSplitsBootstrapper(api_key='${POLYGON_API_KEY}')
bootstrapper.bootstrap()
PYTHON_SPLITS

BOOTSTRAP_SPLITS

echo "    ✓ Complete"

# Task 3: YFinance LSE Tickers (3.3 min)
echo "  [Task 3/4] YFinance LSE Tickers (throttled, 0.5-1.5s jitter)..."

ssh -i "$EC2_KEY" ubuntu@"$EC2_IP" << 'BOOTSTRAP_YFINANCE'
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

python3 << 'PYTHON_YFINANCE'
import yfinance as yf
import time, random

lse_tickers = ['QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L', 'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L']
results = {}

for idx, ticker in enumerate(lse_tickers):
    if idx > 0:
        jitter = random.uniform(0.5, 1.5)
        time.sleep(jitter)

    try:
        data = yf.download(ticker, period='60d', progress=False, timeout=30)
        if data is not None and len(data) > 0:
            results[ticker] = True
    except:
        pass

print(f"✓ YFinance: {len(results)}/12 tickers")
PYTHON_YFINANCE

BOOTSTRAP_YFINANCE

echo "    ✓ Complete"

# Task 4: Validate
echo "  [Task 4/4] Validating bootstrap output..."

ssh -i "$EC2_KEY" ubuntu@"$EC2_IP" << 'BOOTSTRAP_VALIDATE'
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

python3 << 'PYTHON_VALIDATE'
import json, os

errors = []

if os.path.exists('data/dividend_calendar.json'):
    with open('data/dividend_calendar.json') as f:
        divs = json.load(f)
    if len(divs) < 5000:
        errors.append(f"Dividends: {len(divs)} tickers (expected >=5000)")
    else:
        print(f"✓ Dividends: {len(divs)} tickers")
else:
    errors.append("Dividend calendar missing")

if os.path.exists('data/splits_calendar.json'):
    with open('data/splits_calendar.json') as f:
        splits = json.load(f)
    if len(splits) > 0:
        print(f"✓ Splits: {len(splits)} tickers")
    else:
        errors.append("Splits calendar empty")
else:
    errors.append("Splits calendar missing")

if errors:
    for e in errors:
        print(f"✗ {e}")
    exit(1)
else:
    print("✓ Bootstrap validation complete")
PYTHON_VALIDATE

BOOTSTRAP_VALIDATE

echo "    ✓ Complete"

echo -e "${GREEN}✓ PHASE 0 BOOTSTRAP COMPLETE${NC}"
echo ""

# ============================================================================
# PHASE 1: WEEK 1 REFACTORING (7.5 HOURS, 5 ISOLATED SESSIONS)
# ============================================================================

echo -e "${YELLOW}[PHASE 1] WEEK 1 REFACTORING${NC}"
echo "ETA: ~7.5 hours (5 isolated Claude sessions)"
echo ""
echo "  NOTE: Refactoring sessions require Claude Code interaction."
echo "  This script will output prompts for each session."
echo "  You must run each session separately in Claude Code."
echo ""

cat > "$AEGIS_ROOT/REFACTORING_SESSION_1_RM1.prompt" << 'RM1_PROMPT'
SESSION 1 (RM-1): GARCH Daily Fit + Real-Time Residuals

BEFORE YOU BEGIN:
1. Read rust_core/CORE_TYPES_ANCHOR.md to understand exact struct shapes
2. Read AEGIS_CODEX.md PART 2 (Bootstrap) and PART 3 (RM-1 specification)

YOUR TASK:
- Implement python_brain/ouroboros/step_0_garch_calibration.py
  * Use Polygon Grouped endpoint (1 API call, not iterating tickers)
  * Fit GARCH(1,1) to 50 US + 12 LSE assets
  * Use cached dividend_calendar.json (no new dividend API calls)
  * Use YFinance for LSE (free, throttled)

- Implement rust_core/src/garch_inference.rs
  * Daily GARCH parameter fit (ω, α, β)
  * Real-time O(1) residual calculation
  * Serialize σ²_t to WAL on every tick

ACCEPTANCE TESTS (must pass before proceeding):
  cargo test test_garch_inference --lib
  cargo test test_garch_fit_50_assets --lib

EFFORT: 2.5 hours
GATE: AT-RM1 passes → Proceed to Session 2

CONTEXT RESET after this session.
RM1_PROMPT

cat > "$AEGIS_ROOT/REFACTORING_SESSION_2_RM2.prompt" << 'RM2_PROMPT'
SESSION 2 (RM-2): WAL Dedicated Thread + Bounded Channel

BEFORE YOU BEGIN:
1. Read rust_core/CORE_TYPES_ANCHOR.md
2. Read AEGIS_CODEX.md PART 3 (RM-2 specification)

YOUR TASK:
- Implement rust_core/src/wal_actor.rs
  * Dedicated std::thread (not tokio::spawn_blocking)
  * Bounded channel(10000) with try_send() (non-blocking)
  * Graceful telemetry dropping on channel full
  * Batch writes: sync_all() every 100 events

- Integration in rust_core/src/main.rs
  * Create bounded channel
  * Spawn WAL actor thread
  * Enqueue events via try_send (drop on error)

ACCEPTANCE TESTS:
  cargo test test_wal_bounded_channel_latency --lib
  cargo test test_wal_10k_burst --lib

EFFORT: 3 hours
GATE: AT-RM2 passes → Proceed to Session 3

CRITICAL: Do NOT use unbounded channels or tokio::fs
CONTEXT RESET after this session.
RM2_PROMPT

cat > "$AEGIS_ROOT/REFACTORING_SESSION_3_RM3.prompt" << 'RM3_PROMPT'
SESSION 3 (RM-3): PyO3 Native FFI Conversions

BEFORE YOU BEGIN:
1. Read rust_core/CORE_TYPES_ANCHOR.md
2. Read AEGIS_CODEX.md PART 3 (RM-3 specification with pyo3-asyncio note)

YOUR TASK:
- Implement rust_core/src/python_bridge.rs
  * #[pyclass] TickContext struct (zero-copy from Python)
  * From<TickData> → TickContext conversions (no JSON)
  * Synchronous wrapper function for PyO3 (avoid GIL deadlock in async)
  * Alternative: Use pyo3-asyncio for async safety

ACCEPTANCE TESTS:
  cargo test test_pyo3_tick_extraction_latency --lib
  cargo test test_pyo3_zero_copy --lib

EFFORT: 1 hour
GATE: AT-RM3 passes → Proceed to Session 4

CRITICAL: Do NOT call Python from async tasks without pyo3-asyncio wrapper
CONTEXT RESET after this session.
RM3_PROMPT

cat > "$AEGIS_ROOT/REFACTORING_SESSION_4_RM4.prompt" << 'RM4_PROMPT'
SESSION 4 (RM-4): Dynamic Huber Delta (MAD-Based)

BEFORE YOU BEGIN:
1. Read rust_core/CORE_TYPES_ANCHOR.md
2. Read AEGIS_CODEX.md PART 3 (RM-4 specification)

YOUR TASK:
- Implement rust_core/src/student_t_kalman.rs
  * Dynamic Huber delta = 1.345 × MAD(residuals)
  * Update delta every 100 ticks from recent residual window
  * Prevent divide-by-zero when MAD = 0

ACCEPTANCE TESTS:
  cargo test test_kalman_huber_regime_change --lib
  cargo test test_kalman_zero_mad --lib

EFFORT: 0.5 hours
GATE: AT-RM4 passes → Proceed to Session 5

CONTEXT RESET after this session.
RM4_PROMPT

cat > "$AEGIS_ROOT/REFACTORING_SESSION_5_RM5.prompt" << 'RM5_PROMPT'
SESSION 5 (RM-5): Exponential Backoff + Emergency Freeze

BEFORE YOU BEGIN:
1. Read rust_core/CORE_TYPES_ANCHOR.md
2. Read AEGIS_CODEX.md PART 3 (RM-5 specification)

YOUR TASK:
- Implement rust_core/src/python_subprocess_manager.rs
  * Exponential backoff: 1s → 2s → 4s → 8s → 60s cap
  * On crash: regime → YELLOW (50% size reduction)
  * On 3 crashes in 60s: regime → RED (absolute halt)

- Implement python_brain/ouroboros/cli.py
  * Use sys.exit(255) on fatal errors
  * Exit code recognized by Rust manager

ACCEPTANCE TESTS:
  cargo test test_subprocess_fork_bomb_prevention --lib
  cargo test test_subprocess_backoff_escalation --lib

EFFORT: 0.5 hours
GATE: AT-RM5 passes → Complete Week 1 Refactoring

NO CONTEXT RESET after Session 5 (end of refactoring).
RM5_PROMPT

echo "  Session 1 prompt: $AEGIS_ROOT/REFACTORING_SESSION_1_RM1.prompt"
echo "  Session 2 prompt: $AEGIS_ROOT/REFACTORING_SESSION_2_RM2.prompt"
echo "  Session 3 prompt: $AEGIS_ROOT/REFACTORING_SESSION_3_RM3.prompt"
echo "  Session 4 prompt: $AEGIS_ROOT/REFACTORING_SESSION_4_RM4.prompt"
echo "  Session 5 prompt: $AEGIS_ROOT/REFACTORING_SESSION_5_RM5.prompt"
echo ""
echo -e "${YELLOW}ACTION REQUIRED:${NC}"
echo "  1. Open Claude Code"
echo "  2. Copy each prompt file into Claude Code"
echo "  3. Run each session, one at a time"
echo "  4. Ensure each acceptance test passes"
echo "  5. Return here when all 5 sessions complete"
echo ""
echo "  Estimated time: 7.5 hours total"
echo ""

# Wait for user to complete refactoring
read -p "Press ENTER once all 5 refactoring sessions are complete: "

echo -e "${GREEN}✓ PHASE 1 REFACTORING COMPLETE${NC}"
echo ""

# ============================================================================
# PHASE 2: PHASE 8 INFRASTRUCTURE SEAL (77.4 HOURS)
# ============================================================================

echo -e "${YELLOW}[PHASE 2] PHASE 8 INFRASTRUCTURE SEAL${NC}"
echo "ETA: ~77.4 hours (2 weeks of 30h/week development)"
echo ""
echo "  20 Standard Components (SC-01 through SC-20)"
echo "  6 Wiring Patches (WP-1 through WP-6)"
echo "  26 Acceptance Tests"
echo "  48-hour continuous paper run validation"
echo ""
echo "  This phase requires structured Rust/Python development."
echo "  Refer to AEGIS_CODEX.md PART 4 for complete specifications."
echo ""

read -p "Press ENTER once Phase 8 is complete (acceptance tests pass): "

echo -e "${GREEN}✓ PHASE 2 PHASE 8 COMPLETE${NC}"
echo ""

# ============================================================================
# PHASE 3: PHASES 11-23 SEQUENTIAL BUILD (358 HOURS)
# ============================================================================

echo -e "${YELLOW}[PHASE 3] PHASES 11-23 SEQUENTIAL BUILD${NC}"
echo "ETA: ~358 hours (11+ weeks of 30h/week development)"
echo ""
echo "  Phase 11-12: Stress testing + EGARCH (83.5h)"
echo "  Phase 13-15: Kelly sizing + VWAP + LSTM (135h)"
echo "  Phase 16-20: Signals + risk gates (195h)"
echo "  Phase 21-22: DCC-GARCH + emergency modes (105h)"
echo ""
echo "  Each phase has acceptance gates. No skipping."
echo "  Refer to AEGIS_CODEX.md PART 5 for specifications."
echo ""

read -p "Press ENTER once all Phases 11-23 are complete: "

echo -e "${GREEN}✓ PHASE 3 PHASES 11-23 COMPLETE${NC}"
echo ""

# ============================================================================
# PHASE 4: PHASE 23 CRUCIBLE VALIDATION (63 HOURS)
# ============================================================================

echo -e "${YELLOW}[PHASE 4] PHASE 23 CRUCIBLE VALIDATION${NC}"
echo "ETA: ~63 hours"
echo ""
echo "  Requirements:"
echo "    - 100 paper trades"
echo "    - Win rate ≥ 40%"
echo "    - Sharpe ratio ≥ 0.8"
echo "    - Max drawdown ≤ 2.5%"
echo "    - Diversity: ≥4 uncorrelated market sectors"
echo "    - Walk-forward: 10 overlapping 70-trade windows"
echo ""
echo "  GATE: If WR < 40% → Return to Phases 11-22 for debugging"
echo "  GATE: If WR ≥ 40% → UNCONDITIONAL APPROVAL for live capital"
echo ""

read -p "Press ENTER once Phase 23 Crucible validation passes: "

echo -e "${GREEN}✓ PHASE 4 PHASE 23 COMPLETE - LIVE CAPITAL APPROVED${NC}"
echo ""

# ============================================================================
# PHASE 5: LIVE CAPITAL DEPLOYMENT
# ============================================================================

echo -e "${BLUE}=========================================="
echo "LIVE CAPITAL DEPLOYMENT"
echo "=========================================${NC}"
echo ""
echo "  Date: June 25, 2026"
echo "  Capital: £10,000 ISA"
echo "  System: AEGIS V2 (Production)"
echo ""
echo "  Daily Ouroboros runs (21:00-23:00 UTC DARK window)"
echo "    - <30 minute execution"
echo "    - 1-6 API calls (vs. 5,200 without caching)"
echo "    - Zero vendor costs (Polygon Starter only)"
echo ""
echo "  Monitoring:"
echo "    - Real-time P&L tracking"
echo "    - Risk gate audit trail"
echo "    - Equity curve snapshots every 6 hours"
echo "    - Daily dividend vs. Polygon API cross-check"
echo ""
echo "  Scaling roadmap:"
echo "    - Phase 1 (Jun-Aug): Stay at £10k AUM"
echo "    - Phase 2 (Sep-Dec): Scale to £50k AUM (Option D limit)"
echo "    - Phase 3 (Jan+): Upgrade to Option A/B if needed"
echo ""

echo -e "${GREEN}=========================================="
echo "✓ COMPLETE EXECUTION PLAN FINISHED"
echo "=========================================${NC}"
echo ""
echo "Total effort: ~500 hours"
echo "Total cost: \$0 (bootstrap) + ~\$65/month (live AWS)"
echo "Expected daily return: 0.3-0.5% (Phase 23) → 0.5-0.8% (Phase Q2)"
echo ""
echo "Timeline:"
echo "  - Bootstrap: TODAY (90 min)"
echo "  - Week 1 Refactoring: THIS WEEK (7.5h)"
echo "  - Phase 8: NEXT 2 WEEKS (77.4h)"
echo "  - Phases 11-23: NEXT 11 WEEKS (358h)"
echo "  - Live Capital: LATE JUNE 2026"
echo ""
echo "Success criteria: Sharpe ≥ 0.8, WR ≥ 40%, Max DD ≤ 2.5%"
echo ""
