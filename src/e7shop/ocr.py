"""Tesseract wrapper. Text comes back lower-cased; numbers keep only their digits."""

import re

from PIL import Image, ImageOps

_NON_DIGITS = re.compile(r"\D")


def parse_number(text: str) -> int | None:
    """Drop every non-digit (commas, periods, spaces, stray punctuation) and parse the rest.

    "1,234,567", "1.234.567" and "1 234,567" all give 1234567. Returns None when no digits remain.
    """
    digits = _NON_DIGITS.sub("", text)
    return int(digits) if digits else None


def preprocess(image: Image.Image, upscale: int = 2) -> Image.Image:
    gray = ImageOps.grayscale(image)
    if upscale > 1:
        gray = gray.resize((gray.width * upscale, gray.height * upscale), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(gray)


class TesseractOcr:
    def __init__(self, tesseract_path: str, *, text_psm: int = 11, number_whitelist: str = "0123456789,.", upscale: int = 2):
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        self._pt = pytesseract
        self._text_config = f"--psm {text_psm}"
        self._number_config = f"--psm 7 -c tessedit_char_whitelist={number_whitelist}"
        self._upscale = upscale

    def text(self, image: Image.Image) -> str:
        return self._pt.image_to_string(preprocess(image, self._upscale), config=self._text_config).lower()

    def number_raw(self, image: Image.Image) -> str:
        return self._pt.image_to_string(preprocess(image, self._upscale), config=self._number_config).strip()

    def number(self, image: Image.Image) -> int | None:
        return parse_number(self.number_raw(image))
