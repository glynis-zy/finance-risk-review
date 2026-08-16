# -*- coding: utf-8 -*-
"""单据、版本、明细、状态日志。

设计口径（架构文档 §6.3）：
- 5 类单据共用一张表，`document_type` 区分；
- 类型专属字段存 `type_fields_json`，结构由 document_schemas/*.py 声明；
- 提交/重提交写版本快照。
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin

# 单据类型常量（document_type 取值）
DOCUMENT_TYPES = (
    "company_payment",   # 对公付款单
    "advance_payment",   # 预付款单
    "batch_payment",     # 批量付款单
    "expense",           # 费用报销单
    "travel",            # 差旅报销单
)


class FinancialDocument(TimestampMixin, Base):
    __tablename__ = "financial_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    document_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    applicant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    applicant_department: Mapped[str] = mapped_column(String(64))
    budget_department: Mapped[str] = mapped_column(String(64))
    payee_name: Mapped[str] = mapped_column(String(128))
    payee_account: Mapped[str] = mapped_column(String(64))
    expense_category: Mapped[str] = mapped_column(String(32))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    apply_date: Mapped[date] = mapped_column(Date)
    reason_text: Mapped[str] = mapped_column(Text, default="")
    document_status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    type_fields_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DocumentVersion(TimestampMixin, Base):
    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    document_snapshot_json: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class DocumentLineItem(Base):
    __tablename__ = "document_line_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(16))  # expense / payment / ...
    item_name: Mapped[str] = mapped_column(String(128))
    expense_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expense_location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class DocumentStatusLog(TimestampMixin, Base):
    __tablename__ = "document_status_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
