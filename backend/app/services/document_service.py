# -*- coding: utf-8 -*-
"""单据服务：CRUD / 复制 / 提交 / 撤回 / 作废 / 明细 / 状态机。

设计口径（架构文档 §7 / §9 / function-map §2.1）：
- L2 数据权限：`_ensure_visible`（本人/任务/全部）
- L3 状态权限：`_guard` 状态守卫表，状态不合法抛 409
- 提交：校验 → 快照新版本 → 状态 pending_review → 建审批实例 + 分析任务
"""
from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.scopes import visible_document_ids
from app.document_schemas import REQUIRED_ATTACHMENTS, TYPE_LABELS, validate_type_fields
from app.models.analysis import AnalysisTask, ManualReview, ReviewReport, RiskFinding
from app.models.attachment import AttachmentParseResult, DocumentAttachment, InvoiceRecord
from app.models.document import (
    DOCUMENT_TYPES,
    DocumentLineItem,
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

from app.domain import access_policy, document_state
from app.domain.document_state import (
    DRAFT, PENDING, RETURNED, VOIDED, WITHDRAWN,
)

_TYPE_ABBR = {
    "company_payment": "CP", "advance_payment": "AP", "batch_payment": "BP",
    "expense": "EX", "travel": "TR",
}


def _ensure_visible(db: Session, user: User, doc_id: int) -> FinancialDocument:
    """L2：取单据并校验可见（委托 domain/access_policy）。"""
    doc = db.get(FinancialDocument, doc_id)
    if doc is None:
        raise HTTPException(404, "单据不存在")
    access_policy.ensure_visible(db, user, doc)
    return doc


def _ensure_owner(db: Session, user: User, doc: FinancialDocument) -> None:
    access_policy.ensure_owner(db, user, doc)


def _guard(doc: FinancialDocument, action: str) -> None:
    document_state.guard(doc, action)


def _transition(db: Session, doc: FinancialDocument, to_status: str,
                operator: User, remark: str = "") -> None:
    document_state.transition(db, doc, to_status, operator.id, remark)


def _snapshot(db: Session, doc: FinancialDocument, operator: User, version_no: int) -> None:
    """保存一次正式提交的版本快照（P0-3：version_no 与 current_version 对齐，无 off-by-one）。"""
    db.add(DocumentVersion(
        document_id=doc.id,
        version_no=version_no,
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


def delete(db: Session, user: User, doc_id: int) -> None:
    """删除单据：仅 draft/returned 状态的申请人本人/管理员可删；级联清理关联表与附件文件。"""
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "delete")

    # 1) 审批实例/任务
    instance_ids = db.scalars(
        select(ApprovalInstance.id).where(ApprovalInstance.document_id == doc_id)
    ).all()
    if instance_ids:
        db.execute(delete(ApprovalTask).where(ApprovalTask.instance_id.in_(instance_ids)))
        db.execute(delete(ApprovalInstance).where(ApprovalInstance.document_id == doc_id))

    # 2) 分析任务 → 报告 → 人工复核 / 风险项
    task_ids = db.scalars(
        select(AnalysisTask.id).where(AnalysisTask.document_id == doc_id)
    ).all()
    if task_ids:
        report_ids = db.scalars(
            select(ReviewReport.id).where(ReviewReport.task_id.in_(task_ids))
        ).all()
        if report_ids:
            db.execute(delete(ManualReview).where(ManualReview.report_id.in_(report_ids)))
        db.execute(delete(ReviewReport).where(ReviewReport.task_id.in_(task_ids)))
        db.execute(delete(RiskFinding).where(RiskFinding.task_id.in_(task_ids)))
        db.execute(delete(AnalysisTask).where(AnalysisTask.document_id == doc_id))
    # ReviewReport 也可能按 document_id 直接关联
    db.execute(delete(ReviewReport).where(ReviewReport.document_id == doc_id))

    # 3) 附件 → 解析结果/发票记录/物理文件
    attachments = db.scalars(
        select(DocumentAttachment).where(DocumentAttachment.document_id == doc_id)
    ).all()
    for att in attachments:
        db.execute(delete(AttachmentParseResult).where(
            AttachmentParseResult.attachment_id == att.id))
        db.execute(delete(InvoiceRecord).where(InvoiceRecord.attachment_id == att.id))
        path = Path(settings.file_storage_path) / att.file_path
        if path.exists():
            path.unlink()
        db.delete(att)

    # 4) 单据子表与单据本身
    db.execute(delete(DocumentLineItem).where(DocumentLineItem.document_id == doc_id))
    db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc_id))
    db.execute(delete(DocumentStatusLog).where(DocumentStatusLog.document_id == doc_id))

    db.delete(doc)
    audit_service.log(db, user, "document:delete", "document", str(doc_id))
    db.commit()


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
    # P1-12：复制明细（不复制附件/版本/审批/分析/报告/日志）
    for li in db.scalars(select(DocumentLineItem).where(
            DocumentLineItem.document_id == src.id)).all():
        db.add(DocumentLineItem(
            document_id=new.id, item_type=li.item_type, item_name=li.item_name,
            specification=li.specification, expense_date=li.expense_date,
            expense_location=li.expense_location, quantity=li.quantity,
            unit_price=li.unit_price, amount=li.amount, remark=li.remark,
        ))
    audit_service.log(db, user, "document:copy", "document", f"{src.id}->{new.id}")
    db.commit()
    db.refresh(new)
    return new


