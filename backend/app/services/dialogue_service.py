# -*- coding: utf-8 -*-
"""多轮对话服务：LLM NLU + 槽位状态机（规格 2.7.6 / D8）。

流程：用户输入 → LLM 抽 {document_type, document_no}（Pydantic 校验）→
状态机决策下一步（缺类型问类型 / 缺编号问编号 / 齐了查单据并发起分析）。
LLM 解析失败退回纯槽位问答。已确认槽位存会话，不重复询问。
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.document_schemas import TYPE_LABELS, TYPE_FIELD_SCHEMAS
from app.models.document import FinancialDocument
from app.models.session import ReviewSession, SessionMessage
from app.models.user import User
from app.services import analysis_service, audit_service, document_service, llm_client

VALID_TYPES = set(TYPE_FIELD_SCHEMAS.keys())


def create_session(db: Session, user: User) -> ReviewSession:
    session = ReviewSession(user_id=user.id, session_status="active")
    db.add(session)
    db.flush()
    db.add(SessionMessage(
        session_id=session.id, role="assistant",
        content="您好，请提供要审核的单据类型和单据编号，例如：对公付款单 CP-20260816-001",
        message_type="text",
    ))
    audit_service.log(db, user, "session:create", "review_session", str(session.id))
    db.commit()
    db.refresh(session)
    return session


def list_messages(db: Session, user: User, session_id: int) -> list[dict]:
    _owned(db, user, session_id)
    rows = db.scalars(select(SessionMessage).where(
        SessionMessage.session_id == session_id).order_by(SessionMessage.id.asc())).all()
    return [{"role": m.role, "content": m.content, "message_type": m.message_type,
             "created_at": str(m.created_at)} for m in rows]


def process_message(db: Session, user: User, session_id: int, content: str) -> dict:
    session = _owned(db, user, session_id)
    db.add(SessionMessage(session_id=session_id, role="user", content=content, message_type="text"))
    db.flush()

    # 1) LLM NLU 抽槽（失败不影响，继续槽位问答）
    slots = llm_client.parse_dialogue_intent(content)
    if slots is not None:
        if slots.document_type and session.document_type is None:
            if slots.document_type in VALID_TYPES:
                session.document_type = slots.document_type
        if slots.document_no and session.document_no is None:
            session.document_no = slots.document_no

    # 2) 槽位状态机
    if session.document_type is None:
        reply = "请先确认单据类型：" + "、".join(TYPE_LABELS.values())
        return _assistant(db, session, reply, "ask_slot")

    if session.document_no is None:
        reply = f"请输入{TYPE_LABELS.get(session.document_type)}的单据编号，例如 CP-20260816-001"
        return _assistant(db, session, reply, "ask_slot")

    # 3) 槽位齐全：查单据（数据权限范围内）
    doc = _find_document(db, user, session)
    if doc is None:
        reply = f"未在您的权限范围内找到单据 {session.document_no}，请确认编号是否正确"
        return _assistant(db, session, reply, "confirm")

    # 4) 发起分析
    task = analysis_service.create_task(db, doc.id, session_id=session.id)
    db.commit()
    analysis_service.enqueue(task.id)
    reply = (
        f"已定位单据：{TYPE_LABELS.get(doc.document_type)} {doc.document_no}（{doc.total_amount} {doc.currency}）。"
        f"正在发起风险分析，任务编号 {task.id}，稍后可查看结果。"
    )
    return _assistant(db, session, reply, "analysis_started", task_id=task.id)


def _owned(db: Session, user: User, session_id: int) -> ReviewSession:
    session = db.get(ReviewSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(403, "会话不存在或不属于当前用户")
    return session


def _find_document(db: Session, user: User, session: ReviewSession) -> FinancialDocument | None:
    doc = db.scalar(select(FinancialDocument).where(
        FinancialDocument.document_no == session.document_no))
    if doc is None:
        return None
    try:
        document_service.ensure_viewable(db, user, doc.id)
    except HTTPException:
        return None
    return doc


def _assistant(db: Session, session: ReviewSession, content: str,
               message_type: str, task_id: int | None = None) -> dict:
    db.add(SessionMessage(
        session_id=session.id, role="assistant", content=content, message_type=message_type))
    db.commit()
    return {"reply": content, "message_type": message_type, "task_id": task_id}
