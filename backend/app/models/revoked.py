# -*- coding: utf-8 -*-
"""撤销令牌表（JWT jti 黑名单）。

规格 2.7.14 要求"访问令牌设置有效期和撤销机制"。
撤销记录持久化到本表（重启不丢），内存 set 仅作快速路径缓存。
"""
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)   # 原令牌过期时间，用于清理
    revoked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
