# -*- coding: utf-8 -*-
"""认证路由：login / me / logout。

路由层只收参、调 service、回响应（架构文档 §1.1）。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.deps import bearer, get_current_user, get_db
from app.core.perms import get_permission_codes
from app.core.scopes import get_role_codes
from app.core.security import create_access_token, revoke_token
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
from app.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = auth_service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(401, "用户名或密码错误")

    token = create_access_token(user.id, user.username)
    db.add(AuditLog(
        user_id=user.id, action_type="auth:login",
        resource_type="user", resource_id=str(user.id),
    ))
    db.commit()
    return TokenResponse(access_token=token, user=_to_out(db, user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    return _to_out(db, user)


@router.post("/logout")
def logout(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if cred is not None:
        revoke_token(db, cred.credentials)  # 写 revoked_tokens 表，持久化撤销
        audit_service.log(db, user, "auth:logout", "user", str(user.id))
        db.commit()
    return {"ok": True}


def _to_out(db: Session, user: User) -> UserOut:
    out = UserOut.model_validate(user)
    out.role_codes = sorted(get_role_codes(db, user.id))
    out.permission_codes = sorted(get_permission_codes(db, user.id))
    return out
