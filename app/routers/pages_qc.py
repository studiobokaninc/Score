import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id, get_actor_role
from app.qc_delegation import is_qc_delegated
from app.helpers.project_resolver import resolve_project_name, resolve_project_members
from app.helpers.task_status import attach_status_meta, JUDGE_TARGET_STATUSES

router = APIRouter()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)


def _resolve_task(client, shot_id: int, task_id: int | None, actor_id: str):
    """task_id 指定時は当該タスクを探す。なければ None。
    殿御命 2026-06-05: get_task (full DTO with name field) を優先し fallback で get_tasks"""
    if task_id is None:
        return None
    # 優先: get_task で full DTO (name field 含む) 取得
    if hasattr(client, "get_task"):
        try:
            raw = client.get_task(task_id, actor_user_id=actor_id) or {}
            if raw and (raw.get("id") == task_id or raw.get("task_id") == task_id):
                # dict から attr-like object 作成 (name 含む)
                return type("_T", (), {
                    "task_id": raw.get("id") or task_id,
                    "shot_id": raw.get("shot_id") or shot_id,
                    "type": raw.get("type", "Unknown"),
                    "name": raw.get("name") or raw.get("type") or "task",
                    "status": raw.get("status", "mk"),
                    "assignee_id": raw.get("assigned_to") or raw.get("assignee_id") or 0,
                    "thread_id": raw.get("thread_id"),
                    # cmd_075: Calendar が inline 同梱する動的色/ラベル
                    "status_color": raw.get("status_color"),
                    "status_label": raw.get("status_label"),
                    "status_category": raw.get("status_category"),
                    # cmd_091: SHOT_000 (shot 紐付なし task) fallback で project 特定に使用
                    "project_id": raw.get("project_id"),
                })()
        except Exception:
            pass
    # fallback: get_tasks (CalendarTask DTO — name 無いが type は有)
    try:
        tlist = client.get_tasks(shot_id, actor_user_id=actor_id) or []
        for t in tlist:
            if getattr(t, "task_id", None) == task_id:
                return t
    except Exception:
        pass
    return None


