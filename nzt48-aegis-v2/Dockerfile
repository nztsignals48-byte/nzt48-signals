# AEGIS V2 — Rust core + Python brain + Ouroboros cron
# Target: x86_64 (EC2 c7i-flex.large)

FROM python:3.12-bookworm

# Rust toolchain
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
    && . "$HOME/.cargo/env" \
    && rustup default stable
ENV PATH="/root/.cargo/bin:${PATH}"

# Supercronic for Ouroboros nightly cron (no syslog needed)
ARG SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64
ARG SUPERCRONIC_SHA=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
RUN curl -fsSLO "$SUPERCRONIC_URL" \
    && echo "${SUPERCRONIC_SHA}  supercronic-linux-amd64" | sha256sum -c - \
    && chmod +x supercronic-linux-amd64 \
    && mv supercronic-linux-amd64 /usr/local/bin/supercronic

WORKDIR /app

# Python dependencies first (cache layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source
COPY Cargo.toml Cargo.lock ./
COPY rust_core/ rust_core/
COPY python_brain/ python_brain/
COPY ouroboros/ ouroboros/

# Build aegis binary (release)
RUN cd rust_core && cargo build --release --bin aegis 2>&1
RUN cp target/release/aegis /usr/local/bin/aegis && strip /usr/local/bin/aegis

# Build PyO3 extension module (build wheel + install)
RUN cd rust_core && maturin build --release --interpreter python3.12 \
    && pip install --no-cache-dir /app/target/wheels/rust_core-*.whl

# Ouroboros crontab: 23:50 ET = 04:50 UTC (EST) or 03:50 UTC (EDT)
COPY crontab /app/crontab

# Bake build metadata
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
ENV PYTHONUNBUFFERED=1
ENV TZ=America/New_York

# Config and WAL dirs are volume-mounted at runtime
VOLUME ["/app/config", "/app/events"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep aegis || exit 1

# Entrypoint runs both aegis engine and supercronic
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
CMD ["/app/entrypoint.sh"]
