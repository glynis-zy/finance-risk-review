# -*- coding: utf-8 -*-
"""L2 数据权限（行级可见范围）。

规则（对应架构文档 §9）：
- admin / finance：全部可见 → 返回 None
- approver：本人任务涉及的单据 ∪ 本人申请的单据
- applicant：仅本人申请的单据

用法：`ids = visible_document_ids(db, user)`；`None`=不过滤，`[]`=无权限，其余按 in 过滤。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import FinancialDocument
from app.models.user import Role, RolePermission, User, UserRole
from app.models.workflow import ApprovalInstance, ApprovalTask


def get_role_codes(db: Session, user_id: int) -> set[str]:
    rows = db.execute(
        select(Role.role_code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    ).scalars().all()
    return set(rows)


def visible_document_ids(db: Session, user: User) -> list[int] | None:
    roles = get_role_codes(db, user.id)
    if {"admin", "finance"} & roles:
        return None  # 全可见

    own = db.execute(
        select(FinancialDocument.id).where(FinancialDocument.applicant_id == user.id)
    ).scalars().all()

    if "approver" in roles:
        task_docs = db.execute(
            select(ApprovalInstance.document_id)
            .join(ApprovalTask, ApprovalTask.instance_id == ApprovalInstance.id)
            .where(ApprovalTask.approver_id == user.id)
        ).scalars().all()
        return list(set(own) | set(task_docs))

    return list(own)


def approval_document_ids(db: Session, user_id: int) -> set[int]:
    """用户作为审批人处理过的单据 id（人工复核/审批范围的精确判定）。"""
    rows = db.execute(
        select(ApprovalInstance.document_id)
        .join(ApprovalTask, ApprovalTask.instance_id == ApprovalInstance.id)
        .where(ApprovalTask.approver_id == user_id)
    ).scalars().all()
    return set(rows)
