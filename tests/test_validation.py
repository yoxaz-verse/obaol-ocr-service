from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from pypdf import PdfWriter

from app.config import Settings
from app.validation import detect_media_type, validate_upload


def settings(**changes: object) -> Settings:
    return Settings(api_key="a" * 24, redis_required=False, **changes)


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (10, 10), "white").save(stream, "PNG")
    return stream.getvalue()


def test_detect_and_validate_png() -> None:
    data = png_bytes()
    assert detect_media_type(data) == "image/png"
    upload = validate_upload(data, "scan.png", "image/png", settings())
    assert upload.page_count == 1


@pytest.mark.parametrize("filename,claimed", [("scan.jpg", "image/png"), ("scan.png", "image/jpeg")])
def test_rejects_mismatch(filename: str, claimed: str) -> None:
    with pytest.raises(HTTPException) as error:
        validate_upload(png_bytes(), filename, claimed, settings())
    assert error.value.status_code == 415


def test_rejects_size_and_pixels() -> None:
    with pytest.raises(HTTPException) as size_error:
        validate_upload(png_bytes(), "x.png", "image/png", settings(max_upload_bytes=5))
    assert size_error.value.status_code == 413
    with pytest.raises(HTTPException) as pixel_error:
        validate_upload(png_bytes(), "x.png", "image/png", settings(max_image_pixels=20))
    assert pixel_error.value.status_code == 413


def test_rejects_malformed_pdf() -> None:
    with pytest.raises(HTTPException) as error:
        validate_upload(b"%PDF-not-real", "x.pdf", "application/pdf", settings())
    assert error.value.status_code == 422


def test_valid_and_encrypted_pdf() -> None:
    plain = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(plain)
    assert validate_upload(plain.getvalue(), "x.pdf", "application/pdf", settings()).page_count == 1

    encrypted = BytesIO()
    writer.encrypt("secret")
    writer.write(encrypted)
    with pytest.raises(HTTPException) as error:
        validate_upload(encrypted.getvalue(), "x.pdf", "application/pdf", settings())
    assert error.value.status_code == 422


def test_empty_and_unsupported_files() -> None:
    with pytest.raises(HTTPException) as empty:
        validate_upload(b"", "x.png", "image/png", settings())
    assert empty.value.status_code == 400
    with pytest.raises(HTTPException) as unsupported:
        validate_upload(b"GIF89a", "x.gif", "image/gif", settings())
    assert unsupported.value.status_code == 415
