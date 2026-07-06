import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
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


@router.get("/shot/{id}")
def get_shot_detail(
    id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
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
    try:
        shot_detail_raw = client.get_shot_detail(id, actor_user_id=actor_id) if hasattr(client, "get_shot_detail") else {}
    except Exception:
        shot_detail_raw = {}
    project_name = (
        resolve_project_name(shot.project_id, actor_id)
        if shot else "-"
    )
    project_id = shot.project_id if shot else None
    seq_code = getattr(shot, "seq_code", None) if shot else None
    task_name = (
        getattr(shot, "shot_code", None) or getattr(shot, "name", None) or f"SHOT_{id:03d}"
    ) if shot else f"SHOT_{id:03d}"

    from app.helpers.colors import attach_task_palettes, get_project_palette
    tasks = attach_task_palettes(tasks)
    project_palette = get_project_palette(project_id) if project_id else None
    # 殿御命 2026-06-08: asset_list を context に追加 + tasks 各 task に latest_asset attach
    asset_list = list((shot_detail_raw.get("asset_list", []) or [])) if isinstance(shot_detail_raw, dict) else []
    latest_by_task = {}
    for a in asset_list:
        if isinstance(a, dict):
            tid = a.get("task_id")
            if tid is None: continue
            cur = latest_by_task.get(tid)
            if cur is None or (a.get("version", "") > cur.get("version", "")):
                latest_by_task[tid] = a
    for t in tasks:
        try:
            t.latest_asset = latest_by_task.get(getattr(t, "task_id", None))
        except Exception: pass
    return _templates.TemplateResponse(
        request=request,
        name="shot_detail.html",
        context={
            "tasks": tasks,
            "upstream_tasks": tasks,
            "shot_id": id,
            "shot": shot,
            "task_id": None,
            "project_name": project_name,
            "project_id": project_id,
            "project_palette": project_palette,
            "seq_code": seq_code,
            "task_name": task_name,
            "user": user,
            "role": get_actor_role(actor_id),
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "isolated_task": False,
            "shot_detail_raw": shot_detail_raw,
            "asset_list": asset_list,
            "latest_by_task": latest_by_task,
        },
    )


@router.get("/task/{task_id}")
def get_task_detail(
    task_id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
):
    """単独タスクページ — shot_detail.html を流用し isolated 表示 (1 task のみ)."""
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None

    # task_id から所属 shot を解決:
    # 優先: client.get_task(task_id) (Calendar /api/tasks/{id})
    # fallback 1: get_my_tasks (actor 関連 task のみ)
    found_task = None
    found_shot_id = None
    found_shotID = None
    found_seqID = None
    found_name = None
    # 優先: 直接 /api/tasks/{id} で取得
    if hasattr(client, "get_task"):
        try:
            raw = client.get_task(task_id, actor_user_id=actor_id) or {}
            if raw and (raw.get("id") == task_id or raw.get("task_id") == task_id):
                found_task = type("_T", (), {
                    "task_id": raw.get("id") or task_id,
                    "shot_id": raw.get("shot_id") or 0,
                    "type": raw.get("type", "Unknown"),
                    "status": raw.get("status", "open"),
                    "assignee_id": raw.get("assigned_to") or raw.get("assignee_id") or 0,
                    # 殿御命 2026-06-01: 詳細 field (cost/priority/期日/dependsOn 等)
                    "cost": raw.get("cost"),
                    "priority": raw.get("priority"),
                    "due_date": raw.get("due_date"),
                    "start_date": raw.get("start_date"),
                    "progress": raw.get("progress"),
                    "depends_on": raw.get("dependsOn") or [],
                    "description": raw.get("description"),
                    "deliverables": raw.get("deliverables"),
                    "project_id": raw.get("project_id"),
                    "shotID": raw.get("shotID"),
                    "seqID": raw.get("seqID"),
                    "name": raw.get("name"),
                    "thread_id": raw.get("thread_id"),
                })()
                found_shot_id = raw.get("shot_id") or 0
                found_shotID = raw.get("shotID")
                found_seqID = raw.get("seqID")
                found_name = raw.get("name")
        except Exception:
            pass
    # fallback 1: get_my_tasks
    if not found_task and hasattr(client, "get_my_tasks"):
        try:
            for raw in (client.get_my_tasks(actor_user_id=actor_id) or []):
                if isinstance(raw, dict) and (raw.get("id") == task_id or raw.get("task_id") == task_id):
                    found_task = type("_T", (), {
                        "task_id": raw.get("id") or raw.get("task_id"),
                        "shot_id": raw.get("shot_id") or 0,
                        "type": raw.get("type", "Unknown"),
                        "status": raw.get("status", "open"),
                        "assignee_id": raw.get("assigned_to") or raw.get("assignee_id") or 0,
                        "thread_id": raw.get("thread_id"),
                    })()
                    found_shot_id = raw.get("shot_id") or 0
                    found_shotID = raw.get("shotID")
                    found_seqID = raw.get("seqID")
                    found_name = raw.get("name")
                    break
        except Exception:
            pass

    if not found_task:
        # fallback: stub task (task_id を class 内で安全に default 指定)
        _stub = type("_Stub", (), {
            "task_id": task_id,
            "shot_id": 1,
            "type": "Unknown",
            "status": "open",
            "assignee_id": 0,
        })()
        found_task = _stub
        found_shot_id = 1

    # get_shot は found_shot_id が 0/None なら失敗するため try/except
    shot = None
    if found_shot_id:
        try:
            shot = client.get_shot(found_shot_id, actor_user_id=actor_id)
        except Exception:
            shot = None
    # 既存 project_id を Calendar の get_task response からも取得試行 (project_id 不在解消用)
    fetched_project_id = None
    if hasattr(client, "get_task"):
        try:
            raw_full = client.get_task(task_id, actor_user_id=actor_id) or {}
            fetched_project_id = raw_full.get("project_id")
        except Exception:
            pass
    project_id = (shot.project_id if shot else None) or fetched_project_id
    project_name = resolve_project_name(project_id, actor_id) if project_id else "-"
    seq_code = (getattr(shot, "seq_code", None) if shot else None) or found_seqID
    # shot table 不在の場合 found_shotID (文字列・shot_code) を fallback として stub object に
    if not shot and found_shotID:
        shot = type("_ShotStub", (), {
            "shot_id": found_shot_id or 0,
            "shot_code": found_shotID,
            "name": found_shotID,
            "project_id": project_id or 33,
            "status": "",
            "seq_code": found_seqID or "",
        })()
    task_name = found_name or found_task.type or "task"

    # upstream: 同 shotID(shot_code) の全 task を dependsOn chain で sort
    # 表示順: Layout → Animation → Lighting → Composite ... の workflow 順
    upstream_tasks = []
    try:
        # 全 project task を一括 fetch
        all_proj_tasks = []
        if project_id and hasattr(client, "get_tasks_by_project"):
            all_proj_tasks = client.get_tasks_by_project(project_id, actor_user_id=actor_id) or []
        # 同 shotID で filter
        target_shot_code = found_shotID or (shot.shot_code if shot else None)
        same_shot_tasks = []
        if target_shot_code:
            same_shot_tasks = [t for t in all_proj_tasks if (t.get("shotID") or "") == target_shot_code]
        # dependsOn topological sort (chain 順)
        # まず id 別 lookup
        by_id = {str(t.get("id")): t for t in same_shot_tasks}
        # 入次数(依存元なし=0)から topological sort
        from collections import deque
        in_degree = {tid: 0 for tid in by_id}
        for tid, t in by_id.items():
            for dep in (t.get("dependsOn") or []):
                # dep は他 shot を指す場合もある(無関係はスキップ)
                if str(dep) in by_id:
                    in_degree[tid] += 1
        queue = deque([tid for tid, d in in_degree.items() if d == 0])
        sorted_ids = []
        while queue:
            tid = queue.popleft()
            sorted_ids.append(tid)
            for other_tid, t in by_id.items():
                if str(tid) in [str(x) for x in (t.get("dependsOn") or [])]:
                    in_degree[other_tid] -= 1
                    if in_degree[other_tid] == 0:
                        queue.append(other_tid)
        # CalendarTask 風 wrapper に変換
        for tid in sorted_ids:
            t = by_id[tid]
            upstream_tasks.append(type("_TT", (), {
                "task_id": t.get("id"),
                "shot_id": t.get("shot_id") or 0,
                "type": t.get("type", ""),
                "status": t.get("status", ""),
                "assignee_id": t.get("assigned_to") or 0,
                "name": t.get("name", ""),
            })())
        # 何も取れなければ fallback (旧)
        if not upstream_tasks and found_shot_id:
            upstream_tasks = client.get_tasks(found_shot_id, actor_user_id=actor_id) or [found_task]
        elif not upstream_tasks:
            upstream_tasks = [found_task]
    except Exception:
        try:
            upstream_tasks = client.get_tasks(found_shot_id, actor_user_id=actor_id) if found_shot_id else [found_task]
        except Exception:
            upstream_tasks = [found_task]

    # user_id → 表示名 map (assignee 表示用・殿御命 2026-06-01)
    user_name_map = {}
    try:
        if hasattr(client, "get_users"):
            for u in (client.get_users(actor_user_id=actor_id) or []):
                if not isinstance(u, dict):
                    continue
                uid = u.get("id") or u.get("user_id")
                if uid is None:
                    continue
                disp = u.get("name") or u.get("full_name") or u.get("username") or ""
                if disp:
                    user_name_map[uid] = disp
    except Exception:
        user_name_map = {}

    # 殿御命 2026-06-03: version 事前入力 (追記可) — next_version 自動採番
    # 2026-06-03 hotfix: actor_user_id を渡し /api/me/shots/{id} 経由で asset 取得
    try:
        next_version = client.next_version(found_shot_id, task_id, actor_user_id=actor_id) if hasattr(client, "next_version") else "v001"
    except Exception:
        next_version = "v001"

    # 殿御命 2026-06-03: project members 取得 (mention 選択用 multi-select)
    project_members = []
    try:
        if project_id and hasattr(client, "get_team_members"):
            project_members = client.get_team_members(int(project_id), actor_user_id=actor_id) or []
        # real 経路 fallback: project の task assignees から uniq 抽出
        if not project_members and project_id:
            seen_uids = set()
            try:
                tasks_in_proj = client.get_tasks_by_project(int(project_id), actor_user_id=actor_id) if hasattr(client, "get_tasks_by_project") else []
            except Exception:
                tasks_in_proj = []
            for t in (tasks_in_proj or []):
                a = (t.get("assigned_to") if isinstance(t, dict) else getattr(t, "assigned_to", None)) or (t.get("assignee_id") if isinstance(t, dict) else getattr(t, "assignee_id", None))
                if a and a not in seen_uids:
                    seen_uids.add(a)
                    project_members.append({"user_id": a, "name": user_name_map.get(a, f"user_{a}"), "role": ""})
    except Exception:
        project_members = []

    # cmd_058 真因修正: shot_detail().asset_list 経由だと shot に紐付かぬ task
    # (PM task 等・shot_id=None) の asset が永久に一覧不可視となる不具合があった
    # (upload 自体は成功するが shot_id 側から辿れず孤立)。
    # task_id で直接取得する経路 (get_assets_by_task) に変更し、shot 紐付けの
    # 有無に関わらず asset history が表示されるようにする。
    asset_list = []
    try:
        if hasattr(client, "get_assets_by_task"):
            asset_list = list(client.get_assets_by_task(task_id, actor_user_id=actor_id) or [])
        elif found_shot_id and hasattr(client, "get_shot_detail"):
            shot_dict = client.get_shot_detail(found_shot_id, actor_user_id=actor_id) or {}
            asset_list = list(shot_dict.get("asset_list", []) or [])
            asset_list = [a for a in asset_list if (a.get("task_id") if isinstance(a, dict) else getattr(a, "task_id", None)) == task_id]
        # created_at 降順
        asset_list.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else getattr(a, "created_at", "")) or "", reverse=True)
    except Exception:
        asset_list = []

    return _templates.TemplateResponse(
        request=request,
        name="shot_detail.html",
        context={
            "tasks": [found_task],          # asset history: アイソレーション 単独タスクのみ
            "upstream_tasks": upstream_tasks,  # upstream 可視化: 全工程 (context 保持)
            "shot_id": found_shot_id,
            "shot": shot,
            "task_id": task_id,
            "project_name": project_name,
            "project_id": project_id,
            "seq_code": seq_code,
            "shot_code": found_shotID or (shot.shot_code if shot and getattr(shot, "shot_code", None) else None),
            "task_name": task_name,
            "user": user,
            "user_name_map": user_name_map,
            "role": get_actor_role(actor_id),
            "isolated_task": True,
            "next_version": next_version,
            "asset_list": asset_list,
            "project_members": project_members,
            "task_thread_id": getattr(found_task, "thread_id", None) if found_task else None,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
