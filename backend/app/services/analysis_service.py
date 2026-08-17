# -*- coding: utf-8 -*-
"""分析服务：任务创建 / 金额核对 / 状态查询。

流水线（parse→rules→report）在 rule_engine 就绪后于本文件补全：
run_pipeline(task_id) 走 asyncio 队列，前端轮询任务状态。
"""
import asyncio
import logging
import threading
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

logger = logging.getLogger(__name__)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.scopes import visible_document_ids
from app.models.analysis import AnalysisTask
from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.models.document import DocumentLineItem, FinancialDocument
from app.models.user import User
from app.schemas.document import AmountComparisonOut
from app.services import audit_service


def create_task(db: Session, document_id: int, session_id: int | None = None) -> AnalysisTask:
    """创建分析任务（提交时或对话/审批页发起分析时调用）。"""
    task = AnalysisTask(
        session_id=session_id,
        document_id=document_id,
        task_status="queued",
    )
    db.add(task)
    db.flush()
    return task


def _running_task(db: Session, document_id: int) -> AnalysisTask | None:
    """当前文档是否已有 queued/运行中的分析任务（P1-8 防重复）。"""
    from app.repositories.analysis_repo import AnalysisRepository
    return AnalysisRepository(db).running_task(document_id)


def create_or_get_task(db: Session, document_id: int,
                       session_id: int | None = None) -> tuple[AnalysisTask, bool]:
    """发起分析：已有运行中任务则复用（不新建），否则创建。返回 (task, 是否新建)。"""
    running = _running_task(db, document_id)
    if running is not None:
        return running, False
    return create_task(db, document_id, session_id), True


def latest_for_document(db: Session, user: User, document_id: int) -> dict:
    """当前文档最新分析任务 + 报告（风险 Tab 默认加载，P1-8）。"""
    from app.repositories.analysis_repo import AnalysisRepository
    ids = visible_document_ids(db, user)
    if ids is not None and document_id not in ids:
        raise HTTPException(403, "无权访问该单据")
    repo = AnalysisRepository(db)
    task = repo.latest_task(document_id)
    if task is None:
        return {"task_id": None, "task_status": None, "report": None}
    report = repo.report(task.id)
    return {
        "task_id": task.id,
        "task_status": task.task_status,
        "current_step": task.current_step,
        "report": _report_payload(report, task) if report is not None else None,
    }


def _report_payload(report, task) -> dict:
    """报告基础载荷（与 get_report 保持一致的结构）。"""
    return {
        "report_id": report.id,
        "task_id": report.task_id,
        "document_id": report.document_id,
        "overall_risk_level": report.overall_risk_level,
        "risk_summary": report.risk_summary_json,
        "amount_comparison": report.amount_comparison_json,
        "recommendation": report.recommendation,
        "report_markdown": report.report_markdown,
        "created_at": str(report.created_at),
    }


def compare_amounts(db: Session, user: User, doc_id: int) -> AmountComparisonOut:
    """金额核对面板（P1-13）：L2 校验后复用 amount_service 唯一计算。"""
    from app.services.amount_service import calculate_amount_comparison
    doc = db.get(FinancialDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "单据不存在")
    ids = visible_document_ids(db, user)
    if ids is not None and doc.id not in ids:
        raise HTTPException(403, "无权访问该单据")
    return calculate_amount_comparison(db, doc)


def get_status(db: Session, user: User, task_id: int) -> dict:
    _visible_task(db, user, task_id)  # L2：task 对应 document 必须对当前用户可见
    task = db.get(AnalysisTask, task_id)
    return {
        "task_id": task.id,
        "task_status": task.task_status,
        "current_step": task.current_step,
        "error_message": task.error_message,
        "finished_at": str(task.finished_at) if task.finished_at else None,
    }


def mark_started(db: Session, task: AnalysisTask) -> None:
    task.started_at = datetime.utcnow()


def _stage(db: Session, task: AnalysisTask, name: str) -> None:
    """推进分析任务阶段：task_status 对齐规格 2.7.12 枚举
    （queued → querying_document → loading_attachments → parsing_attachments
     → analyzing → succeeded/failed）。current_step 保留为镜像，兼容轮询前端。"""
    task.task_status = name
    task.current_step = name
    db.commit()


def mark_failed(db: Session, task: AnalysisTask, message: str) -> None:
    task.task_status = "failed"
    task.error_message = message
    task.finished_at = datetime.utcnow()
    audit_service.log(db, None, "analysis:failed", "analysis_task", str(task.id), {"error": message})


# ---------- 异步流水线（解析→规则→报告） ----------

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """后台线程里跑一个常驻事件循环，供同步路由入队分析任务。"""
    global _loop
    if _loop is None:
        with _loop_lock:
            if _loop is None:
                _loop = asyncio.new_event_loop()
                threading.Thread(
                    target=_loop.run_forever, daemon=True, name="analysis-loop"
                ).start()
    return _loop


def enqueue(task_id: int) -> None:
    """提交后把分析任务放入后台循环执行（状态可轮询，规格 2.7.12）。"""
    _get_loop().call_soon_threadsafe(lambda: asyncio.create_task(run_pipeline(task_id)))


def cancel_for_document(db: Session, document_id: int) -> None:
    """作废/撤回时取消该单据未完成的分析任务（P0-5）。"""
    for t in db.scalars(select(AnalysisTask).where(
            AnalysisTask.document_id == document_id,
            AnalysisTask.task_status.in_(
                ("queued", "querying_document", "loading_attachments",
                 "parsing_attachments", "analyzing"),
            ),
    )).all():
        t.task_status = "cancelled"
        t.finished_at = datetime.utcnow()


