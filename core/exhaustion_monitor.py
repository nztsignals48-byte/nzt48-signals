"""
NZT-48 Micro-Regime Profit Exhaustion Monitor
===============================================
Once a trade enters profit, this module monitors tick-by-tick momentum
for exhaustion signals and dynamically adjusts the profit ladder.

Three interlocking mechanisms:

1. **Hawkes Process (Self-Exciting Point Process)**
   Models the arrival intensity of directional trades. When buying/selling
   impulses cluster, lambda(t) spikes; when they decay below a threshold,
   the momentum that propelled the move is exhausted.

   lambda(t) = mu + sum_i[ alpha * exp(-beta * (t - t_i)) ]

   - mu:    baseline intensity (ambient flow)
   - alpha: excitation magnitude per event (0 < alpha < beta for stationarity)
   - beta:  exponential decay rate of excitation

   Calibrated via MLE on rolling 30-minute windows per Bacry et al. (2015).
   Stationarity constraint: branching ratio n* = alpha/beta < 1.

   References:
     Hawkes, A.G. (1971). "Spectra of some self-exciting and mutually exciting
       point processes." Biometrika 58(1):83-90.
     Bacry, E., Mastromatteo, I. & Muzy, J.-F. (2015). "Hawkes processes in
       finance." Market Microstructure and Liquidity 1(1):1550005.
     Filimonov, V. & Sornette, D. (2012). "Quantifying reflexivity in financial
       markets." J. International Money and Finance 31(6):1459-1475.

2. **Volume-Time Decay**
   Measures cumulative volume flowing in the direction of the trade over
   a rolling window. When directional volume decays below a threshold
   fraction of its peak rate, the move has exhausted its fuel.

   VTD(t) = V_dir(t, t-w) / max_s{ V_dir(s, s-w) }

   Where V_dir is volume classified as buy (for longs) or sell (for shorts)
   using the Bulk Volume Classification of Easley, Lopez de Prado & O'Hara
   (2012). Threshold: VTD < 0.30 signals exhaustion.

   References:
     Easley, D., Lopez de Prado, M. & O'Hara, M. (2012). "Flow toxicity and
       liquidity in a high-frequency world." Review of Financial Studies 25(5).
     Kyle, A. (1985). "Continuous auctions and insider trading."
       Econometrica 53(6):1315-1335.

3. **Asymmetric Leverage Decay Offset**
   Leveraged ETPs suffer path-dependent variance drag that is
   directionally asymmetric:

   Long Lx daily: E[r_daily] = L*r_underlying - (L^2 - L)/2 * sigma^2
     For L=3: drag = -(9-3)/2 * sigma^2 = -3*sigma^2 per day
     For L=5: drag = -(25-5)/2 * sigma^2 = -10*sigma^2 per day

   Short Lx: same formula but HIGHER tracking error due to asymmetric
   compounding (short gamma exposure). Apply 1.15x penalty to Kelly sizing
   and tighter trail stops.

   References:
     Cheng, M. & Madhavan, A. (2009). "The dynamics of leveraged and inverse
       exchange-traded funds." J. Investment Management 7(4).
     Avellaneda, M. & Zhang, S. (2010). "Path-dependence of leveraged ETF
       returns." SIAM J. Financial Mathematics 1(1):586-603.
     Lu, L., Wang, J. & Zhang, G. (2012). "Long term performance of leveraged
       ETFs." Financial Services Review 21(3).

4. **Dynamic Profit Ladder**
   Replaces the fixed 6-rung ETP ladder with regime-adaptive rung spacing:

   rung_spacing = base_spacing * regime_vol_multiplier * hawkes_momentum_factor
                  * session_time_decay

   - HIGH_VOL / SHOCK regime: wider rungs (let winners run in vol expansion)
   - RANGE / LOW_VOL regime: tighter rungs (book gains quickly)
   - High Hawkes intensity: delay selling (momentum still strong)
   - Decaying Hawkes: accelerate selling (buying impulse fading)
   - Late session (after 15:00): tighter trail (approaching close)

   Exit via Limit-on-Close or passive maker orders, never market orders.

   References:
     Le Beau, C. (1999). "Chandelier Exits."
     Elder, A. (2014). "The New Trading for a Living." Wiley.
     Bianchi, R.J., Drew, M.E. & Fan, J.H. (2016). "Tail risk in momentum
       strategy returns." J. Empirical Finance 39(A):74-94.

Integration:
    This module is designed to be called from _run_etp_ladder() in
    execution/virtual_trader.py. It does NOT replace the existing ladder
    but AUGMENTS it with momentum-exhaustion intelligence.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger("nzt48.exhaustion_monitor")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hawkes calibration defaults (Bacry et al. 2015 empirical range)
_DEFAULT_MU = 0.05        # Baseline intensity (events/sec)
_DEFAULT_ALPHA = 0.6      # Excitation per event
_DEFAULT_BETA = 1.0       # Decay rate (alpha/beta < 1 for stationarity)
_HAWKES_WINDOW_SEC = 1800 # 30-minute calibration window
_HAWKES_EXHAUST_QUANTILE = 0.25  # Below 25th percentile of intensity = exhaustion

# Volume-time decay
_VTD_WINDOW_SEC = 300          # 5-minute rolling window for volume rate
_VTD_EXHAUSTION_THRESHOLD = 0.30  # < 30% of peak directional volume rate

# Leverage decay (Avellaneda & Zhang 2010)
_LEVERAGE_MAP = {
    "QQQ3.L": 3, "3LUS.L": 3, "3SEM.L": 3, "GPT3.L": 3,
    "NVD3.L": 3, "TSL3.L": 3, "TSM3.L": 3,
    "MU2.L": 2,
    "QQQS.L": -3, "3USS.L": -3,  # Inverse: negative leverage
    "QQQ5.L": 5, "SP5L.L": 5,
}
_SHORT_TRACKING_PENALTY = 1.15  # 15% penalty to Kelly for short-side ETPs

# Session time thresholds (LSE hours, UTC)
_LSE_OPEN_HOUR = 8       # 08:00 UTC
_LSE_CLOSE_HOUR = 16     # 16:30 UTC
_LATE_SESSION_HOUR = 15   # After 15:00 = late session, tighten trails

# Regime-to-volatility multiplier for rung spacing
_REGIME_VOL_MULT = {
    "SHOCK": 2.0,
    "RISK_OFF": 1.6,
    "HIGH_VOLATILITY": 1.4,
    "TRENDING_DOWN_STRONG": 1.3,
    "TRENDING_UP_STRONG": 1.2,
    "TRENDING_DOWN_MOD": 1.1,
    "TRENDING_UP_MOD": 1.0,
    "RANGE_BOUND": 0.8,
    "UNKNOWN": 1.0,
}

# Base rung spacing for ETP ladder (as pct-move thresholds)
_BASE_RUNGS_PCT = [0.01, 0.02, 0.04, 0.06, 0.08, 0.10]


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class HawkesState:
    """Persistent state for the Hawkes intensity process on a single trade.

    The event history stores timestamps of directional trade arrivals
    (buys for LONG positions, sells for SHORT positions). The intensity
    function lambda(t) is evaluated in O(n) over recent events.

    Attributes:
        events:         Deque of event timestamps (epoch seconds).
        mu:             Calibrated baseline intensity.
        alpha:          Calibrated excitation parameter.
        beta:           Calibrated decay parameter.
        branching_ratio: n* = alpha/beta. Must be < 1 for stationarity.
        peak_intensity: Highest lambda(t) observed since trade entry.
        last_intensity: Most recent lambda(t) evaluation.
        calibration_ts: Timestamp of last MLE calibration.
    """
    events: deque = field(default_factory=lambda: deque(maxlen=5000))
    mu: float = _DEFAULT_MU
    alpha: float = _DEFAULT_ALPHA
    beta: float = _DEFAULT_BETA
    branching_ratio: float = 0.6
    peak_intensity: float = 0.0
    last_intensity: float = 0.0
    calibration_ts: float = 0.0


@dataclass
class VolumeDecayState:
    """Tracks directional volume flow for exhaustion detection.

    Attributes:
        dir_volume_buckets:  Deque of (timestamp, directional_volume) pairs.
        peak_rate:           Peak directional volume rate observed (vol/sec).
        current_rate:        Current directional volume rate.
        vtd_ratio:           Current VTD ratio (current_rate / peak_rate).
    """
    dir_volume_buckets: deque = field(
        default_factory=lambda: deque(maxlen=10000)
    )
    peak_rate: float = 0.0
    current_rate: float = 0.0
    vtd_ratio: float = 1.0


@dataclass
class ExhaustionState:
    """Composite exhaustion state for one active trade.

    Combines Hawkes process, volume decay, and leverage offset
    into a single actionable exhaustion assessment.
    """
    trade_id: str = ""
    ticker: str = ""
    direction: str = "LONG"
    leverage: int = 3
    entry_price: float = 0.0
    entry_time: float = 0.0
    hawkes: HawkesState = field(default_factory=HawkesState)
    volume_decay: VolumeDecayState = field(default_factory=VolumeDecayState)
    # Dynamic ladder state
    active_rungs_pct: list = field(default_factory=list)
    current_rung: int = 0
    # Exhaustion signals
    hawkes_exhausted: bool = False
    volume_exhausted: bool = False
    composite_exhaustion: float = 0.0  # 0.0 (strong momentum) to 1.0 (exhausted)
    # Leverage decay
    daily_variance_drag: float = 0.0
    kelly_penalty: float = 1.0
    # Timing
    last_update_ts: float = 0.0


# ---------------------------------------------------------------------------
# Hawkes Process Engine
# ---------------------------------------------------------------------------

class HawkesIntensityEngine:
    """Self-exciting point process for trade arrival intensity.

    Implements the univariate Hawkes process (Hawkes 1971) with
    exponential kernel:

        lambda(t) = mu + sum_{t_i < t} alpha * exp(-beta * (t - t_i))

    The branching ratio n* = alpha/beta measures the fraction of events
    that are endogenous (self-excited) vs exogenous (external).

    For leveraged ETP momentum monitoring:
    - n* > 0.7: momentum is highly self-reinforcing (let it run)
    - n* ~ 0.5: balanced flow (standard ladder)
    - n* < 0.3: mostly noise, momentum exhausted (take profits)

    Calibration via conditional MLE (Ozaki 1979):
        L(theta) = sum_i[ log lambda(t_i) ] - integral_0^T lambda(t) dt

    For exponential kernel, the integral has closed form:
        integral = mu*T + (alpha/beta) * sum_i[ 1 - exp(-beta*(T-t_i)) ]

    References:
        Ozaki, T. (1979). "Maximum likelihood estimation of Hawkes'
          self-exciting point processes." Annals of the Institute of
          Statistical Mathematics 31(1):145-155.
        Filimonov & Sornette (2012). Branching ratio interpretation.
    """

    @staticmethod
    def intensity(
        t: float,
        events: deque,
        mu: float,
        alpha: float,
        beta: float,
    ) -> float:
        """Evaluate lambda(t) at time t.

        Computational note: We only consider events within 5/beta seconds
        of t, since exp(-5) < 0.007, making older events negligible.

        Args:
            t:      Evaluation time (epoch seconds).
            events: Deque of event timestamps.
            mu:     Baseline intensity.
            alpha:  Excitation parameter.
            beta:   Decay parameter.

        Returns:
            lambda(t): Current intensity (events/sec).
        """
        if not events:
            return mu

        cutoff = t - (5.0 / beta) if beta > 0 else t - 300.0
        excitation = 0.0
        for t_i in reversed(events):
            if t_i >= t:
                continue
            if t_i < cutoff:
                break
            excitation += math.exp(-beta * (t - t_i))

        return mu + alpha * excitation

    @staticmethod
    def calibrate_mle(
        events: deque,
        window_start: float,
        window_end: float,
        mu_init: float = _DEFAULT_MU,
        alpha_init: float = _DEFAULT_ALPHA,
        beta_init: float = _DEFAULT_BETA,
        max_iter: int = 50,
        lr: float = 0.01,
    ) -> tuple[float, float, float]:
        """Calibrate (mu, alpha, beta) via gradient ascent on log-likelihood.

        Uses the closed-form log-likelihood for the exponential Hawkes
        kernel (Ozaki 1979) and performs constrained gradient ascent with
        the stationarity constraint alpha < beta.

        The log-likelihood is:
            LL = sum_i log(lambda(t_i)) - mu*T
                 - (alpha/beta) * sum_i (1 - exp(-beta*(T - t_i)))

        Args:
            events:       Deque of all event timestamps.
            window_start: Start of calibration window (epoch).
            window_end:   End of calibration window (epoch).
            mu_init:      Initial baseline intensity.
            alpha_init:   Initial excitation.
            beta_init:    Initial decay.
            max_iter:     Maximum gradient ascent iterations.
            lr:           Learning rate.

        Returns:
            Tuple of (mu, alpha, beta) calibrated parameters.
        """
        # Filter events in window
        ts = np.array([
            t for t in events
            if window_start <= t <= window_end
        ])

        if len(ts) < 10:
            # Insufficient data: return defaults
            return mu_init, alpha_init, beta_init

        T = window_end - window_start
        n_events = len(ts)

        mu = max(0.001, mu_init)
        alpha = max(0.001, alpha_init)
        beta = max(0.01, beta_init)

        for _ in range(max_iter):
            # Compute lambda(t_i) for each event
            lambdas = np.zeros(n_events)
            for i in range(n_events):
                lam = mu
                for j in range(i):
                    dt = ts[i] - ts[j]
                    if dt > 5.0 / beta:
                        continue
                    lam += alpha * math.exp(-beta * dt)
                lambdas[i] = max(lam, 1e-10)

            # Integral term: (alpha/beta) * sum_i (1 - exp(-beta*(T - t_i)))
            # where T = window_end - window_start, t_i relative to window_start
            remaining = window_end - ts
            integral_sum = np.sum(1.0 - np.exp(-beta * remaining))

            # Gradients
            # d_LL/d_mu = sum(1/lambda_i) - T
            d_mu = np.sum(1.0 / lambdas) - T

            # d_LL/d_alpha = sum_i[ (1/lambda_i) * sum_{j<i} exp(-beta*(t_i-t_j)) ]
            #                - (1/beta) * integral_sum
            d_alpha_term = 0.0
            for i in range(n_events):
                inner = 0.0
                for j in range(i):
                    dt = ts[i] - ts[j]
                    if dt > 5.0 / beta:
                        continue
                    inner += math.exp(-beta * dt)
                d_alpha_term += inner / lambdas[i]
            d_alpha = d_alpha_term - (1.0 / beta) * integral_sum

            # d_LL/d_beta: complex, use finite difference for stability
            eps = 0.001
            beta_plus = beta + eps
            int_plus = np.sum(1.0 - np.exp(-beta_plus * remaining))
            ll_base = (np.sum(np.log(lambdas)) - mu * T
                       - (alpha / beta) * integral_sum)
            # Recompute lambdas with beta+eps (expensive but robust)
            lambdas_plus = np.zeros(n_events)
            for i in range(n_events):
                lam = mu
                for j in range(i):
                    dt = ts[i] - ts[j]
                    if dt > 5.0 / beta_plus:
                        continue
                    lam += alpha * math.exp(-beta_plus * dt)
                lambdas_plus[i] = max(lam, 1e-10)
            ll_plus = (np.sum(np.log(lambdas_plus)) - mu * T
                       - (alpha / beta_plus) * int_plus)
            d_beta = (ll_plus - ll_base) / eps

            # Update with constraints
            mu = max(0.001, mu + lr * d_mu)
            alpha = max(0.001, alpha + lr * d_alpha)
            beta = max(0.01, beta + lr * d_beta)

            # Enforce stationarity: alpha/beta < 0.95
            if alpha / beta >= 0.95:
                alpha = 0.94 * beta

        return mu, alpha, beta


# ---------------------------------------------------------------------------
# Leverage Decay Calculator
# ---------------------------------------------------------------------------

class LeverageDecayCalculator:
    """Computes asymmetric variance drag for leveraged ETPs.

    Leveraged ETPs reset daily, creating path-dependent returns.
    For a Lx leveraged ETP tracking an index with daily volatility sigma:

        E[ETP_daily] = L * r_index - (L^2 - L)/2 * sigma^2

    The drag term (L^2 - L)/2 * sigma^2 grows quadratically with leverage
    and linearly with variance. For L=3, sigma=1.5%:

        drag = (9-3)/2 * 0.015^2 = 3 * 0.000225 = 0.000675 = 6.75 bps/day

    For L=5, sigma=1.5%:

        drag = (25-5)/2 * 0.015^2 = 10 * 0.000225 = 0.00225 = 22.5 bps/day

    Short-side (inverse) ETPs suffer additional tracking error from
    asymmetric compounding (short gamma), warranting a 1.15x penalty
    on Kelly sizing and tighter trailing stops.

    References:
        Cheng & Madhavan (2009). Dynamics of leveraged ETFs.
        Avellaneda & Zhang (2010). Path-dependence of leveraged ETF returns.
        Lu, Wang & Zhang (2012). Long term performance of leveraged ETFs.
    """

    @staticmethod
    def daily_variance_drag(leverage: int, daily_sigma: float) -> float:
        """Compute daily variance drag in decimal (not bps).

        Formula: (L^2 - L) / 2 * sigma^2

        Args:
            leverage:     Absolute leverage factor (e.g. 3 for 3x).
            daily_sigma:  Daily volatility estimate (e.g. 0.015 for 1.5%).

        Returns:
            Daily drag as a decimal. E.g. 0.000675 for 3x @ 1.5% vol.
        """
        L = abs(leverage)
        return ((L ** 2 - L) / 2.0) * (daily_sigma ** 2)

    @staticmethod
    def annualised_drag_pct(leverage: int, daily_sigma: float) -> float:
        """Annualised variance drag as a percentage.

        Args:
            leverage:     Absolute leverage factor.
            daily_sigma:  Daily volatility estimate.

        Returns:
            Annual drag percentage. E.g. 17.01% for 3x @ 1.5%.
        """
        daily = LeverageDecayCalculator.daily_variance_drag(leverage, daily_sigma)
        return daily * 252 * 100

    @staticmethod
    def kelly_penalty_factor(ticker: str) -> float:
        """Get Kelly sizing penalty for short-side tracking error.

        Short (inverse) ETPs have higher tracking error than long-side
        due to asymmetric compounding. Apply 1.15x penalty to the Kelly
        denominator (effectively reducing position size by ~13%).

        Args:
            ticker: LSE ETP ticker.

        Returns:
            1.0 for long-side ETPs, 1.15 for inverse ETPs.
        """
        lev = _LEVERAGE_MAP.get(ticker, 1)
        if lev < 0:
            return _SHORT_TRACKING_PENALTY
        return 1.0

    @staticmethod
    def adjusted_trail_pct(
        base_trail_pct: float,
        ticker: str,
        daily_sigma: float,
        hold_hours: float = 0.0,
    ) -> float:
        """Adjust trailing stop percentage for leverage drag.

        The trail must be tighter for higher-leverage products because
        the variance drag erodes the cushion faster. For a position
        held intraday (< 1 day), the drag is pro-rated.

        Formula:
            adjusted_trail = base_trail - drag_per_hour * hours_held

        But never tighter than 50% of base trail (safety floor).

        Args:
            base_trail_pct:  Baseline trail percentage (e.g. 0.015 for 1.5%).
            ticker:          LSE ETP ticker.
            daily_sigma:     Daily volatility of the underlying.
            hold_hours:      Hours the position has been held.

        Returns:
            Adjusted trail percentage (decimal).
        """
        lev = abs(_LEVERAGE_MAP.get(ticker, 1))
        daily_drag = LeverageDecayCalculator.daily_variance_drag(lev, daily_sigma)
        hourly_drag = daily_drag / 8.0  # LSE = ~8 trading hours

        # Pro-rate drag for holding period
        drag_adjustment = hourly_drag * hold_hours

        # Apply short-side penalty
        if _LEVERAGE_MAP.get(ticker, 1) < 0:
            drag_adjustment *= _SHORT_TRACKING_PENALTY

        adjusted = base_trail_pct - drag_adjustment
        floor = base_trail_pct * 0.50  # Never tighter than half of base
        return max(adjusted, floor)


# ---------------------------------------------------------------------------
# Dynamic Profit Ladder
# ---------------------------------------------------------------------------

class DynamicLadder:
    """Regime-adaptive profit ladder with Hawkes momentum modulation.

    Replaces fixed rung spacing with dynamic thresholds that expand
    in volatile/trending regimes and contract in range-bound/late-session
    conditions.

    The adjustment formula for each rung threshold:

        adjusted_rung[i] = base_rung[i] * R_vol * H_mom * T_decay

    Where:
        R_vol   = regime volatility multiplier (0.8 to 2.0)
        H_mom   = Hawkes momentum factor:
                  - intensity > 75th pctile of session: 1.3 (delay sells)
                  - intensity in 25th-75th: 1.0 (standard)
                  - intensity < 25th: 0.7 (accelerate sells)
        T_decay = session time decay:
                  - before 15:00 UTC: 1.0
                  - 15:00-15:30: 0.9
                  - 15:30-16:00: 0.8
                  - after 16:00: 0.6 (approaching close)

    For exit execution, prefer Limit-on-Close (LOC) orders when within
    30 minutes of close, or passive maker-peg orders during session.
    Never use market orders on leveraged ETPs (spread cost is fatal).
    """

    @staticmethod
    def compute_rungs(
        regime_tag: str,
        hawkes_intensity: float,
        hawkes_peak: float,
        session_hour: float,
        leverage: int = 3,
        daily_sigma: float = 0.015,
    ) -> list[float]:
        """Compute dynamic rung thresholds for the current conditions.

        Args:
            regime_tag:       Current regime classification string.
            hawkes_intensity: Current Hawkes lambda(t).
            hawkes_peak:      Peak Hawkes lambda(t) observed in this trade.
            session_hour:     Current hour (UTC, float for fractional hours).
            leverage:         Absolute leverage factor of the ETP.
            daily_sigma:      Daily underlying volatility.

        Returns:
            List of 6 rung thresholds as decimal pct-move values.
            E.g. [0.008, 0.016, 0.032, 0.048, 0.064, 0.08] for tighter.
        """
        # --- Regime volatility multiplier ---
        r_vol = _REGIME_VOL_MULT.get(regime_tag, 1.0)

        # --- Hawkes momentum factor ---
        if hawkes_peak > 0:
            intensity_ratio = hawkes_intensity / hawkes_peak
        else:
            intensity_ratio = 0.5

        if intensity_ratio > 0.75:
            h_mom = 1.3   # Strong momentum: widen rungs (delay selling)
        elif intensity_ratio > 0.25:
            h_mom = 1.0   # Standard
        else:
            h_mom = 0.7   # Exhausting: tighten rungs (accelerate selling)

        # --- Session time decay ---
        if session_hour < _LATE_SESSION_HOUR:
            t_decay = 1.0
        elif session_hour < 15.5:
            t_decay = 0.9
        elif session_hour < 16.0:
            t_decay = 0.8
        else:
            t_decay = 0.6  # Final 30 min: aggressive profit-taking

        # --- Leverage adjustment ---
        # Higher leverage = tighter rungs (drag erodes cushion faster)
        lev_factor = 1.0
        if abs(leverage) == 5:
            lev_factor = 0.85  # 5x: 15% tighter rungs
        elif abs(leverage) == 2:
            lev_factor = 1.10  # 2x: 10% wider (less drag)

        # --- Variance drag offset ---
        # Subtract expected drag from rung spacing to avoid holding
        # through the drag zone
        drag_per_hour = LeverageDecayCalculator.daily_variance_drag(
            abs(leverage), daily_sigma
        ) / 8.0

        # Composite multiplier
        multiplier = r_vol * h_mom * t_decay * lev_factor

        rungs = []
        for i, base in enumerate(_BASE_RUNGS_PCT):
            adjusted = base * multiplier
            # Ensure minimum spacing: each rung > previous + 0.3%
            if rungs and adjusted <= rungs[-1] + 0.003:
                adjusted = rungs[-1] + 0.003
            # Ensure minimum rung of 0.5% for first rung
            if i == 0:
                adjusted = max(adjusted, 0.005)
            rungs.append(round(adjusted, 5))

        return rungs

    @staticmethod
    def recommend_exit_type(session_hour: float, pct_move: float) -> str:
        """Recommend exit order type based on session timing.

        Args:
            session_hour: Current hour (UTC float).
            pct_move:     Current unrealised move (decimal).

        Returns:
            One of: "LOC" (Limit on Close), "MAKER_PEG", "PASSIVE_LIMIT".
        """
        if session_hour >= 16.0:
            # Last 30 min: LOC for guaranteed fill at close
            return "LOC"
        elif session_hour >= 15.5:
            # Approaching close: passive but accept wider limit
            return "PASSIVE_LIMIT"
        else:
            # During session: maker-peg (best execution, no spread cost)
            return "MAKER_PEG"


# ---------------------------------------------------------------------------
# Main ExhaustionMonitor Class
# ---------------------------------------------------------------------------

class ExhaustionMonitor:
    """Micro-Regime Profit Exhaustion Monitor.

    Orchestrates the Hawkes process, volume-time decay, leverage decay
    offset, and dynamic profit ladder into a unified exhaustion assessment
    for each active trade.

    Lifecycle:
        1. register()   — Called when a trade enters profit (e.g. +0.5% for ETPs)
        2. on_tick()     — Called on every price update with volume data
        3. get_verdict() — Returns the current exhaustion assessment
        4. close()       — Called when the trade is exited

    Integration with virtual_trader.py:
        In _run_etp_ladder(), after computing pct_move:

            verdict = self.exhaustion_monitor.on_tick(
                trade_id=pos.id,
                price=price,
                volume=current_volume,
                bid=bid, ask=ask,
                bid_size=bid_size, ask_size=ask_size,
                regime_tag=current_regime,
            )
            if verdict.get("exit_now"):
                # Exhaustion exit: use LOC or MAKER_PEG
                ...
            elif verdict.get("adjusted_rungs"):
                # Use dynamic rungs instead of fixed ones
                ...

    Thread safety: Each trade has its own ExhaustionState. The monitor
    is designed for single-threaded use within the scan loop (no locks).

    Redis persistence: The monitor stores state in Redis for container
    restart survival, matching the pattern in chandelier_exit.py.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._states: dict[str, ExhaustionState] = {}  # trade_id -> state
        self._hawkes = HawkesIntensityEngine()
        self._leverage = LeverageDecayCalculator()
        self._ladder = DynamicLadder()
        self._hydrate_from_redis()

    # ------------------------------------------------------------------
    # Redis persistence
    # ------------------------------------------------------------------

    def _hydrate_from_redis(self) -> None:
        """Load exhaustion states from Redis on startup."""
        if not self._redis:
            return
        try:
            keys = self._redis.keys("exhaustion:*")
            for key in keys:
                raw = self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    state = ExhaustionState(
                        trade_id=data.get("trade_id", ""),
                        ticker=data.get("ticker", ""),
                        direction=data.get("direction", "LONG"),
                        leverage=data.get("leverage", 3),
                        entry_price=data.get("entry_price", 0.0),
                        entry_time=data.get("entry_time", 0.0),
                        current_rung=data.get("current_rung", 0),
                        hawkes_exhausted=data.get("hawkes_exhausted", False),
                        volume_exhausted=data.get("volume_exhausted", False),
                        composite_exhaustion=data.get("composite_exhaustion", 0.0),
                        daily_variance_drag=data.get("daily_variance_drag", 0.0),
                        kelly_penalty=data.get("kelly_penalty", 1.0),
                        last_update_ts=data.get("last_update_ts", 0.0),
                    )
                    # Restore Hawkes parameters
                    h = data.get("hawkes", {})
                    state.hawkes.mu = h.get("mu", _DEFAULT_MU)
                    state.hawkes.alpha = h.get("alpha", _DEFAULT_ALPHA)
                    state.hawkes.beta = h.get("beta", _DEFAULT_BETA)
                    state.hawkes.peak_intensity = h.get("peak_intensity", 0.0)
                    state.hawkes.branching_ratio = h.get("branching_ratio", 0.6)
                    # Restore active rungs
                    state.active_rungs_pct = data.get("active_rungs_pct", [])
                    self._states[state.trade_id] = state
            if self._states:
                logger.info(
                    "ExhaustionMonitor: hydrated %d states from Redis",
                    len(self._states),
                )
        except Exception as e:
            logger.warning(
                "ExhaustionMonitor: Redis hydration failed (in-memory mode): %s", e
            )

    def _persist_to_redis(self, state: ExhaustionState) -> None:
        """Persist minimal state to Redis with 24h TTL."""
        if not self._redis:
            return
        try:
            key = f"exhaustion:{state.ticker}:{state.trade_id}"
            payload = {
                "trade_id": state.trade_id,
                "ticker": state.ticker,
                "direction": state.direction,
                "leverage": state.leverage,
                "entry_price": state.entry_price,
                "entry_time": state.entry_time,
                "current_rung": state.current_rung,
                "hawkes_exhausted": state.hawkes_exhausted,
                "volume_exhausted": state.volume_exhausted,
                "composite_exhaustion": state.composite_exhaustion,
                "daily_variance_drag": state.daily_variance_drag,
                "kelly_penalty": state.kelly_penalty,
                "last_update_ts": state.last_update_ts,
                "active_rungs_pct": state.active_rungs_pct,
                "hawkes": {
                    "mu": state.hawkes.mu,
                    "alpha": state.hawkes.alpha,
                    "beta": state.hawkes.beta,
                    "peak_intensity": state.hawkes.peak_intensity,
                    "branching_ratio": state.hawkes.branching_ratio,
                },
            }
            self._redis.set(key, json.dumps(payload), ex=86400)
        except Exception as e:
            logger.debug("ExhaustionMonitor: Redis persist failed: %s", e)

    def _delete_from_redis(self, state: ExhaustionState) -> None:
        """Remove state from Redis on trade close."""
        if not self._redis:
            return
        try:
            key = f"exhaustion:{state.ticker}:{state.trade_id}"
            self._redis.delete(key)
        except Exception as e:
            logger.debug("ExhaustionMonitor: Redis delete failed: %s", e)

    # ------------------------------------------------------------------
    # Trade lifecycle
    # ------------------------------------------------------------------

    def register(
        self,
        trade_id: str,
        ticker: str,
        direction: str,
        entry_price: float,
        daily_sigma: float = 0.015,
    ) -> ExhaustionState:
        """Register a new trade for exhaustion monitoring.

        Call this when the trade first enters profit territory
        (e.g. +0.5% for ETPs, or at entry for aggressive monitoring).

        Args:
            trade_id:     Unique trade identifier.
            ticker:       LSE ETP ticker (e.g. "QQQ3.L").
            direction:    "LONG" or "SHORT".
            entry_price:  Entry fill price.
            daily_sigma:  Estimated daily volatility of underlying.

        Returns:
            The initialised ExhaustionState.
        """
        leverage = _LEVERAGE_MAP.get(ticker, 1)
        abs_lev = abs(leverage)
        now = time.time()

        state = ExhaustionState(
            trade_id=trade_id,
            ticker=ticker,
            direction=direction.upper(),
            leverage=abs_lev,
            entry_price=entry_price,
            entry_time=now,
            daily_variance_drag=self._leverage.daily_variance_drag(abs_lev, daily_sigma),
            kelly_penalty=self._leverage.kelly_penalty_factor(ticker),
            last_update_ts=now,
        )

        # Compute initial dynamic rungs (will be updated on each tick)
        state.active_rungs_pct = self._ladder.compute_rungs(
            regime_tag="RANGE_BOUND",  # Will be updated on first tick
            hawkes_intensity=0.0,
            hawkes_peak=0.0,
            session_hour=self._current_session_hour(),
            leverage=abs_lev,
            daily_sigma=daily_sigma,
        )

        self._states[trade_id] = state
        self._persist_to_redis(state)

        logger.info(
            "EXHAUSTION_REGISTER: %s %s entry=%.4f lev=%dx drag=%.4f bps/day "
            "kelly_penalty=%.2f rungs=%s",
            ticker, direction, entry_price, abs_lev,
            state.daily_variance_drag * 10000, state.kelly_penalty,
            [f"{r*100:.1f}%" for r in state.active_rungs_pct],
        )
        return state

    def on_tick(
        self,
        trade_id: str,
        price: float,
        volume: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
        bid_size: float = 0.0,
        ask_size: float = 0.0,
        regime_tag: str = "RANGE_BOUND",
        daily_sigma: float = 0.015,
    ) -> dict:
        """Process a new tick for an active trade.

        This is the main entry point, called on every price update
        (30-second scan cycle in virtual_trader.py).

        Updates:
            1. Hawkes event detection and intensity evaluation
            2. Volume-time decay measurement
            3. Dynamic ladder rung recomputation
            4. Composite exhaustion score

        Args:
            trade_id:     Trade identifier (must be registered).
            price:        Current price.
            volume:       Volume on this tick/bar.
            bid/ask:      Current best bid/ask (for flow classification).
            bid_size/ask_size: Depth at BBO.
            regime_tag:   Current regime from RegimeProvider.
            daily_sigma:  Current daily volatility estimate.

        Returns:
            Dict with keys:
                exit_now:             bool — True if exhaustion warrants immediate exit
                composite_exhaustion: float — 0.0 (strong) to 1.0 (exhausted)
                hawkes_exhausted:     bool
                volume_exhausted:     bool
                hawkes_intensity:     float — current lambda(t)
                hawkes_branching:     float — n* = alpha/beta
                vtd_ratio:            float — current volume-time decay ratio
                adjusted_rungs:       list[float] — dynamic rung thresholds
                recommended_exit:     str — "LOC", "MAKER_PEG", or "PASSIVE_LIMIT"
                leverage_drag_bps:    float — current variance drag in bps/day
                kelly_penalty:        float — Kelly sizing penalty factor
        """
        state = self._states.get(trade_id)
        if not state:
            return {
                "exit_now": False,
                "composite_exhaustion": 0.0,
                "hawkes_exhausted": False,
                "volume_exhausted": False,
                "hawkes_intensity": 0.0,
                "hawkes_branching": 0.0,
                "vtd_ratio": 1.0,
                "adjusted_rungs": list(_BASE_RUNGS_PCT),
                "recommended_exit": "MAKER_PEG",
                "leverage_drag_bps": 0.0,
                "kelly_penalty": 1.0,
            }

        now = time.time()

        # ----- 1. Hawkes: Detect directional trade events -----
        is_directional = self._classify_tick_direction(
            price=price,
            bid=bid, ask=ask,
            direction=state.direction,
        )
        if is_directional and volume > 0:
            state.hawkes.events.append(now)

        # Evaluate current intensity
        intensity = self._hawkes.intensity(
            t=now,
            events=state.hawkes.events,
            mu=state.hawkes.mu,
            alpha=state.hawkes.alpha,
            beta=state.hawkes.beta,
        )
        state.hawkes.last_intensity = intensity
        state.hawkes.peak_intensity = max(state.hawkes.peak_intensity, intensity)

        # Periodic recalibration (every 5 minutes)
        if now - state.hawkes.calibration_ts > 300.0:
            window_start = now - _HAWKES_WINDOW_SEC
            mu, alpha, beta = self._hawkes.calibrate_mle(
                events=state.hawkes.events,
                window_start=window_start,
                window_end=now,
                mu_init=state.hawkes.mu,
                alpha_init=state.hawkes.alpha,
                beta_init=state.hawkes.beta,
            )
            state.hawkes.mu = mu
            state.hawkes.alpha = alpha
            state.hawkes.beta = beta
            state.hawkes.branching_ratio = alpha / beta if beta > 0 else 0
            state.hawkes.calibration_ts = now
            logger.debug(
                "HAWKES_RECAL: %s mu=%.4f alpha=%.4f beta=%.4f n*=%.3f",
                state.ticker, mu, alpha, beta, state.hawkes.branching_ratio,
            )

        # Hawkes exhaustion: intensity below 25th percentile of peak
        exhaustion_threshold = state.hawkes.peak_intensity * _HAWKES_EXHAUST_QUANTILE
        state.hawkes_exhausted = (
            intensity < exhaustion_threshold
            and state.hawkes.peak_intensity > state.hawkes.mu * 2.0  # Only if we had real momentum
        )

        # ----- 2. Volume-Time Decay -----
        if volume > 0:
            dir_vol = self._classify_directional_volume(
                volume=volume,
                price=price,
                bid=bid, ask=ask,
                direction=state.direction,
            )
            state.volume_decay.dir_volume_buckets.append((now, dir_vol))

        # Compute current directional volume rate over window
        window_start = now - _VTD_WINDOW_SEC
        recent_buckets = [
            (ts, v) for ts, v in state.volume_decay.dir_volume_buckets
            if ts >= window_start
        ]

        if recent_buckets:
            window_duration = max(now - recent_buckets[0][0], 1.0)
            current_rate = sum(v for _, v in recent_buckets) / window_duration
            state.volume_decay.current_rate = current_rate
            state.volume_decay.peak_rate = max(
                state.volume_decay.peak_rate, current_rate
            )
            if state.volume_decay.peak_rate > 0:
                state.volume_decay.vtd_ratio = (
                    current_rate / state.volume_decay.peak_rate
                )
            else:
                state.volume_decay.vtd_ratio = 1.0
        else:
            state.volume_decay.vtd_ratio = 1.0

        state.volume_exhausted = (
            state.volume_decay.vtd_ratio < _VTD_EXHAUSTION_THRESHOLD
            and state.volume_decay.peak_rate > 0  # Only if we measured real volume
        )

        # ----- 3. Dynamic Ladder -----
        session_hour = self._current_session_hour()
        state.active_rungs_pct = self._ladder.compute_rungs(
            regime_tag=regime_tag,
            hawkes_intensity=intensity,
            hawkes_peak=state.hawkes.peak_intensity,
            session_hour=session_hour,
            leverage=state.leverage,
            daily_sigma=daily_sigma,
        )

        # ----- 4. Leverage Drag Update -----
        state.daily_variance_drag = self._leverage.daily_variance_drag(
            state.leverage, daily_sigma
        )

        # ----- 5. Composite Exhaustion Score -----
        # Weighted combination of Hawkes decay and volume decay
        # Hawkes weight: 0.6 (more predictive of momentum exhaustion)
        # Volume weight: 0.4 (confirms the flow has dried up)
        hawkes_score = 0.0
        if state.hawkes.peak_intensity > state.hawkes.mu * 1.5:
            hawkes_score = 1.0 - min(
                intensity / state.hawkes.peak_intensity, 1.0
            )

        volume_score = 1.0 - min(state.volume_decay.vtd_ratio, 1.0)

        state.composite_exhaustion = 0.6 * hawkes_score + 0.4 * volume_score
        state.composite_exhaustion = round(
            min(1.0, max(0.0, state.composite_exhaustion)), 4
        )

        # ----- 6. Exit Decision -----
        # Exit if BOTH signals confirm exhaustion (high-confidence conjunction)
        exit_now = (
            state.hawkes_exhausted
            and state.volume_exhausted
            and state.composite_exhaustion > 0.70
        )

        # Also exit if composite exhaustion is extreme (single-signal override)
        if state.composite_exhaustion > 0.90:
            exit_now = True

        # Late session escalation: lower threshold after 15:30
        if session_hour >= 15.5 and state.composite_exhaustion > 0.55:
            exit_now = True

        state.last_update_ts = now

        # Persist periodically (every 60s to avoid Redis spam)
        if now - state.entry_time > 0 and int(now) % 60 == 0:
            self._persist_to_redis(state)

        if exit_now:
            logger.info(
                "EXHAUSTION_EXIT: %s composite=%.3f hawkes_int=%.4f "
                "vtd=%.3f hawkes_exh=%s vol_exh=%s session=%.1f",
                state.ticker, state.composite_exhaustion,
                intensity, state.volume_decay.vtd_ratio,
                state.hawkes_exhausted, state.volume_exhausted,
                session_hour,
            )

        recommended_exit = self._ladder.recommend_exit_type(
            session_hour, 0.0  # pct_move not needed for order type
        )

        return {
            "exit_now": exit_now,
            "composite_exhaustion": state.composite_exhaustion,
            "hawkes_exhausted": state.hawkes_exhausted,
            "volume_exhausted": state.volume_exhausted,
            "hawkes_intensity": round(intensity, 6),
            "hawkes_branching": round(state.hawkes.branching_ratio, 4),
            "vtd_ratio": round(state.volume_decay.vtd_ratio, 4),
            "adjusted_rungs": state.active_rungs_pct,
            "recommended_exit": recommended_exit,
            "leverage_drag_bps": round(state.daily_variance_drag * 10000, 2),
            "kelly_penalty": state.kelly_penalty,
        }

    def get_verdict(self, trade_id: str) -> dict:
        """Get the current exhaustion verdict without processing a new tick.

        Args:
            trade_id: Trade identifier.

        Returns:
            Same dict structure as on_tick(), from cached state.
        """
        state = self._states.get(trade_id)
        if not state:
            return {
                "exit_now": False,
                "composite_exhaustion": 0.0,
                "hawkes_exhausted": False,
                "volume_exhausted": False,
                "hawkes_intensity": 0.0,
                "hawkes_branching": 0.0,
                "vtd_ratio": 1.0,
                "adjusted_rungs": list(_BASE_RUNGS_PCT),
                "recommended_exit": "MAKER_PEG",
                "leverage_drag_bps": 0.0,
                "kelly_penalty": 1.0,
            }

        exit_now = (
            (state.hawkes_exhausted and state.volume_exhausted
             and state.composite_exhaustion > 0.70)
            or state.composite_exhaustion > 0.90
        )
        session_hour = self._current_session_hour()
        if session_hour >= 15.5 and state.composite_exhaustion > 0.55:
            exit_now = True

        return {
            "exit_now": exit_now,
            "composite_exhaustion": state.composite_exhaustion,
            "hawkes_exhausted": state.hawkes_exhausted,
            "volume_exhausted": state.volume_exhausted,
            "hawkes_intensity": round(state.hawkes.last_intensity, 6),
            "hawkes_branching": round(state.hawkes.branching_ratio, 4),
            "vtd_ratio": round(state.volume_decay.vtd_ratio, 4),
            "adjusted_rungs": state.active_rungs_pct,
            "recommended_exit": self._ladder.recommend_exit_type(session_hour, 0.0),
            "leverage_drag_bps": round(state.daily_variance_drag * 10000, 2),
            "kelly_penalty": state.kelly_penalty,
        }

    def close(self, trade_id: str) -> Optional[dict]:
        """Remove a trade from exhaustion monitoring.

        Returns the final state summary for logging/TCA.

        Args:
            trade_id: Trade identifier.

        Returns:
            Final exhaustion summary dict, or None if not found.
        """
        state = self._states.pop(trade_id, None)
        if not state:
            return None

        self._delete_from_redis(state)

        summary = {
            "trade_id": state.trade_id,
            "ticker": state.ticker,
            "direction": state.direction,
            "final_composite_exhaustion": state.composite_exhaustion,
            "hawkes_final_intensity": state.hawkes.last_intensity,
            "hawkes_peak_intensity": state.hawkes.peak_intensity,
            "hawkes_branching_ratio": state.hawkes.branching_ratio,
            "hawkes_calibrated_mu": state.hawkes.mu,
            "hawkes_calibrated_alpha": state.hawkes.alpha,
            "hawkes_calibrated_beta": state.hawkes.beta,
            "vtd_final_ratio": state.volume_decay.vtd_ratio,
            "vtd_peak_rate": state.volume_decay.peak_rate,
            "daily_variance_drag_bps": state.daily_variance_drag * 10000,
            "kelly_penalty": state.kelly_penalty,
            "total_directional_events": len(state.hawkes.events),
            "monitoring_duration_sec": time.time() - state.entry_time,
        }

        logger.info(
            "EXHAUSTION_CLOSE: %s duration=%.0fs events=%d "
            "final_exhaust=%.3f peak_hawkes=%.4f branching=%.3f",
            state.ticker,
            summary["monitoring_duration_sec"],
            summary["total_directional_events"],
            state.composite_exhaustion,
            state.hawkes.peak_intensity,
            state.hawkes.branching_ratio,
        )
        return summary

    def get_status(self) -> dict:
        """Return summary of all active exhaustion monitors."""
        return {
            "active_count": len(self._states),
            "trades": {
                tid: {
                    "ticker": s.ticker,
                    "direction": s.direction,
                    "composite_exhaustion": s.composite_exhaustion,
                    "hawkes_intensity": round(s.hawkes.last_intensity, 4),
                    "hawkes_branching": round(s.hawkes.branching_ratio, 3),
                    "vtd_ratio": round(s.volume_decay.vtd_ratio, 3),
                    "hawkes_exhausted": s.hawkes_exhausted,
                    "volume_exhausted": s.volume_exhausted,
                    "leverage": s.leverage,
                    "drag_bps_day": round(s.daily_variance_drag * 10000, 1),
                    "active_rungs": [f"{r*100:.1f}%" for r in s.active_rungs_pct],
                }
                for tid, s in self._states.items()
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_tick_direction(
        price: float,
        bid: float,
        ask: float,
        direction: str,
    ) -> bool:
        """Classify whether a tick represents a directional event.

        Uses Lee-Ready (1991) tick test: trades closer to the ask are
        buyer-initiated, trades closer to the bid are seller-initiated.

        For LONG positions, we track buyer-initiated events.
        For SHORT positions, we track seller-initiated events.

        Args:
            price:     Trade price.
            bid:       Best bid.
            ask:       Best ask.
            direction: Position direction ("LONG" or "SHORT").

        Returns:
            True if this tick is directional (supporting the trade).
        """
        if bid <= 0 or ask <= 0 or ask <= bid:
            # No valid BBO: use midpoint heuristic
            return True  # Assume directional (conservative)

        mid = (bid + ask) / 2.0

        if direction == "LONG":
            # Buy-side: price >= mid
            return price >= mid
        else:
            # Sell-side: price <= mid
            return price <= mid

    @staticmethod
    def _classify_directional_volume(
        volume: float,
        price: float,
        bid: float,
        ask: float,
        direction: str,
    ) -> float:
        """Classify volume into directional flow using BVC.

        Bulk Volume Classification (Easley, Lopez de Prado & O'Hara 2012):
        Fraction classified as buy = Phi(z), where z = (price - mid) / sigma.

        Simplified here for tick-level data: linear interpolation between
        bid and ask to determine buy fraction.

        Args:
            volume:    Total volume on this tick/bar.
            price:     Trade price.
            bid:       Best bid.
            ask:       Best ask.
            direction: Position direction.

        Returns:
            Directional volume (positive = in direction of trade).
        """
        if bid <= 0 or ask <= 0 or ask <= bid:
            return volume * 0.5  # Unknown: split 50/50

        spread = ask - bid
        if spread == 0:
            buy_frac = 0.5
        else:
            # Linear interpolation: 0 at bid, 1 at ask
            buy_frac = max(0.0, min(1.0, (price - bid) / spread))

        if direction == "LONG":
            return volume * buy_frac
        else:
            return volume * (1.0 - buy_frac)

    @staticmethod
    def _current_session_hour() -> float:
        """Get the current LSE session hour as a float (UTC).

        Returns e.g. 15.5 for 15:30 UTC.
        """
        now = datetime.now(timezone.utc)
        return now.hour + now.minute / 60.0
