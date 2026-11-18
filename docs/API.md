# API reference

Base URL examples use `https://ocr.example.com`. Requests and responses use JSON except multipart uploads. Every response includes `X-Request-ID`; callers may supply an `X-Request-ID` up to 128 characters.

## Authentication

Send `X-API-Key` on `/v1/*` and `/metrics`. `API_KEY` is the current key. `API_KEYS` is an optional comma-separated rotation list; remove previous keys after all callers migrate. Health endpoints are unauthenticated and reveal no secrets.

## Upload rules

The multipart field name is `file`. The filename extension, declared MIME, and magic bytes must agree. Supported combinations are `.pdf`/`application/pdf`, `.png`/`image/png`, and `.jpg` or `.jpeg`/`image/jpeg`. Encrypted, malformed, empty, oversized, decompression-bomb, excessive-page, and excessive-pixel documents are rejected before OCR.

## `POST /v1/ocr`

Runs OCR synchronously and retains no result. Success is HTTP 200:

```json
{
  "schemaVersion": "1.0",
  "text": "Invoice 123",
  "pageCount": 1,
  "durationMs": 82,
  "language": "eng",
  "engine": "tesseract+poppler",
  "engineVersion": "5.5.2",
  "warnings": [],
  "requestId": "1458f3ce-9ea0-4aae-9d1c-4243ac836972",
  "embeddedTextPageCount": 1,
  "ocrPageCount": 0,
  "pages": [{
    "page": 1,
    "width": 612,
    "height": 792,
    "rotation": 0,
    "originalRotation": 0,
    "deskewAngle": 0.0,
    "source": "embeddedText",
    "text": "Invoice 123",
    "confidence": 1.0,
    "wordCount": 2,
    "words": [{"text": "Invoice", "confidence": 1.0, "page": 1,
      "box": {"x": 0.08, "y": 0.06, "width": 0.12, "height": 0.02}}]
  }]
}
```

`box` coordinates are normalized to `0..1`. `rotation` remains the v1 deskew-compatible integer; `originalRotation` reports PDF/EXIF orientation and `deskewAngle` provides the precise correction. Embedded text receives confidence `1.0`; this indicates extraction certainty, not semantic correctness.

## `POST /v1/jobs`

Accepts the same multipart input and optional `Idempotency-Key` of 1–200 characters. A stable OBAOL document/revision ID is recommended. HTTP 202 returns:

```json
{"id":"uuid","status":"queued","statusUrl":"https://ocr.example.com/v1/jobs/uuid","expiresInSeconds":86400,"requestId":"uuid"}
```

Concurrent submissions with the same key resolve to the same job. A saturated queue returns 503 with `Retry-After: 30`.

## `GET /v1/jobs/{id}`

States are `queued`, `processing`, `completed`, `failed`, `canceled`, and `expired`. Completed responses contain `result`; failed responses contain a safe `error` and `errorCode`. Optional `queuedAt`, `startedAt`, and `endedAt` fields are ISO-8601 UTC timestamps. Poll with exponential backoff (2, 4, 8, then 10 seconds) and stop at any terminal state.

## `DELETE /v1/jobs/{id}`

Returns 204. Queued jobs are removed; running jobs receive an RQ stop command. Source bytes are deleted immediately. A missing or already expired job returns 404. Cancellation is best-effort at the operating-system process boundary.

## Errors

```json
{"detail":"PDF exceeds the configured page limit","code":"LIMIT_EXCEEDED","requestId":"uuid","validation":null}
```

| HTTP | Code | Meaning |
|---:|---|---|
| 400/415/422 | `INVALID_FILE` | Invalid multipart input, type, or document structure |
| 401 | `UNAUTHORIZED` | Missing or invalid API key |
| 404 | `NOT_FOUND` | Job or disabled endpoint unavailable |
| 413 | `LIMIT_EXCEEDED` | Byte, page, or pixel limit exceeded |
| 429 | `RATE_LIMITED` | Distributed request limit exceeded |
| 503 | `QUEUE_FULL` or `DEPENDENCY_UNAVAILABLE` | Capacity or Redis/engine failure |
| 504 | `OCR_TIMEOUT` | Page or document deadline exceeded |
| 500 | `PROCESSING_FAILED` | Sanitized unexpected processing failure |

## Other endpoints

`GET /v1/capabilities` returns formats, languages, sync/async availability, and configured limits. `/health/ready` probes Tesseract, Poppler, temporary storage, and Redis read/write. `/metrics` returns Prometheus text and requires authentication.
