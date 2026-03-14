# PHASE 12 — EUROPEAN DIRECT EQUITIES

## Prerequisite
Phase 11 APPROVED. All tests green. MODE B+, MODE C, and full adaptive
infrastructure operational. UniverseScanner, Allocator, and Router modules
stable and handling US equities alongside LSE ETPs.

---

## Core Architecture Change

**CURRENT (Phase 11):** MODE B covers LSE ETPs only during 08:00-16:30 UTC.
MODE B+ merges LSE ETPs + US equities during the 14:30-16:30 overlap window.
MODE C covers Americas only (16:30-08:00). European exchanges are not scanned.
The system ignores ~3,000-5,000 ISA-eligible European equities.

**NEW:** MODE B expands to include direct equities on ALL major ISA-eligible
European exchanges. European tickers trade DURING MODE B (08:00-14:30 UTC)
alongside LSE ETPs — they are IN Mode B, not a separate mode. Phase 12 does
NOT create a new mode. It extends Mode B's universe.

**Key insight:** Most major European exchanges trade 09:00-17:30 CET
(08:00-16:30 UTC), which overlaps almost perfectly with LSE hours. European
equities are natural Mode B inhabitants. The ETP-first routing principle
still holds: if a leveraged ETP exists for a European underlying (e.g.,
ASML -> ASL3.L), the ETP wins. Direct European equity trading only occurs
when NO ETP exists for that underlying.

**Impact on existing modes:**
- MODE B: Expanded from LSE ETPs to LSE ETPs + European direct equities
- MODE B+: Expanded to include European equities in the triple overlap
  (LSE ETPs + European equities + US equities, 14:30-16:30 UTC)
- MODE C: Unchanged. Americas only (16:30-08:00 UTC). European equities
  are NOT in MODE C — European exchanges are closed during these hours.

---

## Section 1: European Exchange Coverage

Exchanges added to MODE B (all ISA-eligible per HMRC Recognised Stock
Exchanges Table 1+2):

| Exchange | Code | Key Constituents | Trading Hours (CET) | Currency |
|----------|------|------------------|---------------------|----------|
| Euronext Paris | EPA | TotalEnergies, LVMH, Sanofi, BNP Paribas, Schneider Electric | 09:00-17:30 | EUR |
| Euronext Amsterdam | AMS | ASML, Adyen, ING, Philips, Wolters Kluwer | 09:00-17:30 | EUR |
| Euronext Brussels | EBR | KBC Group, UCB, Solvay, Umicore | 09:00-17:30 | EUR |
| Euronext Dublin | ISE | CRH, Ryanair, Kerry Group, Smurfit Kappa | 08:00-16:30 | EUR |
| Euronext Lisbon | ELI | Galp Energia, Jerónimo Martins, EDP | 08:00-16:30 | EUR |
| XETRA / Frankfurt | ETR | SAP, Siemens, Mercedes-Benz, BASF, Allianz, Deutsche Telekom | 09:00-17:30 | EUR |
| SIX Swiss Exchange | VTX | Nestle, Novartis, Roche, UBS, ABB, Zurich Insurance | 09:00-17:30 | CHF |
| OMX Stockholm | STO | Ericsson, Volvo, Spotify, Atlas Copco, Hexagon | 09:00-17:30 | SEK |
| OMX Helsinki | HEL | Nokia, Kone, Neste, UPM-Kymmene | 09:00-17:30 | EUR |
| OMX Copenhagen | CPH | Novo Nordisk, Vestas, Orsted, Carlsberg, Coloplast | 09:00-17:00 | DKK |
| Borsa Italiana | BIT | Ferrari, Enel, Intesa Sanpaolo, STMicroelectronics, UniCredit | 09:00-17:30 | EUR |
| BME Madrid | BME | Inditex, Santander, Iberdrola, Telefonica, BBVA | 09:00-17:30 | EUR |
| Oslo Bors | OSL | Equinor, Yara, DNB, Telenor, Mowi | 09:00-16:20 | NOK |
| Warsaw Stock Exchange | WSE | CD Projekt, Allegro, PKO Bank, Dino Polska | 09:00-17:05 | PLN |
| Athens Exchange | ATH | National Bank of Greece, OPAP, Hellenic Telecom | 10:00-17:20 | EUR |

**Total estimated additions:** ~3,000-5,000 ISA-eligible tickers across all
European exchanges, after hard filters (liquidity, market cap, price floor,
recently traded, not suspended).

**Mode B window alignment:** All European exchanges open within 08:00-10:00
UTC and close within 15:20-16:30 UTC. This fits entirely within Mode B's
08:00-16:30 UTC window. No new mode is needed.

---

## Section 2: UniverseScanner Extension

The existing UniverseScanner (built in Phase 11) extends its nightly crawl
to include European exchanges. No new module is created — the existing
pipeline gains additional data sources.

### Nightly Crawl Steps (Extended)

```
STEP 1: Pull European exchange tickers via IBKR reqContractDetails
    │  Query per exchange: secType=STK, exchange=SBF|AEB|SBF|ISED|BVME|...
    │  IBKR uses different exchange codes than Yahoo/Bloomberg.
    │  Mapping maintained in exchange_profiles.toml
    │
    ▼
STEP 2: Apply hard filters (SAME filters as US equities)
    │  • ISA-eligible: HMRC recognised exchange check ✓
    │  • Liquidity: avg daily volume > configurable threshold
    │  • Market cap: > configurable floor (e.g., $50M USD equivalent)
    │  • Price floor: > configurable minimum (e.g., €1.00 equivalent)
    │  • Recently traded: last trade within 5 business days
    │  • Not suspended: IBKR status check
    │  • FX-adjusted: all thresholds converted to local currency
    │
    ▼
STEP 3: ETP Overlay — European underlying check
    │  For each European survivor:
    │    ASML (AMS) → ASL3.L exists on LSE? YES → ETP wins, skip direct
    │    SAP (ETR) → SAP3.L exists? NO → trade SAP direct on XETRA
    │    Novo Nordisk (CPH) → check LSE ETPs → NO → trade direct on CPH
    │    Nestle (VTX) → check LSE ETPs → NO → trade direct on SIX
    │    Ferrari (BIT) → check LSE ETPs → NO → trade direct on BIT
    │    Inditex (BME) → check LSE ETPs → NO → trade direct on BME
    │
    │  Cross-reference: GraniteShares, Leverage Shares, WisdomTree
    │  full ETP catalogue against European underlyings
    │
    ▼
STEP 4: Adaptive scoring pipeline (NO changes needed)
    │  Same ASER score calculation for European tickers
    │  Same Bayesian win rate, Kelly, alpha decay
    │  European tickers just become new entries in the scoring universe
    │
    ▼
STEP 5: Merge European survivors into MODE B's per-mode list
    │  European tickers appear alongside LSE ETPs
    │  Sorted by ASER score — no regional preference
    │  HotScanner gets the top N regardless of exchange
    │  RotationScanner gets the rest
    │
    ▼
STEP 6: Pre-market update includes European pre-market data
    │  07:30 UTC: fetch European pre-market indications
    │  Update overnight gap detection for European tickers
    │  Recalculate ASER with pre-market data
```

### UniverseScanner Config Extension

File: `config/config.toml`

```toml
[universe.european]
enabled = true
exchanges = [
    "SBF",      # Euronext Paris (IBKR code)
    "AEB",      # Euronext Amsterdam
    "BVME",     # Borsa Italiana
    "IBIS",     # XETRA
    "EBS",      # SIX Swiss
    "SFB",      # OMX Stockholm
    "HEX",      # OMX Helsinki
    "CSE",      # OMX Copenhagen
    "BM",       # BME Madrid
    "OMS",      # Oslo Bors
    "WSE",      # Warsaw Stock Exchange
    "ISED",     # Euronext Dublin
    "ENEXT.BE", # Euronext Brussels
    "BVL",      # Euronext Lisbon
    "ATHEX",    # Athens Exchange
]
min_market_cap_usd = 50_000_000   # $50M USD equivalent
min_avg_daily_volume = 100_000    # shares/day
min_price_local = 1.0             # €1 / CHF1 / SEK10 / etc (adjusted per currency)
max_days_since_last_trade = 5     # business days
```

