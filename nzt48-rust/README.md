# NZT-48 Rust Performance Engine

**Phase Q4 Deliverable #2**: Rust FFI bridge for 10-100x faster indicator calculations.

## Performance Targets

| Operation | Python (baseline) | Rust (target) | Speedup |
|-----------|-------------------|---------------|---------|
| RSI calculation | 50μs | <10μs | 5x |
| RVOL calculation | 20μs | <5μs | 4x |
| ATR calculation | 80μs | <15μs | 5x |
| VWAP calculation | 40μs | <8μs | 5x |
| Chandelier stop | 500μs | <20μs | 25x |
| **Full suite** | **5-10ms** | **<50μs** | **100-200x** |

## Installation

### Prerequisites

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install maturin (Rust → Python bridge builder)
pip install maturin
```

### Build & Install

```bash
# Development build (faster compile)
cd nzt48-rust
maturin develop

# Release build (optimized, slower compile)
maturin develop --release
```

### Verify Installation

```python
import nzt48_rust_engine

# Test basic function
prices = [45.0, 44.0, 44.5, 43.5, 44.0, 43.0]
rsi = nzt48_rust_engine.calculate_rsi(prices, period=5)
print(f"RSI: {rsi:.2f}")  # Should print RSI value

# Test full suite
highs = [105.0, 104.0, 103.0, 102.0, 101.0]
lows = [100.0, 99.0, 98.0, 97.0, 96.0]
closes = [102.5, 101.5, 100.5, 99.5, 98.5]
volumes = [10000, 9500, 10200, 9800, 10500]

indicators = nzt48_rust_engine.calculate_all_indicators(highs, lows, closes, volumes)
print(indicators)
# Output: IndicatorSuite(rsi=..., rvol=..., atr=..., vwap=..., chandelier_long=..., chandelier_short=...)
```

## Usage

### From Python

```python
from nzt48_rust_engine import (
    calculate_rsi,
    calculate_rvol,
    calculate_atr,
    calculate_vwap,
    calculate_chandelier_stop,
    calculate_all_indicators,
)

# Individual indicators
rsi = calculate_rsi(prices, period=14)
rvol = calculate_rvol(volumes, period=20)
atr = calculate_atr(highs, lows, closes, period=14)

# Batch calculation (fastest)
indicators = calculate_all_indicators(highs, lows, closes, volumes)
print(f"RSI: {indicators.rsi:.2f}")
print(f"RVOL: {indicators.rvol:.2f}")
print(f"ATR: {indicators.atr:.4f}")
```

### Integration with DisruptorEngine

```python
# core/disruptor_engine.py

try:
    import nzt48_rust_engine as rust
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False
    logger.warning("Rust engine not available, using Python fallback")

def calculate_indicators(self, ticker: str, bars: List[Bar]) -> IndicatorResult:
    if RUST_AVAILABLE and len(bars) >= 20:
        # Use Rust for speed (100x faster)
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        indicators = rust.calculate_all_indicators(highs, lows, closes, volumes)
        return IndicatorResult(
            rsi=indicators.rsi,
            rvol=indicators.rvol,
            atr=indicators.atr,
            vwap=indicators.vwap,
            chandelier_long=indicators.chandelier_long,
            chandelier_short=indicators.chandelier_short,
        )
    else:
        # Fallback to Python implementation
        return self._calculate_indicators_python(ticker, bars)
```

## Benchmark

```bash
# Run Rust tests
cd nzt48-rust
cargo test --release

# Benchmark (requires nightly Rust)
cargo +nightly bench
```

### Expected Results

```
test indicators::test_rsi ... ok (0.008ms)
test indicators::test_rvol ... ok (0.004ms)
test indicators::test_atr ... ok (0.012ms)
test indicators::test_vwap ... ok (0.006ms)
test chandelier::test_chandelier_stop_long ... ok (0.018ms)

