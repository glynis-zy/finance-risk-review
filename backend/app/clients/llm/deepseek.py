# -*- coding: utf-8 -*-
"""DeepSeek LLM 实现（OpenAI 兼容接口，模块级函数，接口见 base.LLMClient）。

LLM 输出必须是严格 JSON，经 Pydantic 校验后才进入业务；失败返回 None（降级），
绝不参与最终风险判定（规则引擎负责）。
"""
import json
import logging

import httpx
from openai import OpenAI

from app.core.config import settings
from app.schemas.llm import ContractFields, SlotUpdate

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY 未配置（见 .env）")
        kwargs: dict = {"api_key": settings.llm_api_key, "base_url": settings.llm_base_url}
        if getattr(settings, "llm_insecure_ssl", False):
            # AutoDL/自建推理网关常为自签证书；演示环境按需跳过校验
            kwargs["http_client"] = httpx.Client(verify=False)
        _client = OpenAI(**kwargs)
    return _client


def _chat_json(system: str, user: str, schema_cls) -> object | None:
    """调用 LLM，强制 JSON 输出，用 schema_cls 校验。失败返回 None。"""
    try:
        client = _get_client()
        # Qwen3 默认开启 <think> 深度思考，会污染结构化输出和报告润色
        # 通过 chat_template_kwargs 在 vLLM / Qwen3 上禁用思考；DeepSeek 等后端忽略未知字段
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            extra_body=extra_body,
        )
        raw = resp.choices[0].message.content or ""
        return schema_cls(**json.loads(raw))
    except Exception as exc:  # noqa: BLE001  LLM 失败按降级处理
        logger.warning("LLM call failed: %s", exc)
        return None


def extract_contract_fields(full_text: str) -> ContractFields | None:
    """合同全文 → ContractFields。重试一次，仍失败返回 None（转 manual_review）。"""
    system = (
        "你是财务单据审核系统的合同信息提取器。只从给定文本提取下列字段，"
        "输出 JSON（不要任何解释）。无法确定的可为 null。"
        "字段: contract_no, party_a, party_b, contract_amount, "
        "payment_terms, payment_ratio(百分比数字), signed_date(YYYY-MM-DD)。"
    )
    text = full_text[:12000]
    first = _chat_json(system, f"合同文本：\n{text}", ContractFields)
    if first is not None:
        return first
    return _chat_json(system, f"请重新仔细阅读提取：\n{text}", ContractFields)


def parse_dialogue_intent(text: str) -> SlotUpdate | None:
    """对话输入 → 槽位更新（LLM NLU，失败退回本地规则解析）。"""
    system = (
        "你是财务审核对话的意图解析器。从用户一句话中抽出："
        "document_type(单据类型，若提到，取 company_payment/advance_payment/batch_payment/expense/travel 之一，"
        "或其中文名：对公付款/预付/批量付款/费用报销/差旅)，"
        "document_no(单据编号，如 CP-20260816-001)，intent(start_analysis/query_status/other)。"
        "输出 JSON，不确定的字段为 null。"
    )
    return _chat_json(system, f"用户输入：{text[:500]}", SlotUpdate)


def polish_risk_report(summary: str, findings: list[dict]) -> str:
    """把规则引擎结论润色为业务人员易读的 Markdown 叙述（只换表达，不改结论）。"""
    system = (
        "你是财务审核报告撰写助手。基于给定的风险结论，写一段面向业务人员的 Markdown 风险说明。"
        "要求：不得新增或删改结论本身；结构清晰；语言简洁商务。"
    )
    try:
        client = _get_client()
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}  # Qwen3 关闭思考
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"单据摘要：{summary}\n风险项：{json.dumps(findings, ensure_ascii=False)}"},
            ],
            temperature=0.3,
            extra_body=extra_body,
        )
        return resp.choices[0].message.content or summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM polish failed, fallback to raw: %s", exc)
        return summary
