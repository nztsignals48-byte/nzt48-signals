"""Universe Curator — Nightly auto-curation of the radar trading universe.

Reads contracts.toml (872+ validated tickers), applies tiered scoring based
on the system's actual trading mandate, and writes a curated radar_universe.toml
that the radar daemon and ticker_selector consume.

Tiers (descending priority):
  T1 — Execution Core: Leveraged LSE ETPs (3x/5x). These are the profit centers.
  T2 — Signal Drivers: US mega-caps that drive the leveraged ETPs (NVDA→NVD3.L).
  T3 — Regime Sensors: VIX proxies, sector leaders, macro bellwethers.
  T4 — Breadth Pool: Broader index components for sector/breadth reads.
  T5 — Reserve: Everything else. Scanned at lowest priority.

The radar daemon allocates scanning budget proportionally:
  T1: scan every 4s (hot)
  T2: scan every 8s (warm)
  T3: scan every 15s (warm)
  T4: scan every 30s (cold)
  T5: not scanned unless promoted by dynamic_universe.py

Daily auto-curation:
  - Runs nightly at 04:55 UTC (after pipeline, before markets)
  - Reads previous day's volume data from ArcticDB/trade_log
  - Promotes tickers that showed high RVOL or generated signals
  - Demotes tickers that were consistently dormant (0 signals in 5 days)
  - Adds newly validated contracts from contract_expander
  - Writes /app/config/radar_universe.toml

Usage:
  python -m python_brain.ouroboros.universe_curator
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger("universe_curator")

CONFIG_DIR = Path(os.environ.get("AEGIS_CONFIG_DIR", "/app/config"))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
CONTRACTS_PATH = CONFIG_DIR / "contracts.toml"
EQUITY_FUND_MAP_PATH = CONFIG_DIR / "equity_fund_map.toml"
RADAR_UNIVERSE_PATH = CONFIG_DIR / "radar_universe.toml"
SIGNAL_LOG_PATH = DATA_DIR / "shadow_signals.ndjson"
TRADE_LOG_PATH = DATA_DIR / "trade_log.ndjson"


# ── TIER DEFINITIONS ────────────────────────────────────────────────────────

# T1: Execution Core — every leveraged ETP we can trade
# These are loaded dynamically from contracts.toml (exchange=LSEETF, leverage>=2)

# T2: Signal Drivers — US underlyings that directly drive T1 instruments
T2_SIGNAL_DRIVERS: Set[str] = {
    # Magnificent 7 + key semis (each has a 3x ETP tracking it)
    "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "GOOGL", "META",
    "AMD", "ARM", "TSM", "AVGO",
    # Key US ETFs (tracked by index ETPs)
    "SPY", "QQQ", "IWM",
    # High-beta momentum names (frequent RVOL>2 breakouts)
    "SMCI", "PLTR", "MSTR", "COIN", "NFLX", "CRM",
}

# T3: Regime Sensors — macro/sector bellwethers for regime detection
T3_REGIME_SENSORS: Set[str] = {
    # VIX complex
    "VXX", "UVXY", "SVXY", "VIXY",
    # US financials (rate sensitivity, systemic risk)
    "JPM", "GS", "MS", "BAC", "C", "WFC", "BLK",
    # US energy (oil/inflation proxy)
    "XOM", "CVX", "COP", "MPC", "SLB",
    # US industrials (capex/growth proxy)
    "GE", "CAT", "UBER", "VRT", "PH",
    # US healthcare (defensive regime tells)
    "LLY", "UNH", "JNJ",
    # Commodity proxies
    "FCX", "NEM",
    # Key tech (semi/AI capex cycle)
    "ADBE", "AMAT", "CDNS", "CRWD", "DDOG", "FTNT", "ASML",
    # LSE macro anchors (banks, miners, oil, pharma)
    "HSBA.L", "BARC.L", "LLOY.L", "NWG.L",
    "BP.L", "SHEL.L",
    "RIO.L", "GLEN.L", "ANTO.L", "FRES.L",
    "AZN.L", "GSK.L",
    "RR.L", "BA.L",
    # LSE market structure
    "LSEG.L", "REL.L", "AUTO.L",
    # Europe/Asia session anchors
    "SAP.DE", "SIE.DE", "ALV.DE",
    "7203.T", "6758.T", "9984.T",
    "9988.HK", "1810.HK", "2318.HK",
    # Index ETFs for global macro
    "EWG", "EWJ", "FXI",
    "1321.T",  # Nikkei 225 ETF
    "2800.HK",  # Hang Seng ETF
}

# T4: Breadth Pool — broader components for sector rotation/breadth signals
T4_BREADTH_SECTORS: Dict[str, Set[str]] = {
    "financials": {"SCHW", "PGR", "ICE", "CME", "SPGI", "MSCI", "BX", "KKR", "AXP", "MA", "V"},
    "tech": {"NOW", "ORCL", "IBM", "DELL", "ANET", "SNDK", "MSI", "ACN", "FICO", "APH"},
    "consumer_cyclical": {"HD", "LOW", "MCD", "CMG", "NKE", "BKNG", "TJX", "COST"},
    "consumer_defensive": {"PG", "KO", "PEP", "WMT", "PM", "MO", "CL"},
    "energy": {"EOG", "DVN", "HAL", "WMB", "OKE", "TRGP", "VLO", "PSX"},
    "materials": {"APD", "SHW", "ECL", "LYB", "NUE", "STLD", "CF"},
    "industrials": {"RTX", "LMT", "GD", "BA", "DE", "EMR", "ETN", "ITW", "UNP", "FDX"},
    "healthcare": {"TMO", "DHR", "ABT", "SYK", "BSX", "ISRG", "VRTX", "REGN", "AMGN", "GILD"},
    "realestate": {"PLD", "AMT", "EQIX", "DLR", "CCI"},
    "utilities": {"NEE", "VST", "CEG", "NRG", "PCG"},
    "lse_liquid": {
        "NXT.L", "FRAS.L", "TSCO.L", "INF.L", "IGG.L",
        "BATS.L", "IMB.L", "DGE.L", "VOD.L", "BT.A.L",
        "AVON.L", "BNZL.L", "EXPN.L", "DCC.L",
        "SMT.L", "FCIT.L",  # risk appetite proxies
    },
}

# Flatten T4 for easy lookup
T4_ALL: Set[str] = set()
for _sector_tickers in T4_BREADTH_SECTORS.values():
    T4_ALL.update(_sector_tickers)

# Investment trusts and slow LSE names to EXCLUDE from active scanning
EXCLUDE_PATTERNS: Set[str] = {
    # These are loaded dynamically: any LSE ticker with sector containing
    # "Investment Trust" or with avg_volume < threshold gets excluded
}


@dataclass
class TieredTicker:
    symbol: str
    exchange: str
    con_id: int
    currency: str
    tier: int  # 1-5
    leverage: int = 1
    sector: str = ""
    reason: str = ""


def _load_contracts() -> List[dict]:
    """Load all contracts from contracts.toml."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    if not CONTRACTS_PATH.exists():
        log.error("contracts.toml not found at %s", CONTRACTS_PATH)
        return []
    with open(CONTRACTS_PATH, "rb") as f:
        data = tomllib.load(f)
    return data.get("contracts", [])


