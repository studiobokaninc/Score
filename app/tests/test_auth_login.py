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
         patch("app.routers.pages_routine._has_prev_day_exit_submitted", return_value=True):
        instance = MagicMock()
        mock_factory.return_value = instance
        instance.resolve_email_to_user_id.return_value = 1
        resp = client.post("/api/auth/login",
                           data={"username": "ryoji@studiobokan.com", "password": "test"},
                           follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/routine")