def _is_aborted(db: Session, task_id: int) -> bool:
    """合作式取消检查（P0-5）：任务被 cancelled 或单据被撤回/作废 → 停止流水线。"""
    db.expire_all()  # 读最新（取消来自其他会话）
    from app.models.document import FinancialDocument
    task = db.get(AnalysisTask, task_id)
    if task is None or task.task_status == "cancelled":
        return True
    doc = db.get(FinancialDocument, task.document_id)
    return doc is None or doc.document_status in ("withdrawn", "voided")


async def run_pipeline(task_id: int) -> None:
    """分析流水线：querying_document → loading_attachments → parsing_attachments → analyzing → succeeded。"""
    from app.db.session import SessionLocal
    from app.domain.risk_engine import build_context, compute_overall_level, run_all
    from app.models.analysis import RiskFinding
    from app.models.attachment import DocumentAttachment
    from app.models.document import FinancialDocument
    from app.services import parse_pipeline, report_service

    db = SessionLocal()
    try:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return
        if _is_aborted(db, task_id):  # 入口先查取消，避免 _stage 覆盖已 cancelled 状态
            return
        mark_started(db, task)
        _stage(db, task, "querying_document")
        if _is_aborted(db, task_id):
            return

        doc = db.get(FinancialDocument, task.document_id)
        if doc is None:
            mark_failed(db, task, "单据不存在")
            db.commit()
            return

        _stage(db, task, "loading_attachments")
        if _is_aborted(db, task_id):
            return
        attachments = list(db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == doc.id)).all())

        _stage(db, task, "parsing_attachments")
        if _is_aborted(db, task_id):
            return
        for att in attachments:
            if att.parse_status != "succeeded":
                await parse_pipeline.parse_attachment(db, att)
                db.commit()

        _stage(db, task, "analyzing")
        if _is_aborted(db, task_id):
            return
        from app.services import sysparam_service
        ctx = build_context(db, doc)
        findings = run_all(ctx)
        overall = compute_overall_level(
            findings,
            medium_bump=sysparam_service.get_int(db, "risk.medium_bump_count", 3),
            low_bump=sysparam_service.get_int(db, "risk.low_bump_count", 5),
        )
        for f in findings:
            db.add(RiskFinding(
                task_id=task.id, risk_type=f.risk_type, risk_level=f.risk_level,
                risk_title=f.risk_title, description=f.description,
                actual_value_json=f.actual, reference_value_json=f.reference,
                threshold_json=f.threshold, evidence_json=f.evidence,
                suggestion_text=f.suggestion,
            ))
        db.commit()

        if _is_aborted(db, task_id):  # 报告生成前最后一道取消检查（P0-5）
            return
        report_service.generate(db, task, doc, findings, overall)
        task.task_status = "succeeded"
        task.current_step = None
        task.finished_at = datetime.utcnow()
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("analysis pipeline failed: task=%s", task_id)
        try:
            db.rollback()
            t = db.get(AnalysisTask, task_id)
            if t:
                mark_failed(db, t, str(exc))
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()


# ---------- 风险项 / 报告查询（带 L2 权限） ----------

def _visible_task(db: Session, user: User, task_id: int) -> AnalysisTask:
    task = db.get(AnalysisTask, task_id)
    if task is None:
        raise HTTPException(404, "分析任务不存在")
    ids = visible_document_ids(db, user)
    if ids is not None and task.document_id not in ids:
        raise HTTPException(403, "无权访问该分析任务")
    return task


def get_findings(db: Session, user: User, task_id: int) -> list[dict]:
    from app.repositories.analysis_repo import AnalysisRepository
    _visible_task(db, user, task_id)
    rows = AnalysisRepository(db).findings(task_id)
    return [{
        "id": f.id, "risk_type": f.risk_type, "risk_level": f.risk_level,
        "risk_title": f.risk_title, "description": f.description,
        "actual": f.actual_value_json, "reference": f.reference_value_json,
        "threshold": f.threshold_json, "evidence": f.evidence_json,
        "suggestion": f.suggestion_text, "review_status": f.review_status,
    } for f in sorted(rows, key=lambda x: {"low": 1, "medium": 2, "high": 3}[x.risk_level], reverse=True)]


def get_report(db: Session, user: User, task_id: int) -> dict:
    from app.repositories.analysis_repo import AnalysisRepository
    task = _visible_task(db, user, task_id)
    repo = AnalysisRepository(db)
    report = repo.report(task_id)
    if report is None:
        raise HTTPException(404, "报告尚未生成（任务可能仍在执行）")
    reviews = repo.manual_reviews(report.id)
    return {
        "report_id": report.id,
        "task_id": report.task_id,
        "document_id": report.document_id,
        "overall_risk_level": report.overall_risk_level,
        "risk_summary": report.risk_summary_json,
        "amount_comparison": report.amount_comparison_json,
        "recommendation": report.recommendation,
        "report_markdown": report.report_markdown,
        "created_at": str(report.created_at),
        "manual_reviews": [{
            "reviewer_id": r.reviewer_id, "review_result": r.review_result,
            "review_comment": r.review_comment, "reviewed_at": str(r.reviewed_at),
        } for r in reviews],
    }
