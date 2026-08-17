# -*- coding: utf-8 -*-
"""LLM 客户端抽象：上层只依赖统一接口，厂商实现放 deepseek.py。

未来可无侵入增加 local_vllm.py / 通义等（本轮禁止实际接入）。
职责边界（四层分工）：LLM 只做"理解非结构化文本"（合同字段提取、对话意图解析）
与"自然语言解释"（报告润色），绝不参与风险判定。
"""
from abc import ABC, abstractmethod

from app.schemas.llm import ContractFields, SlotUpdate


class LLMClient(ABC):
    @abstractmethod
    def extract_contract_fields(self, full_text: str) -> ContractFields | None: ...

    @abstractmethod
    def parse_dialogue_intent(self, text: str) -> SlotUpdate | None: ...

    @abstractmethod
    def polish_risk_report(self, summary: str, findings: list[dict]) -> str: ...
