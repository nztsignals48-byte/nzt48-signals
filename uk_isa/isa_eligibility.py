"""
NZT-48 AEGIS A-01 -- ISA Eligibility Pre-Trade Gate
====================================================
HARD VETO.  No override.  No bypass.  No exceptions.

One non-ISA-eligible trade inside a Stocks & Shares ISA voids the entire
tax wrapper under HMRC rules (ISA Regulations 1998, SI 1998/1870, reg 4ZA).
This gate prevents that from ever happening.

The whitelist is derived from ``TICKER_REGISTRY`` in ``isa_universe.py``
(the single source of truth).  Any ticker NOT present in the registry
is rejected with a WARNING-level log and a hard False return.

This module has ZERO configuration knobs by design.  There is no
``ISA_ELIGIBILITY_OVERRIDE`` flag, no ``--force-isa`` CLI arg, no
environment variable escape hatch.  The only way to make a new ticker
eligible is to add it to ``TICKER_REGISTRY`` in ``isa_universe.py``.

Usage
-----
    from uk_isa.isa_eligibility import is_isa_eligible

    if not is_isa_eligible(signal.ticker):
        continue  # skip this signal entirely

Exports
-------
    - is_isa_eligible(ticker: str) -> bool
    - check_isa_batch(tickers: list[str]) -> dict[str, bool]
"""

from __future__ import annotations

import logging
from typing import Dict, List

from uk_isa.isa_universe import TICKER_REGISTRY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Frozen whitelist -- computed once at import time, immutable thereafter.
# Uses frozenset so it cannot be mutated at runtime (defence in depth).
# ---------------------------------------------------------------------------
_ISA_WHITELIST: frozenset[str] = frozenset(TICKER_REGISTRY.keys())


def is_isa_eligible(ticker: str) -> bool:
    """Check whether *ticker* is ISA-eligible.

    This is a HARD VETO gate.  Returns ``False`` for any ticker not
    present in ``TICKER_REGISTRY``.  There is no override mechanism.

    Parameters
    ----------
    ticker : str
        Yahoo Finance ticker symbol (e.g. ``"QQQ3.L"``).

    Returns
    -------
    bool
        ``True`` if the ticker is in the ISA whitelist, ``False`` otherwise.

    Side Effects
    ------------
    Logs a WARNING for every rejected ticker, including the ticker name
    and the reason for rejection.  This creates an audit trail in case
    of misconfiguration or unexpected signals reaching the gate.
    """
    if ticker in _ISA_WHITELIST:
        return True

    logger.warning(
        "A-01 ISA ELIGIBILITY VETO: ticker '%s' is NOT in the ISA whitelist "
        "(%d approved tickers). Trade BLOCKED to protect ISA tax wrapper. "
        "To add this ticker, update TICKER_REGISTRY in uk_isa/isa_universe.py.",
        ticker,
        len(_ISA_WHITELIST),
    )
    return False


def check_isa_batch(tickers: List[str]) -> Dict[str, bool]:
    """Check ISA eligibility for a batch of tickers.

    Convenience function for bulk validation (e.g. at startup or in
    PDF report generation).

    Parameters
    ----------
    tickers : list[str]
        List of Yahoo Finance ticker symbols.

    Returns
    -------
    dict[str, bool]
        Mapping of ticker -> eligibility status.
    """
    return {t: is_isa_eligible(t) for t in tickers}
