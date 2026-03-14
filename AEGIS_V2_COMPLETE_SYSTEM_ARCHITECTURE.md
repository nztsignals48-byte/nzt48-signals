# AEGIS V2 — COMPLETE SYSTEM ARCHITECTURE

**Date**: March 13, 2026
**Purpose**: Explain Universe, Feeds (6 markets), Executioner, Ouroboros, and Dynamic Allocation
**Audience**: Technical team, engineers, architects

---

## SYSTEM OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│                     AEGIS V2 TRADING ENGINE                            │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                        UNIVERSE                                 │   │
│  │  (Asset selection + metadata + regime classification)          │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────┴──────────────────────────────────────┐   │
│  │                                                                 │   │
│  │              FEEDS (6 Markets, Real-time Data)                 │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │   │
│  │  │ LSE Leveraged│  │ LSE Inverse  │  │ Euro Stocks  │  ...    │   │
│  │  │ (08:00-14:30)│  │ (08:00-14:30)│  │ (08:00-16:30)│         │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘         │   │
│  │                                                                 │   │
│  │  Each feed: IBKR → yfinance → Polygon → Redis (failover)      │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────┴──────────────────────────────────────┐   │
│  │                                                                 │   │
│  │              SIGNAL ENGINE (25 Phases)                         │   │
│  │  Phase 4: White Reality Check                                  │   │
│  │  Phase 5: Regime Detection                                     │   │
│  │  Phase 6: Volatility Scaling                                   │   │
│  │  Phase 7: Confidence Scoring (8-indicator consensus)           │   │
│  │  Phase 8: Pre-Conditions Gate                                  │   │
│  │  Phase 9: Position Sizer (with leverage prioritization)        │   │
│  │                                                                 │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────┴──────────────────────────────────────┐   │
│  │                                                                 │   │
│  │              EXECUTIONER                                       │   │
│  │  Phase 10: Execution Quality                                   │   │
│  │  Phase 15: Order Routing (underlying→ETP mapping)              │   │
│  │  Phase 19: Risk Manager                                        │   │
│  │  Phase 20: Reconciliation Auditor                              │   │
│  │                                                                 │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────┴──────────────────────────────────────┐   │
│  │                                                                 │   │
│  │              OUROBOROS (Nightly Learning)                      │   │
│  │  Phase 24: ML Adaptation                                       │   │
│  │  Phase 23: Performance Attribution                             │   │
│  │  Phase 22: DQN Signal Weighting                                │   │
│  │                                                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 1. UNIVERSE

### Purpose
The Universe is the **asset selection and metadata engine**. It:
- Defines which assets are tradable (ISA-eligible only)
- Maintains metadata (leverage, ETP mapping, sector classification)
- Classifies assets by market and trading hours
- Tracks regime state for each market

### Components

#### 1.1 Asset Registry
```python
UNIVERSE = {
    'LSE_LEVERAGED_3X': [
        {'symbol': 'NVD3.L', 'underlying': 'NVDA', 'leverage': 3.0, 'decay_daily': 0.08},
        {'symbol': 'QQQ3.L', 'underlying': 'QQQ', 'leverage': 3.0, 'decay_daily': 0.08},
        {'symbol': '3LUS.L', 'underlying': 'SPX', 'leverage': 3.0, 'decay_daily': 0.08},
        {'symbol': 'TSL3.L', 'underlying': 'TSLA', 'leverage': 3.0, 'decay_daily': 0.08},
        {'symbol': '3SEM.L', 'underlying': 'SOX', 'leverage': 3.0, 'decay_daily': 0.08},
    ],

    'LSE_LEVERAGED_5X': [
        {'symbol': 'QQQS.L', 'underlying': 'QQQ', 'leverage': 5.0, 'decay_daily': 0.12},
        {'symbol': '3USS.L', 'underlying': 'SPX', 'leverage': 5.0, 'decay_daily': 0.12},
        {'symbol': 'QQQ5.L', 'underlying': 'QQQ', 'leverage': 5.0, 'decay_daily': 0.12},
        {'symbol': 'SP5L.L', 'underlying': 'FTSE', 'leverage': 5.0, 'decay_daily': 0.12},
        {'symbol': 'GPT3.L', 'underlying': 'GPT', 'leverage': 3.0, 'decay_daily': 0.08},
    ],

    'LSE_INVERSE_5X': [
        {'symbol': 'QQQS.L', 'underlying': 'QQQ', 'leverage': -5.0, 'inverse': True, 'decay_daily': 0.15},
        # Inverse ETPs for hedging during RISK_OFF
    ],

    'LSE_LONG_1X': [
        {'symbol': 'VUSA.L', 'underlying': 'SPX', 'leverage': 1.0, 'sector': 'US_EQUITY'},
        {'symbol': 'VGOV.L', 'underlying': 'US_BONDS', 'leverage': 1.0, 'sector': 'FIXED_INCOME'},
        # 1x direct stocks, LSE-listed
    ],

    'EURO_STOCKS': [
        {'symbol': 'SAP', 'underlying': 'SAP.DE', 'leverage': 1.0, 'sector': 'TECHNOLOGY'},
        {'symbol': 'SIEMENS', 'underlying': 'SIE.DE', 'leverage': 1.0, 'sector': 'INDUSTRIAL'},
        {'symbol': 'ASML', 'underlying': 'ASML.AS', 'leverage': 1.0, 'sector': 'TECHNOLOGY'},
        # European large-cap stocks
    ],

    'US_EQUITY': [
        {'symbol': 'SPY', 'underlying': 'SPX', 'leverage': 1.0, 'sector': 'US_EQUITY'},
        {'symbol': 'QQQ', 'underlying': 'QQQ', 'leverage': 1.0, 'sector': 'TECHNOLOGY'},
        {'symbol': 'IWM', 'underlying': 'RUT', 'leverage': 1.0, 'sector': 'SMALL_CAP'},
        # US stocks (Phase 3 only, 1x leverage)
    ],

    'ASIA_LONG': [
        {'symbol': 'EWJ', 'underlying': 'NKY', 'leverage': 1.0, 'sector': 'JAPAN'},
        {'symbol': 'EWH', 'underlying': 'HSI', 'leverage': 1.0, 'sector': 'HONG_KONG'},
        {'symbol': 'FXI', 'underlying': 'CSI300', 'leverage': 1.0, 'sector': 'CHINA'},
        # Asia Pacific stocks (Phase 4 overnight, 1x leverage)
    ],
}
```

#### 1.2 Asset Metadata
```python
ASSET_METADATA = {
    'NVD3.L': {
        'isin': 'GB0008374308',  # For ISA verification
        'isa_eligible': True,
        'underlying': 'NVDA',
        'leverage': 3.0,
        'decay_daily': 0.0008,  # 0.08% daily decay
        'sector': 'SEMICONDUCTORS',
        'market': 'LSE_LEVERAGED',
        'trading_hours': (8, 16, 30),  # 08:00-16:30 UK
        'optimal_entry': (9, 0),  # 09:00 UK (after US pre-market)
        'optimal_exit': (16, 15),  # 16:15 UK (before US close impact)
        'bid_ask_spread': 0.0015,  # 15 bps typical
        'liquidity_rank': 1,  # Very liquid
        'min_position_size': 100,  # Shares
        'max_position_size': 5000,  # Shares
    },
    'QQQ': {
        'isa_eligible': False,  # US-listed, only in Phase 3-4
        'underlying': 'QQQ',
        'leverage': 1.0,
        'sector': 'TECHNOLOGY',
        'market': 'US_EQUITY',
        'trading_hours': (14, 30, 21, 0),  # 14:30-21:00 UK = 09:30-16:00 US
        'bid_ask_spread': 0.001,  # 10 bps
        'liquidity_rank': 1,
        'min_position_size': 10,  # Shares
        'max_position_size': 1000,
    },
}
```

#### 1.3 Regime Classification
```python
class UniverseRegimeClassifier:
    """
    Classifies each market into one of 5 regimes.
    Each regime has different signal thresholds and leverage multipliers.
    """

    REGIMES = {
        'TRENDING_UP': {
            'vix_threshold': (0, 25),
            'vol_regressor': 'low',
            'leverage_multiplier': 0.6,  # Aggressive
            'signal_threshold': 5.5,  # Lower threshold, more trades
            'confidence_required': 'high',
        },
        'TRENDING_DOWN': {
            'vix_threshold': (25, 35),
            'vol_regressor': 'medium',
            'leverage_multiplier': 0.4,  # Cautious
            'signal_threshold': 6.0,
            'confidence_required': 'very_high',
        },
        'RANGE': {
            'vix_threshold': (15, 25),
            'vol_regressor': 'medium',
            'leverage_multiplier': 0.25,  # Very cautious
            'signal_threshold': 7.0,  # Higher threshold, fewer trades
            'confidence_required': 'extreme',
        },
        'HIGH_VOL': {
            'vix_threshold': (30, 50),
            'vol_regressor': 'high',
            'leverage_multiplier': 0.15,  # Defensive
            'signal_threshold': 7.5,
            'confidence_required': 'extreme',
        },
        'RISK_OFF': {
            'vix_threshold': (50, 100),
            'vol_regressor': 'extreme',
            'leverage_multiplier': 0.0,  # Zero leverage (or inverse hedge only)
            'signal_threshold': 8.5,
            'confidence_required': 'extreme',
            'mode': 'defensive_only',  # Inverse ETPs or flat
        },
    }

    def classify(self, vix, vol_regime, credit_spread, fear_gauge):
        """
        Inputs:
        - vix: Current VIX level (real-time from IBKR)
        - vol_regime: Realized volatility from past 20 days
        - credit_spread: HY OAS (default risk indicator)
        - fear_gauge: Fear & Greed Index (sentiment)

        Output: Regime classification for each market
        """
        if vix > 50:
            return 'RISK_OFF'
        elif vix > 30:
            if vol_regime > 0.25:
                return 'HIGH_VOL'
            else:
                return 'TRENDING_DOWN'
        elif vix > 20:
            if vol_regime < 0.12:
                return 'TRENDING_UP'
            else:
                return 'RANGE'
        else:
            return 'TRENDING_UP'
```

