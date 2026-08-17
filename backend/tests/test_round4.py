# -*- coding: utf-8 -*-
"""第四轮回归测试：审批人 Resolver / workflow priority / 状态机合法性 / 取消 / 复制 / 金额统一 / ManualReview。"""
import asyncio
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.domain import document_state
from app.models.analysis import AnalysisTask, ReviewReport
from app.models.attachment import DocumentAttachment, InvoiceRecord
from app.models.document import DocumentLineItem, DocumentVersion, FinancialDocument
from app.models.user import Permission, Role, RolePermission, User, UserRole
from app.models.workflow import (
    ApprovalInstance,
    ApprovalTask,
    ApprovalWorkflow,
    ApprovalWorkflowNode,
)
from app.services import analysis_service, amount_service, document_service, report_service, workflow_service


def _make_user(db, username, roles, perm_codes=()) -> User:
    """建用户：复用已存在的 Role/Permission，避免唯一约束冲突。"""
    u = User(username=username, display_name=username, password_hash="x")
    db.add(u)
    db.flush()
    for rc in roles:
        role = db.query(Role).filter(Role.role_code == rc).first()
        if role is None:
            role = Role(role_code=rc, role_name=rc)
            db.add(role)
            db.flush()
        for pc in perm_codes:
            perm = db.query(Permission).filter(Permission.permission_code == pc).first()
            if perm is None:
                perm = Permission(permission_code=pc, permission_name=pc,
                                  resource_type=pc.split(":")[0], action_type=pc.split(":")[-1])
                db.add(perm)
                db.flush()
            if db.query(RolePermission).filter_by(role_id=role.id, permission_id=perm.id).first() is None:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db.add(UserRole(user_id=u.id, role_id=role.id))
    return u


def _expense_doc(db, applicant, total="1000", doc_no="R4-EXP", status="draft") -> FinancialDocument:
    d = FinancialDocument(
        document_type="expense", document_no=doc_no, applicant_id=applicant.id,
        applicant_department="市场部", budget_department="市场部", payee_name="某公司",
        payee_account="A", expense_category="差旅", total_amount=Decimal(total),
        currency="CNY", apply_date=date(2026, 8, 1), document_status=status,
        current_version=0,
    )
    db.add(d)
    db.flush()
    return d


def _workflow(db, doc_type="expense", amount_min=0, priority=0, node_role="approver"):
    wf = ApprovalWorkflow(workflow_name=f"wf-{doc_type}-{priority}", document_type=doc_type,
                          match_conditions_json={"amount_min": amount_min},
                          priority=priority, status="active")
    db.add(wf)
    db.flush()
    db.add(ApprovalWorkflowNode(workflow_id=wf.id, node_name="审批", approver_role=node_role, node_order=1))
    return wf


def _no_enqueue(monkeypatch):
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)


# ---------- P0-1 审批人 Resolver ----------

def test_resolve_approver_blocks_self_approval(db):
    """申请人不能审批自己：唯一审批人=申请人 → 409。"""
    applicant = _make_user(db, "u1", ["approver"], ["approval:process"])
    _workflow(db)
    doc = _expense_doc(db, applicant)
    node = db.query(ApprovalWorkflowNode).first()
    with pytest.raises(HTTPException) as ei:
        workflow_service.resolve_approver(db, doc, node)
    assert ei.value.status_code == 409


def test_resolve_approver_no_eligible_no_task(db):
    """角色存在但无人有 approval:process → submit 409，不产生 approver_id=None 的 pending 任务。"""
    applicant = _make_user(db, "u_app", ["applicant"])
    _make_user(db, "u_role_only", ["approver"])   # 有 approver 角色但无 approval:process 权限
    _workflow(db)
    doc = _expense_doc(db, applicant)
    with pytest.raises(HTTPException):
        document_service.submit(db, applicant, doc.id)
    tasks = db.query(ApprovalTask).all()
    assert all(t.approver_id is not None for t in tasks)  # 不允许 approver_id=None 的 pending


def _instance_with_task(db, workflow_id, approver_id) -> ApprovalTask:
    inst = ApprovalInstance(workflow_id=workflow_id, document_id=1,
                            document_version=1, instance_status="running")
    db.add(inst)
    db.flush()
    node = db.query(ApprovalWorkflowNode).first()
    t = ApprovalTask(instance_id=inst.id, node_id=node.id,
                     approver_id=approver_id, task_status="pending")
    db.add(t)
    db.commit()
    return t


