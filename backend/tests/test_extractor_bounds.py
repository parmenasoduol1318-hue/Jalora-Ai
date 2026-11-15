from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.services import extractors


class _Page:
    def __init__(self, text: str):
        self.text = text

    def extract_text(self):
        return self.text


def _pdf_reader(monkeypatch, pages):
    monkeypatch.setitem(
        sys.modules,
        "pypdf",
        SimpleNamespace(PdfReader=lambda _path: SimpleNamespace(pages=pages)),
    )


def test_pdf_page_limit_is_enforced_before_page_extraction(monkeypatch, tmp_path):
    pages = [_Page("") for _ in range(extractors.MAX_PDF_PAGES + 1)]
    _pdf_reader(monkeypatch, pages)

    with pytest.raises(
        RuntimeError,
        match=f"{extractors.MAX_PDF_PAGES}-page processing limit",
    ):
        extractors.extract_pdf(tmp_path / "large.pdf")


def test_pdf_ocr_page_budget_is_explicit(monkeypatch, tmp_path):
    _pdf_reader(monkeypatch, [_Page("") for _ in range(3)])
    monkeypatch.setattr(extractors, "MAX_OCR_PAGES", 1)
    calls = []

    def ocr_page(_path, index, embedded):
        calls.append(index)
        return f"OCR page {index + 1}"

    monkeypatch.setattr(extractors, "_ocr_page", ocr_page)
    text, page_count = extractors.extract_pdf(tmp_path / "scanned.pdf")

    assert page_count == 3
    assert calls == [0]
    assert "OCR page 1" in text
    assert text.count("OCR page limit reached") == 2


def test_pdf_ocr_uses_enumerated_page_index(monkeypatch, tmp_path):
    _pdf_reader(monkeypatch, [_Page(""), _Page("")])
    indices = []
    monkeypatch.setattr(
        extractors, "_ocr_page",
        lambda _path, index, _embedded: indices.append(index) or f"page {index}",
    )

    extractors.extract_pdf(tmp_path / "scanned.pdf")

    assert indices == [0, 1]