#### 1.4 Universe Initialization
```python
class Universe:
    """
    Initialize at startup. Refresh market metadata daily.
    """

    def __init__(self):
        self.assets = self._load_assets()  # From ASSET_METADATA
        self.regime_classifier = UniverseRegimeClassifier()
        self.per_market_regime = {}  # Populated by regime detector
        self.isa_eligible_cache = self._build_isa_eligible_cache()

    def _load_assets(self):
        """Load all ISA-eligible assets from UNIVERSE registry."""
        return UNIVERSE

    def _build_isa_eligible_cache(self):
        """Pre-compute ISA-eligible assets for compliance gate (Phase 3)."""
        return {
            symbol: metadata
            for symbol, metadata in ASSET_METADATA.items()
            if metadata.get('isa_eligible', False)
        }

    def classify_all_markets(self, vix, vol_regime, credit_spread, fear_gauge):
        """
        Called by regime detector (Phase 5) at market open.
        Populates per_market_regime for all 6 markets.

        Example output:
        {
            'LSE_LEVERAGED_3X': 'TRENDING_UP',
            'LSE_LEVERAGED_5X': 'TRENDING_UP',
            'LSE_INVERSE_5X': 'RISK_OFF',
            'LSE_LONG_1X': 'TRENDING_UP',
            'EURO_STOCKS': 'RANGE',
            'US_EQUITY': 'HIGH_VOL',
            'ASIA_LONG': 'TRENDING_DOWN',
        }
        """
        self.per_market_regime = {
            market: self.regime_classifier.classify(vix, vol_regime, credit_spread, fear_gauge)
            for market in self.assets.keys()
        }
        return self.per_market_regime

    def get_tradable_assets(self, market, regime):
        """
        Return list of assets for a specific market and regime.

        Example:
        get_tradable_assets('LSE_LEVERAGED_3X', 'TRENDING_UP')
        → [NVD3.L, QQQ3.L, 3LUS.L, TSL3.L, 3SEM.L]
        """
        assets = self.assets.get(market, [])

        # Filter by regime rules
        if regime == 'RISK_OFF':
            # Only inverse ETPs in RISK_OFF
            return [a for a in assets if a.get('inverse', False)]
        else:
            # All long ETPs in other regimes
            return [a for a in assets if not a.get('inverse', False)]

    def get_asset_metadata(self, symbol):
        """Lookup metadata for a specific asset."""
        return ASSET_METADATA.get(symbol)
```

### Key Functions in Universe

| Function | Purpose | Called By | Frequency |
|----------|---------|-----------|-----------|
| `__init__()` | Load assets, build ISA cache | main.py startup | Once |
| `classify_all_markets()` | Determine regime for each market | Phase 5 (Regime Detector) | Every 60s |
| `get_tradable_assets()` | Filter assets by market + regime | Phase 7 (Confidence Scorer) | Per signal |
| `get_asset_metadata()` | Lookup asset details | Phase 9 (Position Sizer) | Per trade |
| `_build_isa_eligible_cache()` | Pre-compute ISA assets | Phase 3 (Compliance) | At startup |

---

## 2. FEEDS (6 Markets, Real-time Data)

### Purpose
Feeds provide **real-time market data** from multiple sources with automatic failover:
1. Primary: IBKR (Interactive Brokers) — live, <100ms latency
2. Secondary: yfinance — free, 15-min delayed
3. Tertiary: Polygon.io — pro data, real-time
4. Cache: Redis — local state machine

### Market Structure