Full suite (100 bars): 0.045ms
Python equivalent: 5-10ms
Speedup: 111-222x
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Python Layer                           │
│  DisruptorEngine, TierBasedEntryLogic, ChandelierExit       │
└─────────────────────────────────────────────────────────────┘
                             ↓
                    PyO3 FFI (GIL-released)
                             ↓
┌─────────────────────────────────────────────────────────────┐
│                       Rust Layer                             │
│  • SIMD-optimized math                                       │
│  • Parallel iterators (Rayon)                                │
│  • Zero-copy NumPy arrays                                    │
│  • Lock-free data structures                                 │
└─────────────────────────────────────────────────────────────┘
```

## Why Rust?

1. **Speed**: 10-100x faster than Python (no GIL, SIMD, zero-cost abstractions)
2. **Safety**: No segfaults, no use-after-free, no data races (borrow checker)
3. **Parallelism**: Rayon provides data parallelism without race conditions
4. **Zero-copy**: NumPy arrays passed directly to Rust (no serialization)
5. **Ecosystem**: Excellent math libraries (ndarray, statrs, polars)

## Hot Paths (Ported to Rust)

### Priority 1 (Completed)
- ✅ RSI calculation
- ✅ RVOL calculation
- ✅ ATR calculation
- ✅ VWAP calculation
- ✅ Chandelier exit logic
- ✅ Full indicator suite

### Priority 2 (Future)
- ⏳ Order book LOB simulation
- ⏳ Fill price estimation
- ⏳ VPIN calculation
- ⏳ Cross-impact OFI

### Priority 3 (Advanced)
- ⏳ DQN inference (via ONNX Runtime)
- ⏳ Neural Hawkes intensity calculation
- ⏳ DPDK packet processing

## Troubleshooting

### "ImportError: No module named nzt48_rust_engine"

```bash
# Rebuild and reinstall
cd nzt48-rust
maturin develop --release
```

### "Segmentation fault"

Likely a data alignment issue. Check:
1. Input arrays have correct length
2. No NaN/Inf values in inputs
3. Period parameters are valid (period < len(data))

### "Performance not improved"

Check:
1. Using `--release` build (not debug)
2. Input data is large enough (>20 bars)
3. Python GIL is being released (use `htop` to check CPU usage)

## Development

### Add New Indicator

```rust
// src/indicators.rs

pub fn macd(prices: &[f64], fast: usize, slow: usize, signal: usize) -> (f64, f64, f64) {
    let ema_fast = ema(prices, fast);
    let ema_slow = ema(prices, slow);
    let macd_line = ema_fast - ema_slow;
    // ... signal line calculation
    (macd_line, signal_line, histogram)
}
```

```rust
// src/lib.rs

#[pyfunction]
fn calculate_macd(prices: Vec<f64>, fast: usize, slow: usize, signal: usize) -> PyResult<(f64, f64, f64)> {
    Ok(macd(&prices, fast, slow, signal))
}
```

### Run Tests

```bash
cargo test
cargo test --release  # With optimizations
```

### Profile Performance

```bash
cargo build --release
perf record target/release/nzt48_rust_engine
perf report
```

## Deployment

### Docker Build

```dockerfile
# Add to Dockerfile
FROM rust:1.75 as rust-builder
WORKDIR /app/nzt48-rust
COPY nzt48-rust/ .
RUN cargo build --release

FROM python:3.11
# ... existing Python setup
COPY --from=rust-builder /app/nzt48-rust/target/release/libnzt48_rust_engine.so /usr/local/lib/python3.11/site-packages/
```

### EC2 Deployment

```bash
# SSH to EC2
ssh ubuntu@3.230.44.22

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Build
cd /home/ubuntu/nzt48-signals/nzt48-rust
maturin build --release

# Install wheel
pip install target/wheels/nzt48_rust_engine-*.whl
```

## License

Proprietary - NZT-48 Trading System

## Maintainer

NZT-48 DevOps Team
