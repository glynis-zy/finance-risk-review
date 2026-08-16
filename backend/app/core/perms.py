# -*- coding: utf-8 -*-
"""L1 RBAC 功能权限：require_perm 依赖工厂。

用法（路由内）：
    @router.get("/documents")
    def list_documents(user: User = Depends(require_perm("document:view"))): ...
"""
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import Permission, RolePermission, User, UserRole


def get_permission_codes(db: Session, user_id: int) -> set[str]:
    """取用户全部 permission_code（user_roles → role_permissions → permissions）。"""
    rows = db.execute(
        select(Permission.permission_code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
    ).scalars().all()
    return set(rows)


def require_perm(*codes: str):
    def checker(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        owned = get_permission_codes(db, user.id)
        if not any(c in owned for c in codes):
            raise HTTPException(403, f"缺少权限: {'/'.join(codes)}")
        return user

    return checker
