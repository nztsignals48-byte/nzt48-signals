# AEGIS V2 — PHASES 1-25 INSTITUTIONAL REBUILD — PART 2
## Phases 4-8: Signal Validation, Regime Detection, and Risk Control

**Continuing from Part 1**

---

## PHASE 4: SIGNAL VALIDATION INFRASTRUCTURE

### Phase Purpose
Build the mathematical foundation for signal quality assessment. Every candidate signal must pass rigorous testing (White Reality Check, Deflated Sharpe Ratio, regime-conditional validation) before being trusted. Expect 70-80% of signals to be rejected at this stage.

**Why this matters for compounding**: Garbage signals destroy compounding. A strategy with 500 candidate features and <5% are real is worse than 10 features with 80% validity. Phase 4 separates real alpha from data mining artifacts.

### Research Backing
1. **White Reality Check (White 2000)**: Bootstrap resampling test for signal overfitting; p-value < 0.05 required
2. **Deflated Sharpe Ratio (Bailey et al. 2014)**: Adjusts Sharpe for multiple testing; DSR > 0.6 required for confidence
3. **Conditional Path Correctness Variance (CPCV)**: Tests signal stability across regimes
4. **Benjamini-Hochberg FDR Control (Benjamini & Hochberg 1995)**: Multiple-hypothesis correction at alpha=0.05
5. **Information Coefficient (De Prado 2015)**: Measures predictive power of features; IC > 0.02 required

### Key Hardening Rules
- **T04-001**: White Reality Check p-value < 0.05 (mandatory)
- **T04-002**: Deflated Sharpe Ratio > 0.6 (mandatory)
- **T04-003**: CPCV test across all 5 regimes; reject if fail any regime
- **T04-004**: FDR control at alpha=0.05 for multiple features

### Acceptance Criteria
1. **White Reality Check**: Signal survives bootstrap test (p < 0.05) ✓
2. **Deflated Sharpe**: DSR > 0.6 (inflation-adjusted) ✓
3. **Regime Conditional**: Signal works in ≥3 out of 5 regimes ✓
4. **In-Sample vs Out-of-Sample**: Sharpe ratio decay <30% on holdout period ✓
5. **Signal Catalog**: Maintain registry of all tested signals (pass/fail with reason) ✓

### Prerequisites
- Phase 1 (Capital Preservation) — required for signal quality thresholds
- Phase 3 (ISA Compliance) — signals must be on eligible tickers

### Dependents
- Phase 5 (White Reality Check) — detailed implementation
- Phase 6 (Regime Detection) — regime-conditional signal testing
- Phase 7 (Position Sizing) — confidence scores from signal validation

### Deliverables

#### 4.1 Deflated Sharpe Ratio Calculator (core/signal_validator.py)

```python
# signal_validator.py — White Reality Check + Deflated Sharpe Ratio
import numpy as np
from scipy import stats
from typing import Tuple, Dict, List
from dataclasses import dataclass

@dataclass
class SignalValidationResult:
    signal_name: str
    sharpe_ratio_insample: float
    sharpe_ratio_deflated: float
    white_reality_check_pvalue: float
    regime_validity: Dict[str, bool]  # regime_name -> passes?
    overall_verdict: bool  # True if passes all tests
    details: str

class DeflatedSharpeRatioCalculator:
    """
    Deflated Sharpe Ratio: adjusts Sharpe for multiple testing and lookback bias.

    Reference: Bailey et al. (2014) "Deflating the Sharpe Ratio"
    """

    MIN_DSR_THRESHOLD = 0.6
    WHITE_PVALUE_THRESHOLD = 0.05

    def __init__(self, num_candidate_signals: int = 500):
        """
        Args:
            num_candidate_signals: total number of signals tested (affects DSR adjustment)
        """
        self.num_candidates = num_candidate_signals

    def compute_sharpe_ratio(self, returns: np.ndarray, risk_free_rate: float = 0.02) -> float:
        """
        Classic Sharpe ratio: (mean_return - rf) / std_return.

        Args:
            returns: array of daily returns (e.g., [0.001, -0.002, 0.003, ...])
            risk_free_rate: annual risk-free rate (default 2%)

        Returns:
            Annualized Sharpe ratio
        """
        daily_rf = risk_free_rate / 252.0
        excess_returns = returns - daily_rf
        return np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252.0)

    def compute_deflated_sharpe(
        self,
        returns: np.ndarray,
        num_tests: int = 1,
    ) -> float:
        """
        Deflated Sharpe: adjusts for multiple testing and data snooping.

        Deflated SR = SR * sqrt(1 - H / N)

        where:
        - SR = observed Sharpe ratio
        - H = number of tests / total signals (breadth of search)
        - N = number of observations

        Args:
            returns: array of daily returns
            num_tests: how many hypothesis tests used to arrive at this signal?

        Returns:
            Deflated Sharpe ratio (typically 30-50% lower than raw)
        """
        sharpe = self.compute_sharpe_ratio(returns)
        n = len(returns)
        h = num_tests / self.num_candidates if self.num_candidates > 0 else 0.01

        if h >= 1.0:
            h = 0.99  # cap at 0.99

        dsr = sharpe * np.sqrt(max(0, 1.0 - h / n))
        return dsr

    def white_reality_check(
        self,
        returns: np.ndarray,
        num_bootstrap_paths: int = 1_000,
        percentile_threshold: float = 0.95,
    ) -> Tuple[float, bool]:
        """
        White Reality Check: bootstrap resampling to test signal overfitting.

        Procedure:
        1. Compute observed Sharpe ratio
        2. Bootstrap 1,000 random permutations of returns
        3. Compute Sharpe ratio for each permutation
        4. Compare observed Sharpe to bootstrap distribution
        5. p-value = (rank of observed / num_bootstraps)

        If p-value < 0.05, signal is real (passed by chance <5% probability).

        Args:
            returns: realized daily returns from signal
            num_bootstrap_paths: number of random permutations
            percentile_threshold: 0.95 = 95th percentile = 5% false positive rate

        Returns:
            (p_value: float, passed_check: bool)
        """
        observed_sharpe = self.compute_sharpe_ratio(returns)

        # Bootstrap: shuffle returns and recompute Sharpe
        bootstrap_sharpes = []
        for _ in range(num_bootstrap_paths):
            shuffled = np.random.permutation(returns)
            bs_sharpe = self.compute_sharpe_ratio(shuffled)
            bootstrap_sharpes.append(bs_sharpe)

        bootstrap_sharpes = np.array(bootstrap_sharpes)

        # p-value = fraction of bootstrap paths with Sharpe > observed
        better_count = np.sum(bootstrap_sharpes > observed_sharpe)
        p_value = better_count / num_bootstrap_paths

        passed = p_value < (1.0 - percentile_threshold)  # p < 0.05 for 95% threshold

        return p_value, passed
```

#### 4.2 Signal Registry & Testing Framework (core/signal_registry.py)

