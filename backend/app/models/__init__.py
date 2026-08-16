# -*- coding: utf-8 -*-
"""统一导入所有模型，确保 Base.metadata 完整（init_db 依赖）。"""
from app.models.analysis import AnalysisTask, ManualReview, ReviewReport, RiskFinding
from app.models.attachment import AttachmentParseResult, DocumentAttachment, InvoiceRecord
from app.models.audit import AuditLog
from app.models.document import (
    DOCUMENT_TYPES,
    DocumentLineItem,
    DocumentStatusLog,
    DocumentVersion,
    FinancialDocument,
)
from app.models.reference import (
    ExpenseStandard,
    MarketPriceReference,
    RiskRule,
    SupplierProfile,
    SysParam,
)
from app.models.revoked import RevokedToken
from app.models.session import ReviewSession, SessionMessage
from app.models.user import Permission, Role, RolePermission, User, UserRole
from app.models.workflow import (
    ApprovalInstance,
    ApprovalTask,
    ApprovalWorkflow,
    ApprovalWorkflowNode,
)

__all__ = [
    "User", "Role", "Permission", "UserRole", "RolePermission",
    "FinancialDocument", "DocumentVersion", "DocumentLineItem", "DocumentStatusLog",
    "DocumentAttachment", "AttachmentParseResult", "InvoiceRecord",
    "ReviewSession", "SessionMessage",
    "ApprovalWorkflow", "ApprovalWorkflowNode", "ApprovalInstance", "ApprovalTask",
    "AnalysisTask", "RiskFinding", "ReviewReport", "ManualReview",
    "MarketPriceReference", "SupplierProfile", "ExpenseStandard", "RiskRule",
    "SysParam", "RevokedToken", "AuditLog", "DOCUMENT_TYPES",
]
