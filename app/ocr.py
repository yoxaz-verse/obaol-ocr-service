import shutil
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import pytesseract
from defusedxml import ElementTree as ET
from pdf2image import convert_from_path
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pypdf import PdfReader
from pytesseract import Output

from app.config import Settings
from app.errors import DependencyUnavailableError, LimitExceededError, ProcessingTimeoutError
from app.models import BoundingBox, OcrResult, PageResult, WordResult
from app.validation import ValidatedUpload


def normalize_box(
    left: float, top: float, width: float, height: float, image_width: float, image_height: float
) -> BoundingBox:
    return BoundingBox(
        x=max(0.0, min(1.0, left / image_width)),
        y=max(0.0, min(1.0, top / image_height)),
        width=max(0.0, min(1.0, width / image_width)),
        height=max(0.0, min(1.0, height / image_height)),
    )


def _deskew(image: Image.Image, max_degrees: float) -> tuple[Image.Image, float]:
    thumbnail = image.copy()
    thumbnail.thumbnail((1600, 1600))
    array = np.asarray(thumbnail)
    if array.std() < 4:
        return image, 0.0
    best_angle, best_score = 0.0, -1.0
    for angle in np.arange(-max_degrees, max_degrees + 0.01, 0.5):
        rotated = thumbnail.rotate(float(angle), expand=False, fillcolor=255)
        projection = (np.asarray(rotated) < 190).sum(axis=1)
        score = float(np.sum(np.diff(projection) ** 2))
        if score > best_score:
            best_score, best_angle = score, float(angle)
    if abs(best_angle) < 0.25:
        return image, 0.0
    return image.rotate(best_angle, expand=True, fillcolor=255), best_angle


def preprocess(image: Image.Image, settings: Settings) -> tuple[Image.Image, float]:
    gray = ImageOps.grayscale(image)
    if settings.preprocessing_mode == "fast":
        return gray, 0.0
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = ImageEnhance.Contrast(gray).enhance(1.15)
    if settings.preprocessing_mode == "accuracy":
        gray = gray.filter(ImageFilter.MedianFilter(size=3))
    return _deskew(gray, settings.deskew_max_degrees)


def _pdf_geometry(upload: ValidatedUpload, settings: Settings) -> list[tuple[int, int, int]]:
    reader = PdfReader(BytesIO(upload.data), strict=False)
    geometry: list[tuple[int, int, int]] = []
    total_pixels = 0
    for page in reader.pages:
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        width = float(page.mediabox.width) * settings.pdf_dpi / 72
        height = float(page.mediabox.height) * settings.pdf_dpi / 72
        if rotation in {90, 270}:
            width, height = height, width
        pixels = int(width * height)
        if pixels > settings.max_image_pixels:
            raise LimitExceededError("A rendered PDF page exceeds the configured pixel limit")
        total_pixels += pixels
        geometry.append((max(1, int(width)), max(1, int(height)), rotation))
    if total_pixels > settings.max_document_pixels:
        raise LimitExceededError("Rendered PDF exceeds the configured document pixel limit")
    return geometry