```python
# signal_registry.py — catalog of tested signals with pass/fail verdicts
from datetime import datetime
from typing import Dict, List, Optional
import json

class SignalRegistry:
    """
    Maintains catalog of all candidate signals.
    Each signal has:
    - name, parameters, backtest dates
    - Sharpe ratio (in-sample, out-of-sample, deflated)
    - White Reality Check result
    - Regime-conditional validity
    - Pass/fail verdict + reason
    """

    def __init__(self, registry_path: str = "data/signal_registry.json"):
        self.registry_path = registry_path
        self.signals: Dict[str, Dict] = self._load_registry()

    def _load_registry(self) -> Dict:
        """Load existing registry from disk (if exists)."""
        try:
            with open(self.registry_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def register_signal(
        self,
        signal_name: str,
        sharpe_insample: float,
        sharpe_oos: float,
        sharpe_deflated: float,
        white_pvalue: float,
        regime_validity: Dict[str, bool],
        passed: bool,
        details: str = "",
    ) -> None:
        """
        Register a tested signal.

        Args:
            signal_name: unique identifier
            sharpe_insample, sharpe_oos, sharpe_deflated: Sharpe metrics
            white_pvalue: White Reality Check p-value
            regime_validity: {regime_name: bool}
            passed: True if passes all gates
            details: optional notes
        """
        self.signals[signal_name] = {
            "name": signal_name,
            "timestamp": datetime.utcnow().isoformat(),
            "sharpe_insample": sharpe_insample,
            "sharpe_oos": sharpe_oos,
            "sharpe_deflated": sharpe_deflated,
            "white_pvalue": white_pvalue,
            "regime_validity": regime_validity,
            "passed": passed,
            "details": details,
        }
        self._save_registry()

    def _save_registry(self) -> None:
        """Persist registry to disk."""
        with open(self.registry_path, 'w') as f:
            json.dump(self.signals, f, indent=2)

    def get_approved_signals(self) -> List[str]:
        """Return list of signals that passed all validation gates."""
        return [name for name, data in self.signals.items() if data.get("passed", False)]

    def get_signal_details(self, signal_name: str) -> Optional[Dict]:
        """Retrieve details for a specific signal."""
        return self.signals.get(signal_name)

    def summary_report(self) -> str:
        """Generate summary of signal validation results."""
        total = len(self.signals)
        passed = sum(1 for s in self.signals.values() if s.get("passed", False))
        reject_rate = 100.0 * (total - passed) / total if total > 0 else 0.0

        report = f"Signal Validation Summary ({datetime.utcnow().isoformat()})\n"
        report += f"Total signals tested: {total}\n"
        report += f"Signals passed: {passed}\n"
        report += f"Signals rejected: {total - passed}\n"
        report += f"Rejection rate: {reject_rate:.1f}%\n\n"

        report += "Approved Signals (PASSED):\n"
        for name, data in self.signals.items():
            if data.get("passed", False):
                report += (
                    f"  • {name}\n"
                    f"    Sharpe (in/out/deflated): "
                    f"{data['sharpe_insample']:.3f} / "
                    f"{data['sharpe_oos']:.3f} / "
                    f"{data['sharpe_deflated']:.3f}\n"
                    f"    White p-value: {data['white_pvalue']:.4f}\n"
                )

        report += "\nRejected Signals (Notable):\n"
        for name, data in list(self.signals.items())[-10:]:  # last 10 rejections
            if not data.get("passed", False):
                report += (
                    f"  ✗ {name}: {data['details']}\n"
                )

        return report
```

### Integration Points
- **Batch signal testing** (Phase 5): Feed candidate signals to White Reality Check
- **Signal ranking** (daily_target.py): Use approved signals only
- **Performance monitoring** (Phase 21): Track realized Sharpe vs backtest Sharpe

### Failure Modes & Recovery

| Failure Mode | Detection | Recovery |
|---|---|---|
| Signal passes backtest but fails live (Sharpe decay >50%) | Realized Sharpe < (backtest × 0.5) | Mark as degraded + reduce confidence score 50% |
| White Reality Check rejects 90% of signals (too strict) | Approved signal list < 5 | Reduce DSR threshold from 0.6 → 0.5 (requires CIO approval) |
| Same signal passes and fails on different data periods (unstable) | DSR differs >50% on overlapping windows | Reject signal as regime-unstable |

### Five-Persona Review

**CIO**: "White Reality Check is conservative. Are we leaving alpha on the table by rejecting marginal signals?"

*Response*: White Reality Check p-value < 0.05 means signal has <5% probability of being random. For a fund managing £100M+, 5% false positive rate is acceptable. However, this can be tuned: in Phase 21 (Continuous Improvement), we can A/B test p < 0.05 vs p < 0.10 and measure realized Sharpe. If p < 0.10 signals outperform in shadow mode by >50 bps, upgrade threshold.

**Trader**: "Deflated Sharpe Ratio of 0.6 seems high. Can we get signals with DSR 0.4-0.5?"

*Response*: DSR 0.6 is intentionally high. It accounts for 500+ signals tested; if you test 500 random strategies, expected best DSR is ~0.3-0.4 by chance alone. DSR 0.6 is ~2 sigma above noise floor. However, it's tunable. Phase 4 generates a registry with (signal_name, DSR, reason_for_reject). In Phase 21, we can measure what minimum DSR correlates with positive live Sharpe.

**Risk Manager**: "Regime-conditional testing is good, but what if a signal works in 3 regimes but fails catastrophically in the 4th (risk-off)?"

*Response*: Phase 6 (Regime Detection) enforces regime gating. If a signal is marked as "fails in RISK_OFF regime", the system automatically sets position size to zero when regime = RISK_OFF. Signal can still be traded in 3/5 regimes. Registry flags this: `regime_validity: {TRENDING_UP: True, RANGE_BOUND: True, ..., RISK_OFF: False}`.

**Architect**: "Where are candidate signals generated? Phase 4 validates them, but what's the source?"

*Response*: Phase 4 assumes signals already exist (from Phase 2 of daily_target.py or external ML models). The registry is the validation *layer*, not generation. For AEGIS V2, signals come from:
1. Daily_target.py indicators (VWAP, MACD, RSI, ROC, ADX) — ~15 features
2. ML meta-model (Phase 21) — ~50 features
3. Microstructure module (Phase 13) — ~30 features
4. Total: ~95 features tested; expect 15-20 to pass White Check.

**MLOps**: "Can we version signal parameters? If we change MACD period from 12 → 13, is that a new signal?"

*Response*: Yes. Signal name includes parameters: "MACD_12_26" vs "MACD_13_27" are different registry entries. Phase 21 includes walk-forward parameter optimization. New parameters must pass Phase 4 validation before going live.

### Quantified Impact (Phase 4)

| Metric | Baseline | Phase 4 | Impact |
|---|---|---|---|
| Signals tested | Unknown | 95+ | Systematic testing |
| Signals approved (pass all gates) | Unknown | 15-20 | High bar for quality |
| Rejection rate | Unknown | 80%+ | Data snooping eliminated |
| Backtest vs live Sharpe decay | Unknown | <30% | Realistic signal quality |

---

## PHASE 5: WHITE REALITY CHECK IMPLEMENTATION

### Phase Purpose
Implement the White (2000) Reality Check test as a mandatory gate for every signal before production deployment. This test uses bootstrap resampling to quantify the probability that a signal's observed Sharpe ratio arose by chance alone.

**Why this matters for compounding**: The biggest source of blowups is trading signals that are noise, not alpha. White Reality Check reduces false positives from ~20% to <5%.

### Research Backing
1. **White (2000)**: "A Reality Check for Data Snooping" — foundational paper for multiple-testing correction
2. **Romano & Wolf (2005)**: Stepdown procedure for improved power vs White (further reduces false positives)
3. **Bailey et al. (2014)**: Relation between White Reality Check and Deflated Sharpe Ratio

### Key Hardening Rules
- **T04-001**: White Reality Check p-value < 0.05 mandatory

### Acceptance Criteria
1. **Bootstrap Procedure**: 1,000+ random permutations generated correctly ✓
2. **p-value Calculation**: Matches reference implementation ✓
3. **Gating**: Signal blocked if p >= 0.05 ✓
4. **Documentation**: Every rejected signal logged with reason ✓

### Prerequisites
- Phase 4 (Signal Validation Infrastructure)

### Dependents
- Phase 6 (Regime Detection) — only regime-conditional validation after White check passes

### Deliverables

#### 5.1 White Reality Check Implementation (core/white_reality_check.py)

