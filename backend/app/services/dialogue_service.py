# -*- coding: utf-8 -*-
"""多轮对话服务：LLM NLU → 本地规则槽位解析（fallback）→ 槽位状态机。

P1-1：
- LLM 不可用时纯槽位模式仍能完整跑通（本地识别五类中文单据名/简称 + 单据编号）；
- 查询同时校验 document_type + document_no，存在冲突必须澄清，不偷偷忽略 document_type；
- 用户显式纠正时允许覆盖旧槽位；已确认槽位正常情况下不重复询问。
"""
import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.document_schemas import TYPE_LABELS, TYPE_FIELD_SCHEMAS
from app.models.document import FinancialDocument
from app.models.session import ReviewSession, SessionMessage
from app.models.user import User
from app.services import analysis_service, audit_service, document_service, llm_client

VALID_TYPES = set(TYPE_FIELD_SCHEMAS.keys())

# 中文名/简称 → 单据类型 code（本地 fallback）
TYPE_ALIASES: dict[str, list[str]] = {
    "company_payment": ["对公付款单", "对公付款", "对公"],
    "advance_payment": ["预付款单", "预付款", "预付"],
    "batch_payment": ["批量付款单", "批量付款", "批量"],
    "expense": ["费用报销单", "费用报销", "报销单", "报销"],
    "travel": ["差旅报销单", "差旅报销", "差旅", "出差"],
}

_NO_RE = re.compile(r"(CP|AP|BP|EX|TR)[-\s]?\d{8}[-\s]?\d{3}")


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

    reply, mtype, task_id = _advance(db, session, content)
    db.add(SessionMessage(session_id=session.id, role="assistant",
                          content=reply, message_type=mtype))
    db.commit()
    return {"reply": reply, "message_type": mtype, "task_id": task_id}


def _owned(db: Session, user: User, session_id: int) -> ReviewSession:
    session = db.get(ReviewSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(403, "会话不存在或不属于当前用户")
    return session


# ---------- 槽位解析 ----------

def _parse_slots(text: str) -> dict[str, str | None]:
    """LLM NLU 优先，失败/无 key 用本地规则解析。"""
    slots = llm_client.parse_dialogue_intent(text)
    if slots is not None and (slots.document_type or slots.document_no):
        return {"type": slots.document_type, "no": slots.document_no}
    return _local_parse(text)


def _local_parse(text: str) -> dict[str, str | None]:
    doc_type = None
    for code, labels in TYPE_ALIASES.items():
        if any(label in text for label in labels):
            doc_type = code
            break
    m = _NO_RE.search(text.upper())
    doc_no = None
    if m:
        raw = m.group(0).replace(" ", "-")
        # 规整：CP-20260816-001（把可能缺失的分隔补上）
        parts = re.match(r"(CP|AP|BP|EX|TR)-?(\d{8})-?(\d{3})", raw)
        if parts:
            doc_no = f"{parts.group(1)}-{parts.group(2)}-{parts.group(3)}"
    return {"type": doc_type, "no": doc_no}


# ---------- 槽位状态机 ----------

def _advance(db: Session, session: ReviewSession, content: str) -> tuple[str, str, int | None]:
    cand = _parse_slots(content)
    stripped = content.strip().lower()

    # 冲突澄清后的选择：A=采用单据实际类型；B=重新输入编号
    if session.document_type and session.document_no:
        doc0 = _find_document(db, session)
        if doc0 is not None and doc0.document_type != session.document_type:
            label = TYPE_LABELS.get(doc0.document_type, "")
            if stripped == "a" or (label and label in content):
                session.document_type = doc0.document_type   # 采用单据实际类型
                cand = {"type": None, "no": None}
            elif stripped == "b" or "重新输入" in content:
                session.document_no = None
                session.document_type = None
                cand = {"type": None, "no": None}

    # 1) 更新槽位（用户显式纠正时覆盖旧值）
    if cand["no"]:
        if session.document_no and cand["no"] != session.document_no:
            session.document_no = cand["no"]
            session.document_type = None          # 编号变了，类型需重新确认
        else:
            session.document_no = cand["no"]
    if cand["type"] and cand["type"] in VALID_TYPES:
        session.document_type = cand["type"]
    db.flush()

    # 2) 槽位齐全 → 查单 + 类型一致性校验（冲突不得偷偷忽略类型）
    if session.document_type and session.document_no:
        doc = _find_document(db, session)
        if doc is None:
            return (f"未在您的权限范围内找到单据 {session.document_no}，请确认编号是否正确",
                    "confirm", None)
        if doc.document_type != session.document_type:
            return (
                f"类型和编号对应单据不一致，请确认：\n"
                f"  A. {TYPE_LABELS.get(doc.document_type, doc.document_type)} {doc.document_no}\n"
                f"  B. 重新输入单据编号",
                "confirm", None,
            )
        return _start_analysis(db, session, doc)

    # 3) 缺槽位 → 问（已确认的槽位不重复询问）
    if session.document_type is None:
        return ("请先确认单据类型：" + "、".join(TYPE_LABELS.values()), "ask_slot", None)
    if session.document_no is None:
        return (f"请输入{TYPE_LABELS.get(session.document_type)}的单据编号，"
                f"例如 CP-20260816-001", "ask_slot", None)
    # 兜底（不应到达）
    return ("请提供单据类型和编号", "ask_slot", None)


def _find_document(db: Session, session: ReviewSession) -> FinancialDocument | None:
    doc = db.scalar(select(FinancialDocument).where(
        FinancialDocument.document_no == session.document_no))
    if doc is None:
        return None
    user = db.get(User, session.user_id)
    try:
        document_service.ensure_viewable(db, user, doc.id)
    except HTTPException:
        return None
    return doc


def _start_analysis(db: Session, session: ReviewSession, doc: FinancialDocument) -> tuple[str, str, int | None]:
    task = analysis_service.create_task(db, doc.id, session_id=session.id)
    db.commit()
    analysis_service.enqueue(task.id)
    reply = (
        f"已定位单据：{TYPE_LABELS.get(doc.document_type)} {doc.document_no}（{doc.total_amount} {doc.currency}）。"
        f"正在发起风险分析，任务编号 {task.id}，稍后可查看结果。"
    )
    return reply, "analysis_started", task.id
