import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.exceptions import RedisError

from app import __version__
from app.config import get_settings
from app.errors import DependencyUnavailableError, LimitExceededError, ServiceError
from app.jobs import (
    ACTIVE_JOBS_KEY,
    QUEUED_BYTES_KEY,
    QUEUED_JOBS_KEY,
    delete_job,
    enqueue,
    job_state,
    reconcile_counters,
    redis_client,
)
from app.models import Capabilities, JobAccepted, JobState, OcrResult
from app.observability import (
    ACTIVE_JOBS,
    OCR_DURATION,
    OCR_OUTCOMES,
    QUEUE_BYTES,
    QUEUE_JOBS,
    REQUEST_DURATION,
    REQUESTS,
    configure_logging,
)
from app.ocr import extract
from app.security import enforce_rate_limit, require_api_key
from app.validation import ValidatedUpload, validate_upload

settings = get_settings()
configure_logging()
logger = logging.getLogger("obaol-ocr")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings.redis_required:
        try:
            reconcile_counters(settings)
        except RedisError:
            logger.exception("queue counter reconciliation failed")
    yield


app = FastAPI(
    title="OBAOL OCR Service",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)

if settings.allowed_origins.strip():
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[item.strip() for item in settings.allowed_origins.split(",")],
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["X-API-Key", "Idempotency-Key", "Content-Type", "X-Request-ID"],
    )


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


@app.middleware("http")
async def request_context(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    started = time.monotonic()
    supplied = request.headers.get("x-request-id", "")
    request.state.request_id = supplied[:128] if supplied else str(uuid.uuid4())
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_upload_bytes + 1_048_576:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": "Request exceeds the configured size limit",
                        "code": "LIMIT_EXCEEDED",
                        "requestId": _request_id(request),
                    },
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Invalid Content-Length",
                    "code": "INVALID_FILE",
                    "requestId": _request_id(request),
                },
            )
    response = await call_next(request)
    response.headers["X-Request-ID"] = _request_id(request)
    route = request.url.path if not request.url.path.startswith("/v1/jobs/") else "/v1/jobs/{id}"
    REQUESTS.labels(request.method, route, str(response.status_code)).inc()
    REQUEST_DURATION.labels(route).observe(time.monotonic() - started)
    logger.info(
        "request completed",
        extra={
            "request_id": _request_id(request),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "outcome": response.status_code,
        },
    )
    return response


@app.exception_handler(ServiceError)
async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "code": exc.code,
            "requestId": _request_id(request),
            "validation": exc.validation,
        },
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    code = {
        400: "INVALID_FILE",
        401: "UNAUTHORIZED",
        404: "NOT_FOUND",
        413: "LIMIT_EXCEEDED",
        415: "INVALID_FILE",
        422: "INVALID_FILE",
        429: "RATE_LIMITED",
        503: "DEPENDENCY_UNAVAILABLE",
    }.get(exc.status_code, "REQUEST_FAILED")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail), "code": code, "requestId": _request_id(request)},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Request validation failed",
            "code": "INVALID_FILE",
            "requestId": _request_id(request),
            "validation": exc.errors(),
        },
    )


async def read_upload(file: UploadFile) -> ValidatedUpload:
    chunks = bytearray()
    while chunk := await file.read(1024 * 1024):
        chunks.extend(chunk)
        if len(chunks) > settings.max_upload_bytes:
            raise LimitExceededError("File exceeds the configured upload limit")
    return validate_upload(bytes(chunks), file.filename or "upload", file.content_type, settings)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
