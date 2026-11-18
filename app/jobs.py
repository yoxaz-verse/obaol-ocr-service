import time
import uuid
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from rq import Queue, get_current_job
from rq.command import send_stop_job_command
from rq.job import Job

from app.config import Settings, get_settings
from app.errors import DependencyUnavailableError, ServiceError
from app.ocr import extract
from app.validation import ValidatedUpload

QUEUE_NAME = "obaol-ocr"
PREFIX = "obaol-ocr"
QUEUED_JOBS_KEY = f"{PREFIX}:queued-jobs"
QUEUED_BYTES_KEY = f"{PREFIX}:queued-bytes"
ACTIVE_JOBS_KEY = f"{PREFIX}:active-jobs"
ACTIVE_BYTES_KEY = f"{PREFIX}:active-bytes"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def redis_client(settings: Settings | None = None) -> Redis[bytes]:
    config = settings or get_settings()
    return Redis.from_url(config.redis_url.get_secret_value(), decode_responses=False)


def queue(settings: Settings | None = None) -> Queue:
    config = settings or get_settings()
    return Queue(QUEUE_NAME, connection=redis_client(config), default_timeout=config.job_timeout_seconds)


def _source_key(job_id: str) -> str:
    return f"{PREFIX}:source:{job_id}"


def _status_key(job_id: str) -> str:
    return f"{PREFIX}:status:{job_id}"


def _expired_key(job_id: str) -> str:
    return f"{PREFIX}:expired:{job_id}"


def _save_status(connection: Redis[bytes], job_id: str, settings: Settings, **values: object) -> None:
    key = _status_key(job_id)
    connection.hset(key, mapping={name: str(value) for name, value in values.items() if value is not None})
    connection.expire(key, settings.result_ttl_seconds)


def _claim_source(connection: Redis[bytes], job: Job, settings: Settings) -> bytes:
    source = connection.get(_source_key(job.id))
    if source is None:
        raise RuntimeError("OCR source expired before processing")
    size = int(job.meta.get("source_bytes", len(source)))
    if not job.meta.get("admission_released"):
        script = connection.register_script("""
            redis.call('set', KEYS[1], math.max(0, tonumber(redis.call('get', KEYS[1]) or '0') - 1))
            local bytes = tonumber(redis.call('get', KEYS[2]) or '0')
            redis.call('set', KEYS[2], math.max(0, bytes - tonumber(ARGV[1])))
            redis.call('incr', KEYS[3]); redis.call('incrby', KEYS[4], ARGV[1]); return 1
        """)
        script(keys=[QUEUED_JOBS_KEY, QUEUED_BYTES_KEY, ACTIVE_JOBS_KEY, ACTIVE_BYTES_KEY], args=[size])
        job.meta["admission_released"] = True
        job.meta["active_released"] = False
        job.save_meta()  # type: ignore[no-untyped-call]
    _save_status(connection, job.id, settings, status="processing", started_at=_now())
    return source


def _release_active(connection: Redis[bytes], job: Job) -> None:
    if job.meta.get("active_released"):
        return
    size = int(job.meta.get("source_bytes", 0))
    script = connection.register_script("""
        redis.call('set', KEYS[1], math.max(0, tonumber(redis.call('get', KEYS[1]) or '0') - 1))
        local bytes = tonumber(redis.call('get', KEYS[2]) or '0')
        redis.call('set', KEYS[2], math.max(0, bytes - tonumber(ARGV[1])))
        return 1
    """)
    script(keys=[ACTIVE_JOBS_KEY, ACTIVE_BYTES_KEY], args=[size])
    job.meta["active_released"] = True
    job.save_meta()  # type: ignore[no-untyped-call]


