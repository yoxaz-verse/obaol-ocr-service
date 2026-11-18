from io import BytesIO
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from app.errors import LimitExceededError
from app.main import app
from app.models import OcrResult

client = TestClient(app)
headers = {"X-API-Key": "test-api-key-at-least-twenty-four-characters"}


def image() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (20, 20), "white").save(stream, "PNG")
    return stream.getvalue()


def test_health_is_public() -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


def test_endpoint_requires_api_key() -> None:
    assert client.get("/v1/capabilities").status_code == 401


def test_capabilities() -> None:
    response = client.get("/v1/capabilities", headers=headers)
    assert response.status_code == 200
    assert "application/pdf" in response.json()["formats"]


def test_synchronous_ocr() -> None:
    result = OcrResult(
        text="Hello",
        page_count=0,
        duration_ms=1,
        language="eng",
        engine="tesseract",
        engine_version="5",
        warnings=[],
        pages=[],
    )
    with patch("app.main.extract", return_value=result):
        response = client.post("/v1/ocr", headers=headers, files={"file": ("scan.png", image(), "image/png")})
    assert response.status_code == 200
    assert response.json()["text"] == "Hello"
    assert response.json()["pageCount"] == 0


def test_metrics_and_request_id() -> None:
    response = client.get("/metrics", headers={**headers, "X-Request-ID": "known-request"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "known-request"
    assert "obaol_ocr_requests_total" in response.text


def test_request_size_and_validation_errors() -> None:
    oversized = client.post(
        "/v1/ocr",
        headers={**headers, "Content-Length": "999999999"},
        files={"file": ("x.png", image(), "image/png")},
    )
    assert oversized.status_code == 413
    malformed = client.post("/v1/ocr", headers=headers, files={"file": ("x.txt", b"bad", "text/plain")})
    assert malformed.status_code == 415
    assert malformed.json()["code"] == "INVALID_FILE"


def test_async_endpoints() -> None:
    job = MagicMock(id="00000000-0000-0000-0000-000000000001")
    with patch("app.main.enqueue", return_value=(job, True)):
        accepted = client.post(
            "/v1/jobs",
            headers={**headers, "Idempotency-Key": "doc-r1"},
            files={"file": ("x.png", image(), "image/png")},
        )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"

    state = {"id": job.id, "status": "completed", "result": None, "error": None}
    with patch("app.main.job_state", return_value=state):
        assert client.get(f"/v1/jobs/{job.id}", headers=headers).json()["status"] == "completed"
    with patch("app.main.delete_job", return_value=True):
        assert client.delete(f"/v1/jobs/{job.id}", headers=headers).status_code == 204
    with patch("app.main.delete_job", return_value=False):
        assert client.delete(f"/v1/jobs/{job.id}", headers=headers).status_code == 404


def test_queue_full_and_bad_idempotency() -> None:
    bad = client.post(
        "/v1/jobs",
        headers={**headers, "Idempotency-Key": "x" * 201},
        files={"file": ("x.png", image(), "image/png")},
    )
    assert bad.status_code == 400
    with patch("app.main.enqueue", side_effect=OverflowError("OCR queue is at capacity")):
        full = client.post("/v1/jobs", headers=headers, files={"file": ("x.png", image(), "image/png")})
    assert full.status_code == 503
    assert full.headers["retry-after"] == "30"


def test_sync_service_error_is_standardized() -> None:
    with patch("app.main.extract", side_effect=LimitExceededError("too many pixels")):
        response = client.post("/v1/ocr", headers=headers, files={"file": ("scan.png", image(), "image/png")})
    assert response.status_code == 413
    assert response.json()["code"] == "LIMIT_EXCEEDED"
    assert response.json()["requestId"]
