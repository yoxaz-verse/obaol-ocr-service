import json
import logging
import sys
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("obaol_ocr_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("obaol_ocr_request_duration_seconds", "HTTP request duration", ["path"])
OCR_DURATION = Histogram("obaol_ocr_processing_duration_seconds", "OCR processing duration", ["mode"])
OCR_OUTCOMES = Counter("obaol_ocr_outcomes_total", "OCR outcomes", ["mode", "outcome"])
QUEUE_JOBS = Gauge("obaol_ocr_queue_jobs", "Queued OCR jobs")
QUEUE_BYTES = Gauge("obaol_ocr_queue_bytes", "Queued OCR source bytes")
ACTIVE_JOBS = Gauge("obaol_ocr_active_jobs", "Active OCR jobs")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "logger": record.name,
        }
        for field in ("request_id", "job_id", "duration_ms", "page_count", "outcome"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
