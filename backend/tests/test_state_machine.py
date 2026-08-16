# -*- coding: utf-8 -*-
"""状态机测试：版本语义（P0-3）、void 终态（P0-2）、退回重提交。"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.analysis import AnalysisTask
from app.models.attachment import DocumentAttachment
from app.models.document import DocumentVersion, FinancialDocument
from app.models.user import Role, User, UserRole
from app.models.workflow import ApprovalInstance, ApprovalWorkflow, ApprovalWorkflowNode, ApprovalTask
from app.services import document_service, workflow_service


def _approver(db):
    role = Role(role_code="approver", role_name="审批人员")
    db.add(role)
    db.flush()
    u = User(username="u_approver", display_name="测试审批", password_hash="x")
    db.add(u)
    db.flush()
    db.add(UserRole(user_id=u.id, role_id=role.id))
    return u


def _expense_workflow(db):
    wf = ApprovalWorkflow(workflow_name="费用审批", document_type="expense",
                          match_conditions_json={"amount_min": 0}, status="active")
    db.add(wf)
    db.flush()
    db.add(ApprovalWorkflowNode(workflow_id=wf.id, node_name="部门主管",
                                approver_role="approver", node_order=1))


def _doc(db, user, doc_no, status, current_version) -> FinancialDocument:
    doc = FinancialDocument(
        document_type="expense", document_no=doc_no, applicant_id=user.id,
        applicant_department="市场部", budget_department="市场部", payee_name="某公司",
        payee_account="A", expense_category="差旅", total_amount=Decimal("1000"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status=status,
        current_version=current_version,
    )
    db.add(doc)
    db.flush()
    # 暂存附件（document_version=0，待提交时绑定本次版本）
    db.add(DocumentAttachment(document_id=doc.id, file_name="发票.png", file_type="png",
                              file_size=1, file_path="x", file_hash="h",
                              storage_status="stored", parse_status="pending",
                              document_category="invoice"))
    db.commit()
    return doc


def test_first_submit_all_versions_are_1(db, monkeypatch):
    """P0-3：首次提交 snapshot.version_no == current_version == instance.document_version == 附件版本 == 1。"""
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)
    user = _approver(db)
    _expense_workflow(db)

    doc = _doc(db, user, "EX-TEST-001", "draft", 0)  # draft 未提交过
    document_service.submit(db, user, doc.id)

    assert doc.document_status == "pending_review"
    assert doc.current_version == 1
    snap = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).first()
    inst = db.query(ApprovalInstance).filter(ApprovalInstance.document_id == doc.id).first()
    att = db.query(DocumentAttachment).filter(DocumentAttachment.document_id == doc.id).first()
    assert snap.version_no == 1
    assert inst.document_version == 1
    assert att.document_version == 1          # 附件绑定到本次提交版本
    assert db.query(AnalysisTask).filter(AnalysisTask.document_id == doc.id).count() == 1


def test_returned_resubmit_all_versions_are_2(db, monkeypatch):
    """P0-3：draft→submit(v1)→退回→补新附件→重提交(v2)；版本记录区分、新附件绑 v2、旧附件留 v1。"""
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)
    user = _approver(db)
    _expense_workflow(db)

    doc = _doc(db, user, "EX-TEST-002", "draft", 0)
    document_service.submit(db, user, doc.id)           # v1

    doc.document_status = "returned"                    # 模拟审批退回
    # 退回修改期间新增附件（暂存 document_version=0，待 v2 绑定）
    db.add(DocumentAttachment(document_id=doc.id, file_name="补充发票.png", file_type="png",
                              file_size=1, file_path="y", file_hash="h2",
                              storage_status="stored", parse_status="pending"))
    db.commit()

    document_service.submit(db, user, doc.id)           # v2

    assert doc.current_version == 2
    snaps = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).all()
    assert {s.version_no for s in snaps} == {1, 2}      # 第 1、2 版本记录清楚区分
    inst = db.query(ApprovalInstance).filter(
        ApprovalInstance.document_id == doc.id).order_by(ApprovalInstance.id.desc()).first()
    assert inst.document_version == 2
    atts = db.query(DocumentAttachment).filter(DocumentAttachment.document_id == doc.id).all()
    assert sorted(a.document_version for a in atts) == [1, 2]   # 旧附件 v1，新附件 v2


def test_void_blocks_old_approval_task(db, monkeypatch):
    """P0-2：提交 → 作废 → 旧 task approve 必须 409，document 仍 voided。"""
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)
    user = _approver(db)
    _expense_workflow(db)

    doc = _doc(db, user, "EX-TEST-003", "draft", 0)
    document_service.submit(db, user, doc.id)
    task = db.query(ApprovalTask).filter(ApprovalTask.instance_id.in_(
        db.query(ApprovalInstance.id).filter(ApprovalInstance.document_id == doc.id)
    )).first()

    document_service.void(db, user, doc.id)
    assert doc.document_status == "voided"
    inst = db.query(ApprovalInstance).filter(ApprovalInstance.document_id == doc.id).first()
    assert inst.instance_status == "cancelled"           # 审批实例被取消
    assert db.get(ApprovalTask, task.id).task_status == "cancelled"

    with pytest.raises(HTTPException) as ei:
        workflow_service.approve(db, user, task.id)
    assert ei.value.status_code == 409
    db.refresh(doc)
    assert doc.document_status == "voided"               # 终态不可被旧任务迁移


def test_review_comment_and_status_log(db, monkeypatch):
    """P1-3：审批意见落库；状态日志 operator_id 记真实审批人。"""
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)
    user = _approver(db)
    _expense_workflow(db)

    doc = _doc(db, user, "EX-TEST-004", "draft", 0)
    document_service.submit(db, user, doc.id)
    task = db.query(ApprovalTask).filter(ApprovalTask.instance_id.in_(
        db.query(ApprovalInstance.id).filter(ApprovalInstance.document_id == doc.id)
    )).first()

    workflow_service.approve(db, user, task.id, "金额核实无误，同意")
    assert db.get(ApprovalTask, task.id).review_comment == "金额核实无误，同意"

    from app.models.document import DocumentStatusLog
    logs = db.query(DocumentStatusLog).filter(DocumentStatusLog.document_id == doc.id).all()
    assert any(l.to_status == "approved" and l.operator_id == user.id for l in logs)   # 真实审批人