def run_ocr_job(metadata: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    current = get_current_job()
    if current is None:
        raise RuntimeError("OCR worker job context is unavailable")
    connection: Redis[bytes] = current.connection
    source = _claim_source(connection, current, settings)
    try:
        upload = ValidatedUpload(
            data=source,
            filename=metadata["filename"],
            media_type=metadata["media_type"],
            page_count=int(metadata["page_count"]),
        )
        result = None
        for attempt, delay in enumerate((0, 10, 30), start=1):
            if delay:
                time.sleep(delay)
            try:
                result = extract(
                    upload,
                    settings,
                    timeout_seconds=settings.job_timeout_seconds,
                    request_id=str(metadata.get("request_id") or ""),
                ).model_dump(mode="json", by_alias=True)
                break
            except DependencyUnavailableError:
                if attempt == 3:
                    raise
        if result is None:
            raise RuntimeError("OCR processing produced no result")
        _save_status(connection, current.id, settings, status="completed", ended_at=_now())
        return result
    except ServiceError as exc:
        _save_status(
            connection,
            current.id,
            settings,
            status="failed",
            error_code=exc.code,
            error=exc.detail,
            ended_at=_now(),
        )
        raise
    except Exception:
        _save_status(
            connection,
            current.id,
            settings,
            status="failed",
            error_code="PROCESSING_FAILED",
            error="OCR processing failed",
            ended_at=_now(),
        )
        raise
    finally:
        connection.delete(_source_key(current.id))
        _release_active(connection, current)


def enqueue(
    upload: ValidatedUpload,
    idempotency_key: str | None,
    settings: Settings,
    request_id: str | None = None,
) -> tuple[Job, bool]:
    connection = redis_client(settings)
    job_id = str(uuid.uuid4())
    idempotency_redis_key = f"{PREFIX}:idempotency:{idempotency_key}" if idempotency_key else ""
    script = connection.register_script("""
        if KEYS[3] ~= '' then
            local existing = redis.call('get', KEYS[3])
            if existing then return {0, existing} end
        end
        local jobs = tonumber(redis.call('get', KEYS[1]) or '0')
        local bytes = tonumber(redis.call('get', KEYS[2]) or '0')
        if jobs >= tonumber(ARGV[1]) or bytes + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return {-1, ''} end
        redis.call('incr', KEYS[1]); redis.call('incrby', KEYS[2], ARGV[2])
        if KEYS[3] ~= '' then redis.call('set', KEYS[3], ARGV[4], 'EX', ARGV[5]) end
        return {1, ARGV[4]}
    """)
    result = script(
        keys=[QUEUED_JOBS_KEY, QUEUED_BYTES_KEY, idempotency_redis_key],
        args=[
            settings.max_queued_jobs,
            len(upload.data),
            settings.max_queued_bytes,
            job_id,
            settings.result_ttl_seconds,
        ],
    )
    admitted = int(result[0])
    returned_id = result[1].decode() if isinstance(result[1], bytes) else str(result[1])
    if admitted == 0:
        return Job.fetch(returned_id, connection=connection), False
    if admitted == -1:
        raise OverflowError("OCR queue is at capacity")
    try:
        connection.set(_source_key(job_id), upload.data, ex=settings.result_ttl_seconds)
        _save_status(
            connection,
            job_id,
            settings,
            status="queued",
            queued_at=_now(),
            request_id=request_id,
        )
        job = queue(settings).enqueue(
            run_ocr_job,
            {
                "filename": upload.filename,
                "media_type": upload.media_type,
                "page_count": upload.page_count,
                "request_id": request_id,
            },
            job_id=job_id,
            job_timeout=settings.job_timeout_seconds,
            result_ttl=settings.result_ttl_seconds,
            failure_ttl=settings.result_ttl_seconds,
            meta={"source_bytes": len(upload.data), "admission_released": False, "active_released": True},
        )
        return job, True
    except Exception:
        connection.decr(QUEUED_JOBS_KEY)
        connection.decrby(QUEUED_BYTES_KEY, len(upload.data))
        connection.delete(_source_key(job_id), _status_key(job_id))
        if idempotency_redis_key:
            connection.delete(idempotency_redis_key)
        raise


def job_state(job_id: str, settings: Settings) -> dict[str, Any]:
    connection = redis_client(settings)
    status_data = connection.hgetall(_status_key(job_id))
    decoded = {key.decode(): value.decode() for key, value in status_data.items()}
    try:
        job = Job.fetch(job_id, connection=connection)
    except Exception:
        return {"id": job_id, "status": "expired", "result": None, "error": None}
    status_value = job.get_status(refresh=True)
    rq_status = str(getattr(status_value, "value", status_value))
    mapping = {
        "deferred": "queued",
        "scheduled": "queued",
        "queued": "queued",
        "started": "processing",
        "finished": "completed",
        "failed": "failed",
        "stopped": "canceled",
        "canceled": "canceled",
    }
    status = decoded.get("status", mapping.get(rq_status, "failed"))
    return {
        "id": job_id,
        "status": status,
        "result": job.result if status == "completed" else None,
        "error": decoded.get("error") if status == "failed" else None,
        "error_code": decoded.get("error_code"),
        "queued_at": decoded.get("queued_at"),
        "started_at": decoded.get("started_at"),
        "ended_at": decoded.get("ended_at"),
        "request_id": decoded.get("request_id"),
    }


def delete_job(job_id: str, settings: Settings) -> bool:
    connection = redis_client(settings)
    try:
        job = Job.fetch(job_id, connection=connection)
    except Exception:
        return False
    status_value = job.get_status(refresh=True)
    status = str(getattr(status_value, "value", status_value))
    if status in {"queued", "deferred", "scheduled"}:
        job.cancel()
        if not job.meta.get("admission_released"):
            connection.set(QUEUED_JOBS_KEY, max(0, int(connection.get(QUEUED_JOBS_KEY) or 0) - 1))
            connection.set(
                QUEUED_BYTES_KEY,
                max(0, int(connection.get(QUEUED_BYTES_KEY) or 0) - int(job.meta.get("source_bytes", 0))),
            )
    elif status == "started":
        send_stop_job_command(connection, job_id)
    _save_status(connection, job_id, settings, status="canceled", ended_at=_now())
    connection.set(_expired_key(job_id), "canceled", ex=settings.expiration_marker_ttl_seconds)
    connection.delete(_source_key(job_id))
    job.delete(remove_from_queue=True)
    return True


def reconcile_counters(settings: Settings) -> dict[str, int]:
    connection = redis_client(settings)
    queued_jobs = queue(settings).get_jobs()
    queued_bytes = sum(int(job.meta.get("source_bytes", 0)) for job in queued_jobs)
    connection.mset({QUEUED_JOBS_KEY: len(queued_jobs), QUEUED_BYTES_KEY: queued_bytes})
    return {"queuedJobs": len(queued_jobs), "queuedBytes": queued_bytes}
