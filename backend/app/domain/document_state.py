# -*- coding: utf-8 -*-
"""单据状态机：唯一的状态流转与状态权限（L3）权威实现。

- 所有 `document.document_status = ...` 修改必须经 `transition()`；
- 所有动作级状态守卫（edit/submit/withdraw/void）统一走 `guard()`。
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

DRAFT, PENDING, REVIEWING, RETURNED = "draft", "pending_review", "reviewing", "returned"
APPROVED, REJECTED, WITHDRAWN, VOIDED = "approved", "rejected", "withdrawn", "voided"

# L3 动作守卫：动作 → 允许的当前状态
# submit 同时承担首次提交(draft) 与退回后重提交(returned)，生成新版本+新实例+新分析任务
GUARD: dict[str, set[str]] = {
    "edit": {DRAFT, RETURNED},
    "submit": {DRAFT, RETURNED},
    "withdraw": {PENDING},
    "void": {DRAFT, PENDING},
}


def guard(document, action: str) -> None:
    """动作在当前状态是否允许（不允许抛 409）。"""
    allowed = GUARD.get(action, set())
    if document.document_status not in allowed:
        raise HTTPException(409, f"状态 {document.document_status} 不允许该操作")


def transition(db: Session, document, to_status: str, operator_id: int,
               remark: str = "") -> None:
    """统一状态流转：写 DocumentStatusLog + 更新 status。operator 必须是真实操作人。"""
    from app.models.document import DocumentStatusLog
    db.add(DocumentStatusLog(
        document_id=document.id,
        from_status=document.document_status,
        to_status=to_status,
        operator_id=operator_id,
        remark=remark,
    ))
    document.document_status = to_status
