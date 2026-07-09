import os

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from sqlalchemy.orm import Session
from app.adapters.calendar_factory import get_calendar_client
from app.adapters.dto import CalendarUser
from app.deps import get_actor_id, get_db
from app.models import BugReport

router = APIRouter()

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)


def _safe_user(actor_id: str) -> CalendarUser:
    client = get_calendar_client()
    try:
        return client.get_me(actor_user_id=actor_id)
    except Exception:
        return CalendarUser(user_id=0, email="", role="", name="ユーザ")


@router.get("/messages")
def get_messages(request: Request, thread: str | None = None, actor_id: str = Depends(get_actor_id)):
    user = _safe_user(actor_id)
    client = get_calendar_client()
    # mock の messages を取得 (state.messages list)
    try:
        all_messages = client.get_messages(actor_user_id=actor_id) or []
    except Exception:
        all_messages = []
    # sender 別に group 化 (DM thread として表示) — 自分以外の sender を thread 化
    threads_by_sender = {}
    my_uid = None
    try:
        my_uid = user.user_id
    except Exception:
        pass
    for m in all_messages:
        sid = m.get("sender_id")
        if sid is None or sid == my_uid:
            continue
        key = str(sid)
        if key not in threads_by_sender:
            threads_by_sender[key] = {
                "sender_id": sid,
                "sender": m.get("sender") or f"user_{sid}",
                "messages": [],
            }
        threads_by_sender[key]["messages"].append(m)
    dm_threads = list(threads_by_sender.values())

    # SHOT thread participants — SHOT 関係者 全員 (タスクとは独立な SHOT 単位 discussion space)
    # 構成: PM (Tanaka uid=1) + Director (Yamada uid=10) + Lighting Lead (Kato uid=20)
    #       + そのSHOTでタスクを持つ user (タスク assignee) + 関係 Lead
    # → 「SHOT 関係者全員が話せる場所」(殿御指示 2026-05-22)
    user_name_map = {1: 'Tanaka', 10: 'Yamada', 20: 'Kato', 30: 'Sato', 40: 'Suzuki', 99: 'Ryoji'}
    shot_threads_participants = {}
    try:
        for sid in (1, 2, 3):
            tasks = client.get_tasks(sid, actor_user_id=actor_id) or []
            # SHOT 関係者の base 構成 (タスクと独立)
            uids = {
                1,   # PM Tanaka (project 33 担当)
                10,  # Director Yamada
                20,  # Lighting Lead Kato (Lighting/Look の発生時に関与)
            }
            # 加えて当該 SHOT のタスク assignee を participants に
            for t in tasks:
                if t.assignee_id:
                    uids.add(t.assignee_id)
            names = sorted({user_name_map.get(u, f'user_{u}') for u in uids})
            shot_threads_participants[f'shot{sid:03d}'] = {
                'shot_id': sid,
                'participants_ids': sorted(uids),
                'participants_names': names,
            }
    except Exception:
        shot_threads_participants = {}

    # 殿御命 2026-06-04: nibu 6/3 実装 /api/me/dm/threads + task 自動 thread を反映
    calendar_dm_threads = []
    try:
        if hasattr(client, "get_my_dm_threads"):
            calendar_dm_threads = client.get_my_dm_threads(actor_user_id=actor_id) or []
        elif hasattr(client, "get_dm_threads"):  # 後方互換
            calendar_dm_threads = client.get_dm_threads(actor_user_id=actor_id) or []
    except Exception:
        calendar_dm_threads = []

    # 殿御命 2026-06-04: 旧 SSR active_thread 廃止 — click 時のみ JS で右ペイン描画 (URL 不変)
    # JS 用 real threads metadata を JSON 化して template に渡す
    # 殿御命 2026-06-04/05: SHOT 関係者 thread 分類:
    #   1) last_message が 🔍 QC 依頼 / 📌 Review 依頼 / ✅ Approved で始まる → SHOT (content 優先・participants 2 名でも)
    #   2) participants 3 名以上 → SHOT
    #   3) participants 2 名 + content QC 系でない → DM
    shot_group_threads = []
    dm_oneonone_threads = []
    for t in calendar_dm_threads:
        last_msg = (t.get("last_message") or "").lstrip()
        is_qc_content = last_msg.startswith("🔍 QC 依頼") or last_msg.startswith("📌 Review 依頼") or last_msg.startswith("✅ Approved") or last_msg.startswith("🔁 Retake")
        if is_qc_content or len(t.get("participants") or []) > 2:
            shot_group_threads.append(t)
        else:
            dm_oneonone_threads.append(t)

    import json as _json_mod
    real_threads_json = _json_mod.dumps({
        "threads": [
            {
                "thread_id": t.get("thread_id"),
                "participants": t.get("participants") or [],
                "last_message": t.get("last_message") or "",
                "updated_at": t.get("updated_at") or "",
            }
            for t in calendar_dm_threads
        ]
    }, ensure_ascii=False)

    # 殿御命 2026-06-04: 新規 DM 宛先リストを Calendar 実機 user 一覧から動的描画
    # (リアルタイム性重視・cache 無し pass-through)
    dm_candidates = []
    try:
        from app.adapters.calendar_client import _to_calendar_uid
        me_cuid = _to_calendar_uid(actor_id)
        me_cuid_int = int(me_cuid) if me_cuid is not None else None
        _colors = ["bg-purple-600", "bg-emerald-600", "bg-amber-500", "bg-indigo-600", "bg-rose-500", "bg-sky-600", "bg-slate-700", "bg-fuchsia-600", "bg-teal-600", "bg-orange-500"]
        if hasattr(client, "get_users"):
            for u in (client.get_users(actor_user_id=actor_id) or []):
                if not isinstance(u, dict):
                    continue
                uid = u.get("id") or u.get("user_id")
                if uid is None:
                    continue
                try:
                    uid_int = int(uid)
                except (ValueError, TypeError):
                    continue
                if me_cuid_int is not None and uid_int == me_cuid_int:
                    continue  # 自分除外
                email = (u.get("email") or "").strip()
                name = (u.get("name") or u.get("full_name") or u.get("username") or "").strip()
                if not name:
                    name = email.split("@")[0] if email else f"user_{uid_int}"
                slug = email.split("@")[0] if email else f"u{uid_int}"
                initial = name[:1] if name else "?"
                role = (u.get("role") or "").strip()
                dm_candidates.append({
                    "uid": uid_int,
                    "slug": slug,
                    "name": name,
                    "email": email,
                    "role": role,
                    "initial": initial,
                    "color": _colors[uid_int % len(_colors)],
                })
        # 表示順: name 昇順 (安定 sort)
        dm_candidates.sort(key=lambda x: x.get("name", ""))
    except Exception:
        dm_candidates = []

    return _templates.TemplateResponse(
        request=request, name="messages.html",
        context={
            "user": user,
            "active": "messages",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "dm_threads": dm_threads,
            "calendar_dm_threads": calendar_dm_threads,
            "all_messages_count": len(all_messages),
            "shot_threads_participants": shot_threads_participants,
            "thread_query": thread,  # 殿御命 2026-06-03: task/shot 別 thread 指定 (現状 demo data 未配置)
            "dm_candidates": dm_candidates,  # 殿御命 2026-06-04: 新規 DM 宛先リスト (Calendar 実 user)
            "real_threads_json": real_threads_json,  # 殿御命 2026-06-04: JS openRealThread() 用 metadata
            "shot_group_threads": shot_group_threads,  # 殿御命 2026-06-04: 3+ 名 thread → SHOT タブ
            "dm_oneonone_threads": dm_oneonone_threads,  # 殿御命 2026-06-04: 2 名 thread → DM タブ
        },
    )


