# -*- coding: utf-8 -*-
"""百度云 OCR 实现（纯真实 API，模块级函数，接口见 base.OcrClient）。

- 无可靠置信度时 confidence 如实返回 None（不伪造数值）；
- access token 失效（401 / error_code 110/111）自动清空重取一次。
"""
import base64
import logging

import httpx

from app.clients.ocr.base import ParseFailure
from app.core.config import settings

logger = logging.getLogger(__name__)

_access_token: str | None = None


async def _get_access_token() -> str:
    global _access_token
    if _access_token:
        return _access_token
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{settings.ocr_base_url}/oauth/2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.ocr_api_key,
                "client_secret": settings.ocr_secret_key,
            },
        )
        r.raise_for_status()
        _access_token = r.json()["access_token"]
    return _access_token


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def _post_ocr(url: str, data: dict) -> dict:
    """带 token 刷新重试的 OCR POST（token 失效后自动重取一次）。"""
    global _access_token
    for attempt in range(2):
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, data={**data, "access_token": token})
            try:
                body = r.json()
            except ValueError:
                raise ParseFailure(f"OCR 响应异常: HTTP {r.status_code}")
        if r.status_code in (401, 403) or (
            isinstance(body, dict) and body.get("error_code") in (110, 111)
        ):
            _access_token = None  # token 失效，清掉重取
            if attempt == 0:
                continue
            raise ParseFailure("OCR 鉴权失败")
        return body
    raise ParseFailure("OCR 鉴权失败")


async def ocr_invoice(file_bytes: bytes) -> dict:
    """增值税发票识别 → 规整字段 dict（纯真实 API，失败抛 ParseFailure）。"""
    if not settings.ocr_api_key:
        raise ParseFailure("OCR API 未配置（real/auto 模式需要 key）")
    body = await _post_ocr(
        f"{settings.ocr_base_url}/rest/2.0/ocr/v1/vat_invoice",
        {"image": _b64(file_bytes)},
    )
    words = body.get("words_result", {})

    def dec(v):
        if v is None:
            return None
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            return None

    amount_inc = dec(words.get("AmountInFiguers") or words.get("AmountInWords"))
    tax_amt = dec(words.get("Tax"))
    return {
        "invoice_code": words.get("InvoiceCode"),
        "invoice_no": words.get("InvoiceNum"),
        "seller_name": words.get("SellerName"),
        "buyer_name": words.get("PurchaserName"),
        "invoice_date": words.get("InvoiceDate"),
        "amount_including_tax": amount_inc,
        "tax_amount": tax_amt,
        "amount_excluding_tax": (amount_inc - tax_amt)
            if amount_inc is not None and tax_amt is not None else None,
        "confidence": None,  # 无可靠置信度，如实为空
    }


async def ocr_generic(file_bytes: bytes) -> dict:
    """通用文字识别（含文字位置）→ {full_text, positions, confidence}（纯真实 API）。"""
    if not settings.ocr_api_key:
        raise ParseFailure("OCR API 未配置（real/auto 模式需要 key）")
    body = await _post_ocr(
        f"{settings.ocr_base_url}/rest/2.0/ocr/v1/general",
        {"image": _b64(file_bytes)},
    )
    words = body.get("words_result", [])
    lines = [w.get("words", "") for w in words]
    positions = [{"text": w.get("words", ""), "location": w.get("location", {})} for w in words]
    return {
        "full_text": "\n".join(lines),
        "positions": positions,
        "confidence": None,
    }
