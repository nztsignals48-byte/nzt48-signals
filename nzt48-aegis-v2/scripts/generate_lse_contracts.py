#!/usr/bin/env python3
"""
Generate LSE equity contracts for AEGIS V2 contracts.toml.

Fetches FTSE 100, FTSE 250, and FTSE SmallCap constituents from Wikipedia,
then outputs TOML-formatted contract entries for IBKR.

Output goes to stdout. Redirect to file as needed.
"""

import re
import sys
import time
import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────
# Existing LSEETF / LSE symbols already in contracts.toml — EXCLUDE these
# ──────────────────────────────────────────────────────────────────────────
EXISTING_SYMBOLS = {
    # LSEETF leveraged/inverse ETPs
    "QQQ3.L", "QQQS.L", "3LUS.L", "3USS.L", "QQQ5.L", "3SEM.L",
    "NVD3.L", "TSL3.L", "GPT3.L", "TSM3.L", "MU2.L", "5SPY.L",
    "3LTS.L", "3LAM.L", "3LMS.L", "3LNV.L", "3LAP.L", "3LGO.L",
    "3LAZ.L", "3LBA.L", "3LFB.L", "3LRI.L", "3LSI.L",
    "3STS.L", "3SAM.L", "3SMS.L", "3SNV.L", "3SAP.L",
    "3USL.L", "MAG7.L", "3LRR.L", "5LUS.L", "5USS.L", "5USL.L",
    "5ULS.L", "QS5L.L", "QS5S.L", "LQS5.L", "SQS5.L",
    "3NVD.L", "3TSL.L", "LQQS.L", "LQQ3.L",
    "3HCL.L", "3SIL.L", "AMD3.L", "ARM3.L", "NVDS.L", "TSLS.L",
    "3LAA.L", "3LAL.L", "3LMI.L",
    # LSE equities already in contracts.toml
    "BP.L", "SHEL.L", "GLEN.L", "BARC.L", "STAN.L",
}

# GICS-like sector mapping for known companies (best-effort)
# We'll try to extract from Wikipedia tables first
SECTOR_FALLBACK = "General"

# ──────────────────────────────────────────────────────────────────────────
# Wikipedia scraping
# ──────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AEGIS-V2-ContractGen/1.0"
}


def fetch_ftse100():
    """Fetch FTSE 100 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_100_Index"
    print("# Fetching FTSE 100 from Wikipedia...", file=sys.stderr)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = {}

    # Find the constituents table — look for table with "EPIC" or "Ticker" column
    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue
        header_cells = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

        # FTSE 100 Wikipedia table has columns like: Company, EPIC, FTSE ICB sector
        epic_idx = None
        company_idx = None
        sector_idx = None

        for i, h in enumerate(header_cells):
            if "epic" in h or "ticker" in h:
                epic_idx = i
            if "company" in h:
                company_idx = i
            if "sector" in h or "icb" in h:
                sector_idx = i

        if epic_idx is None:
            continue

        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= epic_idx:
                continue

            ticker = cells[epic_idx].get_text(strip=True).upper()
            # Clean up ticker — remove any dots or spaces
            ticker = re.sub(r'[^A-Z0-9]', '', ticker)
            if not ticker:
                continue

            sector = SECTOR_FALLBACK
            if sector_idx is not None and len(cells) > sector_idx:
                sector = cells[sector_idx].get_text(strip=True)

            company = ""
            if company_idx is not None and len(cells) > company_idx:
                company = cells[company_idx].get_text(strip=True)

            symbol = f"{ticker}.L"
            tickers[symbol] = {
                "sector": clean_sector(sector),
                "company": company,
                "index": "FTSE100",
            }

    print(f"# FTSE 100: found {len(tickers)} tickers", file=sys.stderr)
    return tickers


def fetch_ftse250():
    """Fetch FTSE 250 constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_250_Index"
    print("# Fetching FTSE 250 from Wikipedia...", file=sys.stderr)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = {}

    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue
        header_cells = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

        epic_idx = None
        company_idx = None
        sector_idx = None

        for i, h in enumerate(header_cells):
            if "epic" in h or "ticker" in h:
                epic_idx = i
            if "company" in h:
                company_idx = i
            if "sector" in h or "icb" in h:
                sector_idx = i

        if epic_idx is None:
            continue

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= epic_idx:
                continue

            ticker = cells[epic_idx].get_text(strip=True).upper()
            ticker = re.sub(r'[^A-Z0-9]', '', ticker)
            if not ticker:
                continue

            sector = SECTOR_FALLBACK
            if sector_idx is not None and len(cells) > sector_idx:
                sector = cells[sector_idx].get_text(strip=True)

            company = ""
            if company_idx is not None and len(cells) > company_idx:
                company = cells[company_idx].get_text(strip=True)

            symbol = f"{ticker}.L"
            tickers[symbol] = {
                "sector": clean_sector(sector),
                "company": company,
                "index": "FTSE250",
            }

    print(f"# FTSE 250: found {len(tickers)} tickers", file=sys.stderr)
    return tickers


