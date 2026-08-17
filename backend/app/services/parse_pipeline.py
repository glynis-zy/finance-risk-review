# -*- coding: utf-8 -*-
"""文档解析流水线（架构文档 §3 / §11）。

流程：识别文档类型 → 文本型 PDF 直接抽文本（免 OCR）→ 否则走 OCR 适配层
→ 合同类再交 LLM 结构化提取（Pydantic 校验）→ 写 AttachmentParseResult / InvoiceRecord。
只更新 document_attachments.parse_status（五态），不创建 analysis_tasks；失败可重试。
"""
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.clients import llm as llm_client
from app.clients import ocr as ocr_client

logger = logging.getLogger(__name__)

# 文档类别识别：文件名提示词（P1：标准枚举 invoice/contract/itinerary/payment_basis/other）
CATEGORY_HINTS: dict[str, list[str]] = {
    "invoice": ["发票", "invoice"],
    "contract": ["合同", "contract"],
    "itinerary": ["行程", "itinerary", "机票", "火车票", "订票"],
    "payment_basis": ["付款", "payment", "回单", "水单", "银行", "转账"],
}


def recognize_document_type(file_name: str) -> str:
    lower = file_name.lower()
    for cat, hints in CATEGORY_HINTS.items():
        if any(h in lower for h in hints):
            return cat
    return "other"  # 未命中 → other（不再是 invoice）


def _extract_text_pdf(path: Path) -> str | None:
    """文本型 PDF 直接抽全文；图片型/失败返回 None（走渲染 OCR）。"""
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


# 文本型 PDF 至少要有这么多有效字符；否则视为"扫描件"（图片 PDF）走渲染 OCR
_TEXT_MIN_LEN = 20


def _pdf_to_images(path: Path, dpi: int = 150) -> list[bytes]:
    """扫描型 PDF（图片 PDF）：用 PyMuPDF 渲染每页为 PNG bytes，供逐页 OCR。"""
    try:
        import pymupdf  # PyMuPDF>=1.24 推荐入口
    except ImportError:
        try:
            import fitz as pymupdf  # 旧版入口
        except ImportError:
            logger.warning("PyMuPDF 未安装，扫描 PDF 无法转图 OCR")
            return []
    try:
        doc = pymupdf.open(str(path))
        images = [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]
        doc.close()
        return images
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 渲染失败: %s", exc)
        return []


def _content_sources(path: Path, is_pdf: bool, file_bytes: bytes) -> tuple[str | None, list[bytes]]:
    """返回 (有效文本, 页图列表)。

    PDF：有有效文本→直接用文本；无文本/文本太少→渲染每页转图片走 OCR。
    PNG/JPG：直接作为单页图片走 OCR。
    """
    if is_pdf:
        text = _extract_text_pdf(path)
        if text and len(text.strip()) >= _TEXT_MIN_LEN:
            return text, []
        return None, _pdf_to_images(path)
    return None, [file_bytes]


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
    """按 ocr.mode 三模式解析（用户定义，见 DESIGN.md §3）：

    - real  : 真实 OCR/LLM，失败即失败（不查预制）；
    - auto  : 真实 OCR/LLM，失败后回退预制，再失败；
    - preset: 直接用预制结果，不调用任何外部 API。

    命中预制即直接落表（跳过 OCR 与 LLM）。返回 {ok, category} 供上层判断。

    附件解析不创建 analysis_tasks（那是整单风险分析的任务载体）：
    本函数只更新 document_attachments.parse_status（五态：
    pending→parsing→succeeded / failed / manual_review），失败可再次调用重试。
    """
    from app.services import sysparam_service
    attachment.parse_status = "parsing"
    db.flush()
    # 幂等（P1-4）：重新解析前清掉该附件旧记录，避免金额翻倍 / 历史 parse 干扰
    db.execute(delete(InvoiceRecord).where(InvoiceRecord.attachment_id == attachment.id))
    db.execute(delete(AttachmentParseResult).where(AttachmentParseResult.attachment_id == attachment.id))
    db.flush()
    mode = sysparam_service.get(db, "ocr.mode", "auto")
    preset = _full_preset_for(attachment.file_name)

    if mode == "preset":
        if preset:
            _apply_preset(db, attachment, preset)
            return {"ok": True, "category": preset.get("category")}
        return _fail(db, attachment, recognize_document_type(attachment.file_name),
                     f"preset 模式未命中预制案例: {attachment.file_name}")

    try:
        return await _parse_real(db, attachment)
    except ocr_client.ParseFailure as exc:
        if mode == "auto" and preset:
            _apply_preset(db, attachment, preset)
            return {"ok": True, "category": preset.get("category")}
        return _fail(db, attachment, recognize_document_type(attachment.file_name), str(exc))


