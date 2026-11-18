# OBAOL OCR Service

Self-hosted English OCR for PDF, PNG, and JPEG documents. The service returns combined text, page text, confidence, and normalized word coordinates. It deliberately contains no invoice, party, total, or trade-document mapping logic.

One image runs in two modes:

- `SERVICE_MODE=api`: public FastAPI HTTPS service.
- `SERVICE_MODE=worker`: private Redis/RQ worker.

Searchable PDF pages use embedded Poppler text and coordinates. Only scanned or text-insufficient pages are rasterized and sent to Tesseract. PDF pages are processed individually to bound memory.

## Five-minute setup

```bash
cp .env.example .env
# Replace API_KEY with: openssl rand -hex 32
docker compose up --build
curl http://localhost:8000/health/ready
curl -H "X-API-Key: $API_KEY" -F "file=@sample.pdf" http://localhost:8000/v1/ocr
```

Requirements are Docker Compose and at least 2 CPU/2 GB RAM. Redis is private and persistent in the Compose configuration.

## API overview

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | Public process liveness |
| GET | `/health/ready` | Public dependency readiness |
| GET | `/metrics` | Authenticated Prometheus metrics |
| GET | `/v1/capabilities` | Formats, language, and active limits |
| POST | `/v1/ocr` | Synchronous ephemeral OCR |
| POST | `/v1/jobs` | Queue durable asynchronous OCR |
| GET | `/v1/jobs/{id}` | Poll job and retrieve result |
| DELETE | `/v1/jobs/{id}` | Cancel and remove a job early |

All `/v1` and `/metrics` calls require `X-API-Key`. Browser CORS is disabled by default. Use synchronous OCR for small files that fit the 120-second deadline; use jobs for larger or multi-page documents.

```bash
curl --fail-with-body -H "X-API-Key: $OCR_API_KEY" \
  -F "file=@shipping-bill.pdf;type=application/pdf" \
  https://ocr.example.com/v1/ocr

curl --fail-with-body -H "X-API-Key: $OCR_API_KEY" \
  -H "Idempotency-Key: document-123-revision-1" \
  -F "file=@scan.pdf" https://ocr.example.com/v1/jobs
```

Responses retain the v1 camelCase contract and add optional production metadata such as `requestId`, `source`, `originalRotation`, `deskewAngle`, `embeddedTextPageCount`, and `ocrPageCount`.

## Data lifecycle

- Synchronous uploads exist only in request memory and an isolated `/tmp` directory. Temporary files are removed before the response.
- Async source bytes use a dedicated Redis key. RQ arguments contain only metadata and the source key identity.
- Source, result, status, and idempotency data expire after `RESULT_TTL_SECONDS` (24 hours by default).
- A canceled job loses its source immediately. OBAOL remains responsible for its original uploaded document.

## Documentation

- [API reference](docs/API.md)
- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [OBAOL integration guide](docs/INTEGRATION.md)
- [CapRover deployment](docs/CAPROVER.md)
- [Security guide](docs/SECURITY.md)
- [Operations runbook](docs/OPERATIONS.md)
- [Performance tuning](docs/PERFORMANCE.md)

## Development

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
API_KEY=test-api-key-at-least-twenty-four-characters REDIS_REQUIRED=false pytest
ruff check app tests
mypy app
bandit -q -r app
pip-audit -r requirements.txt
```

CI verifies linting, strict types, tests, dependency security, a non-root/read-only container, multi-platform builds, SBOM/provenance, and the final image vulnerability scan. Production deployments must use immutable `sha-...` or semantic-version tags, never `latest`.
