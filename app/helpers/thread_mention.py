"""cmd_348: task thread の @メンション候補 = そのタスクの案件にアサインされた人全員。

候補の出どころ(重複は user 単位で除く):
  1. resolve_project_members(): Calendar の director/pm/lead + 案件配下 task の担当者
  2. ScoreUserRole: Score 側で案件の役職を持つ者(user_id は email か Calendar uid)
thread に紐づく task が複数(共用 thread)なら、それぞれの案件の和集合。
案件が一つも引けぬ時は scoped=False を返し、呼び出し側は従来どおり全ユーザへ戻す。
"""
from __future__ import annotations

import sys

from app.database import SessionLocal
from app.helpers.project_resolver import resolve_project_members
from app.models import ScoreUserRole, TaskThread


def thread_task_ids(thread_id) -> list[str]:
    """thread_id に紐づく task_id(task_threads 正本)。取得失敗は空。"""
    if thread_id is None:
        return []
    try:
        db = SessionLocal()
        try:
            rows = db.query(TaskThread).filter(TaskThread.thread_id == str(thread_id)).all()
            return sorted({str(r.task_id) for r in rows})
        finally:
            db.close()
    except Exception as e:
        print(f"[thread_mention] thread_task_ids skip: {e}", file=sys.stderr)
        return []


def _project_ids_of_tasks(client, actor_id, task_ids) -> list[int]:
    pids: list[int] = []
    for tid in task_ids:
        try:
            raw = client.get_task(int(tid), actor_user_id=actor_id) if hasattr(client, "get_task") else {}
        except Exception:
            raw = {}
        pid = (raw or {}).get("project_id") if isinstance(raw, dict) else None
        try:
            pid_int = int(pid) if pid is not None else None
        except (ValueError, TypeError):
            pid_int = None
        if pid_int and pid_int not in pids:
            pids.append(pid_int)
    return pids


def _score_roles_of_project(project_id: int) -> list[str]:
    try:
        db = SessionLocal()
        try:
            rows = db.query(ScoreUserRole).filter(ScoreUserRole.project_id == str(project_id)).all()
            return [str(r.user_id) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[thread_mention] score roles skip: {e}", file=sys.stderr)
        return []


def build_mention_candidates(client, actor_id, task_ids) -> dict:
    """{"scoped": bool, "project_ids": [...], "users": [{"id","name","email"}]}。
    users は名前順。scoped=False のとき users は空(呼び出し側が全ユーザへ戻す)。"""
    project_ids = _project_ids_of_tasks(client, actor_id, task_ids)
    if not project_ids:
        return {"scoped": False, "project_ids": [], "users": []}

    all_users = []
    try:
        all_users = [u for u in (client.get_users(actor_user_id=actor_id) or []) if isinstance(u, dict)]
    except Exception:
        all_users = []
    by_id: dict[int, dict] = {}
    by_email: dict[str, dict] = {}
    for u in all_users:
        uid = u.get("id") or u.get("user_id")
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            continue
        by_id[uid_int] = u
        em = (u.get("email") or "").strip().lower()
        if em:
            by_email[em] = u
    name_map = {uid: (u.get("name") or u.get("full_name") or u.get("username") or "") for uid, u in by_id.items()}

    chosen: dict[int, dict] = {}

    def _add_user(u: dict) -> None:
        uid = u.get("id") or u.get("user_id")
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            return
        if uid_int in chosen:
            return
        email = (u.get("email") or "").strip()
        name = (u.get("name") or u.get("full_name") or u.get("username") or "").strip()
        if not name:
            name = email.split("@")[0] if email else f"user_{uid_int}"
        chosen[uid_int] = {"id": uid_int, "name": name, "email": email}

    for pid in project_ids:
        try:
            members = resolve_project_members(pid, actor_id, client=client, user_name_map=name_map)
        except Exception:
            members = []
        for m in members:
            u = by_id.get(m.get("user_id"))
            if u is not None:
                _add_user(u)
        for key in _score_roles_of_project(pid):
            u = by_email.get(key.strip().lower())
            if u is None and key.isdigit():
                u = by_id.get(int(key))
            if u is not None:
                _add_user(u)

    users = sorted(chosen.values(), key=lambda x: x["name"])
    return {"scoped": True, "project_ids": project_ids, "users": users}
