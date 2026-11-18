# Operations runbook

## Monitor

Scrape authenticated `/metrics`. Dashboard request rate/p95 latency, OCR p95 duration, queue jobs/bytes, active jobs, outcomes, container CPU/RAM, Redis memory, evictions, persistence health, and worker count.

Recommended alerts:

- Readiness failing for 2 minutes.
- Queue depth above 70% for 10 minutes or oldest job older than 5 minutes.
- Redis memory above 75%, any eviction, or failed persistence.
- OCR failure above 5% or timeouts above 2% over 15 minutes.
- Worker count zero while queue is non-empty.
- Container memory above 85% or repeated OOM restarts.
- `/tmp` usage above 80%.

## Capacity

Start with API: 2 CPU/1–2 GB and two Gunicorn workers. Worker: 2 CPU/2 GB and one RQ worker per container. Scale workers horizontally when queue age grows; do not add concurrency inside one memory-constrained container. Redis capacity must cover queued source bytes, RQ metadata/results, persistence overhead, and at least 30% headroom.

## Incidents

- **Queue grows:** confirm workers and Redis, inspect CPU/memory, then add identical worker replicas. Do not raise admission limits before capacity.
- **Stuck job:** compare its `startedAt` with `JOB_TIMEOUT_SECONDS`; DELETE the job, inspect worker logs by job ID, and restart only the affected worker.
- **Counter drift:** restart an API/worker to run reconciliation. Confirm queue metrics match RQ queue contents.
- **Redis unavailable:** keep API unready, restore the persistent Redis app, verify AOF, then start workers before API. OBAOL should retain files for retry/manual review.
- **High memory/OOM:** lower `PDF_DPI`, page/pixel limits, or worker replicas per host. Verify documents are processed page-at-a-time.
- **Low OCR accuracy:** inspect source resolution/orientation, confidence and warnings; use `PREPROCESSING_MODE=accuracy` only after measuring CPU impact.
- **Suspected data exposure:** rotate API keys, isolate Redis/API, preserve sanitized audit logs, delete affected job/source keys, and apply the company incident process.

## Upgrades

Deploy one worker using the new immutable SHA, run async smoke tests, migrate remaining workers, then API. Verify readiness, metrics, image OCR, searchable PDF bypass, scanned PDF OCR, cancellation, and restart persistence. Roll back API and worker to the previous SHA together.