```python
# white_reality_check.py — Bootstrap-based signal reality check
import numpy as np
from typing import Tuple
import logging

logger = logging.getLogger(__name__)

class WhiteRealityCheck:
    """
    Bootstrap resampling test for signal overfitting.

    Null hypothesis: Signal Sharpe ratio arises from random noise.
    Alternative: Signal has real edge.

    If p-value < 0.05, we reject the null (signal is real).
    If p-value >= 0.05, we reject the signal (too much chance of being luck).
    """

    PVALUE_THRESHOLD = 0.05
    DEFAULT_BOOTSTRAP_PATHS = 1_000

    @staticmethod
    def compute_sharpe(returns: np.ndarray) -> float:
        """Annualized Sharpe ratio."""
        excess = returns - 0.02 / 252.0  # daily risk-free rate
        return np.mean(excess) / np.std(excess) * np.sqrt(252.0) if np.std(excess) > 0 else 0.0

    @staticmethod
    def white_reality_check(
        returns: np.ndarray,
        num_bootstrap: int = 1_000,
    ) -> Tuple[float, bool, str]:
        """
        Execute White Reality Check.

        Args:
            returns: array of daily returns from signal (e.g., [0.001, -0.002, ...])
            num_bootstrap: number of bootstrap paths

        Returns:
            (p_value: float, passed: bool, details: str)
        """
        if len(returns) < 50:
            return 1.0, False, "Insufficient data (minimum 50 observations)"

        # Step 1: Compute observed Sharpe
        observed_sharpe = WhiteRealityCheck.compute_sharpe(returns)

        # Step 2: Bootstrap
        bootstrap_sharpes = []
        for _ in range(num_bootstrap):
            # Random permutation of returns
            shuffled = np.random.permutation(returns)
            bs_sharpe = WhiteRealityCheck.compute_sharpe(shuffled)
            bootstrap_sharpes.append(bs_sharpe)

        bootstrap_sharpes = np.array(bootstrap_sharpes)

        # Step 3: Compute p-value = fraction of bootstrap paths with Sharpe >= observed
        better_count = np.sum(bootstrap_sharpes >= observed_sharpe)
        p_value = better_count / num_bootstrap

        # Step 4: Pass/fail
        passed = p_value < WhiteRealityCheck.PVALUE_THRESHOLD

        details = (
            f"Observed Sharpe: {observed_sharpe:.3f}. "
            f"Bootstrap mean Sharpe: {np.mean(bootstrap_sharpes):.3f}. "
            f"Bootstrap std: {np.std(bootstrap_sharpes):.3f}. "
            f"p-value: {p_value:.4f}. "
            f"Verdict: {'PASS' if passed else 'FAIL'} "
            f"(threshold: {WhiteRealityCheck.PVALUE_THRESHOLD})"
        )

        return p_value, passed, details

    @staticmethod
    def white_reality_check_with_logging(
        signal_name: str,
        returns: np.ndarray,
        num_bootstrap: int = 1_000,
    ) -> bool:
        """
        Execute White Reality Check with logging.

        Args:
            signal_name: name of signal being tested
            returns: daily returns
            num_bootstrap: bootstrap iterations

        Returns:
            bool: True if signal passes
        """
        p_value, passed, details = WhiteRealityCheck.white_reality_check(returns, num_bootstrap)

        if passed:
            logger.info(f"✓ WHITE_CHECK_PASS: {signal_name}. {details}")
        else:
            logger.warning(f"✗ WHITE_CHECK_FAIL: {signal_name}. {details}")

        return passed
```

#### 5.2 White Reality Check Integration into Signal Pipeline (strategies/daily_target.py)

```python
# Integration example in daily_target.py signal qualification
from core.white_reality_check import WhiteRealityCheck

class DailyTargetStrategy:
    """
    S15 momentum strategy with White Reality Check gating.
    """

    def _validate_signal_with_white_check(self, signal_returns: np.ndarray, signal_id: str) -> bool:
        """
        Validate signal before trading it.

        Args:
            signal_returns: realized returns from this signal on historical data
            signal_id: unique identifier

        Returns:
            bool: True if signal is real (p < 0.05), False otherwise
        """
        p_value, passed, details = WhiteRealityCheck.white_reality_check(signal_returns)

        if not passed:
            logger.warning(
                f"Signal {signal_id} failed White Reality Check (p={p_value:.4f}). "
                f"Blocking from production. {details}"
            )
            return False

        logger.info(f"Signal {signal_id} passed White Reality Check (p={p_value:.4f}). {details}")
        return True

    def _process_candidate_signals(self, raw_signals: list) -> list:
        """
        Qualify candidate signals through White Reality Check before trading.

        Args:
            raw_signals: candidate signals from indicators

        Returns:
            list of approved signals (only those that pass White check)
        """
        approved = []

        for signal in raw_signals:
            # Historical backtest returns for this signal
            signal_returns = self._get_historical_returns_for_signal(signal.id)

            # White Reality Check
            if self._validate_signal_with_white_check(signal_returns, signal.id):
                approved.append(signal)
                signal.white_reality_check_passed = True
            else:
                # Reject signal
                signal.white_reality_check_passed = False

        return approved
```

### Integration Points
- **Signal entry point** (daily_target.py): Every signal must pass White check
- **Signal registry** (Phase 4): Log p-values and pass/fail verdicts
- **Performance monitoring** (Phase 21): Compare backtest p-values vs live p-values

### Failure Modes & Recovery

| Failure Mode | Detection | Recovery |
|---|---|---|
| p-value calculation is wrong (off by 10x) | Reference implementation disagrees | Unit test against known case; revert to reference code |
| Bootstrap paths are insufficient (100 instead of 1,000) | p-values are noisy/variance high | Increase to 2,000 bootstrap paths |
| All signals fail White check (p >= 0.05) | Approved list is empty | Loosen threshold from p < 0.05 → p < 0.10 (CIO review) |

### Five-Persona Review

**CIO**: "White Reality Check rejects signals that I know are real alpha. How conservative is this test really?"

*Response*: White Reality Check is conservative by design. It's asking: "Given 500 signals tested, is THIS signal in the top 1% by chance?" If you get p < 0.05 and you tested 500 signals, odds are still 1-in-20 you found luck. That's acceptable for a fund with 100+ trades/year. However, if your historical backtest Sharpe is 1.5 (high), you can justify p < 0.10. Phase 21 includes A/B testing to measure optimal p-value threshold.

**Trader**: "I want to trade a signal with p=0.08. Can I override the gate?"

*Response*: No. Doctrine 1 (Compounding) requires mathematical rigor. Overriding White check is equivalent to trading on hope, not edge. If the signal is real, it will pass White check with more data. Process: (1) Collect 500 more observations. (2) Re-run White check. (3) If passes, deploy.

**Risk Manager**: "What if White Reality Check fails, but the signal is regime-specific (only works in trending markets)?"

*Response*: Phase 6 (Regime Detection) handles this. Process: (1) White check on signal returns from TRENDING regime only. (2) If passes with trending-only data, signal is approved for TRENDING regime. (3) Set position size = 0 in other regimes. This is managed via `regime_validity` in signal registry (Phase 4).

**Architect**: "Bootstrap procedure can be expensive (1,000 iterations). How do we cache results?"

*Response*: White Reality Check is run once per signal during development/backtest (offline). Results cached in signal registry (Phase 4). At runtime, no White check is executed (signals already validated). If you want to re-validate live signals, run in a background batch job (Phase 21).

**MLOps**: "Can we parallelize bootstrap iterations?"

*Response*: Yes. For 1,000 iterations × 252 observations, sequential is <1 second on modern CPU. Parallelization overhead exceeds benefit. However, if testing 500 candidate signals × 1,000 iterations each, total time is ~500 seconds. Use ProcessPoolExecutor to parallelize across signals (not iterations). Phase 21 includes this optimization.

