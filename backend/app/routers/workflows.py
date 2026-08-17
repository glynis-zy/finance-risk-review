# -*- coding: utf-8 -*-
"""审批流程配置路由：流程 + 节点 CRUD（管理员）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.document import DOCUMENT_TYPES
from app.models.user import Role, User, UserRole
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
    match_conditions: dict = Field(default_factory=dict)
    priority: int = 0   # 匹配优先级，大者优先（P0-3）
    nodes: list[NodeIn] = Field(default_factory=list)


VALID_APPROVER_ROLES = {"approver", "finance", "admin"}


def _validate_workflow(db: Session, payload: WorkflowIn) -> None:
    """P1-7：流程配置基础校验——避免创建永远没人能处理的审批任务。"""
    if payload.document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, f"非法单据类型: {payload.document_type}")
    if not payload.nodes:
        raise HTTPException(400, "流程至少需要一个审批节点")
    orders = [n.node_order for n in payload.nodes]
    if len(orders) != len(set(orders)):
        raise HTTPException(400, "节点顺序 node_order 必须唯一")
    cond = payload.match_conditions or {}
    if cond.get("amount_max") is not None and cond.get("amount_min", 0) > cond["amount_max"]:
        raise HTTPException(400, "amount_min 不能大于 amount_max")
    for n in payload.nodes:
        if n.approver_role not in VALID_APPROVER_ROLES:
            raise HTTPException(400, f"非法审批角色: {n.approver_role}")
        # 与 resolve_approver 对齐：角色下必须有拥有 approval:process 的可用用户
        from app.repositories.user_repo import UserRepository
        users = UserRepository(db).users_with_role_and_perm(n.approver_role, "approval:process")
        if not users:
            raise HTTPException(400, f"角色 {n.approver_role} 下没有具备审批权限的用户，无法创建审批任务")


def _to_dict(wf: ApprovalWorkflow, nodes: list[ApprovalWorkflowNode]) -> dict:
    return {
        "id": wf.id, "workflow_name": wf.workflow_name,
        "document_type": wf.document_type, "match_conditions": wf.match_conditions_json,
        "priority": wf.priority, "status": wf.status,
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
    _validate_workflow(db, payload)
    wf = ApprovalWorkflow(
        workflow_name=payload.workflow_name,
        document_type=payload.document_type,
        match_conditions_json=payload.match_conditions,
        priority=payload.priority,
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
    _validate_workflow(db, payload)
    wf = db.get(ApprovalWorkflow, workflow_id)
    if wf is None:
        raise HTTPException(404, "流程不存在")
    wf.workflow_name = payload.workflow_name
    wf.document_type = payload.document_type
    wf.match_conditions_json = payload.match_conditions
    wf.priority = payload.priority
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
