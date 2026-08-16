# -*- coding: utf-8 -*-
"""审批路由：我的待办 / 通过 / 退回 / 驳回。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.workflow import ApprovalTask, ApprovalWorkflowNode
from app.models.document import FinancialDocument
from app.models.user import User
from app.services import workflow_service

router = APIRouter(prefix="/approval-tasks", tags=["approvals"])


class ReviewIn(BaseModel):
    review_comment: str = ""


@router.get("")
def list_my_tasks(
    user: User = Depends(require_perm("approval:view")),
    db: Session = Depends(get_db),
):
    """当前用户的待办任务（L2：仅本人任务）+ 单据摘要。"""
    tasks = workflow_service.list_my_tasks(db, user)
    result = []
    for t in tasks:
        from app.models.workflow import ApprovalInstance
        inst = db.get(ApprovalInstance, t.instance_id)
        doc = db.get(FinancialDocument, inst.document_id) if inst else None
        node = db.get(ApprovalWorkflowNode, t.node_id)
        result.append({
            "task_id": t.id,
            "instance_id": t.instance_id,
            "node_name": node.node_name if node else None,
            "document_id": doc.id if doc else None,
            "document_no": doc.document_no if doc else None,
            "document_type": doc.document_type if doc else None,
            "total_amount": str(doc.total_amount) if doc else None,
            "applicant_department": doc.applicant_department if doc else None,
            "document_status": doc.document_status if doc else None,
            "created_at": str(t.created_at),
        })
    return result


@router.post("/{task_id}/approve")
def approve_task(
    task_id: int,
    payload: ReviewIn = ReviewIn(),
    user: User = Depends(require_perm("approval:process")),
    db: Session = Depends(get_db),
):
    return workflow_service.approve(db, user, task_id, payload.review_comment)


@router.post("/{task_id}/return")
def return_task(
    task_id: int,
    payload: ReviewIn = ReviewIn(),
    user: User = Depends(require_perm("approval:process")),
    db: Session = Depends(get_db),
):
    return workflow_service.return_to_applicant(db, user, task_id, payload.review_comment)


@router.post("/{task_id}/reject")
def reject_task(
    task_id: int,
    payload: ReviewIn = ReviewIn(),
    user: User = Depends(require_perm("approval:process")),
    db: Session = Depends(get_db),
):
    return workflow_service.reject(db, user, task_id, payload.review_comment)
