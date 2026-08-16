# -*- coding: utf-8 -*-
"""管理端路由：用户 / 角色权限 / 系统参数（规格 2.7.3：系统管理员维护）。

权限码：user:manage / role:manage / system:manage。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import get_permission_codes, require_perm
from app.core.security import hash_password
from app.models.reference import SysParam
from app.models.user import Permission, Role, RolePermission, User, UserRole
from app.services import audit_service, sysparam_service

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreate(BaseModel):
    username: str
    display_name: str
    password: str
    role_codes: list[str] = []


class UserUpdate(BaseModel):
    display_name: str | None = None
    status: str | None = None
    password: str | None = None
    role_codes: list[str] | None = None


class RolePermUpdate(BaseModel):
    permission_codes: list[str]


class SysParamUpdate(BaseModel):
    param_value: str


def _user_out(db: Session, u: User) -> dict:
    roles = db.execute(
        select(Role.role_code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == u.id)
    ).scalars().all()
    return {
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "status": u.status, "role_codes": list(roles),
        "permission_codes": sorted(get_permission_codes(db, u.id)),
        "created_at": str(u.created_at),
    }


@router.get("/users")
def list_users(
    user: User = Depends(require_perm("user:manage")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [_user_out(db, u) for u in rows]


@router.post("/users")
def create_user(
    payload: UserCreate,
    user: User = Depends(require_perm("user:manage")),
    db: Session = Depends(get_db),
):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "用户名已存在")
    u = User(username=payload.username, display_name=payload.display_name,
             password_hash=hash_password(payload.password), status="active")
    db.add(u)
    db.flush()
    _set_roles(db, u.id, payload.role_codes)
    audit_service.log(db, user, "user:create", "user", str(u.id), {"username": u.username})
    db.commit()
    return _user_out(db, u)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdate,
    user: User = Depends(require_perm("user:manage")),
    db: Session = Depends(get_db),
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if payload.display_name is not None:
        u.display_name = payload.display_name
    if payload.status is not None:
        if payload.status not in ("active", "disabled"):
            raise HTTPException(400, "status 取值 active/disabled")
        u.status = payload.status
    if payload.password:
        u.password_hash = hash_password(payload.password)
    if payload.role_codes is not None:
        _set_roles(db, u.id, payload.role_codes)
    audit_service.log(db, user, "user:update", "user", str(user_id))
    db.commit()
    return _user_out(db, u)


def _set_roles(db: Session, user_id: int, role_codes: list[str]) -> None:
    db.execute(select(UserRole).where(UserRole.user_id == user_id).delete())
    for code in role_codes:
        role = db.scalar(select(Role).where(Role.role_code == code))
        if role:
            db.add(UserRole(user_id=user_id, role_id=role.id))


@router.get("/roles")
def list_roles(
    user: User = Depends(require_perm("role:manage")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Role).order_by(Role.id)).all()
    result = []
    for r in rows:
        perms = db.execute(
            select(Permission.permission_code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == r.id)
        ).scalars().all()
        result.append({"id": r.id, "role_code": r.role_code, "role_name": r.role_name,
                       "permission_codes": list(perms), "status": r.status})
    return result


@router.patch("/roles/{role_id}/permissions")
def update_role_permissions(
    role_id: int,
    payload: RolePermUpdate,
    user: User = Depends(require_perm("role:manage")),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    db.execute(select(RolePermission).where(RolePermission.role_id == role_id).delete())
    for code in payload.permission_codes:
        perm = db.scalar(select(Permission).where(Permission.permission_code == code))
        if perm:
            db.add(RolePermission(role_id=role_id, permission_id=perm.id))
    audit_service.log(db, user, "role:update_permissions", "role", str(role_id),
                      {"permission_codes": payload.permission_codes})
    db.commit()
    return {"ok": True}


@router.get("/permissions")
def list_permissions(
    user: User = Depends(require_perm("role:manage")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(Permission).order_by(Permission.id)).all()
    return [{"permission_code": p.permission_code, "permission_name": p.permission_name,
             "resource_type": p.resource_type, "action_type": p.action_type} for p in rows]


@router.get("/sys-params")
def list_sys_params(
    user: User = Depends(require_perm("system:manage")),
    db: Session = Depends(get_db),
):
    return sysparam_service.all(db)


@router.patch("/sys-params/{param_key}")
def update_sys_param(
    param_key: str,
    payload: SysParamUpdate,
    user: User = Depends(require_perm("system:manage")),
    db: Session = Depends(get_db),
):
    p = sysparam_service.set_value(db, param_key, payload.param_value, user)
    audit_service.log(db, user, "sys_param:update", "sys_param", param_key,
                      {"value": payload.param_value})
    return {"param_key": p.param_key, "param_value": p.param_value}
