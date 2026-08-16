# -*- coding: utf-8 -*-
"""单据服务：CRUD / 复制 / 提交 / 撤回 / 作废 / 明细 / 状态机。

设计口径（架构文档 §7 / §9 / function-map §2.1）：
- L2 数据权限：`_ensure_visible`（本人/任务/全部）
- L3 状态权限：`_guard` 状态守卫表，状态不合法抛 409
- 提交：校验 → 快照新版本 → 状态 pending_review → 建审批实例 + 分析任务
"""
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.scopes import get_role_codes, visible_document_ids
from app.document_schemas import REQUIRED_ATTACHMENTS, TYPE_LABELS, validate_type_fields
from app.models.analysis import AnalysisTask
from app.models.attachment import DocumentAttachment
from app.models.document import (
    DOCUMENT_TYPES,
    DocumentLineItem,
    DocumentStatusLog,
    DocumentVersion,
    FinancialDocument,
)
from app.models.user import User
from app.models.workflow import ApprovalInstance, ApprovalTask
from app.schemas.document import (
    DocumentCreate,
    DocumentOut,
    DocumentUpdate,
    LineItemCreate,
    LineItemUpdate,
)
from app.services import analysis_service, audit_service, workflow_service

# 状态常量
DRAFT, PENDING, REVIEWING, RETURNED = "draft", "pending_review", "reviewing", "returned"
APPROVED, REJECTED, WITHDRAWN, VOIDED = "approved", "rejected", "withdrawn", "voided"

# L3 状态守卫表：动作 → 允许的当前状态
GUARD: dict[str, set[str]] = {
    "edit": {DRAFT, RETURNED},
    "submit": {DRAFT},
    "withdraw": {PENDING},
    "void": {DRAFT, PENDING},
}

_TYPE_ABBR = {
    "company_payment": "CP", "advance_payment": "AP", "batch_payment": "BP",
    "expense": "EX", "travel": "TR",
}


def _ensure_visible(db: Session, user: User, doc_id: int) -> FinancialDocument:
    """L2 数据权限：用户必须能看到该单据。"""
    ids = visible_document_ids(db, user)
    doc = db.get(FinancialDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "单据不存在")
    if ids is not None and doc.id not in ids:
        raise HTTPException(403, "无权访问该单据")
    return doc


def _ensure_owner(db: Session, user: User, doc: FinancialDocument) -> None:
    """申请人本人或管理员才可对单据执行写操作。"""
    roles = get_role_codes(db, user.id)
    if doc.applicant_id != user.id and "admin" not in roles:
        raise HTTPException(403, "仅申请人本人或管理员可操作该单据")


def _guard(doc: FinancialDocument, action: str) -> None:
    """L3 状态权限：动作在当前状态是否允许。"""
    allowed = GUARD.get(action, set())
    if doc.document_status not in allowed:
        raise HTTPException(409, f"状态 {doc.document_status} 不允许该操作")


def _transition(db: Session, doc: FinancialDocument, to_status: str,
                operator: User, remark: str = "") -> None:
    db.add(DocumentStatusLog(
        document_id=doc.id,
        from_status=doc.document_status,
        to_status=to_status,
        operator_id=operator.id,
        remark=remark,
    ))
    doc.document_status = to_status


def _snapshot(db: Session, doc: FinancialDocument, operator: User) -> None:
    """提交/重提交时保存当前版本快照，版本号 +1。"""
    db.add(DocumentVersion(
        document_id=doc.id,
        version_no=doc.current_version,
        document_snapshot_json={
            "type": doc.document_type,
            "no": doc.document_no,
            "type_fields": doc.type_fields_json or {},
            "total_amount": str(doc.total_amount),
            "status": doc.document_status,
            "snapshot_at": datetime.utcnow().isoformat(),
        },
        created_by=operator.id,
    ))
    doc.current_version += 1


def ensure_editable(db: Session, user: User, doc_id: int) -> FinancialDocument:
    """供附件/明细等服务复用：可见 + 本人/管理员 + draft/returned 状态。"""
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "edit")
    return doc


def ensure_viewable(db: Session, user: User, doc_id: int) -> FinancialDocument:
    """L2 校验：当前用户能查看该单据（用于创建分析、下载附件等）。"""
    return _ensure_visible(db, user, doc_id)


def _gen_no(db: Session, document_type: str) -> str:
    today = date.today().strftime("%Y%m%d")
    prefix = _TYPE_ABBR[document_type]
    like = f"{prefix}-{today}-%"
    count = len(db.scalars(
        select(FinancialDocument.id).where(FinancialDocument.document_no.like(like))
    ).all())
    return f"{prefix}-{today}-{count + 1:03d}"


