# Configuration reference

| Variable | Default | Purpose |
|---|---:|---|
| `SERVICE_MODE` | `api` | Run `api` or `worker` entrypoint |
| `PORT` | `8000` | API HTTP port |
| `API_KEY` | required | Current shared key, minimum 24 characters |
| `API_KEYS` | empty | Comma-separated previous keys during rotation |
| `REDIS_URL` | `redis://localhost:6379/0` | Private Redis URL |
| `REDIS_REQUIRED` | `true` | Require Redis readiness and distributed controls |
| `OCR_LANGUAGE` | `eng` | Installed Tesseract language |
| `MAX_UPLOAD_BYTES` | `15728640` | Maximum source bytes |
| `MAX_PDF_PAGES` | `20` | Maximum PDF pages |
| `MAX_IMAGE_PIXELS` | `40000000` | Per-page decoded/rendered pixels |
| `MAX_DOCUMENT_PIXELS` | `200000000` | Total projected PDF pixels |
| `PDF_DPI` | `180` | Poppler raster DPI |
| `SYNC_TIMEOUT_SECONDS` | `120` | Synchronous document deadline |
| `PAGE_TIMEOUT_SECONDS` | `45` | Poppler/Tesseract page deadline |
| `JOB_TIMEOUT_SECONDS` | `600` | RQ job deadline |
| `RESULT_TTL_SECONDS` | `86400` | Source/status/result/idempotency retention |
| `EXPIRATION_MARKER_TTL_SECONDS` | `3600` | Short cancellation/expiry marker retention |
| `MAX_QUEUED_JOBS` | `100` | Queue admission count |
| `MAX_QUEUED_BYTES` | `536870912` | Queue admission source bytes |
| `RATE_LIMIT_REQUESTS` | `30` | Requests per distributed window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate window seconds |
| `WEB_CONCURRENCY` | `2` | Gunicorn API workers |
| `TEMP_DIR` | `/tmp/obaol-ocr` | Ephemeral processing directory |
| `ALLOWED_ORIGINS` | empty | Explicit browser CORS origins |
| `TRUST_PROXY_HEADERS` | `false` | Trust proxy-supplied client IP |
| `EMBEDDED_TEXT_MIN_WORDS` | `5` | Embedded-text acceptance words |
| `EMBEDDED_TEXT_MIN_CHARS` | `20` | Embedded-text acceptance characters |
| `MINIMUM_CONFIDENCE` | `0.45` | Low-confidence warning threshold |
| `DESKEW_MAX_DEGREES` | `4` | Deskew search range |
| `OCR_PAGE_SEGMENTATION_MODE` | `3` | Tesseract PSM |
| `PREPROCESSING_MODE` | `balanced` | `fast`, `balanced`, or `accuracy` |
| `METRICS_ENABLED` | `true` | Serve authenticated `/metrics` |

Startup rejects invalid combinations: result TTL must exceed job timeout, synchronous timeout must be at least the page timeout, document pixels must be at least page pixels, and confidence must be within `0..1`.