@router.get("/qc/{id}")
def get_qc_viewer(
    id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
    task_id: int | None = None,
    asset_id: int | None = None,
    as_role: str | None = None,  # 殿御命 2026-06-05 (B 案): admin 限定 role preview
    project_id: int | None = None,  # cmd_091: SHOT_000 (shot 紐付なし task) fallback 用 (cmd_088 の /shot/{id} と同一パターン)
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
    selected_task = _resolve_task(client, id, task_id, actor_id)

    # cmd_091 (cmd_088 と同根の不具合): shot 不在 (代表例 id=0・パンくず "SHOT_000" =
    # project 配下だが shotID 未設定の task 集約) の場合、client.get_tasks(id) は
    # /api/shots/{id}/tasks が 404→[] となり、実際に project 配下に存在する task が
    # 「このSHOTにはTaskが0件」と誤表示され、QC判定ボタンも活性化しない不具合があった
    # (実機再現: project 80 "Score 検証" task_id=3255 status=qc)。
    # selected_task (get_task 直接解決・shot 非依存で既に解決成功している) の project_id か
    # クエリの project_id で project を特定し、get_tasks_by_project から shot 紐付なし分を
    # 抽出する (pages_shot.py の /shot/{id} cmd_088 修正と同一パターンで根治)。
    if shot is None:
        project_id = project_id or (getattr(selected_task, "project_id", None) if selected_task else None)
        if not tasks and project_id:
            try:
                all_proj_tasks = client.get_tasks_by_project(int(project_id), actor_user_id=actor_id) or []
            except Exception:
                all_proj_tasks = []
            tasks = [
                type("_TT", (), {
                    "task_id": t.get("id"),
                    "shot_id": t.get("shot_id") or 0,
                    "type": t.get("type") or "task",
                    "status": t.get("status", ""),
                    "assignee_id": t.get("assigned_to") or 0,
                    "thread_id": t.get("thread_id"),
                    "status_color": t.get("status_color"),
                    "status_label": t.get("status_label"),
                    "status_category": t.get("status_category"),
                })()
                for t in all_proj_tasks if not t.get("shotID") and not t.get("shot_id")
            ]
    else:
        project_id = shot.project_id

    tasks = attach_status_meta(tasks, client)  # cmd_075: status_color/status_label 動的付与
    project_name = resolve_project_name(project_id, actor_id) if project_id else "-"
    seq_code = getattr(shot, "seq_code", None) if shot else None
    shot_code = getattr(shot, "shot_code", None) if shot else None
    # 殿御命 2026-06-03: breadcrumb は task.name (例 "Compositing") を優先・fallback で type
    task_name = (getattr(selected_task, "name", None) or getattr(selected_task, "type", None)) if selected_task else None

    # 殿御命 2026-06-03 Phase B: real asset_list 取得 (nibu 仕様: /api/me/shots/{id}.asset_list)
    # cmd_059④ 真因修正: task_id 指定時は「shot取得→task_idフィルタ」だと shot_id 不整合の
    # asset (cmd_058①と同種・shot_id=None の孤立task 等) が取得段階で漏れる。
    # get_assets_by_task(task_id) で直接取得する経路に変更 (根治・pages_shot.py と同方針)。
    asset_list = []
    try:
        if task_id and hasattr(client, "get_assets_by_task"):
            asset_list = list(client.get_assets_by_task(task_id, actor_user_id=actor_id) or [])
        elif hasattr(client, "get_shot_detail"):
            try:
                shot_dict = client.get_shot_detail(id, actor_user_id=actor_id) or {}
            except Exception:
                # 殿御命 2026-07-09 (cmd_076⑤): get_shot_detail (/api/me/shots/{id}) は
                # Calendar 側で明示的 project member 限定 (非member は 403・実機確認済み。
                # task assignee であっても member 登録が無ければ同様に 403)。task_id 未指定で
                # /qc/{shot_id} に来た非member director/PM/assignee はここで asset_list が
                # 常に空になり「QC 確認する対象が何も無い」ページになっていた。
                shot_dict = {}
            asset_list = list(shot_dict.get("asset_list", []) or [])
            if not asset_list and hasattr(client, "get_assets_by_task"):
                # membership 非依存 fallback: get_assets_by_task は member 限定
                # ("/me/" prefix) ではなく実機で non-member でも 200 を確認済み。
                # tasks は上(L76) で既に取得済みのため再取得しない (冗長呼出防止)。
                try:
                    for _t in (tasks or []):
                        _tid = getattr(_t, "task_id", None)
                        if _tid is not None:
                            asset_list.extend(client.get_assets_by_task(_tid, actor_user_id=actor_id) or [])
                except Exception:
                    pass
            if task_id:
                asset_list = [a for a in asset_list if (a.get("task_id") if isinstance(a, dict) else getattr(a, "task_id", None)) == task_id]
        asset_list.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else "") or "", reverse=True)
    except Exception:
        asset_list = []

    # 殿御命 2026-07-06 (cmd_068②③): .exr/.mov 等変換済 asset は DL リンクを原本ファイルへ差替え
    _demo_mode = os.getenv("CALENDAR_MOCK", "0") == "1"
    from app.helpers.asset_originals import resolve_download_url
    for _a in asset_list:
        if isinstance(_a, dict):
            _a["download_url"] = resolve_download_url(_a, _demo_mode)

    # 殿御命 2026-06-05: project_members 取得 (mention 選択用)
    # 殿御命 2026-07-09 (cmd_076③): auto-membership 共通ヘルパーに統一
    # (director/pm/lead は明示的 team member 登録の有無に関わらず常にメンバー扱い)
    project_members = []
    try:
        if project_id:
            user_name_map = {}
            try:
                for u in (client.get_users(actor_user_id=actor_id) or []):
                    if isinstance(u, dict):
                        uid = u.get("id") or u.get("user_id")
                        if uid is not None:
                            user_name_map[int(uid)] = u.get("name") or u.get("full_name") or (u.get("email") or "").split("@")[0] or f"uid {uid}"
            except Exception:
                pass
            project_members = resolve_project_members(int(project_id), actor_id, client=client, user_name_map=user_name_map)
            # 加えて殿御本人 (admin) も含む (役 user.role admin)
            if hasattr(user, 'user_id') and user.user_id and int(user.user_id) not in {m["user_id"] for m in project_members}:
                project_members.append({"user_id": int(user.user_id), "name": getattr(user, 'name', '') or f"uid {user.user_id}", "role": getattr(user, 'role', '') or "admin"})
    except Exception:
        project_members = []

    return _templates.TemplateResponse(
        request=request,
        name="qc_viewer.html",
        context={
            "tasks": tasks, "shot_id": id, "shot": shot, "project_name": project_name,
            "project_id": project_id, "seq_code": seq_code, "shot_code": shot_code,
            "task_id": task_id, "task_name": task_name,
            "judge_target_statuses": tuple(JUDGE_TARGET_STATUSES),  # cmd_075: _can_judge 判定対象
            "project_members": project_members,  # 殿御命 2026-06-05: mention 選択用
            # 殿御命 2026-06-05 (B 案): admin role 元 user のみ as_role で別 role preview 可
            "role": (as_role if (as_role in ('director', 'pm', 'lead', 'user') and get_actor_role(actor_id) == 'admin') else get_actor_role(actor_id)),
            "actual_role": get_actor_role(actor_id),
            "preview_role": as_role if (as_role and get_actor_role(actor_id) == 'admin') else None,
            # 殿御命 2026-06-09 (案A): この依頼で mention された user は委任で Approve/Retake 可
            "can_qc": is_qc_delegated(actor_id, task_id=task_id, shot_id=id),
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "asset_list": asset_list,
            "selected_asset_id": asset_id,
            "task_thread_id": getattr(selected_task, "thread_id", None) if selected_task else None,
            # 暫定: Calendar コメント/リテイク schema 確認中 → 空リスト (cmd_475 Phase A)
            "comment_list": [],
            "retake_history": [],
        },
    )


