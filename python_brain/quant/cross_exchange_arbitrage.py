"""Cross-exchange arbitrage — same economic exposure priced inconsistently across venues.

Finds pairs: AAPL SMART vs AAPL LSE ADR; BHP ASX vs BHP LSE; SONY vs SONY-TSE-ADR;
major dual-listed names. When mid-price spread (adjusted for FX) exceeds threshold,
publishes cross_exchange.opportunity — feeds bandit as candidate strategy.

Beyond institutional: most retail systems don't cross-reference international
listings due to data costs. V5's 10-exchange coverage makes this tractable.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


# Dual-listed pairs: (symbol_in_primary, symbol_in_secondary, primary_exch, secondary_exch)
DUAL_LISTINGS = [
    # ADRs on US vs primary listing
    ("BHP", "BHP", "SMART", "ASX"),       # BHP NYSE vs Sydney
    ("RIO", "RIO", "SMART", "LSE"),       # Rio Tinto NYSE vs LSE
    ("AZN", "AZN", "SMART", "LSE"),       # AstraZeneca
    ("BTI", "BATS", "SMART", "LSE"),      # British American Tobacco ADR vs LSE
    ("SHEL", "SHEL", "SMART", "LSE"),     # Shell ADR vs LSE
    ("BP", "BP", "SMART", "LSE"),          # BP ADR vs LSE
    ("VOD", "VOD", "SMART", "LSE"),        # Vodafone ADR vs LSE
    ("HSBC", "HSBA", "SMART", "LSE"),      # HSBC ADR vs HSBA LSE
    ("BCS", "BARC", "SMART", "LSE"),       # Barclays ADR vs LSE
    ("UL", "ULVR", "SMART", "LSE"),        # Unilever ADR vs LSE
    ("NVS", "NOVN", "SMART", "EBS"),       # Novartis ADR vs Swiss (if subscribed)
    ("SAP", "SAP", "SMART", "IBIS"),       # SAP NYSE vs XETRA
    ("DB", "DBK", "SMART", "IBIS"),        # Deutsche Bank ADR vs XETRA
    ("BAYRY", "BAYN", "SMART", "IBIS"),    # Bayer ADR vs XETRA
    ("TM", "7203", "SMART", "TSEJ"),       # Toyota ADR vs TSE
    ("SNE", "6758", "SMART", "TSEJ"),      # Sony ADR vs TSE
    ("BABA", "9988", "SMART", "SEHK"),     # Alibaba NYSE vs HK
    ("JD", "9618", "SMART", "SEHK"),       # JD.com NYSE vs HK
    ("NIO", "9866", "SMART", "SEHK"),      # NIO NYSE vs HK
]


# FX rates (approximate static; runtime should refresh)
FX_TO_USD = {
    "USD": 1.0,
    "GBP": 1.28,  # GBP to USD
    "EUR": 1.09,
    "JPY": 0.0067,
    "HKD": 0.128,
    "AUD": 0.66,
    "SGD": 0.74,
    "CHF": 1.13,
}

# LSE prices are in GBp (pence) for many stocks — divide by 100 to get GBP
GBP_PENCE_SYMBOLS = {
    "VOD", "HSBA", "BARC", "ULVR", "BP", "SHEL", "AZN", "BATS", "GSK", "RIO",
    "LLOY", "NWG", "GLEN", "BT.A", "TSCO", "ITV", "JD",
}


@dataclass
class ArbOpportunity:
    symbol_a: str
    exch_a: str
    price_a_usd: float
    symbol_b: str
    exch_b: str
    price_b_usd: float
    spread_bps: float
    direction: str              # "long_a_short_b" or "long_b_short_a"
    notional_usd: float


def normalize_to_usd(price: float, currency: str, symbol: str, exchange: str) -> float:
    """Convert quoted price to USD."""
    # LSE stocks often quote in GBp (pence) — divide by 100
    adj_price = price
    if exchange == "LSE" and symbol in GBP_PENCE_SYMBOLS:
        adj_price = price / 100.0
    rate = FX_TO_USD.get(currency.upper(), 1.0)
    return adj_price * rate


def detect_opportunities(
    prices: dict[tuple[str, str], tuple[float, str]],
    min_spread_bps: float = 20.0,
    notional_cap_usd: float = 500.0,
) -> list[ArbOpportunity]:
    """prices: {(symbol, exchange): (price, currency)} → find arb pairs."""
    out = []
    for sym_a, sym_b, exch_a, exch_b in DUAL_LISTINGS:
        key_a = (sym_a, exch_a)
        key_b = (sym_b, exch_b)
        if key_a not in prices or key_b not in prices:
            continue
        price_a, ccy_a = prices[key_a]
        price_b, ccy_b = prices[key_b]
        p_a = normalize_to_usd(price_a, ccy_a, sym_a, exch_a)
        p_b = normalize_to_usd(price_b, ccy_b, sym_b, exch_b)
        if p_a <= 0 or p_b <= 0:
            continue
        mid = (p_a + p_b) / 2
        spread_bps = (p_a - p_b) / mid * 10000

        if abs(spread_bps) >= min_spread_bps:
            direction = "long_b_short_a" if spread_bps > 0 else "long_a_short_b"
            out.append(ArbOpportunity(
                symbol_a=sym_a, exch_a=exch_a, price_a_usd=p_a,
                symbol_b=sym_b, exch_b=exch_b, price_b_usd=p_b,
                spread_bps=spread_bps, direction=direction,
                notional_usd=notional_cap_usd,
            ))
    return out


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Simulate prices: BHP NYSE at $60, BHP ASX at A$90 (= ~$59.4 USD)
        prices = {
            ("BHP", "SMART"): (60.00, "USD"),
            ("BHP", "ASX"): (90.00, "AUD"),
            ("AZN", "SMART"): (75.50, "USD"),
            ("AZN", "LSE"): (12000, "GBP"),  # in pence → 120 GBP → $153 (spread)
            ("SAP", "SMART"): (155.00, "USD"),
            ("SAP", "IBIS"): (142.00, "EUR"),  # = $154.78
        }
        opps = detect_opportunities(prices)
        for o in opps:
            print(f"{o.symbol_a}.{o.exch_a}=${o.price_a_usd:.2f} vs "
                  f"{o.symbol_b}.{o.exch_b}=${o.price_b_usd:.2f} "
                  f"spread={o.spread_bps:.1f}bps direction={o.direction}")
        print("OK")
