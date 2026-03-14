"""
Fractional Differentiation -- Lopez de Prado (2018) Chapter 5.
Preserves memory while achieving stationarity.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def frac_diff(series: pd.Series, d: float = 0.4, thres: float = 0.01) -> pd.Series:
    """
    Fractional differentiation preserving memory while achieving stationarity.
    d=0.4 preserves ~60% of correlation while passing ADF stationarity test.
    """
    w = [1.0]
    k = 1
    while abs(w[-1]) >= thres:
        w_ = -w[-1] * (d - k + 1) / k
        w.append(w_)
        k += 1
    w = np.array(w[::-1]).reshape(-1, 1)
    width = len(w)

    df = series.to_frame()
    res = {}
    for i in range(width - 1, len(df)):
        window = df.iloc[i - width + 1 : i + 1]
        res[df.index[i]] = np.dot(w.T, window.values)[0, 0]
    return pd.Series(res)
