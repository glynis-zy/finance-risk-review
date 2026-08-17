# -*- coding: utf-8 -*-
"""风险引擎（Domain 层）：确定性规则，无随机、无 LLM 判定。

公开 API：build_context / run_all / compute_overall_level / load_configs
类型：Finding（models.py）/ RuleContext（context.py）/ REGISTRY（engine.py）
"""
from app.domain.risk_engine.context import (
    RuleContext,
    build_context,
    load_configs,
)
from app.domain.risk_engine.engine import (
    REGISTRY,
    compute_overall_level,
    run_all,
)
from app.domain.risk_engine.models import Finding, LEVEL_SCORE

__all__ = [
    "Finding", "RuleContext", "LEVEL_SCORE", "REGISTRY",
    "build_context", "load_configs", "run_all", "compute_overall_level",
]
