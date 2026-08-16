# -*- coding: utf-8 -*-
"""审计路由：操作日志查询（审核记录页/管理员）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.audit import AuditLog
from app.models.user import User

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    page: int = 1,
    size: int = 50,
    user: User = Depends(require_perm("audit:view")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AuditLog).order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
    return [{
        "id": a.id, "user_id": a.user_id, "action_type": a.action_type,
        "resource_type": a.resource_type, "resource_id": a.resource_id,
        "detail": a.detail_json, "created_at": str(a.created_at),
    } for a in rows]
