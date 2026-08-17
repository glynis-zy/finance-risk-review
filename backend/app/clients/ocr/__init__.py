# -*- coding: utf-8 -*-
"""OCR 适配层统一出口：默认百度云实现。换厂商只改这里 / .env。"""
from app.clients.ocr.base import OcrClient, ParseFailure
from app.clients.ocr.baidu import ocr_generic, ocr_invoice

__all__ = ["OcrClient", "ParseFailure", "ocr_invoice", "ocr_generic"]