async def ready() -> dict[str, object]:
    checks: dict[str, bool] = {}
    for name, command in {"tesseract": ["tesseract", "--version"], "poppler": ["pdftoppm", "-v"]}.items():
        try:
            binary = shutil.which(command[0])
            if binary is None:
                raise OSError(f"{command[0]} is unavailable")
            subprocess.run(  # noqa: S603 - absolute executable resolved from controlled PATH
                [binary, *command[1:]], capture_output=True, check=True, timeout=3
            )
            checks[name] = True
        except (OSError, subprocess.SubprocessError):
            checks[name] = False
    try:
        Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(dir=settings.temp_dir)
        os.close(fd)
        os.unlink(path)
        checks["temporaryStorage"] = True
    except OSError:
        checks["temporaryStorage"] = False
    if settings.redis_required:
        try:
            connection = redis_client(settings)
            probe = f"obaol-ocr:ready:{uuid.uuid4()}"
            connection.setex(probe, 5, "ok")
            checks["redis"] = connection.get(probe) == b"ok"
            connection.delete(probe)
            QUEUE_JOBS.set(int(connection.get(QUEUED_JOBS_KEY) or 0))
            QUEUE_BYTES.set(int(connection.get(QUEUED_BYTES_KEY) or 0))
            ACTIVE_JOBS.set(int(connection.get(ACTIVE_JOBS_KEY) or 0))
        except RedisError:
            checks["redis"] = False
    if not all(checks.values()):
        raise HTTPException(503, detail={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


protected = [Depends(require_api_key), Depends(enforce_rate_limit)]


@app.get("/metrics", dependencies=protected)
async def metrics() -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(404, "Metrics are disabled")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/capabilities", response_model=Capabilities, dependencies=protected)
async def capabilities() -> Capabilities:
    return Capabilities(
        formats=["application/pdf", "image/png", "image/jpeg"],
        languages=[settings.ocr_language],
        synchronous=True,
        asynchronous=settings.redis_required,
        limits={
            "maxUploadBytes": settings.max_upload_bytes,
            "maxPdfPages": settings.max_pdf_pages,
            "maxImagePixels": settings.max_image_pixels,
            "maxDocumentPixels": settings.max_document_pixels,
            "pdfDpi": settings.pdf_dpi,
            "syncTimeoutSeconds": settings.sync_timeout_seconds,
        },
    )


@app.post("/v1/ocr", response_model=OcrResult, dependencies=protected)
async def synchronous_ocr(request: Request, file: UploadFile = File(...)) -> OcrResult:
    upload = await read_upload(file)
    started = time.monotonic()
    try:
        result = await run_in_threadpool(
            extract, upload, settings, settings.sync_timeout_seconds, _request_id(request)
        )
        OCR_OUTCOMES.labels("sync", "completed").inc()
        return result
    except ServiceError:
        OCR_OUTCOMES.labels("sync", "failed").inc()
        raise
    finally:
        OCR_DURATION.labels("sync").observe(time.monotonic() - started)


@app.post("/v1/jobs", response_model=JobAccepted, status_code=202, dependencies=protected)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None),
) -> JobAccepted:
    upload = await read_upload(file)
    if idempotency_key is not None and not 1 <= len(idempotency_key) <= 200:
        raise HTTPException(400, "Idempotency-Key must be between 1 and 200 characters")
    try:
        job, _created = enqueue(upload, idempotency_key, settings, _request_id(request))
    except OverflowError as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "30"}) from exc
    except RedisError as exc:
        raise DependencyUnavailableError("OCR queue is unavailable") from exc
    return JobAccepted(
        id=job.id,
        status_url=str(request.url_for("get_job", job_id=job.id)),
        expires_in_seconds=settings.result_ttl_seconds,
        request_id=_request_id(request),
    )


@app.get("/v1/jobs/{job_id}", response_model=JobState, dependencies=protected, name="get_job")
async def get_job(request: Request, job_id: str) -> JobState:
    state = job_state(job_id, settings)
    state["request_id"] = state.get("request_id") or _request_id(request)
    return JobState.model_validate(state)


@app.delete("/v1/jobs/{job_id}", status_code=204, dependencies=protected)
async def remove_job(job_id: str) -> None:
    if not delete_job(job_id, settings):
        raise HTTPException(404, "Job not found or already expired")