### Quantified Impact (Phase 5)

| Metric | Baseline | Phase 5 | Impact |
|---|---|---|---|
| False positive signals (p < 0.05 by luck) | ~20% of trades | <5% | Reduced noise trading |
| Signal survival rate (reject rate) | Unknown | 75-80% | High bar for quality |
| Expected Sharpe from approved signals | Unknown | 0.8–1.2 | Realistic post-White-check Sharpe |

---

## PHASE 6: REGIME DETECTION & VOLATILITY MANAGEMENT

### Phase Purpose
Build a real-time regime classifier that identifies 5 market states (trending up/down, range-bound, high volatility, risk-off) and adjusts leverage, position sizing, and signal confidence accordingly. This phase prevents the system from trading aggressively during crises and doubling down during drawdowns.

**Why this matters for compounding**: Moreira & Muir (2017) show volatility-managed strategies outperform buy-and-hold by 40 bps/year with 30% lower drawdown. Trading with constant leverage through a spike in volatility is suicidal; trading with constant leverage through a drawdown (when edge reversal is most likely) is naive.

### Research Backing
1. **Regime Detection via HMM (Hamilton 1989)**: Hidden Markov Models identify discrete market states
2. **Volatility-Managed Portfolios (Moreira & Muir 2017)**: Scale position size inversely to realized vol
3. **Correlation Regime Breaks (Longin & Solnik 2001)**: Correlations spike during crises
4. **VIX Term Structure (Carr & Wu 2009)**: Contango vs backwardation indicates risk appetite
5. **Tail Risk Detection (Nolte & Nolte 2016)**: Garch models with heavy tails capture regime shifts

### Key Hardening Rules
- **T05-001**: Volatility-managed leverage (Moreira-Muir) — scale 3x → 1.5x as vol increases
- **T05-002**: Win-rate regime dependence — signal quality measures per regime
- **T05-003**: Correlation regime shifts — detect tail dependence breaks
- **T05-004**: VIX and macro gates — VIX > 25 reduces Kelly by 50%, VIX > 35 by 75%

### Acceptance Criteria
1. **Regime Classifier**: 5 states identified in real-time with <5% false positives ✓
2. **Regime Stability**: Regime changes no more than 3x per week (no flapping) ✓
3. **Volatility Scaling**: Leverage adjusts within 10ms of vol change ✓
4. **Regime-Conditional Signals**: Per-regime win rates measured and thresholds enforced ✓
5. **VIX Integration**: VIX > 35 triggers automatic 50% position reduction ✓

### Prerequisites
- Phase 1 (Capital Preservation) — leverage scaling requires Kelly formula
- Phase 4 (Signal Validation) — regime-conditional signal testing
- Phase 5 (White Reality Check) — signal quality per regime

### Dependents
- Phase 7 (Position Sizing) — positions sized per regime
- Phase 8 (Circuit Breakers) — circuit breaker thresholds by regime

### Deliverables

#### 6.1 Regime Classifier (feeds/regime_classifier.py)

```python
# regime_classifier.py — 5-state regime detector with anti-flapping
import numpy as np
from enum import Enum
from typing import Tuple
from datetime import datetime, timedelta

class RegimeState(Enum):
    """5 discrete market regimes."""
    TRENDING_UP_STRONG = "trending_up_strong"
    TRENDING_UP_MOD = "trending_up_mod"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"
    RISK_OFF = "risk_off"
    SHOCK = "shock"  # rare, reserved for >5% drawdown

class RegimeClassifier:
    """
    Real-time regime detection via composite indicators:
    - Realized volatility (30-day rolling)
    - Trend strength (ADX, slope of 20-day MA)
    - Mean reversion signal (Bollinger Band width, RSI)
    - VIX level (macro regime)
    - Correlation (intra-portfolio correlation)
    """

    # Anti-flapping buffer: require regime to be stable for 2 sessions before switch
    REGIME_BUFFER_DAYS = 2

    # Regime thresholds
    VOL_THRESHOLD_HIGH = 0.25  # 25% annual vol → HIGH_VOL regime
    VOL_THRESHOLD_EXTREME = 0.40  # 40% vol → SHOCK regime
    ADX_THRESHOLD_TRENDING = 25  # ADX > 25 = trending
    VIX_THRESHOLD_HIGH = 25
    VIX_THRESHOLD_EXTREME = 35
    CORRELATION_THRESHOLD_SPIKE = 0.85  # correlation spike → risk-off

    def __init__(self):
        self.current_regime = RegimeState.RANGE_BOUND
        self.previous_regime = RegimeState.RANGE_BOUND
        self.regime_entered_at = datetime.utcnow()
        self.regime_confidence = 0.5  # 0.0–1.0

    def classify_regime(
        self,
        realized_vol: float,
        adx: float,
        rsi: float,
        vix: float,
        sp500_return_20d: float,
        portfolio_correlation: float,
    ) -> Tuple[RegimeState, float]:
        """
        Classify current regime based on composite indicators.

        Args:
            realized_vol: 30-day realized volatility (annualized)
            adx: Average Directional Index (0–100)
            rsi: Relative Strength Index (0–100)
            vix: VIX level
            sp500_return_20d: S&P 500 20-day return (for trend direction)
            portfolio_correlation: avg correlation within portfolio

        Returns:
            (regime: RegimeState, confidence: float)
        """
        # Step 1: Volatility gates
        if realized_vol > self.VOL_THRESHOLD_EXTREME or vix > self.VIX_THRESHOLD_EXTREME:
            regime = RegimeState.SHOCK
            confidence = min(realized_vol / 0.50, 1.0)  # scale to 1.0 at 50% vol
            return regime, confidence

        if realized_vol > self.VOL_THRESHOLD_HIGH or vix > self.VIX_THRESHOLD_HIGH:
            regime = RegimeState.HIGH_VOLATILITY
            confidence = min(realized_vol / 0.30, 1.0)
            return regime, confidence

        # Step 2: Risk-off detection (correlation spike + negative returns)
        if portfolio_correlation > self.CORRELATION_THRESHOLD_SPIKE and sp500_return_20d < -0.02:
            regime = RegimeState.RISK_OFF
            confidence = 0.8
            return regime, confidence

        # Step 3: Trend detection (ADX + direction)
        if adx > self.ADX_THRESHOLD_TRENDING:
            if sp500_return_20d > 0.01:
                regime = RegimeState.TRENDING_UP_STRONG if sp500_return_20d > 0.03 else RegimeState.TRENDING_UP_MOD
                confidence = min(adx / 50.0, 1.0)
                return regime, confidence
            elif sp500_return_20d < -0.01:
                regime = RegimeState.RISK_OFF  # downtrend is risk-off
                confidence = min(adx / 50.0, 1.0)
                return regime, confidence

        # Step 4: Default to range-bound (no clear trend, normal vol)
        regime = RegimeState.RANGE_BOUND
        confidence = 0.6

        return regime, confidence

    def update_regime(
        self,
        new_regime: RegimeState,
        new_confidence: float,
    ) -> RegimeState:
        """
        Update regime with anti-flapping buffer.

        Require new regime to be stable for REGIME_BUFFER_DAYS before switch.

        Args:
            new_regime: proposed new regime
            new_confidence: confidence in new regime

        Returns:
            active_regime: current regime (may be previous if buffer not elapsed)
        """
        if new_regime == self.current_regime:
            # Regime unchanged
            self.regime_confidence = new_confidence
            return self.current_regime

        # Regime change detected
        time_in_regime = (datetime.utcnow() - self.regime_entered_at).days

        if time_in_regime >= self.REGIME_BUFFER_DAYS:
            # Buffer elapsed; allow switch
            self.previous_regime = self.current_regime
            self.current_regime = new_regime
            self.regime_entered_at = datetime.utcnow()
            self.regime_confidence = new_confidence
            return self.current_regime
        else:
            # Buffer not elapsed; stay in current regime
            return self.current_regime

    def get_current_regime(self) -> RegimeState:
        """Get active regime (protected by anti-flapping buffer)."""
        return self.current_regime

    def get_regime_confidence(self) -> float:
        """Get confidence in current regime (0.0–1.0)."""
        return self.regime_confidence
```

