# -*- coding: utf-8 -*-
"""分析流水线测试：P0-4 规则异常禁止静默跳过 → 任务 failed，不生成 succeeded 报告。"""
import asyncio
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401  注册模型
from app.domain import risk_engine
from app.models.analysis import AnalysisTask, ReviewReport
from app.models.document import FinancialDocument
from app.services import analysis_service


def test_run_all_propagates_rule_exception(db, monkeypatch):
    """run_all 不再吞异常：异常带 rule_code 抛出。"""
    doc = FinancialDocument(
        document_type="expense", document_no="T", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X",
        payee_account="Y", expense_category="差旅", total_amount=Decimal("100"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status="pending_review",
        current_version=1,
    )
    db.add(doc)
    db.commit()
    ctx = risk_engine.build_context(db, doc)

    def boom(ctx, cfg):
        raise ValueError("boom-error")

    monkeypatch.setattr("app.domain.risk_engine.engine.REGISTRY", [("boom_rule", boom)])
    with pytest.raises(RuntimeError, match="boom_rule"):
        risk_engine.run_all(ctx)


def test_rule_exception_marks_analysis_failed(tmp_path, monkeypatch):
    """规则异常 → analysis_task = failed、current_step 保留 analyzing、不生成报告。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'analysis.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.session.SessionLocal", TestSession)

    db = TestSession()
    doc = FinancialDocument(
        document_type="expense", document_no="T-FAIL", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X",
        payee_account="Y", expense_category="差旅", total_amount=Decimal("100"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status="pending_review",
        current_version=1,
    )
    db.add(doc)
    db.flush()
    task = AnalysisTask(document_id=doc.id, task_status="queued")
    db.add(task)
    db.commit()

    def boom(ctx, cfg):
        raise ValueError("boom-error")
    monkeypatch.setattr("app.domain.risk_engine.engine.REGISTRY", [("boom_rule", boom)])

    asyncio.run(analysis_service.run_pipeline(task.id))

    db2 = TestSession()
    t = db2.get(AnalysisTask, task.id)
    assert t.task_status == "failed"
    assert "boom_rule" in t.error_message          # error_message 明确是哪条规则
    assert t.current_step == "analyzing"           # current_step 保留在 analyzing
    assert db2.query(ReviewReport).filter_by(task_id=task.id).first() is None  # 不生成 succeeded 报告


def test_create_or_get_task_reuses_running(db):
    """P1-8：已有 queued/运行中任务时，create_or_get_task 复用而非新建（防重复执行）。"""
    doc = FinancialDocument(
        document_type="expense", document_no="T-REUSE", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X",
        payee_account="Y", expense_category="差旅", total_amount=Decimal("100"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status="pending_review",
        current_version=1,
    )
    db.add(doc)
    db.commit()

    t1, created1 = analysis_service.create_or_get_task(db, doc.id)
    t2, created2 = analysis_service.create_or_get_task(db, doc.id)   # 已有 queued 任务
    assert created1 is True
    assert created2 is False
    assert t2.id == t1.id
