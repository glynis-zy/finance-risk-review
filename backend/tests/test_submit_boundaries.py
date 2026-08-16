# -*- coding: utf-8 -*-
"""提交边界测试（P1-2 / P1-6）：
- 两张 invoice 不能冒充 contract+invoice 通过对公付款提交；
- 零金额 / 非法比例 / 差旅日期倒序被拒绝。
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.document_schemas import validate_type_fields
from app.models.attachment import DocumentAttachment
from app.models.document import FinancialDocument
from app.models.user import User
from app.schemas.document import DocumentCreate, LineItemCreate
from app.services import document_service


def _user(db) -> User:
    u = User(username="b_u", display_name="边界", password_hash="x")
    db.add(u)
    db.flush()
    return u


def test_two_invoices_cannot_pass_company_submit(db):
    """对公付款单要求 contract+invoice；上传两张 invoice 不能通过提交（P1-2 类别校验）。"""
    user = _user(db)
    doc = FinancialDocument(
        document_type="company_payment", document_no="CP-1", applicant_id=user.id,
        applicant_department="A", budget_department="A", payee_name="X", payee_account="Y",
        expense_category="服务费", total_amount=Decimal("1000"), currency="CNY",
        apply_date=date(2026, 8, 1), document_status="draft", current_version=0,
    )
    db.add(doc)
    db.flush()
    db.add(DocumentAttachment(document_id=doc.id, file_name="发票1.png", file_type="png",
                              file_size=1, file_path="a", file_hash="h1",
                              storage_status="stored", parse_status="pending",
                              document_category="invoice"))
    db.add(DocumentAttachment(document_id=doc.id, file_name="发票2.png", file_type="png",
                              file_size=1, file_path="b", file_hash="h2",
                              storage_status="stored", parse_status="pending",
                              document_category="invoice"))
    db.commit()

    with pytest.raises(HTTPException) as ei:
        document_service.submit(db, user, doc.id)
    assert ei.value.status_code == 400
    assert "contract" in str(ei.value.detail)   # 明确缺"合同"类别，而不是缺附件数量


def test_zero_amount_rejected():
    with pytest.raises(ValidationError):
        DocumentCreate(
            document_type="expense", applicant_department="A", budget_department="A",
            payee_name="X", payee_account="Y", expense_category="差旅",
            total_amount=Decimal("0"), currency="CNY", apply_date=date(2026, 8, 1),
        )


def test_negative_line_item_amount_rejected():
    with pytest.raises(ValidationError):
        LineItemCreate(item_type="expense", item_name="交通", amount=Decimal("-1"))


def test_percent_ratio_range_rejected(db):
    _, errors = validate_type_fields("company_payment", {"payment_ratio": 150})
    assert any("0~100" in e for e in errors)


def test_travel_date_order_rejected(db):
    _, errors = validate_type_fields("travel", {
        "travel_destination": "上海", "travel_start": "2026-08-10", "travel_end": "2026-08-05",
        "transport_fee": 2000, "hotel_fee": 500, "meal_fee": 300, "allowance": 200,
    })
    assert any("出差开始日期不能晚于结束日期" in e for e in errors)