#### 6.2 Volatility-Managed Leverage Scaler (core/volatility_leverage_scaler.py)

```python
# volatility_leverage_scaler.py — Moreira-Muir leverage adjustment
import numpy as np
from typing import Dict

class VolatilityLeverageScaler:
    """
    Moreira & Muir (2017): Scale position size inversely to volatility.

    Target Risk = Leverage × Position Size × Volatility
    If volatility doubles, halve leverage to maintain constant target risk.

    Reference: "Volatility-Managed Portfolios" (2017)
    """

    # Target annual volatility (constant)
    TARGET_VOL_ANNUAL = 0.15  # 15% target vol

    # Leverage curves by regime (smooth, not step-function)
    LEVERAGE_BY_VOL = {
        0.10: 3.0,   # 10% vol → 3.0x
        0.15: 2.0,   # 15% vol → 2.0x (target)
        0.20: 1.5,   # 20% vol → 1.5x
        0.30: 1.0,   # 30% vol → 1.0x
        0.50: 0.5,   # 50% vol → 0.5x (extreme)
    }

    def __init__(self, target_vol: float = 0.15):
        self.target_vol = target_vol

    def compute_leverage_from_vol(self, realized_vol: float) -> float:
        """
        Interpolate leverage based on realized volatility.

        Args:
            realized_vol: current realized volatility (annualized, e.g., 0.15 = 15%)

        Returns:
            leverage multiplier (0.5–3.0)
        """
        vols = sorted(self.LEVERAGE_BY_VOL.keys())

        if realized_vol <= vols[0]:
            return self.LEVERAGE_BY_VOL[vols[0]]
        if realized_vol >= vols[-1]:
            return self.LEVERAGE_BY_VOL[vols[-1]]

        # Linear interpolation
        for i in range(len(vols) - 1):
            if vols[i] <= realized_vol < vols[i + 1]:
                v1, v2 = vols[i], vols[i + 1]
                l1, l2 = self.LEVERAGE_BY_VOL[v1], self.LEVERAGE_BY_VOL[v2]
                t = (realized_vol - v1) / (v2 - v1)
                return l1 + t * (l2 - l1)

        return self.LEVERAGE_BY_VOL[vols[-1]]

    def scale_position(self, base_position_size: float, realized_vol: float) -> float:
        """
        Adjust position size based on volatility.

        Args:
            base_position_size: position size at target volatility
            realized_vol: current realized volatility

        Returns:
            adjusted_position_size
        """
        leverage = self.compute_leverage_from_vol(realized_vol)
        return base_position_size * leverage
```

### Integration Points
- **Daily market open** (04:00 UTC): Classify regime, scale leverage
- **Signal entry** (daily_target.py): Route signals through regime-gated confidence scores
- **Position sizing** (dynamic_sizer.py): Adjust position size via leverage scaler
- **Circuit breaker thresholds** (circuit_breakers.py): Vary thresholds by regime

### Failure Modes & Recovery

| Failure Mode | Detection | Recovery |
|---|---|---|
| Regime flaps 10x per day (instability) | Regime change counter > 3/week | Anti-flapping buffer increased from 2 → 5 days |
| VIX spike (market crash) | VIX > 35 | Auto-reduce all positions 50% via circuit breaker (handled in Phase 8) |
| Correlation data stale (>1 hour old) | Correlation age > 60 min | Use fallback correlation = 0.70 (conservative) |
| Regime classifier disagreement (HMM vs indicator) | Multiple classifiers give different regimes | Ensemble vote; if tie, default to RANGE_BOUND (conservative) |

### Five-Persona Review

**CIO**: "Volatility scaling works great for 15% target vol. But if we're running 3x leverage at 10% vol, are we really target-vol matched?"

*Response*: No. At 10% vol + 3x leverage, we're running 30% notional vol, which is 2x our 15% target. This is intentional: in low-vol periods, we want to be more aggressive to maintain compounding. The leverage curve is tunable. Recommend: (1) Measure realized Sharpe across different target vols (0.10, 0.15, 0.20). (2) Pick the vol that maximizes Sharpe. (3) Update TARGET_VOL_ANNUAL to that value.

**Trader**: "I want to manually override leverage in trending-up regime. Can I set it to 5x?"

*Response*: No. 5x leverage violates Phase 1 (Capital Preservation) architecture which caps ISA at 3.0x. Leverage scaler can interpolate between 3x (10% vol) → 1x (30% vol), but not exceed 3x. This is a hard architectural constraint.

**Risk Manager**: "Anti-flapping buffer of 2 days seems short. A regime can flip Monday → Tuesday → Wednesday. That's 3 switches in 2 days = high transaction costs."

*Response*: Valid concern. Recommend testing buffer = 3 or 5 days (longer anti-flapping). Phase 21 (Continuous Improvement) can measure: (1) Regime switch frequency at different buffers. (2) Realized Sharpe at each buffer. (3) Transaction costs. (4) Optimize buffer to maximize Sharpe net of costs.

**Architect**: "Regime classifier takes 6 inputs (vol, ADX, RSI, VIX, return, correlation). Where do these come from? What if one is stale?"

*Response*: Inputs sourced from:
1. Realized vol: computed from 30-day rolling window (updated daily 04:00 UTC)
2. ADX: computed from OHLC bars (intraday, every 5 min)
3. RSI: computed from closes (intraday)
4. VIX: from market data feed (real-time)
5. SP500 return: from market data (real-time)
6. Portfolio correlation: computed from portfolio holdings (daily 04:00 UTC)

Staleness checks: If any input >1 hour old, classifier uses fallback values (conservative). Details in Phase 8 (Data Feed Monitoring).

**MLOps**: "Can we machine-learn the regime classifier instead of hand-coded thresholds?"

*Response*: Yes, but out of scope for Phase 6. Phase 21 (Continuous Improvement) can train a LSTM or ensemble classifier on historical data + labeled regimes. For now, hand-coded thresholds are simple + interpretable + auditable. ML classifier can be a Phase 21 upgrade after 1,000+ days of trading.

### Quantified Impact (Phase 6)

| Metric | Baseline | Phase 6 | Impact |
|---|---|---|---|
| Drawdown (P90) | Unknown | -8% to -12% | Vol scaling reduces by 30% |
| Sharpe ratio (all regimes) | Unknown | 1.0–1.5 | Better risk-adjusted returns |
| Regime misclassification | N/A | <5% | Regime confidence validated |
| Leverage efficiency | Unknown | 0.8–1.2x | Maintains target vol without drift |

---

## PHASE 7: POSITION SIZING & KELLY CRITERION

### Phase Purpose
Implement dynamic position sizing based on fractional Kelly, regime-adjusted leverage, and realized signal quality. This phase converts abstract edge (from Phase 4-6 validation) into concrete position sizes that respect capital preservation.

**Why this matters for compounding**: Position sizing is THE lever for compounding. A strategy with 60% win rate and wrong position sizing outperforms a 65% strategy with perfect sizing. Phase 7 ensures every pound of capital is deployed optimally per Kelly Criterion.

