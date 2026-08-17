# -*- coding: utf-8 -*-
"""Document 聚合：单据 / 明细 / 版本 / 状态日志 的数据访问。"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import (
    DocumentLineItem,
    DocumentStatusLog,
    DocumentVersion,
    FinancialDocument,
)


class DocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, document_id: int) -> FinancialDocument | None:
        return self.db.get(FinancialDocument, document_id)

    def get_by_no(self, document_no: str) -> FinancialDocument | None:
        return self.db.scalar(select(FinancialDocument).where(
            FinancialDocument.document_no == document_no))

    def line_items(self, document_id: int) -> list[DocumentLineItem]:
        return list(self.db.scalars(select(DocumentLineItem).where(
            DocumentLineItem.document_id == document_id)).all())

    def payment_items(self, document_id: int) -> list[DocumentLineItem]:
        return list(self.db.scalars(select(DocumentLineItem).where(
            DocumentLineItem.document_id == document_id,
            DocumentLineItem.item_type == "payment",
        )).all())

    def versions(self, document_id: int) -> list[DocumentVersion]:
        return list(self.db.scalars(select(DocumentVersion).where(
            DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc())).all())

    def status_logs(self, document_id: int) -> list[DocumentStatusLog]:
        return list(self.db.scalars(select(DocumentStatusLog).where(
            DocumentStatusLog.document_id == document_id)
            .order_by(DocumentStatusLog.id.asc())).all())

    def line_items_total(self, document_id: int):
        return self.db.scalar(
            select(func.coalesce(func.sum(DocumentLineItem.amount), 0)).where(
                DocumentLineItem.document_id == document_id))

    def applicant_documents(self, applicant_id: int, exclude_id: int | None = None,
                            statuses: list[str] | None = None) -> list[FinancialDocument]:
        stmt = select(FinancialDocument).where(
            FinancialDocument.applicant_id == applicant_id)
        if exclude_id is not None:
            stmt = stmt.where(FinancialDocument.id != exclude_id)
        if statuses:
            stmt = stmt.where(FinancialDocument.document_status.in_(statuses))
        return list(self.db.scalars(stmt).all())
