# -*- coding: utf-8 -*-
"""认证服务：用户名密码校验 → 用户对象。

说明：JWT 签发/校验/撤销在 core/security.py；权限码查询在 core/perms.py。
本服务只负责"业务层校验"，保持职责单一。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.user import User


def authenticate(db: Session, username: str, password: str) -> User | None:
    """校验用户名 + 密码哈希，成功返回用户，失败返回 None。"""
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