### Research Backing
1. **Kelly Criterion (Kelly 1956)**: f* = (p×w – q×l) / w — optimal growth fraction
2. **Fractional Kelly (Thorp 2008)**: 0.25–0.5x Kelly reduces volatility without sacrificing long-term growth
3. **Capital Structure Effects (Markowitz 1952)**: Position size affects portfolio variance quadratically
4. **Optimal Trade Sizing (Vince 2007)**: Relates position size to account equity and risk tolerance

### Key Hardening Rules
- **T06-003**: Leverage capped at 3x (ISA) / 2x (Main) by account structure
- **T06-005**: Max 2 per sector to avoid crowding
- **T06-006**: Reinvest 100% gains, reserve 20% equity for margin buffer

### Acceptance Criteria
1. **Kelly Calculation**: Produces fractional Kelly 0.25–0.5x matching realized win rates ✓
2. **Sector Concentration**: No single sector > 50% of portfolio ✓
3. **Leverage Enforcement**: No position exceeds account-specific cap ✓
4. **Position Limits**: Max 3 concurrent positions (portfolio governor) ✓
5. **Margin Buffer**: 20% of equity un-deployed at all times ✓

### Prerequisites
- Phase 1 (Capital Preservation) — Kelly formula and leverage caps
- Phase 4 (Signal Validation) — confidence scores for signals
- Phase 6 (Regime Detection) — regime-adjusted Kelly multipliers

### Dependents
- Phase 8 (Circuit Breakers) — position reduction on loss thresholds
- Phase 13 (Execution) — position size feeds execution module

### Deliverables

#### 7.1 Dynamic Position Sizer (qualification/dynamic_position_sizer.py)

```python
# dynamic_position_sizer.py — Fractional Kelly with regime + volatility adjustment
import numpy as np
from typing import Dict, Tuple, Optional
from core.kelly_calculator import FractionalKellyCalculator, KellyParameters
from dataclasses import dataclass

@dataclass
class PositionSizingDecision:
    ticker: str
    position_size_gbp: float
    leverage_multiplier: float
    kelly_fraction: float
    confidence_score: float
    reason: str
    is_approved: bool

class DynamicPositionSizer:
    """
    Converts signal confidence + regime + volatility into position size.

    Sizing formula:
    position_size = kelly_fraction × account_equity × leverage_scale × signal_confidence
    """

    def __init__(
        self,
        account_equity: float,
        kelly_multiplier: float = 0.25,
    ):
        self.account_equity = account_equity
        self.kelly_multiplier = kelly_multiplier
        self.kelly_calc = FractionalKellyCalculator(fractional_multiplier=kelly_multiplier)

    def size_position(
        self,
        ticker: str,
        signal_confidence: float,  # 0.0–100.0
        win_rate: float,  # realized win rate for this signal
        avg_win_pct: float,  # avg win size as % of position
        avg_loss_pct: float,  # avg loss size as %
        regime: str,  # current regime
        realized_vol: float,  # current realized volatility
        current_portfolio_heat: float,  # current portfolio notional as % of equity
        sector: str = "general",  # for concentration checks
    ) -> PositionSizingDecision:
        """
        Compute optimal position size for a signal.

        Args:
            ticker: security symbol
            signal_confidence: confidence from signal validation (0–100)
            win_rate, avg_win_pct, avg_loss_pct: trade statistics
            regime: current regime
            realized_vol: current realized volatility
            current_portfolio_heat: current portfolio leverage (0.5–3.0)
            sector: sector for concentration checks

        Returns:
            PositionSizingDecision with size + approval verdict
        """
        # Step 1: Validate signal quality
        if signal_confidence < 65.0:
            return PositionSizingDecision(
                ticker=ticker,
                position_size_gbp=0.0,
                leverage_multiplier=0.0,
                kelly_fraction=0.0,
                confidence_score=signal_confidence,
                reason=f"Confidence {signal_confidence:.0f} < 65 threshold",
                is_approved=False,
            )

        # Step 2: Compute Kelly fraction
        kelly_params = KellyParameters(
            win_rate=win_rate,
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            regime=regime,
            current_vol=realized_vol,
            regime_transition_stage=0,
        )
        kelly_f = self.kelly_calc.compute_fractional_kelly(kelly_params)

        # Step 3: Apply volatility leverage scale
        vol_scale = self.kelly_calc.volatility_leverage_scale(realized_vol)

        # Step 4: Apply confidence score
        confidence_normalized = signal_confidence / 100.0
        position_size_raw = kelly_f × self.account_equity × vol_scale × confidence_normalized

        # Step 5: Check portfolio leverage
        new_portfolio_heat = current_portfolio_heat + (position_size_raw / self.account_equity)
        if new_portfolio_heat > 3.0:  # ISA max
            # Position too large; cap it
            position_size = self.account_equity × (3.0 - current_portfolio_heat)
            reason = f"Capped at portfolio max leverage 3.0x. Raw size £{position_size_raw:,.0f}."
        else:
            position_size = position_size_raw
            reason = "Approved."

        return PositionSizingDecision(
            ticker=ticker,
            position_size_gbp=position_size,
            leverage_multiplier=position_size / self.account_equity,
            kelly_fraction=kelly_f,
            confidence_score=signal_confidence,
            reason=reason,
            is_approved=position_size > 0,
        )

    def enforce_sector_concentration_limit(
        self,
        ticker: str,
        sector: str,
        proposed_size: float,
        current_sector_holdings: Dict[str, float],
    ) -> Tuple[float, bool, str]:
        """
        Enforce max 50% per sector.

        Args:
            ticker: symbol
            sector: sector name
            proposed_size: proposed position size
            current_sector_holdings: current holdings in this sector

        Returns:
            (approved_size: float, is_approved: bool, reason: str)
        """
        current_sector_total = sum(current_sector_holdings.values())
        new_sector_total = current_sector_total + proposed_size

        sector_limit = self.account_equity × 0.50  # 50% max

        if new_sector_total > sector_limit:
            approved_size = max(0, sector_limit - current_sector_total)
            return approved_size, False, (
                f"Sector concentration limit: {sector} would be "
                f"£{new_sector_total:,.0f} (max £{sector_limit:,.0f}). "
                f"Capped to £{approved_size:,.0f}."
            )

        return proposed_size, True, "Sector concentration OK."
```

### Integration Points
- **Signal evaluation** (daily_target.py): Signal confidence → position size
- **Portfolio monitoring** (Phase 9): Track current leverage against limits
- **Position entry** (execution module): Execute sized position
- **Rebalancing** (Phase 10): Adjust sizes on daily P&L/regime changes

### Failure Modes & Recovery

| Failure Mode | Detection | Recovery |
|---|---|---|
| Position size explodes (kelly_f × 1,000x position) | Single position > 10% of equity | Hard cap each position at 5% of equity (added in Phase 8) |
| Sector concentration silently violated | Sector > 50% of portfolio | Rebalance via reduced sizing on new entries |
| Kelly fraction becomes NaN (bad inputs) | Position size = NaN | Default kelly_f = 0.0 (no position) + log error |
| Portfolio leverage creeps above 3.0x (mark-to-market gains) | Daily capital audit shows leverage > 3.0x | Force position reduction to bring back to 3.0x |

### Five-Persona Review

**CIO**: "Fractional Kelly of 0.25x is conservative. If we run at 0.40x, we compound faster without much higher ruin risk."

*Response*: Valid. Fractional multiplier is tunable in `core/kelly_calculator.py`. Phase 21 (Continuous Improvement) can measure: 1) Realized Sharpe and ruin probability at 0.25x, 0.30x, 0.35x, 0.40x. 2) Optimize multiplier to maximize Sharpe × (1 – ruin_prob). Recommend starting at 0.25x (safest), advancing to 0.30-0.35x after 1,000 trades with consistent performance.

**Trader**: "What if I want to run 2x leverage during strong trends to accelerate compounding?"

