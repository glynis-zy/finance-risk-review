# -*- coding: utf-8 -*-
"""规则引擎集成测试：在 SQLite 上验证 10 条规则中的代表性分支。

策略模式的可测试性体现：规则是纯函数，喂不同数据断言不同输出。
"""
from datetime import date
from decimal import Decimal

from app.models.attachment import DocumentAttachment, InvoiceRecord
from app.models.document import DocumentLineItem, FinancialDocument
from app.models.reference import SupplierProfile
from app.services import rule_engine
from app.services.sysparam_service import get_int


def _doc(db, total: str, doc_type: str = "expense", payee: str = "某公司",
         acct: str = "ACC1", type_fields: dict | None = None) -> FinancialDocument:
    d = FinancialDocument(
        document_type=doc_type, document_no="T-001", applicant_id=1,
        applicant_department="市场部", budget_department="市场部",
        payee_name=payee, payee_account=acct, expense_category="差旅",
        total_amount=Decimal(total), currency="CNY", apply_date=date(2026, 8, 1),
        document_status="pending_review", type_fields_json=type_fields or {},
    )
    db.add(d)
    db.flush()
    return d


def _run(db, doc):
    ctx = rule_engine.build_context(db, doc)
    return rule_engine.run_all(ctx)


def _types(findings):
    return {f.risk_type: f for f in findings}


def test_invoice_amount_mismatch(db):
    doc = _doc(db, total="50000")
    att = DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                             file_size=1, file_path="x.png", file_hash="h1",
                             storage_status="stored", parse_status="succeeded")
    db.add(att)
    db.flush()
    db.add(InvoiceRecord(attachment_id=att.id, invoice_code="1234", invoice_no="88880001",
                         amount_including_tax=Decimal("45000")))
    db.commit()
    findings = _run(db, doc)
    f = _types(findings).get("invoice_amount_consistency")
    assert f is not None and f.risk_level in ("medium", "high")


def test_invoice_amount_consistent_no_finding(db):
    doc = _doc(db, total="50000")
    att = DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                             file_size=1, file_path="x.png", file_hash="h1",
                             storage_status="stored", parse_status="succeeded")
    db.add(att)
    db.flush()
    db.add(InvoiceRecord(attachment_id=att.id, invoice_code="1234", invoice_no="88880001",
                         amount_including_tax=Decimal("50000")))
    db.commit()
    findings = _run(db, doc)
    assert "invoice_amount_consistency" not in _types(findings)


def test_supplier_blacklisted(db):
    doc = _doc(db, total="80000", doc_type="company_payment", payee="远东贸易有限公司",
               type_fields={"supplier_name": "远东贸易有限公司"})
    db.add(SupplierProfile(supplier_code="S1", supplier_name="远东贸易有限公司",
                           credit_status="normal", blacklist_status="blacklisted",
                           risk_tags_json={"tags": ["黑名单"]},
                           bank_accounts_json={"accounts": []}))
    db.commit()
    findings = _run(db, doc)
    sup = [f for f in findings if f.risk_type == "supplier_risk"]
    assert sup and any(f.risk_level == "high" and "黑名单" in f.risk_title for f in sup)


def test_batch_duplicate_account(db):
    doc = _doc(db, total="30000", doc_type="batch_payment", payee="批量")
    db.add(DocumentLineItem(document_id=doc.id, item_type="payment", item_name="A",
                            amount=Decimal("10000"), remark="ACC1"))
    db.add(DocumentLineItem(document_id=doc.id, item_type="payment", item_name="A",
                            amount=Decimal("10000"), remark="ACC1"))
    db.add(DocumentLineItem(document_id=doc.id, item_type="payment", item_name="B",
                            amount=Decimal("10000"), remark="ACC2"))
    db.commit()
    findings = _run(db, doc)
    f = _types(findings).get("batch_payment_consistency")
    assert f is not None and f.risk_level == "high"


def test_expense_over_standard(db):
    doc = _doc(db, total="1200")
    db.add(DocumentLineItem(document_id=doc.id, item_type="expense", item_name="商务酒店",
                            amount=Decimal("1200")))
    from app.models.reference import ExpenseStandard
    db.add(ExpenseStandard(expense_category="住宿", standard_amount=Decimal("600"),
                           currency="CNY", effective_date=date(2026, 1, 1)))
    db.commit()
    findings = _run(db, doc)
    f = _types(findings).get("expense_policy_compliance")
    assert f is not None and f.risk_level == "medium"


def test_missing_required_attachment(db):
    # 费用报销单要求发票，未解析任何附件 → 附件完整性 medium/high
    doc = _doc(db, total="500")
    db.commit()
    findings = _run(db, doc)
    f = _types(findings).get("attachment_completeness")
    assert f is not None and "必需附件" in f.risk_title


def test_sysparam_default(db):
    # 系统参数未灌入时返回内置默认
    assert get_int(db, "risk.medium_bump_count", 99) == 3
