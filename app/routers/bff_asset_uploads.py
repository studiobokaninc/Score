"""cmd_252 (2026-09-17・殿御命): Asset Upload 新設枠 — 3 EP。

既存の /api/bff/assets (QC/Review 提出・Calendar 連携・通知+検分あり) とは
完全に別物。ここは Score 自身のローカル DB + ローカルファイルのみで完結し、
Calendar API 呼び出し・通知・検分の仕組みを一切呼び出さない。
上げる/並べる/閲覧する/ダウンロードするだけの軽い枠 (殿御命)。
"""
from pathlib import Path as _Path

from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_actor_id, get_db
from app.models import UploadedAsset
from app.helpers.asset_uploads_store import save_upload, resolve_path

router = APIRouter()

# 既存 QC/Review 提出 (/api/bff/assets) と同じ上限を踏襲 (殿の妥当な判断に委ねられた枠のため
# 既存基準に合わせる・極端な超過のみ拒否)
_MAX_BYTES = 500 * 1024 * 1024


@router.post("/api/bff/asset_uploads")
async def post_asset_upload_new(
    file: UploadFile = File(...),
    shot_id: int = Form(...),
    task_id: Optional[int] = Form(None),
    project_id: Optional[int] = Form(None),
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    content = await file.read()
    if len(content) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large: {len(content)//1024//1024}MB > 500MB")
    _filename = file.filename or "upload.bin"
    _content_type = file.content_type or "application/octet-stream"

    row = UploadedAsset(
        shot_id=shot_id,
        task_id=task_id,
        project_id=project_id,
        filename=_filename,
        stored_filename="",
        content_type=_content_type,
        size_bytes=len(content),
        uploaded_by=actor_id,
    )
    db.add(row)
    db.flush()  # id 採番 (保存ファイル名に使う)
    stored_filename = save_upload(row.id, _filename, content)
    row.stored_filename = stored_filename
    db.commit()
    db.refresh(row)
    return JSONResponse(content={
        "id": row.id,
        "shot_id": row.shot_id,
        "task_id": row.task_id,
        "filename": row.filename,
        "content_type": row.content_type,
        "size_bytes": row.size_bytes,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    })


@router.get("/api/bff/asset_uploads")
def get_asset_uploads_new(
    shot_id: int,
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(UploadedAsset)
        .filter(UploadedAsset.shot_id == shot_id)
        .order_by(UploadedAsset.created_at.desc())
        .all()
    )
    return JSONResponse(content=[
        {
            "id": r.id,
            "shot_id": r.shot_id,
            "task_id": r.task_id,
            "filename": r.filename,
            "content_type": r.content_type,
            "size_bytes": r.size_bytes,
            "uploaded_by": r.uploaded_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "view_url": f"/api/bff/asset_uploads/{r.id}/file",
            "download_url": f"/api/bff/asset_uploads/{r.id}/download",
        }
        for r in rows
    ])


def _get_row_or_404(asset_upload_id: int, db: Session) -> UploadedAsset:
    row = db.query(UploadedAsset).filter(UploadedAsset.id == asset_upload_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="asset_upload not found")
    path = resolve_path(row.stored_filename)
    if path is None:
        raise HTTPException(status_code=404, detail="stored file missing")
    return row


@router.get("/api/bff/asset_uploads/{asset_upload_id}/file")
def get_asset_upload_file(
    asset_upload_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    """閲覧用 (inline・img/video タグの src 等から直接参照)。"""
    row = _get_row_or_404(asset_upload_id, db)
    path = resolve_path(row.stored_filename)
    return FileResponse(path=str(path), media_type=row.content_type or "application/octet-stream")


@router.get("/api/bff/asset_uploads/{asset_upload_id}/download")
def get_asset_upload_download(
    asset_upload_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    """ダウンロード用 (元ファイル名で Content-Disposition attachment)。"""
    row = _get_row_or_404(asset_upload_id, db)
    path = resolve_path(row.stored_filename)
    return FileResponse(
        path=str(path),
        media_type=row.content_type or "application/octet-stream",
        filename=row.filename,
    )
