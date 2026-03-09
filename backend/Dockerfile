FROM python:3.11-slim

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
    && apt-get install -y --no-install-recommends git ca-certificates \
    && groupadd --gid 3000 missioncontrol \
    && useradd --uid 3000 --gid 3000 --create-home --shell /usr/sbin/nologin missioncontrol \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app
COPY web /app/web
RUN mkdir -p /app/chroma /data \
    && chown -R missioncontrol:missioncontrol /app /data

USER missioncontrol

EXPOSE 8008

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008"]
