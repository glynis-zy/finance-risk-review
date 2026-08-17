# -*- coding: utf-8 -*-
"""单据类型字段元数据（元数据驱动设计）。

面试口径：新增一种单据类型 = 在这里加一份字段定义 + 前端动态表单自动渲染，
平台代码零改动。`type_fields_json` 的值按这里定义校验。
"""
from datetime import date
from decimal import Decimal, InvalidOperation

# 每类单据的专属字段定义（规格 2.7.2）
# 字段类型: string / number / percent / date
TYPE_FIELD_SCHEMAS: dict[str, list[dict]] = {
    "company_payment": [  # 对公付款单
        {"key": "contract_no", "label": "合同编号", "type": "string", "required": True},
        {"key": "supplier_name", "label": "供应商名称", "type": "string", "required": True},
        {"key": "payment_ratio", "label": "付款比例(%)", "type": "percent", "required": True},
        {"key": "payment_terms", "label": "付款条件", "type": "string", "required": False},
        {"key": "planned_payment_date", "label": "计划付款日期", "type": "date", "required": True},
    ],
    "advance_payment": [  # 预付款单
        {"key": "contract_no", "label": "合同编号", "type": "string", "required": True},
        {"key": "supplier_name", "label": "供应商名称", "type": "string", "required": True},
        {"key": "payment_ratio", "label": "付款比例(%)", "type": "percent", "required": True},
        {"key": "payment_terms", "label": "付款条件", "type": "string", "required": False},
        {"key": "planned_payment_date", "label": "计划付款日期", "type": "date", "required": True},
    ],
    "batch_payment": [  # 批量付款单：收款对象/单笔金额由明细承载
        {"key": "batch_note", "label": "批次说明", "type": "string", "required": False},
        {"key": "payment_count", "label": "付款笔数", "type": "number", "required": True},
    ],
    "expense": [  # 费用报销单：明细承载费用明细
        {},
    ],
    "travel": [  # 差旅报销单
        {"key": "travel_destination", "label": "出差地点", "type": "string", "required": True},
        {"key": "travel_start", "label": "出差开始日期", "type": "date", "required": True},
        {"key": "travel_end", "label": "出差结束日期", "type": "date", "required": True},
        {"key": "transport_fee", "label": "交通费", "type": "number", "required": True},
        {"key": "hotel_fee", "label": "住宿费", "type": "number", "required": True},
        {"key": "meal_fee", "label": "餐费", "type": "number", "required": True},
        {"key": "allowance", "label": "补贴金额", "type": "number", "required": True},
    ],
}

# 每类单据提交时要求的附件类别（附件完整性规则的"必需"基线）
REQUIRED_ATTACHMENTS: dict[str, list[str]] = {
    "company_payment": ["contract", "invoice"],
    "advance_payment": ["contract"],
    "batch_payment": ["payment_basis"],
    "expense": ["invoice"],
    "travel": ["invoice", "itinerary"],
}

# 单据类型 → 中文名（前端下拉/报告展示）
TYPE_LABELS: dict[str, str] = {
    "company_payment": "对公付款单",
    "advance_payment": "预付款单",
    "batch_payment": "批量付款单",
    "expense": "费用报销单",
    "travel": "差旅报销单",
}


def get_schema(document_type: str) -> list[dict]:
    """返回该类型的字段定义（供前端动态表单渲染）。"""
    return [f for f in TYPE_FIELD_SCHEMAS.get(document_type, []) if f]


def validate_type_fields(document_type: str, values: dict) -> tuple[dict, list[str]]:
    """校验类型字段：必填、类型、百分数范围。返回 (清洗后的值, 错误列表)。"""
    cleaned: dict = {}
    errors: list[str] = []

    for field in get_schema(document_type):
        key, label = field["key"], field["label"]
        raw = values.get(key)
        if raw is None or raw == "":
            if field.get("required"):
                errors.append(f"{label} 必填")
            continue
        try:
            if field["type"] == "string":
                cleaned[key] = str(raw)
            elif field["type"] in ("number", "percent"):
                # JSON 列不能存 Decimal，转 float（规则侧用 Decimal(str(v)) 读取）
                cleaned[key] = float(_to_decimal(raw, label, errors))
            elif field["type"] == "date":
                cleaned[key] = _to_date(raw, label, errors)
        except Exception:
            errors.append(f"{label} 格式不正确")

    # P1-6：百分数必须在 0~100
    for field in get_schema(document_type):
        if field["type"] == "percent" and field["key"] in cleaned:
            if cleaned[field["key"]] > 100:
                errors.append(f"{field['label']} 应在 0~100")
    # P1-6：差旅开始日期不得晚于结束日期（ISO 字符串比较）
    if document_type == "travel" and cleaned.get("travel_start") and cleaned.get("travel_end"):
        try:
            if date.fromisoformat(cleaned["travel_start"]) > date.fromisoformat(cleaned["travel_end"]):
                errors.append("出差开始日期不能晚于结束日期")
        except ValueError:
            pass
    return cleaned, errors


def _to_decimal(raw, label: str, errors: list[str]) -> Decimal:
    try:
        d = Decimal(str(raw))
    except InvalidOperation:
        errors.append(f"{label} 不是有效数字")
        return Decimal(0)
    if d < 0:
        errors.append(f"{label} 不能为负")
    return d


def _to_date(raw, label: str, errors: list[str]) -> str:
    """日期转 ISO 字符串（JSON 列不能存 date 对象）。"""
    if isinstance(raw, date):
        return raw.isoformat()
    try:
        return date.fromisoformat(str(raw)[:10]).isoformat()
    except ValueError:
        errors.append(f"{label} 日期格式应为 YYYY-MM-DD")
        return str(raw)
