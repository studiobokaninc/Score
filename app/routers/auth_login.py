import os
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from app.adapters.calendar_factory import get_calendar_client
from app.auth import create_score_token, get_next_5am_jst

router = APIRouter()


@router.post("/api/auth/login")
def post_login(
    username: str = Form(...),
    password: str = Form(default=""),
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

    # 殿御下命動線: login → (前日退勤報告未提出なら /exit_report) → /routine → /dashboard
    from app.routers.pages_routine import _has_prev_day_exit_submitted
    if _has_prev_day_exit_submitted(client, str(user_id)):
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
