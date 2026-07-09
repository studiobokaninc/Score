import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.helpers.project_resolver import resolve_project_name, resolve_project_members
from app.adapters.calendar_factory import get_calendar_client
from app.helpers.task_status import attach_status_meta

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")


@router.get("/project_detail/{project_id}")
def get_project_detail(project_id: int, request: Request, actor_id: str = Depends(get_actor_id)):
    role = get_actor_role(actor_id)
    project_name = resolve_project_name(project_id, actor_id) or "-"
    client = get_calendar_client()

    # Project 詳細(start_date / end_date / status etc.)
    project_info = {}
    try:
        if hasattr(client, "get_my_project_detail"):
            project_info = client.get_my_project_detail(project_id, actor_user_id=actor_id) or {}
        else:
            all_projects = client.get_my_projects(actor_user_id=actor_id) or []
            for p in all_projects:
                if p.get("id") == project_id:
                    project_info = p
                    break
    except Exception:
        project_info = {}

    # 進捗 = completed task 数 / 全 task 数
    total_tasks = 0
    completed_tasks = 0
    try:
        # /api/me/tasks は actor 担当のみ・全 project 共有なので、project_id filter
        my_tasks = client.get_my_tasks(actor_user_id=actor_id) if hasattr(client, "get_my_tasks") else []
        for t in (my_tasks or []):
            if isinstance(t, dict) and t.get("project_id") == project_id:
                total_tasks += 1
                if t.get("status") in ("deliver",):
                    completed_tasks += 1
    except Exception:
        pass

    # 進捗 % — 全 project task 一括取得して再計算 (上の my_tasks ベースより正確)
    try:
        all_proj_tasks = client.get_tasks_by_project(project_id, actor_user_id=actor_id) or []
        if all_proj_tasks:
            total_tasks = len(all_proj_tasks)
            completed_tasks = sum(1 for t in all_proj_tasks if t.get("status") in ("deliver",))
    except Exception:
        all_proj_tasks = []
    progress_pct = int(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    # user_id → name 解決 map を構築 (task 担当者名表示用 + member 一覧の名前解決)
    uid_to_name: dict = {}
    try:
        resp_users = httpx_get_users(client)
        for u in resp_users:
            uid = u.get("id") or u.get("user_id")
            if uid is None:
                continue
            uid_to_name[uid] = (u.get("name") or u.get("full_name")
                                or u.get("username") or f"user_{uid}")
    except Exception:
        uid_to_name = {}

    # 参加メンバー一覧 = team member 登録 + task 担当者 + director/pm/lead (auto-membership)
    # cmd_076④(殿要件確定): 旧実装は task の assigned_to のみを member としており、
    # task を持たない director/PM/lead が一覧から漏れていた。resolve_project_members
    # (cmd_076③ で auto-membership 実装済) に一本化し、重複なく統合する。
    try:
        members = resolve_project_members(int(project_id), actor_id, client=client, user_name_map=uid_to_name)
    except Exception:
        members = []

    # SEQ > SHOT > TASK 階層構築 (動的) — task 主軸構築
    # 判明:
    #  (a) Calendar task の shot 紐付けは整数 shot_id ではなく 文字列 shotID (shot_code) 経由
    #  (b) shot table に存在しないが task の shotID で参照されてる shot もある(例: c10)
    # → task の shotID + seqID を主軸に SEQ > SHOT 構造を組む(shot table は補足情報源)
    seq_groups = {}  # {seq_code: {"seq_code": str, "shots": {shot_code: {...}}}}
    try:
        all_shots = client.get_shots(project_id, actor_user_id=actor_id) or []
        # shot table を shot_code → 情報 map に
        shot_info_by_code = {}
        for shot in all_shots:
            sc = getattr(shot, "shot_code", "") or getattr(shot, "name", "")
            sid = getattr(shot, "shot_id", None) or getattr(shot, "id", None)
            ss = getattr(shot, "status", "")
            seq_c = getattr(shot, "seq_code", "")
            if sc:
                shot_info_by_code[sc] = {"id": sid, "status": ss, "seq_code": seq_c}

        # task を SEQ + SHOT 別に group
        for t in all_proj_tasks:
            scode = t.get("shotID") or ""
            # seq/shot 両方未設定 = project-level task (PM 系成果物管理タスク)
            _seq_raw = t.get("seqID") or shot_info_by_code.get(scode, {}).get("seq_code", "")
            if not _seq_raw and not scode:
                seqid = "📋 プロジェクト管理タスク"
            else:
                seqid = _seq_raw or "(SEQ 未設定)"
            # seqID に同じ文字列が重複してる場合 (例: SEQ001SEQ001) — normalize
            if seqid and len(seqid) > 0:
                # 「SEQ001SEQ001」のような重複 prefix を発見したら最初の半分を採用
                half = len(seqid) // 2
                if seqid[:half] == seqid[half:] and half > 0:
                    seqid = seqid[:half]
            if not scode:
                scode = "📋 (SHOT 紐付けなし)"
            if seqid not in seq_groups:
                seq_groups[seqid] = {"seq_code": seqid, "shots": {}}
            if scode not in seq_groups[seqid]["shots"]:
                shot_info = shot_info_by_code.get(scode, {})
                seq_groups[seqid]["shots"][scode] = {
                    "id": shot_info.get("id"),
                    "shot_code": scode,
                    "status": shot_info.get("status", ""),
                    "tasks": [],
                    "in_shot_table": scode in shot_info_by_code,
                }
            _assignee = t.get("assigned_to") or t.get("assignee_id")
            seq_groups[seqid]["shots"][scode]["tasks"].append({
                "id": t.get("id"),
                "name": t.get("name"),
                "type": t.get("type"),
                "status": t.get("status"),
                "assigned_to": _assignee,
                "assigned_user_name": uid_to_name.get(_assignee, "") if _assignee else "",
                # cmd_075: Calendar が inline 同梱する動的色/ラベル
                "status_color": t.get("status_color"),
                "status_label": t.get("status_label"),
                "status_category": t.get("status_category"),
            })

        # dict → list 変換 + 順序 sort + task_count 付与
        seq_groups_list = []
        for seq_c, sg in sorted(seq_groups.items()):
            shots_list = []
            for sc, shot in sorted(sg["shots"].items()):
                shot["task_count"] = len(shot["tasks"])
                shots_list.append(shot)
            seq_groups_list.append({
                "seq_code": seq_c,
                "shots": shots_list,
                "shot_count": len(shots_list),
            })
    except Exception:
        seq_groups_list = []

    from app.helpers.colors import (
        get_project_palette, get_task_type_palette,
        get_seq_palette, get_shot_palette,
    )
    project_palette = get_project_palette(project_id)
    # decorate seq groups with seq_palette, shots with shot_palette, tasks with type palette
    for seq_idx, sg in enumerate(seq_groups_list):
        # project-level (PM/管理) seq is rendered separately — keep neutral slate
        is_pm = "📋" in (sg.get("seq_code") or "")
        sg["palette"] = (
            {"key": "slate", "card_bg": "bg-slate-50", "card_border": "border-slate-300",
             "title": "text-slate-700", "side_bar": "bg-slate-400",
             "badge_bg": "bg-slate-600", "badge_text": "text-white"}
            if is_pm else get_seq_palette(project_id, seq_idx)
        )
        for shot_idx, shot in enumerate(sg.get("shots", [])):
            shot["palette"] = (
                {"key": "slate", "shade": 100, "bg": "bg-slate-100",
                 "border": "border-slate-300", "title": "text-slate-700"}
                if is_pm else get_shot_palette(project_id, seq_idx, shot_idx)
            )
            for tk in shot.get("tasks", []):
                tk["palette"] = get_task_type_palette(tk.get("type"))
            shot["tasks"] = attach_status_meta(shot.get("tasks", []), client)  # cmd_075

    return _templates.TemplateResponse(
        request=request, name="project_detail.html",
        context={
            "role": role,
            "active": "project",
            "project_id": project_id,
            "project_name": project_name,
            "project_info": project_info,
            "members": members,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "progress_pct": progress_pct,
            "seq_groups": seq_groups_list,
            "project_palette": project_palette,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )


def httpx_get_users(client):
    """Calendar /api/users 全件取得 (member 名解決用)。

    優先: client.get_users() (mock/adapter が実装してれば)
    fallback: httpx で /api/users を直接叩く (real Calendar BE)
    """
    if hasattr(client, "get_users"):
        try:
            return client.get_users() or []
        except Exception:
            pass
    import httpx as _httpx
    base_url = getattr(client, "base_url", "http://192.168.44.253:8001")
    token = getattr(client, "m2m_token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = _httpx.get(f"{base_url}/api/users", headers=headers, timeout=5)
    resp.raise_for_status()
    return resp.json()
