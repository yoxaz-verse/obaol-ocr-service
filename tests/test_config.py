import pytest
from pydantic import ValidationError

from app.config import Settings


def test_configuration_validation() -> None:
    base = {"api_key": "a" * 24, "redis_required": False}
    for override in (
        {"service_mode": "bad"},
        {"preprocessing_mode": "bad"},
        {"result_ttl_seconds": 10, "job_timeout_seconds": 20},
        {"sync_timeout_seconds": 20, "page_timeout_seconds": 30},
        {"max_image_pixels": 100, "max_document_pixels": 50},
        {"minimum_confidence": 2},
    ):
        with pytest.raises(ValidationError):
            Settings(**base, **override)


def test_api_key_rotation_deduplicates() -> None:
    settings = Settings(
        api_key="a" * 24,
        api_keys=f"{'a' * 24},{'b' * 24}",
        redis_required=False,
    )
    assert settings.accepted_api_keys == ("a" * 24, "b" * 24)
