#!/usr/bin/env python3
"""Pull the complete LSE universe from IBKR.

Covers:
- FTSE 100, 250, SmallCap, All-Share (main market SETS)
- SETSqx (mid/small caps)
- AIM 100 + AIM All-Share
- Investment trusts
- ETFs + ETPs (LSEETF)
- Funds listed on LSE

Uses reqContractDetails with symbol wildcards via reqMatchingSymbols.
"""
import asyncio
import sys
from pathlib import Path
from ib_insync import IB, Stock, util

# Known LSE index constituents (seed list)
FTSE_ALL_SHARE_SEEDS = """
III ABF ADM AFN AGR AHT ALW AML ANTO APH ASHM ASC ASHM ASY AUTO AVV AVON AV
BA BAB BAG BARC BATS BBOX BBY BDEV BEG BEZ BGCG BGEO BHP BKG BLND BME BNZL
BP BRBY BT.A BVXP BWY CCH CCL CCR CEY CINE CLG CLLN CNA CNE COA CPG CPI CRDA
CRH CRST CTEC CWK DARK DCC DGE DLAR DLG DNLM DOM DPH DRX DTG EDIN EDV EMG ENT
EOG ESP EVR EVTL EXPN FCSS FDM FERG FGP FGT FICE FLTR FOUR FRAS FRES FSFL FSV
FXPO GAW GLE GLEN GOG GRG GSF GSK GSS HALMA HAS HGG HICL HIK HLMA HMSO HPI
HOME HSBA HSV HWDN IAG IBST ICG IGG IMB IMI INCH INDV INF ING INVP ITRK ITV
JE. JLT JMAT JMG JUP KAZ KGF KIE KNOS LAD LAND LGEN LIO LLOY LMI LMP LOOK LRD
LSE LSL MAB1 MCS MGAM MGGT MGNS MKS MMC MNDI MNG MRO MRW MSLH MTO MXCT NCYF
NEX NG. NICL NMC NRR NXT OCDO OMU OXB PAF PAG PAY PDG PETS PFC PFG PHNX PIN
PLUS PMO PNN POLY PRU PSH PSN PSON PURP PZC QQ. QRT RAT RB. RBS RCP RDW REL
RENW RICA RIO RMG RMV RNK ROR RR. RSA RSW RTN RWA SAB SAFE SBRY SCIN SCT SDR
SDY SEGRO SGC SGP SHB SHI SIG SKG SL. SMDS SMIN SMS SMT SMWH SN. SOPH SPT SRP
SSE SSPG STAN STOB SVI SVT SXS SXX TALK TATE TEP TEM TES TMPL TRAIN TRIG TSCO
TUI TW. UKCM ULE ULVR UTG UU. VCT VED VM. VP. VSM VTY VOD WIL WIZZ WG. WHR
WIZZ WLW WMH WOS WPP WTB WTG XP. YELL 4IMP 888 ABDN ABF ADM AGFX AGM AHT
ALG ALW APAX APX ASL ASTO ATG ATYM AVCT AVG BAN BBH BBM BCG BDRS BETS BGO BKG
BLG BNKR BOOM BOOT BOY BRBY BRK BRW BSD BSIF BUR BUT BWO BWY BYG CAKE CALB
CAP CBP CEC CFY CGT CKT CLH CLI CMS CNCT CNIC COM CRL CRP CRS CRW CRX CSE CSP
CTY CUSN CVSG CWR CYAN DAIF DAV DEC DEMG DHL DIA DNA DPLM DRV DWS DX DVO ECO
EDB EEIT EFM EMAN EMIS EMV ENOG EOGX ESNT EVE EWG EZJ FAC FBH FCH FDBK FEET
FIN FINE FLNG FRES FSJ FUTR FVT FWRD GATE GBG GDWN GFRD GLE GLIF GLO GPC GRG
GSS GYM H20P HARL HAYT HDIV HEIQ HEMO HFD HFEL HGT HILS HOTC HYNF HZM IDEA
IGE IMB IMO INCH INF INL INS IOF IPEL IPF IPO IPT IQE IRG ISO ITE ITM ITV
IVI JADE JCG JDG JII JMAT JMG JNG JTC KETL KIE KOV LAND LBOW LIO LIT LLOY LMI
LMP LOM LOOP LSL LTHM LUCE LXI M&S MAB MANX MARS MCKS MEGP MFX MFX MGAM MGR
MHN MIDW MIX MKS MMX MMY MNP MOD MOS MPE MRK MRO MTE MTR MUT MYI NAR NBI NCC
NCYF NETW NEX NFC NGG NIOX NWF OCDO ODX OKYO ONS OPP ORX OSB OTG OXB OXIG OXP
PAF PAG PAN PAY PBR PCF PCTN PEBB PEEL PEN PENN PEO PES PETS PEY PFC PFD PGH
PHP PIN PLAY PLP PLUS PNN POAK POG POLX POR PPH PREM PRSR PSDL PTCM PTSG PURP
PZC QED QTP QTX QUIZ QZG R7 RAT RBG RBS RBW RCI RDL REC REDS REDT REL RENE RENX
RFI RFX RGL RHIM RKH RLE RM RMII RMS RMV RNK ROCK ROL ROO RPT RR. RSA RSE RST
RSW RTNP RUA RUS RWS SAAS SBID SCT SCT SDR SDRC SDY SEC SEE SFP SGM SGE SGP SGRO
SHB SHED SHI SHOE SHRS SIG SIPP SIS SITC SIV SLP SMDS SMF SMS SMWH SND SNG SNN
SNR SOHO SOM SONG SOS SPI SPR SPSY STOCK STVG SUPR SVS SWG SXS SYM SYME TAST
TAVR TBLD TCA TEP TER TFIF TFG TGR THHH THRG TIFS TINY TLOU TLY TMG TMIP TMPL
TND TPFG TPG TPOP TRAC TRD TRIG TRMR TRN TRR TRX TSTL TSWT TTG TUN TV TWRS UANC
UEN UFO UKSM UKW ULE UML UOG UPGS URA VANL VCT VCW VEC VED VFC VINO VLC VLX
VMD VNET VOS VP. VSL WATR WCH WEIR WG. WHF WIN WINE WIX WIZZ WJG WKN WKP WRN
WTAN WTG WTL WTY YEW YU ZOO ZPG ZYT
""".split()

