"""BFF Cross 統合テスト — calendar client factory をモック注入し実 Calendar API 不要で検証
endpoints: GET /api/bff/cross/projects / GET /api/bff/cross/production-tracker/{project_id}
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.main import app

_SECRET = "test_secret_key_32bytes_minimum!"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ─── /api/bff/cross/projects ────────────────────────────────────────────────

class TestBffCrossProjects:
    def test_bff_cross_projects_valid(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_my_projects.return_value = [{"id": "p1", "name": "Project 1"}]
            mock_inst.get_my_shots.return_value = [{"id": "s1", "name": "Shot 1"}]
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/projects",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "projects" in body
        assert "shots" in body
        assert body["projects"] == [{"id": "p1", "name": "Project 1"}]
        assert body["shots"] == [{"id": "s1", "name": "Shot 1"}]

    def test_bff_cross_projects_no_auth(self, client):
        resp = client.get("/api/bff/cross/projects")
        assert resp.status_code == 401

    def test_bff_cross_projects_nonexistent_email(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = None
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/projects",
                headers={"Authorization": f"Bearer {_make_token('nobody@example.com')}"},
            )

        assert resp.status_code == 403

    def test_bff_cross_projects_calendar_down(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_my_projects.side_effect = httpx.ConnectError("connection failed")
            mock_inst.get_my_shots.side_effect = httpx.ConnectError("connection failed")
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/projects",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["projects"] == []
        assert body["shots"] == []

    def test_bff_cross_projects_projects_http_error(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_inst.get_my_projects.side_effect = httpx.HTTPStatusError(
                "404 Not Found", request=MagicMock(), response=mock_response
            )
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/projects",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 404

    def test_bff_cross_projects_shots_http_error(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_my_projects.return_value = []
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_inst.get_my_shots.side_effect = httpx.HTTPStatusError(
                "403 Forbidden", request=MagicMock(), response=mock_response
            )
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/projects",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 403


# ─── /api/bff/cross/production-tracker/{project_id} ─────────────────────────

class TestBffCrossTracker:
    def test_bff_cross_tracker_valid(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_production_tracker.return_value = {"shots": [], "total": 0}
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/production-tracker/proj123",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "proj123"
        assert "tracker" in body
        assert body["tracker"] == {"shots": [], "total": 0}

    def test_bff_cross_tracker_no_auth(self, client):
        resp = client.get("/api/bff/cross/production-tracker/proj123")
        assert resp.status_code == 401

    def test_bff_cross_tracker_nonexistent_email(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = None
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/production-tracker/proj123",
                headers={"Authorization": f"Bearer {_make_token('nobody@example.com')}"},
            )

        assert resp.status_code == 403

    def test_bff_cross_tracker_not_found(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_inst.get_production_tracker.side_effect = httpx.HTTPStatusError(
                "404 Not Found", request=MagicMock(), response=mock_response
            )
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/production-tracker/proj_missing",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 404

    def test_bff_cross_tracker_calendar_down(self, client):
        with patch("app.routers.bff_cross.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.resolve_email_to_user_id.return_value = 5
            mock_inst.get_production_tracker.side_effect = httpx.ConnectError("connection failed")
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/api/bff/cross/production-tracker/proj123",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "proj123"
        assert body["tracker"] == {}
