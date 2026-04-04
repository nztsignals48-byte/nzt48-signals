# AEGIS V2 — Multi-stage build: Rust core + Python brain + Ouroboros cron
# Phase 7.1: Builder → Runtime split reduces image from ~6.3GB to ~2.5GB
# Target: x86_64 (EC2 c7i-flex.large)

# ════════════════════════════════════════════════════════════════
# STAGE 1: Builder — compile Rust binary + PyO3 wheel
# ════════════════════════════════════════════════════════════════
FROM python:3.12-bookworm AS builder

# Rust toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && . "$HOME/.cargo/env" \
    && rustup install 1.94.0 && rustup default 1.94.0
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /app

# Python build deps (maturin needed for PyO3 wheel)
COPY requirements.txt ./
RUN pip install --no-cache-dir maturin>=1.5

# Copy Rust source for compilation
COPY Cargo.toml Cargo.lock ./
COPY rust_core/ rust_core/

# Build aegis binary (release, optimized for icelake)
ENV RUSTFLAGS="-C target-cpu=icelake-server"
RUN cargo build --release --bin aegis 2>&1 \
    && strip target/release/aegis

# Build PyO3 extension wheel
RUN cd rust_core && maturin build --release --interpreter python3.12

# ════════════════════════════════════════════════════════════════
# STAGE 2: Runtime — slim image with only what we need
# ════════════════════════════════════════════════════════════════
FROM python:3.12-bookworm

# tini: lightweight init for PID 1 — reaps zombie child processes
# liblmdb0: LMDB C library required by ArcticDB (Phase 6.3)
RUN apt-get update && apt-get install -y --no-install-recommends tini liblmdb0 && rm -rf /var/lib/apt/lists/*

# Sprint S03: Node.js 22 + Claude Code CLI ($0/month via Max subscription)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && node --version && claude --version

# Supercronic for Ouroboros nightly cron (no syslog needed)
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64
ARG SUPERCRONIC_SHA=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
RUN curl -fsSLO "$SUPERCRONIC_URL" \
    && echo "${SUPERCRONIC_SHA}  supercronic-linux-amd64" | sha256sum -c - \
    && chmod +x supercronic-linux-amd64 \
    && mv supercronic-linux-amd64 /usr/local/bin/supercronic

WORKDIR /app

# Python dependencies (cache layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy compiled artifacts from builder
COPY --from=builder /app/target/release/aegis /usr/local/bin/aegis
COPY --from=builder /app/target/wheels/rust_core-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/rust_core-*.whl && rm -f /tmp/rust_core-*.whl

# Copy Python source (no Rust source, no target dir, no Cargo caches)
COPY python_brain/ python_brain/
COPY ouroboros/ ouroboros/
COPY scripts/ scripts/
COPY CLAUDE.md ./

# Ouroboros crontab: 23:50 ET = 04:50 UTC (EST) or 03:50 UTC (EDT)
COPY crontab /app/crontab

# Bake build metadata
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONUNBUFFERED=1
# CRITICAL FIX (2026-03-18 audit): Crontab entries are in UTC.
# TZ=America/New_York caused ALL cron jobs to run 4-5h late (during market hours).
# Ouroboros nightly was running at 09:50 UTC (LSE open!) instead of 04:50 UTC (DARK).
ENV TZ=UTC

# Config and WAL dirs are volume-mounted at runtime
VOLUME ["/app/config", "/app/events"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD pgrep aegis >/dev/null && test -f /app/events/current.ndjson || exit 1

# Entrypoint runs both aegis engine and supercronic
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/entrypoint.sh"]
