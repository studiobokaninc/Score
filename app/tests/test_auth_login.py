import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("JWT_SECRET", "test_secret_key")

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key")
    monkeypatch.setenv("CALENDAR_MOCK", "1")


def test_login_valid_email(seeded_user_email):
    """有効メールでログイン → 303 /dashboard + Set-Cookie score_token"""
    with patch("app.routers.auth_login.get_calendar_client") as mock_factory, \
         patch("app.routers.pages_routine._has_submitted_routine_today", return_value=False), \
         patch("app.routers.pages_routine._has_prev_day_exit_submitted", return_value=True):
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = 1
        resp = client.post("/api/auth/login", data={"username": seeded_user_email, "password": "any"})
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/routine")
    cookie = resp.headers.get("set-cookie", "")
    assert "score_token=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()


def test_login_unknown_email():
    """未seed メール → 303 /login?error=user_not_found"""
    with patch("app.routers.auth_login.get_calendar_client") as mock_factory:
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = None
        resp = client.post("/api/auth/login", data={"username": "nobody@x.invalid", "password": "pw"})
    assert resp.status_code == 303
    assert "error=user_not_found" in resp.headers["location"]


def test_login_missing_username():
    """username フィールド欠如 → 422"""
    resp = client.post("/api/auth/login", data={"password": "pw"})
    assert resp.status_code == 422


def test_logout_clears_cookie():
    """ログアウト → 303 /login + score_token Max-Age=0"""
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login")
    cookie = resp.headers.get("set-cookie", "")
    assert "score_token=" in cookie
    assert "max-age=0" in cookie.lower()


def test_login_redirects_to_routine():
    """login 成功 → /routine に redirect (cmd_456 変更)"""
    with patch("app.routers.auth_login.get_calendar_client") as mock_factory, \
         patch("app.routers.pages_routine._has_submitted_routine_today", return_value=False), \
         patch("app.routers.pages_routine._has_prev_day_exit_submitted", return_value=True):
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = 1
        resp = client.post("/api/auth/login",
                           data={"username": "ryoji@studiobokan.com", "password": "test"},
                           follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/routine")


def test_login_routine_done_today_skips_to_dashboard_even_without_cookie():
    """cmd_087: 別PC/別ブラウザ(cookie無し)でも、当日サーバDB提出済みなら
    routine skip → dashboard 直行。旧cookie方式のPC依存バグの回帰テスト。"""
    with patch("app.routers.auth_login.get_calendar_client") as mock_factory, \
         patch("app.routers.pages_routine._has_submitted_routine_today", return_value=True) as mock_check:
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = 1
        # score_routine_done cookie は一切送らない = 初めて使うPC/ブラウザを模す
        resp = client.post("/api/auth/login",
                           data={"username": "ryoji@studiobokan.com", "password": "test"},
                           follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/dashboard")
    mock_check.assert_called_once_with(1)


def test_login_routine_not_done_ignores_stale_cookie():
    """cmd_087: サーバDB未提出なら、別PCの古いcookieが残っていても routine を表示する
    (cookieはもはや判定に使われないことの確認)。"""
    with patch("app.routers.auth_login.get_calendar_client") as mock_factory, \
         patch("app.routers.pages_routine._has_submitted_routine_today", return_value=False), \
         patch("app.routers.pages_routine._has_prev_day_exit_submitted", return_value=True):
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = 1
        client.cookies.set("score_routine_done", "2020-01-01T09:00:00+09:00")
        try:
            resp = client.post("/api/auth/login",
                               data={"username": "ryoji@studiobokan.com", "password": "test"},
                               follow_redirects=False)
        finally:
            client.cookies.clear()
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/routine")
