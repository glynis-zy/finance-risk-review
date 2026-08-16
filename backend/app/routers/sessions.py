# -*- coding: utf-8 -*-
"""审核会话路由：多轮对话（LLM NLU + 槽位状态机）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.user import User
from app.services import dialogue_service

router = APIRouter(prefix="/review-sessions", tags=["sessions"])


class MessageIn(BaseModel):
    content: str


@router.post("")
def create_session(
    user: User = Depends(require_perm("session:chat")),
    db: Session = Depends(get_db),
):
    s = dialogue_service.create_session(db, user)
    return {"session_id": s.id, "messages": dialogue_service.list_messages(db, user, s.id)}


@router.post("/{session_id}/messages")
def send_message(
    session_id: int,
    payload: MessageIn,
    user: User = Depends(require_perm("session:chat")),
    db: Session = Depends(get_db),
):
    return dialogue_service.process_message(db, user, session_id, payload.content)


@router.get("/{session_id}/messages")
def get_messages(
    session_id: int,
    user: User = Depends(require_perm("session:chat")),
    db: Session = Depends(get_db),
):
    return dialogue_service.list_messages(db, user, session_id)
