from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from app.config import Settings


@dataclass(frozen=True)
class ValidatedUpload:
    data: bytes
    filename: str
    media_type: str
    page_count: int


EXTENSIONS = {
    "application/pdf": {".pdf"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
}


def detect_media_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def validate_upload(
    data: bytes, filename: str, claimed_type: str | None, settings: Settings
) -> ValidatedUpload:
    if not data:
        raise HTTPException(400, "The uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "File exceeds the configured upload limit")
    detected = detect_media_type(data)
    if detected is None:
        raise HTTPException(415, "Only PDF, PNG, and JPEG files are supported")
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSIONS[detected]:
        raise HTTPException(415, "File extension does not match file content")
    normalized_claim = (claimed_type or "").split(";", maxsplit=1)[0].strip().lower()
    if normalized_claim and normalized_claim not in {detected, "application/octet-stream"}:
        raise HTTPException(415, "Content-Type does not match file content")

    if detected == "application/pdf":
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise HTTPException(422, "Encrypted PDFs are not supported")
            pages = len(reader.pages)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, "Malformed PDF") from exc
        if pages == 0:
            raise HTTPException(422, "PDF contains no pages")
        if pages > settings.max_pdf_pages:
            raise HTTPException(413, "PDF exceeds the configured page limit")
    else:
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                pixels = image.width * image.height
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, "Malformed or unsafe image") from exc
        if pixels > settings.max_image_pixels:
            raise HTTPException(413, "Image exceeds the configured pixel limit")
        pages = 1
    return ValidatedUpload(data=data, filename=Path(filename).name, media_type=detected, page_count=pages)
