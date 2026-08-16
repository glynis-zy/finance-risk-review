# -*- coding: utf-8 -*-
"""系统参数服务：读取/更新 sys_params（管理员维护，规格 2.7.3）。

设计原则：参数集中管理、可运行时修改，业务代码通过本服务读取，
避免把阈值/开关硬编码进各规则。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reference import SysParam
from app.models.user import User

# 默认值（seed 未灌入时兜底）
DEFAULTS: dict[str, tuple[str, str]] = {
    "risk.medium_bump_count": ("3", "整体风险升级：medium 数量达到该值则升 high"),
    "risk.low_bump_count": ("5", "整体风险升级：low 数量达到该值则升 medium"),
    "attachment.max_size_mb": ("10", "附件大小上限（MB）"),
    "attachment.confidence_threshold": ("0.8", "OCR 置信度阈值（附件完整性规则）"),
    "ocr.mode": ("auto", "real=真实OCR/LLM失败即失败；auto=真实→失败回退预制；preset=仅预制不调外部API"),
}


def get(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.scalar(select(SysParam).where(SysParam.param_key == key))
    if row is not None:
        return row.param_value
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    return default


def get_int(db: Session, key: str, default: int) -> int:
    val = get(db, key)
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def all(db: Session) -> list[dict]:
    rows = db.execute(select(SysParam)).scalars().all()
    seen = {r.param_key for r in rows}
    merged = {}
    for key, (val, desc) in DEFAULTS.items():
        merged[key] = {"param_key": key, "param_value": val, "description": desc}
    for r in rows:
        merged[r.param_key] = {"param_key": r.param_key, "param_value": r.param_value,
                               "description": r.description or ""}
    return [merged[k] for k in DEFAULTS if k in merged] + [
        m for k, m in merged.items() if k not in DEFAULTS]


def set_value(db: Session, key: str, value: str, user: User) -> SysParam:
    row = db.scalar(select(SysParam).where(SysParam.param_key == key))
    if row is None:
        row = SysParam(param_key=key, param_value=value, description=DEFAULTS.get(key, ("", ""))[1])
        db.add(row)
    row.param_value = value
    row.updated_by = user.id
    db.commit()
    db.refresh(row)
    return row