def _embedded_page(source: Path, page_number: int, timeout: int) -> tuple[list[WordResult], int, int]:
    try:
        binary = shutil.which("pdftotext")
        if binary is None:
            raise DependencyUnavailableError("Poppler pdftotext is unavailable")
        process = subprocess.run(  # noqa: S603 - absolute executable resolved from controlled PATH
            [binary, "-f", str(page_number), "-l", str(page_number), "-bbox", str(source), "-"],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessingTimeoutError("Embedded PDF text extraction timed out") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DependencyUnavailableError("Poppler text extraction failed") from exc
    try:
        root = ET.fromstring(process.stdout)
    except ET.ParseError:
        return [], 0, 0
    page_node = next((node for node in root.iter() if node.tag.endswith("page")), None)
    if page_node is None:
        return [], 0, 0
    width = max(1, int(float(page_node.attrib.get("width", "1"))))
    height = max(1, int(float(page_node.attrib.get("height", "1"))))
    words: list[WordResult] = []
    for node in page_node.iter():
        if not node.tag.endswith("word") or not (node.text or "").strip():
            continue
        x1, y1 = float(node.attrib["xMin"]), float(node.attrib["yMin"])
        x2, y2 = float(node.attrib["xMax"]), float(node.attrib["yMax"])
        words.append(
            WordResult(
                text=(node.text or "").strip(),
                confidence=1.0,
                page=page_number,
                box=normalize_box(x1, y1, x2 - x1, y2 - y1, width, height),
            )
        )
    return words, width, height


def _ocr_image(
    original: Image.Image, page_number: int, original_rotation: int, settings: Settings, timeout: int
) -> PageResult:
    image, deskew_angle = preprocess(original, settings)
    try:
        data = pytesseract.image_to_data(
            image,
            lang=settings.ocr_language,
            output_type=Output.DICT,
            config=f"--oem 1 --psm {settings.ocr_page_segmentation_mode}",
            timeout=timeout,
        )
    except RuntimeError as exc:
        raise ProcessingTimeoutError() from exc
    words: list[WordResult] = []
    for index, raw_text in enumerate(data["text"]):
        word = str(raw_text).strip()
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1
        if word and confidence >= 0:
            words.append(
                WordResult(
                    text=word,
                    confidence=max(0.0, min(1.0, confidence / 100)),
                    page=page_number,
                    box=normalize_box(
                        float(data["left"][index]),
                        float(data["top"][index]),
                        float(data["width"][index]),
                        float(data["height"][index]),
                        image.width,
                        image.height,
                    ),
                )
            )
    confidence = sum(word.confidence for word in words) / len(words) if words else 0.0
    result = PageResult(
        page=page_number,
        width=image.width,
        height=image.height,
        rotation=int(round(deskew_angle)),
        text=" ".join(word.text for word in words),
        confidence=confidence,
        word_count=len(words),
        words=words,
        source="ocr",
        original_rotation=original_rotation,
        deskew_angle=deskew_angle,
    )
    if image is not original:
        image.close()
    return result


def extract(
    upload: ValidatedUpload,
    settings: Settings,
    timeout_seconds: int | None = None,
    request_id: str | None = None,
) -> OcrResult:
    started = time.monotonic()
    deadline = started + (timeout_seconds or settings.sync_timeout_seconds)
    pages: list[PageResult] = []
    warnings: list[str] = []
    Path(settings.temp_dir).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="job-", dir=settings.temp_dir) as workdir:
        if upload.media_type.startswith("image/"):
            with Image.open(BytesIO(upload.data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source) or source
                image = oriented.convert("RGB")
            pages.append(_ocr_image(image, 1, 0, settings, max(1, int(deadline - time.monotonic()))))
            image.close()
        else:
            source_path = Path(workdir) / "source.pdf"
            source_path.write_bytes(upload.data)
            geometry = _pdf_geometry(upload, settings)
            for page_number, (_width, _height, rotation) in enumerate(geometry, start=1):
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    raise ProcessingTimeoutError()
                embedded, width, height = _embedded_page(
                    source_path, page_number, min(settings.page_timeout_seconds, remaining)
                )
                chars = sum(len(word.text) for word in embedded)
                if (
                    len(embedded) >= settings.embedded_text_min_words
                    and chars >= settings.embedded_text_min_chars
                ):
                    pages.append(
                        PageResult(
                            page=page_number,
                            width=width,
                            height=height,
                            rotation=0,
                            text=" ".join(word.text for word in embedded),
                            confidence=1.0,
                            word_count=len(embedded),
                            words=embedded,
                            source="embeddedText",
                            original_rotation=rotation,
                            deskew_angle=0,
                        )
                    )
                    continue
                images = convert_from_path(
                    source_path,
                    dpi=settings.pdf_dpi,
                    fmt="ppm",
                    output_folder=workdir,
                    first_page=page_number,
                    last_page=page_number,
                    thread_count=1,
                    timeout=min(settings.page_timeout_seconds, remaining),
                )
                if not images:
                    warnings.append(f"Page {page_number} could not be rendered")
                    continue
                original = images[0]
                pages.append(
                    _ocr_image(
                        original,
                        page_number,
                        rotation,
                        settings,
                        min(settings.page_timeout_seconds, max(1, int(deadline - time.monotonic()))),
                    )
                )
                original.close()
    embedded_count = sum(page.source == "embeddedText" for page in pages)
    ocr_count = len(pages) - embedded_count
    if embedded_count and ocr_count:
        warnings.append("Document contains a mix of embedded text and OCR pages")
    for page in pages:
        if not page.words:
            warnings.append(f"Page {page.page} contains no recognized text")
        elif page.confidence < settings.minimum_confidence:
            warnings.append(f"Page {page.page} has low OCR confidence")
        if abs(page.deskew_angle) >= 0.25:
            warnings.append(f"Page {page.page} was deskewed by {page.deskew_angle:.1f} degrees")
    return OcrResult(
        text="\n\n".join(page.text for page in pages),
        page_count=len(pages),
        duration_ms=int((time.monotonic() - started) * 1000),
        language=settings.ocr_language,
        engine="tesseract+poppler",
        engine_version=str(pytesseract.get_tesseract_version()),
        warnings=warnings,
        pages=pages,
        request_id=request_id,
        embedded_text_page_count=embedded_count,
        ocr_page_count=ocr_count,
    )