```
┌────────────────────────────────────────────────────────────────────┐
│                    6 INDEPENDENT MARKET FEEDS                      │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│ FEED 1: LSE_LEVERAGED_3X (08:00-16:30 UK)                         │
│  ├─ Symbols: NVD3.L, QQQ3.L, 3LUS.L, TSL3.L, 3SEM.L              │
│  ├─ Leverage: 3x daily reset, -9.7% annual decay                 │
│  ├─ Update Freq: IBKR 1sec, yfinance 5min, Polygon 1min         │
│  └─ State: Per-asset (OHLCV, Greeks, funding costs)              │
│                                                                    │
│ FEED 2: LSE_LEVERAGED_5X (08:00-16:30 UK)                         │
│  ├─ Symbols: QQQS.L, 3USS.L, QQQ5.L, SP5L.L, GPT3.L              │
│  ├─ Leverage: 5x daily reset, -14.2% annual decay                │
│  ├─ Update Freq: IBKR 1sec, yfinance 5min, Polygon 1min         │
│  └─ State: Per-asset (OHLCV, Greeks, funding costs)              │
│                                                                    │
│ FEED 3: LSE_INVERSE_5X (08:00-16:30 UK, RISK_OFF only)            │
│  ├─ Symbols: Inverse QQQ, Inverse SPX (hedges)                   │
│  ├─ Leverage: -5x (short exposure)                               │
│  ├─ Update Freq: IBKR 1sec (high priority in RISK_OFF)          │
│  └─ State: Hedging only (not primary trading mode)               │
│                                                                    │
│ FEED 4: EURO_STOCKS (08:00-16:30 UK)                              │
│  ├─ Symbols: SAP, SIEMENS, ASML, ADYEN, RELIANCE, etc.          │
│  ├─ Leverage: 1x (no leverage)                                   │
│  ├─ Update Freq: yfinance 5min, Polygon 1min                     │
│  └─ State: Currency conversion (EUR→GBP at bid-ask midpoint)     │
│                                                                    │
│ FEED 5: US_EQUITY (14:30-21:00 UK = 09:30-16:00 US)               │
│  ├─ Symbols: SPY, QQQ, IWM, NVDA, TSLA, etc.                     │
│  ├─ Leverage: 1x (ISA forbids margin)                            │
│  ├─ Update Freq: IBKR 1sec, yfinance 1min, Polygon real-time    │
│  └─ State: GBP conversion (USD→GBP at FX rate)                   │
│                                                                    │
│ FEED 6: ASIA_LONG (23:50-08:00 UTC = overnight UK)                │
│  ├─ Symbols: EWJ, EWH, FXI, SNGSP, ASIANPAC                      │
│  ├─ Leverage: 1x (overnight, lower liquidity)                    │
│  ├─ Update Freq: yfinance 5min, Polygon delayed                  │
│  └─ State: GBP conversion (JPY, HKD, CNY→GBP)                    │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

#### 2.1 Feed Manager Class
```python
class FeedManager:
    """
    Manages real-time data streams for all 6 markets.
    Implements tiered fallback: IBKR → yfinance → Polygon → Redis cache.
    """

    MARKET_FEEDS = {
        'LSE_LEVERAGED_3X': {
            'symbols': ['NVD3.L', 'QQQ3.L', '3LUS.L', 'TSL3.L', '3SEM.L'],
            'update_freq_ms': 1000,  # 1 sec from IBKR
            'trading_hours': (8, 0, 16, 30),  # 08:00-16:30 UK
            'primary_source': 'IBKR',
            'secondary_source': 'yfinance',
            'cache_ttl_sec': 5,
        },
        'LSE_LEVERAGED_5X': {
            'symbols': ['QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L', 'GPT3.L'],
            'update_freq_ms': 1000,
            'trading_hours': (8, 0, 16, 30),
            'primary_source': 'IBKR',
            'secondary_source': 'yfinance',
            'cache_ttl_sec': 5,
        },
        'LSE_INVERSE_5X': {
            'symbols': ['INVERSE_QQQ', 'INVERSE_SPX'],  # Mapped to real symbols
            'update_freq_ms': 1000,
            'trading_hours': (8, 0, 16, 30),
            'primary_source': 'IBKR',
            'secondary_source': 'yfinance',
            'cache_ttl_sec': 5,
            'mode': 'hedging_only',  # Active only in RISK_OFF
        },
        'EURO_STOCKS': {
            'symbols': ['SAP', 'SIEMENS', 'ASML', 'ADYEN', 'RELIANCE'],
            'update_freq_ms': 60000,  # 1 min (lower frequency)
            'trading_hours': (8, 0, 16, 30),
            'primary_source': 'yfinance',
            'secondary_source': 'Polygon',
            'cache_ttl_sec': 60,
            'fx_pair': 'EURUSD',  # Convert EUR to GBP
        },
        'US_EQUITY': {
            'symbols': ['SPY', 'QQQ', 'IWM', 'NVDA', 'TSLA'],
            'update_freq_ms': 1000,  # 1 sec from IBKR
            'trading_hours': (14, 30, 21, 0),  # 14:30-21:00 UK
            'primary_source': 'IBKR',
            'secondary_source': 'Polygon',
            'cache_ttl_sec': 5,
            'fx_pair': 'GBPUSD',  # Convert USD to GBP
        },
        'ASIA_LONG': {
            'symbols': ['EWJ', 'EWH', 'FXI', 'SNGSP', 'ASIANPAC'],
            'update_freq_ms': 300000,  # 5 min (overnight, lower priority)
            'trading_hours': (23, 50, 8, 0),  # 23:50-08:00 UTC
            'primary_source': 'yfinance',
            'secondary_source': 'Redis_cache',
            'cache_ttl_sec': 300,
            'fx_pairs': ['GBPJPY', 'GBPHKD', 'GBPCNY'],  # Multi-currency
        },
    }

    def __init__(self, ibkr_client, redis_client):
        self.ibkr = ibkr_client
        self.redis = redis_client
        self.market_states = {}  # Per-market state machines
        self.data_quality_metrics = {}  # Latency, staleness, errors

    def start_all_feeds(self):
        """
        Start all 6 feeds at market open.
        Called by Phase 25 (Live Orchestrator).
        """
        for market, config in self.MARKET_FEEDS.items():
            self._start_feed(market, config)

    def _start_feed(self, market, config):
        """
        Start a single feed.
        Spawns threads for:
        1. IBKR data collection (primary)
        2. yfinance fallback
        3. Polygon fallback
        4. Redis caching
        """
        feed_thread = threading.Thread(
            target=self._feed_loop,
            args=(market, config),
            daemon=True,
            name=f'feed-{market}'
        )
        feed_thread.start()
        logger.info(f"Started feed for {market}")

    def _feed_loop(self, market, config):
        """
        Continuous loop: fetch data → validate → cache → broadcast.
        Runs in separate thread, updates every 1sec (IBKR) or 5min (yfinance).
        """
        while True:
            try:
                # Step 1: Fetch from primary source
                data = self._fetch_primary(market, config)

                if data is None:
                    # Step 2: Fallback to secondary
                    data = self._fetch_secondary(market, config)

                if data is None:
                    # Step 3: Fallback to cache
                    data = self._fetch_cache(market, config)

                if data is not None:
                    # Step 4: Cache and broadcast
                    self._cache_data(market, data)
                    self._broadcast_data(market, data)
                    self._update_quality_metrics(market, 'success')
                else:
                    # All sources failed
                    self._update_quality_metrics(market, 'stale')
                    logger.warning(f"Feed {market}: all sources failed, using cached data")

            except Exception as e:
                logger.error(f"Feed loop error for {market}: {e}")
                self._update_quality_metrics(market, 'error')

            # Sleep based on update frequency
            sleep_ms = config['update_freq_ms']
            time.sleep(sleep_ms / 1000.0)

    def _fetch_primary(self, market, config):
        """
        Fetch from primary source (IBKR for LSE+US, yfinance for others).
        Timeout: 500ms (must return before next update).
        """
        primary = config['primary_source']
        symbols = config['symbols']

        try:
            if primary == 'IBKR':
                # IBKR API: real-time market data
                data = {
                    symbol: {
                        'last_price': self.ibkr.get_last(symbol),
                        'bid': self.ibkr.get_bid(symbol),
                        'ask': self.ibkr.get_ask(symbol),
                        'volume': self.ibkr.get_volume(symbol),
                        'timestamp': time.time(),
                    }
                    for symbol in symbols
                }
                return data

            elif primary == 'yfinance':
                # yfinance: slower, but free and reliable
                data = {
                    symbol: yfinance.Ticker(symbol).history(period='1d')
                    for symbol in symbols
                }
                return data

        except Exception as e:
            logger.debug(f"Primary source {primary} failed for {market}: {e}")
            return None

    def _fetch_secondary(self, market, config):
        """
        Fallback to secondary source if primary fails.
        Timeout: 1000ms.
        """
        secondary = config['secondary_source']
        symbols = config['symbols']

        try:
            if secondary == 'yfinance':
                # Same as primary yfinance
                return self._fetch_primary(market, {**config, 'primary_source': 'yfinance'})

            elif secondary == 'Polygon':
                # Polygon.io API (real-time, pro plan)
                data = {
                    symbol: self._fetch_polygon(symbol)
                    for symbol in symbols
                }
                return data

            elif secondary == 'Redis_cache':
                # Return cached data (already handled in _fetch_cache)
                return None

        except Exception as e:
            logger.debug(f"Secondary source {secondary} failed for {market}: {e}")
            return None

    def _fetch_polygon(self, symbol):
        """
        Fetch from Polygon.io API.
        Includes crypto quotes, options, and historical data.
        """
        # Call Polygon API (requires API key)
        response = polygon_client.get_last_quote(f"X:{symbol}")
        return {
            'bid': response.bid,
            'ask': response.ask,
            'last': response.last,
            'timestamp': response.timestamp,
        }

    def _fetch_cache(self, market, config):
        """
        Fetch from Redis cache.
        Returns most recent data if primary/secondary failed.
        """
        cache_key = f"feed:{market}:data"
        cached_data = self.redis.get(cache_key)
        return json.loads(cached_data) if cached_data else None

    def _cache_data(self, market, data):
        """
        Store data in Redis with TTL.
        TTL varies by market (5s for LSE, 60s for Euro, 300s for Asia).
        """
        cache_key = f"feed:{market}:data"
        ttl = self.MARKET_FEEDS[market]['cache_ttl_sec']
        self.redis.setex(cache_key, ttl, json.dumps(data))

    def _broadcast_data(self, market, data):
        """
        Broadcast data to all subscribers (Signal Engine, Risk Manager, etc.).
        Uses Redis Pub/Sub for low-latency updates.
        """
        channel = f"data:{market}:update"
        self.redis.publish(channel, json.dumps(data))

    def _update_quality_metrics(self, market, status):
        """
        Track data quality: latency, staleness, error rate.
        Used by Phase 25 (Live Orchestrator) for monitoring.
        """
        if market not in self.data_quality_metrics:
            self.data_quality_metrics[market] = {
                'updates_received': 0,
                'updates_failed': 0,
                'avg_latency_ms': 0,
                'last_update_ts': 0,
                'staleness_ms': 0,
            }

        if status == 'success':
            self.data_quality_metrics[market]['updates_received'] += 1
            self.data_quality_metrics[market]['last_update_ts'] = time.time()
        elif status == 'error':
            self.data_quality_metrics[market]['updates_failed'] += 1
```

#### 2.2 Feed State Machine (Per Market)
```python
class FeedStateMachine:
    """
    Each market has independent state machine.
    Tracks: connected, disconnected, stale, error, recovering.
    """

    STATES = {
        'CONNECTED': {
            'data_quality': 'good',
            'action': 'proceed_with_trading',
            'timeout': 5000,  # 5 sec before stale
        },
        'STALE': {
            'data_quality': 'degraded',
            'action': 'use_cached_data',
            'timeout': 30000,  # 30 sec before error
        },
        'ERROR': {
            'data_quality': 'critical',
            'action': 'halt_trading_market',
            'timeout': 60000,  # 60 sec before emergency stop
        },
        'RECOVERING': {
            'data_quality': 'recovering',
            'action': 'retry_connect',
            'timeout': 10000,  # 10 sec retry interval
        },
    }

    def __init__(self, market):
        self.market = market
        self.state = 'CONNECTED'
        self.last_update_ts = time.time()

    def on_data_received(self):
        """Update timestamp when data received."""
        self.last_update_ts = time.time()
        self.state = 'CONNECTED'

    def check_staleness(self):
        """Check if data is stale (no update for TTL)."""
        elapsed = (time.time() - self.last_update_ts) * 1000
        timeout = self.STATES['CONNECTED']['timeout']

        if elapsed > timeout:
            self.state = 'STALE'
            return True
        return False

    def handle_error(self):
        """Handle connection error."""
        self.state = 'ERROR'
        return self.STATES['ERROR']['action']
