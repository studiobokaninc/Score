import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from app.adapters.dto import CalendarUser
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")

@router.get("/calendar")
def get_calendar(request: Request, week_offset: int = 0, actor_id: str = Depends(get_actor_id)):
    """week_offset=0 を「本日含む週」と定義。-1 で 1 週前 / +1 で 1 週後 表示。"""
    from datetime import datetime, timedelta, timezone
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = CalendarUser(user_id=0, email="", role="", name="")
    try:
        events = client.get_events(actor_user_id=actor_id)
    except Exception:
        events = []
    # project_id → name map (modal 表示用・get_my_projects 1 回呼出)
    project_name_map = {}
    try:
        projects_raw = client.get_my_projects(actor_user_id=actor_id) or []
        for p in projects_raw:
            pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
            pname = p.get("name") if isinstance(p, dict) else getattr(p, "name", "")
            if pid is not None:
                project_name_map[pid] = pname
    except Exception:
        project_name_map = {}
    # user_id → display_name map (殿御命 2026-06-01: ユーザー名表示)
    user_name_map = {}
    try:
        if hasattr(client, "get_users"):
            users_raw = client.get_users(actor_user_id=actor_id) or []
            for u in users_raw:
                if not isinstance(u, dict):
                    continue
                uid = u.get("id") or u.get("user_id")
                if uid is None:
                    continue
                # name > full_name > username の優先順 (空文字 fallback)
                disp = u.get("name") or u.get("full_name") or u.get("username") or ""
                if disp:
                    user_name_map[uid] = disp
    except Exception:
        user_name_map = {}
    # 祝日 (本年)
    today = datetime.now(timezone(timedelta(hours=9)))
    try:
        holidays_raw = client.get_holidays(today.year, actor_user_id=actor_id) or []
        holidays_map = {h.get("date"): h.get("name", "祝日") for h in holidays_raw if h.get("date")}
    except Exception:
        holidays_map = {}

    # 週グリッド構築: 本日を含む週を中心に、先週・今週・来週 (計 21 日)
    # week_offset で中心週を前後に移動 (殿御命 2026-06-01: 1 週ずつナビ)
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"]
    today_date = today.date()
    this_monday_actual = today_date - timedelta(days=today_date.weekday())  # 月=0 (本日週)
    this_monday = this_monday_actual + timedelta(weeks=week_offset)         # 表示中心週
    last_monday = this_monday - timedelta(days=7)
    next_monday = this_monday + timedelta(days=7)
    # 21 日分を組み立て、3 週分のリストに分割
    last_week, this_week, next_week = [], [], []
    for delta in range(21):
        d = last_monday + timedelta(days=delta)
        d_str = d.strftime("%Y-%m-%d")
        wd_idx = d.weekday()
        wd = weekday_jp[wd_idx]
        is_holiday = wd_idx >= 5 or d_str in holidays_map
        holiday_name = holidays_map.get(d_str, "")
        day_events = []
        for ev in (events or []):
            ev_date = ev.get("date") or ev.get("start_time") or ""
            if ev_date.startswith(d_str):
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
                # project name 解決 (殿御命 2026-06-01: 「project_id: 72」→ 名前表示)
                pid = ev.get("project_id")
                ev["project_name"] = project_name_map.get(pid, "") if pid is not None else ""
                # participants normalize ([{type,id},...] → 実名解決 / string はそのまま)
                parts = ev.get("participants")
                if isinstance(parts, list):
                    names = []
                    for p in parts:
                        if isinstance(p, dict):
                            pid_ = p.get("id")
                            ptype = p.get("type", "user")
                            disp = user_name_map.get(pid_)
                            if disp:
                                names.append(disp)
                            elif pid_ is not None:
                                names.append(f"{ptype}_{pid_}")
                            else:
                                names.append(str(p))
                        else:
                            names.append(str(p))
                    ev["participant_names"] = names
                elif isinstance(parts, str) and parts:
                    ev["participant_names"] = [parts]
                else:
                    ev["participant_names"] = []
                # meeting_links 抽出 (殿御命 2026-06-01: Zoom 等もリンク化)
                # meeting_url field + description / memo / body 内 URL を統合
                import re as _re
                _urls = []
                for _k in ("meeting_url", "zoom_url", "meet_url", "teams_url", "webex_url"):
                    _v = ev.get(_k)
                    if _v and isinstance(_v, str) and _v not in _urls:
                        _urls.append(_v)
                for _k in ("description", "memo", "body", "location"):
                    _text = ev.get(_k)
                    if isinstance(_text, str):
                        for _m in _re.finditer(r"https?://[^\s<>\"'\)]+", _text):
                            _u = _m.group(0).rstrip(".,;)")
                            if _u not in _urls:
                                _urls.append(_u)
                ev["meeting_links"] = _urls
                day_events.append(ev)
        if d < this_monday:
            week_offset = -1
        elif d >= next_monday:
            week_offset = 1
        else:
            week_offset = 0
        entry = {
            "date": d_str,
            "day": d.day,
            "weekday": wd,
            "is_today": d == today_date,
            "is_holiday": is_holiday,
            "holiday_name": holiday_name,
            "events": day_events,
            "week_offset": week_offset,
        }
        (last_week if week_offset == -1 else (next_week if week_offset == 1 else this_week)).append(entry)

    # 表示中心週のラベル (本日含む週=0 / 前=- / 後=+)
    week_label = "今週" if week_offset == 0 else (f"{abs(week_offset)} 週前" if week_offset < 0 else f"{week_offset} 週後")
    week_range_str = f"{this_monday.strftime('%Y-%m-%d')} 〜 {(this_monday + timedelta(days=6)).strftime('%m-%d')}"

    return _templates.TemplateResponse(
        request=request, name="calendar.html",
        context={
            "role": role,
            "active": "calendar",
            "events": events,
            "user": user,
            "week_grid": this_week,        # backward compat (this_week alias)
            "last_week": last_week,
            "this_week": this_week,
            "next_week": next_week,
            "week_offset": week_offset,
            "week_label": week_label,
            "week_range_str": week_range_str,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
