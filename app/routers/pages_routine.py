import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")


# Priority order: open tasks needing attention first
_TASK_PRIORITY = {"retake": 0, "reviewing": 1, "in_progress": 2, "open": 3, "approved": 9}


def _enrich_my_tasks(client, actor_id: str) -> list[dict]:
    """Fetch my_tasks and decorate with project_name / seq_code / shot_code."""
    try:
        raw = client.get_my_tasks(actor_user_id=actor_id) or [] if hasattr(client, "get_my_tasks") else []
    except Exception:
        raw = []
    my_tasks: list[dict] = []
    for t in raw:
        if isinstance(t, dict):
            my_tasks.append({
                "task_id": t.get("id") or t.get("task_id"),
                "shot_id": t.get("shot_id"),
                "shot_code": t.get("shot_code") or t.get("name") or "",
                "task_type": t.get("task_type") or t.get("type", ""),
                "name": t.get("name", ""),
                "status": t.get("status", ""),
            })
        else:
            my_tasks.append({
                "task_id": getattr(t, "task_id", None),
                "shot_id": getattr(t, "shot_id", None),
                "shot_code": getattr(t, "name", "") or "",
                "task_type": getattr(t, "type", ""),
                "status": getattr(t, "status", ""),
            })
    # build shot_id → {project_name, seq_code, shot_code} map
    try:
        projects = client.get_my_projects(actor_user_id=actor_id) or []
    except Exception:
        projects = []
    shot_map: dict = {}
    for p in projects:
        pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        pname = (p.get("name") if isinstance(p, dict) else getattr(p, "name", None)) or "-"
        if pid is None:
            continue
        try:
            shots = client.get_shots(pid, actor_user_id=actor_id) or []
        except Exception:
            shots = []
        for s in shots:
            sid = (s.get("id") if isinstance(s, dict) else getattr(s, "shot_id", None) or getattr(s, "id", None))
            if sid is None:
                continue
            shot_map[sid] = {
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
            t["shot_code"] = info.get("shot_code") or (
                f"SHOT_{int(t['shot_id']):03d}" if t.get("shot_id") else ""
            )
    return my_tasks


def _has_prev_day_exit_submitted(client, actor_id: str) -> bool:
    """Check server-side timecards for a clock_out from yesterday-or-later.

    Looks at the actor's timecard list (mock adapter exposes get_timecards;
    real Calendar BE may not — degrade gracefully). Compares
    ``created_at`` (server stamp) to "yesterday or later" using local date.
    Either previous-day or current-day clock_out counts as "the user
    already closed out at least one workday", which is the precondition
    for showing today's prioritized task list.
    """
    from datetime import datetime, timedelta
    if not hasattr(client, "get_timecards"):
        return False
    try:
        tcs = client.get_timecards(actor_user_id=actor_id) or []
    except Exception:
        return False
    today = datetime.now().date()
    yest = today - timedelta(days=1)
    for t in tcs:
        if (t.get("type") or "") != "clock_out":
            continue
        raw = t.get("created_at") or t.get("submitted_at") or ""
        if not raw:
            continue
        # parse just the date portion
        date_str = str(raw)[:10]
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= yest:
            return True
    return False


@router.get("/routine")
def get_routine(request: Request, actor_id: str = Depends(get_actor_id)):
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None

    # 「本日やるべきタスク」を priority 順で組み立て
    my_tasks = _enrich_my_tasks(client, actor_id)
    today_tasks = [t for t in my_tasks if (t.get("status") or "").lower() != "approved"]
    today_tasks.sort(key=lambda x: _TASK_PRIORITY.get((x.get("status") or "").lower(), 5))

    prev_exit_submitted = _has_prev_day_exit_submitted(client, actor_id)

    return _templates.TemplateResponse(
        request=request, name="routine.html",
        context={
            "role": role,
            "active": "routine",
            "user": user,
            "today_tasks": today_tasks,
            "prev_exit_submitted": prev_exit_submitted,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
