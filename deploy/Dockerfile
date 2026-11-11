# syntax=docker/dockerfile:1.4
# MDRAP Self-Hosted Read-Only Monitoring Sidecar
# Multi-stage hardened container

FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# Install build dependencies for optional C fastpath compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Build package
RUN pip install --no-cache-dir build && python -m build --wheel

# Runtime image
FROM python:3.11-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="MDRAP" \
      org.opencontainers.image.description="Market Data Reliability & Assurance Platform — Read-Only Sidecar" \
      org.opencontainers.image.version="6.0.0" \
      org.opencontainers.image.vendor="MDRAP Platform Engineering" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MDRAP_DATA_DIR=/data \
    MDRAP_CONFIG=/etc/mdrap/config.toml

# Create unprivileged system user
RUN groupadd -g 10001 mdrap && \
    useradd -u 10001 -g mdrap -d /data -s /usr/sbin/nologin mdrap && \
    mkdir -p /data /etc/mdrap /app && \
    chown -R mdrap:mdrap /data /etc/mdrap /app

WORKDIR /app

# Install wheel built in previous stage
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl rich uvicorn fastapi && rm -rf /tmp/*.whl

# Copy deploy utilities and entrypoint
COPY deploy/healthcheck.py /app/deploy/healthcheck.py
COPY deploy/validate_environment.py /app/deploy/validate_environment.py
COPY deploy/config.example.toml /etc/mdrap/config.example.toml

# Create writable data directory
VOLUME ["/data", "/etc/mdrap"]

USER mdrap

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]

ENTRYPOINT ["python", "-m", "mdrap.cli"]
CMD ["check"]
