import os
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.adapters.calendar_factory import get_calendar_client
from app.auth import create_score_token, get_next_5am_jst

router = APIRouter()


def _safe_next_path(next: str | None) -> str | None:
    """next= の open-redirect 対策: 自サイト内の相対パスのみ許可。"""
    if not next or not next.startswith("/") or next.startswith("//") or "://" in next:
        return None
    return next


@router.post("/api/auth/login")
def post_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(default=""),
    next: str | None = Form(default=None),
):
    """Score login — Calendar /api/auth/token 経由で password 検証.
    CALENDAR_MOCK=1 時は password 検証 skip(mock 互換).
    """
    client = get_calendar_client()
    user_id = client.resolve_email_to_user_id(username)
    if user_id is None:
        return RedirectResponse(url="/login?error=user_not_found", status_code=303)

    # Calendar password 検証 (real mode)
    if os.environ.get("CALENDAR_MOCK", "0") != "1":
        cal_base = os.environ.get("CALENDAR_API_BASE_URL") or os.environ.get("CALENDAR_BASE_URL") or "http://192.168.44.253:8001"
        try:
            resp = httpx.post(
                f"{cal_base}/api/auth/token",
                data={"username": username, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5,
            )
            if resp.status_code != 200:
                return RedirectResponse(url="/login?error=invalid_password", status_code=303)
        except Exception:
            # Calendar 接続不可時は fail-open でなく fail-closed
            return RedirectResponse(url="/login?error=calendar_unreachable", status_code=303)

    token = create_score_token(username)
    exp = get_next_5am_jst()
    max_age = max(0, int((exp - datetime.now(timezone.utc)).total_seconds()))

    # 殿御下命動線: login → (前日退勤未提出なら /exit_report) → /routine → /dashboard
    # 殿御命 2026-06-05: 当日 routine 既提出 (cookie) なら routine skip → dashboard 直行
    from app.routers.pages_routine import _has_prev_day_exit_submitted
    from datetime import timedelta as _td
    _jst_today = (datetime.utcnow() + _td(hours=9)).date().isoformat()
    _routine_done = request.cookies.get("score_routine_done", "")
    routine_done_today = bool(_routine_done) and _routine_done[:10] == _jst_today
    safe_next = _safe_next_path(next)
    if routine_done_today:
        # subtask_070e: 通知の QC ビューアリンク等、当日routine提出済なら元の遷移先へ復帰
        next_url = safe_next or "/dashboard"
    elif _has_prev_day_exit_submitted(client, str(user_id)):
        next_url = "/routine"
    else:
        next_url = "/exit_report?mode=previous&return=routine"
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie(
        key="score_token",
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,
        max_age=max_age,
    )
    return response


# 殿御命 2026-06-08: tutorial 自動撮影用 test login EP (mock 環境限定)
@router.get("/__test_login")
def test_login(user_id: int, next: str = "/dashboard"):
    """CALENDAR_MOCK=1 限定: user_id 指定で cookie 発行 + redirect (tutorial 撮影専用)"""
    if os.environ.get("CALENDAR_MOCK", "0") != "1":
        return RedirectResponse(url="/login?error=test_login_disabled", status_code=303)
    client = get_calendar_client()
    user = None
    try:
        if hasattr(client, "get_users"):
            for u in (client.get_users(actor_user_id=str(user_id)) or []):
                if isinstance(u, dict) and (u.get("id") == user_id or u.get("user_id") == user_id):
                    user = u; break
    except Exception:
        pass
    if not user:
        return RedirectResponse(url=f"/login?error=user_not_found&uid={user_id}", status_code=303)
    email = user.get("email") or f"uid{user_id}@test"
    token = create_score_token(email)
    exp = get_next_5am_jst()
    max_age = max(0, int((exp - datetime.now(timezone.utc)).total_seconds()))
    # 加えて routine 提出済 cookie + 前日退勤済 localStorage 不可なので URL 経由は guard 通過困難
    # → cookie で score_routine_done 設定 = routine skip
    from datetime import timedelta as _td
    _jst_today = (datetime.utcnow() + _td(hours=9)).isoformat(timespec="seconds")
    # 殿御命 2026-06-08: 撮影用 _screenshot=1 を next URL に自動付与 (SSE skip)
    next_url = next
    if "?" in next_url:
        next_url += "&_screenshot=1"
    else:
        next_url += "?_screenshot=1"
    response = RedirectResponse(url=next_url, status_code=303)
    response.set_cookie("score_token", token, httponly=True, samesite="lax", path="/", secure=False, max_age=max_age)
    response.set_cookie("score_routine_done", _jst_today, httponly=False, samesite="lax", path="/", secure=False, max_age=max_age)
    return response


@router.post("/api/auth/logout")
def post_logout():
    """Cookie削除 + /login へリダイレクト"""
    response = RedirectResponse(url="/login", status_code=303)
    response.set_cookie(
        key="score_token",
        value="",
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,
        max_age=0,
    )
    return response