LSE_ETP_PREFIXES = [
    "3L", "3S", "LVE", "SVE", "SDBR", "STSL", "SUKL",
    "DIVE", "SDIV", "IUKD", "VUKE", "ISF", "CUKX", "LCUK",
    "IUSA", "VUSA", "SPX5", "CSPX", "CSP1", "IWDA", "VWRL",
    "EMIM", "VFEM", "SEMA", "IEEM", "AGED", "ICOM", "SGBL",
    "3UKL", "3USL", "3EUL", "3JPL",
]

async def fetch_lse_universe() -> list[dict]:
    ib = IB()
    import random
    cid = random.randint(500, 900)
    await ib.connectAsync("127.0.0.1", 4002, clientId=cid, timeout=30)
    print(f"Connected to Gateway", flush=True)

    # Seed list from known indices
    candidates: set[str] = set()
    candidates.update(s.strip() for s in FTSE_ALL_SHARE_SEEDS if s.strip())

    # Use reqMatchingSymbols to discover more (max ~16 results per query)
    print(f"Seed universe: {len(candidates)} tickers", flush=True)

    # Expand via wildcard search
    for prefix in list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        try:
            matches = await ib.reqMatchingSymbolsAsync(prefix)
            for m in matches or []:
                c = m.contract
                if c.secType == "STK" and c.primaryExchange in ("LSE", "LSEETF") and c.currency == "GBP":
                    candidates.add(c.symbol)
        except Exception:
            pass

    # ETP prefixes
    for pfx in LSE_ETP_PREFIXES:
        try:
            matches = await ib.reqMatchingSymbolsAsync(pfx)
            for m in matches or []:
                c = m.contract
                if c.secType == "STK" and "LSE" in (c.primaryExchange or ""):
                    candidates.add(c.symbol)
        except Exception:
            pass

    print(f"Candidates after expansion: {len(candidates)}", flush=True)

    # Qualify each candidate
    qualified = []
    batch = []
    for symbol in sorted(candidates):
        batch.append(Stock(symbol, "LSE", "GBP"))
        if len(batch) >= 50:
            try:
                q = await ib.qualifyContractsAsync(*batch)
                for c in q:
                    if c.conId:
                        qualified.append({
                            "symbol": c.symbol,
                            "exchange": c.primaryExchange or "LSE",
                            "currency": c.currency or "GBP",
                            "con_id": c.conId,
                        })
            except Exception as e:
                print(f"batch error: {e}", flush=True)
            batch = []
    if batch:
        try:
            q = await ib.qualifyContractsAsync(*batch)
            for c in q:
                if c.conId:
                    qualified.append({
                        "symbol": c.symbol,
                        "exchange": c.primaryExchange or "LSE",
                        "currency": c.currency or "GBP",
                        "con_id": c.conId,
                    })
        except Exception:
            pass

    print(f"Qualified: {len(qualified)} LSE contracts", flush=True)
    ib.disconnect()
    return qualified


def main():
    util.patchAsyncio()
    results = asyncio.run(fetch_lse_universe())

    # Write to separate file for merge
    out = Path("/Users/rr/aegis-v5/config/lse_full.toml")
    lines = []
    for c in results:
        lines.append("[[contracts]]")
        lines.append(f'symbol = "{c["symbol"]}"')
        lines.append(f'exchange = "{c["exchange"]}"')
        lines.append(f'currency = "{c["currency"]}"')
        lines.append(f'con_id = {c["con_id"]}')
        lines.append("")
    out.write_text("\n".join(lines))
    print(f"Wrote {out} — {len(results)} contracts", flush=True)


if __name__ == "__main__":
    main()