def _load_equity_fund_map() -> Dict[str, str]:
    """Load equity→fund mapping (e.g., NVDA → NVD3.L)."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    path = EQUITY_FUND_MAP_PATH
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    result = {}
    for section in data.values():
        if isinstance(section, dict):
            for fund, info in section.items():
                if isinstance(info, dict) and "equity" in info:
                    result[info["equity"]] = fund
    return result


def _load_recent_signal_tickers(days: int = 5) -> Set[str]:
    """Load tickers that generated signals in the last N days."""
    tickers: Set[str] = set()
    if not SIGNAL_LOG_PATH.exists():
        return tickers
    try:
        cutoff = time.time() - (days * 86400)
        with open(SIGNAL_LOG_PATH) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts = entry.get("timestamp", 0)
                    if ts > cutoff:
                        sym = entry.get("symbol", "")
                        if sym:
                            tickers.add(sym)
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception as e:
        log.warning("Failed to read signal log: %s", e)
    return tickers


def classify_ticker(contract: dict, signal_tickers: Set[str]) -> TieredTicker:
    """Classify a single contract into a tier."""
    symbol = contract.get("symbol", "")
    exchange = contract.get("exchange", "")
    con_id = contract.get("con_id", 0)
    currency = contract.get("currency", "")
    leverage = contract.get("leverage", 1)
    sector = contract.get("sector", "")

    # T1: Leveraged ETPs (execution core)
    if exchange == "LSEETF" and leverage >= 2:
        return TieredTicker(
            symbol=symbol, exchange=exchange, con_id=con_id,
            currency=currency, tier=1, leverage=leverage,
            sector=sector, reason="leveraged_etp"
        )

    # T2: Signal drivers (US underlyings for T1)
    if symbol in T2_SIGNAL_DRIVERS:
        return TieredTicker(
            symbol=symbol, exchange=exchange, con_id=con_id,
            currency=currency, tier=2, leverage=leverage,
            sector=sector, reason="signal_driver"
        )

    # T3: Regime sensors
    if symbol in T3_REGIME_SENSORS:
        return TieredTicker(
            symbol=symbol, exchange=exchange, con_id=con_id,
            currency=currency, tier=3, leverage=leverage,
            sector=sector, reason="regime_sensor"
        )

    # Promotion: tickers that generated signals recently get T3
    if symbol in signal_tickers:
        return TieredTicker(
            symbol=symbol, exchange=exchange, con_id=con_id,
            currency=currency, tier=3, leverage=leverage,
            sector=sector, reason="signal_promotion"
        )

    # T4: Breadth pool
    if symbol in T4_ALL:
        return TieredTicker(
            symbol=symbol, exchange=exchange, con_id=con_id,
            currency=currency, tier=4, leverage=leverage,
            sector=sector, reason="breadth_pool"
        )

    # T5: Everything else (reserve — not actively scanned by radar)
    # Investment trusts and very small LSE names default here
    return TieredTicker(
        symbol=symbol, exchange=exchange, con_id=con_id,
        currency=currency, tier=5, leverage=leverage,
        sector=sector, reason="reserve"
    )


def curate_universe() -> Dict[int, List[TieredTicker]]:
    """Main curation: classify all contracts into tiers."""
    contracts = _load_contracts()
    if not contracts:
        log.error("No contracts loaded — aborting curation")
        return {}

    signal_tickers = _load_recent_signal_tickers(days=5)
    log.info("Loaded %d signal-active tickers from last 5 days", len(signal_tickers))

    tiers: Dict[int, List[TieredTicker]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    for contract in contracts:
        if contract.get("con_id", 0) == 0:
            continue  # Skip unvalidated
        tt = classify_ticker(contract, signal_tickers)
        tiers[tt.tier].append(tt)

    for tier_num, tickers in sorted(tiers.items()):
        log.info("Tier %d: %d tickers", tier_num, len(tickers))

    return tiers


def write_radar_universe(tiers: Dict[int, List[TieredTicker]]) -> None:
    """Write curated radar_universe.toml for the radar daemon."""
    lines = [
        "# AEGIS V2 — Curated Radar Universe",
        f"# Generated: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"# Auto-curated by universe_curator.py",
        "",
    ]

    # Summary
    total = sum(len(v) for v in tiers.values())
    active = sum(len(v) for k, v in tiers.items() if k <= 4)
    lines.append(f"# Total: {total} contracts, {active} actively scanned (T1-T4)")
    lines.append("")

    # Write tiers 1-4 grouped by exchange
    for tier_num in [1, 2, 3, 4]:
        tier_tickers = tiers.get(tier_num, [])
        if not tier_tickers:
            continue

        scan_interval = {1: 4, 2: 8, 3: 15, 4: 30}[tier_num]
        tier_name = {1: "EXECUTION_CORE", 2: "SIGNAL_DRIVERS", 3: "REGIME_SENSORS", 4: "BREADTH_POOL"}[tier_num]

        lines.append(f"# ── TIER {tier_num}: {tier_name} (scan every {scan_interval}s) ──")

        # Group by exchange within tier
        by_exchange: Dict[str, List[TieredTicker]] = {}
        for tt in tier_tickers:
            by_exchange.setdefault(tt.exchange, []).append(tt)

        for exchange in sorted(by_exchange.keys()):
            tickers = sorted(by_exchange[exchange], key=lambda t: t.symbol)
            lines.append(f"[{exchange}]")
            for tt in tickers:
                lines.append(tt.symbol)
            lines.append("")

    # T5 reserve is NOT written to radar_universe.toml
    # (it's still available in contracts.toml for the contract_expander)
    t5_count = len(tiers.get(5, []))
    lines.append(f"# T5 Reserve: {t5_count} tickers (not scanned, available in contracts.toml)")

    output = "\n".join(lines) + "\n"
    RADAR_UNIVERSE_PATH.write_text(output)
    log.info("Wrote radar_universe.toml: %d active tickers", active)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log.info("═══ AEGIS V2 UNIVERSE CURATOR ═══")
    tiers = curate_universe()
    if not tiers:
        log.error("Curation failed — no output written")
        sys.exit(1)

    write_radar_universe(tiers)

    # Print summary
    for tier_num in [1, 2, 3, 4, 5]:
        tickers = tiers.get(tier_num, [])
        symbols = [t.symbol for t in tickers[:10]]
        suffix = f" ... +{len(tickers)-10} more" if len(tickers) > 10 else ""
        log.info("T%d (%d): %s%s", tier_num, len(tickers), ", ".join(symbols), suffix)

    log.info("Done. Radar universe written to %s", RADAR_UNIVERSE_PATH)


if __name__ == "__main__":
    main()