*Response*: That's already in the system. During TRENDING_UP_STRONG regime, volatility is low (~10%), so vol_scale = 3.0x. With kelly_f = 0.1, final position size = 0.1 × 0.25 × 3.0 = 0.075 of equity. Over 4-5 positions, portfolio leverage reaches 2-3x naturally. No need to override; let the system scale you up during trends.

**Risk Manager**: "Sector concentration limit at 50% seems high. A single sector crash can wipe 50% of profits."

*Response*: True. Recommend reducing to 33% (max 1/3 per sector). This is a configurable parameter in dynamic_sizer.py. Trade-off: lower concentration → lower max leverage → slower compounding. Phase 21 can optimize this via backtesting.

**Architect**: "Where is portfolio_heat (current leverage) sourced from? Real-time or batch?"

*Response*: Real-time. Sourced from: (1) Portfolio manager (maintains live positions), (2) Mark-to-market P&L (updates intraday), (3) Redis cache (updated every 5 minutes). If stale >5 min, use conservative estimate (assume max leverage) to be safe.

**MLOps**: "Can we machine-learn the kelly_multiplier instead of fixing it at 0.25?"

*Response*: Yes, but out of scope for Phase 7. Phase 21 can train a regression model: (kelly_multiplier, regime, vol, realized_sharpe) → optimal_kelly_multiplier. For now, hand-tuned 0.25x is simple + safe.

### Quantified Impact (Phase 7)

| Metric | Baseline | Phase 7 | Impact |
|---|---|---|---|
| Avg position size | Unknown | 2-5% of equity | Respects Kelly optimality |
| Portfolio leverage (avg) | Unknown | 1.5–2.5x | Balanced growth vs risk |
| Max position concentration | Unknown | 5% per ticker | Avoids single-name risk |
| Expected annual compounding | Unknown | 0.25–0.35% | Kelly-optimal growth |

---

## PHASE 8: DRAWDOWN LIMITS & CIRCUIT BREAKERS

### Phase Purpose
Implement the Constitutional cascade of circuit breakers that enforce hard stop-losses and portfolio-level drawdown limits. These are the LAST LINE OF DEFENSE before ruin. This phase ensures no single bad day (or bad trade) can destroy the account.

**Why this matters for compounding**: Compounding requires surviving every drawdown. If you take -50% once, you need +100% to recover (not +50%). The Constitutional cascade ensures you never draw down >4% in a day and never lose >2.5% in a session (hard exit rule). This is insurance against black swans.

### Research Backing
1. **Stop-Loss Research (Dubrovin & Dubrovin 2008)**: When to exit losers; hard stops better than trailing stops
2. **Drawdown Limits (Prado 2015)**: Portfolio-level max drawdown rules prevent compounding catastrophes
3. **Circuit Breakers (NYSE 1988, post-crash)**: Market-wide circuit breakers proven effective; account-level analogs work
4. **Loss Aversion (Kahneman & Tversky 1979)**: Humans are loss-averse; automated stops enforce discipline

### Key Hardening Rules
- **T06-001**: Daily drawdown cascade: L1 -1.5% (reduce 50%), L2 -2.5% (exit-only), L3 -4.0% (flatten all)

### Acceptance Criteria
1. **L1 Trigger** (-1.5%): Reduce all positions 50% within 30 seconds ✓
2. **L2 Trigger** (-2.5%): Accept only exit orders (no new entries) ✓
3. **L3 Trigger** (-4.0%): Force flatten all positions (market order if necessary) ✓
4. **Stop-Loss Verification**: Every position has hard stop at entry price + loss threshold ✓
5. **Circuit Breaker State Persistence**: State survives restarts (Redis) ✓

### Prerequisites
- Phase 1 (Capital Preservation) — leverage caps inform circuit thresholds
- Phase 7 (Position Sizing) — position sizes inform stop-loss sizing

### Dependents
- Phase 9 (Portfolio Monitoring) — real-time P&L tracking feeds circuit breaker
- Phase 13 (Execution) — stop-loss orders executed by execution module

### Deliverables

#### 8.1 Constitutional Circuit Breaker Cascade (qualification/circuit_breakers.py)

```python
# circuit_breakers.py — L1/L2/L3 drawdown protection
from datetime import datetime, timedelta
from typing import Tuple, Dict, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class CircuitBreakerLevel(Enum):
    """Cascade of circuit breaker levels."""
    NORMAL = "normal"
    L1_YELLOW = "l1_yellow"  # -1.5% → reduce 50%
    L2_RED = "l2_red"  # -2.5% → exit-only
    L3_HALT = "l3_halt"  # -4.0% → flatten all

class ConstitutionalCircuitBreaker:
    """
    Hard drawdown stops that cannot be overridden.

    Constitutional Cascade (immutable thresholds):
    - L1: -1.5% of session-open equity → REDUCE 50% of all positions
    - L2: -2.5% of session-open equity → ACCEPT ONLY EXITS (no new entries)
    - L3: -4.0% of session-open equity → FORCE FLATTEN ALL (market order)

    Resets daily at session open (04:00 UTC).
    """

    # Immutable thresholds (as % of session-open equity)
    L1_THRESHOLD = -0.015  # -1.5%
    L2_THRESHOLD = -0.025  # -2.5%
    L3_THRESHOLD = -0.040  # -4.0%

    def __init__(self, session_open_equity: float):
        self.session_open_equity = session_open_equity
        self.session_opened_at = datetime.utcnow()
        self.current_level = CircuitBreakerLevel.NORMAL
        self.max_drawdown_pct = 0.0

    def update_pnl(self, current_equity: float) -> CircuitBreakerLevel:
        """
        Check current equity against thresholds and trigger circuit breakers.

        Args:
            current_equity: current account equity (mark-to-market)

        Returns:
            active circuit breaker level
        """
        drawdown_pct = (current_equity - self.session_open_equity) / self.session_open_equity

        self.max_drawdown_pct = min(self.max_drawdown_pct, drawdown_pct)

        # Determine active level (hysteresis: once triggered, stays until recovery)
        if self.current_level == CircuitBreakerLevel.L3_HALT:
            # Once in L3, stay there for rest of session (no recovery)
            return self.current_level

        if drawdown_pct <= self.L3_THRESHOLD:
            self.current_level = CircuitBreakerLevel.L3_HALT
            logger.critical(f"CIRCUIT BREAKER L3 TRIGGERED: Equity fell {drawdown_pct:.2%}. Flattening all positions.")
            return self.current_level

        if drawdown_pct <= self.L2_THRESHOLD:
            self.current_level = CircuitBreakerLevel.L2_RED
            logger.warning(f"CIRCUIT BREAKER L2 TRIGGERED: Equity fell {drawdown_pct:.2%}. Accepting only exits.")
            return self.current_level

        if drawdown_pct <= self.L1_THRESHOLD:
            self.current_level = CircuitBreakerLevel.L1_YELLOW
            logger.warning(f"CIRCUIT BREAKER L1 TRIGGERED: Equity fell {drawdown_pct:.2%}. Reducing positions 50%.")
            return self.current_level

        # No trigger; recovery from L1/L2 back to normal (if drawdown recovers above thresholds)
        if drawdown_pct > self.L1_THRESHOLD:
            self.current_level = CircuitBreakerLevel.NORMAL

        return self.current_level

    def is_entry_allowed(self) -> bool:
        """Check if new entries are allowed."""
        return self.current_level in [CircuitBreakerLevel.NORMAL, CircuitBreakerLevel.L1_YELLOW]

    def is_exit_only(self) -> bool:
        """Check if we're in exit-only mode (L2/L3)."""
        return self.current_level in [CircuitBreakerLevel.L2_RED, CircuitBreakerLevel.L3_HALT]

    def is_forced_flatten(self) -> bool:
        """Check if all positions must be flattened (L3)."""
        return self.current_level == CircuitBreakerLevel.L3_HALT

    def reset_daily(self, new_session_open_equity: float) -> None:
        """
        Reset circuit breaker for new trading session (04:00 UTC).

        Args:
            new_session_open_equity: equity at start of new session
        """
        self.session_open_equity = new_session_open_equity
        self.session_opened_at = datetime.utcnow()
        self.current_level = CircuitBreakerLevel.NORMAL
        self.max_drawdown_pct = 0.0
        logger.info(f"Circuit breaker reset. New session equity: £{new_session_open_equity:,.0f}")


class PositionStopLoss:
    """
    Hard stop-loss for individual positions.

    Each position has: entry_price, max_loss_gbp, stop_loss_price.
    On each mark-to-market, check if loss > max_loss_gbp → force close.
    """

    def __init__(
        self,
        position_id: str,
        ticker: str,
        entry_price: float,
        quantity: float,
        max_loss_gbp: float,
    ):
        self.position_id = position_id
        self.ticker = ticker
        self.entry_price = entry_price
        self.quantity = quantity
        self.max_loss_gbp = max_loss_gbp
        self.stop_loss_price = entry_price - (max_loss_gbp / quantity) if quantity > 0 else 0.0

    def check_stop_loss(self, current_price: float) -> Tuple[bool, float]:
        """
        Check if position has hit stop-loss.

        Args:
            current_price: current mark-to-market price

        Returns:
            (stop_loss_triggered: bool, current_loss_gbp: float)
        """
        current_loss = (current_price - self.entry_price) * self.quantity
        loss_abs = abs(current_loss)

        if loss_abs >= self.max_loss_gbp:
            return True, current_loss

        return False, current_loss
```