def create(db: Session, user: User, payload: DocumentCreate) -> FinancialDocument:
    if payload.document_type not in DOCUMENT_TYPES:
        raise HTTPException(400, f"不支持的单据类型: {payload.document_type}")
    type_fields, errors = validate_type_fields(payload.document_type, payload.type_fields)
    if errors:
        raise HTTPException(400, "；".join(errors))

    doc = FinancialDocument(
        document_type=payload.document_type,
        document_no=_gen_no(db, payload.document_type),
        applicant_id=user.id,
        applicant_department=payload.applicant_department,
        budget_department=payload.budget_department,
        payee_name=payload.payee_name,
        payee_account=payload.payee_account,
        expense_category=payload.expense_category,
        total_amount=payload.total_amount,
        currency=payload.currency,
        apply_date=payload.apply_date,
        reason_text=payload.reason_text,
        type_fields_json=type_fields,
    )
    db.add(doc)
    db.flush()
    audit_service.log(db, user, "document:create", "document", str(doc.id))
    db.commit()
    db.refresh(doc)
    return doc


def update(db: Session, user: User, doc_id: int, payload: DocumentUpdate) -> FinancialDocument:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "edit")

    data = payload.model_dump(exclude_unset=True)
    if "type_fields" in data and data["type_fields"] is not None:
        type_fields, errors = validate_type_fields(doc.document_type, data["type_fields"])
        if errors:
            raise HTTPException(400, "；".join(errors))
        doc.type_fields_json = type_fields
        del data["type_fields"]
    for k, v in data.items():
        if v is not None:
            setattr(doc, k, v)
    audit_service.log(db, user, "document:update", "document", str(doc.id))
    db.commit()
    db.refresh(doc)
    return doc


def copy(db: Session, user: User, doc_id: int) -> FinancialDocument:
    src = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, src)
    new = FinancialDocument(
        document_type=src.document_type,
        document_no=_gen_no(db, src.document_type),
        applicant_id=user.id,
        applicant_department=src.applicant_department,
        budget_department=src.budget_department,
        payee_name=src.payee_name,
        payee_account=src.payee_account,
        expense_category=src.expense_category,
        total_amount=src.total_amount,
        currency=src.currency,
        apply_date=src.apply_date,
        reason_text=src.reason_text,
        type_fields_json=src.type_fields_json,
    )
    db.add(new)
    db.flush()
    audit_service.log(db, user, "document:copy", "document", f"{src.id}->{new.id}")
    db.commit()
    db.refresh(new)
    return new


def submit(db: Session, user: User, doc_id: int) -> FinancialDocument:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "submit")
    _validate_completeness(db, doc)

    _snapshot(db, doc, user)
    _transition(db, doc, PENDING, user, "提交审批")

    # 建审批实例 + 首个任务；建分析任务（异步）
    workflow_service.start_approval(db, doc)
    task = analysis_service.create_task(db, doc.id)

    audit_service.log(db, user, "document:submit", "document", str(doc.id))
    db.commit()
    analysis_service.enqueue(task.id)  # 提交后入队，后台跑解析→规则→报告
    db.refresh(doc)
    return doc


def withdraw(db: Session, user: User, doc_id: int) -> FinancialDocument:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "withdraw")

    # 审批实例 → cancelled，未处理任务 → cancelled（撤回语义，对应 2.7.5）
    instances = db.scalars(
        select(ApprovalInstance).where(
            ApprovalInstance.document_id == doc.id,
            ApprovalInstance.document_version == doc.current_version,
        )
    ).all()
    for inst in instances:
        if inst.instance_status == "running":
            inst.instance_status = "cancelled"
            inst.finished_at = datetime.utcnow()
            tasks = db.scalars(
                select(ApprovalTask).where(
                    ApprovalTask.instance_id == inst.id,
                    ApprovalTask.task_status == "pending",
                )
            ).all()
            for t in tasks:
                t.task_status = "cancelled"
                t.processed_at = datetime.utcnow()

    _transition(db, doc, WITHDRAWN, user, "申请人撤回")
    audit_service.log(db, user, "document:withdraw", "document", str(doc.id))
    db.commit()
    db.refresh(doc)
    return doc


def void(db: Session, user: User, doc_id: int) -> FinancialDocument:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "void")
    _transition(db, doc, VOIDED, user, "作废")
    audit_service.log(db, user, "document:void", "document", str(doc.id))
    db.commit()
    db.refresh(doc)
    return doc


def query(db: Session, user: User, *, document_type: str | None = None,
          document_no: str | None = None, applicant: str | None = None,
          department: str | None = None, status: str | None = None,
          date_from: date | None = None, date_to: date | None = None,
          page: int = 1, size: int = 20) -> tuple[list[FinancialDocument], int]:
    """L2 数据权限过滤后的分页查询。"""
    stmt = select(FinancialDocument)
    ids = visible_document_ids(db, user)
    if ids is not None:
        stmt = stmt.where(FinancialDocument.id.in_(ids))
    if document_type:
        stmt = stmt.where(FinancialDocument.document_type == document_type)
    if document_no:
        stmt = stmt.where(FinancialDocument.document_no.like(f"%{document_no}%"))
    if applicant:
        stmt = stmt.join(User, User.id == FinancialDocument.applicant_id).where(
            User.display_name.like(f"%{applicant}%"))
    if department:
        stmt = stmt.where(FinancialDocument.applicant_department == department)
    if status:
        stmt = stmt.where(FinancialDocument.document_status == status)
    if date_from:
        stmt = stmt.where(FinancialDocument.apply_date >= date_from)
    if date_to:
        stmt = stmt.where(FinancialDocument.apply_date <= date_to)
    stmt = stmt.order_by(FinancialDocument.id.desc())

    total = len(db.scalars(stmt).all())
    rows = db.scalars(stmt.offset((page - 1) * size).limit(size)).all()
    return list(rows), total


