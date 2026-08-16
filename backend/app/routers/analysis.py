# -*- coding: utf-8 -*-
"""分析路由：任务状态轮询 / 风险项列表 / 风险报告。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.user import User
from app.services import analysis_service

router = APIRouter(prefix="/analysis-tasks", tags=["analysis"])


@router.get("/{task_id}")
def get_task_status(
    task_id: int,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    """轮询用：状态 + 当前步骤（规格 2.7.12 task_status）。"""
    return analysis_service.get_status(db, user, task_id)


@router.get("/{task_id}/findings")
def get_findings(
    task_id: int,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    return analysis_service.get_findings(db, user, task_id)


@router.get("/{task_id}/report")
def get_report(
    task_id: int,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    return analysis_service.get_report(db, user, task_id)
