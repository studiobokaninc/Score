from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.adapters.calendar_factory import get_calendar_client
from app.auth import verify_jwt
from app.deps import get_actor_id, get_actor_project_role, get_db
from app.models import ScoreUserRole, TimecardLog

router = APIRouter()


def _extract_jwt_sub(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    payload = verify_jwt(token)
    return str(payload["sub"])


@router.get("/api/bff/me")
def get_me(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    jwt_sub = _extract_jwt_sub(authorization)
    db.query(ScoreUserRole).filter(ScoreUserRole.user_id == jwt_sub).all()
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    user = client.get_me(actor_user_id=actor_id_str)
    return JSONResponse(
        content=asdict(user),
        headers={"X-Actor-User-Id": jwt_sub},
    )


@router.get("/api/bff/shots")
def get_shots(
    project_id: int,
    authorization: str | None = Header(default=None),
):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    shots = client.get_shots(project_id, actor_user_id=actor_id_str)
    return JSONResponse(
        content=[asdict(s) for s in shots],
        headers={"X-Actor-User-Id": jwt_sub},
    )


@router.get("/api/bff/users")
def get_all_users(
    actor_id: str = Depends(get_actor_id),
):
    """Calendar 全ユーザ一覧 (admin JWT) — 送信先セレクトの全ユーザー表示用"""
    client = get_calendar_client()
    users = client.get_users(actor_user_id=actor_id) or []
    return JSONResponse(
        content=[
            {"id": u.get("id"), "name": u.get("name") or u.get("email", ""), "email": u.get("email", "")}
            for u in users if isinstance(u, dict)
        ]
    )


@router.get("/api/bff/shots/{id}/tasks")
def get_shot_tasks(
    id: int,
    authorization: str | None = Header(default=None),
):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    tasks = client.get_tasks(id, actor_user_id=actor_id_str)
    return JSONResponse(
        content=[asdict(t) for t in tasks],
        headers={"X-Actor-User-Id": jwt_sub},
    )


# cmd_279(丙)・承認済(甲案・2026-09-30): 出退勤三欄(blocker/handover/
# next_priority)を日付・利用者指定で読める口(外部連携チームからの新規依頼)。
# 既定は当日+前日までに限る(理由: 今誰が詰まっているかを知るのに古い控えは
# 要らぬ・#279e指図)。上限は承認があれば増やせるよう定数で持つ。
_ATTENDANCE_READ_MAX_DAYS_BACK = 1


def _is_attendance_date_readable(date_str: str) -> bool:
    from datetime import datetime, timedelta, timezone
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return False
    jst = timezone(timedelta(hours=9))
    today = datetime.now(jst).date()
    days_back = (today - target).days
    return 0 <= days_back <= _ATTENDANCE_READ_MAX_DAYS_BACK


def _actor_can_read_attendance(actor_id: str, target_user_id: str, client=None) -> bool:
    """#279e是正——「その案件における役職で照らす」(cmd_167 get_actor_project_role・
    #276/#283是正と同じ発想)を出退勤三欄の読み口にも通す。本人自身の分は
    無条件で読める。他人の分は、対象者が加わっている案件のいずれかで
    director/pm/lead(またはスタジオ全体のadmin)の役職にある者に限り読める。
    Scoreにはproject単位のmember一覧を引く手段が無いため、対象者の所属案件は
    Calendar側 GET /api/me/projects を対象者のuidで(admin token経由・
    app.adapters.calendar_client._headers が常時admin認証を使う作りに乗る)
    引くことで代替する。"""
    if str(actor_id) == str(target_user_id):
        return True
    if client is None:
        client = get_calendar_client()
    if get_actor_project_role(actor_id, None, client=client) == "admin":
        return True
    try:
        projects = client.get_my_projects(actor_user_id=target_user_id) or []
    except Exception:
        projects = []
    if isinstance(projects, dict):
        projects = projects.get("projects", [])
    for proj in projects:
        pid = proj.get("id") if isinstance(proj, dict) else None
        if pid is None:
            continue
        if get_actor_project_role(actor_id, pid, client=client) in ("director", "pm", "lead"):
            return True
    return False


@router.get("/api/bff/timecards/{target_user_id}/{date}")
def get_timecard_attendance_fields(
    target_user_id: str,
    date: str,
    actor_id: str = Depends(get_actor_id),
    db: Session = Depends(get_db),
):
    """#279e是正——出退勤の「詰まり/申し送り/翌日の優先」三欄(TimecardLog.
    blocker/handover/next_priority)をそのまま読める口。
    備え四つ(#279e指図・承認済の甲案):
    (一) 告知: 画面側(app/templates/exit_report.html)に一行案内を掲示済。
    (二) 日付限定: 既定で当日+前日まで(_is_attendance_date_readable・
         _ATTENDANCE_READ_MAX_DAYS_BACK)。それより古い日は400で拒否。
    (三) 読みの控え: 新規の記録機構は設けず、既存の利用ログ(app.usage_log・
         cmd_187 _UsageLogMiddleware・全requestをmethod/path/actor(user_id)/
         時刻で自動捕捉する既存の唯一の口)にそのまま乗る。本パスに
         target_user_id と date を含めているため、追加コード無しで
         「いつ・誰が・誰の・どの日の分を読んだか」が既存の口だけで揃う。
    (四) condition(体調)はRoutineLog側のテーブルでありTimecardLogには
         無いため混入せず、user_name も返さない。
    役職の照らし方は _actor_can_read_attendance 参照(本人自身は無条件、
    他人は対象者の所属案件いずれかでdirector/pm/lead/adminの者のみ許可)。
    """
    if not _is_attendance_date_readable(date):
        raise HTTPException(
            status_code=400,
            detail=f"日付は当日から{_ATTENDANCE_READ_MAX_DAYS_BACK}日前までのみ指定できます",
        )
    if not _actor_can_read_attendance(actor_id, target_user_id):
        raise HTTPException(status_code=403, detail="この案件の関係者ではないため閲覧できません")
    row = (
        db.query(TimecardLog)
        .filter(TimecardLog.user_id == str(target_user_id), TimecardLog.date == date)
        .order_by(TimecardLog.id.desc())
        .first()
    )
    return JSONResponse(content={
        "user_id": str(target_user_id),
        "date": date,
        "blocker": row.blocker if row else None,
        "handover": row.handover if row else None,
        "next_priority": row.next_priority if row else None,
        "found": row is not None,
    })
