# -*- coding: utf-8 -*-
"""整体风险等级公式（D2）纯函数测试：最高单项 + 数量升级。"""
from app.domain.risk_engine import Finding, compute_overall_level


def _finding(level: str) -> Finding:
    return Finding(risk_type="x", risk_level=level, risk_title="t", description="d")


def test_empty_is_low():
    assert compute_overall_level([]) == "low"


def test_single_high_is_high():
    assert compute_overall_level([_finding("high")]) == "high"


def test_any_high_overrides():
    # 一个 high 即 high，不论其他项
    assert compute_overall_level([_finding("low"), _finding("medium"), _finding("high")]) == "high"


def test_medium_bump_threshold():
    # 默认：medium≥3 升 high；2 个 medium 仍为 medium
    assert compute_overall_level([_finding("medium"), _finding("medium")]) == "medium"
    assert compute_overall_level([_finding("medium")] * 3) == "high"


def test_low_bump_threshold():
    # 默认：low≥5 升 medium
    assert compute_overall_level([_finding("low")] * 4) == "low"
    assert compute_overall_level([_finding("low")] * 5) == "medium"


def test_custom_thresholds():
    # 升级阈值来自 sys_params，可调
    assert compute_overall_level([_finding("medium")] * 2, medium_bump=2) == "high"
    assert compute_overall_level([_finding("low")] * 3, low_bump=3) == "medium"


def test_mixed_returns_max_level():
    assert compute_overall_level([_finding("low"), _finding("low"), _finding("medium")]) == "medium"