def test_resolve_approver_prefers_less_pending(db):
    """两个 approver：待办少者优先。"""
    applicant = _make_user(db, "u_app", ["applicant"])
    busy = _make_user(db, "u_busy", ["approver"], ["approval:process"])
    free = _make_user(db, "u_free", ["approver"], ["approval:process"])
    wf = _workflow(db)
    doc = _expense_doc(db, applicant)
    node = db.query(ApprovalWorkflowNode).first()
    _instance_with_task(db, wf.id, busy.id)   # busy 有一个待办，free 没有
    chosen = workflow_service.resolve_approver(db, doc, node)
    assert chosen.id == free.id  # 待办更少


def test_resolve_approver_stable_by_id(db):
    """相同待办数时 user.id 较小者优先，结果稳定。"""
    applicant = _make_user(db, "u_app", ["applicant"])
    a1 = _make_user(db, "u_a1", ["approver"], ["approval:process"])
    a2 = _make_user(db, "u_a2", ["approver"], ["approval:process"])
    _workflow(db)
    doc = _expense_doc(db, applicant)
    node = db.query(ApprovalWorkflowNode).first()
    assert a1.id < a2.id
    for _ in range(3):
        assert workflow_service.resolve_approver(db, doc, node).id == a1.id


# ---------- P0-3 Workflow priority ----------

def test_workflow_priority_wins(db, monkeypatch):
    """普通流程与大额流程同时匹配 → 高 priority 胜出。"""
    _no_enqueue(monkeypatch)
    applicant = _make_user(db, "u_app", ["applicant"])
    _make_user(db, "u_ap", ["approver"], ["approval:process"])
    _workflow(db, "company_payment", amount_min=0, priority=0)
    _workflow(db, "company_payment", amount_min=50000, priority=10)
    doc = FinancialDocument(
        document_type="company_payment", document_no="R4-CP", applicant_id=applicant.id,
        applicant_department="市场部", budget_department="市场部", payee_name="某公司",
        payee_account="A", expense_category="服务费", total_amount=Decimal("50000"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status="draft", current_version=0,
    )
    db.add(doc)
    db.flush()
    db.add(DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                              file_size=1, file_path="x", file_hash="h",
                              storage_status="stored", parse_status="pending",
                              document_category="invoice"))
    db.add(DocumentAttachment(document_id=doc.id, file_name="合同.png", file_type="png",
                              file_size=1, file_path="y", file_hash="h2",
                              storage_status="stored", parse_status="pending",
                              document_category="contract"))
    db.commit()
    document_service.submit(db, applicant, doc.id)
    inst = db.query(ApprovalInstance).filter(ApprovalInstance.document_id == doc.id).first()
    wf = db.get(ApprovalWorkflow, inst.workflow_id)
    assert wf.priority == 10   # 大额流程胜出


# ---------- P0-4 状态机合法性 ----------

def test_state_machine_rejects_illegal_transitions(db):
    doc = _expense_doc(db, _make_user(db, "u_app", ["applicant"]), status="draft")
    db.commit()
    with pytest.raises(HTTPException) as ei:
        document_state.transition(db, doc, "approved", 1, "非法")
    assert ei.value.status_code == 409
    doc.document_status = "voided"
    with pytest.raises(HTTPException):
        document_state.transition(db, doc, "approved", 1, "非法")


# ---------- P0-5 分析取消 ----------

