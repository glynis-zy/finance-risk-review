# -*- coding: utf-8 -*-
"""审批工作流服务：流程匹配 / 实例创建 / 节点流转 / 任务处理。

设计口径（架构文档 §7、function-map §2.2）：
- match_workflow：按 document_type + match_conditions_json（金额区间/部门）匹配
- approve：任务须 pending 且是本人任务（L2/L3）→ 有下节点则推进，否则实例完成、单据 approved
- return/reject：单据 returned/rejected，实例终态，未处理任务 cancelled
"""
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.scopes import get_role_codes
from app.document_schemas import TYPE_LABELS
from app.models.document import FinancialDocument
from app.models.user import User
from app.models.workflow import (
    ApprovalInstance,
    ApprovalTask,
    ApprovalWorkflow,
    ApprovalWorkflowNode,
)
from app.services import audit_service

PENDING, APPROVED, RETURNED, REJECTED = "pending", "approved", "returned", "rejected"


def _pick_user_with_role(db: Session, role_code: str) -> User | None:
    """找一个持有指定角色的启用用户，作为任务办理人。"""
    from app.models.user import Role, UserRole
    return db.scalars(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.role_code == role_code, User.status == "active")
        .limit(1)
    ).first()


def start_approval(db: Session, document: FinancialDocument) -> ApprovalInstance:
    """提交时：匹配流程 → 建实例 → 建首个任务（function-map §2.2）。"""
    wf = _match_workflow(db, document)
    if wf is None:
        raise HTTPException(400, f"{TYPE_LABELS.get(document.document_type, '')} 无匹配审批流程")
    nodes = _workflow_nodes(db, wf.id)
    if not nodes:
        raise HTTPException(400, f"流程 {wf.workflow_name} 未配置节点")

    instance = ApprovalInstance(
        workflow_id=wf.id,
        document_id=document.id,
        document_version=document.current_version,
        instance_status="running",
        started_at=datetime.utcnow(),
    )
    db.add(instance)
    db.flush()

    first = nodes[0]
    instance.current_node_id = first.id
    _create_task(db, instance, first)
    return instance


def list_my_tasks(db: Session, user: User) -> list[ApprovalTask]:
    """L2：只返回分配给当前用户的待办任务。"""
    return list(db.scalars(
        select(ApprovalTask)
        .where(ApprovalTask.approver_id == user.id, ApprovalTask.task_status == PENDING)
        .order_by(ApprovalTask.id.asc())
    ).all())


def approve(db: Session, user: User, task_id: int, review_comment: str = "") -> dict:
    task = _get_owned_pending_task(db, user, task_id)
    instance = db.get(ApprovalInstance, task.instance_id)
    document = db.get(FinancialDocument, instance.document_id)
    _ensure_processable(instance, document)

    if review_comment:
        task.review_comment = review_comment
    _mark_processing(db, document, user.id)
    _finish_task(db, task, "approved")

    nodes = _workflow_nodes(db, instance.workflow_id)
    current_idx = next((i for i, n in enumerate(nodes) if n.id == task.node_id), -1)
    if current_idx < len(nodes) - 1:
        # 有下一节点 → 推进
        nxt = nodes[current_idx + 1]
        instance.current_node_id = nxt.id
        _create_task(db, instance, nxt)
        audit_service.log(db, user, "approval:approve", "approval_task", str(task.id),
                          {"comment": review_comment})
        db.commit()
        return {"result": "approved", "next_node": nxt.node_name}

    # 末节点通过 → 实例完成，单据 approved
    instance.instance_status = "approved"
    instance.finished_at = datetime.utcnow()
    _status_log(db, document, "approved", user.id, "末节点审批通过")
    document.document_status = "approved"
    audit_service.log(db, user, "approval:approve", "approval_task", str(task.id),
                      {"comment": review_comment})
    db.commit()
    return {"result": "approved", "next_node": None}


def return_to_applicant(db: Session, user: User, task_id: int, review_comment: str = "") -> dict:
    task = _get_owned_pending_task(db, user, task_id)
    instance = db.get(ApprovalInstance, task.instance_id)
    document = db.get(FinancialDocument, instance.document_id)
    _ensure_processable(instance, document)

    if review_comment:
        task.review_comment = review_comment
    _mark_processing(db, document, user.id)
    _finish_task(db, task, RETURNED)
    _cancel_instance(db, instance, RETURNED)
    _status_log(db, document, RETURNED, user.id, "审批退回")
    document.document_status = RETURNED
    audit_service.log(db, user, "approval:return", "approval_task", str(task.id),
                      {"comment": review_comment})
    db.commit()
    return {"result": "returned"}