---

## Section 3: Currency-Aware Routing

### New Currencies

Phase 12 introduces six new currencies beyond the existing GBP and USD:

| Currency | Code | Exchanges | Typical FX Spread (bps) |
|----------|------|-----------|------------------------|
| Euro | EUR | EPA, AMS, EBR, ISE, ELI, ETR, BIT, BME, HEL, ATH | 2-5 |
| Swiss Franc | CHF | VTX | 3-8 |
| Swedish Krona | SEK | STO | 5-12 |
| Norwegian Krone | NOK | OSL | 5-15 |
| Danish Krone | DKK | CPH | 3-8 |
| Polish Zloty | PLN | WSE | 10-25 |

### Currency Data Structures

File: `rust_core/src/currency.rs`

```rust
/// All currencies the system can encounter across all European exchanges.
/// GBP and USD already exist from Phase 11. Phase 12 adds EUR through PLN.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[pyclass]
pub enum Currency {
    GBP,    // Base currency (ISA account denominated in GBP)
    USD,    // US equities + some LSE ETPs
    EUR,    // Euronext (Paris, Amsterdam, Brussels, Dublin, Lisbon),
            // XETRA, Borsa Italiana, BME Madrid, OMX Helsinki, Athens
    CHF,    // SIX Swiss Exchange
    SEK,    // OMX Stockholm
    NOK,    // Oslo Bors
    DKK,    // OMX Copenhagen
    PLN,    // Warsaw Stock Exchange
}

/// FX conversion data for a single currency pair, fetched daily from IBKR.
/// The Router uses this to calculate the true cost of direct European trades.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CurrencyRoute {
    /// The currency of the instrument being traded
    pub base_currency: Currency,
    /// Mid-market exchange rate to GBP (e.g., EUR/GBP = 0.86)
    pub fx_rate_to_gbp: f64,
    /// Round-trip FX spread in basis points (e.g., 5 bps = 0.05%)
    pub fx_spread_bps: f64,
    /// When this rate was last fetched from IBKR
    pub fx_last_updated: DateTime<Utc>,
}

/// Complete FX rate table, loaded at boot and refreshed daily.
/// Staleness check: if any rate is >24h old, system re-fetches before routing.
pub struct FxRateTable {
    /// Currency -> CurrencyRoute mapping
    rates: HashMap<Currency, CurrencyRoute>,
    /// Maximum age before mandatory refresh
    max_staleness: Duration,  // 24 hours
}

impl FxRateTable {
    /// Convert an amount from local currency to GBP.
    /// Returns None if rate is stale or missing.
    pub fn to_gbp(&self, amount: f64, currency: Currency) -> Option<f64> {
        if currency == Currency::GBP {
            return Some(amount);
        }
        let route = self.rates.get(&currency)?;
        if route.fx_last_updated.elapsed() > self.max_staleness {
            return None;  // Stale — caller must refresh
        }
        Some(amount * route.fx_rate_to_gbp)
    }

    /// Calculate the FX conversion cost in GBP for a given trade size.
    pub fn fx_cost_gbp(&self, notional_local: f64, currency: Currency) -> Option<f64> {
        if currency == Currency::GBP {
            return Some(0.0);  // No FX cost for GBP instruments
        }
        let route = self.rates.get(&currency)?;
        let notional_gbp = notional_local * route.fx_rate_to_gbp;
        Some(notional_gbp * route.fx_spread_bps / 10_000.0)
    }

    /// Check if any rate is stale and needs refreshing.
    pub fn has_stale_rates(&self) -> bool {
        self.rates.values().any(|r| r.fx_last_updated.elapsed() > self.max_staleness)
    }
}
```

### Router Cost Comparison Extension

The Router's cost comparison now includes FX conversion cost for every
routing decision involving a non-GBP instrument:

```
DIRECT EUROPEAN EQUITY COST:
    direct_cost = spread_cost + fx_conversion_cost + local_transaction_tax

ETP ROUTE COST (when ETP exists for the underlying):
    etp_cost = etp_spread_cost + tracking_error + premium_discount
    (No FX cost for GBP-denominated LSE ETPs)
    (USD-denominated ETPs: add USD/GBP FX cost)

ROUTING DECISION:
    if etp_exists(underlying):
        if etp_cost < direct_cost:
            → Route through ETP (almost always wins)
        else:
            → Route direct (rare — only if ETP has extreme premium/discount)
    else:
        → Route direct (no choice)
```

This is another reason ETP always wins when available: ETP trading avoids
FX conversion entirely for GBP-denominated ETPs, saving 2-25 bps per
round trip depending on the currency.

---

## Section 4: European Exchange-Specific Execution Profiles

Each European exchange has different tick sizes, order types, closing auction
mechanics, and market structure. The Executioner loads exchange-specific
profiles at boot and applies them to every order.

### Exchange Profile Data Structures

File: `rust_core/src/exchange_profile.rs`

```rust
/// Identifies which exchange a ticker trades on.
/// Used to look up the correct ExchangeProfile for execution.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[pyclass]
pub enum Exchange {
    // Existing
    LSE,        // London Stock Exchange (ETPs)
    NYSE,       // New York Stock Exchange
    NASDAQ,     // NASDAQ
    // New — Phase 12 European exchanges
    EuronextParis,
    EuronextAmsterdam,
    EuronextBrussels,
    EuronextDublin,
    EuronextLisbon,
    Xetra,
    SixSwiss,
    OmxStockholm,
    OmxHelsinki,
    OmxCopenhagen,
    BorsaItaliana,
    BmeMadrid,
    OsloBors,
    Warsaw,
    Athens,
}

/// How market making works on this exchange. Affects spread expectations
/// and execution timing.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MarketMakerModel {
    /// Designated Liquidity Providers with quoting obligations
    /// (Euronext, OMX). Tighter spreads during normal hours.
    DesignatedLP,
    /// Pure electronic order book, no designated LPs (XETRA).
    /// Spreads depend entirely on natural flow.
    ElectronicOrderBook,
    /// Mix of designated LPs and electronic matching (SIX, Borsa).
    Hybrid,
}

/// Tick size varies by price range on European exchanges.
/// MiFID II harmonised but each exchange has nuances.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TickSizeRule {
    /// Minimum price for this tick size band (inclusive)
    pub price_range_min: f64,
    /// Maximum price for this tick size band (exclusive, f64::MAX for last band)
    pub price_range_max: f64,
    /// The tick size in local currency units
    pub tick_size: f64,
}

/// Complete execution profile for a single exchange.
/// Loaded from exchange_profiles.toml at boot.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExchangeProfile {
    /// Which exchange this profile applies to
    pub exchange: Exchange,
    /// IBKR exchange code used in order submission
    pub ibkr_exchange_code: String,
    /// Tick size rules (price-dependent, MiFID II compliant)
    pub tick_sizes: Vec<TickSizeRule>,
    /// Order types supported by this exchange via IBKR
    pub supported_orders: Vec<OrderType>,
    /// When the closing auction runs (local exchange time, CET for most)
    /// None if exchange has no closing auction
    pub closing_auction_time: Option<NaiveTime>,
    /// Exchange open time in UTC
    pub open_utc: NaiveTime,
    /// Exchange close time in UTC
    pub close_utc: NaiveTime,
    /// Whether designated LPs provide guaranteed liquidity
    pub has_designated_lps: bool,
    /// Local financial transaction tax rate (None = no FTT)
    pub local_transaction_tax: Option<f64>,
    /// Market structure model (affects spread expectations)
    pub market_maker_model: MarketMakerModel,
    /// Currency instruments on this exchange are denominated in
    pub currency: Currency,
    /// Auction avoidance window around open (minutes before/after)
    pub auction_open_buffer_mins: u32,
    /// Auction avoidance window around close (minutes before/after)
    pub auction_close_buffer_mins: u32,
}

/// Order types available on European exchanges.
/// Not all exchanges support all types via IBKR.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderType {
    Limit,
    Market,
    Stop,
    StopLimit,
    Iceberg,
    Hidden,
    MarketToLimit,
}

impl ExchangeProfile {
    /// Get the correct tick size for a given price on this exchange.
    /// Returns the tick size in local currency.
    pub fn tick_size_for_price(&self, price: f64) -> f64 {
        for rule in &self.tick_sizes {
            if price >= rule.price_range_min && price < rule.price_range_max {
                return rule.tick_size;
            }
        }
        // Fallback: smallest tick size (most conservative)
        self.tick_sizes.last().map(|r| r.tick_size).unwrap_or(0.01)
    }

    /// Round a price to the nearest valid tick for this exchange.
    pub fn round_to_tick(&self, price: f64) -> f64 {
        let tick = self.tick_size_for_price(price);
        (price / tick).round() * tick
    }

    /// Check if a given order type is supported on this exchange.
    pub fn supports_order_type(&self, order_type: OrderType) -> bool {
        self.supported_orders.contains(&order_type)
    }

    /// Check if the exchange is currently in its closing auction window.
    pub fn is_closing_auction(&self, now_utc: NaiveTime) -> bool {
        if let Some(auction_time) = self.closing_auction_time {
            let buffer = chrono::Duration::minutes(self.auction_close_buffer_mins as i64);
            now_utc >= auction_time - buffer && now_utc <= auction_time + buffer
        } else {
            false
        }
    }

    /// Check if the exchange is currently open for continuous trading.
    pub fn is_open(&self, now_utc: NaiveTime) -> bool {
        now_utc >= self.open_utc && now_utc < self.close_utc
    }
}
```

