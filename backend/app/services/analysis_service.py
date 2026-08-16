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


def compare_amounts(db: Session, user: User, doc_id: int) -> AmountComparisonOut:
    """金额核对面板：单据/明细/发票/合同/付款金额对照（规格 2.7.13）。"""
    doc = db.get(FinancialDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "单据不存在")
    ids = visible_document_ids(db, user)
    if ids is not None and doc.id not in ids:
        raise HTTPException(403, "无权访问该单据")

    line_items_total = db.scalar(
        select(func.coalesce(func.sum(DocumentLineItem.amount), 0)).where(
            DocumentLineItem.document_id == doc.id)
    ) or Decimal(0)

    # 发票合计：单据附件关联的发票含税金额之和
    invoice_total = db.scalar(
        select(func.coalesce(func.sum(InvoiceRecord.amount_including_tax), 0))
        .join(DocumentAttachment, DocumentAttachment.id == InvoiceRecord.attachment_id)
        .where(DocumentAttachment.document_id == doc.id)
    ) or Decimal(0)

    # 合同金额：附件解析结果里 document_category=contract 的 fields.contract_amount
    contract_amount: Decimal | None = None
    parse_rows = db.execute(
        select(AttachmentParseResult.fields_json)
        .join(DocumentAttachment, DocumentAttachment.id == AttachmentParseResult.attachment_id)
        .where(
            DocumentAttachment.document_id == doc.id,
            AttachmentParseResult.document_category == "contract",
        )
    ).scalars().all()
    for fields in parse_rows:
        if fields and fields.get("contract_amount") is not None:
            contract_amount = Decimal(str(fields["contract_amount"]))
            break

    payment_amount = doc.total_amount
    if doc.document_type == "batch_payment":
        payment_amount = db.scalar(
            select(func.coalesce(func.sum(DocumentLineItem.amount), 0)).where(
                DocumentLineItem.document_id == doc.id,
                DocumentLineItem.item_type == "payment",
            )
        ) or Decimal(0)

    differences = {
        "document_minus_line_items": (doc.total_amount - line_items_total),
        "document_minus_invoice": (doc.total_amount - invoice_total),
        "document_minus_contract": (
            (doc.total_amount - contract_amount) if contract_amount is not None else None
        ),
        "document_minus_payment": (doc.total_amount - payment_amount),
    }
    return AmountComparisonOut(
        document_total=doc.total_amount,
        line_items_total=line_items_total,
        invoice_total=invoice_total,
        contract_amount=contract_amount,
        payment_amount=payment_amount,
        differences=differences,
    )


def get_status(db: Session, user: User, task_id: int) -> dict:
    task = db.get(AnalysisTask, task_id)
    if task is None:
        raise HTTPException(404, "分析任务不存在")
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


async def run_pipeline(task_id: int) -> None:
    """分析流水线：querying_document → loading_attachments → parsing_attachments → analyzing → succeeded。"""
    from app.db.session import SessionLocal
    from app.models.analysis import RiskFinding
    from app.models.attachment import DocumentAttachment
    from app.models.document import FinancialDocument
    from app.services import parse_pipeline, report_service, rule_engine

    db = SessionLocal()
    try:
        task = db.get(AnalysisTask, task_id)
        if task is None:
            return
        mark_started(db, task)
        _stage(db, task, "querying_document")

        doc = db.get(FinancialDocument, task.document_id)
        if doc is None:
            mark_failed(db, task, "单据不存在")
            db.commit()
            return

        _stage(db, task, "loading_attachments")
        attachments = list(db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == doc.id)).all())

        _stage(db, task, "parsing_attachments")
        for att in attachments:
            if att.parse_status != "succeeded":
                await parse_pipeline.parse_attachment(db, att)
                db.commit()

        _stage(db, task, "analyzing")
        from app.services import sysparam_service
        ctx = rule_engine.build_context(db, doc)
        findings = rule_engine.run_all(ctx)
        overall = rule_engine.compute_overall_level(
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
    from app.models.analysis import RiskFinding
    _visible_task(db, user, task_id)
    rows = db.scalars(select(RiskFinding).where(
        RiskFinding.task_id == task_id)).all()
    return [{
        "id": f.id, "risk_type": f.risk_type, "risk_level": f.risk_level,
        "risk_title": f.risk_title, "description": f.description,
        "actual": f.actual_value_json, "reference": f.reference_value_json,
        "threshold": f.threshold_json, "evidence": f.evidence_json,
        "suggestion": f.suggestion_text, "review_status": f.review_status,
    } for f in sorted(rows, key=lambda x: {"low": 1, "medium": 2, "high": 3}[x.risk_level], reverse=True)]


def get_report(db: Session, user: User, task_id: int) -> dict:
    from app.models.analysis import ManualReview, ReviewReport
    task = _visible_task(db, user, task_id)
    report = db.scalar(select(ReviewReport).where(ReviewReport.task_id == task_id))
    if report is None:
        raise HTTPException(404, "报告尚未生成（任务可能仍在执行）")
    reviews = db.scalars(select(ManualReview).where(ManualReview.report_id == report.id)).all()
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
