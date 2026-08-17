# -*- coding: utf-8 -*-
"""LLM 适配层统一出口：默认 DeepSeek 实现。换厂商只改这里 / .env。"""
from app.clients.llm.base import LLMClient
from app.clients.llm.deepseek import (
    extract_contract_fields,
    parse_dialogue_intent,
    polish_risk_report,
)

__all__ = ["LLMClient", "extract_contract_fields", "parse_dialogue_intent", "polish_risk_report"]
