# -*- coding: utf-8 -*-
"""FastAPI 依赖注入：数据库会话、当前用户。"""
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token, is_token_revoked
from app.db.session import SessionLocal
from app.models.user import User

bearer = HTTPBearer(auto_error=False)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """解析 JWT → 取用户 → 校验状态。任何鉴权接口的第一个依赖。"""
    if cred is None:
        raise HTTPException(401, "缺少访问令牌")
    payload = decode_access_token(cred.credentials)
    if payload is None:
        raise HTTPException(401, "无效或已过期的访问令牌")
    if is_token_revoked(db, payload["jti"]):
        raise HTTPException(401, "令牌已撤销，请重新登录")
    user = db.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise HTTPException(401, "用户不存在或已停用")
    return user
