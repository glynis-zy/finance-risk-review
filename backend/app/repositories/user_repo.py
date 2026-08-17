# -*- coding: utf-8 -*-
"""User 聚合：用户 / 角色 / 权限 的数据访问。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import Permission, Role, RolePermission, User, UserRole


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self.db.scalar(select(User).where(User.username == username))

    def role_codes(self, user_id: int) -> set[str]:
        rows = self.db.execute(
            select(Role.role_code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        ).scalars().all()
        return set(rows)

    def permission_codes(self, user_id: int) -> set[str]:
        rows = self.db.execute(
            select(Permission.permission_code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == user_id)
        ).scalars().all()
        return set(rows)

    def active_user_with_role(self, role_code: str) -> User | None:
        return self.db.scalars(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.role_code == role_code, User.status == "active")
            .limit(1)
        ).first()
