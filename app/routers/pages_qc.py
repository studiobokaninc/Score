import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id, get_actor_role
from app.helpers.project_resolver import resolve_project_name

router = APIRouter()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)


def _resolve_task(client, shot_id: int, task_id: int | None, actor_id: str):
    """task_id 指定時は当該タスクを探す。なければ None。"""
    if task_id is None:
        return None
    try:
        tlist = client.get_tasks(shot_id, actor_user_id=actor_id) or []
        for t in tlist:
            if getattr(t, "task_id", None) == task_id:
                return t
    except Exception:
        pass
    return None


@router.get("/qc/{id}")
def get_qc_viewer(
    id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
    task_id: int | None = None,
    asset_id: int | None = None,
):
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        tasks = client.get_tasks(id, actor_user_id=actor_id)
    except httpx.ConnectError:
        tasks = []
    shot = client.get_shot(id, actor_user_id=actor_id)
    project_name = resolve_project_name(shot.project_id, actor_id) if shot else "-"
    project_id = shot.project_id if shot else None
    seq_code = getattr(shot, "seq_code", None) if shot else None
    shot_code = getattr(shot, "shot_code", None) if shot else None
    selected_task = _resolve_task(client, id, task_id, actor_id)
    # 殿御命 2026-06-03: breadcrumb は task.name (例 "Compositing") を優先・fallback で type
    task_name = (getattr(selected_task, "name", None) or getattr(selected_task, "type", None)) if selected_task else None

    # 殿御命 2026-06-03 Phase B: real asset_list 取得 (nibu 仕様: /api/me/shots/{id}.asset_list)
    asset_list = []
    try:
        if hasattr(client, "get_shot_detail"):
            shot_dict = client.get_shot_detail(id, actor_user_id=actor_id) or {}
            asset_list = list(shot_dict.get("asset_list", []) or [])
        if task_id and asset_list:
            asset_list = [a for a in asset_list if (a.get("task_id") if isinstance(a, dict) else getattr(a, "task_id", None)) == task_id]
        asset_list.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else "") or "", reverse=True)
    except Exception:
        asset_list = []

    return _templates.TemplateResponse(
        request=request,
        name="qc_viewer.html",
        context={
            "tasks": tasks, "shot_id": id, "project_name": project_name,
            "project_id": project_id, "seq_code": seq_code, "shot_code": shot_code,
            "task_id": task_id, "task_name": task_name,
            "role": get_actor_role(actor_id),
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "asset_list": asset_list,
            "selected_asset_id": asset_id,
            # 暫定: Calendar コメント/リテイク schema 確認中 → 空リスト (cmd_475 Phase A)
            "comment_list": [],
            "retake_history": [],
        },
    )


@router.post("/qc/{id}/retake")
def post_qc_retake(
    id: int,
    retake_comment: str = Form(...),
    asset_id: int | None = Form(None),
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    shot = client.get_shot(id, actor_user_id=actor_id)
    project_id = shot.project_id if shot else None
    directors = client.get_project_directors(project_id, actor_user_id=actor_id) if project_id else []
    pms = client.get_project_pms(project_id, actor_user_id=actor_id) if project_id else []
    notify_uids = list({str(u) for u in directors + pms})
    if notify_uids:
        title = f"Retake 発行 — SHOT_{id}" + (f" asset_id={asset_id}" if asset_id else "")
        body = f"{actor_id} が Retake を発行しました: {retake_comment[:120]}"
        client.send_notification_to_users(notify_uids, title, body, actor_user_id=actor_id)
    return {"ok": True, "notified": len(notify_uids)}


@router.get("/reference/{id}")
def get_reference_viewer(
    id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
    task_id: int | None = None,
):
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        tasks = client.get_tasks(id, actor_user_id=actor_id)
    except httpx.ConnectError:
        tasks = []
    shot = client.get_shot(id, actor_user_id=actor_id)
    project_name = resolve_project_name(shot.project_id, actor_id) if shot else "-"
    project_id = shot.project_id if shot else None
    seq_code = getattr(shot, "seq_code", None) if shot else None
    selected_task = _resolve_task(client, id, task_id, actor_id)
    task_name = selected_task.type if selected_task else None

    return _templates.TemplateResponse(
        request=request,
        name="reference_viewer.html",
        context={
            "tasks": tasks, "shot_id": id, "project_name": project_name,
            "project_id": project_id, "seq_code": seq_code,
            "task_id": task_id, "task_name": task_name,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
        },
    )
