"""BFF 統合テスト — CalendarClient をモック注入し実 Calendar API 不要で検証
endpoints: GET /api/bff/me / GET /api/bff/shots / GET /api/bff/shots/{id}/tasks
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.adapters.dto import CalendarShot, CalendarTask, CalendarUser
from app.deps import get_db
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"
_SECRET = "test_secret_key_32bytes_minimum!"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _db_override():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []
    yield mock_db


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_db] = _db_override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ─── /api/bff/me ────────────────────────────────────────────────────────────

class TestBffMe:
    def test_bff_me_valid_jwt(self, client):
        raw = json.loads((FIXTURES / "user_me.json").read_text())
        expected = CalendarUser(
            user_id=raw["id"], email=raw["email"], role=raw["role"], name=raw["name"]
        )
        with patch("app.routers.bff.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_me.return_value = expected
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/me",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["email"] == expected.email
        assert body["role"] == expected.role
        mock_inst.resolve_email_to_user_id.assert_called_once_with("sato@studio.jp")
        mock_inst.get_me.assert_called_once_with(actor_user_id="5")

    def test_bff_me_nonexistent_email(self, client):
        with patch("app.routers.bff.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = None
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/me",
                headers={"Authorization": f"Bearer {_make_token('nonexistent@example.com')}"},
            )

        assert resp.status_code == 403
        mock_inst.resolve_email_to_user_id.assert_called_once_with("nonexistent@example.com")
        mock_inst.get_me.assert_not_called()

    def test_bff_me_no_auth(self, client):
        resp = client.get("/api/bff/me")
        assert resp.status_code == 401


# ─── /api/bff/shots ─────────────────────────────────────────────────────────

class TestBffShots:
    def test_bff_shots_valid_jwt(self, client):
        raw = json.loads((FIXTURES / "shots_list.json").read_text())
        expected = [
            CalendarShot(
                shot_id=item["id"],
                project_id=item["project_id"],
                name=item.get("shot_code") or f'{item.get("seq_code", "")}/{item.get("shot_code", "")}',
                status=item["status"],
            )
            for item in raw
        ]
        with patch("app.routers.bff.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_shots.return_value = expected
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/shots",
                params={"project_id": 1},
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 3
        assert body[0]["name"] == "SHOT_001"
        mock_inst.resolve_email_to_user_id.assert_called_once_with("sato@studio.jp")
        mock_inst.get_shots.assert_called_once_with(1, actor_user_id="5")

    def test_bff_shots_no_auth(self, client):
        resp = client.get("/api/bff/shots", params={"project_id": 1})
        assert resp.status_code == 401


# ─── /api/bff/shots/{id}/tasks ──────────────────────────────────────────────

class TestBffShotTasks:
    def test_bff_tasks_valid_jwt(self, client):
        raw = json.loads((FIXTURES / "tasks_list.json").read_text())
        expected = [
            CalendarTask(
                task_id=item["id"],
                shot_id=item["shot_id"],
                type=item["type"],
                assignee_id=item.get("assigned_to"),
                status=item["status"],
            )
            for item in raw
        ]
        with patch("app.routers.bff.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_tasks.return_value = expected
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/shots/1/tasks",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        types = {t["type"] for t in body}
        assert types == {"Composite", "Lighting"}
        mock_inst.resolve_email_to_user_id.assert_called_once_with("sato@studio.jp")
        mock_inst.get_tasks.assert_called_once_with(1, actor_user_id="5")

    def test_bff_tasks_no_auth(self, client):
        resp = client.get("/api/bff/shots/1/tasks")
        assert resp.status_code == 401
