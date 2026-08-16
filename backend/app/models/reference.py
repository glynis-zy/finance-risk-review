# -*- coding: utf-8 -*-
"""参考数据（市场价/供应商/费用标准）与规则配置。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class MarketPriceReference(Base):
    __tablename__ = "market_price_references"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(String(128), index=True)
    specification: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_min: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    price_max: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    source_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date)


class SupplierProfile(TimestampMixin, Base):
    __tablename__ = "supplier_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    supplier_name: Mapped[str] = mapped_column(String(128))
    credit_status: Mapped[str] = mapped_column(String(16), default="normal")   # normal/warning/abnormal
    blacklist_status: Mapped[str] = mapped_column(String(16), default="normal")  # normal/blacklisted
    risk_tags_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # 风险标签
    bank_accounts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 收款账号历史


class ExpenseStandard(Base):
    __tablename__ = "expense_standards"
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_category: Mapped[str] = mapped_column(String(32), index=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    standard_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    effective_date: Mapped[date] = mapped_column(Date)


class RiskRule(TimestampMixin, Base):
    __tablename__ = "risk_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    rule_name: Mapped[str] = mapped_column(String(128))
    applies_to_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {document_types: [...]}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict] = mapped_column(JSON)  # 各规则阈值，见 architecture.md §10
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class SysParam(TimestampMixin, Base):
    """系统参数配置（规格 2.7.3：系统管理员维护系统参数）。"""
    __tablename__ = "sys_params"
    id: Mapped[int] = mapped_column(primary_key=True)
    param_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    param_value: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
