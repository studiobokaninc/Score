"""cmd_372: QC ビューアーの動画名・ダウンロード名を「上げた時の元のファイル名」にする。

優先順: ①Score DB (asset_original_names) に控えた元の名 ②控えの無い既存 asset は保存名から切り出し。
②の保存名は Calendar が `shot_{S|none}_task_{T}_{version}_{アップロード名}` に改めたもの。
アップロード名は旧 JS が `{元の基底名}_{version}{拡張子}` に改めた場合がある (version が自動採番 vNNN[a-z] の時のみ戻す)。
"""
import re
from urllib.parse import quote

_PREFIX_RE = re.compile(r"^shot_(?:\d+|none)_task_(?:\d+|none)_")
_AUTO_VER_RE = re.compile(r"^v\d{3}[a-z]?$")


def legacy_original_name(file_path: str, version: str | None) -> str:
    """保存名から元の名を切り出す (控えの無い既存 asset 用)。"""
    bn = (file_path or "").replace("\\", "/").split("/")[-1]
    m = _PREFIX_RE.match(bn)
    if not m:
        return bn
    rest = bn[m.end():]
    ver = (version or "").strip()
    if ver and rest.startswith(ver + "."):
        return bn  # 元の名の部分が無い保存名 (shot_none_task_N_vNNN.png 等) は保存名のまま
    if ver and rest.startswith(ver + "_") and len(rest) > len(ver) + 1:
        rest = rest[len(ver) + 1:]
    if ver and _AUTO_VER_RE.match(ver):
        base, dot, ext = rest.rpartition(".")
        if not dot:
            base, ext = rest, ""
        suffix = "_" + ver
        if base.endswith(suffix) and len(base) > len(suffix):
            rest = base[: -len(suffix)] + (dot + ext if dot else "")
    return rest or bn


def attach_display_names(asset_list) -> None:
    """asset_list の各 dict へ display_name を付ける。DB が引けぬ時も切り出しで続ける。"""
    items = [a for a in (asset_list or []) if isinstance(a, dict)]
    stored: dict[int, str] = {}
    try:
        ids = [int(a["id"]) for a in items if a.get("id") is not None]
        if ids:
            from app.database import SessionLocal
            from app.models import AssetOriginalName
            db = SessionLocal()
            try:
                for r in db.query(AssetOriginalName).filter(AssetOriginalName.asset_id.in_(ids)).all():
                    stored[int(r.asset_id)] = r.original_name
            finally:
                db.close()
    except Exception:
        stored = {}
    for a in items:
        try:
            aid = int(a.get("id"))
        except (TypeError, ValueError):
            aid = None
        a["display_name"] = (stored.get(aid) if aid is not None else None) or legacy_original_name(
            a.get("file_path") or "", a.get("version"))


def get_stored_original_name(asset_id: int) -> str | None:
    """Score DB に控えた元の名。無い・引けぬ時は None。"""
    try:
        from app.database import SessionLocal
        from app.models import AssetOriginalName
        db = SessionLocal()
        try:
            row = db.get(AssetOriginalName, int(asset_id))
            return row.original_name if row is not None and row.original_name else None
        finally:
            db.close()
    except Exception:
        return None


def save_original_name(asset_id: int, original_name: str) -> None:
    from app.database import SessionLocal
    from app.models import AssetOriginalName
    name = (original_name or "").replace("\\", "/").split("/")[-1].strip()
    if not name:
        return
    db = SessionLocal()
    try:
        row = db.get(AssetOriginalName, int(asset_id))
        if row is None:
            db.add(AssetOriginalName(asset_id=int(asset_id), original_name=name))
        else:
            row.original_name = name
        db.commit()
    finally:
        db.close()


def content_disposition(filename: str) -> str:
    """日本語・空白・引用符を含む名でも化けぬ Content-Disposition (RFC 6266/5987)。"""
    ascii_fallback = re.sub(r'[^A-Za-z0-9._-]', "_", filename.encode("ascii", "ignore").decode()) or "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
