from unittest.mock import MagicMock, patch

import fakeredis

from app.config import Settings
from app.jobs import (
    ACTIVE_BYTES_KEY,
    ACTIVE_JOBS_KEY,
    QUEUED_BYTES_KEY,
    QUEUED_JOBS_KEY,
    delete_job,
    enqueue,
    job_state,
    reconcile_counters,
    run_ocr_job,
)
from app.models import OcrResult
from app.validation import ValidatedUpload


def config() -> Settings:
    return Settings(api_key="a" * 24, redis_required=True)


def upload() -> ValidatedUpload:
    return ValidatedUpload(b"payload", "x.png", "image/png", 1)


def test_enqueue_stores_source_and_is_idempotent() -> None:
    redis = fakeredis.FakeRedis()

    def admission(**_kwargs: object) -> list[object]:
        redis.incr(QUEUED_JOBS_KEY)
        redis.incrby(QUEUED_BYTES_KEY, 7)
        return [1, b"job-1"]

    redis.register_script = MagicMock(return_value=admission)
    queued_job = MagicMock(id="job-1")
    queue = MagicMock()
    queue.enqueue.return_value = queued_job
    with (
        patch("app.jobs.redis_client", return_value=redis),
        patch("app.jobs.queue", return_value=queue),
        patch("app.jobs.uuid.uuid4", return_value="job-1"),
    ):
        job, created = enqueue(upload(), "same", config(), "request-1")
    assert created and job.id == "job-1"
    assert redis.get("obaol-ocr:source:job-1") == b"payload"
    assert int(redis.get(QUEUED_JOBS_KEY) or 0) == 1
    assert int(redis.get(QUEUED_BYTES_KEY) or 0) == 7


def test_queue_capacity_and_reconciliation() -> None:
    redis = fakeredis.FakeRedis()
    redis.set(QUEUED_JOBS_KEY, 100)
    redis.register_script = MagicMock(return_value=lambda **_kwargs: [-1, b""])
    with patch("app.jobs.redis_client", return_value=redis):
        try:
            enqueue(upload(), None, config())
        except OverflowError:
            pass
        else:
            raise AssertionError("capacity limit was not enforced")
    first = MagicMock(meta={"source_bytes": 4})
    second = MagicMock(meta={"source_bytes": 6})
    queue = MagicMock()
    queue.get_jobs.return_value = [first, second]
    with patch("app.jobs.redis_client", return_value=redis), patch("app.jobs.queue", return_value=queue):
        result = reconcile_counters(config())
    assert result == {"queuedJobs": 2, "queuedBytes": 10}


def test_job_state_and_delete_missing() -> None:
    redis = fakeredis.FakeRedis()
    with (
        patch("app.jobs.redis_client", return_value=redis),
        patch("app.jobs.Job.fetch", side_effect=Exception("missing")),
    ):
        assert job_state("missing", config())["status"] == "expired"
        assert not delete_job("missing", config())


def test_worker_reads_source_and_cleans_up() -> None:
    redis = fakeredis.FakeRedis()
    redis.set("obaol-ocr:source:job-1", b"payload")
    redis.set(QUEUED_JOBS_KEY, 1)
    redis.set(QUEUED_BYTES_KEY, 7)
    job = MagicMock(
        id="job-1",
        connection=redis,
        meta={"source_bytes": 7, "admission_released": False, "active_released": True},
    )

    def counter_script(**_kwargs: object) -> int:
        if not job.meta["admission_released"]:
            redis.set(QUEUED_JOBS_KEY, 0)
            redis.set(QUEUED_BYTES_KEY, 0)
            redis.set(ACTIVE_JOBS_KEY, 1)
            redis.set(ACTIVE_BYTES_KEY, 7)
        else:
            redis.set(ACTIVE_JOBS_KEY, 0)
            redis.set(ACTIVE_BYTES_KEY, 0)
        return 1

    redis.register_script = MagicMock(return_value=counter_script)
    result = OcrResult(
        text="ok",
        page_count=0,
        duration_ms=1,
        language="eng",
        engine="test",
        engine_version="1",
        warnings=[],
        pages=[],
    )
    with patch("app.jobs.get_current_job", return_value=job), patch("app.jobs.extract", return_value=result):
        output = run_ocr_job(
            {
                "filename": "x.png",
                "media_type": "image/png",
                "page_count": 1,
                "request_id": "request",
            }
        )
    assert output["text"] == "ok"
    assert redis.get("obaol-ocr:source:job-1") is None
    assert int(redis.get(ACTIVE_JOBS_KEY) or 0) == 0


def test_delete_queued_job() -> None:
    redis = fakeredis.FakeRedis()
    redis.set(QUEUED_JOBS_KEY, 1)
    redis.set(QUEUED_BYTES_KEY, 7)
    job = MagicMock(id="job-1", meta={"source_bytes": 7, "admission_released": False})
    job.get_status.return_value = "queued"
    with patch("app.jobs.redis_client", return_value=redis), patch("app.jobs.Job.fetch", return_value=job):
        assert delete_job("job-1", config())
    job.cancel.assert_called_once()
    job.delete.assert_called_once()
    assert int(redis.get(QUEUED_JOBS_KEY) or 0) == 0
