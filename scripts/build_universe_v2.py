"""V5 universe builder v2 — merges curated index lists with existing contracts.

Goal: grow contracts.toml from ~1000 to 2500+ tickers across 10 exchanges
without IBKR qualification (runtime qualifier fills in con_ids).

Honest output: stubs with con_id=0 for unqualified symbols. The scanner/
runtime qualifier runs a lookup later when Gateway is healthy.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/Users/rr/aegis-v5")
CONTRACTS = ROOT / "config/contracts.toml"
UNIVERSE_DOC = ROOT / "docs/V5_UNIVERSE_V2.md"


SP500 = (
    "A AAL AAP AAPL ABBV ABC ABMD ABT ACGL ACN ADBE ADI ADM ADP ADSK AEE AEP AES AFL AIG "
    "AIV AIZ AJG AKAM ALB ALGN ALK ALL ALLE AMAT AMCR AMD AME AMGN AMP AMT AMZN ANET ANSS ANTM "
    "AON AOS APA APD APH APTV ARE ATO ATVI AVB AVGO AVY AWK AXP AZO BA BAC BALL BAX BBWI "
    "BBY BDX BEN BFB BIIB BIO BK BKNG BKR BLK BLL BMY BR BRKB BRO BSX BWA BXP C CAG "
    "CAH CARR CAT CB CBOE CBRE CCI CCL CDAY CDNS CDW CE CEG CERN CF CFG CHD CHRW CHTR CI "
    "CINF CL CLX CMA CMCSA CME CMG CMI CMS CNC CNP COF COG COO COP COST CPB CPRT CRL CRM "
    "CSCO CSX CTAS CTLT CTRA CTSH CTVA CTXS CVS CVX CZR D DAL DD DE DFS DG DGX DHI DHR "
    "DIS DISH DLR DLTR DOV DOW DPZ DRI DTE DUK DVA DVN DXC DXCM EA EBAY ECL "
    "ED EFX EIX EL EMN EMR ENPH EOG EPAM EQIX EQR ES ESS ETN ETR ETSY EVRG EW EXC EXPD "
    "EXPE EXR F FANG FAST FBHS FCX FDX FE FFIV FIS FISV FITB FLT FMC FOX FOXA FRC FRT "
    "FTNT FTV GD GE GILD GIS GL GLW GM GNRC GOOG GOOGL GPC GPN GPS GRMN GS GWW HAL HAS "
    "HBAN HBI HCA HD HES HIG HII HLT HOLX HON HPE HPQ HRL HSIC HST HSY HUM HWM IBM ICE "
    "IDXX IEX IFF ILMN INCY INFO INTC INTU IP IPG IPGP IQV IR IRM ISRG IT ITW IVZ J JBHT "
    "JCI JKHY JNJ JNPR JPM K KEY KEYS KHC KIM KLAC KMB KMI KMX KO KR L LDOS LEG LEN "
    "LH LHX LIN LKQ LLY LMT LNC LNT LOW LRCX LUMN LUV LVS LW LYB LYV MA MAA MAR MAS "
    "MCD MCHP MCK MCO MDLZ MDT MET MGM MHK MKC MKTX MLM MMC MMM MNST MO MOH MOS MPC MPWR "
    "MRK MRNA MRO MS MSCI MSFT MSI MTB MTCH MTD MU NCLH NDAQ NDSN NEE NEM NFLX NI NKE NLOK "
    "NOC NOV NOW NRG NSC NTAP NTRS NUE NVDA NVR NWL NWS NWSA NXPI O ODFL OGN OKE OMC "
    "ORCL ORLY OTIS OXY PAYC PAYX PCAR PEAK PEG PENN PEP PFE PFG PG PGR PH PHM PKG PKI "
    "PLD PM PNC PNR PNW POOL PPG PPL PRU PSA PSX PTC PVH PWR PXD PYPL QCOM QRVO RCL RE "
    "REG REGN RF RHI RJF RL RMD ROK ROL ROP ROST RSG RTX SBAC SBNY SBUX SCHW SEDG SEE SHW "
    "SJM SLB SNA SNPS SO SPG SPGI SRE STE STT STX STZ SWK SWKS SYF SYK SYY T TAP "
    "TDG TDY TECH TEL TER TFC TFX TGT TJX TMO TMUS TPR TRMB TROW TRV TSCO TSLA TSN TT TTWO "
    "TXN TXT TYL UA UAA UAL UDR UHS ULTA UNH UNM UNP UPS URI USB V VFC VLO "
    "VMC VNO VRSK VRSN VRTX VTR VTRS VZ WAB WAT WBA WDC WEC WELL WFC WHR WM WMB WMT WRB "
    "WRK WST WTW WU WY WYNN XEL XOM XRAY XYL YUM ZBH ZBRA ZION ZTS GEHC KVUE PLTR "
    "SNOW CRWD DDOG NET MDB TEAM ZM PANW ABNB UBER LYFT DASH RBLX COIN HOOD SOFI"
).split()

RUSSELL_MID = (
    "SQ SHOP SPOT TWLO PINS ROKU DKNG MRVL WDAY OKTA ZS ESTC NOW PATH S DOCU UPWK CHWY "
    "PRU PGR ALL CB AIG MET HIG LNC UNM AFL TRV AJG GL WTW MMC AON BRO RE CINF WRB "
    "AAP ACN ADI ADM ADP AKAM AMAT AMD AMGN AMP AMT AON APA APTV ATO ATVI AVB AVGO AVY "
    "AZO BAX BBY BDX BEN BIIB BIO BK BKNG BKR BLK BMY BR BSX BWA BXP C CAG CAH CARR "
    "AKRO AMAT AMBA APPS ARKF ARKG ARKK ARKQ ARKW AVDL AXON BIRD BKSY BJ BKCH BLOK CPNG "
    "DBX DKNG FVRR HOOD IPOE ISPC MARA PLBY PSNL QCOM RMBL SIGA SNAP SPCE U UIPATH VRM "
    "W WIX WKME ZETA ZEV AFRM AI AMPL ASAN BILL BL BRZE CFLT CHPT CZOO DT ENFN ESTC FROG "
    "GDS GOCO GTLB HCP HUBS IAC INTU IRTC JAMF KROS LICY LOGI MIDU MIME MNDY MRVL NCNO NU "
    "PAAS PATH PEGA PGNY PL PLTR PRCH QS QTRX RACE RDDT RMO RNG RUN SAVE SAVA SE "
    "SHLS SNDL SPT SRPT TDC TOST TWKS TWOU U UDMY UPST VEEV VTEX WKME WOOF WRBY XP ZH ZI"
).split()

US_ETFS = (
    "SPY VOO IVV VTI QQQ IWM DIA SCHB SPLG VXF "
    "XLF XLK XLE XLV XLI XLY XLP XLU XLB XLRE XLC XBI SMH SOXX KRE KBE IBB XME XRT XHB "
    "EFA EEM VWO IEFA IEMG FXI EWJ EWZ EWU EWG EWY INDA EWC EWA EWH EWT EZU VEA "
    "TLT IEF SHY AGG BND LQD HYG JNK MUB TIP EMB BNDX "
    "SH PSQ DOG SDS QID DXD SPXU SQQQ FAZ SOXS TZA SRTY SPXS "
    "SSO QLD DDM UPRO TQQQ UDOW FAS SOXL TNA URTY SPXL TMF "
    "IBIT FBTC BITX BITI ETHE GBTC ETHA ARKB "
    "GLD SLV USO UNG DBA DBC UUP FXE FXY "
    "VXX UVXY SVXY VIXY "
    "MTUM QUAL USMV SPLV VLUE IWD IWF IWN IWO IWP IWR IWS "
    "VYM SCHD DVY SDY VIG NOBL HDV"
).split()

NASDAQ_EXTRA = (
    "ADSK AEP ALGN AMAT AMGN ANSS ASML ATVI AVGO BIDU BIIB BKNG CDNS CDW CHKP CHTR CMCSA COST CPRT "
    "CRWD CSCO CSX CTAS CTSH DDOG DLTR DOCU DXCM EA EBAY EXC FAST FISV FTNT GILD GOOG GOOGL HAS HON "
    "IDXX ILMN INCY INTC INTU ISRG JD KDP KHC KLAC LCID LRCX LULU MAR MCHP MDB MDLZ MELI META MNST "
    "MRNA MRVL MSFT MTCH MU NFLX NTES NVDA NXPI OKTA ORLY PANW PAYX PCAR PDD PEP PTON PYPL QCOM REGN "
    "ROST SBUX SGEN SIRI SNPS SPLK SWKS TCOM TEAM TMUS TSLA TXN VRSK VRSN VRTX WBA WDAY XEL ZM ZS"
).split()

FTSE100 = (
    "AAL ABDN ADM AHT ANTO AV AZN BARC BATS BDEV BEZ BKG BLND BME BNZL BP BRBY BT.A CCH "
    "CNA CPG CRDA CRH CTEC DCC DGE DPH EDV ENT EXPN EZJ FERG FLTR FRES GLEN GSK HIK HL HLMA "
    "HLN HSBA HWDN IAG ICP IHG III IMB INF ITRK ITV JD KGF LAND LGEN LLOY LSEG MKS MNDI MNG "
    "MRO NG NWG NXT OCDO PHNX PRU PSH PSN PSON RIO RKT RMV RR RS1 RTO SBRY SDR SGE SGRO "
    "SHEL SKG SMDS SMIN SMT SN SPX SSE STAN STJ SVT TSCO TW ULVR UU VOD WEIR WPP WTB ZIG"
).split()

FTSE250 = (
    "ABF AO AUTO BAB BAG BEZ BGEO BKG BLND BMY BNKR BNZL BRBY BREE BRSC BRWM BRW BVIC BVS BWY "
    "CCL CEY CHG CINE CKN CLDN CLIG CMCX CNA CNCT CNE COA CPI CRST CSN CTY CWK DCG DLG DLN "
    "DOM DPH DPLM DRX DSCV DSV ECM EDIN EGU ELM ELCO EMIS ENT ESNT ESP ETO EVR FEVR FGP "
    "FLG FLR FNTL FOUR FOXT FPE FQM FRAS FUTR GAW GCL GFRD GFS GHG GLE GNC GNS GOAL GPOR GRG "
    "GRI GTC HARL HAT HEIQ HFD HFEL HGM HICL HILS HL HMSO HOC HOME HSL HSV HSX HTG "
    "HWG HYUK ICG IDS IGC IHG INCH INPP INSP IPO IRV ITM IWG JAI JAT JDW "
    "JEO JII JLG JMAT JSE JUP KAZ KGH KMK KLR KNOS KWS LIO LMP LOOK LRE LSE "
    "LTG LTI LWB LXI MAN MARS MCLS MCS MDC MFX MGR MGAM MGGT MLC MMIT MONY MRC "
    "MSI MSLH MTO MTRO MXP NBI NEX NICL NMC NMH NRR NTG NWBD NXG NXR OCN "
    "OCI OGN OMU OPHR OPTO OXIG PAG PAGE PAY PCT PCTN PDG PFC PHAG PHP PIN PLUS PNN POG POLY "
    "POM PRSM PRTC PTEC PTM PXEN PZC REL REX RNO ROR RPC RPS RTN SAFE SAR SBRE "
    "SCIN SDRY SEC SGC SHAW SHED SHI SIM SLA SLE SLI SLN SMWH SNR SOH "
    "SOLI SPI SPRE SREI SSPG STCK STM STOB SVS SXS SYR TATE TCAP TED TEMP TEP "
    "TET THRG TIFS TLPR TMPL TNT TPFG TPK TRCS TRI TRMR TRYN TUI TYMN UDG UKCM UKW ULE "
    "UTG UTV VCT VEC VED VIC VMUK VP VSVS VTU WG WIZZ WKP WMH WOSG WTAN XPP YELL"
).split()

LSEETF = (
    # 3x long single-stock
    "3LAP 3LAZ 3LBA 3LBC 3LBP 3LDE 3LDO 3LES 3LFP 3LGO 3LGS 3LIP 3LLL 3LMP 3LNP 3LRI 3LSR 3LTS "
    "3LAA 3LAL 3LAM 3LBB 3LBX 3LCA 3LCL 3LCS 3LDI 3LDR 3LEB 3LEN 3LFB 3LGN "
    "3LHO 3LIB 3LJP 3LKL 3LNK 3LNV 3LOR 3LPF 3LPI 3LPL 3LPY 3LQL 3LRL 3LSA 3LSH 3LSL 3LSQ 3LSW "
    "3LUR 3LVS 3LWB 3LXI 3LZM "
    # 3x short single-stock
    "3SAP 3SAZ 3SBA 3SBC 3SBP 3SDE 3SDO 3SES 3SFP 3SGO 3SGS 3SIP 3SLL 3SMP 3SNP 3SRI 3SSR 3STS "
    "3SAA 3SAL 3SAM 3SBB 3SBX 3SCA 3SCL 3SCS 3SDI 3SDR 3SEB 3SEN 3SFB 3SGN 3SHO 3SIB 3SJP 3SKL "
    "3SNK 3SNV 3SOR 3SPF 3SPI 3SPL 3SPY 3SQL 3SRL 3SSA 3SSH 3SSL 3SSQ 3SSW 3SUR 3SVS 3SWB 3SXI 3SZM "
    # Index leveraged
    "3UKL 3UKS 3USL 3USS 3EUL 3EUS 3JPL 3JPS 5USL 5USS 5EUL 5EUS "
    "SSHY SRLV SOIL SPMT GOLD SILV DSWT DGBP DEUR DJPY SDGB "
    "SBUL SGBS SGLS SSLV SSLS SUOI SGAS SCOP SNKL SALU "
    "SVIL SVIX"
).split()

DAX_FULL = (
    "1COV ADS AIR ALV BAS BAYN BEI BMW BNR BOSS CBK CON DB1 DBK DHL DTE DTG EOAN FME FRE "
    "HEI HEN3 HFG HNR1 IFX LHA MBG MRK MTX MUV2 PAH3 PUM QIA RHM RWE SAP SAR SHL SIE SY1 "
    "VNA VOW3 ZAL "
    "AIXA ARL ASK AT1 BC8 BDT BPE5 BYW CEC COK DBAN DEQ DEZ DIC DIS DRW3 "
    "DUE DWS DWNI EBK EKT EVD EVO EVK EVT FIE FIEL FMC FNTN FPE FPH FRA FTK G1A "
    "GBF GED GFJ GFT GIL GLJ GMM GSJ GVC GWI1 HAB HAN HBH HDD HEN HIB HOT HYQ "
    "ITK JEN JUN3 KCO KGX KIN KO1 KRN KSB3 KSL KWS LEG LIN LOGN LXS M5Z MAN MBB MDG1 "
    "MEO MLP MOR MTL NA9 NDA NEM NGE NOEJ NWO O2D OSR PBB PFV PMOX PNE3 PSAN PSM "
    "RAA RAI RHK RKET ROV RRTL RSW S92 SAX SDF SFQ SGL SIX2 SKB SNH SOW SPA "
    "SPR SRT3 STM STO3 SWI SYN SZG SZU TEG TFE THA TKA TLG TLX TMV TNE5 TTK TTR1 TUI UN01 "
    "UTDI VAR1 VBK VIB3 VLK VOS VOW VRL WAC WAF WAH WCH WDI WEW WSU XTPG ZIL2"
).split()

ASX_FULL = (
    "A2M ABC ACL AGL AIA ALD ALL ALQ ALX AMC AMI AMP ANN ANZ APA APE APX ARB ASX "
    "AUB AVH AWC AZJ BAP BBN BEN BGA BHP BKL BKW BLD BOQ BPT BRG BSL BUB BVS BWP BXB "
    "CAR CBA CCL CCP CCX CDA CDD CDV CEN CGC CGF CHC CHN CIA CIM CIP CLW CMM CNI CNU "
    "COF COH COL CPU CQR CRN CSL CTD CUV CWY CXO DEG DHG DMP DOW DRR EBO EDV EHL ELD "
    "EML EOS EVN EVT FBU FCL FLT FMG FPH GEM GMG GNC GOR GOZ GPT GUD GWA HLO HLS HMC "
    "HUB HUM HVN IAG IEL IFL IFT IGO ILU IMD INA ING INR IPD IPL IRE JBH JHG JHX JIN "
    "KAR KGN KLS LFG LLC LNK LOV LYC MAD MEI MFG MGR MIN MMS MND MP1 MPL MQG MTS NAB "
    "NAN NCM NEA NEC NHC NHF NIC NMT NST NSR NUF NWL NWS NXT OBL OGC OPY OVH ORA ORG "
    "ORI OZL PBH PDL PDN PGH PLS PME PMV PNI PNV PPT PRU PSI PXA QAN QBE QUB RBL "
    "RDC REA REG RHC RIO RMD RMS RRL RSG RWC S32 SBM SCG SCP SDF SEK SFR SGF SGM SGP "
    "SGR SHL SIG SIQ SKC SKI SKO SKT SLC SLR SM1 SMP SOL SOR SPK SQ2 STO SUL SUN SVW SWM "
    "SYR TAH TCL TGR TLC TLS TLX TNE TNK TPG TPW TRS TWE UMG URW UWL VEA VOC VUK WAF "
    "WBC WDS WEB WES WGX WHC WOR WOW WSA WSP WTC WYN XRO"
).split()

NIKKEI = (
    "1332 1333 1801 1802 1803 1808 1812 1925 1928 1963 2002 2269 2282 2413 2432 2501 2502 2503 2531 2768 "
    "2801 2802 2871 2914 3086 3099 3101 3103 3105 3289 3382 3401 3402 3405 3407 3436 3861 3863 4004 4005 "
    "4021 4042 4043 4061 4063 4183 4188 4208 4324 4452 4502 4503 4506 4507 4519 4523 4543 4568 4578 4631 "
    "4661 4689 4704 4751 4755 4901 4902 4911 5019 5020 5101 5108 5201 5202 5214 5232 5233 5301 5332 5333 "
    "5401 5406 5411 5541 5631 5703 5706 5707 5711 5713 5714 5801 5802 5803 6098 6103 6113 6178 6273 6301 "
    "6302 6305 6326 6361 6367 6471 6472 6473 6479 6501 6502 6503 6504 6506 6508 6594 6645 6674 6701 6702 "
    "6703 6724 6752 6758 6762 6770 6841 6861 6902 6952 6954 6971 6976 6981 6988 7003 7004 7011 7012 7013 "
    "7186 7201 7202 7203 7205 7211 7261 7267 7269 7270 7272 7309 7731 7732 7733 7735 7741 7751 7752 7762 "
    "7832 7911 7912 7951 7974 8001 8002 8015 8031 8035 8053 8056 8058 8233 8252 8253 8267 8306 8308 8309 "
    "8316 8331 8354 8355 8411 8413 8473 8591 8593 8601 8604 8628 8630 8697 8725 8750 8766 8795 8801 8802 "
    "8804 8830 9001 9005 9007 9008 9009 9020 9021 9022 9062 9064 9101 9104 9107 9147 9202 9301 9412 9432 "
    "9433 9434 9437 9501 9502 9503 9531 9532 9602 9613 9735 9766 9831 9832 9843 9983 9984"
).split()

CAC_FULL = (
    "AC ACA AI AIR ATO BN BNP CA CAP CS DG DSY EL EN ENGI ERF FGR FP FR GLE "
    "HO KER LI LR MC ML MT ORA OR POM PUB RI RMS RNO SAF SAN SGO STLA STM SU "
    "SW TEP TTE UG URW VIE VIV VK WLN "
    "AF AKA AKE ALM ALO ALT APAM ATE AUB AURE BB BEN BIC BIM BOL BON BVI CARM CGG CO "
    "COFA COV CRAP DEC DIM EDEN EDF EFI EI ELO EO EPA EQS ETEX "
    "EURF EXE FDE FDJ FHPP FII FNAC FORE FOUG FRAG FRAT GBT GET GFC GNRO GTT HCG HEMP IAM "
    "ICAD ILD IMDA IML INF INGE INTER IPS IPSOS JCQ KAL KOF LSS LTA LYX MAU MCFR MCPHY MDM MEDI "
    "MERC MET MF MG MIRO MLVIO MOO NANO NEO NEX NEXI NK NNE OK ORP OVH PAR PARRO "
    "PERN PILI PLX PMCH PRI PRISM PROAC PSAT PVL QDT RCO REC REG RENE REX RF ROC ROTH "
    "RUI RXL S30 SAFT SCI SECH SEV SFBS SII SLG SMC SOI SOP SPIE STT SUEZ SYNE "
    "TE TF TFF TKO TMSC TNP TR TRI UBI VCT VETO VIL VOL VP"
).split()

HKEX = (
    "0001 0002 0003 0005 0006 0011 0012 0016 0017 0027 0066 0083 0101 0135 0144 0151 0175 0200 0267 0288 "
    "0291 0293 0316 0322 0386 0388 0669 0688 0700 0762 0823 0836 0857 0883 0939 0941 0968 0992 1038 1044 "
    "1088 1093 1109 1113 1177 1209 1211 1299 1398 1810 1876 1928 1997 2007 2015 2018 2020 2269 2313 2318 "
    "2319 2328 2331 2333 2382 2388 2628 2688 2689 2883 2899 3690 3968 3988 6030 6098 6862 9618 9633 9888 "
    "9988 9999 0019 0023 0041 0116 0119 0120 0125 0136 0137 0166 0177 "
    "0207 0215 0220 0224 0241 0268 0302 0303 0336 0345 0347 0358 0371 0384 "
    "0392 0450 0462 0522 0551 0553 0586 0593 0606 0607 0656 0659 0675 0696 0697 0728 0753 "
    "0780 0788 0799 0806 0808 0811 0813 0817 0839 0853 0855 0867 0868 0874 0881 0914 "
    "0916 0921 0966 0981 1060 1072 1083 1099 1128 1169 "
    "1179 1186 1193 1199 1288 1308 1310 1313 1336 1338 1339 1359 1378 1381 1385"
).split()

STI_FULL = (
    "A17U BN4 C07 C09 C38U C52 C6L CC3 D01 D05 F34 G07 G13 H78 J36 J37 M44U ME8U N2IU O39 "
    "S58 S63 S68 T39 U11 U14 U96 V03 Y92 Z74 BS6 C31 C61U CJLU E5H EMI F25 K71U NS8U "
    "OV8 S08 S41 S51 BUOU BSL CRPU HMN NC2 T82U Q0F 5CP 5UX 9CI"
).split()

FTSE_MIB_FULL = (
    "A2A AMP AZM BAMI BMED BMPS BNIFU BPE BZU CPR DIA ENEL ENI ERG EXO FBK FCT G HER IG "
    "INW ISP IVG LDO MB MONC MT NEXI PIRC PRY PST REC RACE SFL SPM SRG STM TEN TERNA TIT "
    "TPRO UCG UNI ZV"
).split()


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def build_universe() -> dict[str, list[tuple[str, str]]]:
    us = dedupe(SP500 + RUSSELL_MID + US_ETFS + NASDAQ_EXTRA)
    return {
        "SMART": [(s, "USD") for s in us],
        "LSE": [(s, "GBP") for s in dedupe(FTSE100 + FTSE250)],
        "LSEETF": [(s, "GBP") for s in dedupe(LSEETF)],
        "IBIS": [(s, "EUR") for s in dedupe(DAX_FULL)],
        "ASX": [(s, "AUD") for s in dedupe(ASX_FULL)],
        "TSEJ": [(s, "JPY") for s in dedupe(NIKKEI)],
        "SBF": [(s, "EUR") for s in dedupe(CAC_FULL)],
        "SEHK": [(s, "HKD") for s in dedupe(HKEX)],
        "SGX": [(s, "SGD") for s in dedupe(STI_FULL)],
        "BVME": [(s, "EUR") for s in dedupe(FTSE_MIB_FULL)],
    }


def parse_existing() -> dict:
    if not CONTRACTS.exists():
        return {}
    text = CONTRACTS.read_text()
    blocks = re.split(r"\[\[contracts\]\]", text)[1:]
    out = {}
    for b in blocks:
        d = {}
        for line in b.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'(\w+)\s*=\s*"?([^"#]+?)"?\s*(#.*)?$', line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip()
            if key == "con_id":
                try:
                    val = int(val)
                except ValueError:
                    continue
            elif val in ("true", "false"):
                val = (val == "true")
            d[key] = val
        if "symbol" in d and "exchange" in d:
            out[(d["symbol"], d["exchange"])] = d
    return out


def write_contracts(entries: list[dict]) -> None:
    tmp = CONTRACTS.with_suffix(".toml.new")
    lines = [
        "# V5 contracts universe (v2 build) — curated index constituents",
        f"# total entries: {len(entries)}",
        "# con_id=0 means pending runtime qualification",
        "",
    ]
    for e in entries:
        lines.append("[[contracts]]")
        for k in ["symbol", "exchange", "currency", "sec_type", "con_id", "fast_path", "is_etp"]:
            if k not in e:
                continue
            v = e[k]
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            else:
                lines.append(f'{k} = "{v}"')
        # preserve any extra fields
        for k, v in e.items():
            if k in ("symbol", "exchange", "currency", "sec_type", "con_id", "fast_path", "is_etp"):
                continue
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            else:
                lines.append(f'{k} = "{v}"')
        lines.append("")
    tmp.write_text("\n".join(lines))
    tmp.replace(CONTRACTS)


def write_universe_doc(entries: list[dict]) -> None:
    by_ex = {}
    for e in entries:
        by_ex.setdefault(e["exchange"], []).append(e["symbol"])
    lines = [
        "# V5 Universe — Generated by build_universe_v2.py",
        "",
        f"**Total**: {len(entries)} contracts across {len(by_ex)} exchanges",
        "",
    ]
    for exch in sorted(by_ex, key=lambda x: -len(by_ex[x])):
        syms = sorted(set(by_ex[exch]))
        lines.append(f"## {exch} — {len(syms)} symbols\n")
        lines.append(", ".join(syms))
        lines.append("")
    UNIVERSE_DOC.write_text("\n".join(lines))


def main():
    existing = parse_existing()
    print(f"Existing contracts.toml: {len(existing)}")

    target = build_universe()
    total_target = sum(len(v) for v in target.values())
    print(f"Target universe: {total_target}")
    for e, l in sorted(target.items(), key=lambda x: -len(x[1])):
        print(f"  {e}: {len(l)}")

    entries = []
    kept = 0
    new_stubs = 0

    # Union: keep existing + add new stubs
    for exchange, symbols in target.items():
        for symbol, currency in symbols:
            key = (symbol, exchange)
            if key in existing:
                entries.append(existing[key])
                kept += 1
            else:
                is_etp = (
                    exchange == "LSEETF"
                    or (symbol.startswith("3L") and len(symbol) <= 5)
                    or (symbol.startswith("3S") and len(symbol) <= 5)
                    or symbol in {"TQQQ", "SQQQ", "SOXL", "SOXS", "SPXL", "SPXS", "UPRO", "SDS", "SSO", "QLD"}
                )
                entries.append({
                    "symbol": symbol,
                    "exchange": exchange,
                    "currency": currency,
                    "sec_type": "STK",
                    "con_id": 0,
                    "fast_path": False,
                    "is_etp": is_etp,
                })
                new_stubs += 1

    # Preserve existing entries not in target (historical rotations, etc.)
    seen = {(e["symbol"], e["exchange"]) for e in entries}
    for k, v in existing.items():
        if k not in seen:
            entries.append(v)
            kept += 1

    write_contracts(entries)
    write_universe_doc(entries)

    print(f"\nWrote {CONTRACTS} with {len(entries)} entries")
    print(f"  Kept existing (preserved con_ids): {kept}")
    print(f"  New stubs (pending qualification): {new_stubs}")
    print(f"\nUniverse breakdown:")
    by_ex = {}
    for e in entries:
        by_ex[e["exchange"]] = by_ex.get(e["exchange"], 0) + 1
    for ex, n in sorted(by_ex.items(), key=lambda x: -x[1]):
        print(f"  {ex}: {n}")


if __name__ == "__main__":
    main()
