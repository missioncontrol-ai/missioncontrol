FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY integrations/missioncontrol-mcp /app
RUN pip install --no-cache-dir .

CMD ["missioncontrol-mcp", "daemon"]
