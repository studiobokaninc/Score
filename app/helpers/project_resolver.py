import time
from typing import Optional
from app.adapters.calendar_client import CalendarClient
from app.adapters.calendar_factory import get_calendar_client

_CACHE: dict[str, tuple[float, list]] = {}
_TTL_SECONDS = 300  # 5min


def resolve_project_name(
    project_id: int,
    actor_user_id: str,
    client: Optional[CalendarClient] = None,
) -> str:
    """project_id → project name。cache TTL=5min。未存在/エラー時は '-'。
    CALENDAR_MOCK=1 時は MockCalendarClient 経由 (factory)。"""
    now = time.time()
    cached = _CACHE.get(actor_user_id)
    if cached and now - cached[0] < _TTL_SECONDS:
        projects = cached[1]
    else:
        c = client or get_calendar_client()
        try:
            projects = c.get_my_projects(actor_user_id=actor_user_id)
        except Exception:
            projects = []
        _CACHE[actor_user_id] = (now, projects)
    for p in projects:
        if p.get("id") == project_id:
            return p.get("name") or "-"
    return "-"