def get_detail(db: Session, user: User, doc_id: int) -> dict:
    doc = _ensure_visible(db, user, doc_id)
    line_items = db.scalars(
        select(DocumentLineItem).where(DocumentLineItem.document_id == doc.id)
    ).all()
    attachments = db.scalars(
        select(DocumentAttachment).where(DocumentAttachment.document_id == doc.id)
    ).all()
    versions = db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version_no.desc())
    ).all()
    instance = db.scalars(
        select(ApprovalInstance).where(ApprovalInstance.document_id == doc.id)
        .order_by(ApprovalInstance.id.desc())
    ).first()
    tasks = []
    if instance:
        tasks = db.scalars(
            select(ApprovalTask).where(ApprovalTask.instance_id == instance.id)
        ).all()
    return {
        "document": DocumentOut.model_validate(doc),
        "line_items": _line_items_out(line_items),
        "attachments": [_att_out(a) for a in attachments],
        "versions": [_ver_out(v) for v in versions],
        "approval": {
            "instance_status": instance.instance_status if instance else None,
            "tasks": [_task_out(t) for t in tasks],
        },
        "type_label": TYPE_LABELS.get(doc.document_type, doc.document_type),
    }


def add_line_item(db: Session, user: User, doc_id: int, payload: LineItemCreate) -> DocumentLineItem:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "edit")
    item = DocumentLineItem(document_id=doc.id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def update_line_item(db: Session, user: User, doc_id: int, item_id: int,
                     payload: LineItemUpdate) -> DocumentLineItem:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "edit")
    item = db.get(DocumentLineItem, item_id)
    if item is None or item.document_id != doc.id:
        raise HTTPException(404, "明细不存在")
    for k, v in payload.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(item, k, v)
    db.commit()
    db.refresh(item)
    return item


def delete_line_item(db: Session, user: User, doc_id: int, item_id: int) -> None:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "edit")
    item = db.get(DocumentLineItem, item_id)
    if item is None or item.document_id != doc.id:
        raise HTTPException(404, "明细不存在")
    db.delete(item)
    db.commit()


# ---------- 内部工具 ----------

def _validate_completeness(db: Session, doc: FinancialDocument) -> None:
    """提交前完整性校验（规格 2.7.5）：必需类型字段、必需附件数量。"""
    required_attachments = REQUIRED_ATTACHMENTS.get(doc.document_type, [])
    att_count = len(db.scalars(
        select(DocumentAttachment.id).where(
            DocumentAttachment.document_id == doc.id,
            DocumentAttachment.storage_status == "stored",
        )
    ).all())
    if required_attachments and att_count < len(required_attachments):
        raise HTTPException(400, f"缺少必需附件（需要: {'、'.join(required_attachments)}）")
    if doc.document_type == "batch_payment":
        item_count = len(db.scalars(
            select(DocumentLineItem.id).where(
                DocumentLineItem.document_id == doc.id,
                DocumentLineItem.item_type == "payment",
            )
        ).all())
        if item_count < 1:
            raise HTTPException(400, "批量付款单至少需要一条付款明细")


def _line_items_out(items: list[DocumentLineItem]) -> list[dict]:
    return [{
        "id": i.id, "item_type": i.item_type, "item_name": i.item_name,
        "expense_date": str(i.expense_date) if i.expense_date else None,
        "expense_location": i.expense_location,
        "quantity": str(i.quantity) if i.quantity is not None else None,
        "unit_price": str(i.unit_price) if i.unit_price is not None else None,
        "amount": str(i.amount), "remark": i.remark,
    } for i in items]


def _att_out(a: DocumentAttachment) -> dict:
    return {
        "id": a.id, "file_name": a.file_name, "file_type": a.file_type,
        "file_size": a.file_size, "storage_status": a.storage_status,
        "parse_status": a.parse_status, "document_version": a.document_version,
    }


def _ver_out(v: DocumentVersion) -> dict:
    return {
        "version_no": v.version_no, "created_by": v.created_by,
        "created_at": str(v.created_at), "snapshot": v.document_snapshot_json,
    }


def _task_out(t: ApprovalTask) -> dict:
    return {
        "id": t.id, "node_id": t.node_id, "approver_id": t.approver_id,
        "task_status": t.task_status, "review_comment": t.review_comment,
        "created_at": str(t.created_at), "processed_at": str(t.processed_at) if t.processed_at else None,
    }
