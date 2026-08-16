# -*- coding: utf-8 -*-
"""LLM 结构化输出 schema（Pydantic 强校验，禁止自由文本字段输出）。

设计口径（用户确认）：LLM 只输出严格 JSON，经 Pydantic 校验后才进入业务，
失败重试一次，仍失败转 manual_review。金额/付款条件等风险判定由规则引擎负责。
"""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ContractFields(BaseModel):
    """合同关键字段（OCR 全文 → LLM 提取）。P2-3：无法确定时允许 null，与 Prompt 对齐。"""
    contract_no: str
    party_a: str | None = None        # 甲方（买方）
    party_b: str | None = None        # 乙方（卖方/供应商）
    contract_amount: Decimal | None = None
    payment_terms: str | None = None   # 付款条件描述
    payment_ratio: Decimal | None = None  # 付款比例 %
    signed_date: date | None = None


class SlotUpdate(BaseModel):
    """对话 NLU 输出：从用户自由文本抽取的槽位。"""
    document_type: str | None = None   # 与 document_schemas 中的类型 key 对齐
    document_no: str | None = None
    intent: str | None = None          # start_analysis / query_status / other
