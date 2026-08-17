# -*- coding: utf-8 -*-
"""OCR 客户端抽象：上层只依赖统一接口与 DTO，厂商实现放 baidu.py。

未来可无侵入增加 paddle.py / 本地部署（本轮禁止实际接入，只做接口抽象）。
"""
from abc import ABC, abstractmethod


class ParseFailure(Exception):
    """OCR 解析失败（无 API 配置、调用失败）。"""


class OcrClient(ABC):
    """统一 OCR 接口：发票专用识别 + 通用文字识别。"""

    @abstractmethod
    async def invoice(self, image_bytes: bytes) -> dict:
        """增值税发票识别 → 规整字段 dict。"""

    @abstractmethod
    async def generic(self, image_bytes: bytes) -> dict:
        """通用文字识别 → {full_text, positions, confidence}。"""
