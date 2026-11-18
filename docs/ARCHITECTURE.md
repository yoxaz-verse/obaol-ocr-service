# Architecture

## Components

The API validates/authenticates requests and handles synchronous work. Redis provides distributed rate limits, queue admission, async source/status/result retention, and RQ transport. Workers perform the same extraction function as synchronous requests. Poppler supplies PDF metadata, rendering, and embedded word boxes; Tesseract handles raster pages.

## Extraction flow

1. Validate size, magic bytes, extension, MIME, PDF encryption/pages, and image safety.
2. Calculate projected PDF pixels from page media boxes and rotation.
3. For each page, run bounded Poppler `pdftotext -bbox` extraction.
4. Accept embedded text only when both configured word and character thresholds are met.
5. Otherwise render only that page at `PDF_DPI`, normalize contrast, optionally denoise, deskew, and run Tesseract.
6. Normalize all word boxes and combine pages without changing order.
7. Delete the isolated temporary directory in success and exception paths.

At most one rasterized page is retained per active extraction, excluding engine subprocess memory. Mixed PDFs report both source modes and a warning.

## Async lifecycle

Queue admission and idempotency reservation are atomic. Source bytes live at `obaol-ocr:source:<job-id>` with a TTL. The RQ payload contains filename/type/page metadata only. When claimed, queued counters decrease and active counters increase. The worker deletes the source and releases active counters in `finally`. Status metadata and RQ results expire after the configured retention period. Startup reconciliation repairs queued counters after crashes.

## Trust boundaries

Only the OBAOL backend calls the OCR API. Files and extracted text are untrusted. Redis and the worker are private. OCR has no authority to create, verify, issue, or share an OBAOL document. OBAOL retains the original file and presents OCR results for human review.
