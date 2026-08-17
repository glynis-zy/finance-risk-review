# -*- coding: utf-8 -*-
"""规则上下文：RuleContext + build_context + load_configs（数据装配）。"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.models.document import DocumentLineItem, FinancialDocument
from app.models.reference import (
    ExpenseStandard,
    MarketPriceReference,
    RiskRule,
    SupplierProfile,
)


@dataclass
class RuleContext:
    db: Session
    document: FinancialDocument
    line_items: list[DocumentLineItem]
    attachments: list[DocumentAttachment]
    invoices: list[InvoiceRecord]
    parse_results: list[AttachmentParseResult]
    supplier: SupplierProfile | None
    history: list[FinancialDocument]
    configs: dict[str, dict]
    standards: list[ExpenseStandard]
    price_refs: list[MarketPriceReference]
    position_level: str | None = None   # 申请人职级（费用标准规则维度）


DEFAULT_CONFIGS: dict[str, dict] = {
    "invoice_amount_consistency": {"tolerance_pct": 0.5},
    "line_items_total": {"tolerance_pct": 0.5},
    "contract_payment_consistency": {"tolerance_pct": 0.5, "ratio_gap": 10},
    "batch_payment_consistency": {"tolerance_pct": 0.5},
    "expense_policy_compliance": {"exceed_pct": 20},
    "price_reasonableness": {"deviation_pct": 20},
    "spend_anomaly": {"history_spike_ratio": 3.0},
    "supplier_risk": {},
    # confidence_threshold 由 sys_params（attachment.confidence_threshold）注入，见 build_context
    "attachment_completeness": {},
    "duplicate_invoice": {},
}


def load_configs(db: Session) -> dict[str, dict]:
    """读取 risk_rules 表配置；无记录用默认值。"""
    result = {}
    rows = db.execute(select(RiskRule)).scalars().all()
    for r in rows:
        cfg = dict(r.config_json or {})
        cfg["enabled"] = r.enabled
        if r.applies_to_json:
            cfg["document_types"] = r.applies_to_json.get("document_types")
        result[r.rule_code] = cfg
    for code, default in DEFAULT_CONFIGS.items():
        result.setdefault(code, dict(default))
    return result


def build_context(db: Session, document: FinancialDocument) -> RuleContext:
    """汇总单据、明细、附件、发票、解析结果、供应商、历史、配置、标准数据。"""
    line_items = list(db.scalars(select(DocumentLineItem).where(
        DocumentLineItem.document_id == document.id)).all())
    attachments = list(db.scalars(select(DocumentAttachment).where(
        DocumentAttachment.document_id == document.id)).all())
    att_ids = [a.id for a in attachments]
    invoices = []
    parse_results = []
    if att_ids:
        invoices = list(db.scalars(select(InvoiceRecord).where(
            InvoiceRecord.attachment_id.in_(att_ids))).all())
        parse_results = list(db.scalars(select(AttachmentParseResult).where(
            AttachmentParseResult.attachment_id.in_(att_ids))).all())

    # 供应商：对公/预付按 type_fields.supplier_name，其余按 payee_name
    supplier = None
    tf = document.type_fields_json or {}
    supplier_name = tf.get("supplier_name") or document.payee_name
    if supplier_name:
        supplier = db.scalar(select(SupplierProfile).where(
            SupplierProfile.supplier_name == supplier_name))

    # 申请人历史单据（排除当前），供消费异常/历史突增
    history = list(db.scalars(select(FinancialDocument).where(
        FinancialDocument.applicant_id == document.applicant_id,
        FinancialDocument.id != document.id,
        FinancialDocument.document_status.in_(["approved", "pending_review", "reviewing"]),
    )).all())

    # 申请人职级（费用标准规则维度，规格 2.7.7：类别×部门×职级×地区）
    from app.models.user import User
    applicant = db.get(User, document.applicant_id)
    position_level = applicant.position_level if applicant else None

    configs = load_configs(db)
    # P2-2：置信度阈值以 sys_params 为唯一权威来源（附件完整性规则）
    from app.services.sysparam_service import get as _get_param
    _conf = _get_param(db, "attachment.confidence_threshold")
    if _conf is not None:
        try:
            configs.setdefault("attachment_completeness", {})["confidence_threshold"] = float(_conf)
        except ValueError:
            pass
    standards = list(db.scalars(select(ExpenseStandard)).all())
    price_refs = list(db.scalars(select(MarketPriceReference)).all())
    return RuleContext(
        db=db, document=document, line_items=line_items, attachments=attachments,
        invoices=invoices, parse_results=parse_results, supplier=supplier,
        history=history, configs=configs, standards=standards, price_refs=price_refs,
        position_level=position_level,
    )
