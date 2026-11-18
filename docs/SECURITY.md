# Security guide

## Secrets and network

- Generate API keys with at least 32 random bytes and store them only in CapRover secrets and the OBAOL backend.
- Rotate by setting the new `API_KEY`, placing the previous key in `API_KEYS`, migrating callers, then removing it.
- Expose only the API over forced HTTPS. Worker and Redis have no public domains.
- Protect Redis with authentication, private networking, persistence permissions, and TLS when traffic crosses hosts.
- Set `TRUST_PROXY_HEADERS=true` only behind a trusted proxy that overwrites `X-Forwarded-For`.

## File handling

Magic bytes, MIME, extension, structure, encryption, pages, decoded pixels, projected PDF pixels, deadlines, and queue capacity are validated. Processing occurs as UID 10001 on a read-only root filesystem with bounded writable `/tmp`. Tesseract and Poppler remain a hostile-file boundary; keep Debian and Python dependencies patched and retain container CPU/memory limits.

## Data and logs

Redis temporarily contains document bytes and extracted results. Default retention is 24 hours. Backups must not retain OCR data longer than the stated policy. Logs contain request/job IDs, timing, counts, status, and sanitized errors only—never API keys, Redis URLs, filenames, source text, uploaded bytes, or OCR results.

## Threat assumptions

The service is backend-to-backend, not multi-tenant authorization. Possession of an API key authorizes OCR and job lookup. Job UUIDs are not a substitute for authentication. OCR output is untrusted and may contain prompt injection, malicious text, or recognition errors; consuming systems must display it for review and escape it in HTML, logs, and downstream prompts.
