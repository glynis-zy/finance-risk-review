# -*- coding: utf-8 -*-
"""单据访问策略：L2 数据权限 + L3 状态权限的统一收口。

各 service/router 不再自行实现 _ensure_visible/_ensure_owner/_guard，
统一复用本模块的 ensure_editable / ensure_viewable / ensure_owner / ensure_visible。
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.scopes import get_role_codes, visible_document_ids
from app.domain import document_state


def ensure_visible(db: Session, user, doc) -> None:
    """L2：当前用户能看到该单据。"""
    ids = visible_document_ids(db, user)
    if ids is not None and doc.id not in ids:
        raise HTTPException(403, "无权访问该单据")


def ensure_owner(db: Session, user, doc) -> None:
    """申请人本人或管理员才可执行写操作。"""
    roles = get_role_codes(db, user.id)
    if doc.applicant_id != user.id and "admin" not in roles:
        raise HTTPException(403, "仅申请人本人或管理员可操作该单据")


def ensure_editable(db: Session, user, doc) -> None:
    """L2 + L3：可见 + 本人/管理员 + draft/returned 状态。"""
    ensure_visible(db, user, doc)
    ensure_owner(db, user, doc)
    document_state.guard(doc, "edit")


def ensure_viewable(db: Session, user, doc) -> None:
    """L2：仅校验可见（用于查看/下载/发起分析等）。"""
    ensure_visible(db, user, doc)
