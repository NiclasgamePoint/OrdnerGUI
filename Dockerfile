# Compatibility Dockerfile for `docker build .`; canonical file: deploy/server/Dockerfile.
# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

LABEL org.opencontainers.image.title="PapaGUI Server" \
      org.opencontainers.image.version="0.4.3" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PAPAGUI_SOURCE_DIR=/source \
    PAPAGUI_DATA_DIR=/data \
    PAPAGUI_CONFIG_DIR=/config

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        antiword \
        catdoc \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/papagui
COPY packages/contracts ./packages/contracts
COPY packages/server ./packages/server
RUN python -m pip install --no-cache-dir -r ./packages/server/requirements-lock.txt \
    && python -m pip install --no-cache-dir --no-deps ./packages/contracts \
    && python -m pip install --no-cache-dir --no-deps ./packages/server \
    && python -m pip check

VOLUME ["/data", "/config"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import json,urllib.request; json.load(urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3))"]

ENTRYPOINT ["papagui-server"]
CMD ["serve", "--source", "/source", "--data", "/data", "--config", "/config", "--port", "8765"]
