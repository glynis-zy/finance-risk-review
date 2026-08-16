# -*- coding: utf-8 -*-
"""供应商路由：风险信息查询。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.user import User
from app.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("/lookup")
def lookup_supplier(
    name: str,
    user: User = Depends(require_perm("supplier:view")),
    db: Session = Depends(get_db),
):
    """按名称解析供应商编码（单据→供应商风险页入口）。"""
    code = supplier_service.lookup_code_by_name(db, name)
    if code is None:
        raise HTTPException(404, "未找到该供应商")
    return {"supplier_code": code}


@router.get("/{supplier_code}/risks")
def get_supplier_risks(
    supplier_code: str,
    user: User = Depends(require_perm("supplier:view")),
    db: Session = Depends(get_db),
):
    return supplier_service.get_risks(db, supplier_code)
