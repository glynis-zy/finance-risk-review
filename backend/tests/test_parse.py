# -*- coding: utf-8 -*-
"""附件解析幂等测试（P1-4）：同一发票连续解析两次，InvoiceRecord 不重复、金额不翻倍。"""
import asyncio
from datetime import date
from decimal import Decimal

from sqlalchemy import func

from app.models.attachment import DocumentAttachment, InvoiceRecord
from app.models.document import FinancialDocument
from app.services import parse_pipeline


def test_parse_invoice_idempotent(db, monkeypatch):
    doc = FinancialDocument(
        document_type="expense", document_no="EX-1", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X", payee_account="Y",
        expense_category="差旅", total_amount=Decimal("5000"), currency="CNY",
        apply_date=date(2026, 8, 1), document_status="pending_review", current_version=1,
    )
    db.add(doc)
    db.flush()
    att = DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                             file_size=1, file_path="x", file_hash="h",
                             storage_status="stored", parse_status="pending",
                             document_category="invoice")
    db.add(att)
    db.commit()

    async def fake_ocr(file_bytes):
        return {"invoice_code": "C1", "invoice_no": "N1", "seller_name": "S", "buyer_name": "B",
                "invoice_date": "2026-08-10", "amount_including_tax": 5000,
                "tax_amount": 500, "amount_excluding_tax": 4500, "confidence": 0.99}

    monkeypatch.setattr("app.clients.ocr.ocr_invoice", fake_ocr)

    asyncio.run(parse_pipeline.parse_attachment(db, att))
    db.commit()
    asyncio.run(parse_pipeline.parse_attachment(db, att))   # 再解析一次
    db.commit()

    assert db.query(InvoiceRecord).filter_by(attachment_id=att.id).count() == 1
    total = db.query(func.coalesce(func.sum(InvoiceRecord.amount_including_tax), 0)).filter(
        InvoiceRecord.attachment_id == att.id).scalar()
    assert float(total) == 5000.0   # 金额合计不翻倍
    assert att.parse_status == "succeeded"