def fetch_ftse_smallcap():
    """Fetch FTSE SmallCap constituents from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/FTSE_SmallCap_Index"
    print("# Fetching FTSE SmallCap from Wikipedia...", file=sys.stderr)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print("# FTSE SmallCap page not found or error, skipping", file=sys.stderr)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = {}

    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue
        header_cells = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

        epic_idx = None
        company_idx = None
        sector_idx = None

        for i, h in enumerate(header_cells):
            if "epic" in h or "ticker" in h:
                epic_idx = i
            if "company" in h:
                company_idx = i
            if "sector" in h or "icb" in h:
                sector_idx = i

        if epic_idx is None:
            continue

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= epic_idx:
                continue

            ticker = cells[epic_idx].get_text(strip=True).upper()
            ticker = re.sub(r'[^A-Z0-9]', '', ticker)
            if not ticker:
                continue

            sector = SECTOR_FALLBACK
            if sector_idx is not None and len(cells) > sector_idx:
                sector = cells[sector_idx].get_text(strip=True)

            company = ""
            if company_idx is not None and len(cells) > company_idx:
                company = cells[company_idx].get_text(strip=True)

            symbol = f"{ticker}.L"
            tickers[symbol] = {
                "sector": clean_sector(sector),
                "company": company,
                "index": "FTSE_SmallCap",
            }

    print(f"# FTSE SmallCap: found {len(tickers)} tickers", file=sys.stderr)
    return tickers


def fetch_ftse_allshare_extra():
    """
    Try to fetch additional LSE tickers from the FTSE All-Share Wikipedia page.
    This may overlap with 100/250/SmallCap but we deduplicate.
    """
    url = "https://en.wikipedia.org/wiki/FTSE_All-Share_Index"
    print("# Fetching FTSE All-Share from Wikipedia...", file=sys.stderr)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print("# FTSE All-Share page not found or error, skipping", file=sys.stderr)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = {}

    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue
        header_cells = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

        epic_idx = None
        company_idx = None
        sector_idx = None

        for i, h in enumerate(header_cells):
            if "epic" in h or "ticker" in h:
                epic_idx = i
            if "company" in h:
                company_idx = i
            if "sector" in h or "icb" in h:
                sector_idx = i

        if epic_idx is None:
            continue

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= epic_idx:
                continue

            ticker = cells[epic_idx].get_text(strip=True).upper()
            ticker = re.sub(r'[^A-Z0-9]', '', ticker)
            if not ticker:
                continue

            sector = SECTOR_FALLBACK
            if sector_idx is not None and len(cells) > sector_idx:
                sector = cells[sector_idx].get_text(strip=True)

            company = ""
            if company_idx is not None and len(cells) > company_idx:
                company = cells[company_idx].get_text(strip=True)

            symbol = f"{ticker}.L"
            tickers[symbol] = {
                "sector": clean_sector(sector),
                "company": company,
                "index": "FTSE_AllShare",
            }

    print(f"# FTSE All-Share: found {len(tickers)} tickers", file=sys.stderr)
    return tickers


def fetch_ftse_aim100():
    """Fetch FTSE AIM 100 constituents from Wikipedia for additional coverage."""
    url = "https://en.wikipedia.org/wiki/FTSE_AIM_100_Index"
    print("# Fetching FTSE AIM 100 from Wikipedia...", file=sys.stderr)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        print("# FTSE AIM 100 page not found or error, skipping", file=sys.stderr)
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tickers = {}

    tables = soup.find_all("table", class_="wikitable")
    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue
        header_cells = [th.get_text(strip=True).lower() for th in headers_row.find_all(["th", "td"])]

        epic_idx = None
        company_idx = None
        sector_idx = None

        for i, h in enumerate(header_cells):
            if "epic" in h or "ticker" in h or "tidm" in h:
                epic_idx = i
            if "company" in h:
                company_idx = i
            if "sector" in h or "icb" in h:
                sector_idx = i

        if epic_idx is None:
            continue

        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) <= epic_idx:
                continue

            ticker = cells[epic_idx].get_text(strip=True).upper()
            ticker = re.sub(r'[^A-Z0-9]', '', ticker)
            if not ticker:
                continue

            sector = SECTOR_FALLBACK
            if sector_idx is not None and len(cells) > sector_idx:
                sector = cells[sector_idx].get_text(strip=True)

            company = ""
            if company_idx is not None and len(cells) > company_idx:
                company = cells[company_idx].get_text(strip=True)

            symbol = f"{ticker}.L"
            tickers[symbol] = {
                "sector": clean_sector(sector),
                "company": company,
                "index": "FTSE_AIM100",
            }

    print(f"# FTSE AIM 100: found {len(tickers)} tickers", file=sys.stderr)
    return tickers


# ──────────────────────────────────────────────────────────────────────────
# Sector normalization
# ──────────────────────────────────────────────────────────────────────────

SECTOR_MAP = {
    # ICB sector names → clean short names
    "aerospace and defence": "Aerospace_Defense",
    "aerospace & defence": "Aerospace_Defense",
    "automobiles and parts": "Automotive",
    "automobiles & parts": "Automotive",
    "banks": "Banks",
    "banking": "Banks",
    "beverages": "Beverages",
    "chemicals": "Chemicals",
    "closed end investments": "Investment_Trusts",
    "collective investments": "Investment_Trusts",
    "construction and materials": "Construction",
    "construction & materials": "Construction",
    "consumer services": "Consumer_Services",
    "consumer staples": "Consumer_Staples",
    "consumer discretionary": "Consumer_Discretionary",
    "electronic and electrical equipment": "Electronics",
    "electronic & electrical equipment": "Electronics",
    "electricity": "Utilities",
    "energy": "Energy",
    "equity investment instruments": "Investment_Trusts",
    "equity investments": "Investment_Trusts",
    "financial services": "Finance",
    "financials": "Finance",
    "fixed line telecommunications": "Telecommunications",
    "food and drug retailers": "Retail",
    "food & tobacco": "Food",
    "food producers": "Food",
    "food, beverage and tobacco": "Food",
    "food, beverage & tobacco": "Food",
    "forestry and paper": "Materials",
    "gas, water and multiutilities": "Utilities",
    "gas, water & multiutilities": "Utilities",
    "general financial": "Finance",
    "general industrials": "Industrials",
    "general retailers": "Retail",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "health care equipment and services": "Healthcare",
    "health care equipment & services": "Healthcare",
    "household goods": "Consumer_Goods",
    "household goods and home construction": "Consumer_Goods",
    "household goods & home construction": "Consumer_Goods",
    "industrial engineering": "Engineering",
    "industrial goods and services": "Industrials",
    "industrial goods & services": "Industrials",
    "industrial metals and mining": "Mining",
    "industrial metals & mining": "Mining",
    "industrial transportation": "Transport",
    "industrials": "Industrials",
    "insurance": "Insurance",
    "investment banking and brokerage services": "Finance",
    "investment banking & brokerage services": "Finance",
    "investment trust": "Investment_Trusts",
    "investment trusts": "Investment_Trusts",
    "leisure goods": "Leisure",
    "life insurance": "Insurance",
    "media": "Media",
    "mining": "Mining",
    "mobile telecommunications": "Telecommunications",
    "nonequity investment instruments": "Investment_Trusts",
    "nonlife insurance": "Insurance",
    "non-life insurance": "Insurance",
    "oil and gas": "Energy",
    "oil and gas producers": "Energy",
    "oil, gas and coal": "Energy",
    "oil, gas & coal": "Energy",
    "oil equipment, services and distribution": "Energy",
    "personal care, drug and grocery stores": "Retail",
    "personal care, drug & grocery stores": "Retail",
    "personal goods": "Consumer_Goods",
    "pharmaceuticals": "Pharma",
    "pharmaceuticals and biotechnology": "Pharma",
    "pharmaceuticals & biotechnology": "Pharma",
    "precious metals and mining": "Mining",
    "precious metals & mining": "Mining",
    "real estate": "Real_Estate",
    "real estate investment and services": "Real_Estate",
    "real estate investment trusts": "REITs",
    "retailers": "Retail",
    "software and computer services": "Technology",
    "software & computer services": "Technology",
    "support services": "Services",
    "technology": "Technology",
    "technology hardware and equipment": "Technology",
    "technology hardware & equipment": "Technology",
    "telecommunications": "Telecommunications",
    "telecommunications equipment": "Telecommunications",
    "telecommunications service providers": "Telecommunications",
    "tobacco": "Tobacco",
    "travel and leisure": "Travel_Leisure",
    "travel & leisure": "Travel_Leisure",
    "utilities": "Utilities",
}


def clean_sector(raw: str) -> str:
    """Normalize a sector name to a clean identifier."""
    if not raw or raw.strip() == "":
        return SECTOR_FALLBACK

    raw_lower = raw.strip().lower()

    # Direct mapping
    if raw_lower in SECTOR_MAP:
        return SECTOR_MAP[raw_lower]

    # Partial match — check if raw sector name contains a known key
    # Prefer longer keys first to avoid "investment trusts" matching before
    # "real estate investment trusts"
    sorted_keys = sorted(SECTOR_MAP.keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key in raw_lower:
            return SECTOR_MAP[key]

    # If it looks reasonable, title-case it and replace spaces with underscores
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', raw.strip())
    if cleaned:
        return re.sub(r'\s+', '_', cleaned.strip().title())

    return SECTOR_FALLBACK


# ──────────────────────────────────────────────────────────────────────────
# TOML output
# ──────────────────────────────────────────────────────────────────────────

def emit_toml(all_tickers: dict):
    """Print TOML contract entries to stdout."""
    # Sort by index priority then alphabetically
    index_order = {"FTSE100": 0, "FTSE250": 1, "FTSE_SmallCap": 2, "FTSE_AIM100": 3, "FTSE_AllShare": 4}

    sorted_symbols = sorted(
        all_tickers.keys(),
        key=lambda s: (index_order.get(all_tickers[s]["index"], 99), s)
    )

    current_index = None
    count = 0

    for symbol in sorted_symbols:
        info = all_tickers[symbol]
        idx = info["index"]

        if idx != current_index:
            current_index = idx
            # Print section header
            label = idx.replace("_", " ")
            count_in_idx = sum(1 for s in sorted_symbols if all_tickers[s]["index"] == idx)
            print(f"\n# {'=' * 75}")
            print(f"# {label} ({count_in_idx} stocks)")
            print(f"# {'=' * 75}")

        print(f"""
