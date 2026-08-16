# -*- coding: utf-8 -*-
"""审计服务：统一写审计日志（规格 2.7.14：操作人/操作时间/变更内容）。"""
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


def log(
    db: Session,
    user: User | None,
    action_type: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: dict | None = None,
) -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        action_type=action_type,
        resource_type=resource_type,
        resource_id=resource_id,
        detail_json=detail,
    ))
