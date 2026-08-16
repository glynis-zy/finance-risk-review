# -*- coding: utf-8 -*-
"""PDF 解析路径测试：文本型 PDF 直取；扫描型（图片 PDF）渲染转图 OCR。

功能缺口修复验证：原始实现只做 pypdf 文本提取，扫描 PDF 拿不到内容；
现在补了 PyMuPDF 页面渲染 → 逐页 OCR（含页码原文定位）。
"""
from pathlib import Path

import pymupdf  # PyMuPDF

from app.services.parse_pipeline import (
    _content_sources,
    _extract_text_pdf,
    _pdf_to_images,
)


def _make_text_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    # 用 ASCII 保证默认字体可靠写入文字层（测试的是"有文字层→直取"机制）
    page.insert_text((72, 72), "INVOICE NO 88886666 TOTAL 5000.00 DATE 2026-08-10")
    doc.save(str(path))
    doc.close()


def _make_scanned_pdf(path: Path) -> None:
    """图片型 PDF：只有一张位图，没有任何文字层。"""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 80))
    pix.clear_with(220)
    page.insert_image(pymupdf.Rect(0, 0, 200, 80), pixmap=pix)
    doc.save(str(path))
    doc.close()


def test_text_pdf_extracts_text(tmp_path):
    p = tmp_path / "text.pdf"
    _make_text_pdf(p)
    text = _extract_text_pdf(p)
    assert text and "INVOICE" in text


def test_text_pdf_uses_text_directly(tmp_path):
    p = tmp_path / "text.pdf"
    _make_text_pdf(p)
    text, images = _content_sources(p, True, b"")
    assert text and "INVOICE" in text
    assert images == []  # 有有效文本 → 不渲染


def test_scanned_pdf_no_text_layer(tmp_path):
    p = tmp_path / "scan.pdf"
    _make_scanned_pdf(p)
    assert _extract_text_pdf(p) is None  # 图片 PDF 无文字层


def test_scanned_pdf_renders_to_images(tmp_path):
    p = tmp_path / "scan.pdf"
    _make_scanned_pdf(p)
    text, images = _content_sources(p, True, b"")
    assert text is None                    # 无文本 → 走渲染 OCR
    assert len(images) >= 1                # 渲染出至少一页
    assert images[0][:8] == b"\x89PNG\r\n\x1a\n"  # 合法 PNG
    rendered = _pdf_to_images(p)
    assert len(rendered) == len(images)
