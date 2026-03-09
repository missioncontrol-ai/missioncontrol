FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY integrations/missioncontrol-mcp /app
RUN pip install --no-cache-dir .

CMD ["missioncontrol-mcp", "daemon"]
