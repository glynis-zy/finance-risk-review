# -*- coding: utf-8 -*-
"""单据相关 Pydantic 模型。"""
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    document_type: str
    applicant_department: str
    budget_department: str
    payee_name: str
    payee_account: str
    expense_category: str
    total_amount: Decimal = Field(gt=0)   # P1-6：金额必须 > 0，防规则引擎除零
    currency: str = "CNY"
    apply_date: date
    reason_text: str = ""
    type_fields: dict[str, Any] = {}


class DocumentUpdate(BaseModel):
    applicant_department: str | None = None
    budget_department: str | None = None
    payee_name: str | None = None
    payee_account: str | None = None
    expense_category: str | None = None
    total_amount: Decimal | None = None
    currency: str | None = None
    apply_date: date | None = None
    reason_text: str | None = None
    type_fields: dict[str, Any] | None = None


class LineItemCreate(BaseModel):
    item_type: str = "expense"     # expense / payment / transport / hotel / meal
    item_name: str
    specification: str | None = None   # 规格：市场价规则维度
    expense_date: date | None = None
    expense_location: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)     # P1-6
    unit_price: Decimal | None = Field(default=None, ge=0)   # P1-6
    amount: Decimal = Field(gt=0)                            # P1-6
    remark: str | None = None


class LineItemUpdate(BaseModel):
    item_name: str | None = None
    specification: str | None = None
    expense_date: date | None = None
    expense_location: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, gt=0)
    remark: str | None = None


class LineItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_type: str
    item_name: str
    specification: str | None
    expense_date: date | None
    expense_location: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal
    remark: str | None


class DocumentOut(BaseModel):
    """DTO：API 侧叫 `type_fields`，DB 列叫 `type_fields_json`（P0-2，双向正确）。"""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    document_type: str
    document_no: str
    applicant_id: int
    applicant_department: str
    budget_department: str
    payee_name: str
    payee_account: str
    expense_category: str
    total_amount: Decimal
    currency: str
    apply_date: date
    reason_text: str
    document_status: str
    current_version: int
    type_fields: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias="type_fields_json",   # 读 ORM 的 type_fields_json
        serialization_alias="type_fields",     # 序列化回 type_fields
    )


class AmountComparisonOut(BaseModel):
    """金额核对面板数据（规格 2.7.13 金额核对）。"""
    document_total: Decimal
    line_items_total: Decimal
    invoice_total: Decimal
    contract_amount: Decimal | None
    payment_amount: Decimal
    differences: dict[str, Decimal | None] = {}
