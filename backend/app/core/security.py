# -*- coding: utf-8 -*-
"""密码哈希 / JWT 签发与校验 / 令牌撤销。

设计口径（面试用）：
- 密码绝不明文，用 bcrypt 哈希（规格 2.7.14）；
- 访问令牌带有效期 + jti，登出/泄露时加入撤销集合（内存实现，注释里说明生产换 Redis）。
"""
import time
import uuid

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 已撤销令牌的 jti 集合：{jti: expire_ts}，校验时按过期时间清理。
_revoked_jti: dict[str, int] = {}


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: int, username: str) -> str:
    """签发 JWT，payload 含 user_id/username/jti/exp。"""
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
    """校验签名/有效期/撤销状态，非法返回 None。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("jti") in _revoked_jti:
        return None
    return payload


def revoke_token(token: str) -> None:
    """登出时撤销：解析出 exp，登记 jti 到内存撤销集合。"""
    payload = decode_access_token(token)
    if payload:
        _revoked_jti[payload["jti"]] = int(payload["exp"])


def purge_revoked() -> None:
    """清理已过期的撤销登记（可后台定时调用）。"""
    now = int(time.time())
    for jti, exp in list(_revoked_jti.items()):
        if exp <= now:
            _revoked_jti.pop(jti, None)
