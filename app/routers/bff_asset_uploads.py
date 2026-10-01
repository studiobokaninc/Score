"""cmd_252 (2026-09-17・殿御命): Asset Upload 新設枠 — 3 EP。

既存の /api/bff/assets (QC/Review 提出・Calendar 連携・通知+検分あり) とは
完全に別物。ここは Score 自身のローカル DB + ローカルファイルのみで完結し、
Calendar API 呼び出し・通知・検分の仕組みを一切呼び出さない。
上げる/並べる/閲覧する/ダウンロードするだけの軽い枠 (殿御命)。
"""
from pathlib import Path as _Path

from fastapi import APIRouter, Depends, HTTPException, Path, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from typing import Optional

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id, get_actor_project_role, get_db
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
    # cmd_258 殿御下命(23:07最終版): 21:03の定め(カットの無いタスクは拒否)は撤回。
    # 判別の単位はカットではなくタスクであり(殿「タスクがあればアップできる
    # ように」)、shot_id/shot_code の有無による拒否は行わない。案件跨ぎ混線の
    # 歯止め(shot_id==0時のtask_id絞り)は一覧側(get_asset_uploads_new)で維持。
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
    import sys as _sys
    try:
        db.add(row)
        db.flush()  # id 採番 (保存ファイル名に使う)
    except OperationalError as e:
        # subtask_258a・追加是正: db.flush() (INSERT) の時点で score.db 自体が
        # 書込不可 (要対応#263: score.db が root所有mode644でuvicorn実行ユーザ
        # bokanから書込めぬ) だと、従来はここが try/except の外にあり素の
        # Internal Server Error (詳細なし・実質「反応なし」) のまま画面へ
        # 抜けていた。save_upload() 側の握り潰し対策だけでは塞げていなかった
        # 穴 (実測で確認: INSERT時点で OperationalError('attempt to write a
        # readonly database') が発生し save_upload() へ到達すらしない)。
        db.rollback()
        print(f"[asset_uploads] db insert failed: {e!r}", file=_sys.stderr, flush=True)
        raise HTTPException(
            status_code=500,
            detail="データベースへの書込みに失敗しました(サーバ側DBが読み取り専用です)。管理者へご連絡ください。",
        )
    try:
        stored_filename = save_upload(row.id, _filename, content)
    except OSError as e:
        # 保存先ディレクトリの権限不足等で書込めない場合、従来は
        # 素の Internal Server Error (詳細なし) のみが返り、画面側には
        # "HTTP 500" としか表示されず実質「反応なし」に近い握り潰しになって
        # いた。ここで明示的に捕捉し、原因を握り潰さず利用者へ伝わる
        # detail を返す。
        db.rollback()
        print(f"[asset_uploads] save_upload failed for row.id={row.id}: {e!r}", file=_sys.stderr, flush=True)
        raise HTTPException(
            status_code=500,
            detail="ファイルの保存に失敗しました(サーバ側ストレージへ書込めません)。管理者へご連絡ください。",
        )
    row.stored_filename = stored_filename
    try:
        db.commit()
    except OperationalError as e:
        db.rollback()
        print(f"[asset_uploads] db commit failed: {e!r}", file=_sys.stderr, flush=True)
        raise HTTPException(
            status_code=500,
            detail="データベースへの書込みに失敗しました(サーバ側DBが読み取り専用です)。管理者へご連絡ください。",
        )
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
    task_id: Optional[int] = None,
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    # subtask_258c: shot_id==0 は「shotが無い」印であって特定のshotを指さぬため、
    # shot_id==0 の時に限り task_id でも絞り込む(案件跨ぎの見え方混線を防ぐ)。
    # shot_idがある場合の既存の振る舞い(shot単位で絞る)は変えない。
    query = db.query(UploadedAsset).filter(UploadedAsset.shot_id == shot_id)
    if shot_id == 0 and task_id is not None:
        query = query.filter(UploadedAsset.task_id == task_id)
    rows = query.order_by(UploadedAsset.created_at.desc()).all()
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


def _actor_can_access_shot(shot_id: int, actor_id: str) -> bool:
    """#276是正——入った者の権限で分かつ。
    Score の役職(director/pm/lead)はアカウント固定値ではなく案件(project_id)
    ごとに解決される作りであり、director/pm/lead 以外は一様に "user" を返す
    ため、役職名では「その案件の一般利用者」と「無関係な他人」を区別できない
    (score-san-ken-tougou-shuusei-keikakusho-2026-09-25 1-4節②)。ゆえに役職
    判定は使わず、Calendar 側 GET /api/me/shots/{id} (get_shot_detail) の
    project member 限定応答(非member は403・taskのassigneeでもmember登録が
    無ければ同様に403・pages_qc.py で実機確認済のパターンに倣う)にそのまま乗る。
    404(shot不在)・403(非member、raise_for_status経由の例外)いずれも
    「見せない」で扱う(同計画書1-3節)。"""
    client = get_calendar_client()
    try:
        shot_dict = client.get_shot_detail(shot_id, actor_user_id=actor_id) or {}
    except Exception:
        shot_dict = {}
    return bool(shot_dict)


