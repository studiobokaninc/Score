import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client
from app.qc_delegation import is_qc_delegated

router = APIRouter()
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)

@router.get("/director_retake_input")
def get_director_retake_input(
    request: Request,
    actor_id: str = Depends(get_actor_id),
    shot_id: int | None = None,
    task_id: int | None = None,
    as_role: str | None = None,  # 殿御命 2026-06-05 (B 案): admin 限定 role preview
):
    actual_role = get_actor_role(actor_id)
    # admin 元 user は as_role=director で進入可
    role = "director" if (as_role == "director" and actual_role == "admin") else actual_role
    # 殿御命 2026-06-09: admin=Director 同等で直接可・pm も可・案A 委任 user も この依頼に限り可
    _delegated = is_qc_delegated(actor_id, task_id=task_id, shot_id=shot_id)
    if role not in ("director", "pm", "admin") and not _delegated:
        # 殿御命 2026-06-09: 権限なき者 (push/link 経由含む) は生 JSON 403 でなく QC ビューアへ誘導
        from fastapi.responses import RedirectResponse as _RR
        if shot_id is not None:
            _q = f"?task_id={task_id}" if task_id else ""
            return _RR(url=f"/qc/{shot_id}{_q}", status_code=303)
        return _RR(url="/dashboard", status_code=303)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        projects = client.get_my_projects(actor_user_id=actor_id)
        pid = projects[0]["id"] if projects else 33
        shots = client.get_shots(pid, actor_user_id=actor_id)
    except Exception:
        shots = []

    # 殿御命 2026-06-05: shot_id 受領時 動的に shot/asset 解決
    shot_code = ""
    seq_code = ""
    proj_name = ""
    task_type = ""
    latest_asset = None
    asset_list = []
    assignee_name = ""   # 殿御命 2026-06-09: 通知先 = 実 assignee
    assignee_uid = None
    if shot_id is not None:
        # shot DTO
        try:
            s_dto = client.get_shot(int(shot_id), actor_user_id=actor_id)
            if s_dto:
                shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "") or f"SHOT_{shot_id:03d}"
                seq_code = getattr(s_dto, "seq_code", "") or ""
                pj = getattr(s_dto, "project_id", None)
                if pj is not None:
                    try:
                        if hasattr(client, "get_project"):
                            p = client.get_project(int(pj), actor_user_id=actor_id) or {}
                            proj_name = p.get("name") or ""
                        if not proj_name:
                            for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                                if isinstance(p, dict) and p.get("id") == pj:
                                    proj_name = p.get("name") or ""; break
                    except Exception:
                        pass
        except Exception:
            pass
        # asset_list (task_id filter)
        try:
            if hasattr(client, "get_shot_detail"):
                shot_dict = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
                asset_list = list(shot_dict.get("asset_list", []) or [])
                if task_id:
                    asset_list = [a for a in asset_list if isinstance(a, dict) and a.get("task_id") == task_id]
                asset_list.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else "") or "", reverse=True)
                if asset_list:
                    latest_asset = asset_list[0]
        except Exception:
            pass
        # task_type
        if task_id:
            try:
                for tk in (client.get_tasks(int(shot_id), actor_user_id=actor_id) or []):
                    tid = getattr(tk, "task_id", None) if not isinstance(tk, dict) else (tk.get("id") or tk.get("task_id"))
                    if tid == task_id:
                        task_type = (getattr(tk, "type", "") if not isinstance(tk, dict) else (tk.get("type") or tk.get("task_type") or "")) or ""
                        break
            except Exception:
                pass
        # 殿御命 2026-06-09: 通知先表示を実 assignee に (ハードコード 'Sato' 撤廃)
        if task_id:
            try:
                if hasattr(client, "get_task"):
                    _tr = client.get_task(int(task_id), actor_user_id=actor_id) or {}
                    _au = _tr.get("assigned_to") or _tr.get("assignee_id") if isinstance(_tr, dict) else None
                    if _au is not None:
                        assignee_uid = int(_au)
                        for u in (client.get_users(actor_user_id=actor_id) or []):
                            if isinstance(u, dict):
                                _uid = u.get("id") or u.get("user_id")
                                if _uid is not None and int(_uid) == assignee_uid:
                                    assignee_name = (u.get("name") or u.get("full_name") or (u.get("email") or "").split("@")[0]) or ""
                                    break
            except Exception:
                pass

    return _templates.TemplateResponse(
        request=request, name="director_retake_input.html",
        context={
            "role": role, "active": "director",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "shots": shots,
            "shot_id": shot_id or 0,
            "task_id": task_id,
            "shot_code": shot_code,
            "seq_code": seq_code,
            "proj_name": proj_name,
            "task_type": task_type,
            "latest_asset": latest_asset,
            "asset_list": asset_list,
            "assignee_name": assignee_name,
            "assignee_uid": assignee_uid,
        },
    )


