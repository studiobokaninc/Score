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


def resolve_project_members(
    project_id: Optional[int],
    actor_user_id: str,
    client: Optional[CalendarClient] = None,
    user_name_map: Optional[dict] = None,
) -> list[dict]:
    """project の実効メンバー一覧を返す ({"user_id", "name", "role"} のリスト)。

    殿御命 2026-07-09 (cmd_076③・auto-membership): Calendar 側で project の
    director/pm/lead に割当られている user は、score 側の明示的 team member
    登録(get_team_members)や task 担当実績の有無に関わらず、常にメンバー扱いに
    する。実機でディレクター本人が score 側では明示的メンバー未登録のため
    QC ビューア関連の mention/メンバー系 UI に一切現れなかった事例の根治。
    旧実装 (pages_qc.py/pages_shot.py 個別実装) は get_team_members が
    非空を返すと director/pm/lead の union 自体を丸ごとスキップしていたため、
    pages_qc.py の fallback ですら常に有効とは限らなかった。ここでは
    director/pm/lead の union を常時実行することでその抜け穴も塞ぐ。
    """
    if not project_id:
        return []
    c = client or get_calendar_client()

    def _name_for(uid: int) -> str:
        if user_name_map and uid in user_name_map:
            return user_name_map[uid]
        return f"user_{uid}"

    members: list[dict] = []
    seen_uids: set[int] = set()

    def _add(uid, role: str = "", name: str | None = None) -> None:
        if uid is None:
            return
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            return
        if uid_int in seen_uids:
            if role:
                for m in members:
                    if m["user_id"] == uid_int and not m.get("role"):
                        m["role"] = role
            return
        seen_uids.add(uid_int)
        members.append({"user_id": uid_int, "name": name or _name_for(uid_int), "role": role})

    # 1. 明示的 team member 登録 (mock 環境等・real Calendar には現状未実装)
    try:
        if hasattr(c, "get_team_members"):
            for m in (c.get_team_members(int(project_id), actor_user_id=actor_user_id) or []):
                if isinstance(m, dict):
                    _add(m.get("user_id"), role=m.get("role", ""), name=m.get("name"))
    except Exception:
        pass

    # 2. real 経路 fallback: project 配下 task の assigned_to から収集
    try:
        tasks_in_proj = c.get_tasks_by_project(int(project_id), actor_user_id=actor_user_id) if hasattr(c, "get_tasks_by_project") else []
    except Exception:
        tasks_in_proj = []
    for t in (tasks_in_proj or []):
        a = (t.get("assigned_to") if isinstance(t, dict) else getattr(t, "assignee_id", None)) or (t.get("assignee_id") if isinstance(t, dict) else None)
        _add(a)

    # 3. auto-membership: director/pm/lead は 1./2. の結果に関わらず常に union する
    try:
        if hasattr(c, "get_project_roles"):
            roles = c.get_project_roles(int(project_id), actor_user_id=actor_user_id) or {}
            for rname, ruid in roles.items():
                if ruid is not None:
                    _add(ruid, role=rname)
    except Exception:
        pass

    return members
