# -*- coding: utf-8 -*-
"""报告路由：人工复核 / 导出 / 审核记录列表（全部带 L2 数据权限）。

- list/export：报告对应 document 必须在当前用户可见范围内；
- manual-reviews：admin 可操作；approver 仅能操作自己审批任务范围内的单据报告。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.core.scopes import approval_document_ids, get_role_codes, visible_document_ids
from app.models.analysis import ReviewReport
from app.models.document import FinancialDocument
from app.models.user import User
from app.services import report_service

router = APIRouter(prefix="/review-reports", tags=["reports"])


class ManualReviewIn(BaseModel):
    # P1-7：人工复核 ≠ 正式审批。枚举：confirmed / needs_material / escalated
    review_result: str
    review_comment: str


def _visible_ids(db: Session, user: User) -> list[int] | None:
    """用户可见 document id；None=全部可见。"""
    return visible_document_ids(db, user)


def _ensure_doc_visible(db: Session, user: User, doc_id: int) -> None:
    ids = _visible_ids(db, user)
    if ids is not None and doc_id not in ids:
        raise HTTPException(403, "无权访问该报告")


def _ensure_manual_review_allowed(db: Session, user: User, report: ReviewReport) -> None:
    """admin 可操作；approver 仅限自己审批任务范围内的单据报告。"""
    roles = get_role_codes(db, user.id)
    if "admin" in roles:
        return
    task_docs = approval_document_ids(db, user.id)
    if report.document_id not in task_docs:
        raise HTTPException(403, "只能复核自己审批范围内的单据报告")


@router.get("")
def list_reports(
    page: int = 1,
    size: int = 20,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    """审核记录页：历史报告列表（按数据权限过滤，张三看不到李四的报告）。"""
    ids = _visible_ids(db, user)
    stmt = select(ReviewReport)
    if ids is not None:
        stmt = stmt.where(ReviewReport.document_id.in_(ids))
    stmt = stmt.order_by(ReviewReport.id.desc()).offset((page - 1) * size).limit(size)
    rows = db.scalars(stmt).all()
    result = []
    for r in rows:
        doc = db.get(FinancialDocument, r.document_id)
        result.append({
            "report_id": r.id, "document_no": doc.document_no if doc else None,
            "overall_risk_level": r.overall_risk_level,
            "recommendation": r.recommendation,
            "created_at": str(r.created_at),
        })
    return result


@router.post("/{report_id}/manual-reviews")
def submit_manual_review(
    report_id: int,
    payload: ManualReviewIn,
    user: User = Depends(require_perm("approval:process")),
    db: Session = Depends(get_db),
):
    if payload.review_result not in ("confirmed", "needs_material", "escalated"):
        raise HTTPException(400, "review_result 取值: confirmed/needs_material/escalated")
    report = db.get(ReviewReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    _ensure_manual_review_allowed(db, user, report)
    review = report_service.submit_manual_review(
        db, user, report_id, payload.review_result, payload.review_comment)
    return {"id": review.id, "review_result": review.review_result}


@router.get("/{report_id}/export", response_class=HTMLResponse)
def export_report(
    report_id: int,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    """导出 HTML（浏览器可打印 PDF），规格 2.7.13 / D4。"""
    report = db.get(ReviewReport, report_id)
    if report is None:
        raise HTTPException(404, "报告不存在")
    _ensure_doc_visible(db, user, report.document_id)
    return HTMLResponse(report_service.export_html(report))