def _resolve_shot_id_via_task(task_id: int, actor_id: str) -> Optional[int]:
    """#283是正——shot_id==0の行をtask_id経由でCalendar側の実shot_idへ引き当てる
    (score_get_task_member_check_investigation_20260925.md)。get_task自体が
    member限定で応ずるかは未実証・無条件応答寄りと見立てられているが、実際の
    関所はここでは置かず、引き当てた実shot_idを_actor_can_access_shot
    (get_shot_detail・member限定実証済)へそのまま橋渡しする(get_taskは
    「shot_idの引き当て役」に留める)ことで安全側に倒す。
    引き当て不能(404/例外/shot_id欠落・0/None)の場合はNoneを返し、
    呼び出し側は従来どおり未解決のまま通す(繕わない・#283計画書の指図どおり)。

    ★向きのある危うさ(2026-09-30・QC279C-1是正・計画書1-4節にも追記済):
    現状は get_task が無条件応答(member限定を課していない)と見立てられて
    おり、実際の関門は橋渡し先の _actor_can_access_shot 側にあるため塞がって
    いる。だが将来 get_task 自体が member 限定へ締まると、逆にこの橋渡しは
    緩む——get_task が非member呼び出しを例外で拒むようになった瞬間、その
    例外が「引き当て不能」(本来は task_id がshot非紐づき等の正当な未解決)
    と区別なく扱われ、_actor_can_access_shot による関係者限定チェックそのもの
    に到達せず通してしまう。上流(get_task)を安全側へ締めるほど下流(本関数)
    が緩むという逆説的な性質であり、get_task の認可仕様を変える際は本関数の
    見直しとセットで行うこと。"""
    client = get_calendar_client()
    try:
        task_dict = client.get_task(task_id, actor_user_id=actor_id) or {}
    except Exception:
        return None
    shot_id = task_dict.get("shot_id")
    return shot_id if shot_id else None


def _actor_can_access_project(project_id: int, actor_id: str) -> bool:
    """QC283B-1是正(2026-10-01)——shot/task経由で引き当てられぬがproject_idは
    持っておる行向けの関所。#279e(_actor_can_read_attendance)が組んだのと同じ
    道具(get_my_projects+get_actor_project_role)に乗せる。studio全体のadmin、
    その案件のdirector/pm/lead(auto-membership)、または明示的team member
    登録(get_my_projects)のいずれかであれば見せる(get_shot_detailのmember限定
    応答と同じ粒度に寄せた安全側の判定)。"""
    client = get_calendar_client()
    if get_actor_project_role(actor_id, project_id, client=client) in ("admin", "director", "pm", "lead"):
        return True
    try:
        projects = client.get_my_projects(actor_user_id=actor_id) or []
    except Exception:
        projects = []
    if isinstance(projects, dict):
        projects = projects.get("projects", [])
    for proj in projects:
        pid = proj.get("id") if isinstance(proj, dict) else None
        if pid is not None and int(pid) == int(project_id):
            return True
    return False


def _get_row_or_404(asset_upload_id: int, db: Session, actor_id: str) -> UploadedAsset:
    row = db.query(UploadedAsset).filter(UploadedAsset.id == asset_upload_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="asset_upload not found")
    path = resolve_path(row.stored_filename)
    if path is None:
        raise HTTPException(status_code=404, detail="stored file missing")
    if row.shot_id != 0:
        if not _actor_can_access_shot(row.shot_id, actor_id):
            raise HTTPException(status_code=403, detail="この案件の関係者ではないため閲覧できません")
        return row

    # shot_id==0——#283是正: task_idがあればまず実shot_idへの橋渡しを試みる。
    resolved_shot_id = None
    if row.task_id is not None:
        resolved_shot_id = _resolve_shot_id_via_task(row.task_id, actor_id)
    if resolved_shot_id is not None:
        if not _actor_can_access_shot(resolved_shot_id, actor_id):
            raise HTTPException(status_code=403, detail="この案件の関係者ではないため閲覧できません")
        return row

    # task_idが無い、またはtask→shotの橋渡しが不能——QC283B-1是正: project_idが
    # あれば#279eと同じ関所(_actor_can_access_project)で判ずる。これにより
    # 「task_idが無い行」「taskがshotに紐づかぬ行」の双方がproject_id単位で
    # 塞がる(project単位のmember判定手段がScoreに無い点も同じ土台で解消)。
    if row.project_id is not None:
        if not _actor_can_access_project(row.project_id, actor_id):
            raise HTTPException(status_code=403, detail="この案件の関係者ではないため閲覧できません")
        return row

    # project_idもtask_idも無い(またはtask_idはあるが橋渡し不能でproject_idも
    # 無い)行——判定材料そのものが無く意図的に未解決のまま通す(繕わない・
    # #283計画書の指図どおり・計画書記載の残る穴)。ADV279C-2是正(2026-09-30)
    # を踏襲し、通った事実だけはstderrへ残す。
    import sys as _sys3
    print(
        f"[asset_uploads #283] unresolved passthrough: "
        f"asset_upload_id={asset_upload_id} task_id={row.task_id} "
        f"project_id={row.project_id} actor_id={actor_id}",
        file=_sys3.stderr, flush=True,
    )
    return row


@router.get("/api/bff/asset_uploads/{asset_upload_id}/file")
def get_asset_upload_file(
    asset_upload_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    """閲覧用 (inline・img/video タグの src 等から直接参照)。"""
    row = _get_row_or_404(asset_upload_id, db, actor_id)
    path = resolve_path(row.stored_filename)
    return FileResponse(path=str(path), media_type=row.content_type or "application/octet-stream")


@router.get("/api/bff/asset_uploads/{asset_upload_id}/download")
def get_asset_upload_download(
    asset_upload_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    """ダウンロード用 (元ファイル名で Content-Disposition attachment)。"""
    row = _get_row_or_404(asset_upload_id, db, actor_id)
    path = resolve_path(row.stored_filename)
    return FileResponse(
        path=str(path),
        media_type=row.content_type or "application/octet-stream",
        filename=row.filename,
    )
