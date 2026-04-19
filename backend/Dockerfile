# ── base: shared deps layer ─────────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MC_OBJECT_STORAGE_ENDPOINT= \
    MC_OBJECT_STORAGE_REGION=us-east-1 \
    MC_OBJECT_STORAGE_BUCKET=missioncontrol \
    MC_OBJECT_STORAGE_SECURE=true \
    MC_OBJECT_STORAGE_ACCESS_KEY= \
    MC_OBJECT_STORAGE_ACCESS_SECRET=

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && groupadd --gid 3000 missioncontrol \
    && useradd --uid 3000 --gid 3000 --create-home --shell /usr/sbin/nologin missioncontrol \
    && rm -rf /var/lib/apt/lists/*

ARG INSTALL_CHROMA=false
COPY backend/requirements.txt backend/requirements-chroma.txt /app/
RUN if [ "$INSTALL_CHROMA" = "true" ]; then \
      pip install --no-cache-dir -r /app/requirements-chroma.txt; \
    else \
      pip install --no-cache-dir -r /app/requirements.txt; \
    fi

# ── dev: source is bind-mounted at runtime; runs as root for clean permissions
FROM base AS dev
EXPOSE 8008
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008", "--reload"]

# ── prod: source baked in, non-root user ────────────────────────────────────
FROM base AS prod

COPY backend /app
COPY web /app/web
RUN mkdir -p /app/chroma /data \
    && chown -R missioncontrol:missioncontrol /app /data

USER missioncontrol

EXPOSE 8008

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