```

### Key Functions in Feeds

| Function | Purpose | Called By | Frequency |
|----------|---------|-----------|-----------|
| `start_all_feeds()` | Initialize all 6 feeds | Phase 25 (Orchestrator) | Market open |
| `_feed_loop()` | Continuous data collection | Spawned thread | Every 1-5 sec |
| `_fetch_primary()` | Get data from IBKR/yfinance | _feed_loop | Every update |
| `_fetch_secondary()` | Fallback if primary fails | _feed_loop | On error |
| `_fetch_cache()` | Return cached data | _feed_loop | On all fail |
| `_broadcast_data()` | Publish to Redis Pub/Sub | _feed_loop | Per update |
| `check_staleness()` | Monitor data freshness | Phase 25 (Orchestrator) | Every 60s |

---

## 3. SIGNAL ENGINE (Phases 4-9)

### Purpose
The Signal Engine is the **core decision-making system**. It:
1. Validates signals (White Reality Check, Phase 4)
2. Detects market regime (HMM, Phase 5)
3. Scales leverage by volatility (Phase 6)
4. Scores confidence (8-indicator consensus, Phase 7)
5. Checks pre-conditions (ISA compliance, Phase 8)
6. Sizes positions (with leverage prioritization, Phase 9)

### Phase 4: White Reality Check
```python
class WhiteRealityCheck:
    """
    Phase 4: Validate that signals are statistically significant.
    Goal: Reject 80% of candidate signals as false positives.

    Methods:
    1. Bootstrap hypothesis testing (Efron, 1979)
    2. Deflated Sharpe Ratio (Bailey et al., 2014)
    3. Regime-conditional testing (all 5 regimes must pass)
    """

    def __init__(self, num_bootstrap_samples=1000):
        self.num_bootstrap_samples = num_bootstrap_samples

    def test_signal(self, signal_returns, backtest_period='1y', regime='TRENDING_UP'):
        """
        Test if a signal is statistically significant.

        Inputs:
        - signal_returns: array of returns when signal fired (np.array)
        - backtest_period: '1y', '2y', '3y'
        - regime: 'TRENDING_UP', 'TRENDING_DOWN', 'RANGE', 'HIGH_VOL', 'RISK_OFF'

        Output:
        - is_significant: boolean (True if DSR >0.6 and passes bootstrap)
        - deflated_sharpe: float (0.0-2.0)
        - bootstrap_pvalue: float (0.0-1.0)
        """

        # Step 1: Calculate Deflated Sharpe Ratio
        dsr = self._deflated_sharpe_ratio(signal_returns)

        if dsr < 0.6:
            # Fail DSR test (too much overfitting)
            return False, dsr, 1.0

        # Step 2: Bootstrap hypothesis test
        null_mean = 0.0  # H0: signal has zero expected return
        pvalue = self._bootstrap_test(signal_returns, null_mean)

        if pvalue > 0.05:
            # Fail bootstrap test (not statistically significant)
            return False, dsr, pvalue

        # Step 3: Regime-conditional test
        # (Test in all 5 regimes, require >0% WR in each)
        regime_wr = self._test_all_regimes(signal_returns)

        if any(wr < 0.4 for wr in regime_wr.values()):
            # Fail regime test (not robust across markets)
            return False, dsr, 0.9

        # All tests passed
        return True, dsr, pvalue

    def _deflated_sharpe_ratio(self, signal_returns):
        """
        Calculate Deflated Sharpe Ratio (Bailey et al., 2014).
        Adjusts for overfitting, data mining bias, and multiple testing.

        Formula: DSR = SR * sqrt((1 - gamma) / (T - 1))
        where:
        - SR = observed Sharpe ratio
        - gamma = skewness and kurtosis adjustment
        - T = sample size
        """
        sr = self._sharpe_ratio(signal_returns)
        skewness = scipy.stats.skew(signal_returns)
        kurtosis = scipy.stats.kurtosis(signal_returns)

        gamma = (skewness * sr) + (kurtosis * sr**2 / 4)

        dsr = sr * np.sqrt((1 - gamma) / (len(signal_returns) - 1))
        return max(0.0, dsr)  # DSR can't be negative

    def _sharpe_ratio(self, signal_returns, rf=0.0):
        """Calculate Sharpe ratio (excess return / volatility)."""
        excess_return = np.mean(signal_returns) - rf
        volatility = np.std(signal_returns)
        if volatility == 0:
            return 0.0
        return excess_return / volatility

    def _bootstrap_test(self, signal_returns, null_mean=0.0, num_samples=1000):
        """
        Bootstrap hypothesis test (Efron, 1979).

        H0: signal has zero expected return
        H1: signal has positive expected return

        Resample with replacement, calculate distribution of test statistic.
        """
        bootstrap_means = []

        for _ in range(num_samples):
            sample = np.random.choice(signal_returns, len(signal_returns), replace=True)
            bootstrap_means.append(np.mean(sample))

        observed_mean = np.mean(signal_returns)
        pvalue = np.sum(np.array(bootstrap_means) <= null_mean) / num_samples

        return pvalue

    def _test_all_regimes(self, signal_returns, regime_labels):
        """
        Test signal in each regime separately.
        Require >40% WR in each regime (not just average).
        """
        regime_wr = {}

        for regime in self.REGIMES:
            regime_mask = regime_labels == regime
            regime_returns = signal_returns[regime_mask]

            if len(regime_returns) == 0:
                regime_wr[regime] = 0.0
            else:
                wr = np.sum(regime_returns > 0) / len(regime_returns)
                regime_wr[regime] = wr

        return regime_wr
```

### Phase 5: Regime Detection
```python
class RegimeDetector:
    """
    Phase 5: Detect market regime using 5-state HMM.

    States: TRENDING_UP, TRENDING_DOWN, RANGE, HIGH_VOL, RISK_OFF
    """

    def __init__(self):
        self.hmm_model = self._build_hmm_model()
        self.per_market_regime = {}

    def _build_hmm_model(self):
        """
        Hidden Markov Model with 5 hidden states.
        Observations: VIX, realized vol, credit spreads, Fear & Greed.
        """
        # Transition matrix (prob of moving between regimes)
        transition_matrix = np.array([
            [0.90, 0.05, 0.04, 0.01, 0.00],  # TRENDING_UP → all states
            [0.05, 0.80, 0.10, 0.04, 0.01],  # TRENDING_DOWN → all states
            [0.04, 0.10, 0.70, 0.10, 0.06],  # RANGE → all states
            [0.01, 0.04, 0.10, 0.70, 0.15],  # HIGH_VOL → all states
            [0.00, 0.01, 0.06, 0.15, 0.78],  # RISK_OFF → mostly RISK_OFF
        ])

        # Emission probabilities (how likely to observe VIX given state)
        emission_matrix = {
            'TRENDING_UP': {'vix_range': (0, 15), 'vol_mean': 0.10},
            'TRENDING_DOWN': {'vix_range': (15, 25), 'vol_mean': 0.15},
            'RANGE': {'vix_range': (12, 20), 'vol_mean': 0.12},
            'HIGH_VOL': {'vix_range': (25, 40), 'vol_mean': 0.25},
            'RISK_OFF': {'vix_range': (40, 100), 'vol_mean': 0.40},
        }

        return {'transition': transition_matrix, 'emission': emission_matrix}

    def classify_regime(self, vix, realized_vol, credit_spread, fear_gauge):
        """
        Classify market regime based on 4 indicators.
        Called by Phase 5 every 60 seconds.
        """
        if vix > 50:
            return 'RISK_OFF'
        elif vix > 30:
            if realized_vol > 0.25:
                return 'HIGH_VOL'
            else:
                return 'TRENDING_DOWN'
        elif vix > 20:
            if realized_vol < 0.12:
                return 'TRENDING_UP'
            else:
                return 'RANGE'
        else:
            return 'TRENDING_UP'

    def update_all_markets(self, market_data):
        """
        Update regime for all 6 markets.
        Each market can have different regime!
        """
        for market, data in market_data.items():
            vix = data.get('vix', 20.0)
            vol = data.get('realized_vol', 0.15)
            cs = data.get('credit_spread', 3.5)
            fg = data.get('fear_gauge', 50)

            self.per_market_regime[market] = self.classify_regime(vix, vol, cs, fg)