### Exchange Profile Reference Table

| Exchange | Tick Size (representative) | Order Types | Closing Auction (CET) | Market Model | Notes |
|----------|--------------------------|-------------|----------------------|--------------|-------|
| Euronext Paris | Variable: 0.001 (<0.50), 0.005 (0.50-5), 0.01 (5-50), 0.05 (>50) | Limit, Market, Stop, Iceberg | 17:30 | DesignatedLP | Designated LPs with quoting obligations. MiFID II tick regime |
| Euronext Amsterdam | Same as Euronext Paris | Limit, Market, Stop, Iceberg | 17:30 | DesignatedLP | Shared Optiq platform with Paris |
| Euronext Brussels | Same as Euronext Paris | Limit, Market, Stop | 17:30 | DesignatedLP | Smaller liquidity pool |
| Euronext Dublin | Same as Euronext Paris | Limit, Market, Stop | 16:28 | DesignatedLP | Slightly earlier close |
| Euronext Lisbon | Same as Euronext Paris | Limit, Market, Stop | 16:30 | DesignatedLP | Smallest Euronext venue |
| XETRA | Variable: 0.001 (<1), 0.005 (1-5), 0.01 (5-100), 0.05 (>100) | Limit, Market, Iceberg, Hidden | 17:30 | ElectronicOrderBook | Pure electronic order book, no designated LPs. T7 matching engine |
| SIX Swiss | Variable: 0.001 (<0.50), 0.005 (0.50-10), 0.01 (10-100), 0.05 (>100) | Limit, Market, Stop | 17:20 | Hybrid | Midpoint match available. Swiss Blue Chip segment |
| OMX Stockholm | Variable: 0.001 (<0.50), 0.005 (0.50-5), 0.01 (5-50), 0.05 (>50) | Limit, Market, Stop | 17:25 | DesignatedLP | Nordic close auctions. INET matching engine |
| OMX Helsinki | Same as OMX Stockholm | Limit, Market, Stop | 17:25 | DesignatedLP | Shared Nordic platform |
| OMX Copenhagen | Same as OMX Stockholm | Limit, Market, Stop | 17:00 | DesignatedLP | Earlier close than other Nordics |
| Borsa Italiana | Variable: 0.0001 (<0.25), 0.0005 (0.25-1), 0.001 (1-5), 0.005 (5-50), 0.01 (>50) | Limit, Market, MarketToLimit | 17:35 | Hybrid | After-hours MOT segment (not used). Euronext-owned since 2021 |
| BME Madrid | Variable: 0.0001 (<1), 0.001 (1-10), 0.005 (10-50), 0.01 (>50) | Limit, Market | 17:30 | ElectronicOrderBook | Spanish FTT on large-caps. SIBE matching engine |
| Oslo Bors | 0.01 (most instruments) | Limit, Market | 16:20 | Hybrid | Shorter session. Euronext-owned |
| Warsaw | 0.01 (most instruments) | Limit, Market | 17:05 | ElectronicOrderBook | Longest CET close. Emerging market classification by some indices |
| Athens | 0.001 (<5), 0.01 (>5) | Limit, Market | 17:20 | ElectronicOrderBook | Smallest volume of all covered exchanges |

### Exchange Profile Config

File: `config/exchange_profiles.toml`

```toml
[[exchange]]
name = "EuronextParis"
ibkr_code = "SBF"
currency = "EUR"
open_utc = "08:00"
close_utc = "16:30"
closing_auction_utc = "16:30"
market_maker_model = "DesignatedLP"
auction_open_buffer_mins = 10
auction_close_buffer_mins = 5

[[exchange.tick_sizes]]
price_range_min = 0.0
price_range_max = 0.50
tick_size = 0.001

[[exchange.tick_sizes]]
price_range_min = 0.50
price_range_max = 5.0
tick_size = 0.005

[[exchange.tick_sizes]]
price_range_min = 5.0
price_range_max = 50.0
tick_size = 0.01

[[exchange.tick_sizes]]
price_range_min = 50.0
price_range_max = 1e18
tick_size = 0.05

# ... (remaining 14 exchanges follow the same pattern)
```

---

## Section 5: Stamp Duty and Transaction Tax Handling

### Financial Transaction Tax Reference

| Country | Tax Name | Rate | Applies To | Threshold | Notes |
|---------|----------|------|-----------|-----------|-------|
| United Kingdom | Stamp Duty Reserve Tax | 0.5% | UK equities (NOT ETPs) | All UK equities | LSE ETPs are exempt — another reason ETP wins |
| France | Financial Transaction Tax | 0.3% | French large-caps | Market cap > 1B EUR | ~140 companies on Euronext Paris |
| Italy | Financial Transaction Tax | 0.1% (on exchange) | Italian equities | Market cap > 500M EUR | 0.2% for OTC — always use exchange |
| Spain | Financial Transaction Tax | 0.2% | Spanish equities | Market cap > 1B EUR | Since Jan 2021. ~60 companies on BME |
| Germany | None | 0% | All | N/A | No FTT as of Phase 12 |
| Switzerland | Swiss Stamp Tax | 0.075% | Swiss securities (buyer + seller) | All | Turnover tax, not FTT. Applied by broker |
| Norway | None | 0% | All | N/A | No FTT |
| Sweden | None | 0% | All | N/A | No FTT (abolished 1991) |
| Denmark | None | 0% | All | N/A | No FTT |
| Finland | None | 0% | All | N/A | No FTT |
| Poland | None | 0% | All | N/A | No FTT |
| Greece | Transaction Tax | 0.2% | Greek equities | All | Since 2014 |
| Belgium | Stock Exchange Tax | 0.12% | Belgian securities | All | Tax on stock exchange transactions |
| Portugal | None | 0% | All | N/A | No FTT |
| Ireland | Stamp Duty | 1.0% | Irish equities | All | One of the highest in Europe |

### Transaction Tax Data Structures

File: `rust_core/src/transaction_tax.rs`

