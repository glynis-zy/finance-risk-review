# -*- coding: utf-8 -*-
"""风险项复核状态：确认 / 排除（人工复核，规格 2.7.12）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.analysis import RiskFinding
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/risk-findings", tags=["risk-findings"])


class ReviewStatusIn(BaseModel):
    review_status: str   # confirmed / dismissed


@router.patch("/{finding_id}/review-status")
def update_finding_status(
    finding_id: int,
    payload: ReviewStatusIn,
    user: User = Depends(require_perm("analysis:review")),
    db: Session = Depends(get_db),
):
    if payload.review_status not in ("confirmed", "dismissed"):
        raise HTTPException(400, "review_status 取值: confirmed/dismissed")
    finding = db.get(RiskFinding, finding_id)
    if finding is None:
        raise HTTPException(404, "风险项不存在")
    finding.review_status = payload.review_status
    audit_service.log(db, user, "finding:review_status", "risk_finding",
                      str(finding_id), {"review_status": payload.review_status})
    db.commit()
    return {"id": finding.id, "review_status": finding.review_status}
