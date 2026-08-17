# -*- coding: utf-8 -*-
"""Attachment 聚合：附件 / 解析结果 / 发票记录 的数据访问。"""
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)


class AttachmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_document(self, document_id: int) -> list[DocumentAttachment]:
        return list(self.db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == document_id)).all())

    def pending_attachments(self, document_id: int) -> list[DocumentAttachment]:
        """document_version=0 的暂存附件（提交时绑定版本）。"""
        return list(self.db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == document_id,
            DocumentAttachment.document_version == 0,
        )).all())

    def invoices_of(self, attachment_ids: list[int]) -> list[InvoiceRecord]:
        if not attachment_ids:
            return []
        return list(self.db.scalars(select(InvoiceRecord).where(
            InvoiceRecord.attachment_id.in_(attachment_ids))).all())

    def parse_results_of(self, attachment_ids: list[int]) -> list[AttachmentParseResult]:
        if not attachment_ids:
            return []
        return list(self.db.scalars(select(AttachmentParseResult).where(
            AttachmentParseResult.attachment_id.in_(attachment_ids))).all())

    def invoice_total(self, document_id: int):
        return self.db.scalar(
            select(func.coalesce(func.sum(InvoiceRecord.amount_including_tax), 0))
            .join(DocumentAttachment, DocumentAttachment.id == InvoiceRecord.attachment_id)
            .where(DocumentAttachment.document_id == document_id))

    def contract_fields(self, document_id: int) -> dict | None:
        """单据的合同提取字段（contract parse_result 的 fields）。"""
        for fields in self.db.execute(
            select(AttachmentParseResult.fields_json)
            .join(DocumentAttachment, DocumentAttachment.id == AttachmentParseResult.attachment_id)
            .where(DocumentAttachment.document_id == document_id,
                   AttachmentParseResult.document_category == "contract")
        ).scalars().all():
            if fields and fields.get("contract_amount") is not None:
                return fields
        return None

    def clear_parse_results(self, attachment_id: int) -> None:
        self.db.execute(delete(AttachmentParseResult).where(
            AttachmentParseResult.attachment_id == attachment_id))
        self.db.execute(delete(InvoiceRecord).where(InvoiceRecord.attachment_id == attachment_id))
