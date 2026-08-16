# -*- coding: utf-8 -*-
"""审核会话与消息（多轮对话：LLM NLU + 槽位状态机）。"""
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class ReviewSession(TimestampMixin, Base):
    __tablename__ = "review_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 已确认槽位
    document_no: Mapped[str | None] = mapped_column(String(32), nullable=True)    # 已确认槽位
    session_status: Mapped[str] = mapped_column(String(16), default="active")


class SessionMessage(TimestampMixin, Base):
    __tablename__ = "session_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("review_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))          # user / assistant
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(16))  # text / ask_slot / confirm / analysis_started