```rust
/// Transaction tax configuration for a specific country/exchange.
/// Loaded from transaction_taxes.toml at boot.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TransactionTax {
    /// Which exchange this tax applies to
    pub exchange: Exchange,
    /// Tax rate as a decimal (0.003 = 0.3%)
    pub rate: f64,
    /// Minimum market cap (in USD equivalent) for the tax to apply.
    /// None means the tax applies to all equities on this exchange.
    pub market_cap_threshold_usd: Option<f64>,
    /// Human-readable name for logging
    pub tax_name: String,
}

/// Registry of all transaction taxes across European exchanges.
/// Used by the Router in cost comparison calculations.
pub struct TransactionTaxRegistry {
    taxes: HashMap<Exchange, TransactionTax>,
}

impl TransactionTaxRegistry {
    /// Calculate the transaction tax for a given trade.
    /// Returns 0.0 if no tax applies (exchange has no FTT, or
    /// the ticker's market cap is below the threshold).
    pub fn tax_cost(
        &self,
        exchange: Exchange,
        notional_gbp: f64,
        market_cap_usd: Option<f64>,
    ) -> f64 {
        if let Some(tax) = self.taxes.get(&exchange) {
            // Check market cap threshold if applicable
            if let Some(threshold) = tax.market_cap_threshold_usd {
                if let Some(mcap) = market_cap_usd {
                    if mcap < threshold {
                        return 0.0;  // Below threshold — no tax
                    }
                } else {
                    // Unknown market cap — assume tax applies (conservative)
                }
            }
            notional_gbp * tax.rate
        } else {
            0.0  // No tax configured for this exchange
        }
    }
}
```

### Router Cost Comparison (Complete Formula)

The Router now calculates the full cost of both routes for every European
underlying:

```
DIRECT EUROPEAN EQUITY ROUTE:
    spread_cost     = notional * (ask - bid) / mid
    fx_cost         = notional_gbp * fx_spread_bps / 10000
    transaction_tax = notional_gbp * tax_rate  (if applicable)
    commission      = IBKR commission (exchange-dependent)
    ─────────────────────────────────────────────────────
    total_direct    = spread_cost + fx_cost + transaction_tax + commission

ETP ROUTE (when ETP exists):
    spread_cost     = notional * (etp_ask - etp_bid) / etp_mid
    tracking_error  = annual_TE / 252  (daily tracking error)
    premium_discount = |NAV - market_price| / NAV
    commission      = IBKR LSE commission
    fx_cost         = 0 (GBP ETP) or notional_gbp * usd_fx_spread (USD ETP)
    ─────────────────────────────────────────────────────
    total_etp       = spread_cost + tracking_error + premium_discount + commission + fx_cost

ROUTING DECISION:
    route = if total_etp < total_direct { ETP } else { Direct }
```

**Why ETP almost always wins for European underlyings with ETPs:**
1. No FX conversion cost (GBP-denominated ETP vs EUR/CHF/SEK direct)
2. No local transaction tax (ETPs exempt from stamp duty and FTT)
3. 3x leverage amplifies PnL without amplifying cost
4. Single exchange (LSE) — no exchange-specific complexity

---

## Section 6: European Closing Auction Integration

Many European exchanges run closing auctions that capture 20-25% of daily
volume. These are significant liquidity events that differ from the LSE
closing auction the system already handles.

### Closing Auction Times (UTC)

```
Exchange                  Auction Start (UTC)  Duration
──────────────────────────────────────────────────────
Euronext Paris/AMS/BRU         16:25-16:30      5 min
Euronext Dublin                15:23-15:28      5 min
Euronext Lisbon                15:25-15:30      5 min
XETRA                         16:25-16:30      5 min (Xetra closing)
SIX Swiss                     16:15-16:20      5 min
OMX Stockholm                 16:20-16:25      5 min
OMX Helsinki                  16:20-16:25      5 min
OMX Copenhagen                15:55-16:00      5 min
Borsa Italiana                16:30-16:35      5 min
BME Madrid                    16:25-16:30      5 min
Oslo Bors                     15:15-15:20      5 min
Warsaw                        16:00-16:05      5 min
Athens                        16:15-16:20      5 min
LSE (existing)                15:50-16:00     10 min (intra-day close)
```

### Integration Rules

1. **Chandelier T-5 rule accounts for European closing auction times.**
   The existing Chandelier EOD flatten phases (T-35, T-15, T-5) are
   relative to the EXCHANGE CLOSE of each position's exchange, not a
   global time. A position on XETRA flattens relative to 16:30 UTC.
   A position on Oslo Bors flattens relative to 15:20 UTC.

2. **Closing auction participation:** Only for positions the system is
   already holding and wants to close. The Executioner submits a closing
   auction order (MOC or LOC equivalent) when:
   - The position is in T-5 phase (last resort exit)
   - The exchange supports closing auction orders via IBKR
   - Volume conditions suggest the auction will provide better fill

3. **NO new entries during closing auctions.** Closing auctions lack
   continuous price discovery — signal quality is zero. The system's
   existing AuctionPeriod veto applies per-exchange.

4. **Exchange-specific auction avoidance windows.** Each ExchangeProfile
   defines its own auction buffer. The RiskGate checks the position's
   exchange, not a global LSE time.

```rust
/// Extended EOD flatten phases — exchange-aware.
/// Each position tracks its exchange's close time independently.
pub struct ExchangeAwareEodFlatten {
    /// The exchange this position trades on
    pub exchange: Exchange,
    /// Exchange close time in UTC (from ExchangeProfile)
    pub exchange_close_utc: NaiveTime,
}

impl ExchangeAwareEodFlatten {
    /// Calculate the absolute UTC times for the 3 EOD flatten phases
    /// relative to THIS exchange's close time.
    pub fn flatten_phases(&self) -> EodPhases {
        EodPhases {
            // T-35: passive limit at mid + 1 tick
            phase1: self.exchange_close_utc - Duration::minutes(35),
            // T-15: limit at mid
            phase2: self.exchange_close_utc - Duration::minutes(15),
            // T-5: MTL emergency / closing auction order
            phase3: self.exchange_close_utc - Duration::minutes(5),
        }
    }
}
```

---

## Section 7: ETP Coverage Check for European Underlyings

### Current ETP Coverage (Phase 12 Baseline)

The nightly Ouroboros pipeline checks every European underlying against
the Master Registry of leveraged ETPs on LSE:

```
UNDERLYING (Exchange)     ETP EXISTS?    ROUTE
───────────────────────────────────────────────────────
ASML (AMS)               ASL3.L (3x)    ETP wins ✓
SAP (ETR)                No ETP          Trade direct on XETRA
Novo Nordisk (CPH)       No ETP          Trade direct on CPH
Nestle (VTX)             No ETP          Trade direct on SIX
Roche (VTX)              No ETP          Trade direct on SIX
Novartis (VTX)           No ETP          Trade direct on SIX
LVMH (EPA)               No ETP          Trade direct on Euronext Paris
TotalEnergies (EPA)      No ETP          Trade direct on Euronext Paris
Ferrari (BIT)            No ETP          Trade direct on Borsa Italiana
Inditex (BME)            No ETP          Trade direct on BME Madrid
Siemens (ETR)            No ETP          Trade direct on XETRA
Ericsson (STO)           No ETP          Trade direct on OMX Stockholm
Spotify (STO)            No ETP          Trade direct on OMX Stockholm
CD Projekt (WSE)         No ETP          Trade direct on Warsaw
Equinor (OSL)            No ETP          Trade direct on Oslo Bors
```

**Key observation:** As of Phase 12 baseline, very few European underlyings
have leveraged ETPs on LSE. Most European equities will trade direct. This
makes FX handling and transaction tax awareness critical.

### Nightly ETP Discovery (Ouroboros Extension)

