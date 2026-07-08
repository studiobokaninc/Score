import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from typing import Optional

from app.adapters.calendar_factory import get_calendar_client
from app.adapters.dto import CalendarUser
from app.deps import get_actor_id
from app.helpers.task_status import (
    attach_status_meta, STATUS_PRIORITY, COMPLETED_STATUSES,
    WIP_STATUSES, STATUS_DEFAULT_PROGRESS,
)

router = APIRouter()
_templates = Jinja2Templates(
    env=Environment(
        loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
        cache_size=0,
    )
)


@router.get("/")
@router.get("/login")
def read_login(request: Request, error: Optional[str] = None, next: Optional[str] = None):
    context = {"error": error, "next": next}
    return _templates.TemplateResponse(request=request, name="login.html", context=context)


@router.get("/exit_report")
def read_exit_report(request: Request, mode: Optional[str] = None, actor_id: str = Depends(get_actor_id)):
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = CalendarUser(user_id=0, email="", role="", name="ユーザ")

    # 担当 task を集約 (Calendar /api/me/tasks 経由)
    # updated_at で sort して 新しい順・mode=previous は「昨日」range filter・current は active 全件
    my_tasks = []
    try:
        if hasattr(client, "get_my_tasks"):
            raw = client.get_my_tasks(actor_user_id=actor_id) or []
            for t in raw:
                if isinstance(t, dict):
                    my_tasks.append({
                        "task_id": t.get("id") or t.get("task_id"),
                        "shot_id": t.get("shot_id"),
                        "shot_code": t.get("shot_code") or t.get("name") or "",
                        "task_type": t.get("task_type") or t.get("type", ""),
                        "name": t.get("name", ""),
                        "status": t.get("status", ""),
                        "display_status": t.get("display_status", ""),
                        "updated_at": t.get("updated_at", ""),
                    })
                else:
                    my_tasks.append({
                        "task_id": getattr(t, "task_id", None),
                        "shot_id": getattr(t, "shot_id", None),
                        "shot_code": getattr(t, "name", ""),
                        "task_type": getattr(t, "type", ""),
                        "status": getattr(t, "status", ""),
                        "updated_at": getattr(t, "updated_at", ""),
                    })
        # Enrich each task with project_name / seq_code / shot_code via shot lookup
        try:
            projects = client.get_my_projects(actor_user_id=actor_id) or []
        except Exception:
            projects = []
        shot_map = {}
        project_name_map = {}
        for p in projects:
            pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
            pname = (p.get("name") if isinstance(p, dict) else getattr(p, "name", None)) or "-"
            if pid is None:
                continue
            project_name_map[pid] = pname
            try:
                shots = client.get_shots(pid, actor_user_id=actor_id) or []
            except Exception:
                shots = []
            for s in shots:
                sid = (s.get("id") if isinstance(s, dict) else getattr(s, "shot_id", None) or getattr(s, "id", None))
                if sid is None:
                    continue
                shot_map[sid] = {
                    "project_id": pid,
                    "project_name": pname,
                    "shot_code": (s.get("shot_code") if isinstance(s, dict) else getattr(s, "shot_code", None))
                                or (s.get("name") if isinstance(s, dict) else getattr(s, "name", None))
                                or f"SHOT_{int(sid):03d}",
                    "seq_code": (s.get("seq_code") if isinstance(s, dict) else getattr(s, "seq_code", None)) or "",
                }
        for t in my_tasks:
            info = shot_map.get(t.get("shot_id"), {})
            t["project_name"] = info.get("project_name", "")
            t["seq_code"] = info.get("seq_code", "")
            if not t.get("shot_code"):
                t["shot_code"] = info.get("shot_code") or (f"SHOT_{int(t['shot_id']):03d}" if t.get("shot_id") else "")
        # updated_at で 新しい順 sort (mode=previous 「昨日の作業記録」 = 直近更新)
        my_tasks.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    except Exception:
        # 旧 per-shot loop fallback
        try:
            actor_uid = int(actor_id) if actor_id and actor_id.isdigit() else 0
            projects = client.get_my_projects(actor_user_id=actor_id) or []
            for proj in projects:
                shots = client.get_shots(proj.get("id"), actor_user_id=actor_id) or []
                for shot in shots:
                    tasks = client.get_tasks(shot.shot_id, actor_user_id=actor_id) or []
                    for t in tasks:
                        if t.assignee_id == actor_uid and t.status != "deliver":
                            my_tasks.append({
                                "task_id": t.task_id,
                                "shot_code": shot.shot_code or shot.name,
                                "task_type": t.type,
                                "status": t.status,
                            })
        except Exception:
            my_tasks = []

    # 過去 7 日 timecards 履歴 (nibu 殿納品 2026-06-01 / GET /api/me/timecards)
    timecards_history = []
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        today_d = _dt.now(_tz(_td(hours=9))).date()
        seven_ago = today_d - _td(days=7)
        if hasattr(client, "get_timecards"):
            timecards_history = client.get_timecards(
                actor_user_id=actor_id,
                from_date=seven_ago.strftime("%Y-%m-%d"),
                to_date=today_d.strftime("%Y-%m-%d"),
                limit=20,
            ) or []
    except Exception:
        timecards_history = []

    my_tasks = attach_status_meta(my_tasks, client)  # cmd_075: status_color/status_label 動的付与
    common = {
        "user": user,
        "active": "exit_report",
        "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        "my_tasks": my_tasks,
        "timecards_history": timecards_history,
        # cmd_075: TaskStatus 新19値の判定定数を JS へ single-source で渡す (ハードコード禁止)
        "status_priority": STATUS_PRIORITY,
        "completed_statuses": list(COMPLETED_STATUSES),
        "wip_statuses": WIP_STATUSES,
        "status_default_progress": STATUS_DEFAULT_PROGRESS,
    }
    if mode == "current":
        return _templates.TemplateResponse(
            request=request,
            name="exit_report.html",
            context={**common, "mode": "current"},
        )
    return _templates.TemplateResponse(
        request=request,
        name="exit_report.html",
        context={**common, "mode": "previous"},
    )


@router.get("/index")
def read_index(request: Request):
    return _templates.TemplateResponse(request=request, name="index.html")
