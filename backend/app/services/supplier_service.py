# -*- coding: utf-8 -*-
"""供应商服务：档案 + 风险标签 + 黑名单 + 历史付款。"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import FinancialDocument
from app.models.reference import SupplierProfile


def lookup_code_by_name(db: Session, name: str) -> str | None:
    """按供应商名称查编码（供前端从单据跳到供应商风险页）。"""
    row = db.scalar(select(SupplierProfile.supplier_code).where(
        SupplierProfile.supplier_name == name))
    return row


def get_risks(db: Session, supplier_code: str) -> dict:
    supplier = db.scalar(select(SupplierProfile).where(
        SupplierProfile.supplier_code == supplier_code))
    if supplier is None:
        raise HTTPException(404, "供应商不存在")

    # 历史付款：只统计已通过（approved）单据，未审批/审批中的不算"累计付款"（P1-14）
    history = db.scalars(select(FinancialDocument).where(
        FinancialDocument.payee_name == supplier.supplier_name,
        FinancialDocument.document_status == "approved",
    ).order_by(FinancialDocument.apply_date.desc())).all()

    total_paid = sum(d.total_amount for d in history)
    accounts = supplier.bank_accounts_json or {}
    return {
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier.supplier_name,
        "credit_status": supplier.credit_status,
        "blacklist_status": supplier.blacklist_status,
        "risk_tags": (supplier.risk_tags_json or {}).get("tags", []),
        "bank_accounts": accounts.get("accounts", []),
        "history": [{
            "document_no": d.document_no, "amount": str(d.total_amount),
            "apply_date": str(d.apply_date), "status": d.document_status,
        } for d in history],
        "total_paid": str(total_paid),
        "payment_count": len(history),
    }