```
OUROBOROS NIGHTLY (23:50 ET) — ETP DISCOVERY STEP:

STEP 1: Scrape Leverage Shares full product catalogue
    │  URL: leverageshares.com/en/products
    │  Extract: underlying → ETP ticker → leverage → exchange
    │
    ▼
STEP 2: Scrape GraniteShares full product catalogue
    │  URL: graniteshares.com/institutional/uk/en-uk/etps
    │  Extract: same fields
    │
    ▼
STEP 3: Scrape WisdomTree ETP catalogue
    │  URL: wisdomtree.eu/en-gb/products
    │  Extract: same fields
    │
    ▼
STEP 4: Cross-reference European underlyings with ETP catalogue
    │  For each European ticker in universe:
    │    if new_etp_found(underlying):
    │      → Update routing_table.toml: underlying → new ETP
    │      → Log INFO: "New ETP detected: SAP3.L for SAP (XETRA)"
    │      → Free up direct subscription line
    │
    ▼
STEP 5: Check for DELISTED ETPs
    │  For each ETP in routing table:
    │    if not_in_catalogue(etp) AND not_tradeable_on_ibkr(etp):
    │      → Update routing_table.toml: underlying falls back to direct
    │      → Log WARNING: "ETP delisted: SAP3.L — SAP reverts to direct"
    │      → Allocate direct subscription line
```

### Routing Table Extension

File: `config/routing_table.toml`

```toml
# Phase 12 European entries (appended to existing Phase 11 table)

# European underlyings WITH LSE ETP (ETP wins)
[[route]]
underlying = "ASML"
scan_via = "ASL3.L"
execution = "ASL3.L"
exchange = "AMS"
leverage = 3
is_direct = false

# European underlyings WITHOUT ETP (trade direct)
[[route]]
underlying = "SAP"
scan_via = "SAP"
execution = "SAP"
exchange = "ETR"
leverage = 1
is_direct = true
currency = "EUR"

[[route]]
underlying = "NOVO-B"
scan_via = "NOVO-B"
execution = "NOVO-B"
exchange = "CPH"
leverage = 1
is_direct = true
currency = "DKK"

# ... (remaining European tickers generated nightly by Ouroboros)
```

---

## Section 8: Mode B Scanner Integration

### Sub-Universe Architecture

MODE B now has TWO sub-universes feeding the SAME HotScanner and
RotationScanner pipelines:

```
MODE B SUB-UNIVERSES (08:00-14:30 UTC):

    ┌────────────────────────────────────────────────┐
    │                MODE B UNIVERSE                  │
    │                                                 │
    │  SUB-UNIVERSE 1: LSE ETPs (Phase 10)           │
    │    ~1,000 tickers: QQQ3.L, NVD3.L, 3OIL.L ... │
    │    Leverage: 2x-5x                              │
    │    Currency: GBP / USD                          │
    │    FX cost: 0 (GBP) or low (USD)               │
    │                                                 │
    │  SUB-UNIVERSE 2: European Equities (Phase 12)  │
    │    ~3,000-5,000 tickers: SAP, Novo, Nestle ... │
    │    Leverage: 1x (direct)                        │
    │    Currency: EUR / CHF / SEK / NOK / DKK / PLN │
    │    FX cost: 2-25 bps round trip                 │
    │                                                 │
    │  COMBINED: ~4,000-6,000 tickers                │
    │  Sorted by ASER score — no regional preference  │
    │  HotScanner takes top N regardless of exchange  │
    │  RotationScanner rotates through the rest       │
    └────────────────────────────────────────────────┘
```

### Allocator Split Logic

The Allocator manages the dynamic split between ETPs and European equities
within Mode B using Thompson Sampling per sub-universe:

```rust
/// Thompson Sampling allocator for Mode B sub-universe split.
/// Dynamically allocates hot slots between ETPs and European equities
/// based on which sub-universe is generating better signals.
pub struct SubUniverseAllocator {
    /// Beta distribution parameters for ETP sub-universe
    pub etp_alpha: f64,    // Successes (profitable signals)
    pub etp_beta: f64,     // Failures (unprofitable signals)
    /// Beta distribution parameters for European equity sub-universe
    pub euro_alpha: f64,
    pub euro_beta: f64,
    /// Minimum allocation to each sub-universe (prevents starvation)
    pub min_fraction: f64,  // 0.15 = at least 15% to each
}

impl SubUniverseAllocator {
    /// Sample from both Beta distributions and allocate proportionally.
    /// Returns (etp_fraction, euro_fraction) where both sum to 1.0.
    pub fn allocate(&self, rng: &mut impl Rng) -> (f64, f64) {
        let etp_sample = Beta::new(self.etp_alpha, self.etp_beta)
            .unwrap()
            .sample(rng);
        let euro_sample = Beta::new(self.euro_alpha, self.euro_beta)
            .unwrap()
            .sample(rng);

        let total = etp_sample + euro_sample;
        let etp_raw = etp_sample / total;
        let euro_raw = euro_sample / total;

        // Enforce minimum allocation
        let etp_frac = etp_raw.max(self.min_fraction);
        let euro_frac = euro_raw.max(self.min_fraction);

        // Renormalize
        let sum = etp_frac + euro_frac;
        (etp_frac / sum, euro_frac / sum)
    }

    /// Update posterior after a trade outcome.
    pub fn update(&mut self, sub_universe: SubUniverse, profitable: bool) {
        match sub_universe {
            SubUniverse::LseEtp => {
                if profitable { self.etp_alpha += 1.0; }
                else { self.etp_beta += 1.0; }
            }
            SubUniverse::EuropeanEquity => {
                if profitable { self.euro_alpha += 1.0; }
                else { self.euro_beta += 1.0; }
            }
            SubUniverse::UsEquity => { /* Handled by Mode C allocator */ }
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SubUniverse {
    LseEtp,
    EuropeanEquity,
    UsEquity,
}
```

### MODE B+ Integration (Triple Overlap)

During 14:30-16:30 UTC, MODE B+ activates the triple overlap. The Allocator
distributes lines across three sub-universes:

```
MODE B+ (14:30-16:30 UTC — Triple Overlap):

    ┌────────────────────────────────────────────────┐
    │              MODE B+ UNIVERSE                   │
    │                                                 │
    │  SUB-UNIVERSE 1: LSE ETPs                      │
    │  SUB-UNIVERSE 2: European Equities (Phase 12)  │
    │  SUB-UNIVERSE 3: US Equities (Phase 11)        │
    │                                                 │
    │  ALLOCATOR: Thompson Sampling across all 3      │
    │  Min 10% to each sub-universe                   │
    │  Rest allocated by signal quality               │
    │                                                 │
    │  100-line constraint still enforced.            │
    │  Safety-locked positions: always subscribed.    │
    └────────────────────────────────────────────────┘
```

### Worked Example: Wednesday, 11:00 UTC (MODE B)

```
Open positions: QQQ3.L (ETP), SAP (XETRA direct) = 2 locked lines
Available: 100 - 2 = 98

Thompson Sampling sample: ETP=0.62, Euro=0.38
→ ETP gets 62% of 98 = 61 hot/rotating ETP lines
→ Euro gets 38% of 98 = 37 hot/rotating European lines

HotScanner: top 25 ETPs + top 15 European equities = 40 hot lines
RotationScanner: 36 ETP rotating + 22 European rotating = 58 rotating

TOTAL: 2 + 40 + 58 = 100 ✓

European sweep: ~4,800 euro tickers / 22 batch lines = ~218 batches
   × 60s rotation = ~218 min ≈ 3.6 hours full sweep
   (Acceptable: European session is 8.5 hours)
```

### Worked Example: Wednesday, 15:00 UTC (MODE B+ — Triple Overlap)

```
Open positions: QQQ3.L (ETP), SAP (XETRA), AVGO (US) = 3 locked lines
Available: 100 - 3 = 97

Thompson Sampling sample: ETP=0.45, Euro=0.25, US=0.30
→ ETP: 44 lines (hot + rotating)
→ Euro: 24 lines (hot + rotating)
→ US: 29 lines (hot + rotating)

TOTAL: 3 + 44 + 24 + 29 = 100 ✓

All three regions actively scanned during the 2-hour overlap.
```

### Safety-Locked European Positions in MODE C

When MODE C activates (16:30 UTC), European exchanges are closed. Any
open European position:
- Retains its safety-locked streaming line (Chandelier still monitors
  last known price for gap detection)
- No new European scanning occurs (exchanges closed)
- European position's EOD flatten should have triggered before
  exchange close (see Section 6)
