# -*- coding: utf-8 -*-
"""规则引擎：10 条规则注册表 + 确定性判定 + 整体风险等级。

设计口径（用户确认 + 架构文档 §10 / §8）：
- 每条规则 = 一个纯函数 check_xxx(ctx)，只读数据与配置，无随机、无模型调用；
- 风险结论永远由本引擎决定，LLM 只做报告润色；
- 整体风险 = 最高单项 + 数量升级（有 high→high；medium≥3→high；low≥5→medium）。
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.document_schemas import REQUIRED_ATTACHMENTS
from app.models.analysis import RiskFinding
from app.models.attachment import (
    AttachmentParseResult,
    DocumentAttachment,
    InvoiceRecord,
)
from app.models.document import DocumentLineItem, FinancialDocument
from app.models.reference import ExpenseStandard, MarketPriceReference, RiskRule, SupplierProfile

LEVEL_SCORE = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Finding:
    risk_type: str
    risk_level: str
    risk_title: str
    description: str
    actual: dict | None = None
    reference: dict | None = None
    threshold: dict | None = None
    evidence: dict | None = None
    suggestion: str | None = None


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

    configs = load_configs(db)
    standards = list(db.scalars(select(ExpenseStandard)).all())
    price_refs = list(db.scalars(select(MarketPriceReference)).all())
    return RuleContext(
        db=db, document=document, line_items=line_items, attachments=attachments,
        invoices=invoices, parse_results=parse_results, supplier=supplier,
        history=history, configs=configs, standards=standards, price_refs=price_refs,
    )


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


DEFAULT_CONFIGS: dict[str, dict] = {
    "invoice_amount_consistency": {"tolerance_pct": 0.5},
    "line_items_total": {"tolerance_pct": 0.5},
    "contract_payment_consistency": {"tolerance_pct": 0.5, "ratio_gap": 10},
    "batch_payment_consistency": {"tolerance_pct": 0.5},
    "expense_policy_compliance": {"exceed_pct": 20},
    "price_reasonableness": {"deviation_pct": 20},
    "spend_anomaly": {"history_spike_ratio": 3.0},
    "supplier_risk": {},
    "attachment_completeness": {"confidence_threshold": 0.8},
    "duplicate_invoice": {},
}


def run_all(ctx: RuleContext) -> list[Finding]:
    """按注册顺序跑全部启用的规则，返回风险项列表。"""
    findings: list[Finding] = []
    for code, check in REGISTRY:
        cfg = ctx.configs.get(code, {})
        if not cfg.get("enabled", True):
            continue
        types = cfg.get("document_types")
        if types and ctx.document.document_type not in types:
            continue
        try:
            findings.extend(check(ctx, cfg))
        except Exception:  # noqa: BLE001  单规则失败不影响整体
            continue
    return findings


# ---------- 整体风险等级（D2，已确认） ----------

def compute_overall_level(findings: list[Finding],
                          medium_bump: int = 3, low_bump: int = 5) -> str:
    """整体风险 = 最高单项 + 数量升级（D2）。

    升级阈值来自 sys_params（risk.medium_bump_count / risk.low_bump_count），
    管理员可运行时调整，纯函数保证确定性。
    """
    if not findings:
        return "low"
    levels = [f.risk_level for f in findings]
    n = lambda lv: sum(1 for x in levels if x == lv)  # noqa: E731
    if n("high") >= 1:
        return "high"
    if n("medium") >= medium_bump:
        return "high"
    if n("low") >= low_bump:
        return "medium"
    return max(levels, key=lambda x: LEVEL_SCORE[x])


# ---------- 规则 1：单据与发票金额一致性 ----------

def check_invoice_amount(ctx: RuleContext, cfg: dict) -> list[Finding]:
    invoices = [i for i in ctx.invoices if i.amount_including_tax is not None]
    if not invoices:
        return []
    doc_total = ctx.document.total_amount
    inv_total = sum(i.amount_including_tax for i in invoices)
    if inv_total == 0:
        return []
    diff = abs(doc_total - inv_total)
    ratio = diff / inv_total * 100
    tol = Decimal(str(cfg.get("tolerance_pct", 0.5)))
    level = "high" if ratio > tol * 4 else ("medium" if ratio > tol else None)
    if level is None:
        return []
    return [Finding(
        risk_type="invoice_amount_consistency", risk_level=level,
        risk_title="单据金额与发票金额不一致",
        description=f"单据申请金额 {doc_total} 与发票含税合计 {inv_total} 差异 {diff}（{ratio:.2f}%），超过容差 {tol}%",
        actual={"document_total": str(doc_total), "invoice_total": str(inv_total)},
        reference={"invoice_total": str(inv_total)},
        threshold={"tolerance_pct": str(tol)},
        evidence={"invoice_nos": [i.invoice_no for i in invoices if i.invoice_no]},
        suggestion="核对发票金额与申请金额，补充差异说明或更正单据",
    )]


# ---------- 规则 2：明细与总金额一致性 ----------

def check_line_items_total(ctx: RuleContext, cfg: dict) -> list[Finding]:
    items = ctx.line_items
    if not items:
        return []
    tol = Decimal(str(cfg.get("tolerance_pct", 0.5)))
    sum_items = sum(i.amount for i in items)
    diff = abs(ctx.document.total_amount - sum_items)
    findings: list[Finding] = []
    if diff > Decimal("0.01") and (ctx.document.total_amount != 0 and diff / ctx.document.total_amount * 100 > tol):
        findings.append(Finding(
            risk_type="line_items_total", risk_level="medium",
            risk_title="明细合计与总金额不一致",
            description=f"明细合计 {sum_items} 与单据总金额 {ctx.document.total_amount} 差异 {diff}",
            actual={"line_items_total": str(sum_items)},
            reference={"document_total": str(ctx.document.total_amount)},
            threshold={"tolerance_pct": str(tol)},
            suggestion="检查是否有明细漏录或多录",
        ))
    # 重复明细（仅费用类，付款明细的重复由 batch_payment 规则管）
    seen = {}
    for i in items:
        if i.item_type == "payment":
            continue
        key = (i.item_name, str(i.amount))
        seen[key] = seen.get(key, 0) + 1
    dup = {k: v for k, v in seen.items() if v > 1}
    if dup:
        findings.append(Finding(
            risk_type="line_items_total", risk_level="low",
            risk_title="存在重复明细",
            description=f"以下明细出现多次: {', '.join(k[0] for k in dup)}",
            actual={"duplicates": {k[0]: v for k, v in dup.items()}},
            suggestion="核对是否重复报销",
        ))
    return findings


# ---------- 规则 3：合同与付款一致性 ----------

def check_contract_payment(ctx: RuleContext, cfg: dict) -> list[Finding]:
    if ctx.document.document_type not in ("company_payment", "advance_payment"):
        return []
    contract = None
    for r in ctx.parse_results:
        if r.document_category == "contract" and r.fields_json and "error" not in r.fields_json:
            contract = r.fields_json
            break
    if not contract:
        return []
    findings: list[Finding] = []
    tol = Decimal(str(cfg.get("tolerance_pct", 0.5)))

    if contract.get("contract_amount") is not None:
        contract_amount = Decimal(str(contract["contract_amount"]))
        payment = ctx.document.total_amount
        if payment > contract_amount:
            gap = (payment - contract_amount) / contract_amount * 100
            level = "high" if gap > Decimal(str(cfg.get("ratio_gap", 10))) else "medium"
            findings.append(Finding(
                risk_type="contract_payment_consistency", risk_level=level,
                risk_title="付款金额超过合同金额",
                description=f"本次付款 {payment} 超过合同金额 {contract_amount}（超出 {gap:.1f}%）",
                actual={"payment_amount": str(payment)},
                reference={"contract_amount": str(contract_amount)},
                threshold={"ratio_gap_pct": cfg.get("ratio_gap")},
                evidence={"contract_no": contract.get("contract_no")},
                suggestion="确认是否分次付款或合同增补",
            ))

    tf = ctx.document.type_fields_json or {}
    if contract.get("payment_ratio") is not None and tf.get("payment_ratio") is not None:
        doc_ratio = Decimal(str(tf["payment_ratio"]))
        ctr_ratio = Decimal(str(contract["payment_ratio"]))
        if abs(doc_ratio - ctr_ratio) > 5:
            findings.append(Finding(
                risk_type="contract_payment_consistency", risk_level="medium",
                risk_title="付款比例与合同约定不符",
                description=f"单据付款比例 {doc_ratio}% 与合同约定 {ctr_ratio}% 差异超过 5%",
                actual={"document_ratio": str(doc_ratio)},
                reference={"contract_ratio": str(ctr_ratio)},
                threshold={"ratio_gap_pct": 5},
                suggestion="核对付款条件是否达成",
            ))
    return findings


# ---------- 规则 4：批量付款一致性 ----------

def check_batch_payment(ctx: RuleContext, cfg: dict) -> list[Finding]:
    if ctx.document.document_type != "batch_payment":
        return []
    pay_items = [i for i in ctx.line_items if i.item_type == "payment"]
    if not pay_items:
        return []
    tol = Decimal(str(cfg.get("tolerance_pct", 0.5)))
    findings: list[Finding] = []
    sum_pay = sum(i.amount for i in pay_items)
    if abs(sum_pay - ctx.document.total_amount) / ctx.document.total_amount * 100 > tol:
        findings.append(Finding(
            risk_type="batch_payment_consistency", risk_level="medium",
            risk_title="批量付款笔数金额合计与批次总额不符",
            description=f"{len(pay_items)} 笔付款合计 {sum_pay} 与批次总金额 {ctx.document.total_amount} 不一致",
            actual={"sum_payments": str(sum_pay), "count": len(pay_items)},
            reference={"batch_total": str(ctx.document.total_amount)},
            threshold={"tolerance_pct": str(tol)},
            suggestion="核对各笔付款金额与批次总额",
        ))
    # 重复收款账号
    seen = {}
    for i in pay_items:
        acct = i.remark or i.item_name
        seen[acct] = seen.get(acct, 0) + 1
    dup = [k for k, v in seen.items() if v > 1]
    if dup:
        findings.append(Finding(
            risk_type="batch_payment_consistency", risk_level="high",
            risk_title="批量付款存在重复收款账号",
            description=f"收款账号/对象出现多次: {', '.join(dup)}",
            actual={"duplicates": dup},
            suggestion="确认是否存在重复付款",
        ))
    return findings


# ---------- 规则 5：费用标准合规性 ----------

# 明细名称 → 费用科目（与 expense_standards.expense_category 对齐）
_EXPENSE_KEYWORDS: dict[str, list[str]] = {
    "住宿": ["酒店", "住宿", "民宿"],
    "餐饮": ["餐饮", "餐", "食堂", "饭"],
    "交通": ["交通", "机票", "车票", "高铁", "火车票", "打车"],
}


def _expense_category(item_name: str) -> str | None:
    for cat, kws in _EXPENSE_KEYWORDS.items():
        if any(k in item_name for k in kws):
            return cat
    return None


def check_expense_policy(ctx: RuleContext, cfg: dict) -> list[Finding]:
    if ctx.document.document_type not in ("expense", "travel"):
        return []
    findings: list[Finding] = []
    exceed_pct = Decimal(str(cfg.get("exceed_pct", 20)))
    for item in ctx.line_items:
        if item.item_type == "payment":
            continue
        cat = _expense_category(item.item_name)
        if cat is None:
            continue
        standards = [s for s in ctx.standards if s.expense_category == cat]
        if not standards:
            continue
        s = standards[0]
        if item.amount > s.standard_amount:
            ratio = (item.amount - s.standard_amount) / s.standard_amount * 100
            if ratio > exceed_pct:
                findings.append(Finding(
                    risk_type="expense_policy_compliance", risk_level="medium",
                    risk_title="费用超出标准",
                    description=f"{item.item_name} {item.amount} 超过标准 {s.standard_amount}（超 {ratio:.1f}%）",
                    actual={"amount": str(item.amount)},
                    reference={"standard_amount": str(s.standard_amount)},
                    threshold={"exceed_pct": str(exceed_pct)},
                    suggestion="补充超标说明或申请特批",
                ))
    return findings


# ---------- 规则 6：市场价格合理性 ----------

def check_price(ctx: RuleContext, cfg: dict) -> list[Finding]:
    findings: list[Finding] = []
    deviation_pct = Decimal(str(cfg.get("deviation_pct", 20)))
    for item in ctx.line_items:
        if item.unit_price is None:
            continue
        refs = [r for r in ctx.price_refs if r.item_name in item.item_name or item.item_name in r.item_name]
        if not refs:
            continue
        ref = refs[0]
        price = item.unit_price
        dev = 0
        if price < ref.price_min:
            dev = (ref.price_min - price) / ref.price_min * 100
        elif price > ref.price_max:
            dev = (price - ref.price_max) / ref.price_max * 100
        if dev > deviation_pct:
            findings.append(Finding(
                risk_type="price_reasonableness", risk_level="medium" if dev <= 50 else "high",
                risk_title="价格偏离市场区间",
                description=f"{item.item_name} 单价 {price} 偏离市场区间 [{ref.price_min}, {ref.price_max}] {dev:.1f}%",
                actual={"unit_price": str(price)},
                reference={"price_min": str(ref.price_min), "price_max": str(ref.price_max)},
                threshold={"deviation_pct": str(deviation_pct)},
                evidence={"source": ref.source_name},
                suggestion="核对采购价格合理性",
            ))
    return findings


# ---------- 规则 7：消费行为异常 ----------

def check_spend_anomaly(ctx: RuleContext, cfg: dict) -> list[Finding]:
    findings: list[Finding] = []
    items = [i for i in ctx.line_items if i.expense_date is not None]
    if not items:
        return []
    # 同日重复报销：同一天同一项目重复出现（如两笔"商务酒店"）；或单日项目数≥4 视为可疑
    from collections import Counter
    key_count = Counter((i.expense_date, i.item_name) for i in items)
    dup_keys = [f"{d} {name}" for (d, name), c in key_count.items() if c > 1]
    if dup_keys:
        findings.append(Finding(
            risk_type="spend_anomaly", risk_level="medium",
            risk_title="存在同日重复报销",
            description=f"同一天同一项目重复出现: {', '.join(dup_keys[:5])}",
            actual={"duplicates": dup_keys[:5]},
            suggestion="核对同日多笔消费是否真实",
        ))
    date_count = Counter(i.expense_date for i in items)
    dense = [str(d) for d, c in date_count.items() if c >= 4]
    if dense:
        findings.append(Finding(
            risk_type="spend_anomaly", risk_level="low",
            risk_title="单日报销项目密集",
            description=f"以下日期单日报销项目较多: {', '.join(dense)}",
            actual={"dates": dense},
            suggestion="核对单日多笔消费的真实性",
        ))
    # 周末/节假日消费
    weekend = [str(i.expense_date) for i in items if i.expense_date.weekday() >= 5]
    if weekend:
        findings.append(Finding(
            risk_type="spend_anomaly", risk_level="low",
            risk_title="存在周末消费",
            description=f"周末消费日期: {', '.join(weekend[:5])}",
            actual={"weekend_dates": weekend[:5]},
            suggestion="核对该消费是否与出差相关",
        ))
    # 历史金额突增
    if ctx.history:
        avg_prev = sum(h.total_amount for h in ctx.history) / len(ctx.history)
        ratio_cfg = Decimal(str(cfg.get("history_spike_ratio", 3.0)))
        if avg_prev > 0 and ctx.document.total_amount > avg_prev * ratio_cfg:
            findings.append(Finding(
                risk_type="spend_anomaly", risk_level="medium",
                risk_title="报销金额较历史显著突增",
                description=f"本单 {ctx.document.total_amount} 为申请人历史均值 {avg_prev:.2f} 的 {ctx.document.total_amount / avg_prev:.1f} 倍",
                actual={"amount": str(ctx.document.total_amount)},
                reference={"historical_avg": f"{avg_prev:.2f}"},
                threshold={"ratio": str(ratio_cfg)},
                suggestion="核实大额报销的真实性与合规性",
            ))
    return findings


# ---------- 规则 8：供应商风险 ----------

def check_supplier(ctx: RuleContext, cfg: dict) -> list[Finding]:
    supplier = ctx.supplier
    if supplier is None:
        return []
    findings: list[Finding] = []
    if supplier.blacklist_status == "blacklisted":
        findings.append(Finding(
            risk_type="supplier_risk", risk_level="high",
            risk_title="供应商在黑名单",
            description=f"供应商 {supplier.supplier_name} 处于黑名单",
            actual={"blacklist_status": supplier.blacklist_status},
            evidence={"supplier_code": supplier.supplier_code},
            suggestion="禁止付款，转人工处理",
        ))
    if supplier.credit_status == "abnormal":
        findings.append(Finding(
            risk_type="supplier_risk", risk_level="medium",
            risk_title="供应商资质异常",
            description=f"供应商 {supplier.supplier_name} 资质状态异常",
            actual={"credit_status": supplier.credit_status},
            suggestion="核实供应商资质",
        ))
    tags = supplier.risk_tags_json or {}
    if tags.get("tags"):
        findings.append(Finding(
            risk_type="supplier_risk", risk_level="medium",
            risk_title="供应商存在风险标签",
            description=f"风险标签: {', '.join(tags['tags'])}",
            actual={"tags": tags["tags"]},
            suggestion="结合标签评估交易风险",
        ))
    # 收款账号变更/与档案不符
    accounts = supplier.bank_accounts_json or {}
    known = [a.get("account") for a in accounts.get("accounts", []) if a.get("account")]
    if known and ctx.document.payee_account not in known:
        findings.append(Finding(
            risk_type="supplier_risk", risk_level="medium",
            risk_title="收款账号与供应商档案不符",
            description=f"单据收款账号 {ctx.document.payee_account} 不在供应商档案账号列表中",
            actual={"payee_account": ctx.document.payee_account},
            reference={"known_accounts": known},
            suggestion="核实账号变更真实性，防止资金诈骗",
        ))
    return findings


# ---------- 规则 9：附件完整性（含 OCR 置信度代理） ----------

def check_attachment(ctx: RuleContext, cfg: dict) -> list[Finding]:
    required = REQUIRED_ATTACHMENTS.get(ctx.document.document_type, [])

    def _ok(r: AttachmentParseResult) -> bool:
        # fields_json={} 表示"已解析但无字段"，仍是有效解析；只有 error 才是失败
        return r.fields_json is not None and "error" not in (r.fields_json or {})

    ok_cats = {r.document_category for r in ctx.parse_results if _ok(r)}
    missing = [c for c in required if c not in ok_cats]
    findings: list[Finding] = []
    if missing:
        findings.append(Finding(
            risk_type="attachment_completeness", risk_level="high" if "contract" in missing else "medium",
            risk_title="缺少必需附件",
            description=f"缺少附件类别: {', '.join(missing)}",
            actual={"missing": missing},
            reference={"required": required},
            suggestion="补充对应附件后重新解析",
        ))
    threshold = Decimal(str(cfg.get("confidence_threshold", 0.8)))
    low_conf = [
        {"file": r.attachment_id, "conf": float(r.confidence)}
        for r in ctx.parse_results
        if r.confidence is not None and r.confidence < threshold
    ]
    if low_conf:
        findings.append(Finding(
            risk_type="attachment_completeness", risk_level="medium",
            risk_title="附件解析置信度偏低",
            description="OCR 置信度低于阈值，字段提取可能不准",
            actual={"low_confidence": low_conf},
            threshold={"confidence_threshold": str(threshold)},
            suggestion="人工复核附件识别结果",
        ))
    return findings


# ---------- 规则 10：重复票据风险 ----------

def check_duplicate_invoice(ctx: RuleContext, cfg: dict) -> list[Finding]:
    findings: list[Finding] = []
    for inv in ctx.invoices:
        if not inv.invoice_no:
            continue
        other = ctx.db.scalar(select(InvoiceRecord).where(
            InvoiceRecord.invoice_no == inv.invoice_no,
            InvoiceRecord.invoice_code == inv.invoice_code,
            InvoiceRecord.id != inv.id,
        ))
        if other:
            findings.append(Finding(
                risk_type="duplicate_invoice", risk_level="high",
                risk_title="发票重复提交",
                description=f"发票号码 {inv.invoice_no} 已在其他单据中使用",
                actual={"invoice_no": inv.invoice_no, "invoice_code": inv.invoice_code},
                reference={"other_record_id": other.id},
                suggestion="确认是否重复报销",
            ))
    # 文件 hash 重复
    for att in ctx.attachments:
        dup_att = ctx.db.scalar(select(DocumentAttachment).where(
            DocumentAttachment.file_hash == att.file_hash,
            DocumentAttachment.id != att.id,
        ))
        if dup_att:
            findings.append(Finding(
                risk_type="duplicate_invoice", risk_level="medium",
                risk_title="同一文件重复上传",
                description=f"文件 {att.file_name} 与其他单据附件内容相同",
                actual={"file_hash": att.file_hash},
                evidence={"other_attachment_id": dup_att.id},
                suggestion="核对重复文件来源",
            ))
    return findings


# ---------- 注册表（顺序即报告顺序） ----------

REGISTRY: list[tuple[str, object]] = [
    ("invoice_amount_consistency", check_invoice_amount),
    ("line_items_total", check_line_items_total),
    ("contract_payment_consistency", check_contract_payment),
    ("batch_payment_consistency", check_batch_payment),
    ("expense_policy_compliance", check_expense_policy),
    ("price_reasonableness", check_price),
    ("spend_anomaly", check_spend_anomaly),
    ("supplier_risk", check_supplier),
    ("attachment_completeness", check_attachment),
    ("duplicate_invoice", check_duplicate_invoice),
]