```

### Phase 7: Confidence Scorer (8-Indicator Consensus)
```python
class ConfidenceScorer:
    """
    Phase 7: Score signal confidence using 8 indicators.
    Weighted consensus: VWAP 1.8x, RSI 1.2x, EMA 0.8x, etc.

    Output: confidence_score (0-10)
    Threshold: ≥6.5 to trade (regime-dependent, see Universe)
    """

    INDICATOR_WEIGHTS = {
        'vwap': 1.8,    # Volume-weighted average price (trend strength)
        'rsi': 1.2,     # Relative strength index (momentum)
        'ema': 0.8,     # Exponential moving average (trend direction)
        'roc': 1.0,     # Rate of change (velocity)
        'macd': 1.0,    # MACD (trend + momentum)
        'adx': 1.5,     # Average directional index (trend strength)
        'bb': 0.7,      # Bollinger bands (volatility mean reversion)
        'volume': 0.9,  # Volume profile (strength of move)
    }

    def score_signal(self, symbol, price_data, market_data):
        """
        Score a potential signal across 8 indicators.

        Inputs:
        - symbol: e.g., 'NVD3.L'
        - price_data: {'open', 'high', 'low', 'close', 'volume', 'timestamp'}
        - market_data: {'vix', 'regime', 'realized_vol', etc.}

        Output:
        - confidence: float (0-10)
        - component_scores: dict of 8 indicator scores
        """
        scores = {
            'vwap': self._score_vwap(price_data),
            'rsi': self._score_rsi(price_data),
            'ema': self._score_ema(price_data),
            'roc': self._score_roc(price_data),
            'macd': self._score_macd(price_data),
            'adx': self._score_adx(price_data),
            'bb': self._score_bb(price_data),
            'volume': self._score_volume(price_data, market_data),
        }

        # Weighted average
        total_weight = sum(self.INDICATOR_WEIGHTS.values())
        confidence = sum(
            scores[ind] * self.INDICATOR_WEIGHTS[ind]
            for ind in scores
        ) / total_weight

        return min(10.0, confidence), scores

    def _score_vwap(self, price_data):
        """
        VWAP score: 0-10
        - Close > VWAP: bullish (higher score)
        - Close < VWAP: bearish (lower score)
        """
        vwap = self._calculate_vwap(price_data['volume'], price_data['close'])
        if price_data['close'] > vwap * 1.01:
            return 8.0  # Significantly above VWAP
        elif price_data['close'] > vwap:
            return 6.0  # Above VWAP
        else:
            return 3.0  # Below VWAP

    def _score_rsi(self, price_data):
        """
        RSI score: 0-10
        - RSI 30-70: neutral (5)
        - RSI <30: oversold, bullish (8)
        - RSI >70: overbought, bearish (2)
        """
        rsi = self._calculate_rsi(price_data['close'], period=14)
        if rsi < 30:
            return 8.0  # Oversold, potential reversal
        elif rsi > 70:
            return 2.0  # Overbought, potential reversal
        elif 40 < rsi < 60:
            return 5.0  # Neutral zone
        else:
            return 6.0  # Momentum (30-70)

    def _score_ema(self, price_data):
        """
        EMA score: 0-10
        - Close > EMA50 > EMA200: strong uptrend (8)
        - Close < EMA50 < EMA200: strong downtrend (2)
        - Mixed: neutral (5)
        """
        ema50 = self._calculate_ema(price_data['close'], period=50)
        ema200 = self._calculate_ema(price_data['close'], period=200)

        if price_data['close'] > ema50 > ema200:
            return 8.0
        elif price_data['close'] < ema50 < ema200:
            return 2.0
        else:
            return 5.0

    # ... similar methods for ROC, MACD, ADX, Bollinger Bands, Volume

    def _calculate_rsi(self, closes, period=14):
        """Calculate RSI (Wilder's smoothing)."""
        deltas = np.diff(closes)
        seed = deltas[:period+1]

        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period

        rs = up / down if down != 0 else 0
        rsi = 100 - (100 / (1 + rs))

        return rsi
```

### Phase 9: Position Sizer (Leverage Prioritization)
```python
class PositionSizer:
    """
    Phase 9: Size position based on Kelly Criterion + regime + leverage.

    Core innovation: Leverage prioritization
    - NVDA signal + LSE open → buy NVD3.L (3x) NOT direct NVDA
    - QQQ signal + LSE open → buy QQQ3.L (3-5x) NOT direct QQQ
    """

    def __init__(self, account_size=10000, kelly_fraction=0.25):
        self.account_size = account_size
        self.kelly_fraction = kelly_fraction  # Fractional Kelly (0.25-0.5x)

    def size_position(self, signal, current_price, win_rate, avg_win, avg_loss,
                      market_regime, portfolio_heat, inventory):
        """
        Calculate position size for a signal.

        Inputs:
        - signal: {'symbol': 'NVDA', 'direction': 'BUY', 'confidence': 7.2}
        - current_price: float (bid-ask midpoint)
        - win_rate: float (0.4-0.6, from backtests)
        - avg_win: float (expected win size, e.g., 0.015 = 1.5%)
        - avg_loss: float (expected loss size, e.g., 0.010 = 1.0%)
        - market_regime: str ('TRENDING_UP', etc.)
        - portfolio_heat: float (current portfolio risk, 0-3.5%)
        - inventory: dict (current positions)

        Output:
        - size: int (shares to buy)
        - etp_symbol: str (which instrument to buy)
        - reason: str (why this size/instrument)
        """

        # STEP 1: Check leverage prioritization
        underlying = signal['symbol']  # e.g., 'NVDA'
        market_open = self._is_market_open('LSE')

        etp_symbol = self._get_etp_symbol(underlying, market_open)
        # e.g., NVDA → NVD3.L (3x) if LSE open, else direct NVDA

        # STEP 2: Calculate Kelly size (base position)
        kelly_pct = self._calculate_kelly(win_rate, avg_win, avg_loss)
        kelly_size = (self.account_size * kelly_pct * self.kelly_fraction) / current_price

        # STEP 3: Apply regime multiplier
        regime_mult = self._get_regime_multiplier(market_regime)
        # TRENDING_UP: 0.6x (aggressive)
        # TRENDING_DOWN: 0.4x (cautious)
        # RANGE: 0.25x (very cautious)
        # HIGH_VOL: 0.15x (defensive)
        # RISK_OFF: 0.0x (zero, or inverse hedge only)

        # STEP 4: Apply portfolio heat cap
        remaining_heat = 3.5 - portfolio_heat  # Max 3.5% heat
        heat_constrain = remaining_heat / 3.5  # 0.0-1.0

        # STEP 5: Combine all factors
        position_size = kelly_size * regime_mult * heat_constrain

        # STEP 6: Round to valid lot size
        position_size = self._round_to_lot_size(position_size, etp_symbol)

        # STEP 7: Check leverage cap
        if etp_symbol.endswith('.L'):  # LSE leveraged ETP
            leverage = self._get_leverage(etp_symbol)
            max_position = (self.account_size * 0.10) / (current_price * leverage)
            position_size = min(position_size, max_position)

        return {
            'size': int(position_size),
            'symbol': etp_symbol,
            'kelly_pct': kelly_pct,
            'regime_mult': regime_mult,
            'heat_constrain': heat_constrain,
            'reason': f"Kelly {kelly_pct:.1%} × regime {regime_mult:.1f}x × heat {heat_constrain:.1f}x",
        }

    def _get_etp_symbol(self, underlying, lse_open):
        """
        LEVERAGE PRIORITIZATION ALGORITHM

        If signal fires for underlying AND LSE open AND leveraged ETP exists:
            → Buy 3x/5x ETP
        Else:
            → Buy 1x direct stock
        """
        etp_map = {
            'NVDA': {'lse_open': 'NVD3.L', 'lse_closed': 'NVDA', 'leverage': 3},
            'QQQ': {'lse_open': 'QQQ3.L', 'lse_closed': 'QQQ', 'leverage': 3},
            'SPX': {'lse_open': '3LUS.L', 'lse_closed': 'SPY', 'leverage': 3},
            'TSLA': {'lse_open': 'TSL3.L', 'lse_closed': 'TSLA', 'leverage': 3},
            'SOX': {'lse_open': '3SEM.L', 'lse_closed': 'XSD', 'leverage': 3},
        }

        if underlying in etp_map:
            if lse_open:
                return etp_map[underlying]['lse_open']  # 3x leverage
            else:
                return etp_map[underlying]['lse_closed']  # 1x direct
        else:
            return underlying  # Default: direct stock

    def _calculate_kelly(self, win_rate, avg_win, avg_loss):
        """
        Kelly Criterion: f* = (p×b - q) / b
        where:
        - p = win rate (probability of win)
        - q = loss rate (1 - p)
        - b = win/loss ratio (avg_win / avg_loss)
        """
        if win_rate < 0.4 or avg_loss == 0:
            return 0.0

        q = 1.0 - win_rate
        b = avg_win / avg_loss
        kelly = (win_rate * b - q) / b

        return max(0.0, min(kelly, 0.25))  # Cap at 25% (fractional)

    def _get_regime_multiplier(self, regime):
        """Regime-adjusted leverage multiplier."""
        multipliers = {
            'TRENDING_UP': 0.6,
            'TRENDING_DOWN': 0.4,
            'RANGE': 0.25,
            'HIGH_VOL': 0.15,
            'RISK_OFF': 0.0,
        }
        return multipliers.get(regime, 0.25)
