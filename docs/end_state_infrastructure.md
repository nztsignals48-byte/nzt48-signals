# NZT-48 End-State Infrastructure (Phase Q3-Q4)

## Target Architecture
- **Hardware:** Bare metal c7g.metal (64 ARM cores, 128GB RAM) in eu-west-2 (London)
- **Network:** DPDK kernel bypass for <3us wire-to-wire latency
- **Clock:** IEEE 1588 PTP synchronization (<1us accuracy)
- **Database:** TimescaleDB replacing SQLite (concurrent R/W, time-series optimized)
- **Process Management:** systemd replacing Docker (eliminates container overhead)
- **CPU:** Core pinning -- dedicated cores for execution, data feed, and brain

## Latency Budget
| Component | Target | Current |
|-----------|--------|---------|
| Signal-to-wire | <10us | ~50ms |
| Market data ingestion | <1us | ~5ms |
| IPC (brain -> execution) | <200ns | ~1ms |
| Clock sync | <1us | ~10ms |

## Prerequisites
- Phase Q1 validation gate passed (WR >= 40%)
- Phase Q2 execution physics complete
- Equity > 50k GBP (justifies infrastructure cost)
- 500+ live trades with positive expectancy

## Migration Checklist
- [ ] Procure c7g.metal dedicated instance
- [ ] Install DPDK drivers and configure hugepages
- [ ] Set up PTP clock synchronization
- [ ] Migrate SQLite -> TimescaleDB
- [ ] Build Rust FFI bridge (L-01)
- [ ] Deploy DQN Ghost-Maker (L-02)
- [ ] Deploy Neural Hawkes Exit (L-03)
- [ ] Configure CPU core pinning
- [ ] Run all 5 chaos drills on new hardware
- [ ] 30-day burn-in before live trading
