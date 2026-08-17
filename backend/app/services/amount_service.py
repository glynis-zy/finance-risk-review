# -*- coding: utf-8 -*-
"""金额核对唯一权威实现（P1-13）。

页面金额、报告金额、风险规则统一复用本计算，避免
"页面金额 = A、报告金额 = B" 的分叉。
"""
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.models.document import DocumentLineItem, FinancialDocument
from app.schemas.document import AmountComparisonOut


def calculate_amount_comparison(db: Session, doc: FinancialDocument) -> AmountComparisonOut:
    """单据/明细/发票/合同/付款金额对照（规格 2.7.13 金额核对）。"""
    line_items_total = db.scalar(
        select(func.coalesce(func.sum(DocumentLineItem.amount), 0)).where(
            DocumentLineItem.document_id == doc.id)) or Decimal(0)

    invoice_total = db.scalar(
        select(func.coalesce(func.sum(InvoiceRecord.amount_including_tax), 0))
        .join(DocumentAttachment, DocumentAttachment.id == InvoiceRecord.attachment_id)
        .where(DocumentAttachment.document_id == doc.id)) or Decimal(0)

    contract_amount: Decimal | None = None
    for f in db.execute(
        select(AttachmentParseResult.fields_json)
        .join(DocumentAttachment, DocumentAttachment.id == AttachmentParseResult.attachment_id)
        .where(DocumentAttachment.document_id == doc.id,
               AttachmentParseResult.document_category == "contract")
    ).scalars().all():
        if f and f.get("contract_amount") is not None:
            contract_amount = Decimal(str(f["contract_amount"]))
            break

    payment_amount = doc.total_amount
    if doc.document_type == "batch_payment":
        payment_amount = db.scalar(
            select(func.coalesce(func.sum(DocumentLineItem.amount), 0)).where(
                DocumentLineItem.document_id == doc.id,
                DocumentLineItem.item_type == "payment",
            )) or Decimal(0)

    differences = {
        "document_minus_line_items": (doc.total_amount - line_items_total),
        "document_minus_invoice": (doc.total_amount - invoice_total),
        "document_minus_contract": (
            (doc.total_amount - contract_amount) if contract_amount is not None else None
        ),
        "document_minus_payment": (doc.total_amount - payment_amount),
    }
    return AmountComparisonOut(
        document_total=doc.total_amount,
        line_items_total=line_items_total,
        invoice_total=invoice_total,
        contract_amount=contract_amount,
        payment_amount=payment_amount,
        differences=differences,
    )
