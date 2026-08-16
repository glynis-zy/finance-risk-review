# -*- coding: utf-8 -*-
"""审批流程配置路由：流程 + 节点 CRUD（管理员）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.user import User
from app.models.workflow import ApprovalWorkflow, ApprovalWorkflowNode
from app.services import audit_service

router = APIRouter(prefix="/approval-workflows", tags=["workflows"])


class NodeIn(BaseModel):
    node_name: str
    approver_role: str
    node_order: int


class WorkflowIn(BaseModel):
    workflow_name: str
    document_type: str
    match_conditions: dict = {}
    nodes: list[NodeIn] = []


def _to_dict(wf: ApprovalWorkflow, nodes: list[ApprovalWorkflowNode]) -> dict:
    return {
        "id": wf.id, "workflow_name": wf.workflow_name,
        "document_type": wf.document_type, "match_conditions": wf.match_conditions_json,
        "status": wf.status,
        "nodes": [{"id": n.id, "node_name": n.node_name,
                   "approver_role": n.approver_role, "node_order": n.node_order}
                  for n in sorted(nodes, key=lambda x: x.node_order)],
    }


@router.get("")
def list_workflows(
    user: User = Depends(require_perm("workflow:view")),
    db: Session = Depends(get_db),
):
    result = []
    for wf in db.scalars(select(ApprovalWorkflow).order_by(ApprovalWorkflow.id)).all():
        nodes = db.scalars(select(ApprovalWorkflowNode).where(
            ApprovalWorkflowNode.workflow_id == wf.id)).all()
        result.append(_to_dict(wf, nodes))
    return result


@router.post("")
def create_workflow(
    payload: WorkflowIn,
    user: User = Depends(require_perm("workflow:manage")),
    db: Session = Depends(get_db),
):
    wf = ApprovalWorkflow(
        workflow_name=payload.workflow_name,
        document_type=payload.document_type,
        match_conditions_json=payload.match_conditions,
    )
    db.add(wf)
    db.flush()
    for n in payload.nodes:
        db.add(ApprovalWorkflowNode(
            workflow_id=wf.id, node_name=n.node_name,
            approver_role=n.approver_role, node_order=n.node_order,
        ))
    audit_service.log(db, user, "workflow:create", "approval_workflow", str(wf.id))
    db.commit()
    return _to_dict(wf, db.scalars(select(ApprovalWorkflowNode).where(
        ApprovalWorkflowNode.workflow_id == wf.id)).all())


@router.patch("/{workflow_id}")
def update_workflow(
    workflow_id: int,
    payload: WorkflowIn,
    user: User = Depends(require_perm("workflow:manage")),
    db: Session = Depends(get_db),
):
    wf = db.get(ApprovalWorkflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "流程不存在")
    wf.workflow_name = payload.workflow_name
    wf.document_type = payload.document_type
    wf.match_conditions_json = payload.match_conditions
    # 简单策略：更新时重建节点
    old_nodes = db.scalars(select(ApprovalWorkflowNode).where(
        ApprovalWorkflowNode.workflow_id == wf.id)).all()
    for n in old_nodes:
        db.delete(n)
    for n in payload.nodes:
        db.add(ApprovalWorkflowNode(
            workflow_id=wf.id, node_name=n.node_name,
            approver_role=n.approver_role, node_order=n.node_order,
        ))
    audit_service.log(db, user, "workflow:update", "approval_workflow", str(workflow_id))
    db.commit()
    return _to_dict(wf, db.scalars(select(ApprovalWorkflowNode).where(
        ApprovalWorkflowNode.workflow_id == wf.id)).all())