```

---

## 4. EXECUTIONER

### Purpose
The Executioner is the **order execution and risk management engine**. It:
1. Routes orders to the correct market (Phase 15)
2. Manages execution quality (Phase 10)
3. Monitors real-time risk (Phase 19)
4. Audits compliance (Phase 20)

### Phase 15: Order Router (Underlying→ETP Mapping)
```python
class OrderRouter:
    """
    Phase 15: Route orders with leverage prioritization.

    Decision flow:
    1. Check ISA compliance (MANDATORY)
    2. Check leverage prioritization (NVDA→NVD3.L)
    3. Submit order to IBKR
    4. Verify execution vs. intention
    """

    UNDERLYING_ETP_MAP = {
        'NVDA': [
            {'symbol': 'NVD3.L', 'leverage': 3, 'when': 'lse_open', 'priority': 1},
            {'symbol': 'NVDA', 'leverage': 1, 'when': 'lse_closed', 'priority': 2},
        ],
        'QQQ': [
            {'symbol': 'QQQ3.L', 'leverage': 3, 'when': 'lse_open', 'priority': 1},
            {'symbol': 'QQQS.L', 'leverage': 5, 'when': 'lse_open_high_conf', 'priority': 0},  # Highest priority if 5x available
            {'symbol': 'QQQ', 'leverage': 1, 'when': 'us_open', 'priority': 2},
        ],
        'SPX': [
            {'symbol': '3LUS.L', 'leverage': 3, 'when': 'lse_open', 'priority': 1},
            {'symbol': '3USS.L', 'leverage': 5, 'when': 'lse_open_high_conf', 'priority': 0},
            {'symbol': 'SPY', 'leverage': 1, 'when': 'us_open', 'priority': 2},
        ],
    }

    def __init__(self, ibkr_client, universe, compliance_auditor):
        self.ibkr = ibkr_client
        self.universe = universe
        self.compliance = compliance_auditor
        self.execution_log = []

    def route_order(self, signal, position_size_output):
        """
        Route an order with full compliance and leverage prioritization.

        Inputs:
        - signal: {'symbol': 'NVDA', 'direction': 'BUY', 'confidence': 7.2}
        - position_size_output: {'size': 500, 'symbol': 'NVD3.L', ...}

        Output:
        - execution_result: {'order_id', 'filled_shares', 'fill_price', 'reason'}
        """

        # STEP 1: ISA COMPLIANCE CHECK (MANDATORY FIRST)
        is_isa_eligible = self.compliance.verify_isa_eligible(position_size_output['symbol'])
        if not is_isa_eligible:
            return {
                'status': 'REJECTED',
                'reason': f"Symbol {position_size_output['symbol']} not ISA-eligible",
                'order_id': None,
            }

        # STEP 2: VERIFY MARGIN = ZERO (ISA requirement)
        margin_check = self.compliance.verify_zero_margin()
        if not margin_check:
            return {
                'status': 'REJECTED',
                'reason': 'Margin debt > 0, ISA violation',
                'order_id': None,
            }

        # STEP 3: GET SYMBOL (leverage prioritization)
        symbol_to_buy = position_size_output['symbol']
        size = position_size_output['size']

        # STEP 4: SUBMIT ORDER TO IBKR
        try:
            order_result = self.ibkr.place_order(
                symbol=symbol_to_buy,
                quantity=size,
                direction='BUY' if signal['direction'] == 'BUY' else 'SELL',
                order_type='MARKET',  # Or LIMIT with tight spread
                time_in_force='DAY',
            )

            # STEP 5: LOG EXECUTION
            self.execution_log.append({
                'timestamp': time.time(),
                'original_underlying': signal['symbol'],
                'executed_symbol': symbol_to_buy,
                'size': size,
                'order_id': order_result['order_id'],
                'filled': order_result['filled'],
                'fill_price': order_result['fill_price'],
                'leverage': self._get_leverage(symbol_to_buy),
            })

            # STEP 6: VERIFY POST-EXECUTION
            self.compliance.verify_zero_margin()  # Must still be zero

            return {
                'status': 'FILLED',
                'order_id': order_result['order_id'],
                'symbol': symbol_to_buy,
                'size': size,
                'fill_price': order_result['fill_price'],
                'reason': f"Leverage prioritization: {signal['symbol']} → {symbol_to_buy}",
            }

        except Exception as e:
            logger.error(f"Order routing failed: {e}")
            return {
                'status': 'ERROR',
                'reason': str(e),
                'order_id': None,
            }

    def _get_leverage(self, symbol):
        """Get leverage multiple for a symbol."""
        if symbol.endswith('S.L'):  # 5x (e.g., QQQS.L)
            return 5.0
        elif symbol.endswith('3.L'):  # 3x (e.g., QQQ3.L)
            return 3.0
        else:
            return 1.0  # Direct stock
```

### Phase 19: Risk Manager
```python
class RiskManager:
    """
    Phase 19: Real-time risk monitoring and stops.
    - Leverage-adjusted stops (wide in TRENDING, tight in RANGE)
    - Portfolio heat cap (max 3.5% daily loss)
    - Circuit breaker L3 (hard stop at -4%)
    """

    def __init__(self):
        self.positions = {}  # Current holdings
        self.daily_pnl = 0.0
        self.heat = 0.0  # Current portfolio risk

    def update_stop_loss(self, position_id, entry_price, regime, confidence):
        """
        Set stop loss based on regime and confidence.

        Regime mapping:
        - TRENDING_UP: -5% stop (wide, let it run)
        - TRENDING_DOWN: -3% stop (medium)
        - RANGE: -1.5% stop (tight, quick exit)
        - HIGH_VOL: -1% stop (very tight)
        - RISK_OFF: Close all (0% tolerance)
        """
        stop_distances = {
            'TRENDING_UP': 0.05,
            'TRENDING_DOWN': 0.03,
            'RANGE': 0.015,
            'HIGH_VOL': 0.01,
            'RISK_OFF': 0.00,
        }

        stop_dist = stop_distances.get(regime, 0.02)
        stop_price = entry_price * (1.0 - stop_dist)

        return {
            'position_id': position_id,
            'stop_price': stop_price,
            'stop_pct': stop_dist * 100,
            'reason': f"{regime} regime → {stop_dist*100:.1f}% stop",
        }

    def check_heat(self, daily_pnl, account_size):
        """
        Calculate portfolio heat (% of account at risk today).

        Heat cap: 3.5% (max daily loss before circuit breaker L3)
        """
        heat = abs(daily_pnl) / account_size

        if heat > 0.035:
            return {
                'status': 'CIRCUIT_BREAKER_L3',
                'action': 'CLOSE_ALL_POSITIONS',
                'reason': f'Heat {heat:.1%} > 3.5% limit',
            }
        elif heat > 0.02:
            return {
                'status': 'CIRCUIT_BREAKER_L2',
                'action': 'CLOSE_50_PCT_POSITIONS',
                'reason': f'Heat {heat:.1%} > 2% warning',
            }
        elif heat > 0.01:
            return {
                'status': 'CIRCUIT_BREAKER_L1',
                'action': 'NO_NEW_POSITIONS',
                'reason': f'Heat {heat:.1%} > 1% caution',
            }
        else:
            return {
                'status': 'NORMAL',
                'action': 'PROCEED',
                'reason': f'Heat {heat:.1%} < 1% threshold',
            }
```

### Phase 20: Reconciliation Auditor
```python
class ReconciliationAuditor:
    """
    Phase 20: ISA compliance auditor.
    Verifies every 5 minutes:
    1. Margin debt = 0
    2. All holdings ISA-eligible
    3. No short positions (except inverse ETPs)
    4. No margin trading
    """

    def __init__(self, ibkr_client):
        self.ibkr = ibkr_client
        self.audit_log = []

    def verify_isa_compliance(self):
        """
        Full ISA compliance verification.
        Called every 5 minutes by Phase 25 (Orchestrator).

        Returns:
        - is_compliant: bool
        - violations: list of issues found
        """
        violations = []

        # Check 1: Margin debt must be zero
        margin_debt = self.ibkr.get_margin_debt()
        if margin_debt > 0:
            violations.append(f"Margin debt: £{margin_debt:.2f} (must be zero)")

        # Check 2: All holdings must be ISA-eligible
        holdings = self.ibkr.get_holdings()
        for symbol, quantity in holdings.items():
            if not self._is_isa_eligible(symbol):
                violations.append(f"{symbol}: not ISA-eligible")

        # Check 3: No short positions (except inverse ETPs)
        for symbol, quantity in holdings.items():
            if quantity < 0:
                if not self._is_inverse_etp(symbol):
                    violations.append(f"{symbol}: short position {quantity} (forbidden)")

        # Check 4: No margin trading (every share must be fully paid)
        for symbol, quantity in holdings.items():
            buying_power = self.ibkr.get_buying_power()
            if buying_power < 0:
                violations.append(f"Negative buying power: £{buying_power:.2f}")

        # Log audit
        is_compliant = len(violations) == 0
        self.audit_log.append({
            'timestamp': time.time(),
            'is_compliant': is_compliant,
            'violations': violations,
        })

        # Alert if violations found
        if not is_compliant:
            logger.error(f"ISA COMPLIANCE VIOLATION: {violations}")
            self._trigger_emergency_halt()

        return is_compliant, violations

    def _is_isa_eligible(self, symbol):
        """Check if symbol is ISA-eligible."""
        isa_eligible_symbols = [
            'NVD3.L', 'QQQ3.L', '3LUS.L', 'TSL3.L', '3SEM.L',
            'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L',
            'VUSA.L', 'VGOV.L',
            'SPY', 'QQQ', 'IWM',
            'EWJ', 'EWH', 'FXI',
        ]
        return symbol in isa_eligible_symbols

    def _is_inverse_etp(self, symbol):
        """Check if symbol is an inverse ETP (allowed short)."""
        inverse_symbols = ['QQQS_INV', 'SPX_INV']  # Mapped symbols
        return symbol in inverse_symbols

    def _trigger_emergency_halt(self):
        """Emergency: halt all trading if ISA violation detected."""
        logger.critical("ISA VIOLATION DETECTED - TRIGGERING EMERGENCY HALT")
        # Liquidate all positions immediately
        # Alert team
        # Disable all orders
