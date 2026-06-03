"""書込BFF 10EP — calender_api_complete_list.md §8 実在EPのみ (捏造ゼロ)"""
from fastapi import APIRouter, Depends, Path, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id

router = APIRouter()


@router.post("/api/bff/retakes")
def post_retakes(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_retakes(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/shots/{id}/approve")
def post_shot_approve(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_shot_approve(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/look_distributions")
def post_look_distributions(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_look_distributions(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/timecards/clock_out")
def post_timecard_clock_out(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_timecard_clock_out(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/routines")
def post_routines(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_routines(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/change_requests")
def post_change_requests(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_change_requests(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/troubles")
def post_troubles(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_troubles(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/troubles/{id}/resolve")
def patch_trouble_resolve(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.patch_trouble_resolve(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/messages")
def post_messages(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_messages(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/notifications/{id}/read")
def patch_notification_read(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.patch_notification_read(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/look_distributions/{id}/accept")
def patch_look_distribution_accept(
    id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """Look 配布 受諾 (nibu 殿御回答 2026-06-01 F 高)"""
    client = get_calendar_client()
    result = client.patch_look_distribution_accept(id, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/look_distributions/{id}/complete")
def patch_look_distribution_complete(
    id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """Look 配布 完了通知 (nibu 殿御回答 2026-06-01 F 高)"""
    client = get_calendar_client()
    result = client.patch_look_distribution_complete(id, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/assets")
async def post_asset_upload(
    file: UploadFile = File(...),
    task_id: Optional[int] = Form(None),
    shot_id: Optional[int] = Form(None),
    version: Optional[str] = Form(None),
    submission_type: Optional[str] = Form(None),   # 殿御命 2026-06-03: 'qc' | 'review'
    mentions: Optional[str] = Form(None),           # 殿御命 2026-06-03: カンマ区切り uid/email
    actor_id: str = Depends(get_actor_id),
):
    """QC/review asset upload (殿御命 2026-06-01)
    multipart pass-through → Calendar POST /api/assets
    殿御命 2026-06-03: submission_type (qc/review) + mentions を受領 (Phase 1: log のみ・Phase 2 cmd で通知作成)
    殿御命 2026-06-03: QC/review は 500MB 上限 (実データ納品は別経路)"""
    client = get_calendar_client()
    content = await file.read()
    # server side size check (client side JS と二重防壁)
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large: {len(content)//1024//1024}MB > 500MB (QC/review max・実データ納品は別経路)")
    result = client.post_asset(
        file_data=content,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        actor_user_id=actor_id,
        task_id=task_id,
        shot_id=shot_id,
        version=version,
    )
    if submission_type in ("qc", "review"):
        # Phase 2: 通知作成ロジック (殿御命 2026-06-03)
        # project_id resolve: shot_id → get_shot_detail → project_id
        project_id = None
        if shot_id:
            shot_info = client.get_shot_detail(shot_id, actor_user_id=actor_id)
            project_id = shot_info.get("project_id")
        if submission_type == "qc":
            directors = client.get_project_directors(project_id, actor_user_id=actor_id) if project_id else []
            pms = client.get_project_pms(project_id, actor_user_id=actor_id) if project_id else []
            extra = [m.strip() for m in (mentions or "").split(",") if m.strip()]
            if not directors and not extra:
                raise HTTPException(status_code=400, detail="QC 提出: Director 未設定かつ mention 指定なし")
            notify_uids = list({str(u) for u in directors + pms} | set(extra))
        else:  # review
            mention_list = [m.strip() for m in (mentions or "").split(",") if m.strip()]
            pms = client.get_project_pms(project_id, actor_user_id=actor_id) if project_id else []
            notify_uids = list({*mention_list, *[str(u) for u in pms]})
        if notify_uids:
            sub_label = "QC" if submission_type == "qc" else "レビュー"
            notif_title = f"{sub_label} 提出 — {result.get('filename', file.filename or 'asset')}"
            notif_body = f"{actor_id} が asset を提出しました (shot_id={shot_id}, task_id={task_id})"
            client.send_notification_to_users(
                notify_uids, notif_title, notif_body, actor_user_id=actor_id
            )
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/tasks/{task_id}")
def patch_task(
    task_id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-03: task status / progress 更新
    Calendar PATCH /api/tasks/{id} pass-through (status: todo/in-progress/review/completed/delayed, progress: 0-100)"""
    client = get_calendar_client()
    payload = body or {}
    # validation
    if "status" in payload and payload["status"] not in ("todo", "in-progress", "review", "completed", "delayed"):
        raise HTTPException(status_code=400, detail=f"Invalid status: {payload['status']}")
    if "progress" in payload:
        try:
            p = int(payload["progress"])
            if p < 0 or p > 100:
                raise ValueError("range")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="progress must be 0-100")
    if not payload:
        raise HTTPException(status_code=400, detail="empty body")
    result = client.patch_task(task_id, payload, actor_user_id=actor_id) if hasattr(client, "patch_task") else {"ok": False, "reason": "client method not implemented"}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.delete("/api/bff/assets/{asset_id}")
def delete_asset_endpoint(
    asset_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-03: asset 削除 (nibu 殿 DELETE /api/assets/{id} pass-through)
    本人 or admin のみ可 (Calendar 側で 403 enforce)"""
    client = get_calendar_client()
    result = client.delete_asset(asset_id, actor_user_id=actor_id) if hasattr(client, "delete_asset") else {"ok": False, "reason": "client method not implemented"}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/me/avatar")
async def upload_my_avatar(
    file: UploadFile = File(...),
    actor_id: str = Depends(get_actor_id),
):
    """Avatar image upload → Calendar POST /api/me/avatar pass-through"""
    client = get_calendar_client()
    content = await file.read()
    result = client.post_my_avatar(
        file_data=content,
        filename=file.filename or "avatar",
        content_type=file.content_type or "application/octet-stream",
        actor_user_id=actor_id,
    )
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})
