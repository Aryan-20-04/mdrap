# ==============================================================================
# MDRAP Core Commercial Production Dockerfile
# Self-Hosted Multi-Stage Build with Native C Hot-Path Compilation
# ==============================================================================

# Stage 1: Build stage with compiler tools
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY src/ src/
COPY build_fastpath.py .

# Compile native C fastpath accelerator shared object (_fastpath_native.so)
RUN python build_fastpath.py || echo "Warning: C fastpath compilation skipped"

# Stage 2: Production runtime image (lean and hardened)
FROM python:3.12-slim-bookworm AS runner

LABEL maintainer="MDRAP Platform Engineering" \
      description="Market Data Reliability & Acceleration Platform (Self-Hosted)" \
      version="2.2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MDRAP_HOST=0.0.0.0 \
    MDRAP_PORT=8000 \
    MDRAP_DB_PATH=/data/mdrap.db \
    PYTHONPATH=/app/src:/app

WORKDIR /app

# Install curl for healthchecks and runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition and install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[all]"

# Copy application source and native libraries from builder
COPY --from=builder /build/src /app/src
COPY --from=builder /build/build_fastpath.py /app/build_fastpath.py
COPY cli.py /app/cli.py
COPY scripts/ /app/scripts/

# Create non-root system user and prepare persistent data directory
RUN groupadd -r mdrap -g 1000 && \
    useradd -r -u 1000 -g mdrap -m -d /app mdrap && \
    mkdir -p /data && \
    chown -R mdrap:mdrap /app /data && \
    chmod 750 /data

VOLUME ["/data"]

USER mdrap

EXPOSE 8000 9001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/v1/health || exit 1

ENTRYPOINT ["python", "cli.py"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/mdrap.db"]
