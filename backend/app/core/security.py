# -*- coding: utf-8 -*-
"""密码哈希 / JWT 签发与校验 / 令牌撤销（持久化）。

设计口径（面试用）：
- 密码绝不明文，用 bcrypt 哈希（规格 2.7.14）；
- 访问令牌带有效期 + jti；
- 登出/泄露撤销：写 `revoked_tokens` 表（jti 黑名单，**重启不丢**），
  内存 set 仅作快速路径缓存；令牌校验时先查缓存再查库。
"""
import time
import uuid
from datetime import datetime

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.revoked import RevokedToken

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 已撤销 jti 的快速路径缓存（持久来源在 revoked_tokens 表）
_revoked_cache: set[str] = set()


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    """签发 JWT，payload 含 sub/username/jti/iat/exp。"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + settings.jwt_expire_minutes * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    """仅校验签名与有效期，返回 payload；撤销判定走 is_token_revoked。"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


def is_token_revoked(db: Session, jti: str) -> bool:
    """jti 是否已撤销：先查内存缓存，未命中再查库（库命中则回填缓存）。"""
    if jti in _revoked_cache:
        return True
    row = db.scalar(select(RevokedToken).where(RevokedToken.jti == jti))
    if row is not None:
        _revoked_cache.add(jti)
        return True
    return False


def revoke_token(db: Session, token: str) -> None:
    """登出时撤销：解析出 jti，写入 revoked_tokens 表（持久化，重启不丢）。"""
    payload = decode_access_token(token)
    if payload is None:
        return
    purge_revoked(db)  # 顺带清理已过期撤销记录
    jti = payload["jti"]
    if db.scalar(select(RevokedToken).where(RevokedToken.jti == jti)):
        _revoked_cache.add(jti)
        return
    db.add(RevokedToken(
        jti=jti,
        expires_at=datetime.utcfromtimestamp(payload["exp"]),
        revoked_at=datetime.utcnow(),
    ))
    db.commit()
    _revoked_cache.add(jti)


def purge_revoked(db: Session) -> None:
    """清理已过期的撤销记录（表 + 缓存），可登录/登出时顺带调用。"""
    now = datetime.utcnow()
    rows = db.execute(
        select(RevokedToken).where(RevokedToken.expires_at <= now)
    ).scalars().all()
    if rows:
        for r in rows:
            db.delete(r)
            _revoked_cache.discard(r.jti)
        db.commit()
