# CapRover deployment

Deploy three apps on the same CapRover cluster: a private Redis app, public `obaol-ocr-api`, and private `obaol-ocr-worker`. API and worker must use the identical immutable image tag.

## 1. Publish the image

Create this project as its own GitHub repository under `yoxaz-verse`. GitHub Actions uses `GITHUB_TOKEN` to publish `ghcr.io/yoxaz-verse/obaol-ocr-service:sha-<full-commit>`. Make the package visible to the deployment account or configure CapRover's registry credentials with a GitHub PAT that has `read:packages`.

## 2. Create Redis

Create a one-click Redis app such as `obaol-ocr-redis`. Do not enable a public domain. Enable a persistent volume at `/data`, AOF persistence, a password, backups, and memory monitoring. Use its internal service name in `REDIS_URL`, for example `redis://:PASSWORD@srv-captain--obaol-ocr-redis:6379/0`. Never expose port 6379 publicly.

Set Redis eviction to `noeviction`; queue admission must reject cleanly rather than losing source/result keys. Allocate at least `MAX_QUEUED_BYTES × 2.3` plus 30% headroom, and confirm AOF recovery before production use.

## 3. Create the API app

Create `obaol-ocr-api`, choose “deploy via image name,” and use an immutable `sha-...` tag. Set container HTTP port to `8000`, enable HTTPS and forced HTTPS, and configure:

```text
SERVICE_MODE=api
API_KEY=<64-character-random-secret>
REDIS_URL=<private Redis URL>
REDIS_REQUIRED=true
WEB_CONCURRENCY=2
```

Add the remaining limits from `.env.example` as required. Configure the health path `/health/ready`. Set the proxy upload limit slightly above `MAX_UPLOAD_BYTES`, request timeout above `SYNC_TIMEOUT_SECONDS`, and rate limiting at the proxy. Allocate approximately 2 CPU and 1–2 GB RAM initially. A read-only root filesystem is supported when `/tmp` is writable; mount `/tmp` as ephemeral storage if your CapRover/runtime policy requires it.

Recommended production values:

```text
PREPROCESSING_MODE=balanced
PDF_DPI=180
MAX_UPLOAD_BYTES=15728640
MAX_PDF_PAGES=20
MAX_IMAGE_PIXELS=40000000
MAX_DOCUMENT_PIXELS=200000000
SYNC_TIMEOUT_SECONDS=120
PAGE_TIMEOUT_SECONDS=45
RESULT_TTL_SECONDS=86400
TRUST_PROXY_HEADERS=true
```

Use `TRUST_PROXY_HEADERS=true` only because CapRover overwrites the forwarded client address. Configure the upstream body limit to 16 MiB plus multipart overhead and its response timeout to at least 130 seconds. Keep `/metrics` inaccessible from the public internet where possible; the API key is still required.

## 4. Create the worker app

Create `obaol-ocr-worker` from the same image tag. Do not enable a public domain. Use the same `API_KEY`, `REDIS_URL`, OCR limits, TTL, and job timeout, with:

```text
SERVICE_MODE=worker
```

Allocate roughly 2 CPU and 2 GB RAM per replica and writable ephemeral `/tmp`. Start one replica, observe queue latency and memory, then scale horizontally. Each replica consumes jobs from the shared `obaol-ocr` queue.

Set the worker health strategy to process/container health rather than HTTP because worker mode intentionally opens no public port. Graceful stop must be at least 30 seconds; RQ handles termination and source cleanup on normal completion. Keep API and worker environment values identical except `SERVICE_MODE`, `PORT`, and `WEB_CONCURRENCY`.

## 5. Verify

```bash
curl https://ocr.example.com/health/live
curl https://ocr.example.com/health/ready
curl -H "X-API-Key: $OCR_API_KEY" https://ocr.example.com/v1/capabilities
curl -H "X-API-Key: $OCR_API_KEY" -F "file=@sample.png" https://ocr.example.com/v1/ocr
curl -H "X-API-Key: $OCR_API_KEY" https://ocr.example.com/metrics
```

Submit an async job, restart API and worker apps, and confirm the job remains available. Then DELETE it and confirm its status becomes expired. Verify the API and worker container user is UID 10001 in deployment inspection.

Also verify a searchable PDF reports `source: embeddedText`, a scanned PDF reports `source: ocr`, Redis has no remaining source key after completion, and the container starts with a read-only root filesystem plus a bounded `/tmp` mount.

## Upgrades and rollback

Deploy the new SHA to worker first and then API. Confirm readiness and a sample synchronous/asynchronous request before scaling down old replicas. Keep schema-compatible result changes under a new `schema_version`. Roll back both apps to the previous SHA if health, memory, or accuracy regresses. Do not deploy `latest` in production.
