from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from app.config import Settings
from app.errors import DependencyUnavailableError, LimitExceededError
from app.models import BoundingBox, PageResult, WordResult
from app.ocr import _deskew, _embedded_page, _pdf_geometry, extract, normalize_box, preprocess
from app.validation import ValidatedUpload


def test_normalize_box() -> None:
    box = normalize_box(10, 20, 30, 40, 100, 200)
    assert box.model_dump() == {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.2}


def test_image_extraction_shape() -> None:
    stream = BytesIO()
    Image.new("RGB", (100, 50), "white").save(stream, "PNG")
    upload = ValidatedUpload(stream.getvalue(), "x.png", "image/png", 1)
    tesseract_data = {
        "text": ["Hello", "world"],
        "conf": ["90", "80"],
        "left": [0, 50],
        "top": [0, 0],
        "width": [40, 45],
        "height": [20, 20],
    }
    config = Settings(api_key="a" * 24, redis_required=False)
    with (
        patch("app.ocr.preprocess", side_effect=lambda image, _settings: (image, 0.0)),
        patch("app.ocr.pytesseract.image_to_data", return_value=tesseract_data),
        patch("app.ocr.pytesseract.get_tesseract_version", return_value="5.0"),
    ):
        result = extract(upload, config)
    assert result.text == "Hello world"
    assert result.page_count == 1
    assert result.pages[0].words[0].confidence == 0.9
    assert result.ocr_page_count == 1


def test_fast_preprocessing_skips_deskew() -> None:
    config = Settings(api_key="a" * 24, redis_required=False, preprocessing_mode="fast")
    image = Image.new("RGB", (100, 50), "white")
    processed, angle = preprocess(image, config)
    assert processed.mode == "L"
    assert angle == 0


def test_deskew_blank_and_line_image() -> None:
    blank = Image.new("L", (100, 50), "white")
    unchanged, angle = _deskew(blank, 4)
    assert unchanged is blank and angle == 0
    lines = Image.new("L", (200, 100), "white")
    for y in range(20, 81, 20):
        for x in range(20, 180):
            lines.putpixel((x, y), 0)
    processed, detected = _deskew(lines, 2)
    assert processed.size[0] >= lines.size[0]
    assert -2 <= detected <= 2


def test_embedded_text_coordinates(tmp_path: Path) -> None:
    xml = (
        b'<?xml version="1.0"?><doc><page width="100" height="200">'
        b'<word xMin="10" yMin="20" xMax="40" yMax="60">Invoice</word></page></doc>'
    )
    with (
        patch("app.ocr.shutil.which", return_value="/usr/bin/pdftotext"),
        patch("app.ocr.subprocess.run") as run,
    ):
        run.return_value.stdout = xml
        words, width, height = _embedded_page(tmp_path, 1, 10)
    assert (width, height) == (100, 200)
    assert words[0].text == "Invoice"
    assert words[0].box.x == 0.1


def test_embedded_text_dependency_failure(tmp_path: Path) -> None:
    with patch("app.ocr.shutil.which", return_value=None):
        try:
            _embedded_page(tmp_path, 1, 10)
        except DependencyUnavailableError as error:
            assert error.code == "DEPENDENCY_UNAVAILABLE"
        else:
            raise AssertionError("missing pdftotext was accepted")


def test_pdf_geometry_limits() -> None:
    page = MagicMock()
    page.get.return_value = 0
    page.mediabox.width = 612
    page.mediabox.height = 792
    reader = MagicMock(pages=[page])
    upload = ValidatedUpload(b"%PDF", "x.pdf", "application/pdf", 1)
    with patch("app.ocr.PdfReader", return_value=reader):
        geometry = _pdf_geometry(upload, Settings(api_key="a" * 24, redis_required=False))
    assert geometry[0][2] == 0
    with patch("app.ocr.PdfReader", return_value=reader):
        try:
            _pdf_geometry(
                upload,
                Settings(
                    api_key="a" * 24,
                    redis_required=False,
                    max_image_pixels=10,
                    max_document_pixels=10,
                ),
            )
        except LimitExceededError:
            pass
        else:
            raise AssertionError("pixel limit was not enforced")


def test_pdf_embedded_text_bypasses_ocr() -> None:
    upload = ValidatedUpload(b"%PDF fake", "x.pdf", "application/pdf", 1)
    words = [
        WordResult(text=f"word{i}", confidence=1, page=1, box=BoundingBox(x=0, y=0, width=0.1, height=0.1))
        for i in range(5)
    ]
    config = Settings(api_key="a" * 24, redis_required=False)
    with (
        patch("app.ocr._pdf_geometry", return_value=[(100, 200, 90)]),
        patch("app.ocr._embedded_page", return_value=(words, 100, 200)),
        patch("app.ocr.pytesseract.get_tesseract_version", return_value="5"),
        patch("app.ocr.convert_from_path") as render,
    ):
        result = extract(upload, config, request_id="request")
    assert result.pages[0].source == "embeddedText"
    assert result.embedded_text_page_count == 1
    assert result.request_id == "request"
    render.assert_not_called()


def test_pdf_scanned_page_uses_ocr() -> None:
    upload = ValidatedUpload(b"%PDF fake", "x.pdf", "application/pdf", 1)
    image = Image.new("RGB", (100, 50), "white")
    page = PageResult(
        page=1,
        width=100,
        height=50,
        rotation=1,
        text="scan",
        confidence=0.2,
        word_count=1,
        words=[
            WordResult(
                text="scan",
                confidence=0.2,
                page=1,
                box=BoundingBox(x=0, y=0, width=0.1, height=0.1),
            )
        ],
        source="ocr",
        deskew_angle=1.0,
    )
    config = Settings(api_key="a" * 24, redis_required=False)
    with (
        patch("app.ocr._pdf_geometry", return_value=[(100, 50, 0)]),
        patch("app.ocr._embedded_page", return_value=([], 100, 50)),
        patch("app.ocr.convert_from_path", return_value=[image]),
        patch("app.ocr._ocr_image", return_value=page),
        patch("app.ocr.pytesseract.get_tesseract_version", return_value="5"),
    ):
        result = extract(upload, config)
    assert result.ocr_page_count == 1
    assert any("low OCR confidence" in warning for warning in result.warnings)
    assert any("deskewed" in warning for warning in result.warnings)
