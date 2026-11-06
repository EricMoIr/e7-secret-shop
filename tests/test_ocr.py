import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from e7shop.ocr import parse_number, preprocess

SAMPLES = Path(__file__).parent / "samples"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1,234,567", 1_234_567),
        ("1.234.567", 1_234_567),
        ("1 234,567", 1_234_567),
        (" 184,000\n", 184_000),
        ("30", 30),
        ("'2,0.00,000'", 2_000_000),
        ("", None),
        (",.", None),
    ],
)
def test_parse_number_keeps_only_digits(text, expected):
    assert parse_number(text) == expected


def test_preprocess_upscales_and_grays():
    image = Image.new("RGB", (40, 10), (200, 30, 30))
    out = preprocess(image, upscale=2)
    assert out.size == (80, 20)
    assert out.mode == "L"


def _sample_cases():
    cases_file = SAMPLES / "cases.json"
    if not cases_file.exists():
        return []
    return json.loads(cases_file.read_text(encoding="utf-8"))


@pytest.mark.skipif(not shutil.which("tesseract") or not _sample_cases(), reason="needs tesseract and tests/samples/cases.json")
@pytest.mark.parametrize("case", _sample_cases(), ids=lambda c: c["image"])
def test_samples(case):
    """Real screenshots from the game. See tests/samples/README.md."""
    from e7shop.ocr import TesseractOcr

    ocr = TesseractOcr(shutil.which("tesseract"))
    image = Image.open(SAMPLES / case["image"])
    if region := case.get("region"):
        image = image.crop(tuple(region))
    if case["kind"] == "number":
        assert ocr.number(image) == case["expect"]
    else:
        assert case["expect"].lower() in ocr.text(image)


def test_find_tesseract_prefers_configured_then_bundled_then_standard(tmp_path, monkeypatch):
    import e7shop.ocr as ocr

    configured, bundled, standard = (tmp_path / name / "tesseract.exe" for name in ("configured", "bundled", "standard"))
    monkeypatch.setattr(ocr, "bundled_tesseract", lambda: bundled)
    monkeypatch.setattr(ocr, "STANDARD_INSTALL", standard)
    assert ocr.find_tesseract(str(configured)) is None
    for exe in (standard, bundled, configured):
        exe.parent.mkdir()
        exe.write_text("")
    assert ocr.find_tesseract(str(configured)) == configured
    assert ocr.find_tesseract(None) == bundled
    # A configured path that no longer exists falls back to the bundled one.
    assert ocr.find_tesseract(str(tmp_path / "gone.exe")) == bundled
    bundled.unlink()
    assert ocr.find_tesseract(None) == standard


def test_bundled_tesseract_is_found_in_the_packaged_app(tmp_path, monkeypatch):
    import sys

    from e7shop.ocr import bundled_tesseract

    exe = tmp_path / "tesseract" / "tesseract.exe"
    exe.parent.mkdir()
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert bundled_tesseract() == exe
