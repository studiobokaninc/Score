"""統合 Dashboard (殿御命 2026-06-01)
- role 別振分廃止・全 user 単一 dashboard
- 全 project 横断 + action 中心 (やる事があれば section 表示・空なら非表示)
- section: 本日の予定 / 本日のタスク / QC 依頼 / トラブル対応 / マイルストーン
"""
import os
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.adapters.dto import CalendarUser
from app.deps import get_actor_id, get_actor_role
from app.i18n import get_translator, get_time_greeting_key, t

router = APIRouter()
_templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader("app/templates"), cache_size=0)
)


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


@router.get("/dashboard")
def read_dashboard(
    request: Request,
    lang: str = "ja",
    actor_id: str = Depends(get_actor_id),
):
    """統合 Dashboard — 全 user 共通 layout・action 中心 (count > 0 で section 表示)"""
    client = get_calendar_client()
    actor_uid = int(actor_id) if actor_id and actor_id.isdigit() else 0
    today_jst = datetime.now(timezone(timedelta(hours=9))).date()
    today_str = today_jst.strftime("%Y-%m-%d")
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"][today_jst.weekday()]

    user = _safe(lambda: client.get_me(actor_user_id=actor_id), None)
    if user is None:
        user = CalendarUser(user_id=0, email="", role="", name="")

    user_projects = _safe(lambda: client.get_my_projects(actor_user_id=actor_id), []) or []
    project_name_map = {p.get("id"): p.get("name", "") for p in user_projects if isinstance(p, dict) and p.get("id") is not None}

    # ===== my_tasks: 全 project 横断 (get_my_tasks 1 call で完結) =====
    raw_tasks = []
    if hasattr(client, "get_my_tasks"):
        raw_tasks = _safe(lambda: client.get_my_tasks(actor_user_id=actor_id), []) or []
    my_tasks = []
    for tk in raw_tasks:
        if not isinstance(tk, dict):
            continue
        status = (tk.get("status") or "").lower()
        if status in ("done", "approved", "完了", "completed", "complete"):
            continue  # 完了済は除外
        pid = tk.get("project_id")
        my_tasks.append({
            "task_id": tk.get("id") or tk.get("task_id"),
            "shot_id": tk.get("shot_id"),
            "shot_code": tk.get("shotID") or tk.get("shot_code") or tk.get("name", ""),
            "seq_code": tk.get("seqID") or tk.get("seq_code", ""),
            "task_type": tk.get("type") or tk.get("task_type", ""),
            "status": tk.get("status", ""),
            "priority": (tk.get("priority") or "").upper() if tk.get("priority") else "",
            "project_id": pid,
            "project_name": project_name_map.get(pid, ""),
            "due_date": (tk.get("due_date") or "")[:10],
        })
    _priority_order = {"retake": 0, "reviewing": 1, "open": 2, "todo": 2, "in_progress": 3}
    my_tasks.sort(key=lambda x: _priority_order.get((x.get("status") or "").lower(), 5))

    # ===== QC 依頼: 自分担当 task で retake / reviewing =====
    qc_requests = [t for t in my_tasks if (t.get("status") or "").lower() in ("retake", "reviewing")]

    # ===== troubles: 自分関連 =====
    troubles_raw = []
    if hasattr(client, "get_my_troubles"):
        troubles_raw = _safe(lambda: client.get_my_troubles(actor_user_id=actor_id), []) or []
    elif hasattr(client, "get_troubles"):
        troubles_raw = _safe(lambda: client.get_troubles(actor_user_id=actor_id), []) or []
    troubles = []
    for tr in troubles_raw:
        if not isinstance(tr, dict):
            continue
        if (tr.get("status") or "").lower() in ("resolved", "closed", "完了"):
            continue
        troubles.append({
            "id": tr.get("id"),
            "title": tr.get("title", ""),
            "status": tr.get("status", ""),
            "project_id": tr.get("project_id"),
            "project_name": project_name_map.get(tr.get("project_id"), ""),
        })

    # ===== my_retakes: Calendar 側 retake 一覧 =====
    my_retakes_raw = []
    if hasattr(client, "get_my_retakes"):
        my_retakes_raw = _safe(lambda: client.get_my_retakes(actor_user_id=actor_id), []) or []
    my_retakes = []
    for r in my_retakes_raw:
        if not isinstance(r, dict):
            continue
        my_retakes.append({
            "id": r.get("id"),
            "shot_id": r.get("shot_id"),
            "shot_code": r.get("shot_code") or r.get("shotID", ""),
            "task_id": r.get("task_id"),
            "task_type": r.get("task_type") or r.get("type", ""),
            "assignee_id": r.get("assignee_id") or r.get("assigned_to"),
            "project_id": r.get("project_id"),
            "project_name": project_name_map.get(r.get("project_id"), ""),
        })

    # ===== events: 本日予定 + 直近 30 日マイルストーン (全 project 横断) =====
    all_events = _safe(lambda: client.get_events(actor_user_id=actor_id), []) or []
    today_events = []
    upcoming_milestones = []
    next_30d = today_jst + timedelta(days=30)
    for ev in all_events:
        ev_date_str = (ev.get("date") or (ev.get("start_time") or "")[:10] or "")[:10]
        if not ev_date_str:
            continue
        # display_time inject (allDay + start_time)
        ev["display_time"] = None
        if not ev.get("allDay"):
            start = ev.get("start_time")
            if start:
                try:
                    ev["display_time"] = datetime.fromisoformat(start).strftime("%H:%M")
                except ValueError:
                    pass
        # project_name inject
        ev["project_name"] = project_name_map.get(ev.get("project_id"), "")
        try:
            ev_date = datetime.fromisoformat(ev_date_str).date()
        except ValueError:
            continue
        if ev_date == today_jst:
            today_events.append(ev)
        elif (ev.get("type") or "").lower() == "milestone" and today_jst < ev_date <= next_30d:
            ev["date_short"] = ev_date.strftime("%m/%d")
            upcoming_milestones.append(ev)
    upcoming_milestones.sort(key=lambda e: e.get("date") or "")

    # next_event: today_events 空時の直近未来 event 1 件
    next_event = None
    if not today_events:
        future = sorted(
            [ev for ev in all_events
             if (ev.get("date") or (ev.get("start_time") or "")[:10] or "")[:10] > today_str],
            key=lambda e: (e.get("date") or (e.get("start_time") or "")[:10] or "")[:10]
        )
        next_event = future[0] if future else None

    trans = get_translator(lang)
    greeting_suffix, greeting_emoji = get_time_greeting_key()
    role = get_actor_role(actor_id)
    return _templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "trans": trans,
            "t": t,
            "user_projects": user_projects,
            "role": role,
            "active": "dashboard",
            "greeting_key": f"dashboard.greeting.{greeting_suffix}",
            "greeting_emoji": greeting_emoji,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "today_str": today_str,
            "today_weekday_jp": weekday_jp,
            # 統合 dashboard 用 集約 data
            "my_tasks": my_tasks[:10],
            "my_tasks_total": len(my_tasks),
            "qc_requests": qc_requests[:5],
            "qc_requests_total": len(qc_requests),
            "troubles": troubles[:5],
            "troubles_total": len(troubles),
            "my_retakes": my_retakes[:5],
            "my_retakes_total": len(my_retakes),
            "today_events": today_events,
            "next_event": next_event,
            "upcoming_milestones": upcoming_milestones[:5],
            "project_name_map": project_name_map,
        },
    )
