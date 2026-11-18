from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class BoundingBox(ApiModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class WordResult(ApiModel):
    text: str
    confidence: float = Field(ge=0, le=1)
    page: int = Field(ge=1)
    box: BoundingBox


class PageResult(ApiModel):
    page: int
    width: int
    height: int
    rotation: int
    text: str
    confidence: float = Field(ge=0, le=1)
    word_count: int
    words: list[WordResult]
    source: Literal["embeddedText", "ocr"] = "ocr"
    original_rotation: int = 0
    deskew_angle: float = 0.0


class OcrResult(ApiModel):
    schema_version: Literal["1.0"] = "1.0"
    text: str
    page_count: int
    duration_ms: int
    language: str
    engine: str
    engine_version: str
    warnings: list[str]
    pages: list[PageResult]
    request_id: str | None = None
    embedded_text_page_count: int = 0
    ocr_page_count: int = 0


class JobAccepted(ApiModel):
    id: str
    status: Literal["queued"] = "queued"
    status_url: str
    expires_in_seconds: int
    request_id: str | None = None


class JobState(ApiModel):
    id: str
    status: Literal["queued", "processing", "completed", "failed", "canceled", "expired"]
    result: OcrResult | None = None
    error: str | None = None
    error_code: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    request_id: str | None = None


class ErrorResponse(ApiModel):
    detail: str
    code: str
    request_id: str
    validation: list[dict[str, object]] | None = None


class Capabilities(ApiModel):
    schema_version: Literal["1.0"] = "1.0"
    formats: list[str]
    languages: list[str]
    synchronous: bool
    asynchronous: bool
    limits: dict[str, int]
