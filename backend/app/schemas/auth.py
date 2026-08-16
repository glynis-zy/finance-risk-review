# -*- coding: utf-8 -*-
"""认证相关 Pydantic 模型。"""
from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role_codes: list[str] = []
    permission_codes: list[str] = []


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
