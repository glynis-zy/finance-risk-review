# -*- coding: utf-8 -*-
"""文档解析流水线（架构文档 §3 / §11）。

流程：识别文档类型 → 文本型 PDF 直接抽文本（免 OCR）→ 否则走 OCR 适配层
→ 合同类再交 LLM 结构化提取（Pydantic 校验）→ 写 AttachmentParseResult / InvoiceRecord。
失败 → parse_status=failed/manual_review，允许重试。
"""
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.services import llm_client, ocr_client

logger = logging.getLogger(__name__)

# 文档类别识别：文件名提示词
CATEGORY_HINTS: dict[str, list[str]] = {
    "invoice": ["发票", "invoice"],
    "contract": ["合同", "contract"],
    "itinerary": ["行程", "itinerary", "机票", "火车票", "订票"],
    "payment_doc": ["付款", "payment", "回单", "水单", "银行", "转账"],
}


def recognize_document_type(file_name: str) -> str:
    lower = file_name.lower()
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in lower for h in hints):
            return cat
    return "invoice"  # 默认按发票处理


def _extract_text_pdf(path: Path) -> str | None:
    """文本型 PDF 直接抽全文；图片型/失败返回 None（走 OCR）。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(path))
        text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
        return text or None
    except Exception:  # noqa: BLE001
        return None


def _full_preset_for(file_name: str) -> dict | None:
    """全量预制解析结果（含 category+fields）。命中则整条解析跳过 OCR/LLM。"""
    base = Path(file_name).stem
    preset_dir = Path(settings.preset_parse_dir)
    for p in (preset_dir / f"{file_name}.json", preset_dir / f"{base}.json"):
        if p.exists():
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("category"):
                return data
    return None


async def parse_attachment(db: Session, attachment: DocumentAttachment) -> dict:
    """解析单个附件并落表。返回 {ok, category} 供上层判断。

    ocr.mode 系统参数：preset=仅用预制结果（演示确定性）；auto=AUTO→预制→失败。
    """
    from app.services import sysparam_service
    mode = sysparam_service.get(db, "ocr.mode", "auto")
    preset = _full_preset_for(attachment.file_name)
    if preset:
        _apply_preset(db, attachment, preset)
        return {"ok": True, "category": preset.get("category")}
    if mode == "preset":
        return _fail(db, attachment, "unknown", f"preset 模式未命中预制案例: {attachment.file_name}")

    path = Path(settings.file_storage_path) / attachment.file_path
    file_bytes = path.read_bytes() if path.exists() else b""
    category = recognize_document_type(attachment.file_name)
    is_pdf = attachment.file_type == "pdf"

    if category == "invoice":
        return await _parse_invoice(db, attachment, file_bytes, path, is_pdf)
    if category == "contract":
        return await _parse_contract(db, attachment, file_bytes, path, is_pdf)
    return await _parse_generic(db, attachment, file_bytes, path, is_pdf, category)


def _apply_preset(db: Session, attachment: DocumentAttachment, preset: dict) -> None:
    """用预制结果落表（发票另写 InvoiceRecord），置 succeeded。"""
    fields = preset.get("fields") or {}
    category = preset.get("category")
    _write_parse(db, attachment, category, preset.get("full_text"), fields,
                 preset.get("positions", []), preset.get("confidence"))
    if category == "invoice":
        db.add(_make_invoice(attachment.id, fields))
    _succeed(db, attachment)


async def _parse_invoice(db, attachment, file_bytes, path, is_pdf) -> dict:
    fields = None
    if is_pdf:
        text = _extract_text_pdf(path)
        if text:
            fields = _parse_invoice_from_text(text)
    if fields is None:
        try:
            data = await ocr_client.ocr_invoice(file_bytes, attachment.file_name)
            fields = _norm_invoice(data)
        except ocr_client.ParseFailure as exc:
            return _fail(db, attachment, "invoice", str(exc))

    _write_parse(db, attachment, "invoice", None, fields, [], fields.get("confidence"))
    db.add(_make_invoice(attachment.id, fields))
    _succeed(db, attachment)
    return {"ok": True, "category": "invoice"}


async def _parse_contract(db, attachment, file_bytes, path, is_pdf) -> dict:
    full_text = _extract_text_pdf(path) if is_pdf else None
    positions, confidence = [], (0.98 if full_text else None)
    if full_text is None:
        try:
            ocr = await ocr_client.ocr_generic(file_bytes, attachment.file_name)
            full_text = ocr.get("full_text", "")
            positions = ocr.get("positions", [])
            confidence = ocr.get("confidence")
        except ocr_client.ParseFailure as exc:
            return _fail(db, attachment, "contract", str(exc))
    if not full_text:
        return _fail(db, attachment, "contract", "未提取到合同文本")

    cf = llm_client.extract_contract_fields(full_text)
    if cf is None:
        return _fail(db, attachment, "contract", "合同字段提取失败", full_text=full_text)
    _write_parse(db, attachment, "contract", full_text, cf.model_dump(), positions, confidence)
    _succeed(db, attachment)
    return {"ok": True, "category": "contract"}


async def _parse_generic(db, attachment, file_bytes, path, is_pdf, category) -> dict:
    full_text = _extract_text_pdf(path) if is_pdf else None
    positions, confidence = [], (0.98 if full_text else None)
    if full_text is None:
        try:
            ocr = await ocr_client.ocr_generic(file_bytes, attachment.file_name)
            full_text = ocr.get("full_text", "")
            positions = ocr.get("positions", [])
            confidence = ocr.get("confidence")
        except ocr_client.ParseFailure as exc:
            return _fail(db, attachment, category, str(exc))
    if not full_text:
        return _fail(db, attachment, category, "未提取到文本")
    _write_parse(db, attachment, category, full_text, None, positions, confidence)
    _succeed(db, attachment)
    return {"ok": True, "category": category}


# ---------- 落表工具 ----------

def _write_parse(db, attachment, category, full_text, fields, positions, confidence):
    db.add(AttachmentParseResult(
        attachment_id=attachment.id,
        document_category=category,
        full_text=full_text,
        fields_json=fields,
        evidence_positions_json={"positions": positions} if positions else None,
        confidence=confidence,
    ))


def _succeed(db, attachment):
    attachment.parse_status = "succeeded"


def _fail(db, attachment, category, message: str, full_text: str | None = None) -> dict:
    db.add(AttachmentParseResult(
        attachment_id=attachment.id,
        document_category=category,
        full_text=full_text,
        fields_json={"error": message},
    ))
    attachment.parse_status = "manual_review"  # 人工复核入口，可重试
    logger.warning("parse failed: %s %s", attachment.file_name, message)
    return {"ok": False, "category": category, "error": message}


# ---------- 文本发票兜底解析（正则） ----------

def _make_invoice(attachment_id: int, fields: dict) -> InvoiceRecord:
    """构造 InvoiceRecord，统一规整日期/金额（OCR/预制结果多为字符串）。"""

    def _date(v):
        if not v:
            return None
        s = str(v).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s[:10], fmt).date()
            except ValueError:
                continue
        return None

    def _dec(v):
        if v is None:
            return None
        try:
            return Decimal(str(v).replace(",", ""))
        except InvalidOperation:
            return None

    return InvoiceRecord(
        attachment_id=attachment_id,
        invoice_code=fields.get("invoice_code"),
        invoice_no=fields.get("invoice_no"),
        seller_name=fields.get("seller_name"),
        buyer_name=fields.get("buyer_name"),
        invoice_date=_date(fields.get("invoice_date")),
        amount_excluding_tax=_dec(fields.get("amount_excluding_tax")),
        tax_amount=_dec(fields.get("tax_amount")),
        amount_including_tax=_dec(fields.get("amount_including_tax")),
    )


def _norm_invoice(data: dict) -> dict:
    def dec(v):
        if v is None:
            return None
        try:
            return round(float(str(v).replace(",", "").replace("￥", "").replace("¥", "")), 2)
        except ValueError:
            return None
    return {
        "invoice_code": data.get("invoice_code"),
        "invoice_no": data.get("invoice_no"),
        "seller_name": data.get("seller_name"),
        "buyer_name": data.get("buyer_name"),
        "invoice_date": data.get("invoice_date"),
        "amount_including_tax": dec(data.get("amount_including_tax")),
        "tax_amount": dec(data.get("tax_amount")),
        "amount_excluding_tax": dec(data.get("amount_excluding_tax")),
        "confidence": data.get("confidence", 0.99),
    }


def _parse_invoice_from_text(text: str) -> dict | None:
    """文本型发票 PDF 的正则兜底；抽不到关键字段返回 None。"""
    code = re.search(r"发票代码[：:\s]*(\d{10,12})", text)
    num = re.search(r"发票号码[：:\s]*(\d{8,10})", text)
    total = re.search(r"价税合计[（(]?:?[）)]?\s*[¥￥]?\s*([\d,]+\.\d{2})", text)
    if not (code and num and total):
        return None
    return {
        "invoice_code": code.group(1),
        "invoice_no": num.group(1),
        "seller_name": None,
        "buyer_name": None,
        "invoice_date": None,
        "amount_including_tax": float(total.group(1).replace(",", "")),
        "tax_amount": None,
        "amount_excluding_tax": None,
        "confidence": 0.90,
    }
