"""書込BFF 10EP — calender_api_complete_list.md §8 実在EPのみ (捏造ゼロ)"""
import os as _os
import json as _json
from pathlib import Path as _Path
from threading import Lock as _Lock

from fastapi import APIRouter, Depends, Path, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Optional

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id, get_actor_role
from app.helpers.task_status import NEW_TASK_STATUSES, OLD_TO_NEW_STATUS, COMPLETED_STATUSES
from app.qc_delegation import is_qc_delegated

# 殿御命 2026-06-04 cmd_477: Web Push subscription store (簡易 file-based)
_PUSH_STORE = _Path("/tmp/score_push_subs.json")
_PUSH_LOCK = _Lock()


def _push_store_read() -> dict:
    if not _PUSH_STORE.exists():
        return {}
    try:
        return _json.loads(_PUSH_STORE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def _push_store_write(data: dict) -> None:
    with _PUSH_LOCK:
        _PUSH_STORE.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _send_web_push(subscription: dict, payload: dict) -> tuple[bool, str]:
    """単一 subscription に Web Push 配信。成否 + メッセージ返却。"""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return False, "pywebpush 未導入"
    priv = _os.environ.get("VAPID_PRIVATE_KEY", "")
    sub_email = _os.environ.get("VAPID_CLAIM_SUB", "mailto:noreply@example.com")
    if not priv:
        return False, "VAPID_PRIVATE_KEY 未設定"
    try:
        webpush(
            subscription_info=subscription,
            data=_json.dumps(payload, ensure_ascii=False),
            vapid_private_key=priv,
            vapid_claims={"sub": sub_email},
            ttl=60,
        )
        return True, "sent"
    except Exception as e:
        return False, str(e)[:200]


def _push_to_cuids(cuid_list: list[int], payload: dict) -> dict:
    """Calendar uid list に対応する全 subscription に push 配信。結果 dict 返却。"""
    store = _push_store_read()
    sent = 0
    failed = 0
    details = []
    for cuid in cuid_list:
        subs = store.get(str(cuid), [])
        for sub in subs:
            ok, msg = _send_web_push(sub, payload)
            if ok: sent += 1
            else: failed += 1
            details.append({"cuid": cuid, "ok": ok, "msg": msg})
    return {"sent": sent, "failed": failed, "details": details}

router = APIRouter()

# cmd_141 (2026-07-23・殿御命): Score書込API server側 role/権限検査 新設。
# 従来 role gate は qc_viewer.html / pages_director.py 等 HTML 画面側にのみ存在し、
# 書込 API 本体 (本ファイル) には認可検査が無かった。get_actor_id (認証) はあっても
# get_actor_role / is_qc_delegated (認可) が一度も呼ばれていなかったため、URL 直叩きで
# 無権限アクターが承認・差戻し・状態改変を実行できていた。
# qc_viewer.html の既存クライアント側ゲート (role in ('director','pm','admin') or
# is_qc_delegated) と同一の判定基準を server 側にも新設し fail-closed で拒否する。
PRIVILEGED_TASK_STATUSES = COMPLETED_STATUSES | {"qc_fb"}  # {"ap","client_ap","deliver","qc_fb"}


def _require_qc_judge_authority(actor_id: str, task_id: int | None = None, shot_id: int | None = None) -> None:
    """承認/差戻し/完了相当の判定アクションは Director/PM/Admin、または当該
    task/shot の QC 委任者 (is_qc_delegated・依頼単位の一時委任) のみ許可する。
    それ以外は 403 (fail-closed)。"""
    role = get_actor_role(actor_id)
    if role in ("director", "pm", "admin"):
        return
    if is_qc_delegated(actor_id, task_id=task_id, shot_id=shot_id):
        return
    raise HTTPException(
        status_code=403,
        detail="QC判定権限がありません(Director/PM/Admin、またはこの依頼の委任者のみ実行可)",
    )


@router.post("/api/bff/retakes")
async def post_retakes(request: Request, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05: Retake 発行 + SHOT thread に「🔁 Retake 発令」 投稿 (multipart 対応)"""
    client = get_calendar_client()
    # multipart or JSON 両対応
    content_type = request.headers.get("content-type", "")
    if "multipart" in content_type:
        form = await request.form()
        body = {k: v for k, v in form.items() if not k.startswith("ref_")}
        import json as _json_m
        for jf in ("markers", "comments"):
            v = body.get(jf)
            if isinstance(v, str):
                try: body[jf] = _json_m.loads(v)
                except Exception: body[jf] = []
        # 殿御命 2026-06-05: ref files 実保存 (/tmp/score_retake_refs/{retake_id}/)
        from pathlib import Path as _Path
        import time as _time
        _retake_id = f"r_{int(_time.time())}_{body.get('shot_id','?')}"
        ref_dir = _Path(f"/tmp/score_retake_refs/{_retake_id}")
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_imgs_saved = []
        ref_videos_saved = []
        for k, v in form.items():
            if not (k.startswith("ref_img_") or k.startswith("ref_video_")):
                continue
            if hasattr(v, "filename") and hasattr(v, "read"):
                try:
                    fn = (v.filename or k).replace("/", "_")
                    p = ref_dir / fn
                    content = await v.read()
                    p.write_bytes(content)
                    if k.startswith("ref_img_"): ref_imgs_saved.append(fn)
                    else: ref_videos_saved.append(fn)
                except Exception:
                    pass
        body["retake_id"] = _retake_id
        body["ref_imgs"] = ref_imgs_saved
        body["ref_videos"] = ref_videos_saved
        body["ref_img_count"] = len(ref_imgs_saved)
        body["ref_video_count"] = len(ref_videos_saved)
        # retake meta を別途 JSON 保存 (view 用)
        meta_path = ref_dir / "meta.json"
        try:
            meta_path.write_text(_json_m.dumps({
                "retake_id": _retake_id,
                "shot_id": body.get("shot_id"),
                "task_id": body.get("task_id"),
                "asset_id": body.get("asset_id"),
                "direction": body.get("direction") or "",
                "priority": body.get("priority") or "high",
                "due_date": body.get("due_date") or "",
                "reference_url": body.get("reference_url") or "",
                "markers": body.get("markers") or [],
                "comments": body.get("comments") or [],
                "ref_imgs": ref_imgs_saved,
                "ref_videos": ref_videos_saved,
                "submitted_at": __import__("datetime").datetime.now().isoformat(),
                "submitted_by": actor_id,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    else:
        body = await request.json()

    # 殿御命 2026-06-05: multipart の場合 全 field string → int 変換
    def _to_int(v):
        try: return int(v) if v not in (None, "", "None") else None
        except (ValueError, TypeError): return None
    shot_id = _to_int(body.get("shot_id"))
    task_id = _to_int(body.get("task_id"))
    asset_id = _to_int(body.get("asset_id"))
    direction = (body.get("direction") or "").strip()
    priority = body.get("priority") or "high"
    due_date = body.get("due_date") or ""
    comments_list = body.get("comments") or []
    markers_list = body.get("markers") or []
    ref_url = (body.get("reference_url") or "").strip()

    # cmd_141: Retake(差戻し)発行は判定権限アクター限定 (既存 UI ゲートの server 側実装)
    _require_qc_judge_authority(actor_id, task_id=task_id, shot_id=shot_id)

    # 階層解決
    from app.adapters.calendar_client import _to_calendar_uid
    sender_cuid = _to_calendar_uid(actor_id)
    sender_cuid_int = int(sender_cuid) if sender_cuid is not None else None
    proj_name = seq_code = shot_code = task_type = ""
    pid = None
    if shot_id is not None:
        try:
            si = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
            shot_code = si.get("shotID") or si.get("name") or ""
            seq_code = si.get("seqID") or ""
            pid = si.get("project_id")
        except Exception: pass
        if not shot_code or not seq_code or pid is None:
            try:
                s_dto = client.get_shot(int(shot_id), actor_user_id=actor_id)
                if s_dto:
                    if not shot_code: shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "")
                    if not seq_code: seq_code = getattr(s_dto, "seq_code", "") or ""
                    if pid is None: pid = getattr(s_dto, "project_id", None)
            except Exception: pass
        if not shot_code: shot_code = f"SHOT_{int(shot_id):03d}"
        if task_id:
            try:
                for tk in (client.get_tasks(int(shot_id), actor_user_id=actor_id) or []):
                    tid = tk.get("id") or tk.get("task_id") if isinstance(tk, dict) else getattr(tk, "task_id", None)
                    if tid == task_id:
                        task_type = (tk.get("type") if isinstance(tk, dict) else getattr(tk, "type", "")) or ""
                        break
            except Exception: pass
    # cmd_092b: shot_id=0 (SHOT_000・shot 紐付なし task) は上記 shot 系 lookup が常に
    # 空を返す (実在しない shot のため)。post_qc_notify_existing (cmd_091c) と同一設計で
    # task_id から project_id を直接解決する fallback を挟む。これが無いと pid=None の
    # まま→通知先(parts)がハードコード FALLBACK_PM(uid52) に誤フォールバックし、実
    # PM/Director/Lead に Retake 発令通知が届かない静かな誤送信となる (400 にも
    # クラッシュにもならず検出困難・subtask_092a 発見)。
    if pid is None and task_id and hasattr(client, "get_task"):
        try:
            task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
            if pid is None:
                pid = task_info.get("project_id")
            if not seq_code:
                seq_code = task_info.get("seqID") or ""
            if not task_type:
                task_type = task_info.get("type") or ""
        except Exception:
            pass
    if pid is not None and not proj_name and hasattr(client, "get_project"):
        try:
            p = client.get_project(int(pid), actor_user_id=actor_id) or {}
            proj_name = p.get("name") or ""
        except Exception: pass

    # cmd_075 (2026-07-08): TaskStatus 新19値対応 — retake 発令 → 'qc_fb' (社内フィードバック) に PATCH
    if task_id and hasattr(client, "patch_task"):
        try: client.patch_task(int(task_id), {"status": "qc_fb"}, actor_user_id=actor_id)
        except Exception: pass

    # SHOT thread に Retake 発令 投稿
    thread_id = None
    push_result = {"sent": 0, "failed": 0, "details": []}
    sse_result = {"delivered": 0, "skipped_no_listener": 0}
    try:
        # 既存 thread を探す or 新規作成 (post_dm_thread = task_id 単位 で 一意 or 新規)
        # 簡易: shot 関係者 PM/Director/Lead + sender でカレンダー側 thread 作成
        FALLBACK_PM = 52
        roles = {}
        if pid is not None and hasattr(client, "get_project_roles"):
            try: roles = client.get_project_roles(int(pid), actor_user_id=actor_id) or {}
            except Exception: pass
        parts = set()
        parts.add(int(roles.get("pm") or FALLBACK_PM))
        if roles.get("director"): parts.add(int(roles["director"]))
        if roles.get("lead") or roles.get("lighting_lead"): parts.add(int(roles.get("lead") or roles.get("lighting_lead")))
        # assignee 自動追加 (task)
        if task_id:
            try:
                tr = client.get_task(int(task_id), actor_user_id=actor_id) or {} if hasattr(client, "get_task") else {}
                a = tr.get("assigned_to") or tr.get("assignee_id")
                if a: parts.add(int(a))
            except Exception: pass
        if sender_cuid_int is not None: parts.add(sender_cuid_int)
        participants = sorted(parts)

        # sender name
        sender_name = actor_id
        try:
            me = client.get_me(actor_user_id=actor_id)
            nm = getattr(me, "name", "") or (getattr(me, "email", "") or "").split("@")[0]
            if nm: sender_name = nm
        except Exception: pass

        # 殿御命 2026-06-05 (②採択): Retake 通知 URL は retake_view へ
        # cmd_094a (SHOT000-PROACTIVE-AUDIT): shot_id=0 (SHOT_000) は旧 `(shot_id and task_id)`
        # が 0 を falsy 誤判定し、task_id 付き retake_view でなく task_id 無しの曖昧な
        # /qc/0 (どの shotless task か特定不能) に誤フォールバックしていた。
        import os as _os
        public_base = _os.environ.get("SCORE_PUBLIC_URL", "").rstrip("/")
        qc_path = f"/retake_view/{shot_id}/{task_id}" if (shot_id is not None and task_id) else f"/qc/{shot_id}"
        qc_link = (public_base + qc_path) if public_base else qc_path

        # body 構築
        hier = [p for p in (proj_name, seq_code, shot_code, task_type) if p]
        title_line = " / ".join(hier) if hier else "(対象未指定)"
        lines = [
            "🔁 Retake 発令",
            title_line,
            "",
            f"優先度: {priority} / 期日: {due_date or '(未指定)'}",
        ]
        if direction: lines.append(f"全体方針: {direction[:300]}")
        if markers_list: lines.append(f"マーカー: {len(markers_list)} 点 ({', '.join(markers_list[:5])})")
        if comments_list:
            lines.append(f"コメント ({len(comments_list)} 件):")
            for c in comments_list[:5]:
                if isinstance(c, dict):
                    lines.append(f"  ⏱️ {c.get('tc','')}: {(c.get('text','') or '')[:80]}")
        if ref_url: lines.append(f"参考 URL: {ref_url}")
        nimg = body.get("ref_img_count", 0); nvid = body.get("ref_video_count", 0)
        if nimg or nvid: lines.append(f"添付: 静止画 {nimg} / 動画 {nvid}")
        if qc_link:
            lines.append("")
            lines.append(qc_link)
        lines.append(""); lines.append(f"— {sender_name} (Director)")
        body_text = "\n".join(lines)

        if hasattr(client, "post_dm_thread") and len(participants) >= 2:
            thread_resp = client.post_dm_thread(participant_ids=participants, task_id=task_id, actor_user_id=actor_id)
            thread_id = thread_resp.get("thread_id") or thread_resp.get("id")
            if thread_id and hasattr(client, "post_dm"):
                client.post_dm(int(thread_id), body_text, actor_user_id=actor_id)
                # push / SSE 配信
                from app.routers.pages_notif_settings import get_user_prefs
                from app.routers.sse_notifications import push_sse_event
                payload = {"title": f"🔁 Retake 発令: {title_line}", "body": (direction or "新たな Retake が発令されました")[:200], "url": qc_path, "tag": f"score-retake-{thread_id}"}
                push_t = []; sse_t = []
                for cuid in participants:
                    if sender_cuid_int is not None and cuid == sender_cuid_int: continue
                    pref = get_user_prefs(int(cuid))
                    if pref.get("channels", {}).get("push", True): push_t.append(cuid)
                    if pref.get("channels", {}).get("sse", True): sse_t.append(cuid)
                if push_t: push_result = _push_to_cuids(push_t, payload)
                if sse_t: sse_result = push_sse_event(sse_t, "notif", payload)
    except Exception as e:
        return JSONResponse(content={"ok": False, "error": str(e)[:200]}, status_code=500)

    # Calendar post_retakes も並行 (旧経路)
    try:
        result = client.post_retakes(body, actor_user_id=actor_id) if hasattr(client, "post_retakes") else {}
    except Exception:
        result = {}
    return JSONResponse(content={"ok": True, "thread_id": thread_id, "participants": list(parts) if 'parts' in dir() else [], "qc_link": qc_link if 'qc_link' in dir() else "", "push_result": push_result, "sse_result": sse_result, "calendar_result": result}, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/shots/{id}/approve")
def post_shot_approve(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_shot_approve(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/look_distributions")
def post_look_distributions(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_look_distributions(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/timecards/clock_out")
def post_timecard_clock_out(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    # 殿御命 2026-06-09: 退勤(申し送り含む)を Score DB にも保存 (Calendar 送信前に記録=取りこぼし防止)
    try:
        import json as _json2, sys as _s2
        from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
        from app.database import SessionLocal as _SL
        from app.models import TimecardLog as _TL
        from app.adapters.calendar_client import _to_calendar_uid as _tcu
        _cuid = _tcu(actor_id)
        _date = (body.get("date") or (_dt2.now(_tz2(_td2(hours=9))).date().isoformat()))
        _db = _SL()
        try:
            _db.add(_TL(
                user_id=str(_cuid) if _cuid is not None else str(actor_id),
                date=str(_date)[:10] or None,
                clock_out_time=str(body.get("clock_out_time") or "")[:40] or None,
                mode=str(body.get("mode") or "")[:40] or None,
                blocker=str(body.get("blocker") or "")[:5000] or None,
                handover=str(body.get("handover") or "")[:5000] or None,
                next_priority=str(body.get("next_priority") or "")[:5000] or None,
                raw_json=_json2.dumps(body, ensure_ascii=False)[:20000],
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"[timecard_log] skip: {_e}", file=_s2.stderr)
    result = client.post_timecard_clock_out(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/routines")
def post_routines(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-05: routine 提出 → Calendar 経由保存 + cookie 'score_routine_done' set
    cookie 値 = submitted_at (ISO+TZ)。次 5am JST まで有効。login 動線で 当日 routine skip + dashboard 出勤時刻表示に活用。
    """
    client = get_calendar_client()
    # body に submitted_at が無ければ server 時刻 (JST) を補完
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _jst = _tz(_td(hours=9))
    if not body.get("submitted_at"):
        body["submitted_at"] = _dt.now(_jst).isoformat(timespec="seconds")
    # 殿御命 2026-06-09: 体調含む routine を Score DB にも保存 (Calendar 送信前に記録=取りこぼし防止)
    try:
        from app.database import SessionLocal as _SL
        from app.models import RoutineLog as _RL
        from app.adapters.calendar_client import _to_calendar_uid as _tcu
        _cuid = _tcu(actor_id)
        _db = _SL()
        try:
            _db.add(_RL(
                user_id=str(_cuid) if _cuid is not None else str(actor_id),
                condition=str(body.get("condition") or "")[:20] or None,
                date=str(body.get("date") or "")[:10] or None,
                submitted_at=str(body.get("submitted_at") or "")[:40] or None,
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        import sys as _s; print(f"[routine_log] skip: {_e}", file=_s.stderr)
    result = client.post_routines(body, actor_user_id=actor_id)
    # cookie set (次 5am JST まで)
    from app.auth import get_next_5am_jst
    exp = get_next_5am_jst()
    max_age = max(0, int((exp - _dt.now(_tz.utc)).total_seconds()))
    resp = JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})
    resp.set_cookie(
        key="score_routine_done",
        value=body["submitted_at"],
        httponly=False,  # JS から読めるように (UI 表示用)
        samesite="lax",
        path="/",
        secure=False,
        max_age=max_age,
    )
    return resp


@router.post("/api/bff/change_requests")
def post_change_requests(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_change_requests(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/troubles")
def post_troubles(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-05 (C 案準拠): Lead 不在 + mention 無 → 拒否"""
    client = get_calendar_client()
    shot_id = body.get("shot_id")
    task_id = body.get("task_id")
    mentions = body.get("mentions") or []
    # Lead 解決 (shot → project → roles)
    lead_uid = None
    project_id = None
    if shot_id is not None and hasattr(client, "get_shot_detail"):
        try:
            shot_info = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
            project_id = shot_info.get("project_id")
        except Exception:
            pass
    # cmd_092b: shot_id=0 (SHOT_000・shot 紐付なし task) は旧 `if shot_id` が 0 を
    # falsy 誤判定して lookup 自体をスキップしていた (SHOT-ZERO 系と同型バグ)。
    # `is not None` 化しても実在しない shot のため project_id は解決できないので、
    # post_qc_notify_existing/post_retakes (cmd_091c/092b) と同一設計で task_id
    # から project_id を直接解決する fallback を追加する。無いと shotless task の
    # トラブル報告が (mention 未指定時は) 常に「Lead 未設定」400 に落ちる。
    if project_id is None and task_id and hasattr(client, "get_task"):
        try:
            task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
            project_id = task_info.get("project_id")
        except Exception:
            pass
    if project_id is not None and hasattr(client, "get_project_roles"):
        try:
            roles = client.get_project_roles(int(project_id), actor_user_id=actor_id) or {}
            lead_uid = roles.get("lead") or roles.get("lighting_lead")
        except Exception:
            pass
    if lead_uid is None and not mentions:
        raise HTTPException(
            status_code=400,
            detail="Lead 未設定 project: 送信先を mention で指定してください (代替担当を選択)"
        )
    # Calendar post_troubles に pass-through (mentions も付加)
    payload = {**body, "mentions": mentions}
    if lead_uid is not None:
        payload.setdefault("lead_uid", int(lead_uid))
    result = client.post_troubles(payload, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/troubles/{id}/resolve")
def patch_trouble_resolve(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.patch_trouble_resolve(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/messages")
def post_messages(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.post_messages(body, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/notifications/{id}/read")
def patch_notification_read(
    id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    result = client.patch_notification_read(id, body or {}, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/look_distributions/{id}/accept")
def patch_look_distribution_accept(
    id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """Look 配布 受諾 (nibu 殿御回答 2026-06-01 F 高)"""
    client = get_calendar_client()
    result = client.patch_look_distribution_accept(id, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.patch("/api/bff/look_distributions/{id}/complete")
def patch_look_distribution_complete(
    id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """Look 配布 完了通知 (nibu 殿御回答 2026-06-01 F 高)"""
    client = get_calendar_client()
    result = client.patch_look_distribution_complete(id, actor_user_id=actor_id)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/assets")
async def post_asset_upload(
    file: UploadFile = File(...),
    task_id: Optional[int] = Form(None),
    shot_id: Optional[int] = Form(None),
    version: Optional[str] = Form(None),
    submission_type: Optional[str] = Form(None),   # 殿御命 2026-06-03: 'qc' | 'review'
    mentions: Optional[str] = Form(None),           # 殿御命 2026-06-03: カンマ区切り uid/email
    actor_id: str = Depends(get_actor_id),
):
    """QC/review asset upload (殿御命 2026-06-01)
    multipart pass-through → Calendar POST /api/assets
    殿御命 2026-06-03: submission_type (qc/review) + mentions を受領 (Phase 1: log のみ・Phase 2 cmd で通知作成)
    殿御命 2026-06-03: QC/review は 500MB 上限 (実データ納品は別経路)"""
    client = get_calendar_client()
    content = await file.read()
    # server side size check (client side JS と二重防壁)
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large: {len(content)//1024//1024}MB > 500MB (QC/review max・実データ納品は別経路)")
    _filename = file.filename or "upload.bin"
    _content_type = file.content_type or "application/octet-stream"
    # 殿御命 2026-07-06 (cmd_068②③): 変換前の原本を保持 (DL は常に原本を返す・プレビューのみ変換後)
    _original_content = content
    _original_filename = _filename
    _needs_original_preserve = False
    # .mov → .mp4 自動トランスコード (cmd_059)
    if _filename.lower().endswith('.mov') or _content_type == 'video/quicktime':
        import subprocess as _sp, tempfile as _tf
        _needs_original_preserve = True
        try:
            import imageio_ffmpeg as _iff
            _ffmpeg_exe = _iff.get_ffmpeg_exe()
        except Exception:
            _ffmpeg_exe = 'ffmpeg'  # fallback: system ffmpeg
        _tmp_in = _tf.NamedTemporaryFile(suffix='.mov', delete=False)
        _tmp_out = _tmp_in.name[:-4] + '.mp4'
        try:
            _tmp_in.write(content)
            _tmp_in.close()
            _proc = _sp.run(
                [_ffmpeg_exe, '-y', '-i', _tmp_in.name,
                 '-c:v', 'libx264', '-c:a', 'aac', '-movflags', '+faststart',
                 _tmp_out],
                capture_output=True, timeout=300,
            )
            if _proc.returncode == 0 and _os.path.exists(_tmp_out):
                with open(_tmp_out, 'rb') as _f:
                    content = _f.read()
                _filename = (_filename[:-4] if _filename.lower().endswith('.mov') else _filename) + '.mp4'
                _content_type = 'video/mp4'
            else:
                _err = _proc.stderr.decode('utf-8', errors='replace')[:300]
                raise HTTPException(status_code=422, detail=f".mov→.mp4 変換失敗: {_err}")
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="ffmpeg 未インストール — imageio-ffmpeg が requirements.txt に追加済のため docker.exe restart で自動解決します")
        finally:
            if _os.path.exists(_tmp_in.name):
                _os.unlink(_tmp_in.name)
            if _os.path.exists(_tmp_out):
                _os.unlink(_tmp_out)
    # .exr → プレビューPNG 単純変換 (cmd_068・殿御命: トーンマップ不要・単純変換のみ)
    elif _filename.lower().endswith('.exr') or _content_type in ('image/x-exr', 'image/aces'):
        import tempfile as _tf
        _needs_original_preserve = True
        _tmp_in = _tf.NamedTemporaryFile(suffix='.exr', delete=False)
        try:
            _tmp_in.write(content)
            _tmp_in.close()
            import numpy as _np
            import OpenEXR as _OpenEXR
            from PIL import Image as _Image
            import io as _io
            try:
                _exrf = _OpenEXR.File(_tmp_in.name)
            except Exception as _e:
                raise HTTPException(status_code=422, detail=f".exr 読込失敗 (破損または非対応形式): {str(_e)[:300]}")
            _part = _exrf.parts[0]
            _chs = _part.channels
            if 'RGBA' in _chs:
                _rgb = _chs['RGBA'].pixels[..., :3]
            elif 'RGB' in _chs:
                _rgb = _chs['RGB'].pixels[..., :3]
            elif all(c in _chs for c in ('R', 'G', 'B')):
                _rgb = _np.stack([_chs['R'].pixels, _chs['G'].pixels, _chs['B'].pixels], axis=-1)
            else:
                raise HTTPException(status_code=422, detail=f".exr: RGB相当のチャンネルが見つからず (channels={list(_chs.keys())})")
            # 単純変換のみ (殿御命: トーンカーブ調整・露出/トーンマップ不要)。
            # HDR float 値を [0,1] へ clamp → 簡易 sRGB ガンマのみ (真っ黒/白飛びの最低限回避)。
            _rgb = _np.clip(_rgb.astype(_np.float32), 0.0, 1.0)
            _rgb = _np.power(_rgb, 1.0 / 2.2)
            _img8 = (_rgb * 255.0 + 0.5).astype(_np.uint8)
            _buf = _io.BytesIO()
            _Image.fromarray(_img8, 'RGB').save(_buf, format='PNG')
            content = _buf.getvalue()
            _filename = (_filename[:-4] if _filename.lower().endswith('.exr') else _filename) + '_preview.png'
            _content_type = 'image/png'
        except HTTPException:
            raise
        except Exception as _e:
            raise HTTPException(status_code=422, detail=f".exr→PNG変換失敗: {str(_e)[:300]}")
        finally:
            if _os.path.exists(_tmp_in.name):
                _os.unlink(_tmp_in.name)
    try:
        result = client.post_asset(
            file_data=content,
            filename=_filename,
            content_type=_content_type,
            actor_user_id=actor_id,
            task_id=task_id,
            shot_id=shot_id,
            version=version,
        )
    except Exception as _e:
        _cal_body = getattr(getattr(_e, "response", None), "text", str(_e))[:300]
        raise HTTPException(status_code=502, detail=f"Calendar /api/assets エラー: {_cal_body}")
    # 殿御命 2026-07-06 (cmd_068②③): 変換を経た asset は原本を Score ローカルに保存
    # (Calendar には変換後ファイルのみ送信されるため、DL 用の原本はここでしか保持できない)
    if _needs_original_preserve:
        try:
            _asset_id_for_orig = result.get("id") or result.get("asset_id") if isinstance(result, dict) else None
            if _asset_id_for_orig is not None:
                from app.helpers.asset_originals import save_original as _save_original
                _save_original(int(_asset_id_for_orig), _original_filename, _original_content)
        except Exception as _e:
            import sys as _s_orig
            print(f"[asset_original] save skip: {_e}", file=_s_orig.stderr)
    # 殿御命 2026-06-04 (cmd_476): review/QC 提出時 SHOT 関係者全員 thread に依頼 自動投稿
    # 御方針: QC=Director 自動 + PM 必須 / Review=mention 主体 + PM 必須
    #         SHOT thread = PM + Director + Lighting Lead + その SHOT の task assignee 全員
    #         (殿御指摘 2026-06-04: 個別 DM ではなく SHOT 関係者 全員に届くべき)
    # 暫定 hardcode: Calendar 側に project.director_id / pm_id 解決 EP 不在 (Phase 2 nibu 殿差替)
    # 殿御命 2026-07-07 (cmd_070③): 通常アップロード(submission_type 無指定)時も同じ SHOT thread へ
    #   通知メッセージ(QC ビューアリンク付き)を投稿する。qc/review 提出時専用の副作用
    #   (task status patch・QC delegation 記録・Director 必須チェック)はここから分離する。
    is_qc_review = submission_type in ("qc", "review")
    if is_qc_review:
        # cmd_075 (2026-07-08): task status を 'qc' に patch (wip 等 → qc)
        # これにより qc_viewer の判定 block (_can_judge = status in JUDGE_TARGET_STATUSES) が活性化する
        # (通常アップロード時にこの副作用が起きるのは意図しない挙動のため qc/review 提出時限定)
        if task_id and hasattr(client, "patch_task"):
            try:
                client.patch_task(int(task_id), {"status": "qc"}, actor_user_id=actor_id)
            except Exception:
                pass
    # project / seq / shot / task 階層解決 (殿御命 2026-06-04/05: get_shot_detail 空時 get_shot DTO + get_tasks fallback)
    # (通知本文タイトル階層 + qc_link 構築に使うため submission_type 問わず実行)
    proj_name = ""
    seq_code = ""
    shot_code = ""
    task_type = ""
    shot_assignee_uids: set[int] = set()
    pid = None
    if shot_id:
        try:
            shot_info = client.get_shot_detail(shot_id, actor_user_id=actor_id) or {}
            shot_code = shot_info.get("shotID") or shot_info.get("name") or ""
            seq_code = shot_info.get("seqID") or shot_info.get("seq_code") or shot_info.get("sequence") or ""
            pid = shot_info.get("project_id")
            # SHOT 内 全 task の assignee + 対象 task の type 取得
            for tk in (shot_info.get("task_list") or shot_info.get("tasks") or []):
                if isinstance(tk, dict):
                    a = tk.get("assignee_id") or tk.get("assigned_to")
                    if a is not None:
                        try: shot_assignee_uids.add(int(a))
                        except (ValueError, TypeError): pass
                    if task_id and (tk.get("id") == task_id or tk.get("task_id") == task_id):
                        task_type = tk.get("type") or tk.get("task_type") or ""
        except Exception:
            pass
        # 殿御命 2026-06-05: fallback (get_shot_detail 空時)
        if not shot_code or not seq_code or pid is None:
            try:
                s_dto = client.get_shot(int(shot_id), actor_user_id=actor_id)
                if s_dto:
                    if not shot_code: shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "")
                    if not seq_code: seq_code = getattr(s_dto, "seq_code", "") or ""
                    if pid is None: pid = getattr(s_dto, "project_id", None)
            except Exception:
                pass
        if not shot_code:
            shot_code = f"SHOT_{shot_id:03d}"
        # task_type fallback: get_tasks(shot_id)
        if not task_type and task_id:
            try:
                for tk in (client.get_tasks(int(shot_id), actor_user_id=actor_id) or []):
                    tid = tk.get("id") or tk.get("task_id") if isinstance(tk, dict) else getattr(tk, "task_id", None)
                    if tid == task_id:
                        task_type = (tk.get("type") or tk.get("task_type") if isinstance(tk, dict) else getattr(tk, "type", "")) or ""
                        break
            except Exception:
                pass
        # project_name: get_my_projects 経由 (sender 参加分)
        if pid is not None and not proj_name:
            try:
                for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                    if isinstance(p, dict) and p.get("id") == pid:
                        proj_name = p.get("name") or ""
                        break
            except Exception:
                pass
        # 殿御命 2026-06-05: sender 未参加でも get_project (admin) fallback
        if pid is not None and not proj_name and hasattr(client, "get_project"):
            try:
                p = client.get_project(int(pid), actor_user_id=actor_id) or {}
                proj_name = p.get("name") or ""
            except Exception:
                pass
    elif task_id:
        # cmd_084: shot 未設定のプロジェクト管理タスク (PM 系 task。shotID/shot_id 無し・
        # 例 task 3282) は上の `if shot_id:` 一式が丸ごとスキップされ pid が None のまま
        # 残る。この後の director_uid 判定が project 実際の設定に関わらず「Director 未設定」
        # 誤判定となり、is_qc_review (デフォルト qc) 提出時に asset 本体の Calendar 登録
        # (client.post_asset、この時点で既に成功済) 後に誤って 400 を返していた
        # (task/3282 アップロードで実機再現)。task_id から project_id を直接解決する。
        try:
            task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
            pid = task_info.get("project_id")
            seq_code = task_info.get("seqID") or ""
            task_type = task_info.get("type") or ""
        except Exception:
            pass
        # cmd_094a (SHOT000-PROACTIVE-AUDIT): shot_id=0 (SHOT_000) はここに falsy-zero で
        # 到達するが (pid 解決自体は上記で完結・正常)、`if shot_id:` 内にしかない
        # SHOT_xxx placeholder 付与がここでは実行されず、通知タイトルの階層表示から
        # shot 区分が欠落していた (shot_id=None の PM 専用タスクは対象外・区別する)。
        if shot_id is not None and not shot_code:
            shot_code = f"SHOT_{shot_id:03d}"
        if pid is not None and not proj_name:
            try:
                for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                    if isinstance(p, dict) and p.get("id") == pid:
                        proj_name = p.get("name") or ""
                        break
            except Exception:
                pass
        if pid is not None and not proj_name and hasattr(client, "get_project"):
            try:
                p = client.get_project(int(pid), actor_user_id=actor_id) or {}
                proj_name = p.get("name") or ""
            except Exception:
                pass

    # mention 列 → uid 解決
    def _resolve_uids(raw_csv: str | None) -> set[int]:
        uids: set[int] = set()
        if not raw_csv:
            return uids
        for token in (m.strip() for m in raw_csv.split(",") if m.strip()):
            if token.isdigit():
                uids.add(int(token))
            elif "@" in token:
                try:
                    for u in (client.get_users(actor_user_id=actor_id) or []):
                        if isinstance(u, dict) and (u.get("email") or "").lower() == token.lower():
                            uid = u.get("id") or u.get("user_id")
                            if uid is not None:
                                uids.add(int(uid))
                            break
                except Exception:
                    pass
        return uids

    mention_uids = _resolve_uids(mentions)

    if is_qc_review:
        # 殿御命 2026-06-09 (案A): mention された user に『この依頼 1 件限定』で Approve/Retake を委任 (DB 記録)。
        # グローバル昇格ではなく依頼単位。後で QC viewer 表示時・approve/retake 時に参照する。
        # (通常アップロードには承認/差戻し依頼の概念が無いため qc/review 提出時限定)
        try:
            if mention_uids:
                _asset_id_d = (result.get("id") or result.get("asset_id")) if isinstance(result, dict) else None
                from app.database import SessionLocal as _SL
                from app.models import QcDelegation as _QD
                _uids_csv = "," + ",".join(str(u) for u in sorted(mention_uids)) + ","
                _db = _SL()
                try:
                    _db.add(_QD(
                        task_id=str(task_id) if task_id is not None else None,
                        shot_id=str(shot_id) if shot_id is not None else None,
                        asset_id=str(_asset_id_d) if _asset_id_d is not None else None,
                        submission_type=submission_type if submission_type in ("qc", "review") else "qc",
                        mentioned_uids=_uids_csv,
                        requested_by=str(actor_id),
                        status="open",
                    ))
                    _db.commit()
                finally:
                    _db.close()
        except Exception as _de:
            import sys as _s; print(f"[qc_delegation] skip: {_de}", file=_s.stderr)

    # 殿御命 2026-06-05 (C 案): Director 不在 project は mention 必須
    # PM は project 未設定でも fallback で必ず含む (殿御方針「両方とも PM 必須」)
    FALLBACK_PM_CUID = 52
    from app.adapters.calendar_client import _to_calendar_uid
    sender_cuid = _to_calendar_uid(actor_id)
    sender_cuid_int = int(sender_cuid) if sender_cuid is not None else None

    # project_id は上記の project/seq/shot/task 階層解決 (pid, get_shot_detail→get_shot fallback
    # 込み) で解決済みのため再解決しない (cmd_076: ここで get_shot_detail を再呼びし
    # project_id を直接参照するだけの実装だと、Calendar の get_shot_detail レスポンスに
    # project_id が乗らないケースで常に None になり director_uid が引けず QC 提出が
    # 全滅していた — 実機再現で確認)。
    project_roles = {}
    if pid is not None and hasattr(client, "get_project_roles"):
        try:
            project_roles = client.get_project_roles(int(pid), actor_user_id=actor_id) or {}
        except Exception:
            project_roles = {}

    director_uid = project_roles.get("director")
    # 殿御命 2026-06-05 (C 案): Director 不在 + mention 無 → 拒否 (qc/review 提出時限定。
    # 通常アップロードにまでこの制約を課すと Director 未設定 project の通常アップロードが
    # 全滅するため、is_qc_review でのみ強制する)
    if is_qc_review and director_uid is None and not mention_uids:
        raise HTTPException(
            status_code=400,
            detail="Director 未設定 project: 送信先を mention で指定してください (代替担当を選択して御願い致す)"
        )

    # SHOT thread participants 構築
    shot_member_uids: set[int] = set()
    # PM (必ず含む・hardcode fallback OK)
    pm_uid = project_roles.get("pm")
    shot_member_uids.add(int(pm_uid) if pm_uid is not None else FALLBACK_PM_CUID)
    # Director (project 設定あれば追加・無ければ mention で代替)
    if director_uid is not None:
        shot_member_uids.add(int(director_uid))
    # Lead (project 設定あれば追加・無ければ skip — 任意)
    lead_uid = project_roles.get("lead") or project_roles.get("lighting_lead")
    if lead_uid is not None:
        shot_member_uids.add(int(lead_uid))
    # SHOT 内 task assignees
    shot_member_uids |= shot_assignee_uids
    # mention (Director 不在時の代替担当 含む)
    shot_member_uids |= mention_uids
    # sender
    if sender_cuid_int is not None:
        shot_member_uids.add(sender_cuid_int)
    participants = sorted(shot_member_uids)

    # sender display name (本文末尾 署名用)
    sender_name = actor_id
    try:
        me = client.get_me(actor_user_id=actor_id)
        nm = getattr(me, "name", "") or (getattr(me, "email", "") or "").split("@")[0]
        if nm: sender_name = nm
    except Exception:
        pass

    # QC ビューアリンク (殿御命 2026-06-05: task_id + asset_id 含めた正規 URL 自動生成)
    # 注意: モジュール冒頭で `import os as _os` 済 (L2)。ここでローカル再import すると
    # Python が _os を関数スコープ全体でローカル変数と解釈し、L533 の _os 参照が
    # UnboundLocalError になる (cmd_064 .mov アップロード500の真因)。再importしないこと。
    public_base = _os.environ.get("SCORE_PUBLIC_URL", "").rstrip("/")
    qc_link = ""
    if shot_id or task_id:
        # cmd_091 (cmd_084 pid 解決と対): shot_id 未設定 (shot 紐付なし task・SHOT_000。
        # 例 task 3255 project 80 "Score 検証") の場合、従来はこの if 自体が丸ごと
        # skip され本文に QC ビューアへの URL が一切埋め込まれなかった (実機再現:
        # 2026-07-10 21:21 の実際の QC 依頼 DM に URL 無し・Director/委任先が判定画面へ
        # 辿り着く手段が皆無だった)。0 を SHOT_000 sentinel として使い必ず埋め込む
        # (pages_qc.py 側の /qc/0 fallback 修正 [cmd_091] と対で機能する)。
        path = f"/qc/{shot_id if shot_id else 0}"
        qp = []
        if task_id:
            qp.append(f"task_id={task_id}")
        # 提出されたばかりの asset id を埋める (result.id = 新 asset id)
        _aid = result.get("id") if isinstance(result, dict) else None
        if _aid:
            qp.append(f"asset_id={_aid}")
        if not shot_id and pid is not None:
            qp.append(f"project_id={pid}")
        if qp:
            path += "?" + "&".join(qp)
        qc_link = (public_base + path) if public_base else path

    if len(participants) >= 2 and hasattr(client, "post_dm_thread"):
        try:
            thread_resp = client.post_dm_thread(
                participant_ids=participants,
                task_id=task_id,
                actor_user_id=actor_id,
            )
            thread_id = thread_resp.get("thread_id") or thread_resp.get("id")
            if thread_id and hasattr(client, "post_dm"):
                fname = result.get("filename", file.filename or "asset")
                ver = version or "version 未指定"
                # 殿御命 2026-07-07 (cmd_070③): 通常アップロード時は qc/review 提出時と
                # 区別できる専用の見出し・文言を使う (既存 qc/review 文言の流用禁止)
                head = ("🔍 QC 依頼" if submission_type == "qc" else "📌 Review 依頼") if is_qc_review else "📤 アップロード通知"
                # mention 表示
                mention_text = ""
                if mention_uids:
                    names = []
                    try:
                        users = client.get_users(actor_user_id=actor_id) or []
                        uid_to_name = {int(u.get("id") or u.get("user_id") or 0): (u.get("name") or (u.get("email") or "").split("@")[0]) for u in users if isinstance(u, dict)}
                        names = [uid_to_name.get(u, f"uid {u}") for u in sorted(mention_uids)]
                    except Exception:
                        names = [f"uid {u}" for u in sorted(mention_uids)]
                    mention_text = "宛先: " + ", ".join(names)
                # 殿御命 2026-06-04: タイトル階層化 (proj / seq / shot / task)
                hier = [p for p in (proj_name, seq_code, shot_code, task_type) if p]
                title_line = " / ".join(hier) if hier else "(対象未指定)"
                if is_qc_review:
                    lines = [
                        f"{head}",
                        title_line,
                        "",
                        f"{ver} を提出致しました。御手隙の際に御確認願います。",
                        f"ファイル: {fname}",
                    ]
                else:
                    lines = [
                        f"{head}",
                        title_line,
                        "",
                        f"{fname} がアップロードされました。",
                        f"バージョン: {ver}",
                    ]
                if mention_text:
                    lines.append(mention_text)
                if qc_link:
                    # 殿御命 2026-06-04: 本文末尾は URL のみ (Score 側 JS で button 化)
                    lines.append("")
                    lines.append(qc_link)
                lines.append("")
                lines.append(f"— {sender_name}")
                body_text = "\n".join(lines)
                client.post_dm(int(thread_id), body_text, actor_user_id=actor_id)
                # 殿御命 2026-06-04 cmd_478: D 案 — user 設定で Push / SSE 振り分け配信
                from app.routers.pages_notif_settings import get_user_prefs
                from app.routers.sse_notifications import push_sse_event
                # 殿御命 2026-07-09 (cmd_076⑤): push 通知の url が task_id/asset_id 無しの
                # /qc/{shot_id} のみだったため、非member director/PM が (task_id 無指定では
                # 403 になる) get_shot_detail 依存の asset_list 解決に落ちて QC ビューアに
                # 何も表示されなかった(post_qc_notify_existing は既に task_id/asset_id 付き
                # だった非対称を是正・qc_link と同じ組立に統一)。
                # cmd_091: qc_link と同一パターンで shot_id 未設定 (SHOT_000) も 0 sentinel で救済
                _push_url = (f"/qc/{shot_id if shot_id else 0}" if (shot_id or task_id) else "/messages")
                if shot_id or task_id:
                    _push_qp = []
                    if task_id:
                        _push_qp.append(f"task_id={task_id}")
                    _push_aid = result.get("id") if isinstance(result, dict) else None
                    if _push_aid:
                        _push_qp.append(f"asset_id={_push_aid}")
                    if not shot_id and pid is not None:
                        _push_qp.append(f"project_id={pid}")
                    if _push_qp:
                        _push_url += "?" + "&".join(_push_qp)
                push_payload = {
                    "title": f"{head}: {title_line}",
                    "body": (f"{ver} 提出 by {sender_name}" if is_qc_review else f"{fname} アップロード by {sender_name}"),
                    "url": _push_url,
                    "tag": f"score-review-{thread_id}",
                }
                cat_key = ("qc_request" if submission_type == "qc" else "review_request") if is_qc_review else "asset_upload"
                push_targets = []
                sse_targets = []
                skipped_by_pref = []
                for cuid in participants:
                    if sender_cuid_int is not None and cuid == sender_cuid_int:
                        continue  # sender 除外
                    prefs = get_user_prefs(int(cuid))
                    if not prefs.get("categories", {}).get(cat_key, True):
                        skipped_by_pref.append(cuid)
                        continue  # この種別が OFF
                    if prefs.get("channels", {}).get("push", True):
                        push_targets.append(cuid)
                    if prefs.get("channels", {}).get("sse", True):
                        sse_targets.append(cuid)
                push_result = _push_to_cuids(push_targets, push_payload) if push_targets else {"sent": 0, "failed": 0, "details": []}
                sse_result = push_sse_event(sse_targets, "notif", push_payload) if sse_targets else {"delivered": 0, "skipped_no_listener": 0}
                result = {**result, "review_thread_id": thread_id, "shot_thread_participants": participants, "push_result": push_result, "sse_result": sse_result, "skipped_by_pref": skipped_by_pref}
        except Exception as e:
            result = {**result, "review_thread_error": str(e)}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


def _notify_next_artist_on_deliver(client, task_id: int, actor_id: str) -> dict:
    """cmd_106 パートB (2026-07-16): DELIVER遷移時、同一shot内で status='wt'(待機)
    な次工程taskの担当者へPush/SSE通知する(例: FX完成→ライトコンプへ渡す時)。
    次担当を一意に特定できない(0件/複数件)場合は誤送信を避けるため通知をスキップし、
    ログのみ残す(推測で送信しない — cmd_106d 殿仕様)。"""
    import sys as _sys
    if not hasattr(client, "get_task"):
        print(f"[deliver_notify] task_id={task_id}: client.get_task 未実装 — 次工程解決不可・スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "get_task_unsupported"}
    try:
        task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
    except Exception as e:
        print(f"[deliver_notify] task_id={task_id}: get_task 失敗 ({e}) — スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "get_task_failed"}
    shot_id = task_info.get("shot_id")
    if not shot_id:
        print(f"[deliver_notify] task_id={task_id}: shotless task — 次工程解決不可・スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "shotless"}
    try:
        siblings = client.get_tasks(int(shot_id), actor_user_id=actor_id) or []
    except Exception as e:
        print(f"[deliver_notify] task_id={task_id}: get_tasks(shot_id={shot_id}) 失敗 ({e}) — スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "get_tasks_failed"}
    candidates = []
    for t in siblings:
        tid = (t.get("id") or t.get("task_id")) if isinstance(t, dict) else getattr(t, "task_id", None)
        if tid is None or int(tid) == int(task_id):
            continue
        st = (t.get("status") if isinstance(t, dict) else getattr(t, "status", None))
        assignee = (t.get("assigned_to") or t.get("assignee_id")) if isinstance(t, dict) else getattr(t, "assignee_id", None)
        if st == "wt" and assignee is not None:
            candidates.append((tid, assignee))
    if len(candidates) != 1:
        print(f"[deliver_notify] task_id={task_id} shot_id={shot_id}: 次工程候補(status=wt) {len(candidates)}件 — 一意特定不可・スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "ambiguous_or_none", "candidate_count": len(candidates)}
    next_task_id, next_assignee = candidates[0]
    try:
        next_assignee_cuid = int(next_assignee)
    except (ValueError, TypeError):
        print(f"[deliver_notify] task_id={task_id}: 次工程 task_id={next_task_id} の assignee 不正 — スキップ", file=_sys.stderr)
        return {"notified": False, "reason": "invalid_assignee"}
    from app.routers.pages_notif_settings import get_user_prefs
    from app.routers.sse_notifications import push_sse_event
    push_payload = {
        "title": "📦 DELIVER — 次工程 引き継ぎ",
        "body": f"task {task_id} が納品されました。担当タスク(task {next_task_id})に着手可能です。",
        "url": f"/qc/{shot_id}?task_id={next_task_id}",
        "tag": f"score-deliver-{task_id}",
    }
    prefs = get_user_prefs(next_assignee_cuid)
    push_result = {"sent": 0, "failed": 0, "details": []}
    sse_result = {"delivered": 0, "skipped_no_listener": 0}
    if prefs.get("channels", {}).get("push", True):
        push_result = _push_to_cuids([next_assignee_cuid], push_payload)
    if prefs.get("channels", {}).get("sse", True):
        sse_result = push_sse_event([next_assignee_cuid], "notif", push_payload)
    return {
        "notified": True,
        "next_task_id": next_task_id,
        "next_assignee": next_assignee_cuid,
        "push_result": push_result,
        "sse_result": sse_result,
    }


@router.patch("/api/bff/tasks/{task_id}")
def patch_task(
    task_id: int = Path(...),
    body: dict = None,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-03: task status / progress 更新
    Calendar PATCH /api/tasks/{id} pass-through
    (status: 新9値 wt/mk/wip/qc/qc_fb/ap/client_ap/deliver/omit
    — cmd_106 2026-07-16 9値体系刷新。progress: 0-100)"""
    client = get_calendar_client()
    payload = body or {}
    # validation — 新9値のみ受理。旧値は互換のため新値へ正規化して受理 (旧クライアント救済)
    if "status" in payload:
        incoming_status = payload["status"]
        if incoming_status in OLD_TO_NEW_STATUS:
            payload = {**payload, "status": OLD_TO_NEW_STATUS[incoming_status]}
        elif incoming_status not in NEW_TASK_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {incoming_status}")
    if "progress" in payload:
        try:
            p = int(payload["progress"])
            if p < 0 or p > 100:
                raise ValueError("range")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="progress must be 0-100")
    if not payload:
        raise HTTPException(status_code=400, detail="empty body")

    # cmd_141: 判定(承認/差戻し/完了)相当ステータスへの直接書込は判定権限アクター限定。
    # qc/approve・retakes を経由せずこの汎用 EP に直接 status=ap 等を投げる URL 直叩きの
    # 抜け道を塞ぐ (通常の自己管理系遷移 wt/mk/wip/qc/omit は従来通り誰でも書込可)。
    if payload.get("status") in PRIVILEGED_TASK_STATUSES:
        _require_qc_judge_authority(actor_id, task_id=task_id)

    # cmd_141 (任意項目③・限定実装): 完了済 (ap/client_ap/deliver) から未完了への
    # 逆行 (例: ap→wip) は判定権限アクター以外には認めない。全9値の遷移可否を厳密に
    # 検証する完全な状態機械は業務ルールが未確定な箇所があり副作用リスクが高いため
    # 見送り、報告で殿判断を仰ぐ (詳細は report 参照)。ここでは「完了済からの離脱」
    # という最も実害の大きいケースに限定して fail-closed で防ぐ。
    if "status" in payload and hasattr(client, "get_task"):
        try:
            _current_task = client.get_task(task_id, actor_user_id=actor_id) or {}
            _current_status = _current_task.get("status")
        except Exception:
            _current_status = None
        if _current_status in COMPLETED_STATUSES and payload["status"] not in COMPLETED_STATUSES:
            _require_qc_judge_authority(actor_id, task_id=task_id)

    result = client.patch_task(task_id, payload, actor_user_id=actor_id) if hasattr(client, "patch_task") else {"ok": False, "reason": "client method not implemented"}
    # cmd_106 パートB (2026-07-16・殿御命): DELIVER遷移時、次担当アーティストへ通知
    # 「【DELIVER】アーティスト — 担当アーティストに通知(例: FX完成→ライトコンプへ渡す時)」
    deliver_notify = None
    if payload.get("status") == "deliver":
        deliver_notify = _notify_next_artist_on_deliver(client, task_id, actor_id)
    response_content = {**result, "deliver_notify": deliver_notify} if isinstance(result, dict) else result
    return JSONResponse(content=response_content, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/dm/threads")
def post_dm_thread(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-04: 手動 DM thread 作成 (nibu 殿 2026-06-03 実装 pass-through)
    actor を participant_ids に自動 include (Score UI 簡素化)"""
    client = get_calendar_client()
    pids = list(body.get("participant_ids") or [])
    tid = body.get("task_id")
    # 殿御命: actor を participant に自動含める (Score UX 改善)
    from app.adapters.calendar_client import _to_calendar_uid
    actor_cuid = _to_calendar_uid(actor_id)
    if actor_cuid is not None and int(actor_cuid) not in [int(p) for p in pids if str(p).isdigit() or isinstance(p, int)]:
        pids.append(int(actor_cuid))
    if not pids or len(pids) < 2:
        raise HTTPException(status_code=400, detail="participant_ids must contain >= 2 users (incl self)")
    result = client.post_dm_thread(pids, task_id=tid, actor_user_id=actor_id) if hasattr(client, "post_dm_thread") else {"ok": False, "reason": "client method not implemented"}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.post("/api/bff/dm")
def post_dm(
    body: dict,
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-04: DM thread 内 message 送信 (nibu 殿 POST /api/dm pass-through)"""
    client = get_calendar_client()
    tid = body.get("thread_id")
    bd = (body.get("body") or "").strip()
    if not tid or not bd:
        raise HTTPException(status_code=400, detail="thread_id and body required")
    result = client.post_dm(int(tid), bd, actor_user_id=actor_id) if hasattr(client, "post_dm") else {"ok": False, "reason": "client method not implemented"}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.get("/api/bff/push/meta")
def push_meta():
    """殿御命 2026-06-04 cmd_477: VAPID 公開鍵 配布 (Service Worker 購読登録時に使用)"""
    return JSONResponse(content={
        "vapid_public_key": _os.environ.get("VAPID_PUBLIC_KEY", ""),
    })


@router.post("/api/bff/push/subscribe")
async def push_subscribe(request: Request, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-04 cmd_477: Service Worker subscription 受領
    cuid 別に保存 (同一 endpoint は重複登録防止)"""
    from app.adapters.calendar_client import _to_calendar_uid
    cuid = _to_calendar_uid(actor_id)
    if cuid is None:
        raise HTTPException(status_code=400, detail="actor_id → Calendar uid 解決不可")
    body = await request.json()
    if not body or "endpoint" not in body:
        raise HTTPException(status_code=400, detail="subscription endpoint 必須")
    store = _push_store_read()
    key = str(cuid)
    # 殿御命 2026-06-09: 1 user 1 購読に統一。複数 origin/端末/再購読で同一 user に複数 endpoint が
    # 溜まると OS 通知が重複する (殿: Chrome push が 2 個)。最後に subscribe した 1 件のみ保持。
    store[key] = [body]
    _push_store_write(store)
    return JSONResponse(content={"ok": True, "cuid": int(cuid), "total_subs": 1})


@router.post("/api/bff/push/test")
def push_test(actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-04 cmd_477/478: 自分宛 テスト 配信 (Push + SSE 両方発火)"""
    from app.adapters.calendar_client import _to_calendar_uid
    from app.routers.sse_notifications import push_sse_event
    cuid = _to_calendar_uid(actor_id)
    if cuid is None:
        raise HTTPException(status_code=400, detail="cuid 解決不可")
    payload = {
        "title": "Score テスト通知",
        "body": "通知配信 動作確認 — 本将軍より 御確認願いたく",
        "url": "/notification_center",
        "tag": "score-test",
    }
    push_result = _push_to_cuids([int(cuid)], payload)
    sse_result = push_sse_event([int(cuid)], "notif", payload)
    return JSONResponse(content={"push": push_result, "sse": sse_result})


@router.get("/api/bff/retake/refs/{retake_id}/{filename}")
def get_retake_ref(retake_id: str, filename: str, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05: Retake 参考素材 配信"""
    from fastapi.responses import FileResponse
    from pathlib import Path as _P
    # path traversal 防止: retake_id と filename に / 含まれぬ
    if "/" in retake_id or "/" in filename or ".." in retake_id or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid path")
    p = _P(f"/tmp/score_retake_refs/{retake_id}/{filename}")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p))


@router.post("/api/bff/qc/notify_existing")
async def post_qc_notify_existing(request: Request, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05: 既存 asset 参照で QC/Review 依頼を SHOT thread に通知 (新 asset upload なし)
    body: {asset_id, submission_type ('qc'|'review'), mentions (csv), comment(optional)}"""
    body = await request.json()
    asset_id = body.get("asset_id")
    submission_type = body.get("submission_type") or "qc"
    mentions = body.get("mentions") or ""
    comment = (body.get("comment") or "").strip()
    if not asset_id:
        raise HTTPException(status_code=400, detail="asset_id 必須")

    client = get_calendar_client()
    # 既存 asset 情報取得 — shot_id から asset_list 走査
    shot_id_from_asset = None
    task_id_from_asset = None
    version_from_asset = None
    file_path = None
    # heuristic: body に shot_id/task_id があれば優先
    shot_id = body.get("shot_id")
    task_id = body.get("task_id")
    if shot_id and hasattr(client, "get_shot_detail"):
        try:
            shot_info = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
            for a in (shot_info.get("asset_list") or []):
                if isinstance(a, dict) and (a.get("id") == int(asset_id)):
                    task_id_from_asset = a.get("task_id")
                    version_from_asset = a.get("version")
                    file_path = a.get("file_path")
                    shot_id_from_asset = a.get("shot_id")
                    break
        except Exception:
            pass
    shot_id = shot_id_from_asset if shot_id_from_asset is not None else shot_id
    task_id = task_id_from_asset or task_id
    if shot_id is None:
        raise HTTPException(status_code=400, detail="asset から shot_id 解決不可")

    # cmd_075 (2026-07-08): task status を 'qc' に patch (wip 等 → qc)
    if task_id and hasattr(client, "patch_task"):
        try:
            client.patch_task(int(task_id), {"status": "qc"}, actor_user_id=actor_id)
        except Exception:
            pass

    # 既存 POST /api/bff/assets と同じ thread + body 構築 logic を再利用
    # 階層解決 (殿御命 2026-06-05: get_shot_detail 空時 get_shot DTO + get_tasks fallback)
    proj_name, seq_code, shot_code, task_type = "", "", "", ""
    shot_assignee_uids: set[int] = set()
    pid = None
    try:
        shot_info = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
        shot_code = shot_info.get("shotID") or shot_info.get("name") or ""
        seq_code = shot_info.get("seqID") or ""
        pid = shot_info.get("project_id")
        for tk in (shot_info.get("task_list") or shot_info.get("tasks") or []):
            if isinstance(tk, dict):
                a = tk.get("assignee_id") or tk.get("assigned_to")
                if a is not None:
                    try: shot_assignee_uids.add(int(a))
                    except (ValueError, TypeError): pass
                if task_id and (tk.get("id") == task_id or tk.get("task_id") == task_id):
                    task_type = tk.get("type") or tk.get("task_type") or ""
    except Exception:
        pass
    # fallback: get_shot DTO で 補完
    if not shot_code or not seq_code or pid is None:
        try:
            s_dto = client.get_shot(int(shot_id), actor_user_id=actor_id)
            if s_dto:
                if not shot_code: shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "")
                if not seq_code: seq_code = getattr(s_dto, "seq_code", "") or ""
                if pid is None: pid = getattr(s_dto, "project_id", None)
        except Exception:
            pass
    if not shot_code:
        shot_code = f"SHOT_{shot_id:03d}"
    # cmd_091c: shot_id=0 (SHOT_000・shot 紐付なし task) は上記 shot 系 lookup が常に
    # 空を返す (実在しない shot のため)。task_id から project_id を直接解決する
    # (post_asset_upload の cmd_084 elif task_id: 分岐と同一パターン。これが無いと
    # director_uid が永久に引けず、mention 未指定時に誤った「Director 未設定」400 と
    # なる — shot_id=0 の 400 是正だけでは不十分だったため対で修正)。
    if pid is None and task_id and hasattr(client, "get_task"):
        try:
            task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
            if pid is None:
                pid = task_info.get("project_id")
            if not seq_code:
                seq_code = task_info.get("seqID") or ""
            if not task_type:
                task_type = task_info.get("type") or ""
        except Exception:
            pass
    # task_type fallback: get_tasks(shot_id)
    if not task_type and task_id:
        try:
            for tk in (client.get_tasks(int(shot_id), actor_user_id=actor_id) or []):
                tid = tk.get("id") or tk.get("task_id") if isinstance(tk, dict) else getattr(tk, "task_id", None)
                if tid == task_id:
                    task_type = (tk.get("type") or tk.get("task_type") if isinstance(tk, dict) else getattr(tk, "type", "")) or ""
                    break
        except Exception:
            pass
    # project_name: get_my_projects (sender 参加分)
    if pid is not None and not proj_name:
        try:
            for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                if isinstance(p, dict) and p.get("id") == pid:
                    proj_name = p.get("name") or ""
                    break
        except Exception:
            pass
    # 殿御命 2026-06-05: sender 未参加でも get_project (admin) fallback
    if pid is not None and not proj_name and hasattr(client, "get_project"):
        try:
            p = client.get_project(int(pid), actor_user_id=actor_id) or {}
            proj_name = p.get("name") or ""
        except Exception:
            pass

    # mention 解決
    mention_uids: set[int] = set()
    for token in (m.strip() for m in str(mentions).split(",") if m.strip()):
        if token.isdigit():
            mention_uids.add(int(token))
        elif "@" in token:
            try:
                for u in (client.get_users(actor_user_id=actor_id) or []):
                    if isinstance(u, dict) and (u.get("email") or "").lower() == token.lower():
                        uid = u.get("id") or u.get("user_id")
                        if uid is not None:
                            mention_uids.add(int(uid))
                        break
            except Exception:
                pass

    # project roles
    from app.adapters.calendar_client import _to_calendar_uid
    sender_cuid = _to_calendar_uid(actor_id)
    sender_cuid_int = int(sender_cuid) if sender_cuid is not None else None
    project_roles = {}
    if pid is not None and hasattr(client, "get_project_roles"):
        try:
            project_roles = client.get_project_roles(int(pid), actor_user_id=actor_id) or {}
        except Exception:
            pass
    director_uid = project_roles.get("director")
    if director_uid is None and not mention_uids:
        raise HTTPException(status_code=400, detail="Director 未設定 + mention 無 — 代替担当を選択してください")
    pm_uid = project_roles.get("pm")
    lead_uid = project_roles.get("lead") or project_roles.get("lighting_lead")

    shot_member_uids: set[int] = set()
    shot_member_uids.add(int(pm_uid) if pm_uid is not None else 52)
    if director_uid is not None:
        shot_member_uids.add(int(director_uid))
    if lead_uid is not None:
        shot_member_uids.add(int(lead_uid))
    shot_member_uids |= shot_assignee_uids
    shot_member_uids |= mention_uids
    if sender_cuid_int is not None:
        shot_member_uids.add(sender_cuid_int)
    participants = sorted(shot_member_uids)

    # sender name
    sender_name = actor_id
    try:
        me = client.get_me(actor_user_id=actor_id)
        nm = getattr(me, "name", "") or (getattr(me, "email", "") or "").split("@")[0]
        if nm: sender_name = nm
    except Exception:
        pass

    # QC ビューア URL
    import os as _os
    public_base = _os.environ.get("SCORE_PUBLIC_URL", "").rstrip("/")
    qc_path = f"/qc/{shot_id}"
    qp = []
    if task_id: qp.append(f"task_id={task_id}")
    qp.append(f"asset_id={asset_id}")
    qc_path += "?" + "&".join(qp)
    qc_link = (public_base + qc_path) if public_base else qc_path

    # thread + body
    thread_resp = client.post_dm_thread(participant_ids=participants, task_id=task_id, actor_user_id=actor_id)
    thread_id = thread_resp.get("thread_id") or thread_resp.get("id")
    head = "🔍 QC 依頼" if submission_type == "qc" else "📌 Review 依頼"
    hier = [p for p in (proj_name, seq_code, shot_code, task_type) if p]
    title_line = " / ".join(hier) if hier else "(対象未指定)"
    fname = (file_path or "").split("/")[-1] if file_path else f"asset_{asset_id}"
    ver = version_from_asset or "?"
    lines = [
        head, title_line, "",
        f"既存 {ver} ({fname}) の御確認を御願い致します。",
    ]
    if mention_uids:
        names = []
        try:
            users = client.get_users(actor_user_id=actor_id) or []
            uid_to_name = {int(u.get("id") or u.get("user_id") or 0): (u.get("name") or (u.get("email") or "").split("@")[0]) for u in users if isinstance(u, dict)}
            names = [uid_to_name.get(u, f"uid {u}") for u in sorted(mention_uids)]
        except Exception:
            names = [f"uid {u}" for u in sorted(mention_uids)]
        lines.append("宛先: " + ", ".join(names))
    if comment:
        lines.append("補足: " + comment[:200])
    if qc_link:
        lines.append(""); lines.append(qc_link)
    lines.append(""); lines.append(f"— {sender_name}")
    body_text = "\n".join(lines)
    client.post_dm(int(thread_id), body_text, actor_user_id=actor_id)

    # push + SSE 配信
    from app.routers.pages_notif_settings import get_user_prefs
    from app.routers.sse_notifications import push_sse_event
    payload = {
        "title": f"{head}: {title_line}",
        "body": f"既存 {ver} ({fname})",
        "url": qc_path,
        "tag": f"score-qc-{thread_id}",
    }
    push_targets = []; sse_targets = []
    cat_key = "qc_request" if submission_type == "qc" else "review_request"
    for cuid in participants:
        # 殿御命 2026-06-05: mention 明示指定者は sender でも配信
        if sender_cuid_int is not None and cuid == sender_cuid_int and cuid not in mention_uids:
            continue
        prefs = get_user_prefs(int(cuid))
        if not prefs.get("categories", {}).get(cat_key, True):
            continue
        if prefs.get("channels", {}).get("push", True):
            push_targets.append(cuid)
        if prefs.get("channels", {}).get("sse", True):
            sse_targets.append(cuid)
    push_result = _push_to_cuids(push_targets, payload) if push_targets else {"sent": 0, "failed": 0, "details": []}
    sse_result = push_sse_event(sse_targets, "notif", payload) if sse_targets else {"delivered": 0, "skipped_no_listener": 0}

    return JSONResponse(content={
        "ok": True, "thread_id": thread_id, "participants": participants,
        "qc_link": qc_link, "asset_id": asset_id, "task_id": task_id,
        "push_result": push_result, "sse_result": sse_result,
    })


@router.post("/api/bff/qc/approve")
async def post_qc_approve_bff(request: Request, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05 (C 案): Director Approve 実 API + SHOT thread に通知投稿"""
    body = await request.json()
    shot_id = body.get("shot_id")
    task_id = body.get("task_id")
    comment = (body.get("comment") or "").strip()
    if shot_id is None:
        raise HTTPException(status_code=400, detail="shot_id 必須")

    client = get_calendar_client()
    # cmd_106 (2026-07-16): task_id 不在時 shot 内 判定待ち (qc) な task を auto-resolve
    # (9値体系では v1qc/dir_wt は qc へ集約済み — task_status.py OLD_TO_NEW_STATUS 参照)
    # cmd_091c: shot_id=0 (SHOT_000 sentinel) は「値あり」— not shot_id だと falsy-zero で誤判定
    if not task_id and shot_id is not None:
        try:
            tasks = client.get_tasks(int(shot_id), actor_user_id=actor_id) or []
            for t in tasks:
                st = getattr(t, 'status', None) or (t.get('status') if isinstance(t, dict) else None)
                if st == 'qc':
                    task_id = getattr(t, 'task_id', None) or (t.get('id') if isinstance(t, dict) else None)
                    if task_id: break
        except Exception:
            pass

    # cmd_141: Approve(承認)実行は判定権限アクター限定 (既存 UI ゲートの server 側実装)
    _require_qc_judge_authority(actor_id, task_id=task_id, shot_id=shot_id)

    # cmd_089 (2026-07-10・殿御命): Ap(承認)と Deliver(納品)は別ステータス。
    # cmd_106 (2026-07-16): 9値体系では ap/client_ap/deliver の3値すべてが completed
    # (task_status.py STATUS_CATEGORY 参照)。本ハンドラは ap への遷移のみ担当し、
    # client_ap/deliver への遷移は本ハンドラの責務外 (別トリガーで行う設計)。
    if task_id and hasattr(client, "patch_task"):
        try:
            client.patch_task(int(task_id), {"status": "ap"}, actor_user_id=actor_id)
        except Exception:
            pass

    # SHOT thread (既存の review thread を探して 通知投稿)
    thread_notified = False
    # cmd_106 パートB (2026-07-16・殿御命): AP承認 Push/SSE 通知結果
    # (QC提出/QC_FB は三重通知済だが承認だけ thread post のみで手薄だった是正)
    approve_push_result = {"sent": 0, "failed": 0, "details": []}
    approve_sse_result = {"delivered": 0, "skipped_no_listener": 0}
    try:
        if hasattr(client, "get_my_dm_threads"):
            threads = client.get_my_dm_threads(actor_user_id=actor_id) or []
            # task_id 一致 thread を探す (last_message に task: {task_id} 含む thread)
            target = None
            for t in threads:
                last = (t.get("last_message") or "")
                if task_id and (f"task: {task_id}" in last or f"task_id={task_id}" in last):
                    target = t; break
            if not target and threads:
                # fallback: 最新 thread (sort by updated_at desc)
                target = sorted(threads, key=lambda x: x.get("updated_at",""), reverse=True)[0] if threads else None
            if target and hasattr(client, "post_dm"):
                tid = target.get("thread_id") or target.get("id")
                # sender name
                sender_name = actor_id
                try:
                    me = client.get_me(actor_user_id=actor_id)
                    sender_name = getattr(me, "name", "") or sender_name
                except Exception:
                    pass
                # cmd_095 (score Approve通知の可読化): 生 shot_id/task_id のみでは
                # 受け手が何の承認か判別不能。post_qc_notify_existing と同一パターンで
                # proj/seq/shot(cut)/task種別 + 対象 asset の version を解決し、
                # tutorial/qc_flow.html の表記 (例: sq01 c05 Compositing v04) に
                # 倣った可読形式に整形する。
                proj_name, seq_code, shot_code, task_type, version = "", "", "", "", ""
                pid = None
                asset_list = []
                try:
                    shot_info = client.get_shot_detail(int(shot_id), actor_user_id=actor_id) or {}
                    shot_code = shot_info.get("shotID") or shot_info.get("shot_code") or shot_info.get("name") or ""
                    seq_code = shot_info.get("seqID") or shot_info.get("seq_code") or ""
                    pid = shot_info.get("project_id")
                    for tk in (shot_info.get("task_list") or shot_info.get("tasks") or []):
                        if isinstance(tk, dict) and task_id and (tk.get("id") == task_id or tk.get("task_id") == task_id):
                            task_type = tk.get("type") or tk.get("task_type") or ""
                            break
                    asset_list = [a for a in (shot_info.get("asset_list") or []) if isinstance(a, dict) and (not task_id or a.get("task_id") == task_id)]
                except Exception:
                    pass
                if not shot_code or not seq_code or pid is None:
                    try:
                        s_dto = client.get_shot(int(shot_id), actor_user_id=actor_id)
                        if s_dto:
                            if not shot_code: shot_code = getattr(s_dto, "shot_code", "") or getattr(s_dto, "name", "")
                            if not seq_code: seq_code = getattr(s_dto, "seq_code", "") or ""
                            if pid is None: pid = getattr(s_dto, "project_id", None)
                    except Exception:
                        pass
                if not shot_code:
                    shot_code = f"SHOT_{shot_id:03d}"
                # cmd_094a (SHOT000-PROACTIVE-AUDIT) 準拠: shot_id=0 (SHOT_000・shot 紐付
                # なし task) は上記 shot 系 lookup が常に空。task_id から project_id を
                # 直接解決する fallback (post_qc_notify_existing と同一パターン)。
                if task_id and hasattr(client, "get_task"):
                    try:
                        task_info = client.get_task(int(task_id), actor_user_id=actor_id) or {}
                        if pid is None:
                            pid = task_info.get("project_id")
                        if not seq_code:
                            seq_code = task_info.get("seqID") or task_info.get("seq_code") or ""
                        if not task_type:
                            task_type = task_info.get("type") or ""
                    except Exception:
                        pass
                if not task_type and task_id:
                    try:
                        for tk in (client.get_tasks(int(shot_id), actor_user_id=actor_id) or []):
                            tid = tk.get("id") or tk.get("task_id") if isinstance(tk, dict) else getattr(tk, "task_id", None)
                            if tid == task_id:
                                task_type = (tk.get("type") or tk.get("task_type") if isinstance(tk, dict) else getattr(tk, "type", "")) or ""
                                break
                    except Exception:
                        pass
                if pid is not None:
                    try:
                        if hasattr(client, "get_project"):
                            p = client.get_project(int(pid), actor_user_id=actor_id) or {}
                            proj_name = p.get("name") or ""
                        if not proj_name:
                            for p in (client.get_my_projects(actor_user_id=actor_id) or []):
                                if isinstance(p, dict) and p.get("id") == pid:
                                    proj_name = p.get("name") or ""; break
                    except Exception:
                        pass
                # shotless (asset_list が shot 経由で取れない) 時は get_assets_by_task へ
                # fallback (get_retake_view/cmd_094a と同一パターン)
                if not asset_list and task_id and hasattr(client, "get_assets_by_task"):
                    try:
                        asset_list = [a for a in (client.get_assets_by_task(int(task_id), actor_user_id=actor_id) or []) if isinstance(a, dict)]
                    except Exception:
                        pass
                if asset_list:
                    asset_list.sort(key=lambda a: (a.get("created_at") or ""), reverse=True)
                    version = asset_list[0].get("version") or ""

                hier = [p for p in (proj_name, seq_code, shot_code, task_type) if p]
                title_line = " ".join(hier) if hier else f"shot:{shot_id} task:{task_id or '-'}"
                if version:
                    title_line += f" {version}"
                # task_type 未解決時は hier だけでは task を一意特定できない (例:
                # "SHOT_055" のみ) — 生 task_id を括弧で補い追跡可能性を維持する。
                if task_id and not task_type:
                    title_line += f" (task:{task_id})"

                body_lines = [
                    "✅ Approved",
                    title_line,
                ]
                if comment:
                    body_lines.append(f"comment: {comment[:200]}")
                body_lines.append(f"— {sender_name} (Director)")
                client.post_dm(int(tid), "\n".join(body_lines), actor_user_id=actor_id)
                thread_notified = True

                # cmd_106 パートB (2026-07-16・殿御命): AP(承認)通知に Push/SSE を追加。
                # QC提出/QC_FB通知(_push_to_cuids・push_sse_event)と同じパターン。
                # 宛先はQC_FB同様、アーティスト(assignee)を必ず含める(殿仕様)。
                from app.routers.pages_notif_settings import get_user_prefs
                from app.routers.sse_notifications import push_sse_event
                from app.adapters.calendar_client import _to_calendar_uid
                notify_targets = set()
                for _p in (target.get("participants") or []):
                    try:
                        notify_targets.add(int(_p))
                    except (ValueError, TypeError):
                        pass
                if task_id and hasattr(client, "get_task"):
                    try:
                        _ti = client.get_task(int(task_id), actor_user_id=actor_id) or {}
                        _assignee = _ti.get("assigned_to") or _ti.get("assignee_id")
                        if _assignee is not None:
                            notify_targets.add(int(_assignee))
                    except Exception:
                        pass
                _sender_cuid = _to_calendar_uid(actor_id)
                if _sender_cuid is not None:
                    notify_targets.discard(int(_sender_cuid))
                if notify_targets:
                    approve_push_payload = {
                        "title": f"✅ Approved: {title_line}",
                        "body": (comment[:200] if comment else f"{sender_name} が承認しました"),
                        "url": f"/qc/{shot_id}" + (f"?task_id={task_id}" if task_id else ""),
                        "tag": f"score-approve-{tid}",
                    }
                    approve_push_targets, approve_sse_targets = [], []
                    for _cuid in notify_targets:
                        _prefs = get_user_prefs(int(_cuid))
                        if _prefs.get("channels", {}).get("push", True):
                            approve_push_targets.append(_cuid)
                        if _prefs.get("channels", {}).get("sse", True):
                            approve_sse_targets.append(_cuid)
                    if approve_push_targets:
                        approve_push_result = _push_to_cuids(approve_push_targets, approve_push_payload)
                    if approve_sse_targets:
                        approve_sse_result = push_sse_event(approve_sse_targets, "notif", approve_push_payload)
    except Exception:
        pass

    return JSONResponse(content={"ok": True, "shot_id": shot_id, "task_id": task_id, "thread_notified": thread_notified, "push_result": approve_push_result, "sse_result": approve_sse_result})


@router.get("/api/bff/projects/{project_id}/roles")
def get_project_roles_bff(project_id: int, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05 (C 案): UI 側 Director 不在 事前検査用 — pass-through"""
    client = get_calendar_client()
    if not hasattr(client, "get_project_roles"):
        return JSONResponse(content={})
    try:
        roles = client.get_project_roles(project_id, actor_user_id=actor_id) or {}
    except Exception as e:
        return JSONResponse(content={"_error": str(e)[:120]}, status_code=200)
    return JSONResponse(content=roles, headers={"X-Actor-User-Id": actor_id})


@router.get("/api/bff/dm/threads/{thread_id}/messages")
def get_dm_thread_messages_bff(thread_id: int, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05 (nibu Phase 2 EP): DM thread 全件 messages 取得 pass-through"""
    client = get_calendar_client()
    if not hasattr(client, "get_dm_thread_messages"):
        return JSONResponse(content=[])
    try:
        messages = client.get_dm_thread_messages(thread_id, actor_user_id=actor_id) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"messages 取得失敗: {str(e)[:150]}")
    return JSONResponse(content=messages, headers={"X-Actor-User-Id": actor_id})


# 殿御命 2026-06-08 (nibu cmd_471 ②): Calendar BE → Score webhook 受信 EP
# HMAC-SHA256 (X-Calendar-Signature) 検証 + event 別 SSE dispatch
@router.post("/api/bff/webhook/calendar")
async def calendar_webhook(request: Request):
    """Calendar BE → Score outbound webhook 受信。
    署名: X-Calendar-Signature ヘッダ (HMAC-SHA256, hex)
    secret: 環境変数 CALENDAR_WEBHOOK_SECRET
    対応 event: event.created / event.updated / dm_thread.new_message
    各 event は payload 内 対象 user_id 群に対して SSE 配信 (notif category 別)。
    """
    import hmac as _hmac
    import hashlib as _hashlib
    raw_body = await request.body()
    signature = request.headers.get("X-Calendar-Signature", "")
    secret = _os.environ.get("CALENDAR_WEBHOOK_SECRET", "")
    if not secret:
        # 設定不在は 503 (障害扱い)
        raise HTTPException(status_code=503, detail="CALENDAR_WEBHOOK_SECRET not configured")
    expected = _hmac.new(secret.encode("utf-8"), raw_body, _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        body = _json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON")
    event_type = (body.get("event") or body.get("type") or "").strip()
    payload = body.get("payload") or body.get("data") or {}
    if not isinstance(payload, dict):
        payload = {}

    # SSE helper
    from app.routers.sse_notifications import push_sse_event
    delivered = {"event": event_type, "sse_targets": [], "errors": []}

    if event_type in ("event.created", "event.updated"):
        # Calendar event: attendees に SSE 配信
        attendees = payload.get("attendees") or payload.get("user_ids") or []
        title = payload.get("title") or "予定変更"
        body_text = (payload.get("description") or payload.get("body") or "")[:200]
        for uid in attendees:
            try:
                cuid = int(uid)
            except (ValueError, TypeError):
                continue
            push_sse_event([cuid], "calendar", {
                "category": event_type,
                "title": title,
                "body": body_text,
                "event_id": payload.get("event_id") or payload.get("id"),
                "start_at": payload.get("start_at"),
                "end_at": payload.get("end_at"),
            })
            delivered["sse_targets"].append(cuid)

    elif event_type == "dm_thread.new_message":
        # DM thread 新着: participants (sender 除外) に SSE 配信
        thread_id = payload.get("thread_id")
        sender_id = payload.get("sender_id") or payload.get("sender_uid")
        participants = payload.get("participants") or payload.get("recipient_ids") or []
        message_body = (payload.get("body") or payload.get("content") or "")[:200]
        try:
            sender_int = int(sender_id) if sender_id is not None else None
        except (ValueError, TypeError):
            sender_int = None
        for uid in participants:
            try:
                cuid = int(uid)
            except (ValueError, TypeError):
                continue
            if cuid == sender_int:
                continue
            push_sse_event([cuid], "dm", {
                "category": "dm_message",
                "thread_id": thread_id,
                "sender_id": sender_int,
                "body": message_body,
                "message_id": payload.get("message_id") or payload.get("id"),
            })
            delivered["sse_targets"].append(cuid)
    else:
        delivered["errors"].append(f"unknown event_type: {event_type}")

    return JSONResponse(content={"ok": True, **delivered})


@router.post("/api/bff/dm/threads/{thread_id}/read")
def post_dm_thread_read_bff(thread_id: int, actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-05 (nibu Phase 2 EP): DM thread 既読 mark pass-through (冪等)"""
    client = get_calendar_client()
    if not hasattr(client, "post_dm_thread_read"):
        return JSONResponse(content={"ok": False, "reason": "未実装"})
    try:
        result = client.post_dm_thread_read(thread_id, actor_user_id=actor_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"既読 mark 失敗: {str(e)[:150]}")
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.get("/api/bff/dm/threads_meta")
def get_dm_threads_meta(actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-04: sidemenu 未読 badge 用 軽量 endpoint
    全 page で 1 fetch で済むよう thread_id + updated_at のみ返却
    (リアルタイム性重視・cache 無し pass-through)"""
    client = get_calendar_client()
    threads = []
    if hasattr(client, "get_my_dm_threads"):
        try:
            raw = client.get_my_dm_threads(actor_user_id=actor_id) or []
            for t in raw:
                if isinstance(t, dict):
                    threads.append({
                        "thread_id": t.get("thread_id") or t.get("id"),
                        "updated_at": t.get("updated_at") or "",
                    })
        except Exception:
            pass
    return JSONResponse(content={"threads": threads}, headers={"X-Actor-User-Id": actor_id})


@router.delete("/api/bff/assets/{asset_id}")
def delete_asset_endpoint(
    asset_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-06-03: asset 削除 (nibu 殿 DELETE /api/assets/{id} pass-through)
    本人 or admin のみ可 (Calendar 側で 403 enforce)"""
    client = get_calendar_client()
    result = client.delete_asset(asset_id, actor_user_id=actor_id) if hasattr(client, "delete_asset") else {"ok": False, "reason": "client method not implemented"}
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})


@router.get("/api/bff/assets/{asset_id}/original")
def get_asset_original(
    asset_id: int = Path(...),
    actor_id: str = Depends(get_actor_id),
):
    """殿御命 2026-07-06 (cmd_068②③): .exr/.mov 等サーバ側変換済 asset の原本ファイルを返す。
    変換で原本を保存していない asset (通常の画像等) は 404 — 呼び出し側は Calendar 直配信 URL を使うこと。"""
    from fastapi.responses import FileResponse
    from app.helpers.asset_originals import find_original
    path = find_original(asset_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="original file not found")
    # 保存名は "{asset_id}_{original_filename}" — 先頭の "{asset_id}_" を除いた名で DL させる
    display_name = path.name.split("_", 1)[1] if "_" in path.name else path.name
    return FileResponse(str(path), filename=display_name)


@router.post("/api/bff/me/avatar")
async def upload_my_avatar(
    file: UploadFile = File(...),
    actor_id: str = Depends(get_actor_id),
):
    """Avatar image upload → Calendar POST /api/me/avatar pass-through.

    殿御命 2026-06-09: Calendar は upload を受け avatar_url(/uploads/...) を返すが、
    その画像を自身では配信しておらず (全パス 404)、相対 URL ゆえブラウザは Score origin に
    取りに行き 404 → アバター真っ白。対策として Calendar が返した URL と同一パスで
    Score 側にローカル複製を保存し、Score の /uploads mount で配信して表示を担保する。"""
    client = get_calendar_client()
    content = await file.read()
    result = client.post_my_avatar(
        file_data=content,
        filename=file.filename or "avatar",
        content_type=file.content_type or "application/octet-stream",
        actor_user_id=actor_id,
    )
    # avatar_url が /uploads/ 配下なら Score にも同一パスで複製保存 (real mode で Calendar 未配信を補う)
    try:
        avatar_url = result.get("avatar_url", "") if isinstance(result, dict) else ""
        if isinstance(avatar_url, str) and avatar_url.startswith("/uploads/"):
            rel = avatar_url[len("/uploads/"):].split("?")[0].lstrip("/")
            uploads_root = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                "uploads",
            )
            dest = _os.path.normpath(_os.path.join(uploads_root, rel))
            # path traversal 防止: uploads_root 配下に限定
            if dest.startswith(_os.path.abspath(uploads_root) + _os.sep):
                _os.makedirs(_os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(content)
    except Exception:
        pass  # 複製失敗は致命でない (Calendar 側保存は成功・表示のみ best-effort)
    return JSONResponse(content=result, headers={"X-Actor-User-Id": actor_id})
# touch 1780901283
