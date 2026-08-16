# -*- coding: utf-8 -*-
"""报告路由：人工复核 / 导出 / 审核记录列表。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.analysis import ReviewReport
from app.models.document import FinancialDocument
from app.models.user import User
from app.services import report_service

router = APIRouter(prefix="/review-reports", tags=["reports"])


class ManualReviewIn(BaseModel):
    review_result: str   # approved / return / reject / manual
    review_comment: str


@router.get("")
def list_reports(
    page: int = 1,
    size: int = 20,
    user: User = Depends(require_perm("analysis:view")),
    db: Session = Depends(get_db),
):
    """审核记录页：历史报告列表。"""
    rows = db.scalars(
        select(ReviewReport).order_by(ReviewReport.id.desc()).offset((page - 1) * size).limit(size)
    ).all()
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
    if payload.review_result not in ("approved", "return", "reject", "manual"):
        raise HTTPException(400, "review_result 取值: approved/return/reject/manual")
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
    return HTMLResponse(report_service.export_html(report))