def test_cancelled_analysis_no_report(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.models  # noqa
    engine = create_engine(f"sqlite:///{tmp_path/'c.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.session.SessionLocal", TestSession)

    db = TestSession()
    doc = FinancialDocument(
        document_type="expense", document_no="R4-C", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X", payee_account="Y",
        expense_category="差旅", total_amount=Decimal("100"), currency="CNY",
        apply_date=date(2026, 8, 1), document_status="pending_review", current_version=1,
    )
    db.add(doc)
    db.flush()
    task = AnalysisTask(document_id=doc.id, task_status="queued")
    db.add(task)
    db.commit()
    task.task_status = "cancelled"   # 提交后取消
    db.commit()
    asyncio.run(analysis_service.run_pipeline(task.id))
    db2 = TestSession()
    t = db2.get(AnalysisTask, task.id)
    assert t.task_status == "cancelled"
    assert db2.query(ReviewReport).filter_by(task_id=task.id).first() is None


def test_void_stops_running_analysis(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import app.models  # noqa
    engine = create_engine(f"sqlite:///{tmp_path/'v.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("app.db.session.SessionLocal", TestSession)

    db = TestSession()
    doc = FinancialDocument(
        document_type="expense", document_no="R4-V", applicant_id=1,
        applicant_department="A", budget_department="A", payee_name="X", payee_account="Y",
        expense_category="差旅", total_amount=Decimal("100"), currency="CNY",
        apply_date=date(2026, 8, 1), document_status="voided", current_version=1,  # 单据已作废
    )
    db.add(doc)
    db.flush()
    task = AnalysisTask(document_id=doc.id, task_status="analyzing")
    db.add(task)
    db.commit()
    asyncio.run(analysis_service.run_pipeline(task.id))
    db2 = TestSession()
    t = db2.get(AnalysisTask, task.id)
    assert t.task_status != "succeeded"            # 未生成成功报告
    assert db2.query(ReviewReport).filter_by(task_id=task.id).first() is None


# ---------- P1-12 复制 ----------

def test_copy_syncs_line_items(db):
    applicant = _make_user(db, "u_app", ["applicant"])
    src = _expense_doc(db, applicant, total="3000", doc_no="R4-SRC")
    for i in range(3):
        db.add(DocumentLineItem(document_id=src.id, item_type="expense",
                                item_name=f"项{i}", amount=Decimal("1000")))
    db.commit()
    new = document_service.copy(db, applicant, src.id)
    items = db.query(DocumentLineItem).filter(DocumentLineItem.document_id == new.id).all()
    assert len(items) == 3
    # 修改新单明细不影响原单
    items[0].amount = Decimal("1")
    db.commit()
    assert db.query(DocumentLineItem).filter(
        DocumentLineItem.document_id == src.id,
        DocumentLineItem.item_name == "项0").first().amount == Decimal("1000")
    # 不复制附件/版本/审批/风险
    assert db.query(DocumentVersion).filter(DocumentVersion.document_id == new.id).count() == 0
    assert db.query(ApprovalInstance).filter(ApprovalInstance.document_id == new.id).count() == 0
    assert db.query(AnalysisTask).filter(AnalysisTask.document_id == new.id).count() == 0


# ---------- P1-13 金额统一 ----------

def test_report_amount_equals_amount_comparison(db, monkeypatch):
    """报告金额与金额核对面板使用同一实现，结果一致。"""
    _no_enqueue(monkeypatch)
    applicant = _make_user(db, "u_app", ["applicant"])
    doc = _expense_doc(db, applicant, total="2000", doc_no="R4-AMT")
    db.add(DocumentLineItem(document_id=doc.id, item_type="expense", item_name="住宿",
                            amount=Decimal("2000")))
    att = DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                             file_size=1, file_path="x", file_hash="h",
                             storage_status="stored", parse_status="succeeded",
                             document_category="invoice")
    db.add(att)
    db.flush()
    db.add(InvoiceRecord(attachment_id=att.id, invoice_no="N1", amount_including_tax=Decimal("2000")))
    db.commit()

    comp = amount_service.calculate_amount_comparison(db, doc)
    from app.domain.risk_engine import build_context, compute_overall_level, run_all
    from app.services import report_service as rs
    task = AnalysisTask(document_id=doc.id, task_status="succeeded")
    db.add(task)
    db.commit()
    # 测试不调用真实 LLM 润色（省 API 调用）
    monkeypatch.setattr("app.clients.llm.polish_risk_report", lambda s, f: "AI narrative")
    ctx = build_context(db, doc)
    findings = run_all(ctx)
    rep = rs.generate(db, task, doc, findings, compute_overall_level(findings))
    # 报告 amount_comparison_json 与面板金额一致
    assert Decimal(rep.amount_comparison_json["line_items_total"]) == comp.line_items_total
    assert Decimal(rep.amount_comparison_json["invoice_total"]) == comp.invoice_total


# ---------- P1-7 ManualReview 不改变单据状态 ----------

def test_manual_review_does_not_change_status(db):
    applicant = _make_user(db, "u_app", ["applicant"])
    doc = _expense_doc(db, applicant, status="pending_review")
    from app.models.analysis import ReviewReport
    report = ReviewReport(task_id=1, document_id=doc.id, overall_risk_level="medium",
                          report_markdown="# x")
    db.add(report)
    db.commit()
    report_service.submit_manual_review(db, applicant, report.id, "confirmed", "ok")
    db.refresh(doc)
    assert doc.document_status == "pending_review"   # 复核不改变单据状态
    with pytest.raises(HTTPException):
        report_service.submit_manual_review(db, applicant, report.id, "approved", "非法枚举")
