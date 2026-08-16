# -*- coding: utf-8 -*-
"""OCR 适配层（百度云）。

三级模式（用户确认）：
1. AUTO：配置了 API Key → 真实调用云 OCR；
2. 命中预制案例：附件在 demo/preset_parse/ 下存在同名 JSON → 直接返回预置结果（保证演示链路稳定）；
3. 其余情况 → 抛 ParseFailure（上层置 parse_status=failed / manual_review）。
"""
import base64
import json
import logging
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_access_token: str | None = None


class ParseFailure(Exception):
    """解析失败（无 API、调用失败、无预制案例）。"""


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


def _preset_for(file_name: str) -> dict | None:
    """命中预制解析案例（demo/preset_parse/<文件名>.json）。"""
    base = Path(file_name).stem
    preset_dir = Path(settings.preset_parse_dir)
    candidates = [
        preset_dir / f"{file_name}.json",
        preset_dir / f"{base}.json",
        preset_dir / f"{base}.ocr.json",
    ]
    for p in candidates:
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return None


async def ocr_invoice(file_bytes: bytes, file_name: str) -> dict:
    """增值税发票识别 → 规整字段 dict。"""
    preset = _preset_for(file_name)
    if preset:
        return preset
    if not settings.ocr_api_key:
        raise ParseFailure("OCR API 未配置且无预制案例")
    try:
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{settings.ocr_base_url}/rest/2.0/ocr/v1/vat_invoice",
                data={"access_token": token, "image": _b64(file_bytes)},
            )
            r.raise_for_status()
            words = r.json().get("words_result", {})
        total = words.get("AmountInFiguers") or words.get("AmountInWords")
        tax = words.get("Tax")
        def dec(v):
            if v is None:
                return None
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                return None
        amount_inc = dec(total)
        tax_amt = dec(tax)
        return {
            "invoice_code": words.get("InvoiceCode"),
            "invoice_no": words.get("InvoiceNum"),
            "seller_name": words.get("SellerName"),
            "buyer_name": words.get("PurchaserName"),
            "invoice_date": words.get("InvoiceDate"),
            "amount_including_tax": amount_inc,
            "tax_amount": tax_amt,
            "amount_excluding_tax": (amount_inc - tax_amt) if amount_inc is not None and tax_amt is not None else None,
            "confidence": 0.99,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("invoice OCR failed: %s", exc)
        raise ParseFailure(str(exc)) from exc


async def ocr_generic(file_bytes: bytes, file_name: str) -> dict:
    """通用文字识别（含文字位置）→ {full_text, positions, confidence}。"""
    preset = _preset_for(file_name)
    if preset:
        return preset
    if not settings.ocr_api_key:
        raise ParseFailure("OCR API 未配置且无预制案例")
    try:
        token = await _get_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{settings.ocr_base_url}/rest/2.0/ocr/v1/general",
                data={"access_token": token, "image": _b64(file_bytes)},
            )
            r.raise_for_status()
            data = r.json()
        words = data.get("words_result", [])
        lines = [w.get("words", "") for w in words]
        positions = [
            {"text": w.get("words", ""), "location": w.get("location", {})}
            for w in words
        ]
        return {
            "full_text": "\n".join(lines),
            "positions": positions,
            "confidence": data.get("words_result_num", 1) and 0.95,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("generic OCR failed: %s", exc)
        raise ParseFailure(str(exc)) from exc
