import os
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")

@router.get("/lead_dashboard")
def get_lead_dashboard(request: Request, actor_id: str = Depends(get_actor_id)):
    # 殿御命 2026-06-01: 統合 dashboard 化・本 route は /dashboard へ redirect
    return RedirectResponse(url="/dashboard", status_code=302)
    role = get_actor_role(actor_id)
    if role not in ("lighting_lead", "lead", "kato"):
        raise HTTPException(status_code=403, detail="lighting_lead role required")
    client = get_calendar_client()
    actor_uid = int(actor_id) if actor_id and actor_id.isdigit() else 0
    is_lighting_lead = role in ("lighting_lead", "kato")

    # ===== 軽量化 (2026-05-27 殿御指示): API call を最小化 =====
    # 旧版は get_my_projects × 3 + per-shot loop × N = 150+ HTTP request で重かった。
    # 新版は ~7 直列 + projects_list 1 回・oversight は get_my_tasks ベースで N+1 排除。

    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    # 1 まとめ fetch (重複排除)
    user = _safe(lambda: client.get_me(actor_user_id=actor_id), None)
    distributions = _safe(lambda: client.get_look_distributions(actor_user_id=actor_id), []) or []
    troubles = _safe(lambda: client.get_troubles(actor_user_id=None), []) or []
    all_events = _safe(lambda: client.get_events(actor_user_id=actor_id), []) or []
    projects_list_raw = _safe(lambda: client.get_my_projects(actor_user_id=actor_id), []) or []
    raw_tasks = _safe(lambda: client.get_my_tasks(actor_user_id=actor_id) if hasattr(client, "get_my_tasks") else [], []) or []

    # 本日の予定 — events filter (本日 date + actor 該当 or チーム共有)
    today_jst = datetime.now(timezone(timedelta(hours=9))).date()
    today_str = today_jst.strftime("%Y-%m-%d")
    today_events = []
    for ev in all_events:
        ev_date = (ev.get("date") or (ev.get("start_time") or "")[:10] or "")[:10]
        if ev_date != today_str:
            continue
        uids = ev.get("user_ids") or []
        if actor_uid in uids or not uids:
            # nibu 殿御回答 2026-06-01: time は @property・allDay=True 時 null
            # → start_time + allDay 判定で display_time 補完
            ev["display_time"] = None
            if not ev.get("allDay"):
                start = ev.get("start_time")
                if start:
                    try:
                        ev["display_time"] = datetime.fromisoformat(start).strftime("%H:%M")
                    except ValueError:
                        ev["display_time"] = ev.get("time")
            today_events.append(ev)

    # 議事録 — 全 project を loop だが project あたり 1 HTTP call のみ (test data filter)
    _TEST_TITLE_PATTERNS = ("テスト", "test", "更新されたテスト", "サンプル", "ダミー", "dummy")
    meetings = []
    for proj in projects_list_raw:
        pid = proj.get("id")
        mtgs = _safe(lambda p=pid: client.get_meetings(p, actor_user_id=actor_id), []) or []
        for m in mtgs:
            title = (m.get("title") or "").lower()
            if any(p.lower() in title for p in _TEST_TITLE_PATTERNS):
                continue
            meetings.append(m)
    meetings.sort(key=lambda m: m.get("date", ""), reverse=True)

    # my_tasks — get_my_tasks 1 call で完結 (旧 N+1 排除)
    my_tasks = []
    for tk in raw_tasks:
        if isinstance(tk, dict):
            my_tasks.append({
                "task_id": tk.get("id") or tk.get("task_id"),
                "id": tk.get("id"),
                "project_id": tk.get("project_id"),
                "shot_id": tk.get("shot_id"),
                "shot_code": tk.get("shotID") or tk.get("shot_code"),
                "seq_code": tk.get("seqID") or tk.get("seq_code"),
                "task_type": tk.get("type") or tk.get("task_type"),
                "type": tk.get("type"),
                "name": tk.get("name", ""),
                "status": tk.get("status", ""),
                "updated_at": tk.get("updated_at", ""),
                "assignee_id": tk.get("assigned_to") or tk.get("assignee_id"),
            })
        else:
            my_tasks.append({
                "task_id": getattr(tk, "task_id", None),
                "shot_code": getattr(tk, "name", "task"),
                "task_type": getattr(tk, "type", ""),
                "status": getattr(tk, "status", ""),
                "assignee_id": getattr(tk, "assignee_id", None),
            })
    # sort: active 上位・updated_at 新しい順
    _prio_order = {"retake": 0, "reviewing": 1, "open": 2, "todo": 2, "in_progress": 3, "delayed": 3, "approved": 9, "completed": 9, "done": 9}
    active_tasks = [t for t in my_tasks if _prio_order.get(t.get("status", ""), 5) < 9]
    done_tasks = [t for t in my_tasks if _prio_order.get(t.get("status", ""), 5) >= 9]
    active_tasks.sort(key=lambda t: (_prio_order.get(t.get("status", ""), 5), t.get("updated_at", "")), reverse=False)
    done_tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    my_tasks = active_tasks + done_tasks

    # 部署 oversight — my_tasks の中から status filter (旧 N+1 query を排除)
    # 注: get_my_tasks は actor の関与する task しか返さないため、純粋な oversight (他人の task) は
    # 取得できないが、軽量化優先で my_tasks 内で filter 動作。本式は別 endpoint 追加要(後段)。
    review_targets = []
    team_retakes = []
    distribution_candidates = []
    for t in my_tasks:
        ttype = t.get("task_type", "") or ""
        if is_lighting_lead and ttype not in ("Look", "Lighting", "lighting"):
            continue
        is_self = (t.get("assignee_id") == actor_uid)
        entry = {
            "task_id": t.get("task_id"),
            "shot_id": t.get("shot_id"),
            "shot_code": t.get("shot_code") or t.get("name", "task"),
            "task_type": ttype,
            "status": t.get("status"),
            "assignee_id": t.get("assignee_id"),
        }
        if t.get("status") == "reviewing" and not is_self:
            review_targets.append(entry)
        elif t.get("status") == "retake" and not is_self:
            team_retakes.append(entry)
        if ttype == "Look" and t.get("status") in ("reviewing", "approved"):
            distribution_candidates.append(entry)

    return _templates.TemplateResponse(
        request=request, name="lead_dashboard.html",
        context={
            "role": role, "active": "lead_dashboard",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user, "distributions": distributions,
            "troubles": troubles,
            "review_targets": review_targets,
            "team_retakes": team_retakes,
            "distribution_candidates": distribution_candidates,
            "today_events": today_events,
            "today_str": today_str,
            "today_weekday_jp": ["月", "火", "水", "木", "金", "土", "日"][today_jst.weekday()],
            "my_tasks": my_tasks,
            "meetings": meetings,
            "projects_list": projects_list_raw,
        },
    )