async def _parse_real(db, attachment) -> dict:
    """真实解析路径：按文档类别分发（OCR + 合同 LLM 提取），失败抛 ParseFailure。"""
    category = recognize_document_type(attachment.file_name)
    path = Path(settings.file_storage_path) / attachment.file_path
    file_bytes = path.read_bytes() if path.exists() else b""
    is_pdf = attachment.file_type == "pdf"
    if category == "invoice":
        return await _parse_real_invoice(db, attachment, file_bytes, path, is_pdf)
    if category == "contract":
        return await _parse_real_contract(db, attachment, file_bytes, path, is_pdf)
    return await _parse_real_generic(db, attachment, file_bytes, path, is_pdf, category)


def _apply_preset(db: Session, attachment: DocumentAttachment, preset: dict) -> None:
    """用预制结果落表（发票另写 InvoiceRecord），置 succeeded。"""
    fields = preset.get("fields") or {}
    category = preset.get("category")
    _write_parse(db, attachment, category, preset.get("full_text"), fields,
                 preset.get("positions", []), preset.get("confidence"))
    if category == "invoice":
        db.add(_make_invoice(attachment.id, fields))
    _succeed(db, attachment)


async def _parse_real_invoice(db, attachment, file_bytes, path, is_pdf) -> dict:
    text, images = _content_sources(path, is_pdf, file_bytes)
    fields = None
    if text:
        fields = _parse_invoice_from_text(text)
    if fields is None:
        # 文本 PDF 正则失败 → 仍渲染页面走专用发票 OCR（P1-5）
        if not images and is_pdf:
            images = _pdf_to_images(path)
        # 扫描 PDF / 图片 / 渲染页 → 逐页 OCR 发票，取第一个识别出关键字段的结果
        for page_bytes in images:
            data = await ocr_client.ocr_invoice(page_bytes)
            cand = _norm_invoice(data)
            if cand.get("invoice_no") or cand.get("amount_including_tax") is not None:
                fields = cand
                break
        if fields is None:
            raise ocr_client.ParseFailure("发票 OCR 未识别出有效字段")

    _write_parse(db, attachment, "invoice", None, fields, [], fields.get("confidence"))
    db.add(_make_invoice(attachment.id, fields))
    _succeed(db, attachment)
    return {"ok": True, "category": "invoice"}


async def _parse_real_contract(db, attachment, file_bytes, path, is_pdf) -> dict:
    text, images = _content_sources(path, is_pdf, file_bytes)
    full_text = text or ""
    positions: list[dict] = []
    confidences: list[float] = []
    if not text:
        # 扫描 PDF / 图片：逐页 OCR 并聚合全文，位置带页码（原文定位）
        for idx, page_bytes in enumerate(images, 1):
            ocr = await ocr_client.ocr_generic(page_bytes)
            page_text = ocr.get("full_text", "")
            if not page_text:
                continue
            if full_text:
                full_text += "\n"
            full_text += f"[第{idx}页]\n" + page_text
            positions += [{"page": idx, **p} for p in ocr.get("positions", [])]
            if ocr.get("confidence"):
                confidences.append(ocr["confidence"])
        if not full_text:
            raise ocr_client.ParseFailure("合同 OCR 未提取到文本")

    # P2-4：无可靠置信度时如实为 None（文本直取/真实 OCR 均不伪造）
    confidence = min(confidences) if confidences else None
    cf = llm_client.extract_contract_fields(full_text)
    if cf is None:
        raise ocr_client.ParseFailure("合同字段提取失败")
    _write_parse(db, attachment, "contract", full_text, cf.model_dump(), positions, confidence)
    _succeed(db, attachment)
    return {"ok": True, "category": "contract"}


async def _parse_real_generic(db, attachment, file_bytes, path, is_pdf, category) -> dict:
    text, images = _content_sources(path, is_pdf, file_bytes)
    full_text = text or ""
    positions: list[dict] = []
    confidences: list[float] = []
    if not text:
        for idx, page_bytes in enumerate(images, 1):
            ocr = await ocr_client.ocr_generic(page_bytes)
            page_text = ocr.get("full_text", "")
            if not page_text:
                continue
            if full_text:
                full_text += "\n"
            full_text += f"[第{idx}页]\n" + page_text
            positions += [{"page": idx, **p} for p in ocr.get("positions", [])]
            if ocr.get("confidence"):
                confidences.append(ocr["confidence"])
        if not full_text:
            raise ocr_client.ParseFailure("未提取到文本")

    # P2-4：无可靠置信度时如实为 None
    confidence = min(confidences) if confidences else None
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
    # 解析失败置 failed（可重试）；manual_review 保留给"解析成功但需人工确认"场景
    attachment.parse_status = "failed"
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
