"""殿御命 2026-07-06 (cmd_068追記②③): サーバ側変換 (.exr→PNG / .mov→.mp4) を経た asset について、
ダウンロードは常に変換前の原本ファイルを返せるよう、原本を Score ローカルに保持する。
プレビュー用の変換後ファイルは従来どおり Calendar へ送信・保存する (表示はそのまま)。"""
import os
from pathlib import Path

_ORIGINALS_DIR = Path(__file__).parent.parent.parent / "uploads" / "originals"


def save_original(asset_id: int, filename: str, content: bytes) -> None:
    """変換前の原本バイト列を asset_id に紐づけて保存する。"""
    _ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = os.path.basename(filename or "original.bin").replace("/", "_")
    dest = _ORIGINALS_DIR / f"{asset_id}_{safe_name}"
    dest.write_bytes(content)


def find_original(asset_id: int) -> Path | None:
    """asset_id に対応する原本ファイルが保存されていればそのパスを返す。無ければ None。"""
    if not _ORIGINALS_DIR.is_dir():
        return None
    matches = sorted(_ORIGINALS_DIR.glob(f"{int(asset_id)}_*"))
    return matches[0] if matches else None


def has_original(asset_id) -> bool:
    try:
        return find_original(int(asset_id)) is not None
    except (TypeError, ValueError):
        return False


def resolve_download_url(asset: dict, demo_mode: bool) -> str:
    """asset dict (Calendar API 由来) から DL 用 URL を決定する。
    原本を保存済 (変換を経た asset) なら常に原本 DL route を返す。
    未保存 (通常アセット・無変換) なら従来どおり Calendar 直配信 URL を返す。"""
    asset_id = asset.get("id") if isinstance(asset, dict) else None
    if asset_id is not None and has_original(asset_id):
        return f"/api/bff/assets/{int(asset_id)}/original"
    bn = ((asset.get("file_path") or "") if isinstance(asset, dict) else "").split("/")[-1]
    if demo_mode or "sample_" in bn:
        return f"/static/assets/{bn}"
    return f"http://192.168.44.253:8001/static/assets/{bn}"