@router.post("/qc/{id}/retake")
def post_qc_retake(
    id: int,
    retake_comment: str = Form(...),
    asset_id: int | None = Form(None),
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    shot = client.get_shot(id, actor_user_id=actor_id)
    project_id = shot.project_id if shot else None
    directors = client.get_project_directors(project_id, actor_user_id=actor_id) if project_id else []
    pms = client.get_project_pms(project_id, actor_user_id=actor_id) if project_id else []
    notify_uids = list({str(u) for u in directors + pms})
    if notify_uids:
        title = f"Retake 発行 — SHOT_{id}" + (f" asset_id={asset_id}" if asset_id else "")
        body = f"{actor_id} が Retake を発行しました: {retake_comment[:120]}"
        client.send_notification_to_users(notify_uids, title, body, actor_user_id=actor_id)
    return {"ok": True, "notified": len(notify_uids)}


@router.get("/reference/{id}")
def get_reference_viewer(
    id: int,
    request: Request,
    actor_id: str = Depends(get_actor_id),
    task_id: int | None = None,
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
    project_name = resolve_project_name(shot.project_id, actor_id) if shot else "-"
    project_id = shot.project_id if shot else None
    seq_code = getattr(shot, "seq_code", None) if shot else None
    selected_task = _resolve_task(client, id, task_id, actor_id)
    task_name = selected_task.type if selected_task else None

    return _templates.TemplateResponse(
        request=request,
        name="reference_viewer.html",
        context={
            "tasks": tasks, "shot_id": id, "project_name": project_name,
            "project_id": project_id, "seq_code": seq_code,
            "task_id": task_id, "task_name": task_name,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
        },
    )
