import os

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
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

    # nibu 殿納品 /api/me/dm/threads 試行取得 (response 形式確認後に template 切替予定)
    calendar_dm_threads = []
    try:
        if hasattr(client, "get_dm_threads"):
            calendar_dm_threads = client.get_dm_threads(actor_user_id=actor_id) or []
    except Exception:
        calendar_dm_threads = []

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
        status="open",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    # 303 redirect で GET 化 (POST-then-GET 慣習)
    return RedirectResponse(url=f"/help?submitted={report.id}", status_code=303)


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
    client = get_calendar_client()
    role = get_actor_role(actor_id)
    # PM (admin) は全 project 閲覧可・他 user は /api/me/projects (自分関連)
    try:
        if role in ("pm", "admin"):
            # /api/projects で全 project 取得
            try:
                resp = httpx.get(
                    f"{client.base_url}/api/projects",
                    headers={"Authorization": f"Bearer {client.m2m_token}", "X-Actor-User-Id": str(actor_id)},
                    timeout=5,
                )
                resp.raise_for_status()
                projects = resp.json()
                if not isinstance(projects, list):
                    projects = projects.get("projects", [])
            except Exception:
                projects = client.get_my_projects(actor_user_id=actor_id) or []
        else:
            projects = client.get_my_projects(actor_user_id=actor_id)
    except httpx.ConnectError:
        projects = []
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
    return _templates.TemplateResponse(
        request=request, name="projects.html",
        context={
            "projects": projects,
            "project_meetings": project_meetings,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
