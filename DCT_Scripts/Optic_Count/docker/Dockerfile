# Multi-stage Dockerfile for Atlas DCT Infrastructure Intelligence

# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

WORKDIR /build

# gcc is required to compile the Cython extension (cutsheet_normalizer_fast).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Compile the Cython extension. Cython is a build-time-only dependency,
# kept out of requirements.txt so it doesn't bloat the runtime image.
COPY cutsheet_normalizer_fast.pyx setup.py ./
RUN pip install --no-cache-dir Cython==3.0.11 \
    && PYTHONPATH=/install/lib/python3.11/site-packages \
       python setup.py build_ext --inplace

# --- Stage 2: Runtime ---
FROM python:3.11-slim

RUN groupadd -r -g 1000 atlas && useradd -r -u 1000 -g 1000 -d /app atlas

WORKDIR /app

# BASE_PATH is injected by the Canvas CI build pipeline (e.g. /canvas-apps/atlas).
# Baking it into the image as an env var lets the app mount itself at the
# correct prefix so Kubernetes health probes and all routes resolve correctly.
ARG BASE_PATH=""
ENV BASE_PATH=${BASE_PATH}

COPY --from=builder /install /usr/local

# Copy application code
COPY *.py ./
COPY atlas_schema.sql ./
COPY netbox_dashboard.html ./

# Bring in the compiled Cython extension. If absent (e.g. build failure),
# cutsheet_normalizer.py falls back to the pure-Python row loop.
COPY --from=builder /build/*.so ./

# Create upload directory
RUN mkdir -p /app/uploads && chown -R atlas:atlas /app

USER atlas

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/api/health')" || exit 1

# Production server: 1 worker + 4 threads.
# Single worker keeps USER_CONTEXT/USER_SITE in one process (no cross-worker
# state isolation). Threads share memory safely under _state_lock. Atlas is
# I/O bound (Postgres + LLM API), so threads give equivalent throughput.
# Timeout for large cutsheet uploads (19MB+ workbooks) plus combined
# preprocessor + optional in-service sort + Postgres ingest in one request.
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5050", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "atlas_web_app:application"]
