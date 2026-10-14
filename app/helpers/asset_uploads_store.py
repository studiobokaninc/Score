"""cmd_252 (2026-09-17・殿御命): Asset Upload 新設枠のローカルファイル保存。
既存の asset_originals.py (既存アセット履歴の原本退避) とは別ディレクトリ・別用途。
通知・検分は一切挟まない。"""
import os
from pathlib import Path

# subtask_258a (2026-09-18): 当初は uploads/asset_uploads だったが、uploads/ は
# root 所有 (mode 755) のため uvicorn 実行ユーザ(bokan)からはサブディレクトリを
# 新設できず PermissionError (要対応#263と同根)。chown には sudo が要り
# サーバの実行ユーザの権限では直せぬため、uploads/ の外側 (score リポジトリ直下・
# bokan 所有で書込可) に置き場所を変えて回避する。既存 uploads/originals
# (別用途・別ディレクトリ) には一切影響しない。
_UPLOADS_DIR = Path(__file__).parent.parent.parent / "asset_uploads"


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


_ORIG_PREFIX = "original__"


def save_upload_original(record_id: int, filename: str, content: bytes) -> str:
    """cmd_323: 変換前の原本を、変換後(閲覧用)とは別名で同じ保管場所へ残す。"""
    return save_upload(record_id, _ORIG_PREFIX + (filename or "upload.bin"), content)


def find_upload_original(record_id: int):
    """cmd_323: 原本が保管されていればそのパスと元のファイル名を返す。無ければ None。"""
    if not _UPLOADS_DIR.is_dir():
        return None
    matches = sorted(_UPLOADS_DIR.glob(f"{int(record_id)}_{_ORIG_PREFIX}*"))
    if not matches:
        return None
    p = matches[0]
    return p, p.name[len(f"{int(record_id)}_{_ORIG_PREFIX}"):]
