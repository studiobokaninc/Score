import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.routers import pages_shot

# /shot/{id} は get_actor_id を Depends() 経由で使用するため cookie fallback 検証に適切
_test_app = FastAPI()
_test_app.include_router(pages_shot.router)

VALID_JWT_EMAIL = "sato@studio.jp"
_RESOLVED_ACTOR_ID = "42"


def _make_token(email: str = VALID_JWT_EMAIL) -> str:
    from app.auth import create_score_token
    return create_score_token(email)


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key_32bytes_minimum!")


def _mock_calendar():
    mock = MagicMock()
    mock.resolve_email_to_user_id.return_value = _RESOLVED_ACTOR_ID
    mock.get_tasks.return_value = []
    return mock


def test_authorization_bearer_still_works():
    """既存 Authorization Bearer path は変更なく動作する(退行ゼロ確認)"""
    token = _make_token()
    with patch("app.adapters.calendar_client.CalendarClient", return_value=_mock_calendar()), \
         patch("app.routers.pages_shot.get_calendar_client", return_value=_mock_calendar()):
        with TestClient(_test_app, follow_redirects=False) as c:
            resp = c.get("/shot/1", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code != 401


def test_cookie_only_path():
    """cookie のみで get_actor_id を使う EP が 401/500 にならない"""
    token = _make_token()
    with patch("app.adapters.calendar_client.CalendarClient", return_value=_mock_calendar()), \
         patch("app.routers.pages_shot.get_calendar_client", return_value=_mock_calendar()):
        with TestClient(_test_app, follow_redirects=False) as c:
            c.cookies.set("score_token", token)
            resp = c.get("/shot/1")
    assert resp.status_code not in (401, 500)


def test_no_auth_no_cookie():
    """認証情報なし → 401"""
    with TestClient(_test_app, follow_redirects=False) as c:
        resp = c.get("/shot/1")
    assert resp.status_code in (401, 302, 303)


def test_invalid_cookie_rejected():
    """不正 JWT cookie → 401"""
    with TestClient(_test_app, follow_redirects=False) as c:
        c.cookies.set("score_token", "invalid.jwt.token")
        resp = c.get("/shot/1")
    assert resp.status_code in (401, 302, 303)
