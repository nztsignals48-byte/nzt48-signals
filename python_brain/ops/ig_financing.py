"""IG overnight financing model: LIBOR + 2.5% annualised on notional per night."""
from __future__ import annotations


def overnight_financing_gbp(notional_gbp: float, nights_held: int, libor_annual: float = 0.0475, spread: float = 0.025) -> float:
    rate = libor_annual + spread
    return notional_gbp * rate * (nights_held / 365.0)
