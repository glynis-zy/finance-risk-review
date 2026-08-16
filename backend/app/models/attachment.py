# -*- coding: utf-8 -*-
"""附件、解析结果、发票记录。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class DocumentAttachment(TimestampMixin, Base):
    __tablename__ = "document_attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(32))      # pdf/png/jpg
    file_size: Mapped[int] = mapped_column(Integer)
    file_path: Mapped[str] = mapped_column(String(512))      # 相对 storage_dir 的路径
    file_hash: Mapped[str] = mapped_column(String(64), index=True)  # 去重/重复票据依据
    storage_status: Mapped[str] = mapped_column(String(16), default="stored")
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")


class AttachmentParseResult(TimestampMixin, Base):
    __tablename__ = "attachment_parse_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("document_attachments.id"), index=True)
    document_category: Mapped[str] = mapped_column(String(32))  # invoice/contract/itinerary/payment_doc
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fields_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_positions_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)


class InvoiceRecord(TimestampMixin, Base):
    __tablename__ = "invoice_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("document_attachments.id"), index=True)
    invoice_code: Mapped[str | None] = mapped_column(String(32), index=True)
    invoice_no: Mapped[str | None] = mapped_column(String(32), index=True)
    seller_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_excluding_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    amount_including_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
