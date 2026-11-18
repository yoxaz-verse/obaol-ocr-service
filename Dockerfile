FROM python:3.12-slim-bookworm AS builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime
ARG VCS_REF=unknown
ARG VERSION=dev
LABEL org.opencontainers.image.title="OBAOL OCR Service" \
      org.opencontainers.image.source="https://github.com/yoxaz-verse/obaol-ocr-service" \
      org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.version="$VERSION" \
      org.opencontainers.image.licenses="Proprietary"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SERVICE_MODE=api \
    PORT=8000 \
    TEMP_DIR=/tmp/obaol-ocr
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl dumb-init poppler-utils tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 ocr \
    && useradd --system --uid 10001 --gid ocr --home-dir /app --shell /usr/sbin/nologin ocr \
    && mkdir -p /app /tmp/obaol-ocr \
    && chown -R ocr:ocr /app /tmp/obaol-ocr
COPY --from=builder /opt/venv /opt/venv
COPY --chown=ocr:ocr app /app/app
WORKDIR /app
RUN chmod 0555 /app/app/entrypoint.sh
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl --fail --silent http://127.0.0.1:${PORT}/health/live || exit 1
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/app/app/entrypoint.sh"]
