# -*- coding: utf-8 -*-
"""单据路由：CRUD / 复制 / 提交 / 撤回 / 作废 / 明细 / 金额核对 / 类型元数据。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.document_schemas import TYPE_FIELD_SCHEMAS, TYPE_LABELS, get_schema
from app.models.user import User
from app.schemas.document import (
    DocumentCreate,
    DocumentOut,
    DocumentUpdate,
    LineItemCreate,
    LineItemUpdate,
)
from app.services import analysis_service, document_service

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/types", response_model=list[dict])
def list_types(user: User = Depends(require_perm("document:view"))):
    """单据类型 + 字段定义（前端动态表单数据源）。"""
    return [
        {"document_type": t, "label": TYPE_LABELS.get(t, t), "fields": get_schema(t)}
        for t in TYPE_FIELD_SCHEMAS
    ]


@router.post("", response_model=DocumentOut)
def create_document(
    payload: DocumentCreate,
    user: User = Depends(require_perm("document:create")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(document_service.create(db, user, payload))


@router.get("")
def list_documents(
    document_type: str | None = None,
    document_no: str | None = None,
    applicant: str | None = None,
    department: str | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_perm("document:view")),
    db: Session = Depends(get_db),
):
    rows, total = document_service.query(
        db, user,
        document_type=document_type, document_no=document_no,
        applicant=applicant, department=department, status=status,
        date_from=date_from, date_to=date_to, page=page, size=size,
    )
    return {
        "total": total,
        "items": [DocumentOut.model_validate(d) for d in rows],
    }


@router.get("/{document_id}")
def get_document(
    document_id: int,
    user: User = Depends(require_perm("document:view")),
    db: Session = Depends(get_db),
):
    return document_service.get_detail(db, user, document_id)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: int,
    payload: DocumentUpdate,
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(
        document_service.update(db, user, document_id, payload))


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    user: User = Depends(require_perm("document:delete")),
    db: Session = Depends(get_db),
):
    document_service.delete(db, user, document_id)
    return {"ok": True}


@router.post("/{document_id}/copy", response_model=DocumentOut)
def copy_document(
    document_id: int,
    user: User = Depends(require_perm("document:create")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(document_service.copy(db, user, document_id))


@router.post("/{document_id}/submit", response_model=DocumentOut)
def submit_document(
    document_id: int,
    user: User = Depends(require_perm("document:submit")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(document_service.submit(db, user, document_id))


@router.post("/{document_id}/withdraw", response_model=DocumentOut)
def withdraw_document(
    document_id: int,
    user: User = Depends(require_perm("document:withdraw")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(document_service.withdraw(db, user, document_id))


@router.post("/{document_id}/void", response_model=DocumentOut)
def void_document(
    document_id: int,
    user: User = Depends(require_perm("document:void")),
    db: Session = Depends(get_db),
):
    return DocumentOut.model_validate(document_service.void(db, user, document_id))


@router.post("/{document_id}/line-items", response_model=dict)
def add_line_item(
    document_id: int,
    payload: LineItemCreate,
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    item = document_service.add_line_item(db, user, document_id, payload)
    return {"id": item.id}


@router.patch("/{document_id}/line-items/{line_item_id}", response_model=dict)
def update_line_item(
    document_id: int,
    line_item_id: int,
    payload: LineItemUpdate,
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    item = document_service.update_line_item(db, user, document_id, line_item_id, payload)
    return {"id": item.id}


@router.delete("/{document_id}/line-items/{line_item_id}")
def delete_line_item(
    document_id: int,
    line_item_id: int,
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    document_service.delete_line_item(db, user, document_id, line_item_id)
    return {"ok": True}


@router.get("/{document_id}/amount-comparison")
def amount_comparison(
    document_id: int,
    user: User = Depends(require_perm("document:view")),
    db: Session = Depends(get_db),
):
    return analysis_service.compare_amounts(db, user, document_id)


@router.post("/{document_id}/analysis")
def create_analysis(
    document_id: int,
    user: User = Depends(require_perm("analysis:create")),
    db: Session = Depends(get_db),
):
    """创建风险分析任务（对话/审批页"发起分析"入口）。
    P1-8：若当前已有 queued/运行中任务则复用，防止重复执行。"""
    document_service.ensure_viewable(db, user, document_id)
    task, created = analysis_service.create_or_get_task(db, document_id)
    db.commit()
    if created:
        analysis_service.enqueue(task.id)
    return {"task_id": task.id, "task_status": task.task_status, "created": created}


@router.get("/{document_id}/analysis/latest")
def get_latest_analysis(
    document_id: int,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    """风险 Tab 默认加载：当前文档最新分析任务 + 报告（P1-8，不自动新建）。"""
    return analysis_service.latest_for_document(db, user, document_id)
