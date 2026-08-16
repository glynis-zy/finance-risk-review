# -*- coding: utf-8 -*-
"""分析任务 / 风险项 / 风险报告 / 人工复核。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AnalysisTask(TimestampMixin, Base):
    __tablename__ = "analysis_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("review_sessions.id"), nullable=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    task_status: Mapped[str] = mapped_column(String(32), default="queued")  # 见 2.7.12
    current_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RiskFinding(TimestampMixin, Base):
    __tablename__ = "risk_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id"), index=True)
    risk_type: Mapped[str] = mapped_column(String(64))        # 对应 rule_code
    risk_level: Mapped[str] = mapped_column(String(8))        # low/medium/high
    risk_title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    actual_value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reference_value_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    threshold_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 附件/页码/原文位置
    suggestion_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # LLM 润色
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/confirmed/dismissed


class ReviewReport(TimestampMixin, Base):
    __tablename__ = "review_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("analysis_tasks.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    overall_risk_level: Mapped[str] = mapped_column(String(8))
    risk_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # 分类统计/高中低数量
    amount_comparison_json: Mapped[dict | None] = mapped_column(JSON, nullable=True) # 金额核对面板数据
    recommendation: Mapped[str] = mapped_column(String(32), default="manual_review") # 建议通过/补充材料/人工复核/建议驳回
    report_markdown: Mapped[str] = mapped_column(Text)


class ManualReview(Base):
    __tablename__ = "manual_reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("review_reports.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    review_result: Mapped[str] = mapped_column(String(16))     # approved/return/reject/manual
    review_comment: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
