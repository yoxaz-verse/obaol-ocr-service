from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_mode: str = "api"
    port: int = 8000
    api_key: SecretStr = Field(min_length=24)
    api_keys: str = ""
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    redis_required: bool = True
    ocr_language: str = "eng"
    max_upload_bytes: int = 15 * 1024 * 1024
    max_pdf_pages: int = 20
    max_image_pixels: int = 40_000_000
    max_document_pixels: int = 200_000_000
    pdf_dpi: int = 180
    sync_timeout_seconds: int = 120
    job_timeout_seconds: int = 600
    result_ttl_seconds: int = 86_400
    max_queued_jobs: int = 100
    max_queued_bytes: int = 512 * 1024 * 1024
    rate_limit_requests: int = 30
    rate_limit_window_seconds: int = 60
    web_concurrency: int = 2
    temp_dir: str = "/tmp/obaol-ocr"
    allowed_origins: str = ""
    embedded_text_min_words: int = 5
    embedded_text_min_chars: int = 20
    minimum_confidence: float = 0.45
    deskew_max_degrees: float = 4.0
    ocr_page_segmentation_mode: int = 3
    preprocessing_mode: str = "balanced"
    page_timeout_seconds: int = 45
    trust_proxy_headers: bool = False
    expiration_marker_ttl_seconds: int = 3600
    metrics_enabled: bool = True

    @field_validator("service_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {"api", "worker"}:
            raise ValueError("SERVICE_MODE must be api or worker")
        return value

    @field_validator("preprocessing_mode")
    @classmethod
    def validate_preprocessing_mode(cls, value: str) -> str:
        if value not in {"fast", "balanced", "accuracy"}:
            raise ValueError("PREPROCESSING_MODE must be fast, balanced, or accuracy")
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> "Settings":
        if self.result_ttl_seconds <= self.job_timeout_seconds:
            raise ValueError("RESULT_TTL_SECONDS must exceed JOB_TIMEOUT_SECONDS")
        if self.sync_timeout_seconds < self.page_timeout_seconds:
            raise ValueError("SYNC_TIMEOUT_SECONDS must be at least PAGE_TIMEOUT_SECONDS")
        if self.max_document_pixels < self.max_image_pixels:
            raise ValueError("MAX_DOCUMENT_PIXELS must be at least MAX_IMAGE_PIXELS")
        if not 0 <= self.minimum_confidence <= 1:
            raise ValueError("MINIMUM_CONFIDENCE must be between 0 and 1")
        return self

    @property
    def accepted_api_keys(self) -> tuple[str, ...]:
        values = [self.api_key.get_secret_value()]
        values.extend(key.strip() for key in self.api_keys.split(",") if key.strip())
        return tuple(dict.fromkeys(values))


@lru_cache
def get_settings() -> Settings:
    return Settings()
