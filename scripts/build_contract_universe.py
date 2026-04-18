"""Build contracts.toml from IBKR-qualified tickers across 9 exchanges.

Sources (hardcoded lists of index components):
  US      S&P 500 + NASDAQ 100 + Russell 1000 liquid names
  UK      FTSE 100 + FTSE 250 + major LSE ETPs (LevShares / GraniteShares / WisdomTree)
  DE      DAX 40 + MDAX 50
  FR      CAC 40
  IT      FTSE MIB 40
  HK      HSI 50
  JP      Nikkei 225 top-50 liquid
  SG      STI 30
  AU      ASX 200 top-50

For each ticker, calls IBKR qualifyContracts to resolve to a real con_id
on the right exchange. Skips anything that doesn't qualify (delisted,
wrong market, etc.).

Outputs data/contracts_full.toml which is THEN merged into config/contracts.toml.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

OUT_PATH = Path("/Users/rr/aegis-v5/data/contracts_full.toml")


# Hardcoded index components. Curated — focus on liquid names.
US_SP500_TOP = [
    # FAANGMULA + megacaps (already in seed)
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","BRK.B",
    "LLY","JPM","V","UNH","XOM","WMT","MA","PG","JNJ","HD","ORCL","COST",
    "CVX","ABBV","MRK","ADBE","NFLX","CRM","KO","AMD","PEP","BAC","ACN",
    "MCD","TMO","CSCO","WFC","DIS","ABT","LIN","TXN","DHR","INTC","VZ",
    "PM","INTU","IBM","AMGN","NOW","NEE","NKE","RTX","SPGI","QCOM","HON",
    "LOW","MS","UPS","UNP","ELV","LMT","CAT","GS","ISRG","BA","BKNG","PLD",
    "SYK","T","BLK","MDLZ","ADP","TJX","GILD","C","AMT","AXP","DE","MMC",
    "VRTX","CB","ADI","CI","SCHW","MO","REGN","BMY","TMUS","CVS","SBUX",
    "ZTS","FI","PGR","ETN","LRCX","BSX","PYPL","BDX","SO","CME","CSX","KLAC",
    "AON","DUK","NOC","MU","CL","ITW","TGT","APH","SHW","PH","WM","MMM",
    "EMR","ICE","FCX","USB","GD","MCO","NXPI","ROP","ORLY","HUM","MDT",
    "SNPS","ECL","PNC","MCK","MAR","TFC","APD","CDNS","CTAS","PSA","PSX",
    "FDX","AJG","HCA","GM","AFL","TRV","COP","AZO","ADM","TT","CARR","NSC",
    "MPC","EOG","ROST","EW","MET","SLB","F","SRE","DXCM","PAYX","PCAR","AIG",
    "OXY","CPRT","PCG","WELL","AEP","KMB","ALL","O","KMI","MSI","EXC","STZ",
    "HSY","NUE","HLT","VLO","GIS","PRU","A","AMP","DLR","SPG","KHC","MSCI",
    "TEL","IDXX","OTIS","BKR","SYY","NEM","GEV","VRSK","MNST","FTNT","YUM",
    "AME","LHX","ROK","CTSH","CCI","ACGL","MCHP","EXR","BF.B","IT","DHI",
    "FIS","ODFL","TDG","DVN","ON","FAST","CTVA","DD","CMI","BIIB","EA",
    "HPQ","LEN","DELL","URI","RMD","CNC","XEL","VICI","NDAQ","HAL","ED",
    "DOW","KR","DG","WMB","NVR","AVB","CHTR","WEC","TSCO","PEG","PPG","GLW",
    "ES","FER","AWK","IQV","KDP","MTB","RSG","CMG","TTWO","APTV","FITB",
    "HES","GEHC","CSGP","FANG","STT","IR","STLD","CBRE","WST","MTD","PWR",
    "ZBH","BR","LYB","IRM","CDW","KEYS","WBD","CNP","RJF","TRGP","TSN",
    "BALL","HWM","SWK","PFG","WRB","HPE","AEE","HBAN","RF","CAH","DFS",
    "WDC","BAX","COO","PTC","NTAP","DRI","LVS","K","OMC","DTE","LDOS",
    "EIX","CFG","NTRS","IFF","BLDR","ALB","CHD","FOXA","FOX","EQR","UAL",
    "ATO","RCL","CLX","STE","ULTA","TPG","LUV","BRO","LH","GRMN","CMS","DXC",
]

# UK FTSE100 + 250 liquid subset (LSE)
UK_FTSE = [
    "AAL","ABDN","ABF","ADM","AHT","ANTO","AUTO","AV","AZN","BA","BARC","BATS",
    "BDEV","BKG","BME","BNZL","BP","BRBY","BT.A","CCEP","CCH","CNA","CPG","CRDA",
    "CRH","CTEC","DCC","DGE","DPLM","DUK","EXPN","EZJ","FCIT","FRAS","FRES","GLEN",
    "GSK","HIK","HL","HLN","HLMA","HSBA","HWDN","IAG","ICAG","ICG","IHG","III","IMB",
    "IMI","INF","ITRK","ITV","JD","KGF","LAND","LGEN","LLOY","LMP","LSE","LSEG",
    "MKS","MNDI","MNG","MRO","NG","NMC","NWG","NXT","OCDO","PHNX","PRU","PSH","PSN",
    "PSON","REL","RIO","RKT","RMG","RMV","RR","RS1","RTO","SBRY","SDR","SGE","SGRO",
    "SHEL","SMDS","SMIN","SMT","SN","SPX","SSE","STAN","SVT","TSCO","TW","ULVR",
    "UU","VOD","WEIR","WPP","WTB","AML","ASHM","BAKK","BEZ","BKG","BMI","BWY","CHK",
    "GNS","HAS","LEG","NEX","TRAC","ENT","BARC","WIZZ","JMAT","ENT","EURN","LGPS",
    "CGS","ASC","BRW","CBG","DOCS","SPI","TPK","SYNC","QQ","MKS","FERG","INVP","NCC",
]

# LSE ETPs — leveraged & inverse (GraniteShares 3×, LevShares 3×, WisdomTree).
LSE_ETPS = [
    "3LAP","3SAP","3LTS","3STS","3LNV","3SNV","3LAM","3SAM","3LGO","3SGO","3LMS","3SMS",
    "3LUB","3SUB","3LNF","3SNF","3LAZ","3SAZ","3LBA","3SBA","3LSQ","3SSQ","3LCO","3SCO",
    "3LDI","3SDI","3LUA","3SUA","3LFA","3SFA","3LXO","3SXO","3LPE","3SPE","3LTE","3STE",
    "3LUT","3SUT","3LXS","3SXS","3LBM","3SBM","3LRI","3SRI","3LBP","3SBP","3LPS","3SPS",
    "3LAB","3SAB","3LAL","3SAL","3LAS","3SAS","3LAR","3SAR","3LBT","3SBT","3LCM","3SCM",
    "3LDE","3SDE","3LDP","3SDP","3LET","3SET","3LFT","3SFT","3LGS","3SGS","3LGE","3SGE",
    "3LHI","3SHI","3LHS","3SHS","3LIQ","3SIQ","3LIS","3SIS","3LJP","3SJP","3LKO","3SKO",
    "3LMC","3SMC","3LMD","3SMD","3LMO","3SMO","3LNE","3SNE","3LNP","3SNP","3LOR","3SOR",
    "3LPR","3SPR","3LQC","3SQC","3LQQ","3SQQ","3LRV","3SRV","3LRY","3SRY","3LSL","3SSL",
    "3LSM","3SSM","3LSN","3SSN","3LSU","3SSU","3LTW","3STW","3LUN","3SUN","3LVI","3SVI",
    "3LVL","3SVL","3LZM","3SZM","BITX","IBIT","ETHU","IBIT",
    "TSLL","TSLS","NVDL","NVD","MSTU","MSTX","MSTZ","MSTP","MSDD","MSTQ",
    "NFXL","NFLY","NFLU","NFXS","UBER3L","UBER3S","PLTR3L","PLTR3S","COIN3L","COIN3S",
]

DE_DAX = [
    "SAP","SIE","ALV","AIR","DTE","BAS","BMW","DHL","MBG","VOW3","MUV2","ADS","BAYN",
    "IFX","RWE","BEI","DBK","EOAN","P911","HNR1","MRK","FRE","SHL","CBK","MTX","BNR",
    "HEI","RHM","SY1","FME","HEN3","ENR","VNA","ZAL","SRT3","1COV","LIN","CON","DB1",
    "QIA","PAH3","BOSS","GXI","LHA","LEG","DIC","FRA","PUM","S92","TKA","VOS","EVT",
]

FR_CAC = [
    "MC","AI","OR","SAN","TTE","BNP","CS","SU","ACA","EN","DG","RI","STLAP","ML",
    "SGO","DSY","VIV","AIR","CAP","CA","ENGI","ERF","GLE","KER","LR","ORA","PUB",
    "RMS","SAF","SGO","STM","TEP","UG","URW","VIE","WLN","EDF","ATO","BN","EL",
]

IT_MIB = [
    "ENI","ENEL","ISP","UCG","STLA","STM","G","BAMI","TIT","LDO","UCG","ITE",
    "FCT","BPE","A2A","SRG","CPR","AMP","BMPS","MB","NEXI","UNI","PST","HER","MONC",
]

HK_HSI = [
    "0005","0700","0939","0941","0002","0003","0011","0012","0017","0027","0066",
    "0083","0101","0175","0285","0288","0293","0316","0322","0386","0388","0669",
    "0688","0762","0823","0857","0883","0939","0960","0968","0992","1038","1044",
    "1088","1093","1109","1113","1177","1209","1211","1299","1398","1810","1876",
    "1928","1997","2018","2020","2269","2313","2318","2319","2382","2388","2628",
    "2688","3328","3690","3968","3988","6098","6862","9618","9888","9988","9999",
]

JP_TOP = [
    "7203","6758","9984","8306","6861","9432","8035","6098","4519","7974","6902",
    "4063","8058","4661","4502","7267","6501","6954","6723","4452","8411","9437",
    "4568","4503","6273","7011","8031","6503","2914","3382","4578","6367","6301",
    "9433","5108","8802","8604","7751","4543","6752","9022","8801","9101","8766",
]

SG_STI = [
    "D05","O39","U11","Z74","S63","S68","U96","C07","C31","C38U","N2IU","F34",
    "H78","G13","C52","C09","C61U","S58","Y92","U14","J36","J37","V03","M44U",
    "D01","BN4","A17U","M1Z","T82U","C6L",
]

AU_ASX = [
    "BHP","CBA","CSL","NAB","WBC","ANZ","WES","MQG","WDS","RIO","TLS","GMG","WOW",
    "COL","FMG","TCL","ALL","STO","WPL","APA","RMD","NCM","MGR","SHL","IAG","SCG",
    "SUN","QBE","ORG","BXB","AMC","TCL","REA","NST","MIN","JBH","CAR","CPU","LLC",
    "BSL","EVN","WTC","XRO","QAN","AGL","TAH","ILU","WOR","PMGOLD","S32",
]


async def qualify_batch(ib, exchange: str, currency: str, secType: str,
                        tickers, out_list, suffix: str = ""):
    from ib_insync import Stock  # type: ignore
    log.info("qualifying %d tickers on %s/%s...", len(tickers), exchange, currency)
    ok, fail = 0, 0
    # Batch in groups of 40 to avoid hammering
    for i in range(0, len(tickers), 40):
        chunk = tickers[i:i+40]
        contracts = [Stock(t + suffix, exchange, currency) for t in chunk]
        try:
            qs = await ib.qualifyContractsAsync(*contracts)
        except Exception as e:
            log.warning("batch %d-%d failed: %s", i, i+len(chunk), e)
            fail += len(chunk)
            continue
        for q in qs:
            if q.conId:
                out_list.append({
                    "symbol": q.symbol, "exchange": q.exchange or exchange,
                    "con_id": q.conId, "sec_type": secType,
                    "currency": q.currency or currency,
                    "fast": False,
                })
                ok += 1
            else:
                fail += 1
        await asyncio.sleep(0.5)
    log.info("  exchange=%s: %d qualified, %d failed", exchange, ok, fail)


async def main() -> None:
    from ib_insync import IB  # type: ignore
    ib = IB()
    await ib.connectAsync("127.0.0.1", 4002, clientId=155, readonly=True, timeout=30)
    log.info("connected; starting batch qualification")

    contracts = []

    await qualify_batch(ib, "SMART",  "USD", "STK", sorted(set(US_SP500_TOP)), contracts)
    await qualify_batch(ib, "LSE",    "GBP", "STK", sorted(set(UK_FTSE)), contracts)
    await qualify_batch(ib, "LSEETF", "GBP", "STK", sorted(set(LSE_ETPS)), contracts)
    await qualify_batch(ib, "IBIS",   "EUR", "STK", sorted(set(DE_DAX)), contracts)
    await qualify_batch(ib, "SBF",    "EUR", "STK", sorted(set(FR_CAC)), contracts)
    await qualify_batch(ib, "BVME",   "EUR", "STK", sorted(set(IT_MIB)), contracts)
    await qualify_batch(ib, "SEHK",   "HKD", "STK", sorted(set(HK_HSI)), contracts)
    await qualify_batch(ib, "TSEJ",   "JPY", "STK", sorted(set(JP_TOP)), contracts)
    await qualify_batch(ib, "SGX",    "SGD", "STK", sorted(set(SG_STI)), contracts)
    await qualify_batch(ib, "ASX",    "AUD", "STK", sorted(set(AU_ASX)), contracts)

    ib.disconnect()

    # Dedupe (symbol, exchange)
    seen = set()
    unique = []
    for c in contracts:
        k = (c["symbol"], c["exchange"])
        if k in seen:
            continue
        seen.add(k)
        unique.append(c)

    # Mark top 20 US megacaps as fast_path
    fast_symbols = {"AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD",
                    "NFLX","LLY","JPM","V","XOM","WMT","UNH","JNJ","HD","ORCL","COST"}
    for c in unique:
        if c["symbol"] in fast_symbols and c["exchange"] in ("SMART","NASDAQ","NYSE"):
            c["fast"] = True

    # Write
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# AEGIS V5 full contract universe (auto-generated)\n"]
    for c in unique:
        lines += [
            "[[contracts]]",
            f'symbol = "{c["symbol"]}"',
            f'con_id = {c["con_id"]}',
            f'exchange = "{c["exchange"]}"',
            f'sec_type = "{c["sec_type"]}"',
            f'currency = "{c["currency"]}"',
            f'fast = {"true" if c["fast"] else "false"}',
            "",
        ]
    OUT_PATH.write_text("\n".join(lines))
    log.info("wrote %d contracts to %s", len(unique), OUT_PATH)
    # Summary
    from collections import Counter
    by_ex = Counter(c["exchange"] for c in unique)
    for ex, n in by_ex.most_common():
        log.info("  %s: %d", ex, n)


if __name__ == "__main__":
    asyncio.run(main())
