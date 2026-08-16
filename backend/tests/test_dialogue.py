# -*- coding: utf-8 -*-
"""多轮对话测试（P1-1）：无 LLM key 纯槽位可跑通；类型+编号冲突必须澄清。"""
from datetime import date
from decimal import Decimal

from app.models.document import FinancialDocument
from app.models.session import ReviewSession
from app.models.user import User
from app.services import dialogue_service


def _user(db) -> User:
    u = User(username="chat_u", display_name="对话", password_hash="x")
    db.add(u)
    db.flush()
    return u


def _doc(db, user, doc_no, doc_type) -> FinancialDocument:
    d = FinancialDocument(
        document_type=doc_type, document_no=doc_no, applicant_id=user.id,
        applicant_department="市场部", budget_department="市场部", payee_name="某公司",
        payee_account="A", expense_category="差旅", total_amount=Decimal("1000"),
        currency="CNY", apply_date=date(2026, 8, 1), document_status="pending_review",
        current_version=1,
    )
    db.add(d)
    db.flush()
    return d


def _no_llm(monkeypatch):
    """无 LLM key：parse_dialogue_intent 返回 None → 走本地规则槽位解析。"""
    monkeypatch.setattr(
        "app.services.dialogue_service.llm_client.parse_dialogue_intent",
        lambda text: None,
    )
    monkeypatch.setattr("app.services.analysis_service.enqueue", lambda task_id: None)


def test_pure_slot_without_llm_key(db, monkeypatch):
    _no_llm(monkeypatch)
    user = _user(db)
    _doc(db, user, "CP-20260816-001", "company_payment")
    session = ReviewSession(user_id=user.id, session_status="active")
    db.add(session)
    db.commit()

    r = dialogue_service.process_message(db, user, session.id, "帮我查对公付款单 CP-20260816-001")
    assert r["task_id"] is not None
    assert "任务编号" in r["reply"]


def test_local_parse_abbreviation_and_no(db, monkeypatch):
    """本地 fallback：中文简称 + 编号规整。"""
    _no_llm(monkeypatch)
    user = _user(db)
    _doc(db, user, "TR-20260816-005", "travel")
    session = ReviewSession(user_id=user.id, session_status="active")
    db.add(session)
    db.commit()

    r = dialogue_service.process_message(db, user, session.id, "查差旅 TR-20260816-005")
    assert r["task_id"] is not None


def test_conflict_type_and_no_clarified(db, monkeypatch):
    """用户给"费用报销单 CP-..."，CP 实际是对公付款单 → 冲突澄清，不偷偷忽略类型。"""
    _no_llm(monkeypatch)
    user = _user(db)
    _doc(db, user, "CP-20260816-001", "company_payment")
    session = ReviewSession(user_id=user.id, session_status="active")
    db.add(session)
    db.commit()

    r = dialogue_service.process_message(db, user, session.id, "帮我查费用报销单 CP-20260816-001")
    assert "不一致" in r["reply"] and "请确认" in r["reply"]
    assert r["task_id"] is None

    # 用户选 A（采用单据实际类型：对公付款单）→ 覆盖类型 → 发起分析
    r2 = dialogue_service.process_message(db, user, session.id, "A")
    assert r2["task_id"] is not None