def _build_goodbye_message(name: str, completed: int) -> str:
    """完了タスク件数と user に応じたお礼メッセージを生成。
    件数閾値で 5 段階・name で個別感を加える。
    """
    n = name or "ユーザ"
    if completed == 0:
        return f"今日はゆっくり充電の日になりましたね、{n}さん。明日また一歩、進みましょう。"
    if completed <= 2:
        return f"{n}さん、本日も一歩前進。小さな一歩も確かな積み重ねです。ゆっくりお休みください。"
    if completed <= 4:
        return f"嵐の日も、{n}さんは最善を尽くしました。ゆっくり休んでください。"
    if completed <= 7:
        return f"{n}さん、本日も濃い一日でした。集中力に感謝・無理せず明日へ。"
    return f"{n}さん、驚くほどの集中力で大成果。十分な休息で次の波に備えてください。"


@router.get("/help")
def get_help(request: Request, submitted: str | None = None, actor_id: str = Depends(get_actor_id),
             db: Session = Depends(get_db)):
    user = _safe_user(actor_id)
    # 直近 5 件のバグ報告を表示 (公開性のため reporter は名前のみ)
    try:
        recent_reports = (
            db.query(BugReport)
            .order_by(BugReport.created_at.desc())
            .limit(5)
            .all()
        )
    except Exception:
        recent_reports = []
    return _templates.TemplateResponse(
        request=request, name="help.html",
        context={
            "user": user,
            "active": "",
            "submitted": submitted,
            "recent_reports": recent_reports,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )


@router.post("/api/bug_reports")
def post_bug_report(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    severity: str = Form(default="medium"),
    page_url: str = Form(default=""),
    operation_log: str = Form(default=""),   # 殿御命 2026-06-09: client が直近操作を JSON で添付
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    user = _safe_user(actor_id)
    # 軽い validation
    sev_norm = severity if severity in ("low", "medium", "high", "critical") else "medium"
    report = BugReport(
        reporter_user_id=str(actor_id) if actor_id else None,
        reporter_name=getattr(user, "name", None) or "ユーザ",
        title=title.strip()[:200],
        description=description.strip()[:5000],
        severity=sev_norm,
        page_url=page_url.strip()[:500] or None,
        operation_log=(operation_log or "").strip()[:20000] or None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        status="open",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    # 303 redirect で GET 化 (POST-then-GET 慣習)
    return RedirectResponse(url=f"/bug_report?submitted={report.id}", status_code=303)


@router.get("/bug_report")
def get_bug_report(request: Request, submitted: str | None = None,
                   actor_id: str = Depends(get_actor_id)):
    """殿御命 2026-06-09: バグ報告 専用ページ (サイドメニュー導線)。
    閲覧 UI は持たない (殿御命: Score 内にバグ閲覧 IF 不要)。送信のみ。"""
    user = _safe_user(actor_id)
    return _templates.TemplateResponse(
        request=request, name="bug_report.html",
        context={"user": user, "active": "bug_report", "submitted": submitted,
                 "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1"},
    )


@router.get("/api/bug_reports/export.csv")
def export_bug_reports_csv(actor_id: str = Depends(get_actor_id), db: Session = Depends(get_db)):
    """殿御命 2026-06-09: 全バグ報告を CSV ダウンロード (admin 限定・こちら側で閲覧用)。
    Score 内に閲覧 UI は作らず、この CSV を落として修正対象を選ぶ運用。"""
    from app.deps import get_actor_role
    import csv as _csv, io as _io
    from fastapi.responses import Response as _Response
    if get_actor_role(actor_id) != "admin":
        return JSONResponse(status_code=403, content={"detail": "admin 限定"})
    rows = db.query(BugReport).order_by(BugReport.created_at.desc()).all()
    buf = _io.StringIO()
    buf.write("﻿")  # Excel 用 BOM (日本語文字化け防止)
    w = _csv.writer(buf)
    w.writerow(["id", "created_at", "severity", "status", "reporter_name", "reporter_user_id",
                "title", "description", "page_url", "user_agent", "operation_log"])
    for r in rows:
        w.writerow([
            r.id, r.created_at.isoformat() if r.created_at else "", r.severity, r.status,
            r.reporter_name or "", r.reporter_user_id or "", r.title or "",
            r.description or "", r.page_url or "", r.user_agent or "", r.operation_log or "",
        ])
    return _Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=score_bug_reports.csv"},
    )


@router.get("/meetings/{meeting_id}")
def get_meeting_detail(meeting_id: int, request: Request, actor_id: str = Depends(get_actor_id)):
    """議事録 詳細 page (Calendar /api/meetings/{id} 経由)"""
    user = _safe_user(actor_id)
    client = get_calendar_client()
    try:
        meeting = client.get_meeting(meeting_id, actor_user_id=actor_id) or {}
    except Exception:
        meeting = {}
    # project 名解決
    project_name = ""
    pid = meeting.get("project_id") if isinstance(meeting, dict) else None
    if pid:
        try:
            projs = client.get_my_projects(actor_user_id=actor_id) or []
            for p in projs:
                if p.get("id") == pid:
                    project_name = p.get("name", "")
                    break
        except Exception:
            pass
    return _templates.TemplateResponse(
        request=request, name="meeting_detail.html",
        context={
            "user": user, "active": "",
            "meeting": meeting,
            "meeting_id": meeting_id,
            "project_name": project_name,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )


@router.get("/profile")
def get_profile(request: Request, actor_id: str = Depends(get_actor_id)):
    """ユーザープロファイル設定画面 (Google アカウント連携 / 誕生日 / 言語 / 通知 / 連絡先 等)。
    Calendar §5-bis 配備済 — `/api/me/profile` 経由で profile 取得。"""
    user = _safe_user(actor_id)
    client = get_calendar_client()
    try:
        profile = client.get_my_profile(actor_user_id=actor_id) or {}
    except Exception:
        profile = {}
    return _templates.TemplateResponse(
        request=request, name="profile.html",
        context={
            "user": user, "active": "profile",
            "profile": profile,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )


@router.patch("/api/bff/profile")
async def patch_profile_bff(request: Request, actor_id: str = Depends(get_actor_id)):
    """BFF: /api/me/profile への passthrough — Score Front から JSON で submit"""
    client = get_calendar_client()
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        updated = client.patch_my_profile(body=body, actor_user_id=actor_id)
        return {"ok": True, "profile": updated}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/bff/birthdays_today")
def get_birthdays_today_bff(project_id: int | None = None, actor_id: str = Depends(get_actor_id)):
    """BFF: /api/users/birthdays_today への passthrough (誕生日 banner 用)"""
    client = get_calendar_client()
    try:
        # project_id 未指定なら actor の最初の project を使う
        if project_id is None:
            projs = client.get_my_projects(actor_user_id=actor_id) or []
            if projs:
                project_id = projs[0].get("id", 33)
            else:
                project_id = 33
        return {"users": client.get_birthdays_today(project_id, actor_user_id=actor_id) or []}
    except Exception:
        return {"users": []}


@router.get("/goodbye")
def get_goodbye(request: Request, actor_id: str = Depends(get_actor_id)):
    user = _safe_user(actor_id)
    # 完了タスク件数を Calendar 経由で取得 (なければ stub 4)
    client = get_calendar_client()
    completed_count = 0
    try:
        # mock は固定 4 件想定・本物は別 endpoint 待ち (Calendar 22+4 未実装)
        # 暫定: 全 shots 中 'approved' or 'completed' なものを数える
        all_shots = []
        for proj in (client.get_my_projects(actor_user_id=actor_id) or []):
            try:
                shots = client.get_shots(proj["id"], actor_user_id=actor_id)
                all_shots.extend(shots)
            except Exception:
                pass
        completed_count = sum(
            1 for s in all_shots
            if getattr(s, "status", "").lower() in ("approved", "completed", "done")
        )
        if completed_count == 0:
            completed_count = 4  # mock fallback (旧 hardcode 維持)
    except Exception:
        completed_count = 4

    greeting_message = _build_goodbye_message(user.name or "", completed_count)
    response = _templates.TemplateResponse(
        request=request, name="goodbye.html",
        context={
            "user": user,
            "completed_count": completed_count,
            "greeting_message": greeting_message,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
    # 退勤完了 → 自動ログアウト (score_token cookie 削除・session 終了)
    # 殿御指示 2026-05-22: /goodbye 到達 = 既にログアウト状態であるべき
    response.delete_cookie(key="score_token", path="/")
    return response


@router.get("/projects")
def get_projects(request: Request, actor_id: str = Depends(get_actor_id)):
    from app.deps import get_actor_role
    from app.adapters.calendar_client import _to_calendar_uid
    client = get_calendar_client()
    role = get_actor_role(actor_id)

    def _fetch_all_projects():
        """/api/projects (m2m token・全件) — pm/admin 閲覧用 + auto-membership 判定用の全件母集合"""
        resp = httpx.get(
            f"{client.base_url}/api/projects",
            headers={"Authorization": f"Bearer {client.m2m_token}", "X-Actor-User-Id": str(actor_id)},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("projects", [])

    # PM (admin) は全 project 閲覧可・他 user は /api/me/projects (自分関連)
    try:
        if role in ("pm", "admin"):
            try:
                projects = _fetch_all_projects()
            except Exception:
                projects = client.get_my_projects(actor_user_id=actor_id) or []
        else:
            projects = client.get_my_projects(actor_user_id=actor_id)
    except httpx.ConnectError:
        projects = []
    # 殿御命 2026-06-09: /projects 表示は ① completed/cancelled 除外 ② 自分アサイン分のみ
    # 殿御命 2026-07-09 (cmd_076③・auto-membership): 下記 _assigned_ids 絞り込みが
    # role を問わず適用されており、pm/admin であっても「全 project 閲覧可」の方針
    # (直上のコメント)が事実上無効化されていた不具合があった(実機: Director 割当済だが
    # score 側の明示的メンバー登録が無い殿御自身のアカウントで、admin 権限にも関わらず
    # project が /projects 一覧から消え、shot/QC ビューアへ到達不能になる事象を確認)。
    # pm/admin はこの絞り込みを skip する。一般 user は明示メンバーに加え、Calendar 側で
    # director/pm/lead に割当られている project も auto-membership で union する。
    _EXCLUDED_STATUS = {"completed", "complete", "cancelled", "canceled", "archived"}
    if role in ("pm", "admin"):
        _assigned_ids = None  # 絞り込みなし (全 project 閲覧可の方針を尊重)
    else:
        try:
            _mine = client.get_my_projects(actor_user_id=actor_id)
            _mine = _mine if isinstance(_mine, list) else (_mine or {}).get("projects", [])
            _assigned_ids = {p.get("id") for p in _mine if isinstance(p, dict)}
        except Exception:
            _assigned_ids = None  # 取得失敗時は assignment 絞り込みを skip (status filter のみ適用)
        if _assigned_ids is not None and hasattr(client, "get_project_roles"):
            try:
                actor_cuid = _to_calendar_uid(actor_id)
            except Exception:
                actor_cuid = None
            if actor_cuid is not None:
                try:
                    _all_projects = _fetch_all_projects()
                except Exception:
                    _all_projects = []
                for p in (_all_projects or []):
                    pid = p.get("id") if isinstance(p, dict) else None
                    if pid is None or pid in _assigned_ids:
                        continue
                    try:
                        _roles = client.get_project_roles(pid, actor_user_id=actor_id) or {}
                    except Exception:
                        _roles = {}
                    if actor_cuid in (_roles.get("director"), _roles.get("pm"), _roles.get("lead")):
                        _assigned_ids.add(pid)
                        projects.append(p)

    def _project_visible(p):
        if (p.get("status") or "").lower() in _EXCLUDED_STATUS:
            return False
        if _assigned_ids is not None and p.get("id") not in _assigned_ids:
            return False
        return True

    _seen_ids = set()
    _deduped = []
    for p in (projects or []):
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        if pid in _seen_ids:
            continue
        _seen_ids.add(pid)
        _deduped.append(p)
    projects = [p for p in _deduped if _project_visible(p)]
    # 各 project 別の議事録を fetch (Calendar /projects/{id}/meetings)
    # test data filter: タイトルが test/sample/dummy 系の物は除外 (nibu 殿削除依頼後 filter 除去予定)
    _TEST_PATTERNS = ("テスト", "test", "更新されたテスト", "サンプル", "ダミー", "dummy")
    project_meetings = {}
    for p in (projects or []):
        pid = p.get("id")
        if pid is None:
            continue
        try:
            mtgs = client.get_meetings(pid, actor_user_id=actor_id) or []
            project_meetings[pid] = [
                m for m in mtgs
                if not any(pat.lower() in (m.get("title", "") or "").lower() for pat in _TEST_PATTERNS)
            ]
        except Exception:
            project_meetings[pid] = []
    from app.helpers.colors import attach_project_palettes
    projects = attach_project_palettes(projects)
    # 殿御命 2026-06-11: サイドメニューのユーザーアイコン用に user を渡す (他ページと統一・欠落で fallback「U」になっていた)
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    return _templates.TemplateResponse(
        request=request, name="projects.html",
        context={
            "user": user,
            "projects": projects,
            "project_meetings": project_meetings,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
