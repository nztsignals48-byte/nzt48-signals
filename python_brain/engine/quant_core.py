"""Quant core (Python mirror of rust_core/src/quant_core/*.rs).

- GARCH(1,1) rolling conditional vol
- GARCH-EVT 95% CVaR from residuals
- Student-t Kalman filter (price denoising + residual z-score)
- HMM regime probability vector [steady, trending, crisis, rotation]
- Hayashi-Yoshida async correlation (to SPY)

Every output is CONSUMED by the risk arbiter (see Phase 2B ledger update).
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Tuple


@dataclass
class Garch:
    omega: float = 1e-6
    alpha: float = 0.1
    beta: float = 0.85
    var: float = 1e-4
    last_return: float = 0.0
    residuals: Deque[float] = field(default_factory=lambda: deque(maxlen=500))

    def step(self, r: float) -> float:
        self.var = self.omega + self.alpha * (self.last_return ** 2) + self.beta * self.var
        self.last_return = r
        sigma = math.sqrt(max(self.var, 1e-12))
        self.residuals.append(r / sigma if sigma else 0.0)
        return sigma * math.sqrt(252 * 78)  # annualised (rough)


def cvar_95(residuals: List[float]) -> float:
    if len(residuals) < 30:
        return 0.0
    xs = sorted(residuals)
    tail_n = max(1, len(xs) // 20)
    return statistics.mean(xs[:tail_n])


@dataclass
class Kalman:
    state: float = 0.0
    p: float = 1.0
    q: float = 0.01
    r: float = 1.0
    residual: float = 0.0
    residual_z: float = 0.0
    _res_hist: Deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def step(self, z: float) -> None:
        self.p += self.q
        k = self.p / (self.p + self.r)
        self.residual = z - self.state
        self._res_hist.append(self.residual)
        if len(self._res_hist) > 20:
            sd = statistics.pstdev(self._res_hist) or 1.0
            self.residual_z = self.residual / sd
        self.state = self.state + k * self.residual
        self.p = (1 - k) * self.p


def regime_probs(returns: List[float], vol: float) -> List[float]:
    """Simple HMM proxy:
       - high vol -> crisis
       - strong directional trend -> trending
       - opposite-signed short-term reversal vs longer trend -> rotation
       - otherwise steady
    """
    if len(returns) < 10:
        return [1.0, 0.0, 0.0, 0.0]
    mean = statistics.mean(returns[-10:])
    sd_raw = statistics.pstdev(returns[-30:]) if len(returns) >= 30 else statistics.pstdev(returns)
    sd = sd_raw if sd_raw > 0 else 1e-6
    short = statistics.mean(returns[-3:])
    long_ = statistics.mean(returns[-20:]) if len(returns) >= 20 else mean
    vol_z = vol / 0.25
    steady, trending, crisis, rotation = 0.25, 0.25, 0.25, 0.25
    if vol_z > 2.5:
        crisis, steady, trending, rotation = 0.55, 0.20, 0.15, 0.10
    elif abs(mean) / sd > 1.5:
        trending, steady, crisis, rotation = 0.55, 0.25, 0.10, 0.10
    elif short * long_ < 0 and abs(short - long_) / sd > 1.0:
        rotation, steady, trending, crisis = 0.45, 0.30, 0.15, 0.10
    else:
        steady, trending, crisis, rotation = 0.70, 0.15, 0.05, 0.10
    total = steady + trending + crisis + rotation
    return [steady/total, trending/total, crisis/total, rotation/total]


def hayashi_yoshida(a: List[Tuple[int, float]], b: List[Tuple[int, float]]) -> float:
    """Async correlation. a,b: lists of (ts_ns, return)."""
    if len(a) < 5 or len(b) < 5:
        return 0.0
    prod = 0.0
    sa, sb = 0.0, 0.0
    for (ta, ra) in a:
        for (tb, rb) in b:
            if abs(ta - tb) < 5_000_000_000:   # 5s window
                prod += ra * rb
        sa += ra * ra
    for (_, rb) in b:
        sb += rb * rb
    if sa == 0 or sb == 0:
        return 0.0
    return prod / math.sqrt(sa * sb)


@dataclass
class QuantState:
    garch_vol_annualized: float = 0.2
    evt_cvar_95: float = 0.0
    kalman_residual: float = 0.0
    kalman_z: float = 0.0
    regime_probs: List[float] = field(default_factory=lambda: [1.0, 0.0, 0.0, 0.0])
    hy_correlation_to_spy: float = 0.0


@dataclass
class QuantCore:
    garch: Dict[str, Garch] = field(default_factory=dict)
    kalman: Dict[str, Kalman] = field(default_factory=dict)
    returns: Dict[str, Deque[float]] = field(default_factory=dict)
    last_price: Dict[str, float] = field(default_factory=dict)
    ts_returns: Dict[str, Deque[Tuple[int, float]]] = field(default_factory=dict)

    def on_tick(self, ticker: str, price: float, ts_ns: int) -> QuantState:
        prev = self.last_price.get(ticker, price)
        r = (price - prev) / prev if prev else 0.0
        self.last_price[ticker] = price
        g = self.garch.setdefault(ticker, Garch())
        vol = g.step(r)
        k = self.kalman.setdefault(ticker, Kalman())
        k.step(price)
        rets = self.returns.setdefault(ticker, deque(maxlen=100))
        rets.append(r)
        trets = self.ts_returns.setdefault(ticker, deque(maxlen=200))
        trets.append((ts_ns, r))
        probs = regime_probs(list(rets), vol)
        spy_trets = list(self.ts_returns.get("SPY", []))
        hy = hayashi_yoshida(list(trets), spy_trets) if ticker != "SPY" and spy_trets else 0.0
        return QuantState(
            garch_vol_annualized=vol,
            evt_cvar_95=cvar_95(list(g.residuals)),
            kalman_residual=k.residual,
            kalman_z=k.residual_z,
            regime_probs=probs,
            hy_correlation_to_spy=hy,
        )
