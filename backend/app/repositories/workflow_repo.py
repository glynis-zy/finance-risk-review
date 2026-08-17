# -*- coding: utf-8 -*-
"""Workflow 聚合：流程 / 节点 / 实例 / 任务 的数据访问。"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import (
    ApprovalInstance,
    ApprovalTask,
    ApprovalWorkflow,
    ApprovalWorkflowNode,
)


class WorkflowRepository:
    def __init__(self, db: Session):
        self.db = db

    def match_workflow(self, document_type: str, amount: Decimal,
                       department: str) -> ApprovalWorkflow | None:
        """按 单据类型 + 金额区间 + 部门 匹配启用流程。"""
        wfs = self.db.scalars(select(ApprovalWorkflow).where(
            ApprovalWorkflow.document_type == document_type,
            ApprovalWorkflow.status == "active",
        )).all()
        for wf in wfs:
            cond = wf.match_conditions_json or {}
            amount_min = Decimal(str(cond.get("amount_min", "0")))
            amount_max = cond.get("amount_max")
            if amount < amount_min:
                continue
            if amount_max is not None and amount > Decimal(str(amount_max)):
                continue
            dept = cond.get("department")
            if dept and department != dept:
                continue
            return wf
        return None

    def nodes(self, workflow_id: int) -> list[ApprovalWorkflowNode]:
        return list(self.db.scalars(select(ApprovalWorkflowNode).where(
            ApprovalWorkflowNode.workflow_id == workflow_id)
            .order_by(ApprovalWorkflowNode.node_order.asc())).all())

    def instance(self, instance_id: int) -> ApprovalInstance | None:
        return self.db.get(ApprovalInstance, instance_id)

    def instances_of_document(self, document_id: int,
                              document_version: int | None = None) -> list[ApprovalInstance]:
        stmt = select(ApprovalInstance).where(ApprovalInstance.document_id == document_id)
        if document_version is not None:
            stmt = stmt.where(ApprovalInstance.document_version == document_version)
        return list(self.db.scalars(stmt).all())

    def running_instances(self, document_id: int) -> list[ApprovalInstance]:
        return list(self.db.scalars(select(ApprovalInstance).where(
            ApprovalInstance.document_id == document_id,
            ApprovalInstance.instance_status == "running",
        )).all())

    def tasks_of_instance(self, instance_id: int) -> list[ApprovalTask]:
        return list(self.db.scalars(select(ApprovalTask).where(
            ApprovalTask.instance_id == instance_id)).all())

    def pending_tasks_of_instance(self, instance_id: int) -> list[ApprovalTask]:
        return list(self.db.scalars(select(ApprovalTask).where(
            ApprovalTask.instance_id == instance_id,
            ApprovalTask.task_status == "pending",
        )).all())

    def my_pending_tasks(self, approver_id: int) -> list[ApprovalTask]:
        return list(self.db.scalars(select(ApprovalTask).where(
            ApprovalTask.approver_id == approver_id,
            ApprovalTask.task_status == "pending",
        ).order_by(ApprovalTask.id.asc())).all())

    def task(self, task_id: int) -> ApprovalTask | None:
        return self.db.get(ApprovalTask, task_id)
