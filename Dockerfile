# ==============================================================================
# NZT-48 Trading System — Production Dockerfile
# Multi-stage build: Python backend (engine + API) managed by supervisord
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Python dependencies
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS python-deps

WORKDIR /app

# System dependencies required for lightgbm, scipy, numpy compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gfortran \
    libopenblas-dev \
    pkg-config \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Production image
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS production

LABEL maintainer="NZT-48 Trading System"
LABEL description="NZT-48 signal engine + FastAPI dashboard"

WORKDIR /app

# H-03: IMAGE_PARITY — bake git SHA into image for boot-time verification
ARG GIT_SHA=unknown
RUN echo "$GIT_SHA" > /app/.git_sha

# Runtime dependencies only (libgomp for lightgbm, supervisor for process mgmt)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    supervisor \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from build stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy application source code — top-level Python files
COPY main.py .
COPY models.py .
COPY system_watchdog.py .
COPY scheduled_jobs.py .
COPY exceptions.py .

# Copy application source code — package directories
COPY config/ ./config/
COPY feeds/ ./feeds/
COPY strategies/ ./strategies/
COPY qualification/ ./qualification/
COPY execution/ ./execution/
COPY learning/ ./learning/
COPY delivery/ ./delivery/
COPY bots/ ./bots/
COPY dashboard/ ./dashboard/
COPY signal_engine/ ./signal_engine/
COPY uk_isa/ ./uk_isa/
COPY risk_officer/ ./risk_officer/
COPY command_center/ ./command_center/
COPY core/ ./core/
COPY api/ ./api/
COPY data_hub/ ./data_hub/
COPY tests/ ./tests/

# Copy supervisord configuration
COPY supervisord.conf /etc/supervisor/conf.d/nzt48.conf

# Create data directory and reports subdirectory (volume mount in production)
RUN mkdir -p /app/data/reports && chmod -R 755 /app/data

# Expose API port
EXPOSE 8000

# Health check — hit the /api/config endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/config || exit 1

# Run supervisord to manage both engine and API processes
CMD ["supervisord", "-c", "/etc/supervisor/conf.d/nzt48.conf", "-n"]
