import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id, get_actor_role
from app.qc_delegation import is_qc_delegated
from app.helpers.project_resolver import resolve_project_name

router = APIRouter()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)


def _resolve_task(client, shot_id: int, task_id: int | None, actor_id: str):
    """task_id 指定時は当該タスクを探す。なければ None。
    殿御命 2026-06-05: get_task (full DTO with name field) を優先し fallback で get_tasks"""
    if task_id is None:
        return None
    # 優先: get_task で full DTO (name field 含む) 取得
    if hasattr(client, "get_task"):
        try:
            raw = client.get_task(task_id, actor_user_id=actor_id) or {}
            if raw and (raw.get("id") == task_id or raw.get("task_id") == task_id):
                # dict から attr-like object 作成 (name 含む)
                return type("_T", (), {
                    "task_id": raw.get("id") or task_id,
                    "shot_id": raw.get("shot_id") or shot_id,
                    "type": raw.get("type", "Unknown"),
                    "name": raw.get("name") or raw.get("type") or "task",
                    "status": raw.get("status", "open"),
                    "assignee_id": raw.get("assigned_to") or raw.get("assignee_id") or 0,
                    "thread_id": raw.get("thread_id"),
                })()
        except Exception:
            pass
    # fallback: get_tasks (CalendarTask DTO — name 無いが type は有)
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
    as_role: str | None = None,  # 殿御命 2026-06-05 (B 案): admin 限定 role preview
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
    # cmd_059④ 真因修正: task_id 指定時は「shot取得→task_idフィルタ」だと shot_id 不整合の
    # asset (cmd_058①と同種・shot_id=None の孤立task 等) が取得段階で漏れる。
    # get_assets_by_task(task_id) で直接取得する経路に変更 (根治・pages_shot.py と同方針)。
    asset_list = []
    try:
        if task_id and hasattr(client, "get_assets_by_task"):
            asset_list = list(client.get_assets_by_task(task_id, actor_user_id=actor_id) or [])
        elif hasattr(client, "get_shot_detail"):
            shot_dict = client.get_shot_detail(id, actor_user_id=actor_id) or {}
            asset_list = list(shot_dict.get("asset_list", []) or [])
            if task_id:
                asset_list = [a for a in asset_list if (a.get("task_id") if isinstance(a, dict) else getattr(a, "task_id", None)) == task_id]
        asset_list.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else "") or "", reverse=True)
    except Exception:
        asset_list = []

    # 殿御命 2026-06-05: project_members 取得 (mention 選択用・pages_shot.py から移植)
    project_members = []
    try:
        if project_id and hasattr(client, "get_team_members"):
            project_members = client.get_team_members(int(project_id), actor_user_id=actor_id) or []
        # fallback: project task の assignee + project_roles から user 集約
        if not project_members and project_id:
            user_name_map = {}
            try:
                for u in (client.get_users(actor_user_id=actor_id) or []):
                    if isinstance(u, dict):
                        uid = u.get("id") or u.get("user_id")
                        if uid is not None:
                            user_name_map[int(uid)] = u.get("name") or u.get("full_name") or (u.get("email") or "").split("@")[0] or f"uid {uid}"
            except Exception:
                pass
            seen_uids = set()
            # task assignees
            try:
                tasks_in_proj = client.get_tasks_by_project(int(project_id), actor_user_id=actor_id) if hasattr(client, "get_tasks_by_project") else []
            except Exception:
                tasks_in_proj = []
            for tk in (tasks_in_proj or []):
                a = (tk.get("assigned_to") if isinstance(tk, dict) else getattr(tk, "assignee_id", None)) if tk else None
                if a is not None and a not in seen_uids:
                    seen_uids.add(int(a))
                    project_members.append({"user_id": int(a), "name": user_name_map.get(int(a), f"user_{a}"), "role": ""})
            # project_roles の director/pm/lead も追加
            if hasattr(client, "get_project_roles"):
                try:
                    roles = client.get_project_roles(int(project_id), actor_user_id=actor_id) or {}
                    for rname, ruid in roles.items():
                        if ruid is None: continue
                        try:
                            ruid_int = int(ruid)
                        except (ValueError, TypeError):
                            continue
                        if ruid_int not in seen_uids:
                            seen_uids.add(ruid_int)
                            project_members.append({"user_id": ruid_int, "name": user_name_map.get(ruid_int, f"user_{ruid_int}"), "role": rname})
                        else:
                            # 既存 entry に role 上書き (role なしから role 付与)
                            for pm in project_members:
                                if pm.get("user_id") == ruid_int and not pm.get("role"):
                                    pm["role"] = rname
                except Exception:
                    pass
            # 加えて殿御本人 (admin) も含む (役 user.role admin)
            if hasattr(user, 'user_id') and user.user_id and int(user.user_id) not in seen_uids:
                seen_uids.add(int(user.user_id))
                project_members.append({"user_id": int(user.user_id), "name": getattr(user, 'name', '') or f"uid {user.user_id}", "role": getattr(user, 'role', '') or "admin"})
    except Exception:
        project_members = []

    return _templates.TemplateResponse(
        request=request,
        name="qc_viewer.html",
        context={
            "tasks": tasks, "shot_id": id, "project_name": project_name,
            "project_id": project_id, "seq_code": seq_code, "shot_code": shot_code,
            "task_id": task_id, "task_name": task_name,
            "project_members": project_members,  # 殿御命 2026-06-05: mention 選択用
            # 殿御命 2026-06-05 (B 案): admin role 元 user のみ as_role で別 role preview 可
            "role": (as_role if (as_role in ('director', 'pm', 'lead', 'user') and get_actor_role(actor_id) == 'admin') else get_actor_role(actor_id)),
            "actual_role": get_actor_role(actor_id),
            "preview_role": as_role if (as_role and get_actor_role(actor_id) == 'admin') else None,
            # 殿御命 2026-06-09 (案A): この依頼で mention された user は委任で Approve/Retake 可
            "can_qc": is_qc_delegated(actor_id, task_id=task_id, shot_id=id),
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "asset_list": asset_list,
            "selected_asset_id": asset_id,
            "task_thread_id": getattr(selected_task, "thread_id", None) if selected_task else None,
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
