# -*- coding: utf-8 -*-
"""规则配置路由：risk_rules CRUD（财务人员维护）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.reference import RiskRule
from app.models.user import User
from app.services import audit_service

router = APIRouter(prefix="/rules", tags=["rules"])


class RuleIn(BaseModel):
    rule_code: str
    rule_name: str
    applies_to: dict | None = None      # {document_types: [...]}
    enabled: bool = True
    config: dict = Field(default_factory=dict)


@router.get("")
def list_rules(
    user: User = Depends(require_perm("rule:view")),
    db: Session = Depends(get_db),
):
    rows = db.scalars(select(RiskRule).order_by(RiskRule.id)).all()
    return [{
        "id": r.id, "rule_code": r.rule_code, "rule_name": r.rule_name,
        "applies_to": r.applies_to_json, "enabled": r.enabled, "config": r.config_json,
        "updated_at": str(r.updated_at),
    } for r in rows]


@router.post("")
def create_rule(
    payload: RuleIn,
    user: User = Depends(require_perm("rule:manage")),
    db: Session = Depends(get_db),
):
    if db.scalar(select(RiskRule).where(RiskRule.rule_code == payload.rule_code)):
        raise HTTPException(409, f"规则 {payload.rule_code} 已存在")
    rule = RiskRule(
        rule_code=payload.rule_code, rule_name=payload.rule_name,
        applies_to_json=payload.applies_to, enabled=payload.enabled,
        config_json=payload.config, updated_by=user.id,
    )
    db.add(rule)
    db.flush()
    audit_service.log(db, user, "rule:create", "risk_rule", str(rule.id), {"code": rule.rule_code})
    db.commit()
    return {"id": rule.id}


@router.patch("/{rule_id}")
def update_rule(
    rule_id: int,
    payload: RuleIn,
    user: User = Depends(require_perm("rule:manage")),
    db: Session = Depends(get_db),
):
    rule = db.get(RiskRule, rule_id)
    if rule is None:
        raise HTTPException(404, "规则不存在")
    rule.rule_name = payload.rule_name
    rule.applies_to_json = payload.applies_to
    rule.enabled = payload.enabled
    rule.config_json = payload.config
    rule.updated_by = user.id
    audit_service.log(db, user, "rule:update", "risk_rule", str(rule_id),
                      {"config": payload.config, "enabled": payload.enabled})
    db.commit()
    return {"id": rule.id}