```

---

## 5. OUROBOROS (Nightly Learning Pipeline)

### Purpose
Ouroboros is the **overnight machine learning system**. It:
1. Recalculates signal thresholds (Phase 22)
2. Attributes daily returns (Phase 23)
3. Retrains DQN model (Phase 24)
4. Optimizes leverage per regime

### Phase 24: Ouroboros ML Pipeline
```python
class OuroborosMLPipeline:
    """
    Phase 24: Nightly learning cycle (22:00-23:50 UK time).
    Duration: 37.5 minutes (from previous session: 22:00-23:50).

    Steps:
    1. Fetch all daily trades (500+ trades)
    2. Attribute returns to signals, regimes, execution timing
    3. Update signal thresholds (DSR >0.6 required)
    4. Retrain DQN weighting (8 indicators)
    5. Adjust leverage multipliers
    6. Corp actions: dividend, split adjustments
    7. Output: updated params for tomorrow's trading
    """

    def __init__(self, data_store, ml_model):
        self.data = data_store
        self.dqn = ml_model  # Deep Q-Network for signal weighting
        self.nightly_params = {}

    def run_nightly_cycle(self):
        """
        Execute full nightly learning cycle.
        Called by Phase 25 (Orchestrator) at 22:00 UTC.
        Completes by 23:50 UTC (50 minutes).
        """
        logger.info("Ouroboros: Starting nightly cycle...")
        start_time = time.time()

        # Step 1: Fetch daily trades (10 min)
        trades = self._fetch_daily_trades()
        logger.info(f"Ouroboros: Loaded {len(trades)} daily trades")

        # Step 2: Attribute returns (5 min)
        attribution = self._attribute_returns(trades)
        logger.info(f"Ouroboros: Attribution complete - avg return {attribution['mean_return']:.2%}")

        # Step 3: Update signal thresholds (5 min)
        new_thresholds = self._update_signal_thresholds(attribution)
        logger.info(f"Ouroboros: Signal thresholds updated")

        # Step 4: Retrain DQN (15 min)
        dqn_weights = self._train_dqn(trades, attribution)
        logger.info(f"Ouroboros: DQN retraining complete - loss {dqn_weights['loss']:.6f}")

        # Step 5: Adjust leverage (5 min)
        leverage_updates = self._adjust_leverage_multipliers(attribution)
        logger.info(f"Ouroboros: Leverage multipliers adjusted")

        # Step 6: Corp actions (3 min)
        corp_actions = self._process_corp_actions()
        logger.info(f"Ouroboros: Processed {len(corp_actions)} corporate actions")

        # Step 7: Save params (2 min)
        self._save_updated_params(new_thresholds, dqn_weights, leverage_updates)

        elapsed = time.time() - start_time
        logger.info(f"Ouroboros: Nightly cycle complete in {elapsed:.1f}s")

        return {
            'thresholds': new_thresholds,
            'dqn_weights': dqn_weights,
            'leverage': leverage_updates,
            'corp_actions': corp_actions,
            'elapsed_sec': elapsed,
        }

    def _fetch_daily_trades(self):
        """
        Fetch all trades from today (UTC).
        Returns list of trade objects with OHLCV, execution price, result.
        """
        start_of_day = datetime.utcnow().replace(hour=0, minute=0, second=0)
        trades = self.data.get_trades_since(start_of_day)

        return [
            {
                'symbol': t.symbol,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'return_pct': (t.exit_price - t.entry_price) / t.entry_price,
                'regime': t.regime,
                'confidence': t.confidence,
                'holding_time_sec': (t.exit_time - t.entry_time).total_seconds(),
            }
            for t in trades
        ]

    def _attribute_returns(self, trades):
        """
        Attribute returns to:
        1. Signal quality (confidence score)
        2. Market regime (TRENDING vs RANGE vs HIGH_VOL)
        3. Execution timing (entry timing score)
        4. Holding period (overnight vs intra-day)
        """
        returns_by_regime = {}
        returns_by_confidence = {}

        for trade in trades:
            regime = trade['regime']
            confidence = trade['confidence']
            ret = trade['return_pct']

            if regime not in returns_by_regime:
                returns_by_regime[regime] = []
            returns_by_regime[regime].append(ret)

            if confidence not in returns_by_confidence:
                returns_by_confidence[confidence] = []
            returns_by_confidence[confidence].append(ret)

        attribution = {
            'total_trades': len(trades),
            'mean_return': np.mean([t['return_pct'] for t in trades]),
            'win_rate': sum(1 for t in trades if t['return_pct'] > 0) / len(trades),
            'by_regime': {
                regime: {
                    'mean_return': np.mean(rets),
                    'win_rate': sum(1 for r in rets if r > 0) / len(rets),
                    'num_trades': len(rets),
                }
                for regime, rets in returns_by_regime.items()
            },
        }

        return attribution

    def _update_signal_thresholds(self, attribution):
        """
        Update confidence thresholds per regime.

        Rules:
        - If regime's WR < 40%, raise threshold by 0.5 points
        - If regime's WR > 50%, lower threshold by 0.25 points
        - Never go below 5.5 or above 8.5
        """
        current_thresholds = {
            'TRENDING_UP': 5.5,
            'TRENDING_DOWN': 6.0,
            'RANGE': 7.0,
            'HIGH_VOL': 7.5,
            'RISK_OFF': 8.5,
        }

        new_thresholds = {}
        for regime, stats in attribution['by_regime'].items():
            wr = stats['win_rate']
            current = current_thresholds.get(regime, 6.5)

            if wr < 0.40:
                new = current + 0.5  # Higher threshold
            elif wr > 0.50:
                new = current - 0.25  # Lower threshold
            else:
                new = current  # Keep same

            new_thresholds[regime] = max(5.5, min(8.5, new))

        return new_thresholds

    def _train_dqn(self, trades, attribution):
        """
        Retrain DQN model for signal weighting.

        Input: (signal_returns, regime) pairs
        Output: optimal weights for 8 indicators per regime
        """
        # Group trades by regime
        regime_datasets = {}
        for trade in trades:
            regime = trade['regime']
            if regime not in regime_datasets:
                regime_datasets[regime] = []
            regime_datasets[regime].append(trade['return_pct'])

        # Retrain DQN for each regime
        dqn_weights = {}
        for regime, returns in regime_datasets.items():
            # DQN input: last 20 days of returns in regime
            # DQN output: optimal weights for [VWAP, RSI, EMA, ROC, MACD, ADX, BB, Volume]

            dqn_loss = self.dqn.train(
                X=np.array(returns).reshape(-1, 1),
                y=np.array(returns),  # Target: future return
            )

            # Get new weights
            new_weights = self.dqn.get_weights()  # 8-element vector
            dqn_weights[regime] = {
                'weights': new_weights.tolist(),
                'loss': dqn_loss,
                'num_samples': len(returns),
            }

        return dqn_weights

    def _adjust_leverage_multipliers(self, attribution):
        """
        Adjust leverage multipliers based on daily performance.

        Rules:
        - High WR (>50%) → increase multiplier by 5%
        - Low WR (<40%) → decrease multiplier by 10%
        - Normal WR (40-50%) → keep same
        """
        leverage_updates = {}
        current_multipliers = {
            'TRENDING_UP': 0.6,
            'TRENDING_DOWN': 0.4,
            'RANGE': 0.25,
            'HIGH_VOL': 0.15,
            'RISK_OFF': 0.0,
        }

        for regime, stats in attribution['by_regime'].items():
            wr = stats['win_rate']
            current = current_multipliers.get(regime, 0.3)

            if wr > 0.50:
                new = current * 1.05  # +5%
            elif wr < 0.40:
                new = current * 0.90  # -10%
            else:
                new = current

            # Cap between 0.0 and 1.0
            new = max(0.0, min(1.0, new))

            leverage_updates[regime] = {
                'current': current,
                'new': new,
                'reason': f"WR {wr:.1%}, num_trades {stats['num_trades']}",
            }

        return leverage_updates

    def _process_corp_actions(self):
        """
        Handle corporate actions: dividends, splits, spin-offs.

        For each LSE-listed asset:
        1. Check for dividend ex-date today
        2. Check for split announcement
        3. Update position counts and cost basis
        """
        corp_actions = []

        # Check LSE dividend calendar
        ex_dates_today = self.data.get_dividend_ex_dates(datetime.utcnow())
        for symbol, div_amount in ex_dates_today.items():
            # Adjust cost basis downward
            current_holdings = self.data.get_holding(symbol)
            dividend_credit = current_holdings * div_amount

            corp_actions.append({
                'symbol': symbol,
                'action': 'DIVIDEND',
                'amount': div_amount,
                'total_credit': dividend_credit,
            })

        return corp_actions

    def _save_updated_params(self, thresholds, dqn_weights, leverage):
        """
        Save all updated parameters to database.
        These become live when Phase 25 reloads at 08:00 UTC.
        """
        self.nightly_params = {
            'timestamp': datetime.utcnow(),
            'thresholds': thresholds,
            'dqn_weights': dqn_weights,
            'leverage_multipliers': leverage,
        }

        # Write to DB
        self.data.save_nightly_params(self.nightly_params)
