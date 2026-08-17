# -*- coding: utf-8 -*-
"""Analysis 聚合：分析任务 / 风险项 / 报告 / 人工复核 的数据访问。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import AnalysisTask, ManualReview, ReviewReport, RiskFinding


class AnalysisRepository:
    def __init__(self, db: Session):
        self.db = db

    def task(self, task_id: int) -> AnalysisTask | None:
        return self.db.get(AnalysisTask, task_id)

    def latest_task(self, document_id: int) -> AnalysisTask | None:
        return self.db.scalar(select(AnalysisTask).where(
            AnalysisTask.document_id == document_id).order_by(AnalysisTask.id.desc()))

    def running_task(self, document_id: int) -> AnalysisTask | None:
        return self.db.scalar(select(AnalysisTask).where(
            AnalysisTask.document_id == document_id,
            AnalysisTask.task_status.in_(
                ("queued", "querying_document", "loading_attachments",
                 "parsing_attachments", "analyzing"),
            ),
        ).order_by(AnalysisTask.id.desc()))

    def findings(self, task_id: int) -> list[RiskFinding]:
        return list(self.db.scalars(select(RiskFinding).where(
            RiskFinding.task_id == task_id)).all())

    def report(self, task_id: int) -> ReviewReport | None:
        return self.db.scalar(select(ReviewReport).where(ReviewReport.task_id == task_id))

    def report_by_id(self, report_id: int) -> ReviewReport | None:
        return self.db.get(ReviewReport, report_id)

    def manual_reviews(self, report_id: int) -> list[ManualReview]:
        return list(self.db.scalars(select(ManualReview).where(
            ManualReview.report_id == report_id)).all())
