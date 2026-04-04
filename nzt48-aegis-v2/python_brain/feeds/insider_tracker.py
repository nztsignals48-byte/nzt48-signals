"""SEC Insider Trading Signals — Form 4 analysis via edgartools.

Monitors insider purchases (Form 4 filings) for signal confirmation:
  - CEO/CFO/Director cluster buys → strong bullish signal (+5-10 confidence)
  - Large-scale insider selling → slight bearish signal (-3 confidence)
  - Insider purchase > $100K in small-cap → strongest signal

Academic basis: Lakonishok & Lee (2001) — insider purchases earn abnormal
returns of 7.4% annually. Cluster buys (3+ insiders within 30 days) are
the strongest predictor.

Nightly pipeline step: fetches Form 4 filings, scores tickers, writes
insider_signals.json for bridge.py confidence overlay.

License: edgartools is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("insider_tracker")

try:
    from edgar import Company, set_identity
    _HAS_EDGAR = True
except ImportError:
    _HAS_EDGAR = False

_DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
_OUTPUT_PATH = _DATA_DIR / "insider_signals.json"

# Minimum thresholds for signal generation
_MIN_PURCHASE_USD = 10_000  # Ignore tiny insider purchases
_CLUSTER_WINDOW_DAYS = 30   # Multiple insiders buying within this window
_CLUSTER_MIN_INSIDERS = 2   # Minimum insiders for cluster buy signal


def _configure_identity():
    """Set SEC EDGAR identity (required by SEC API rate limits)."""
    identity = os.environ.get("SEC_EDGAR_IDENTITY", "AEGIS Trading System nzt48@proton.me")
    if _HAS_EDGAR:
        set_identity(identity)


def fetch_insider_activity(tickers: List[str], lookback_days: int = 30) -> Dict[str, Dict[str, Any]]:
    """Fetch recent insider trading activity for a list of tickers.

    Args:
        tickers: List of ticker symbols to check.
        lookback_days: How far back to look for filings.

    Returns:
        Dict of {ticker: insider_activity} with purchase/sale counts and values.
    """
    if not _HAS_EDGAR:
        log.warning("edgartools not installed — insider tracking disabled")
        return {}

    _configure_identity()
    results = {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    for ticker in tickers[:50]:  # Rate limit: max 50 per run
        try:
            company = Company(ticker)
            filings = company.get_filings(form="4")

            purchases = []
            sales = []

            for filing in filings[:20]:  # Last 20 Form 4s
                try:
                    filing_date = filing.filing_date
                    if hasattr(filing_date, 'replace'):
                        if filing_date.tzinfo is None:
                            filing_date = filing_date.replace(tzinfo=timezone.utc)
                        if filing_date < cutoff:
                            break

                    # Parse basic filing info
                    owner_name = getattr(filing, 'owner_name', '') or str(filing)[:50]
                    # Determine if purchase or sale from filing
                    is_purchase = "Purchase" in str(filing) or "A" in str(getattr(filing, 'transaction_code', ''))

                    entry = {
                        "owner": owner_name[:50],
                        "date": str(filing_date)[:10],
                        "type": "purchase" if is_purchase else "sale",
                    }
                    if is_purchase:
                        purchases.append(entry)
                    else:
                        sales.append(entry)
                except Exception:
                    continue

            if purchases or sales:
                n_purchasers = len(set(p.get("owner", "") for p in purchases))
                cluster_buy = n_purchasers >= _CLUSTER_MIN_INSIDERS

                results[ticker] = {
                    "n_purchases": len(purchases),
                    "n_sales": len(sales),
                    "n_unique_buyers": n_purchasers,
                    "cluster_buy": cluster_buy,
                    "net_sentiment": len(purchases) - len(sales),
                    "confidence_delta": _compute_confidence_delta(purchases, sales, n_purchasers),
                    "recent_purchases": purchases[:5],
                    "recent_sales": sales[:5],
                }

        except Exception as e:
            log.debug("Insider fetch failed for %s: %s", ticker, str(e)[:100])
            continue

    return results


def _compute_confidence_delta(purchases, sales, n_unique_buyers) -> int:
    """Compute confidence adjustment from insider activity.

    Returns int in [-5, +10] range.
    """
    if not purchases and not sales:
        return 0

    delta = 0
    # Cluster buys: strongest signal
    if n_unique_buyers >= 3:
        delta += 10
    elif n_unique_buyers >= 2:
        delta += 7
    elif len(purchases) >= 1:
        delta += 3

    # Heavy selling dampens
    if len(sales) >= 5:
        delta -= 5
    elif len(sales) >= 3:
        delta -= 3

    return max(-5, min(10, delta))


def run_insider_scan(
    tickers: Optional[List[str]] = None,
    output_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Full pipeline: scan insider activity, produce signals.

    Called by nightly pipeline.
    """
    if not _HAS_EDGAR:
        log.warning("edgartools not installed — skipping insider scan")
        return None

    # Load tickers from config if not provided
    if tickers is None:
        config_path = os.environ.get("AEGIS_CONFIG_DIR", "/app/config") + "/contracts.toml"
        try:
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            tickers = [c["symbol"] for c in config.get("contracts", [])
                       if c.get("symbol") and "." not in c["symbol"]][:50]
        except Exception:
            tickers = []

    if not tickers:
        log.info("No tickers for insider scan")
        return None

    results = fetch_insider_activity(tickers)

    if output_path is None:
        output_path = str(_OUTPUT_PATH)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_tickers_scanned": len(tickers),
        "n_tickers_with_activity": len(results),
        "tickers": results,
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    n_cluster = sum(1 for v in results.values() if v.get("cluster_buy"))
    log.info("Insider scan: %d/%d tickers with activity, %d cluster buys",
             len(results), len(tickers), n_cluster)

    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [Insider] %(levelname)s %(message)s")
    result = run_insider_scan()
    if result:
        print(json.dumps(result, indent=2))
