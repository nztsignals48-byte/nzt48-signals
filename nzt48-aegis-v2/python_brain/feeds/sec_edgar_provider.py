"""SEC EDGAR Filing Provider — automated 8-K/10-Q download pipeline.

Downloads material event filings (8-K) and quarterly reports (10-Q) from SEC EDGAR.
Feeds into Gemini scanner context and event_calendar.py for catalyst tracking.

License: sec-edgar-downloader is MIT.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("sec_edgar_provider")

# Mapping of AEGIS tickers to SEC CIK-compatible symbols
# Only US-listed equities have SEC filings
_AEGIS_US_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "SLV",
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "JPM", "BAC", "GS", "MS", "WFC", "C",
    "XOM", "CVX", "COP", "SLB",
    "JNJ", "UNH", "PFE", "ABBV",
}


def download_recent_filings(
    tickers: Optional[List[str]] = None,
    filing_types: Optional[List[str]] = None,
    amount: int = 3,
    output_dir: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Download recent SEC filings for specified tickers.

    Args:
        tickers: List of ticker symbols. Defaults to AEGIS US tickers.
        filing_types: List of filing types (e.g., ["8-K", "10-Q"]). Defaults to ["8-K"].
        amount: Number of most recent filings per ticker per type.
        output_dir: Where to save filings. Defaults to /app/data/sec_filings/

    Returns:
        Dict mapping ticker → list of filing metadata dicts.
    """
    if output_dir is None:
        output_dir = os.environ.get("AEGIS_DATA_DIR", "/app/data") + "/sec_filings"
    if tickers is None:
        tickers = list(_AEGIS_US_TICKERS)
    if filing_types is None:
        filing_types = ["8-K"]

    try:
        from sec_edgar_downloader import Downloader
    except ImportError:
        log.warning("sec-edgar-downloader not installed — pip install sec-edgar-downloader")
        return {}

    os.makedirs(output_dir, exist_ok=True)

    # SEC requires a user agent with name and email
    company_name = os.environ.get("SEC_COMPANY_NAME", "AEGIS Research")
    email = os.environ.get("SEC_EMAIL", "research@example.com")

    results: Dict[str, List[Dict[str, Any]]] = {}

    try:
        dl = Downloader(company_name, email, output_dir)

        for ticker in tickers:
            ticker_filings = []
            for ftype in filing_types:
                try:
                    dl.get(ftype, ticker, amount=amount)
                    # Check what was downloaded
                    ticker_dir = Path(output_dir) / "sec-edgar-filings" / ticker / ftype
                    if ticker_dir.exists():
                        for filing_dir in sorted(ticker_dir.iterdir(), reverse=True)[:amount]:
                            # Read the filing text
                            for txt_file in filing_dir.glob("*.txt"):
                                try:
                                    content = txt_file.read_text(errors="replace")[:5000]  # First 5K chars
                                    ticker_filings.append({
                                        "ticker": ticker,
                                        "type": ftype,
                                        "date": filing_dir.name[:10] if len(filing_dir.name) >= 10 else "",
                                        "path": str(txt_file),
                                        "preview": content[:500],
                                    })
                                except Exception:
                                    pass
                    log.info("  %s %s: %d filings", ticker, ftype, len(ticker_filings))
                except Exception as e:
                    log.warning("  %s %s: FAILED — %s", ticker, ftype, str(e)[:80])

            if ticker_filings:
                results[ticker] = ticker_filings

    except Exception as e:
        log.error("SEC EDGAR downloader failed: %s", str(e)[:200])

    # Write summary metadata
    summary_path = os.path.join(output_dir, "filings_summary.json")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers_scanned": len(tickers),
        "filings_found": sum(len(v) for v in results.values()),
        "filings": {
            ticker: [
                {"type": f["type"], "date": f["date"], "preview": f["preview"][:200]}
                for f in filings
            ]
            for ticker, filings in results.items()
        },
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    log.info("SEC filings summary: %s (%d tickers, %d filings)",
             summary_path, len(results), summary["filings_found"])

    return results


def get_material_events(
    tickers: Optional[List[str]] = None,
    days_back: int = 7,
) -> List[Dict[str, Any]]:
    """Get recent material events (8-K filings) for event-driven signals.

    Returns simplified event list suitable for event_calendar.py consumption.
    """
    filings = download_recent_filings(
        tickers=tickers,
        filing_types=["8-K"],
        amount=2,
    )

    events = []
    cutoff = date.today() - timedelta(days=days_back)

    for ticker, ticker_filings in filings.items():
        for filing in ticker_filings:
            try:
                filing_date = date.fromisoformat(filing["date"][:10])
                if filing_date >= cutoff:
                    events.append({
                        "ticker": ticker,
                        "event_type": "SEC_8K",
                        "date": filing["date"],
                        "preview": filing["preview"][:300],
                        "source": "SEC EDGAR",
                    })
            except (ValueError, KeyError):
                pass

    return sorted(events, key=lambda x: x.get("date", ""), reverse=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [SEC] %(levelname)s %(message)s")
    log.info("Downloading SEC filings...")
    # Only download for a small test set
    results = download_recent_filings(
        tickers=["AAPL", "MSFT", "NVDA"],
        filing_types=["8-K"],
        amount=2,
    )
    print(f"\nDownloaded filings for {len(results)} tickers")
    for ticker, filings in results.items():
        print(f"  {ticker}: {len(filings)} filings")
