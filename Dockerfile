FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_ROOT_USER_ACTION=ignore \
    QT_QPA_PLATFORM=offscreen \
    PAPAGUI_DATA_DIR=/data \
    PAPAGUI_SOURCE_DIR=/source \
    PAPAGUI_SETTINGS_DIR=/config

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        antiword \
        catdoc \
        libreoffice-writer \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/papagui

COPY requirements-indexer-lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements-indexer-lock.txt

COPY app ./app

VOLUME ["/data", "/config"]
ENTRYPOINT ["python", "-m", "app.services.index_service"]
CMD ["--source", "/source", "--data", "/data", "--interval-seconds", "86400"]
