"""書込BFF 10EP テスト — calender_api_complete_list.md §8 実在EPのみ
10EP × 2ケース (valid_jwt / no_auth) = 20テスト + resolve関連テスト
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id, get_db
from app.main import app
from app.routers import bff_write as _bff_write_module

# bff_write router は subtask_445s が main.py に結線するまでテスト用に登録
app.include_router(_bff_write_module.router)

_SECRET = "test_secret_key_32bytes_minimum!"
_MOCK_RESULT = {"ok": True}
_RESOLVED_ACTOR_ID = "42"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _db_override():
    mock_db = MagicMock()
    yield mock_db


def _mock_get_actor_id():
    return _RESOLVED_ACTOR_ID


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_actor_id] = _mock_get_actor_id
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client_no_auth():
    app.dependency_overrides[get_db] = _db_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


# ─── POST /api/bff/retakes ───────────────────────────────────────────────────

class TestBffWriteRetakes:
    def test_post_retakes_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_retakes.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/retakes", json={"shot_id": 1}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_retakes.assert_called_once()

    def test_post_retakes_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/retakes", json={"shot_id": 1})
        assert resp.status_code == 401


# ─── POST /api/bff/shots/{id}/approve ───────────────────────────────────────

class TestBffWriteShotApprove:
    def test_post_shot_approve_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_shot_approve.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/shots/1/approve", json={}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_shot_approve.assert_called_once()

    def test_post_shot_approve_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/shots/1/approve", json={})
        assert resp.status_code == 401


# ─── POST /api/bff/look_distributions ───────────────────────────────────────

class TestBffWriteLookDistributions:
    def test_post_look_distributions_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_look_distributions.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/look_distributions", json={"look_id": 5}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_look_distributions.assert_called_once()

    def test_post_look_distributions_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/look_distributions", json={"look_id": 5})
        assert resp.status_code == 401


# ─── POST /api/bff/timecards/clock_out ──────────────────────────────────────

class TestBffWriteTimecardClockOut:
    def test_post_timecard_clock_out_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_timecard_clock_out.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/timecards/clock_out", json={"hours": 8}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_timecard_clock_out.assert_called_once()

    def test_post_timecard_clock_out_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/timecards/clock_out", json={"hours": 8})
        assert resp.status_code == 401


# ─── POST /api/bff/routines ─────────────────────────────────────────────────

class TestBffWriteRoutines:
    def test_post_routines_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_routines.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/routines", json={"condition": "good"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_routines.assert_called_once()

    def test_post_routines_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/routines", json={"condition": "good"})
        assert resp.status_code == 401


# ─── POST /api/bff/change_requests ──────────────────────────────────────────

class TestBffWriteChangeRequests:
    def test_post_change_requests_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_change_requests.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/change_requests", json={"reason": "delay"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_change_requests.assert_called_once()

    def test_post_change_requests_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/change_requests", json={"reason": "delay"})
        assert resp.status_code == 401


# ─── POST /api/bff/troubles ─────────────────────────────────────────────────

class TestBffWriteTroubles:
    def test_post_troubles_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_troubles.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/troubles", json={"description": "render error"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_troubles.assert_called_once()

    def test_post_troubles_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/troubles", json={"description": "render error"})
        assert resp.status_code == 401


# ─── PATCH /api/bff/troubles/{id}/resolve ───────────────────────────────────

class TestBffWriteTroubleResolve:
    def test_patch_trouble_resolve_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.patch_trouble_resolve.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/troubles/1/resolve", json={}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_trouble_resolve.assert_called_once()

    def test_patch_trouble_resolve_no_auth(self, client_no_auth):
        resp = client_no_auth.patch("/api/bff/troubles/1/resolve", json={})
        assert resp.status_code == 401


# ─── POST /api/bff/messages ─────────────────────────────────────────────────

class TestBffWriteMessages:
    def test_post_messages_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.post_messages.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.post("/api/bff/messages", json={"text": "hello"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.post_messages.assert_called_once()

    def test_post_messages_no_auth(self, client_no_auth):
        resp = client_no_auth.post("/api/bff/messages", json={"text": "hello"})
        assert resp.status_code == 401


# ─── PATCH /api/bff/notifications/{id}/read ─────────────────────────────────

class TestBffWriteNotificationRead:
    def test_patch_notification_read_valid_jwt(self, client):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.patch_notification_read.return_value = _MOCK_RESULT
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/notifications/1/read", json={}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_notification_read.assert_called_once()

    def test_patch_notification_read_no_auth(self, client_no_auth):
        resp = client_no_auth.patch("/api/bff/notifications/1/read", json={})
        assert resp.status_code == 401


# ─── resolve失敗(403) テスト ─────────────────────────────────────────────────

class TestBffWriteResolveFailure:
    def test_retakes_user_not_found_returns_403(self):
        from fastapi import HTTPException

        def _actor_id_not_found():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        app.dependency_overrides[get_db] = _db_override
        app.dependency_overrides[get_actor_id] = _actor_id_not_found
        with TestClient(app) as c:
            resp = c.post("/api/bff/retakes", json={"shot_id": 1}, headers={"Authorization": f"Bearer {_make_token()}"})
        app.dependency_overrides.clear()
        assert resp.status_code == 403
