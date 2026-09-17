"""cmd_252 (2026-09-17・殿御命): Asset Upload 新設枠のローカルファイル保存。
既存の asset_originals.py (既存アセット履歴の原本退避) とは別ディレクトリ・別用途。
通知・検分は一切挟まない。"""
import os
from pathlib import Path

_UPLOADS_DIR = Path(__file__).parent.parent.parent / "uploads" / "asset_uploads"


def save_upload(record_id: int, filename: str, content: bytes) -> str:
    """アップロードされたバイト列を record_id に紐づけて保存し、保存ファイル名を返す。"""
    _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(filename or "upload.bin").replace("/", "_")
    stored_filename = f"{record_id}_{safe_name}"
    dest = _UPLOADS_DIR / stored_filename
    dest.write_bytes(content)
    return stored_filename


def resolve_path(stored_filename: str) -> Path | None:
    p = _UPLOADS_DIR / stored_filename
    return p if p.is_file() else None