```

---

## 6. DYNAMIC ALLOCATION (IBKR + Polygon)

### Purpose
Dynamic allocation distributes capital across 6 markets based on:
1. Real-time regime classification (each market independent)
2. Per-market win rate from Ouroboros
3. Portfolio heat constraints (max 3.5% daily loss)
4. ISA compliance (zero margin, ISA-eligible only)

### Dynamic Allocation Algorithm
```python
class DynamicAllocator:
    """
    Allocate £10,000 ISA capital across 6 markets dynamically.

    Input:
    - Current holdings (from IBKR)
    - Market regimes (from Phase 5)
    - Win rates per regime (from Ouroboros)
    - Current portfolio heat

    Output:
    - Allocation: {'LSE_LEVERAGED_3X': 3000, 'US_EQUITY': 4000, ...}
    - Reason: why each market gets this allocation
    """

    def __init__(self, account_size=10000, max_per_market=0.40):
        self.account_size = account_size
        self.max_per_market = max_per_market  # 40% max per market
        self.current_holdings = {}

    def allocate_capital(self, market_regimes, win_rates, current_heat):
        """
        Dynamically allocate capital based on regime and win rate.

        Inputs:
        - market_regimes: {'LSE_LEVERAGED_3X': 'TRENDING_UP', ...}
        - win_rates: {'LSE_LEVERAGED_3X': 0.45, ...}
        - current_heat: float (0.0-3.5%, current daily loss)

        Output:
        - allocation: dict of capital per market
        """

        # Step 1: Calculate regime scores
        regime_scores = {}
        for market, regime in market_regimes.items():
            # Higher score = better regime
            scores = {
                'TRENDING_UP': 1.0,
                'TRENDING_DOWN': 0.6,
                'RANGE': 0.3,
                'HIGH_VOL': 0.2,
                'RISK_OFF': 0.0,
            }
            regime_scores[market] = scores.get(regime, 0.5)

        # Step 2: Calculate performance scores (from Ouroboros)
        perf_scores = {}
        for market, wr in win_rates.items():
            # WR 0.4 → score 0.0
            # WR 0.5 → score 0.5
            # WR 0.6 → score 1.0
            perf_scores[market] = max(0.0, min(1.0, (wr - 0.4) * 10))

        # Step 3: Combine regime + performance
        combined_scores = {}
        for market in market_regimes.keys():
            regime_score = regime_scores[market]
            perf_score = perf_scores[market]

            # Weight: regime 60%, performance 40%
            combined = (regime_score * 0.6) + (perf_score * 0.4)
            combined_scores[market] = combined

        # Step 4: Allocate based on scores (proportional to ranking)
        total_score = sum(combined_scores.values())
        allocation = {}

        for market, score in combined_scores.items():
            if total_score > 0:
                pct = score / total_score
            else:
                pct = 1.0 / len(combined_scores)  # Equal if all scores 0

            allocated = self.account_size * pct

            # Cap at max_per_market
            allocated = min(allocated, self.account_size * self.max_per_market)

            allocation[market] = allocated

        # Step 5: Apply heat constraint
        # If current_heat > 2%, reduce allocations by 50%
        if current_heat > 0.02:
            factor = max(0.5, 1.0 - (current_heat / 0.035))
            for market in allocation:
                allocation[market] *= factor

        return allocation

    def execute_allocation(self, target_allocation, current_holdings, universe):
        """
        Execute rebalancing to match target allocation.

        Algorithm:
        1. Calculate current value per market
        2. Identify over/under-weighted positions
        3. Generate buy/sell orders
        4. Execute via IBKR
        """
        orders = []

        # Calculate target share count per market
        for market, target_value in target_allocation.items():
            # Get representative asset for market (first one)
            assets = universe.get_tradable_assets(market, market_regimes[market])
            if not assets:
                continue

            representative_asset = assets[0]  # e.g., NVD3.L for LSE_LEVERAGED_3X
            price = self._get_current_price(representative_asset['symbol'])

            target_shares = int(target_value / price)
            current_shares = current_holdings.get(representative_asset['symbol'], 0)

            diff = target_shares - current_shares

            if diff > 0:
                orders.append({
                    'symbol': representative_asset['symbol'],
                    'action': 'BUY',
                    'quantity': diff,
                    'reason': f"Allocate to {market}",
                })
            elif diff < 0:
                orders.append({
                    'symbol': representative_asset['symbol'],
                    'action': 'SELL',
                    'quantity': -diff,
                    'reason': f"Deallocate from {market}",
                })

        return orders
```

### IBKR Integration
```python
class IBKRClient:
    """
    Interactive Brokers TWS API wrapper.
    Handles order execution, data fetching, compliance.
    """

    def __init__(self, host='127.0.0.1', port=7497, client_id=101):
        self.app = EClient(self)
        self.app.connect(host, port, client_id)
        self.market_data_cache = {}
        self.order_status = {}

    def place_order(self, symbol, quantity, direction, order_type='MARKET'):
        """
        Place order on IBKR.

        Inputs:
        - symbol: 'NVD3.L' or 'SPY'
        - quantity: int
        - direction: 'BUY' or 'SELL'
        - order_type: 'MARKET' or 'LIMIT'

        Output:
        - order_id: int
        - status: 'submitted' or 'error'
        """
        # Create contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = 'STK'
        contract.exchange = 'SMART'  # Smart routing
        contract.currency = 'GBP'

        # Create order
        order = Order()
        order.action = direction
        order.totalQuantity = quantity
        order.orderType = order_type

        if order_type == 'LIMIT':
            # Get current bid-ask midpoint
            bid = self.get_bid(symbol)
            ask = self.get_ask(symbol)
            midpoint = (bid + ask) / 2
            order.lmtPrice = midpoint

        # Place order
        self.app.placeOrder(self.app.nextValidOrderId, contract, order)

        return {
            'order_id': self.app.nextValidOrderId,
            'status': 'submitted',
        }

    def get_last(self, symbol):
        """Get last traded price."""
        return self.market_data_cache.get(f"{symbol}:last", None)

    def get_bid(self, symbol):
        """Get current bid."""
        return self.market_data_cache.get(f"{symbol}:bid", None)

    def get_ask(self, symbol):
        """Get current ask."""
        return self.market_data_cache.get(f"{symbol}:ask", None)

    def get_margin_debt(self):
        """Get current margin debt (must be zero for ISA)."""
        # Request account summary
        return self._get_account_value('GrossPositionValue')

    def get_buying_power(self):
        """Get available buying power."""
        return self._get_account_value('BuyingPower')

    def get_holdings(self):
        """Get all current positions."""
        # Request portfolio
        return self._get_portfolio()
```

### Polygon Integration
```python
class PolygonClient:
    """
    Polygon.io real-time and historical data API.
    """

    def __init__(self, api_key):
        self.client = RESTClient(api_key)

    def get_last_quote(self, symbol):
        """
        Get last quote (bid/ask) for symbol.
        Real-time or 15-min delayed depending on subscription.
        """
        agg = self.client.get_last_quote(symbol)
        return {
            'bid': agg.bid,
            'ask': agg.ask,
            'last': (agg.bid + agg.ask) / 2,
            'timestamp': agg.timestamp,
        }

    def get_historical_bars(self, symbol, from_date, to_date, timespan='day'):
        """
        Get historical OHLCV bars.
        Useful for backtesting signal validation (Phase 4).
        """
        bars = self.client.get_aggs(
            symbol,
            1,  # 1-day bars
            timespan,
            from_date,
            to_date,
        )
        return bars
```

---

## SUMMARY TABLE

| Component | Purpose | Key Functions | Update Freq |
|-----------|---------|---------------|------------|
| **Universe** | Asset selection + metadata | classify_all_markets, get_tradable_assets, get_asset_metadata | Every 60s |
| **Feeds (6 Markets)** | Real-time data | _feed_loop, _fetch_primary, _broadcast_data | Every 1-5s |
| **Signal Engine** | Decision-making | White Reality Check, Regime Detection, Confidence Scoring, Position Sizing | Per signal |
| **Executioner** | Order execution + risk | Order Router, Risk Manager, Reconciliation Auditor | Per trade |
| **Ouroboros** | Nightly learning | run_nightly_cycle, _train_dqn, _adjust_leverage | Nightly 22:00-23:50 UTC |
| **Dynamic Allocator** | Capital allocation | allocate_capital, execute_allocation | Every 60s |
| **IBKR Client** | Broker integration | place_order, get_holdings, get_margin_debt | Per order / Per 5min |
| **Polygon Client** | Market data | get_last_quote, get_historical_bars | Per request |

---

**Document Complete**
Date: March 13, 2026
Status: ✅ READY FOR IMPLEMENTATION