- If European position is still open at MODE C transition: it stays
  locked with last-known stop. Manual review may be needed.

---

## Section 9: Acceptance Tests

File: `rust_core/src/phase12_tests.rs`

### European Exchange Integration (Tests 1-8)

| # | Test | Expected |
|---|------|----------|
| 1 | XETRA ticker SAP added to universe by UniverseScanner | SAP appears in MODE B ranked list sorted by ASER score |
| 2 | Euronext ticker ASML in universe, ASL3.L ETP exists | Routed to ASL3.L (ETP exists, ETP wins) |
| 3 | OMX Copenhagen ticker Novo Nordisk in universe, no ETP | Traded direct on CPH, leverage=1, currency=DKK |
| 4 | Athens Exchange ticker with $30M market cap | REJECTED by hard filter (below $50M threshold) |
| 5 | Warsaw ticker not traded in 5+ business days | REJECTED by hard filter (not recently traded) |
| 6 | European ticker (SAP) appears in MODE B HotScanner list | Present alongside LSE ETPs, sorted by ASER score |
| 7 | European ticker in MODE B+ overlap (14:30-16:30 UTC) | Present alongside LSE ETPs AND US equities |
| 8 | European ticker NOT in MODE C list (16:30-08:00 UTC) | Absent from MODE C scanning (MODE C = Americas only) |

### Currency Routing (Tests 9-14)

| # | Test | Expected |
|---|------|----------|
| 9 | EUR-denominated XETRA ticker (SAP) cost comparison | FX cost (EUR/GBP spread) included in Router direct_cost |
| 10 | CHF-denominated SIX ticker (Nestle) cost comparison | FX cost (CHF/GBP spread) included in Router direct_cost |
| 11 | SEK-denominated OMX ticker (Ericsson) cost comparison | FX cost (SEK/GBP spread) included in Router direct_cost |
| 12 | ETP exists for European underlying (ASML -> ASL3.L) | ETP wins: etp_cost < direct_cost (no FX for GBP ETP) |
| 13 | FX spread spikes above 50 bps for EUR/GBP | Router preference for ETP strengthens (higher FX cost penalty) |
| 14 | FX rate stale (>24 hours since last update) | System re-fetches from IBKR before any routing decision. FxRateTable.has_stale_rates() returns true |

### Stamp Duty / FTT (Tests 15-20)

| # | Test | Expected |
|---|------|----------|
| 15 | French large-cap on Euronext Paris (market cap > 1B EUR) | 0.3% FTT added to direct_cost by TransactionTaxRegistry |
| 16 | Italian equity on Borsa Italiana (market cap > 500M EUR) | 0.1% FTT added to direct_cost |
| 17 | Spanish equity on BME Madrid (market cap > 1B EUR) | 0.2% FTT added to direct_cost |
| 18 | German equity on XETRA (any market cap) | No FTT in direct_cost (Germany has no FTT) |
| 19 | ETP for French large-cap underlying (ASML -> ASL3.L) | FTT avoided entirely, only etp_cost applies |
| 20 | Direct cost + FTT + FX > ETP cost for a ticker with ETP | Router selects ETP route. Log confirms: "ETP wins: total_etp={} < total_direct={}" |

### Exchange-Specific Execution (Tests 21-28)

| # | Test | Expected |
|---|------|----------|
| 21 | Euronext order at EUR 75 uses correct tick size | Tick size = 0.05 (price > 50 EUR on Euronext). Price rounded correctly by ExchangeProfile.round_to_tick() |
| 22 | XETRA order rejects unsupported Stop order type | XETRA profile lists [Limit, Market, Iceberg, Hidden]. Stop order rejected, Limit used instead |
| 23 | SIX Swiss order at CHF 85 uses correct CHF tick size | Tick size = 0.01 (price 10-100 CHF). Rounded correctly |
| 24 | European closing auction order submitted during auction window | Accepted: order submitted within T-5 phase of exchange-specific close time |
| 25 | Order submitted after European exchange close (e.g., XETRA after 16:30 UTC) | REJECTED with VetoReason::ExchangeClosed |
| 26 | Euronext designated LP under stress (wide spread during LP quoting pause) | Execution delayed. SpreadTooWide veto fires until LP resumes |
| 27 | Partial fill on XETRA electronic order book | Standard partial fill rules apply: VWAP updated, remaining qty tracked |
| 28 | All 15 European exchange profiles loaded at boot | ExchangeProfileRegistry contains exactly 15 entries, one per exchange. assert_eq!(registry.len(), 15) |

### Scanner Integration (Tests 29-35)

| # | Test | Expected |
|---|------|----------|
| 29 | European hot slot alongside LSE ETP in MODE B HotScanner | Both SAP (XETRA) and QQQ3.L (LSE) present in same HotScanner tier, sorted by ASER |
| 30 | European ticker promoted from RotationScanner to HotScanner | Weakest MODE B hot slot (lowest ASER) demoted to rotation, European ticker promoted |
| 31 | European ticker in MODE B+ merged list (triple overlap) | SAP, QQQ3.L, and AVGO all present in unified B+ scanner list |
| 32 | Allocator splits Mode B lines between ETP and European sub-universes | Thompson Sampling produces valid split. Both fractions >= min_fraction (0.15). Sum = 1.0 |
| 33 | Allocator splits Mode B+ lines across 3 sub-universes | Thompson Sampling across ETP + European + US. All fractions >= 0.10. Sum = 1.0 |
| 34 | Safety-locked European position during MODE C transition | European position retains streaming line. Chandelier monitors last-known price. No new European scanning |
| 35 | European ticker's underlying tracked for ETP emergence | Nightly Ouroboros checks European underlyings against ETP catalogues. New ETP detection triggers routing update |

### Ouroboros Extension (Tests 36-40)

| # | Test | Expected |
|---|------|----------|
| 36 | Nightly crawl includes Euronext Paris (SBF) | reqContractDetails for SBF exchange returns tickers. New Euronext tickers appear in universe after nightly run |
| 37 | Nightly crawl includes XETRA (IBIS) | reqContractDetails for IBIS exchange returns tickers. New XETRA tickers appear in universe after nightly run |
| 38 | New European ETP detected (e.g., SAP3.L launched) | routing_table.toml updated: SAP route changes from direct to SAP3.L. Log: "New ETP detected: SAP3.L for SAP (XETRA)" |
| 39 | European ticker scoring uses same adaptive ASER pipeline | ASER score for SAP computed identically to US equities: Amihud + spread + volume + Bayesian WR + Kelly |
| 40 | Component calibration includes European trade data | Ouroboros nightly pipeline processes European trades in WAL. Bayesian WR, Kelly, and alpha decay updated for European tickers |

---

## Section 10: Files Summary

