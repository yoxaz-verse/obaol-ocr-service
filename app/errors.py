from typing import Any


class ServiceError(RuntimeError):
    def __init__(
        self, code: str, detail: str, status_code: int, validation: list[dict[str, Any]] | None = None
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.validation = validation


class InvalidFileError(ServiceError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__("INVALID_FILE", detail, status_code)


class LimitExceededError(ServiceError):
    def __init__(self, detail: str):
        super().__init__("LIMIT_EXCEEDED", detail, 413)


class ProcessingTimeoutError(ServiceError):
    def __init__(self, detail: str = "OCR processing exceeded its timeout"):
        super().__init__("OCR_TIMEOUT", detail, 504)


class DependencyUnavailableError(ServiceError):
    def __init__(self, detail: str):
        super().__init__("DEPENDENCY_UNAVAILABLE", detail, 503)
