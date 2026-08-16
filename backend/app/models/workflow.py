# -*- coding: utf-8 -*-
"""审批流程 / 节点 / 实例 / 任务。"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ApprovalWorkflow(TimestampMixin, Base):
    __tablename__ = "approval_workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_name: Mapped[str] = mapped_column(String(64))
    document_type: Mapped[str] = mapped_column(String(32), index=True)
    match_conditions_json: Mapped[dict] = mapped_column(JSON)  # {amount_min, amount_max, department}
    status: Mapped[str] = mapped_column(String(16), default="active")


class ApprovalWorkflowNode(TimestampMixin, Base):
    __tablename__ = "approval_workflow_nodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("approval_workflows.id"), index=True)
    node_name: Mapped[str] = mapped_column(String(64))
    node_order: Mapped[int] = mapped_column(Integer)
    approver_role: Mapped[str] = mapped_column(String(32))  # approver / finance / admin
    approval_mode: Mapped[str] = mapped_column(String(16), default="single")


class ApprovalInstance(Base):
    __tablename__ = "approval_instances"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("approval_workflows.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("financial_documents.id"), index=True)
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    instance_status: Mapped[str] = mapped_column(String(16), default="running")
    current_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApprovalTask(TimestampMixin, Base):
    __tablename__ = "approval_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("approval_instances.id"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("approval_workflow_nodes.id"), index=True)
    approver_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    task_status: Mapped[str] = mapped_column(String(16), default="pending")
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