| File | Change | LOC (est.) |
|------|--------|------------|
| `rust_core/src/currency.rs` | NEW — Currency enum, CurrencyRoute, FxRateTable | ~120 |
| `rust_core/src/exchange_profile.rs` | NEW — Exchange enum, ExchangeProfile, TickSizeRule, MarketMakerModel, OrderType, ExchangeAwareEodFlatten | ~250 |
| `rust_core/src/transaction_tax.rs` | NEW — TransactionTax, TransactionTaxRegistry | ~80 |
| `rust_core/src/sub_universe_allocator.rs` | NEW — SubUniverseAllocator (Thompson Sampling), SubUniverse enum | ~100 |
| `rust_core/src/phase12_tests.rs` | NEW — 40 acceptance tests | ~400 |
| `config/exchange_profiles.toml` | NEW — 15 European exchange profiles with tick sizes, order types, auction times | ~300 |
| `config/transaction_taxes.toml` | NEW — FTT/stamp duty rates for all covered countries | ~80 |
| `config/config.toml` | MODIFIED — Add `[universe.european]` section with exchange codes, thresholds | ~20 |
| `config/routing_table.toml` | MODIFIED — Add European direct equity routes (generated nightly) | ~variable |
| `rust_core/src/types.rs` | MODIFIED — Add Currency, Exchange, SubUniverse enums; extend VetoReason with ExchangeClosed | ~30 |
| `rust_core/src/smart_router.rs` | MODIFIED — Integrate CurrencyRoute + TransactionTaxRegistry into cost comparison | ~60 |
| `rust_core/src/line_allocator.rs` | MODIFIED — Integrate SubUniverseAllocator for Mode B/B+ splits | ~80 |
| `rust_core/src/exit_engine.rs` | MODIFIED — Exchange-aware EOD flatten phases (per-exchange close times) | ~40 |
| `rust_core/src/risk_arbiter.rs` | MODIFIED — Add ExchangeClosed veto, exchange-specific auction avoidance, exchange-specific spread veto | ~30 |
| `rust_core/src/universe.rs` | MODIFIED — Extended nightly crawl to include 15 European exchanges | ~60 |
| `rust_core/src/broker.rs` | MODIFIED — Exchange-specific tick size rounding, exchange-aware order submission | ~40 |
| `rust_core/src/clock.rs` | MODIFIED — Add per-exchange is_open() checks, European exchange calendar | ~30 |
| `python_brain/ouroboros/universe.py` | MODIFIED — ETP discovery scraping (Leverage Shares, GraniteShares, WisdomTree), European ticker scoring | ~100 |
| `python_brain/ouroboros/fx_rates.py` | NEW — IBKR FX rate fetching for EUR, CHF, SEK, NOK, DKK, PLN | ~60 |
| `docs/checkpoints/PHASE_12_GATE.md` | NEW — Checkpoint gate for Phase 12 approval | ~50 |

---

## Section 11: Key Invariants

These invariants are unconditional. Any violation is a P0 build failure.

1. **European tickers are IN Mode B, not a separate mode.**
   No new AllocationMode enum variant is created. European tickers merge
   into the existing Mode B universe alongside LSE ETPs.

2. **ETP always wins when available for European underlying.**
   If a leveraged ETP exists on LSE for a European stock, the Router
   MUST select the ETP route. Direct trading only occurs when no ETP
   exists. This is enforced in SmartRouter.route() and verified by
   acceptance test #2.

3. **FX conversion cost included in ALL routing decisions.**
   Every non-GBP instrument has its FX cost calculated by FxRateTable
   and included in Router.total_direct_cost(). No direct European trade
   is executed without FX cost factored into the routing decision.

4. **Local transaction taxes included in cost comparison.**
   TransactionTaxRegistry.tax_cost() is called for every direct European
   routing decision. French FTT (0.3%), Italian FTT (0.1%), Spanish FTT
   (0.2%), Swiss stamp tax (0.075%), Greek transaction tax (0.2%),
   Belgian stock exchange tax (0.12%), and Irish stamp duty (1.0%) are
   all modelled.

5. **Exchange-specific execution profiles loaded for every European exchange.**
   All 15 ExchangeProfile entries must be present in ExchangeProfileRegistry
   at boot. Missing profiles cause startup failure (fail-closed).

6. **100-line IBKR constraint still enforced across expanded universe.**
   The Allocator's proptest guarantee from Phase 11 extends to Phase 12.
   At NO point in time are more than 100 IBKR market data lines active,
   even with ~6,000 tickers across all sub-universes.

7. **All adaptive infrastructure from Phase 11 works unchanged with European tickers.**
   ASER scoring, Bayesian win rate, Kelly recalibration, alpha decay,
   walk-forward validation, and universe reclassification treat European
   tickers identically to US equities and LSE ETPs. No special-casing.

8. **Exchange-specific EOD flatten phases.**
   Each position's Chandelier T-35/T-15/T-5 phases are calculated
   relative to its exchange's close time, not a global LSE time.
   Oslo Bors positions flatten 35 minutes before 15:20 UTC, not
   35 minutes before 16:30 UTC.

9. **FX rates must be fresh (<24h) before any routing decision.**
   FxRateTable.has_stale_rates() is checked before every Router
   invocation. Stale rates trigger an immediate refresh from IBKR.

10. **No European scanning in MODE C.**
    European exchanges are closed during MODE C hours (16:30-08:00 UTC).
    Only safety-locked European positions retain streaming lines.
    No new European scans or entries during MODE C.

---

## Section 12: Estimated Effort

~12 hours of agent coding time, broken down as follows:

| Task | Hours |
|------|-------|
| Currency module (currency.rs + FxRateTable + fx_rates.py) | 1.5 |
| Exchange profiles (exchange_profile.rs + exchange_profiles.toml) | 2.0 |
| Transaction tax module (transaction_tax.rs + transaction_taxes.toml) | 1.0 |
| SubUniverseAllocator (Thompson Sampling for Mode B/B+ splits) | 1.5 |
| Router cost comparison extension (FX + tax + exchange-specific) | 1.5 |
| EOD flatten exchange-awareness (Chandelier per-exchange phases) | 1.0 |
| UniverseScanner extension (15 European exchanges, nightly crawl) | 1.5 |
| RiskGate extension (ExchangeClosed veto, per-exchange auction avoidance) | 0.5 |
| Acceptance tests (40 tests) | 1.0 |
| Config files + integration + checkpoint gate | 0.5 |

**Total: ~12 hours**

One checkpoint gate: `docs/checkpoints/PHASE_12_GATE.md`

Phase 12 requires no new architectural patterns — it extends existing
Phase 11 infrastructure with new data sources, currency handling, and
exchange-specific execution profiles. The hardest part is getting the
15 exchange profiles correct (tick sizes, auction times, tax rates).
The adaptive infrastructure (ASER, Bayesian, Kelly, Ouroboros) works
unchanged.

---

## Section 17: Triage Amendments (Post-Gemini Audit 2026-03-09)

The following amendments supersede or extend earlier sections. All are binding.

### Amendment A1: FX Minimum Fee Awareness (P1-8)

**Extends:** Section 3, CurrencyRoute cost comparison

IBKR charges a minimum FX conversion fee of approximately £2.00 per trade.
For small Kelly-sized positions on European equities in a £10k portfolio, this
minimum fee can consume the entire edge.

**New rule in Router cost comparison:**
```python
IBKR_FX_MIN_FEE_GBP = 2.00  # configurable in config.toml

def estimate_european_cost_bps(position_value_gbp, spread_bps, ftt_bps, fx_drag_bps):
    """
    If position_value_gbp < 1000: prefer LSE-listed ETP alternative.
    The minimum FX fee at £500 position = 2.00/500 = 40bps — kills the trade.
    """
    fx_min_fee_bps = (IBKR_FX_MIN_FEE_GBP / position_value_gbp) * 10000
    effective_fx_cost = max(fx_drag_bps, fx_min_fee_bps)
    return spread_bps + ftt_bps + effective_fx_cost

# Hard rule: if position < £1000 AND an LSE ETP equivalent exists → force ETP route
MIN_EUROPEAN_DIRECT_POSITION_GBP = 1000.0
```

### Amendment A2: FTT Intraday Exemption (P2-7)

**Supersedes:** Section 5, TransactionTaxRegistry

French FTT (0.30%) and Italian FTT (0.10%) are typically exempt for intraday
round-trips (buy and sell same day on same account). This materially changes
routing economics for AEGIS (which closes all positions same-session).

**Update TransactionTaxRegistry:**
```toml
# config/european_transaction_taxes.toml

[FR]
stamp_duty_bps = 0.0
ftt_bps = 30.0
ftt_applies_intraday = false  # AMENDED: intraday exempt

[IT]
stamp_duty_bps = 0.0
ftt_bps = 10.0
ftt_applies_intraday = false  # AMENDED: intraday exempt

[ES]
stamp_duty_bps = 0.0
ftt_bps = 20.0
ftt_applies_intraday = true   # Spain FTT applies intraday

[UK]
stamp_duty_bps = 50.0
ftt_bps = 0.0
ftt_applies_intraday = true   # SDRT always applies
```

Router cost comparison uses `ftt_bps = 0.0` for FR and IT positions when
`session_type == INTRADAY` (i.e., position opened and closed same mode).

