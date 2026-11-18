# Performance tuning

Use measured production documents rather than raising every limit.

| Setting | Effect | Guidance |
|---|---|---|
| `PDF_DPI` | Accuracy, CPU, RAM scale roughly with pixel count | Keep 180; try 150 for throughput or 220 for small print |
| `PREPROCESSING_MODE` | `fast`, `balanced`, or `accuracy` | Keep balanced; accuracy adds median filtering |
| `EMBEDDED_TEXT_MIN_WORDS/CHARS` | Controls searchable-page bypass | Keep 5/20; raise if PDFs contain unreliable hidden OCR layers |
| `MINIMUM_CONFIDENCE` | Warning threshold only | Keep 0.45; tune from reviewed samples |
| `DESKEW_MAX_DEGREES` | Deskew search cost/range | Keep 4° for business scans |
| `OCR_PAGE_SEGMENTATION_MODE` | Tesseract layout assumption | Keep 3 for automatic mixed layouts |
| `WEB_CONCURRENCY` | Concurrent sync requests per container | Start at 2; ensure enough RAM for two pages |
| Worker replicas | Async throughput | Prefer more containers over multiple workers per container |

Benchmark a one-page image, searchable 20-page PDF, scanned 20-page PDF, rotated scan, and mixed PDF. Record p50/p95 latency, peak RSS, Redis bytes/job, CPU seconds/page, and recognition score against reviewed text. Re-run after engine, DPI, preprocessing, or base-image changes.

Queue admission must remain below practical worker capacity. As a baseline, size Redis for `MAX_QUEUED_BYTES` plus two times that amount for RQ/result/persistence overhead. Keep 30% memory headroom and use `noeviction`; clean rejection is safer than silent document loss.
