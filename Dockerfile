FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN python -m pip wheel --wheel-dir /wheels ".[api]"

FROM python:3.12-slim-bookworm AS runner

LABEL maintainer="MDRAP Platform Engineering" \
      description="MDRAP market-data quality library and optional API"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MDRAP_HOST=0.0.0.0 \
    MDRAP_PORT=8000 \
    MDRAP_DB_PATH=/data/mdrap.db

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
RUN groupadd -r mdrap -g 1000 && \
    useradd -r -u 1000 -g mdrap -m -d /app mdrap && \
    mkdir -p /data && chown -R mdrap:mdrap /app /data && chmod 750 /data

VOLUME ["/data"]
USER mdrap
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/v1/health || exit 1

ENTRYPOINT ["mdrap"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000", "--db", "/data/mdrap.db"]
