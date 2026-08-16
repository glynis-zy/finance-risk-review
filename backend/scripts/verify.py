# -*- coding: utf-8 -*-
"""冒烟验证：打印每个分析任务的整体风险、建议与风险项。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.analysis import AnalysisTask, ReviewReport, RiskFinding
from app.models.document import FinancialDocument

db = SessionLocal()
for t in db.scalars(select(AnalysisTask)).all():
    doc = db.get(FinancialDocument, t.document_id)
    rep = db.scalar(select(ReviewReport).where(ReviewReport.task_id == t.id))
    findings = db.scalars(select(RiskFinding).where(RiskFinding.task_id == t.id)).all()
    levels = [f.risk_level for f in findings]
    overall = rep.overall_risk_level if rep else "?"
    rec = rep.recommendation if rep else "?"
    print(f"task{t.id} {doc.document_no} status={t.task_status} err={t.error_message} -> overall={overall} 建议={rec} findings={len(findings)} {levels}")
    for f in findings:
        print(f"    [{f.risk_level}] {f.risk_title}")
db.close()
