# -*- coding: utf-8 -*-
"""JWT 撤销持久化测试：登出写 revoked_tokens 表，重启/新会话仍生效。"""
import time

import jwt as pyjwt
from sqlalchemy import select

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    is_token_revoked,
    revoke_token,
)
from app.models.revoked import RevokedToken


def test_revoke_writes_table_and_blocks(db):
    token = create_access_token(1, "u1")
    payload = decode_access_token(token)
    assert payload is not None
    assert not is_token_revoked(db, payload["jti"])

    revoke_token(db, token)
    assert is_token_revoked(db, payload["jti"])

    # 持久化：DB 中有记录（模拟重启后新 session 仍可查到）
    row = db.scalar(select(RevokedToken).where(RevokedToken.jti == payload["jti"]))
    assert row is not None and row.jti == payload["jti"]


def test_revoke_idempotent(db):
    token = create_access_token(1, "u2")
    payload = decode_access_token(token)
    revoke_token(db, token)
    revoke_token(db, token)  # 重复撤销不报错、不重复写
    rows = db.scalars(select(RevokedToken).where(RevokedToken.jti == payload["jti"])).all()
    assert len(rows) == 1


def test_expired_token_rejected():
    payload = {
        "sub": "1", "username": "u", "jti": "expired-jti",
        "iat": int(time.time()) - 2000, "exp": int(time.time()) - 1000,
    }
    token = pyjwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert decode_access_token(token) is None
