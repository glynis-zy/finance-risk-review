# -*- coding: utf-8 -*-
"""风险引擎数据模型。"""
from dataclasses import dataclass

LEVEL_SCORE = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Finding:
    """一条风险项（规则输出）。"""
    risk_type: str
    risk_level: str
    risk_title: str
    description: str
    actual: dict | None = None
    reference: dict | None = None
    threshold: dict | None = None
    evidence: dict | None = None
    suggestion: str | None = None