def reject(db: Session, user: User, task_id: int, review_comment: str = "") -> dict:
    task = _get_owned_pending_task(db, user, task_id)
    instance = db.get(ApprovalInstance, task.instance_id)
    document = db.get(FinancialDocument, instance.document_id)
    _ensure_processable(instance, document)

    if review_comment:
        task.review_comment = review_comment
    _mark_processing(db, document, user.id)
    _finish_task(db, task, REJECTED)
    _cancel_instance(db, instance, REJECTED)
    _status_log(db, document, "rejected", user.id, "审批驳回")
    document.document_status = "rejected"
    audit_service.log(db, user, "approval:reject", "approval_task", str(task.id),
                      {"comment": review_comment})
    db.commit()
    return {"result": "rejected"}


# ---------- 内部工具 ----------

def _get_owned_pending_task(db: Session, user: User, task_id: int) -> ApprovalTask:
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise HTTPException(404, "审批任务不存在")
    if task.task_status != PENDING:
        raise HTTPException(409, "该任务已处理")
    if task.approver_id != user.id:
        roles = get_role_codes(db, user.id)
        if "admin" not in roles:
            raise HTTPException(403, "不是分配给您的任务")
    return task


def _workflow_nodes(db: Session, workflow_id: int) -> list[ApprovalWorkflowNode]:
    return list(db.scalars(
        select(ApprovalWorkflowNode)
        .where(ApprovalWorkflowNode.workflow_id == workflow_id)
        .order_by(ApprovalWorkflowNode.node_order.asc())
    ).all())


def _match_workflow(db: Session, document: FinancialDocument) -> ApprovalWorkflow | None:
    """按 document_type + 金额区间 + 部门匹配启用流程。"""
    wfs = db.scalars(
        select(ApprovalWorkflow).where(
            ApprovalWorkflow.document_type == document.document_type,
            ApprovalWorkflow.status == "active",
        )
    ).all()
    for wf in wfs:
        cond = wf.match_conditions_json or {}
        amount_min = Decimal(str(cond.get("amount_min", "0")))
        amount_max = cond.get("amount_max")
        if document.total_amount < amount_min:
            continue
        if amount_max is not None and document.total_amount > Decimal(str(amount_max)):
            continue
        dept = cond.get("department")
        if dept and document.applicant_department != dept:
            continue
        return wf
    return None


def _create_task(db: Session, instance: ApprovalInstance, node: ApprovalWorkflowNode) -> ApprovalTask:
    approver = _pick_user_with_role(db, node.approver_role)
    task = ApprovalTask(
        instance_id=instance.id,
        node_id=node.id,
        approver_id=approver.id if approver else None,
        task_status=PENDING,
    )
    db.add(task)
    db.flush()
    return task


def _ensure_processable(instance: ApprovalInstance, document: FinancialDocument) -> None:
    """P0-2：实例已结束 / 单据非审批中状态 → 拒绝处理旧任务。"""
    if instance.instance_status != "running":
        raise HTTPException(409, "审批实例已结束，无法处理该任务")
    if document.document_status not in ("pending_review", "reviewing"):
        raise HTTPException(409, f"单据状态 {document.document_status} 不允许审批操作")


def _status_log(db: Session, document: FinancialDocument, to_status: str,
                operator_id: int, remark: str = "") -> None:
    """写单据状态日志，operator 必须是真实操作人（P1-3）。"""
    from app.models.document import DocumentStatusLog
    db.add(DocumentStatusLog(
        document_id=document.id,
        from_status=document.document_status,
        to_status=to_status,
        operator_id=operator_id,
        remark=remark,
    ))


def _mark_processing(db: Session, document: FinancialDocument, operator_id: int) -> None:
    """首个任务被处理 → 单据进入 reviewing；operator 记真实审批人（P1-3）。"""
    if document.document_status == "pending_review":
        _status_log(db, document, "reviewing", operator_id, "审批处理开始")
        document.document_status = "reviewing"


def _finish_task(db: Session, task: ApprovalTask, result: str) -> None:
    task.task_status = result
    task.processed_at = datetime.utcnow()


def _cancel_instance(db: Session, instance: ApprovalInstance, status: str) -> None:
    """return/reject/withdraw 共用：实例终态 + 未处理任务 cancelled。"""
    instance.instance_status = status
    instance.finished_at = datetime.utcnow()
    tasks = db.scalars(
        select(ApprovalTask).where(
            ApprovalTask.instance_id == instance.id,
            ApprovalTask.task_status == PENDING,
        )
    ).all()
    for t in tasks:
        t.task_status = "cancelled"
        t.processed_at = datetime.utcnow()
