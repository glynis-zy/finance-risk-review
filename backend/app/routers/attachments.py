# -*- coding: utf-8 -*-
"""附件路由：上传 / 下载 / 删除 / 解析触发（规格 2.7.11）。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.perms import require_perm
from app.models.user import User
from app.services import attachment_service, parse_pipeline

router = APIRouter(prefix="/documents", tags=["attachments"])

_MEDIA = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


@router.post("/{document_id}/attachments")
async def upload_attachment(
    document_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    att = await attachment_service.upload(db, user, document_id, file)
    return {"id": att.id, "file_name": att.file_name, "parse_status": att.parse_status}


@router.get("/{document_id}/attachments/{attachment_id}")
def download_attachment(
    document_id: int,
    attachment_id: int,
    user: User = Depends(require_perm("document:view")),
    db: Session = Depends(get_db),
):
    """下载/预览（L2 鉴权后流式返回）。"""
    att = attachment_service.download(db, user, document_id, attachment_id)
    path = attachment_service.abs_path(att)
    if not path.exists():
        raise HTTPException(404, "文件已丢失")
    return FileResponse(path, filename=att.file_name,
                        media_type=_MEDIA.get(att.file_type, "application/octet-stream"))


@router.delete("/{document_id}/attachments/{attachment_id}")
def delete_attachment(
    document_id: int,
    attachment_id: int,
    user: User = Depends(require_perm("document:edit")),
    db: Session = Depends(get_db),
):
    attachment_service.delete(db, user, document_id, attachment_id)
    return {"ok": True}


@router.post("/{document_id}/attachments/{attachment_id}/parse")
async def trigger_parse(
    document_id: int,
    attachment_id: int,
    user: User = Depends(require_perm("document:view")),
    db: Session = Depends(get_db),
):
    """触发单个附件解析（失败置 manual_review，可重试）。"""
    att = attachment_service.download(db, user, document_id, attachment_id)
    result = await parse_pipeline.parse_attachment(db, att)
    db.commit()
    return {"parse_status": att.parse_status, **result}