def submit(db: Session, user: User, doc_id: int) -> FinancialDocument:
    """提交/重提交：draft→pending_review（首次）；returned→pending_review（退回后重提交，
    同样走 新版本快照 + 新审批实例 + 新分析任务）。"""
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "submit")
    _validate_completeness(db, doc)

    is_resubmit = doc.document_status == RETURNED
    # 版本语义（P0-3）：current_version=最近一次正式提交版本；本次提交版本 = 上一版 + 1
    new_version = doc.current_version + 1
    _snapshot(db, doc, user, new_version)
    doc.current_version = new_version
    # 暂存附件（document_version=0）绑定到本次提交版本，可追溯进入系统的版本
    for a in db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == doc.id,
            DocumentAttachment.document_version == 0,
    )).all():
        a.document_version = new_version

    _transition(db, doc, PENDING, user, "退回后重新提交" if is_resubmit else "提交审批")

    # 建审批实例（document_version 取 current_version=本次版本）+ 分析任务
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

    analysis_service.cancel_for_document(db, doc.id)  # P0-5：取消未完成分析任务
    _transition(db, doc, WITHDRAWN, user, "申请人撤回")
    audit_service.log(db, user, "document:withdraw", "document", str(doc.id))
    db.commit()
    db.refresh(doc)
    return doc


def void(db: Session, user: User, doc_id: int) -> FinancialDocument:
    doc = _ensure_visible(db, user, doc_id)
    _ensure_owner(db, user, doc)
    _guard(doc, "void")

    if doc.document_status == PENDING:
        # P0-2：作废审批中单据 → 取消进行中审批实例与待处理任务
        instances = db.scalars(select(ApprovalInstance).where(
            ApprovalInstance.document_id == doc.id,
            ApprovalInstance.instance_status == "running",
        )).all()
        for inst in instances:
            inst.instance_status = "cancelled"
            inst.finished_at = datetime.utcnow()
            for t in db.scalars(select(ApprovalTask).where(
                    ApprovalTask.instance_id == inst.id,
                    ApprovalTask.task_status == "pending",
            )).all():
                t.task_status = "cancelled"
                t.processed_at = datetime.utcnow()
    # P0-5：取消该单据未完成的分析任务（含运行中，pipeline 合作式检查停止）
    analysis_service.cancel_for_document(db, doc.id)

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
    from app.repositories.attachment_repo import AttachmentRepository
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.workflow_repo import WorkflowRepository
    doc_repo, att_repo, wf_repo = DocumentRepository(db), AttachmentRepository(db), WorkflowRepository(db)
    line_items = doc_repo.line_items(doc.id)
    attachments = att_repo.list_by_document(doc.id)
    versions = doc_repo.versions(doc.id)
    instances = wf_repo.instances_of_document(doc.id)
    instance = instances[0] if instances else None
    tasks = wf_repo.tasks_of_instance(instance.id) if instance else []
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
    """提交前完整性校验（规格 2.7.5）：必需类型字段、必需附件【类别】（P1-2，不能靠数量蒙混）。

    附件类别来源：上传时按文件名识别的 `document_category` ∪ 已解析结果 `document_category`。
    """
    required_attachments = REQUIRED_ATTACHMENTS.get(doc.document_type, [])
    present: set[str] = set()
    for att in db.scalars(select(DocumentAttachment).where(
            DocumentAttachment.document_id == doc.id,
            DocumentAttachment.storage_status == "stored",
    )).all():
        if att.document_category:
            present.add(att.document_category)
    for cat in db.scalars(select(AttachmentParseResult.document_category).where(
            AttachmentParseResult.attachment_id.in_(
                select(DocumentAttachment.id).where(DocumentAttachment.document_id == doc.id)
            ))).all():
        if cat:
            present.add(cat)
    missing = [c for c in required_attachments if c not in present]
    if missing:
        raise HTTPException(400, f"缺少必需附件类别: {'、'.join(missing)}")
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
        "specification": i.specification,
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