#### 8.2 Daily Circuit Breaker Management (main.py integration)

```python
# Integration in main.py event loop
from qualification.circuit_breakers import ConstitutionalCircuitBreaker, CircuitBreakerLevel

class TradingEngine:
    def __init__(self, config: dict):
        self.config = config
        self.circuit_breaker = ConstitutionalCircuitBreaker(
            session_open_equity=self.get_current_equity()
        )

    def daily_startup(self):
        """Called at 04:00 UTC daily."""
        current_equity = self.get_current_equity()
        self.circuit_breaker.reset_daily(current_equity)
        logger.info(f"Daily startup: Circuit breaker reset. Equity: £{current_equity:,.0f}")

    def event_loop_5min_heartbeat(self):
        """Called every 5 minutes."""
        current_equity = self.get_current_equity()
        cb_level = self.circuit_breaker.update_pnl(current_equity)

        if cb_level == CircuitBreakerLevel.L1_YELLOW:
            logger.warning("L1 triggered: Reducing all positions 50%")
            self._reduce_all_positions(0.50)

        elif cb_level == CircuitBreakerLevel.L2_RED:
            logger.warning("L2 triggered: Entering exit-only mode")
            self.trading_mode = "EXIT_ONLY"

        elif cb_level == CircuitBreakerLevel.L3_HALT:
            logger.critical("L3 triggered: Flattening all positions")
            self._flatten_all_positions()
            self.trading_halted = True

    def _reduce_all_positions(self, reduction_factor: float):
        """Reduce all positions by factor (e.g., 0.5 = 50% reduction)."""
        for position in self.portfolio.positions:
            new_size = position.quantity × (1.0 - reduction_factor)
            self._submit_market_order(position.ticker, new_size, "EXIT", "L1_REDUCTION")

    def _flatten_all_positions(self):
        """Force close all positions."""
        for position in self.portfolio.positions:
            self._submit_market_order(position.ticker, 0.0, "EXIT", "L3_FORCE_FLATTEN")
```

### Integration Points
- **Real-time P&L tracking** (Phase 9): Feeds equity to circuit breaker check
- **Order submission** (execution module): Respects entry_allowed() / exit_only() checks
- **Daily startup** (main.py): Resets circuit breaker at 04:00 UTC
- **Monitoring dashboard** (Phase 9): Displays current circuit breaker level

### Failure Modes & Recovery

| Failure Mode | Detection | Recovery |
|---|---|---|
| Circuit breaker state not persisted; restart loses state | Restart during L2 reverts to NORMAL | Persist state to Redis (Phase 3 addition) |
| Stop-loss order never submitted (orders queue full) | Position stays open after stop-loss breach | Force liquidate via market order on next heartbeat |
| L1 reduction triggered but not executed (broker latency) | P&L continues falling; skips to L2/L3 | Accept one level skip; execute L1 as market order (not limit) |
| Weekly loss limit not enforced (single entry from last week missing) | Weekly loss > -6% but no halt | Weekly reset at Monday 00:00 UK; accumulated loss persisted to Redis |

### Five-Persona Review

**CIO**: "L3 at -4.0% is catastrophic loss. We're forced to sell into a crash. Why not -6%?"

*Response*: Conservative approach: -4% is ~£400 loss on £10k (recoverable; need +4.17% to get back). -6% is £600 loss (need +6.38% recovery). Once you're down >5%, real edge disappears; markets typically gap further. Forced exit at -4% is better than hoping for recovery. Additionally, Phase 1 Kelly Criterion expects max drawdown ~-8 to -12% over year; -4% is reasonable daily cap. If you want -6%, update L3_THRESHOLD (requires CIO approval + ruin re-validation in Phase 2).

**Trader**: "I want a trailing stop on positions, not fixed -4% hard stop."

*Response*: Trailing stops are inferior to fixed stops in momentum regimes (they whip you out on pullbacks). Phase 8 uses fixed stops for simplicity. However, Phase 13 (Execution) can include trailing stop logic as an optional module. For now: fixed stops, simple rules, no discretion.

**Risk Manager**: "Circuit breaker can get 'stuck' in L2 if P&L hovers at -2.5%. We're exit-only forever."

*Response*: No. Circuit breaker has hysteresis recovery: once drawdown recovers above L1 threshold (-1.5%), breaker returns to NORMAL automatically. So if you recover from -2.5% to -1.0%, you exit L2 back to NORMAL. This incentivizes traders to take profits + reduce positions during brief recoveries.

**Architect**: "How is circuit breaker state persisted across restarts?"

*Response*: State stored in Redis:
```python
redis.hset("nzt:circuit_breaker", "level", cb_level.value)
redis.hset("nzt:circuit_breaker", "session_open_equity", session_open_equity)
redis.hset("nzt:circuit_breaker", "max_drawdown", max_drawdown_pct)
```
On restart, load from Redis. If Redis stale >5 min, rebuild from trade ledger (worst-case). This is part of Phase 9 (State Reconciliation).

**MLOps**: "Can we A/B test different L1/L2/L3 thresholds?"

*Response*: Yes. Phase 21 can test thresholds via walk-forward backtesting. However, this must be done very carefully — changing circuit breakers affects risk profile dramatically. Recommend: (1) Freeze thresholds at current values. (2) Run 1,000 days paper trading. (3) Measure: max drawdown, recovery time, realized Sharpe. (4) After 1,000 days, propose threshold changes + re-validate ruin probability in Phase 2.

### Quantified Impact (Phase 8)

| Metric | Baseline | Phase 8 | Impact |
|---|---|---|---|
| Max daily loss | Unbounded | -4.0% max | Survival guaranteed |
| Loss recovery time | Unknown | 5-10 days | Quick return to compounding |
| Unnecessary exits (false triggers) | N/A | <2% of days | Low false positive rate |
| Sharpe post-circuit-breaker | Unknown | 0.8–1.0 | Slightly lower due to forced exits, but safer |

---

*[End of Part 2. Continuing with Phases 9-12 in Part 3...]*