### Amendment A3: XETRA Closing Auction Cutoff (P1 note)

**Extends:** Section 6, MODE B closing behaviour

XETRA continuous order book freezes at exactly **16:25:00 CET** (15:25 UTC in winter,
14:25 UTC in summer). The T-5 rule must fire at 15:20 UTC (winter) / 14:20 UTC (summer)
for XETRA — not the generic T-5 = 5 minutes before mode close.

**Update ExchangeProfile for XETRA:**
```toml
[XETRA]
continuous_close_utc_offset_secs = -300  # 5 minutes before official close
# i.e., closing_auction_entry_cutoff = official_close - 5min
# use chrono_tz for DST-correct calculation
```

### Amendment A4: Dual-Listed Security — Single Subscription Rule

**Extends:** Section 8, SubUniverseAllocator

If a security trades on both Euronext Paris and XETRA (e.g., LVMH, Airbus),
the Router subscribes to EXACTLY ONE venue: the one with higher 5-day median ADV.

```rust
// If ticker appears in both euronext_paris AND xetra universe lists:
// Subscribe only to the higher-ADV venue. Never both.
// This is enforced in SubUniverseAllocator::deduplicate_cross_listings()
pub fn deduplicate_cross_listings(candidates: &mut Vec<EuropeanTicker>) {
    // Group by ISIN. For duplicates, keep only highest ADV entry.
    let mut by_isin: HashMap<String, EuropeanTicker> = HashMap::new();
    for ticker in candidates.drain(..) {
        by_isin.entry(ticker.isin.clone())
            .and_modify(|existing| {
                if ticker.adv_5d > existing.adv_5d {
                    *existing = ticker;
                }
            })
            .or_insert(ticker);
    }
    *candidates = by_isin.into_values().collect();
}
```

### Amendment A5: SubUniverseAllocator Minimum Fraction — Volatility Override

**Extends:** Section 8, minimum_fraction = 0.15

The 15% minimum forces 15 lines to European equities even during flat markets.

**New rule:** minimum_fraction is suspended when:
- HMM regime = BEAR_VOLATILE AND
- VIX > 25 AND
- European average realised vol < 0.5% per day (flat)

When suspended: minimum_fraction = 0.0 (lines freed for MODE B+ US universe).
Suspension logged to Telegram 🔄 SYSTEM SHIFT.

### Amendment A6: Tick Size Dynamic Reload

**Extends:** Section 4, tick_size_for_price()

Static TOML tick size bands will break after MiFID II liquidity band reclassifications
or after corporate actions (splits).

**Add to Phase 12 Ouroboros pipeline:** Monthly `reqContractDetails` refresh for
all European equities. If `minTick` from IBKR differs from TOML value by > 10%:
update TOML atomically (write to .tmp, rename). Log SYSTEM SHIFT alert.

### Amendment A7: IBKR Commission Model — Tiered vs Fixed

**Extends:** Section 5, cost estimation

IBKR offers two commission structures for European equities:
- **Tiered**: 0.05% of trade value, min €1.25, max 0.5% (generally cheaper for small trades)
- **Fixed**: flat €1.75 per order

The Router must model both and use the actual account setting. Read from
`reqAccountSummary` key `CommissionCurrency` and `CommissionModel` at startup.

```python
# python_brain/ibkr_commission.py
def estimate_ibkr_commission_eur(trade_value_eur: float, model: str) -> float:
    if model == "TIERED":
        return max(1.25, min(trade_value_eur * 0.0005, trade_value_eur * 0.005))
    else:  # FIXED
        return 1.75
```

### Amendment A8: FTT Market Cap Gate (FLAW-20 Fix)

**Problem:** The spec applies French FTT (0.3%) and Italian FTT (0.1%) as flat
rates on all French/Italian trades. In practice, France's FTT applies ONLY to
equities with market capitalisation > €1B EUR (~140 companies). Italy's FTT
applies only to equities > €500M market cap. Trading Renault (market cap was
below €1B in 2023–2024) would be incorrectly FTT-taxed under the flat model.

**Fix:** Add a market-cap flag to `TransactionTax` struct and check it at
order submission time:

```rust
/// Extended TransactionTax with market cap threshold enforcement.
pub struct TransactionTax {
    pub exchange: Exchange,
    pub rate_bps: f64,
    pub applies_intraday: bool,
    pub market_cap_threshold_eur: Option<f64>,  // None = applies to all
}

impl TransactionTax {
    /// Returns the applicable FTT rate for this specific position.
    /// Returns 0.0 if market cap is below the threshold.
    pub fn effective_rate_bps(&self, market_cap_eur: f64) -> f64 {
        if let Some(threshold) = self.market_cap_threshold_eur {
            if market_cap_eur < threshold { return 0.0; }
        }
        self.rate_bps
    }
}
```

Market cap values are pulled from `reqContractDetails` during the nightly
Ouroboros European universe crawl and stored in `exchange_profiles.toml`.
If market cap is unavailable (IBKR returns 0): apply FTT conservatively
(assume eligible) to avoid under-reporting costs.

### Amendment A9: Market-Hours-Aware SubUniverseAllocator Minimum Fraction

**Problem (FLAW-29):** The global `min_fraction = 0.15` forces 15 lines to
European equities even when those exchanges are outside their trading hours.
At 08:00 UTC, all 15 exchanges are opening simultaneously and get minimum
allocations — this is correct. At 13:30 UTC (MODE B only), Oslo Børs closes
at 16:20 CET (15:20 UTC) and Athens Exchange opens at 10:00 CET (09:00 UTC
— already closed). Lines allocated to closed exchanges are wasted.

**Fix:** Replace global `min_fraction` with an exchange-activity-weighted
minimum:

```rust
impl SubUniverseAllocator {
    /// Returns the minimum line allocation for this sub-universe,
    /// scaled by the fraction of exchanges currently within trading hours.
    pub fn active_min_fraction(&self, now_utc: NaiveTime, exchange_profiles: &[ExchangeProfile]) -> f64 {
        let active = exchange_profiles.iter()
            .filter(|ex| ex.is_within_trading_hours(now_utc))
            .count();
        let total = exchange_profiles.len().max(1);
        // Scale min_fraction by fraction of exchanges currently open
        // If 10/15 exchanges are open: 0.15 * (10/15) = 0.10
        self.base_min_fraction * (active as f64 / total as f64)
    }
}
```

Exchanges outside their trading hours contribute zero to the minimum fraction
calculation. This frees lines for active scanning without starving the sub-
universe when markets are open.

### Amendment: Updated Phase 12 Gate Criteria

Before Phase 13 begins, ALL of the following must be verified:

- [ ] All 40 original Phase 12 acceptance tests green
- [ ] FX minimum fee: positions < £1000 routed to LSE ETP, verified by router test
- [ ] FTT intraday exemption: FR + IT positions cost-compared correctly (0 FTT)
- [ ] XETRA T-5 cutoff: verified correct UTC time accounting for DST
- [ ] Dual-listing dedup: ISIN dedup verified (LVMH not subscribed twice)
- [ ] Commission model: tiered vs fixed correctly loaded from account settings
- [ ] Tick size reload: mock MiFID reclassification triggers atomic TOML update
- [ ] SubUniverseAllocator min fraction override: VIX>25 + flat market suspends 15% floor
- [ ] FTT market cap gate: Renault (low market cap) correctly gets 0.0% FTT; LVMH gets 30bps
- [ ] Active min fraction: at 13:30 UTC, exchanges outside trading hours get zero minimum
- [ ] 5 paper trading days: MODE B + B+ with European universe, no ISA violations

---

*Section 17 added 2026-03-09 — Gemini Adversarial Audit Integration*
*Amendments A8–A9 added 2026-03-09 — Claude Self-Analysis Triage Integration*
*FTT market-cap gate, market-hours-aware SubUniverseAllocator min fraction.*
*See GEMINI_TRIAGE.md and AEGIS_SELF_ANALYSIS_TRIAGE.md for full rationale.*
