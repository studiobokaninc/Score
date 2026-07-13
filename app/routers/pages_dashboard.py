"""統合 Dashboard (殿御命 2026-06-01)
- role 別振分廃止・全 user 単一 dashboard
- 全 project 横断 + action 中心 (やる事があれば section 表示・空なら非表示)
- section: 本日の予定 / 本日のタスク / QC 依頼 / トラブル対応 / マイルストーン
"""
import os
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.adapters.dto import CalendarUser
from app.deps import get_actor_id, get_actor_role
from app.i18n import get_translator, get_time_greeting_key, t
from app.helpers.task_status import COMPLETED_STATUSES, JUDGE_TARGET_STATUSES, STATUS_PRIORITY, attach_status_meta

router = APIRouter()
_templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader("app/templates"), cache_size=0)
)


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


@router.get("/dashboard")
def read_dashboard(
    request: Request,
    lang: str = "ja",
    actor_id: str = Depends(get_actor_id),
):
    """統合 Dashboard — 全 user 共通 layout・action 中心 (count > 0 で section 表示)"""
    client = get_calendar_client()
    actor_uid = int(actor_id) if actor_id and actor_id.isdigit() else 0
    today_jst = datetime.now(timezone(timedelta(hours=9))).date()
    today_str = today_jst.strftime("%Y-%m-%d")
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"][today_jst.weekday()]

    user = _safe(lambda: client.get_me(actor_user_id=actor_id), None)
    if user is None:
        user = CalendarUser(user_id=0, email="", role="", name="")
    # 殿御命 2026-06-08: user.name が空/「ユーザ」 fallback の場合 get_users から actor_id 経由で再解決
    if not user.name or user.name == "ユーザ":
        try:
            _aid_int = int(actor_id) if str(actor_id).isdigit() else None
            if _aid_int is not None and hasattr(client, "get_users"):
                for _u in (client.get_users(actor_user_id=actor_id) or []):
                    if isinstance(_u, dict):
                        _uid = _u.get("id") or _u.get("user_id")
                        if _uid is not None and int(_uid) == _aid_int:
                            _name = _u.get("name") or _u.get("full_name") or (_u.get("email") or "").split("@")[0]
                            if _name:
                                user = CalendarUser(user_id=_aid_int, email=_u.get("email", ""), role=_u.get("role", user.role or ""), name=_name)
                            break
        except Exception:
            pass

    user_projects = _safe(lambda: client.get_my_projects(actor_user_id=actor_id), []) or []
    project_name_map = {p.get("id"): p.get("name", "") for p in user_projects if isinstance(p, dict) and p.get("id") is not None}
    # 殿御命 2026-06-08: 「action item ございませぬ」時 PM 確認 DM 用に PM uid + name を解決
    pm_contact = None
    try:
        for _p in user_projects:
            if not isinstance(_p, dict): continue
            _pid = _p.get("id")
            if _pid is None: continue
            _roles = _safe(lambda: client.get_project_roles(int(_pid), actor_user_id=actor_id), {}) or {}
            _pm_uid = _roles.get("pm")
            if _pm_uid:
                _pm_name = f"uid {_pm_uid}"
                if hasattr(client, "get_users"):
                    for _u in (_safe(lambda: client.get_users(actor_user_id=actor_id), []) or []):
                        if isinstance(_u, dict) and (_u.get("id") == _pm_uid or _u.get("user_id") == _pm_uid):
                            _pm_name = _u.get("name") or _u.get("full_name") or (_u.get("email") or "").split("@")[0] or _pm_name
                            break
                pm_contact = {"uid": int(_pm_uid), "name": _pm_name}
                break
    except Exception:
        pm_contact = None
    # 殿御命 2026-06-04: ryoji の member 外 project も name 解決可にする (全 project map fallback)
    try:
        if hasattr(client, "get_projects"):
            for _p in (client.get_projects(actor_user_id=actor_id) or []):
                if isinstance(_p, dict) and _p.get("id") is not None and _p.get("id") not in project_name_map:
                    project_name_map[_p.get("id")] = _p.get("name", "")
    except Exception:
        pass
    # 殿御命 2026-06-04: users name map 事前 cache (attendees name 解決用・iterate ごと呼出回避)
    _user_name_cache = {}
    try:
        if hasattr(client, "get_users"):
            for _u in (client.get_users(actor_user_id=actor_id) or []):
                _uid = _u.get("id") or _u.get("user_id")
                if _uid is not None:
                    _user_name_cache[int(_uid)] = _u.get("name") or _u.get("full_name") or _u.get("username") or _u.get("email") or f"uid {_uid}"
    except Exception:
        pass

    # ===== my_tasks: 全 project 横断 (get_my_tasks 1 call で完結) =====
    raw_tasks = []
    if hasattr(client, "get_my_tasks"):
        raw_tasks = _safe(lambda: client.get_my_tasks(actor_user_id=actor_id), []) or []
    my_tasks = []
    for tk in raw_tasks:
        if not isinstance(tk, dict):
            continue
        status = (tk.get("status") or "").lower()
        if status in ("done", "approved", "完了", "completed", "complete") or status in COMPLETED_STATUSES:
            continue  # 完了済は除外 (新体系: deliver のみが完了)
        pid = tk.get("project_id")
        my_tasks.append({
            "task_id": tk.get("id") or tk.get("task_id"),
            "shot_id": tk.get("shot_id"),
            "shot_code": tk.get("shotID") or tk.get("shot_code") or tk.get("name", ""),
            "seq_code": tk.get("seqID") or tk.get("seq_code", ""),
            "task_type": tk.get("type") or tk.get("task_type", ""),
            "status": tk.get("status", ""),
            "priority": (tk.get("priority") or "").upper() if tk.get("priority") else "",
            "project_id": pid,
            "project_name": project_name_map.get(pid, ""),
            "due_date": (tk.get("due_date") or "")[:10],
            # cmd_075: Calendar が inline 同梱する動的色/ラベル
            "status_color": tk.get("status_color"),
            "status_label": tk.get("status_label"),
            "status_category": tk.get("status_category"),
        })
    my_tasks.sort(key=lambda x: STATUS_PRIORITY.get((x.get("status") or "").lower(), 5))

    # ===== QC 依頼: 自分担当 task で判定待ち (社内 qc/v1qc・社外 dir_wt 等) =====
    qc_requests = [t for t in my_tasks if (t.get("status") or "").lower() in JUDGE_TARGET_STATUSES]
    # 殿御命 2026-06-05: 各 QC 依頼 task に対応する 最新 asset_id を解決 (qc_viewer 遷移用)
    # cmd_094a (SHOT000-PROACTIVE-AUDIT): shot_id=0 (SHOT_000・shot 紐付なし task) は
    # 旧 `if _q.get("shot_id")` が 0 を falsy 誤判定し本ブロック自体を丸ごとスキップして
    # いた。get_shot_detail(0) は実在しない shot のため呼んでも無駄なので is not None 化
    # した上で、get_director_retake_input (pages_director.py) と同一パターンの
    # get_assets_by_task フォールバックを追加する。
    for _q in qc_requests:
        if _q.get("shot_id") is not None and _q.get("task_id") and hasattr(client, "get_shot_detail"):
            try:
                shot_dict = client.get_shot_detail(int(_q["shot_id"]), actor_user_id=actor_id) or {}
                assets_for_task = [a for a in (shot_dict.get("asset_list") or []) if isinstance(a, dict) and a.get("task_id") == _q["task_id"]]
                assets_for_task.sort(key=lambda a: a.get("created_at", "") or "", reverse=True)
                if assets_for_task:
                    _q["latest_asset_id"] = assets_for_task[0].get("id")
            except Exception:
                pass
        if _q.get("latest_asset_id") is None and _q.get("shot_id") == 0 and _q.get("task_id") and hasattr(client, "get_assets_by_task"):
            try:
                assets_for_task = list(client.get_assets_by_task(int(_q["task_id"]), actor_user_id=actor_id) or [])
                assets_for_task.sort(key=lambda a: (a.get("created_at") if isinstance(a, dict) else "") or "", reverse=True)
                if assets_for_task:
                    _q["latest_asset_id"] = assets_for_task[0].get("id") if isinstance(assets_for_task[0], dict) else getattr(assets_for_task[0], "id", None)
            except Exception:
                pass

    # 殿御命 2026-06-05: SHOT thread で受信した「🔍 QC 依頼」「📌 Review 依頼」も dashboard に表示
    thread_qc_requests = []
    try:
        if hasattr(client, "get_my_dm_threads"):
            for _thr in (client.get_my_dm_threads(actor_user_id=actor_id) or []):
                if not isinstance(_thr, dict):
                    continue
                lm = (_thr.get("last_message") or "").strip()
                if not lm:
                    continue
                # 🔍 QC 依頼 / 📌 Review 依頼 を抽出 (本文先頭行)
                first_line = lm.split("\n")[0]
                if not (first_line.startswith("🔍 QC 依頼") or first_line.startswith("📌 Review 依頼")):
                    continue
                lines = lm.split("\n")
                title = lines[1] if len(lines) > 1 else ""
                # cmd_090: QC/Review 依頼投稿時、本文に qc_viewer リンク(/qc/{shot_id}?task_id=..&asset_id=..)
                # が埋め込み済 (bff_write.py post_qc_notify_existing 等)。そこから直接抽出し、
                # QC ビューアへ遷移させる (従来は抽出せず SHOT thread へ誤遷移していた)。
                import re as _re_qc
                _qc_url_match = _re_qc.search(r'/qc/\d+\S*', lm)
                thread_qc_requests.append({
                    "thread_id": _thr.get("thread_id"),
                    "kind": "qc" if first_line.startswith("🔍") else "review",
                    "title": title,
                    "snippet": (lines[3] if len(lines) > 3 else "")[:80],
                    "updated_at": _thr.get("updated_at"),
                    "participants_count": len(_thr.get("participants") or []),
                    "qc_url": _qc_url_match.group(0) if _qc_url_match else None,
                })
        thread_qc_requests.sort(key=lambda x: x.get("updated_at",""), reverse=True)
    except Exception:
        thread_qc_requests = []

    # 殿御命 2026-06-05: 受信 QC/Review 依頼を「本日やるべきタスク」に統合 (task 風 entry)
    for _r in thread_qc_requests:
        title = _r.get("title") or "QC 依頼"
        # 階層 title parse: "marukome / SEQ001 / SHOT_153 / comp" → shot_code = SHOT_153, task_type = comp
        parts = [p.strip() for p in title.split("/")]
        _shot = parts[2] if len(parts) >= 3 else title
        _task = parts[3] if len(parts) >= 4 else ""
        _proj = parts[0] if len(parts) >= 1 else ""
        my_tasks.insert(0, {  # 先頭挿入 (QC 依頼を優先表示)
            "task_id": None,
            "thread_id": _r.get("thread_id"),
            "shot_id": None,
            "shot_code": _shot,
            "seq_code": parts[1] if len(parts) >= 2 else "",
            "task_type": _task or ("Review" if _r.get("kind") == "review" else "QC"),
            "status": "qc_inbox" if _r.get("kind") == "qc" else "review_inbox",
            "priority": "",
            "project_id": None,
            "project_name": _proj,
            "due_date": "",
            "is_qc_inbox": True,
            "kind": _r.get("kind"),
            "qc_url": _r.get("qc_url"),
        })

    my_tasks = attach_status_meta(my_tasks, client)  # cmd_075: status_color/status_label 動的付与

    # ===== troubles: 自分関連 =====
    troubles_raw = []
    if hasattr(client, "get_my_troubles"):
        troubles_raw = _safe(lambda: client.get_my_troubles(actor_user_id=actor_id), []) or []
    elif hasattr(client, "get_troubles"):
        troubles_raw = _safe(lambda: client.get_troubles(actor_user_id=actor_id), []) or []
    troubles = []
    for tr in troubles_raw:
        if not isinstance(tr, dict):
            continue
        if (tr.get("status") or "").lower() in ("resolved", "closed", "完了"):
            continue
        troubles.append({
            "id": tr.get("id"),
            "title": tr.get("title", ""),
            "status": tr.get("status", ""),
            "project_id": tr.get("project_id"),
            "project_name": project_name_map.get(tr.get("project_id"), ""),
        })

    # ===== my_retakes: Calendar 側 retake 一覧 =====
    my_retakes_raw = []
    if hasattr(client, "get_my_retakes"):
        my_retakes_raw = _safe(lambda: client.get_my_retakes(actor_user_id=actor_id), []) or []
    my_retakes = []
    for r in my_retakes_raw:
        if not isinstance(r, dict):
            continue
        my_retakes.append({
            "id": r.get("id"),
            "shot_id": r.get("shot_id"),
            "shot_code": r.get("shot_code") or r.get("shotID", ""),
            "task_id": r.get("task_id"),
            "task_type": r.get("task_type") or r.get("type", ""),
            "assignee_id": r.get("assignee_id") or r.get("assigned_to"),
            "project_id": r.get("project_id"),
            "project_name": project_name_map.get(r.get("project_id"), ""),
        })

    # ===== events: 本日予定 + 直近 30 日マイルストーン (全 project 横断) =====
    all_events = _safe(lambda: client.get_events(actor_user_id=actor_id), []) or []
    today_events = []
    upcoming_milestones = []
    next_30d = today_jst + timedelta(days=30)
    for ev in all_events:
        ev_date_str = (ev.get("date") or (ev.get("start_time") or "")[:10] or "")[:10]
        if not ev_date_str:
            continue
        # display_time inject (allDay + start_time)
        ev["display_time"] = None
        if not ev.get("allDay"):
            start = ev.get("start_time")
            if start:
                try:
                    ev["display_time"] = datetime.fromisoformat(start).strftime("%H:%M")
                except ValueError:
                    pass
        # project_name inject
        ev["project_name"] = project_name_map.get(ev.get("project_id"), "")
        # 殿御命 2026-06-04: attendees の {type:'user', id:N or 'user-N'} → name 解決 (事前 cache 活用)
        _atts = ev.get("attendees") or ev.get("participants") or []
        _resolved = []
        for a in (_atts if isinstance(_atts, list) else []):
            if isinstance(a, dict):
                _raw_id = a.get("id") or a.get("user_id")
                # 'user-31' 形式の id を数値化
                _num_id = None
                if isinstance(_raw_id, int):
                    _num_id = _raw_id
                elif isinstance(_raw_id, str):
                    import re as _re
                    _m = _re.search(r'(\d+)', _raw_id)
                    if _m:
                        _num_id = int(_m.group(1))
                nm = a.get("name")
                if not nm and _num_id is not None and _num_id in _user_name_cache:
                    nm = _user_name_cache[_num_id]
                if not nm:
                    nm = a.get("email") or (f"uid {_num_id}" if _num_id else "?")
                _resolved.append({"id": _num_id, "name": nm})
            else:
                _resolved.append({"id": None, "name": str(a)})
        ev["attendees_resolved"] = _resolved
        # 殿御命 2026-06-04: location が URL なら mtg リンクへ昇格 (実機 event 113 確認: location に zoom URL 格納)
        _loc_raw = (ev.get("location") or "").strip()
        if _loc_raw.startswith(("http://", "https://")):
            ev["meeting_url_extracted"] = _loc_raw
            ev["location"] = ""  # 📍 場所欄で URL 2 重表示を防止
        # description / notes / location 残文字列 内 URL を抽出 fallback (補助)
        if not ev.get("meeting_url") and not ev.get("zoom_url") and not ev.get("meeting_url_extracted"):
            _d = (ev.get("description") or "") + " " + (ev.get("notes") or "") + " " + (ev.get("location") or "")
            import re as _re2
            _u = _re2.search(r'https?://[^\s<>"]+', _d)
            if _u:
                ev["meeting_url_extracted"] = _u.group(0)
        try:
            ev_date = datetime.fromisoformat(ev_date_str).date()
        except ValueError:
            continue
        if ev_date == today_jst:
            # 殿御命 2026-06-04: actor が participants に含まれない event は「本日の予定」から除外
            # (participants 空 event は全社向け可能性につき残す)
            _p_uids = set()
            for _a in (ev.get("attendees") or ev.get("participants") or []):
                if isinstance(_a, dict):
                    _rid = _a.get("id") or _a.get("user_id")
                    if isinstance(_rid, int):
                        _p_uids.add(_rid)
                    elif isinstance(_rid, str):
                        import re as _re3
                        _m3 = _re3.search(r'(\d+)', _rid)
                        if _m3:
                            _p_uids.add(int(_m3.group(1)))
            for _u in (ev.get("user_ids") or []):
                try:
                    _p_uids.add(int(_u))
                except (ValueError, TypeError):
                    pass
            if _p_uids and actor_uid and actor_uid not in _p_uids:
                continue  # actor 不参加 → skip
            today_events.append(ev)
        elif (ev.get("type") or "").lower() == "milestone" and today_jst < ev_date <= next_30d:
            ev["date_short"] = ev_date.strftime("%m/%d")
            upcoming_milestones.append(ev)
    upcoming_milestones.sort(key=lambda e: e.get("date") or "")

    # next_event: today_events 空時の直近未来 event 1 件 (殿御命 2026-06-04: actor 不参加 event 除外)
    def _ev_participant_uids(ev):
        s = set()
        for a in (ev.get("attendees") or ev.get("participants") or []):
            if isinstance(a, dict):
                r = a.get("id") or a.get("user_id")
                if isinstance(r, int):
                    s.add(r)
                elif isinstance(r, str):
                    import re as _re4
                    m = _re4.search(r'(\d+)', r)
                    if m:
                        s.add(int(m.group(1)))
        for u in (ev.get("user_ids") or []):
            try:
                s.add(int(u))
            except (ValueError, TypeError):
                pass
        return s

    next_event = None
    if not today_events:
        def _is_actor_in(ev):
            uids = _ev_participant_uids(ev)
            # participants 空 event は全社向け可能性につき残す
            return (not uids) or (actor_uid in uids)
        future = sorted(
            [ev for ev in all_events
             if (ev.get("date") or (ev.get("start_time") or "")[:10] or "")[:10] > today_str
             and _is_actor_in(ev)],
            key=lambda e: (e.get("date") or (e.get("start_time") or "")[:10] or "")[:10]
        )
        next_event = future[0] if future else None

    trans = get_translator(lang)
    greeting_suffix, greeting_emoji = get_time_greeting_key()
    role = get_actor_role(actor_id)
    # 殿御命 2026-06-05: 出勤時刻 (cookie score_routine_done = ISO+TZ) を HH:MM 形式で抽出
    clock_in_time = ""
    _routine_done = request.cookies.get("score_routine_done", "")
    if _routine_done and len(_routine_done) >= 16 and _routine_done[10] == "T":
        clock_in_time = _routine_done[11:16]  # "HH:MM"
    return _templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": user,
            "clock_in_time": clock_in_time,
            "trans": trans,
            "t": t,
            "user_projects": user_projects,
            "pm_contact": pm_contact,
            "role": role,
            "active": "dashboard",
            "greeting_key": f"dashboard.greeting.{greeting_suffix}",
            "greeting_emoji": greeting_emoji,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "today_str": today_str,
            "today_weekday_jp": weekday_jp,
            # 統合 dashboard 用 集約 data
            "my_tasks": my_tasks[:10],
            "my_tasks_total": len(my_tasks),
            "qc_requests": qc_requests[:5],
            "qc_requests_total": len(qc_requests),
            "thread_qc_requests": thread_qc_requests[:5],  # 殿御命 2026-06-05: SHOT thread 経由 QC/Review 依頼
            "thread_qc_requests_total": len(thread_qc_requests),
            "troubles": troubles[:5],
            "troubles_total": len(troubles),
            "my_retakes": my_retakes[:5],
            "my_retakes_total": len(my_retakes),
            "today_events": today_events,
            "next_event": next_event,
            "upcoming_milestones": upcoming_milestones[:5],
            "project_name_map": project_name_map,
        },
    )
