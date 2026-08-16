# -*- coding: utf-8 -*-
"""附件服务：上传（类型/大小校验 + 哈希）/ 下载 / 删除。

规格 2.7.14：附件上传下载必须校验文件类型、大小、路径和访问权限。
"""
import hashlib
import uuid
from pathlib import Path

from fastapi import HTTPException
from fastapi.datastructures import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.scopes import visible_document_ids
from app.models.attachment import DocumentAttachment
from app.models.document import FinancialDocument
from app.models.user import User
from app.services import audit_service, document_service

ALLOWED_TYPES = {"pdf", "png", "jpg", "jpeg"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def upload(db: Session, user: User, doc_id: int, file: UploadFile) -> DocumentAttachment:
    document_service.ensure_editable(db, user, doc_id)  # L2 + L3

    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if file.filename else ""
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"仅支持 {sorted(ALLOWED_TYPES)} 格式")
    data = await file.read()
    if len(data) > MAX_SIZE:
        raise HTTPException(400, "文件超过 10MB 上限")
    if not data:
        raise HTTPException(400, "空文件")

    # 存储路径：data/uploads/<doc_id>/<uuid>.<ext>（路径可控，禁止用户输入进路径）
    rel_dir = Path(str(doc_id))
    file_id = uuid.uuid4().hex
    rel_path = rel_dir / f"{file_id}.{ext}"
    abs_path = Path(settings.file_storage_path) / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(data)

    att = DocumentAttachment(
        document_id=doc_id,
        document_version=0,  # 上传后提交时取当前版本
        file_name=file.filename or f"{file_id}.{ext}",
        file_type=ext,
        file_size=len(data),
        file_path=str(rel_path).replace("\\", "/"),
        file_hash=_sha256(data),
        storage_status="stored",
        parse_status="pending",
    )
    db.add(att)
    db.flush()
    audit_service.log(db, user, "attachment:upload", "attachment", str(att.id),
                      {"doc_id": doc_id, "file_name": att.file_name})
    db.commit()
    db.refresh(att)
    return att


def download(db: Session, user: User, doc_id: int, attachment_id: int) -> DocumentAttachment:
    """L2：单据可见才允许下载附件。"""
    ids = visible_document_ids(db, user)
    doc = db.get(FinancialDocument, doc_id)
    if doc is None or (ids is not None and doc.id not in ids):
        raise HTTPException(403, "无权访问该单据附件")
    att = db.get(DocumentAttachment, attachment_id)
    if att is None or att.document_id != doc_id:
        raise HTTPException(404, "附件不存在")
    return att


def delete(db: Session, user: User, doc_id: int, attachment_id: int) -> None:
    document_service.ensure_editable(db, user, doc_id)  # L2 + L3
    att = db.get(DocumentAttachment, attachment_id)
    if att is None or att.document_id != doc_id:
        raise HTTPException(404, "附件不存在")
    # 删除文件 + 记录
    path = Path(settings.file_storage_path) / att.file_path
    if path.exists():
        path.unlink()
    db.delete(att)
    audit_service.log(db, user, "attachment:delete", "attachment", str(attachment_id))
    db.commit()


def abs_path(att: DocumentAttachment) -> Path:
    return Path(settings.file_storage_path) / att.file_path