@router.get("/retake_view/{shot_id}/{task_id}")
def get_retake_view(
    request: Request,
    shot_id: int,
    task_id: int,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-05 (②採択): Retake 内容 view-only page
    SHOT thread 最新 Retake 内容 parse + 添付素材表示 + task thread link"""
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None

    # 階層解決
    proj_name = seq_code = shot_code = task_type = ""
    try:
        s_dto = client.get_shot(shot_id, actor_user_id=actor_id)
        if s_dto:
            shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "") or f"SHOT_{shot_id:03d}"
            seq_code = getattr(s_dto, "seq_code", "") or ""
            pid = getattr(s_dto, "project_id", None)
            if pid is not None:
                try:
                    if hasattr(client, "get_project"):
                        p = client.get_project(int(pid), actor_user_id=actor_id) or {}
                        proj_name = p.get("name") or ""
                    if not proj_name:
                        for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                            if isinstance(p, dict) and p.get("id") == pid:
                                proj_name = p.get("name") or ""; break
                except Exception: pass
    except Exception: pass
    try:
        for tk in (client.get_tasks(shot_id, actor_user_id=actor_id) or []):
            tid = getattr(tk, "task_id", None)
            if tid == task_id:
                task_type = getattr(tk, "type", "") or ""
                break
    except Exception: pass
    # task full name (Compositing 等)
    task_name = task_type
    if hasattr(client, "get_task"):
        try:
            t_raw = client.get_task(task_id, actor_user_id=actor_id) or {}
            task_name = t_raw.get("name") or task_type
        except Exception: pass

    # 最新 retake meta を /tmp/score_retake_refs/ から検索 (task_id 一致)
    import json as _json_m
    from pathlib import Path as _Path
    latest_meta = None
    refs_root = _Path("/tmp/score_retake_refs")
    if refs_root.exists():
        candidates = []
        for d in refs_root.iterdir():
            if d.is_dir() and (d / "meta.json").exists():
                try:
                    m = _json_m.loads((d / "meta.json").read_text(encoding="utf-8"))
                    if str(m.get("task_id")) == str(task_id) and str(m.get("shot_id")) == str(shot_id):
                        candidates.append((m.get("submitted_at",""), m, d))
                except Exception: pass
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            latest_meta = candidates[0][1]
            latest_meta["_dir"] = str(candidates[0][2])
            # 殿御命 2026-06-05: submitted_by uid → name 解決
            sb = latest_meta.get("submitted_by")
            if sb:
                try:
                    from app.adapters.calendar_client import _to_calendar_uid
                    sb_cuid = _to_calendar_uid(str(sb))
                    target_uid = int(sb_cuid) if sb_cuid is not None else (int(sb) if str(sb).isdigit() else None)
                    if target_uid is not None and hasattr(client, "get_users"):
                        for u in (client.get_users(actor_user_id=actor_id) or []):
                            if isinstance(u, dict):
                                uid = u.get("id") or u.get("user_id")
                                if uid is not None and int(uid) == target_uid:
                                    latest_meta["submitted_by_name"] = u.get("name") or u.get("full_name") or (u.get("email") or "").split("@")[0] or f"uid {target_uid}"
                                    break
                except Exception: pass

    # 殿御命 2026-06-05: 対象 asset 取得 (qc_viewer 同様の latest mp4 / 最新 asset)
    target_asset = None
    target_url = ""
    try:
        if hasattr(client, "get_shot_detail"):
            shot_dict = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
            assets_for_task = [a for a in (shot_dict.get("asset_list") or []) if isinstance(a, dict) and a.get("task_id") == task_id]
            assets_for_task.sort(key=lambda a: (a.get("created_at") or ""), reverse=True)
            if assets_for_task:
                target_asset = assets_for_task[0]
                _bn = (target_asset.get("file_path") or "").split("/")[-1]
                if _bn:
                    target_url = f"http://192.168.44.253:8001/static/assets/{_bn}"
    except Exception: pass

    # 殿御命 2026-06-05: SHOT thread 探索 — 段階 fallback
    # ① shot_code + (task_type or task_name) を含む thread (= 該当 task 関連の SHOT thread)
    # ② shot_code を含む thread
    # ③ 任意の最新 SHOT 関係者 thread (participants > 2 or QC/Review/Approved/Retake content)
    task_thread_id = None
    try:
        if hasattr(client, "get_my_dm_threads"):
            threads = client.get_my_dm_threads(actor_user_id=actor_id) or []
            # 更新日時 降順
            threads_sorted = sorted([t for t in threads if isinstance(t, dict)], key=lambda x: x.get("updated_at", ""), reverse=True)
            # ① shot_code + task 名で完全一致 を探す
            sc_low = (shot_code or "").lower()
            tn_low = (task_name or task_type or "").lower()
            for t in threads_sorted:
                lm = (t.get("last_message") or "").lower()
                if sc_low and tn_low and sc_low in lm and tn_low in lm:
                    task_thread_id = t.get("thread_id"); break
            # ② shot_code のみ
            if not task_thread_id:
                for t in threads_sorted:
                    lm = (t.get("last_message") or "").lower()
                    if sc_low and sc_low in lm:
                        task_thread_id = t.get("thread_id"); break
            # ③ 任意の SHOT thread (3+名 or QC/Review/Retake)
            if not task_thread_id:
                for t in threads_sorted:
                    lm = (t.get("last_message") or "").lstrip()
                    parts_n = len(t.get("participants") or [])
                    if parts_n > 2 or any(lm.startswith(k) for k in ("🔍 QC 依頼", "📌 Review 依頼", "✅ Approved", "🔁 Retake")):
                        task_thread_id = t.get("thread_id"); break
    except Exception: pass

    return _templates.TemplateResponse(
        request=request, name="retake_view.html",
        context={
            "role": role, "active": "qc",
            "user": user,
            "shot_id": shot_id, "task_id": task_id,
            "shot_code": shot_code, "seq_code": seq_code, "proj_name": proj_name,
            "task_type": task_type, "task_name": task_name,
            "latest_meta": latest_meta,
            "task_thread_id": task_thread_id,
            "target_asset": target_asset,
            "target_url": target_url,
        },
    )
