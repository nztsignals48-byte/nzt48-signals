# AEGIS V5

Simulation-first trading research lab. Produces clean, labelled, Ouroboros-ready trades under real IBKR paper fills.

See `docs/AEGIS_V5_MASTER_PLAN.md` for the full plan.

## Quick start

```bash
docker compose -f infra/docker-compose.yml up -d   # NATS, Prometheus, Grafana, Loki
cargo build --manifest-path rust_core/Cargo.toml
python -m pytest tests/smoke
python scripts/dead_code_check.py
python scripts/field_ledger_check.py
```

## Philosophy

Nothing is built unless it is consumed. Nothing is consumed unless it is measured. Nothing that doesn't change a trade stays in the system.