[[contracts]]
symbol = "{symbol}"
con_id = 0
exchange = "LSE"
sec_type = "STK"
currency = "GBP"
leverage = 1
sector = "{info['sector']}"
inverse_of = \"\"""")
        count += 1

    return count


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    all_tickers = {}

    # Fetch from all sources with small delays to be polite
    ftse100 = fetch_ftse100()
    time.sleep(1)
    ftse250 = fetch_ftse250()
    time.sleep(1)
    ftse_sc = fetch_ftse_smallcap()
    time.sleep(1)
    ftse_aim = fetch_ftse_aim100()
    time.sleep(1)
    ftse_all = fetch_ftse_allshare_extra()

    # Merge in priority order (FTSE100 > 250 > SmallCap > AIM100 > AllShare)
    # Earlier sources take priority for sector info
    for source in [ftse100, ftse250, ftse_sc, ftse_aim, ftse_all]:
        for symbol, info in source.items():
            if symbol not in all_tickers:
                all_tickers[symbol] = info

    # Remove existing symbols
    for sym in EXISTING_SYMBOLS:
        all_tickers.pop(sym, None)

    # Remove Investment Trusts — closed-end funds, not suitable for momentum equity trading
    # They make up ~30% of FTSE 250 and dilute the universe with low-volume, low-beta instruments
    inv_trust_count = 0
    inv_trust_syms = []
    for sym, info in all_tickers.items():
        sector = info["sector"]
        if sector in ("Investment_Trusts", "Investment_Trust"):
            inv_trust_syms.append(sym)
            inv_trust_count += 1
    for sym in inv_trust_syms:
        all_tickers.pop(sym)
    print(f"# Filtered out {inv_trust_count} Investment Trusts (closed-end funds)", file=sys.stderr)

    # Remove obviously bad tickers (single char, too long, etc.)
    bad_syms = []
    for sym in all_tickers:
        ticker_part = sym.replace(".L", "")
        if len(ticker_part) < 2 or len(ticker_part) > 6:
            bad_syms.append(sym)
        # Remove anything that looks like an ETP (numbers followed by letters typical of leveraged)
        if re.match(r'^\d[A-Z]{2,3}$', ticker_part):
            bad_syms.append(sym)
    for sym in bad_syms:
        all_tickers.pop(sym, None)

    # Summary to stderr
    print(f"\n# ═══════════════════════════════════════════════════════════════", file=sys.stderr)
    print(f"# TOTAL UNIQUE LSE EQUITIES: {len(all_tickers)}", file=sys.stderr)

    index_counts = {}
    for info in all_tickers.values():
        idx = info["index"]
        index_counts[idx] = index_counts.get(idx, 0) + 1
    for idx, cnt in sorted(index_counts.items()):
        print(f"#   {idx}: {cnt}", file=sys.stderr)

    sector_counts = {}
    for info in all_tickers.values():
        s = info["sector"]
        sector_counts[s] = sector_counts.get(s, 0) + 1
    print(f"# Sectors represented: {len(sector_counts)}", file=sys.stderr)
    for s, cnt in sorted(sector_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"#   {s}: {cnt}", file=sys.stderr)
    print(f"# ═══════════════════════════════════════════════════════════════", file=sys.stderr)

    # Print header
    print("# ═══════════════════════════════════════════════════════════════════════════")
    print(f"# LSE Equities — Auto-generated by generate_lse_contracts.py")
    indices_used = sorted(set(info["index"] for info in all_tickers.values()))
    print(f"# Sources: {', '.join(i.replace('_', ' ') for i in indices_used)}")
    print(f"# Total: {len(all_tickers)} contracts")
    print(f"# All: exchange=LSE, currency=GBP, leverage=1, con_id=0 (runtime resolution)")
    print("# ═══════════════════════════════════════════════════════════════════════════")

    count = emit_toml(all_tickers)
    print(f"\n# Total contracts emitted: {count}", file=sys.stderr)


if __name__ == "__main__":
    main()
