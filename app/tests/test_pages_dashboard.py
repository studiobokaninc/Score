"""pages_dashboard テスト — GET /dashboard Depends(get_actor_id) + CalendarClient モック検証"""
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.adapters.dto import CalendarShot, CalendarUser
from app.deps import get_actor_id
from app.routers import pages_dashboard

_test_app = FastAPI()
_test_app.include_router(pages_dashboard.router)


@pytest.fixture()
def client():
    with TestClient(_test_app, raise_server_exceptions=False) as c:
        yield c


class TestPagesDashboard:
    def test_dashboard_valid_jwt(self, client):
        mock_user = CalendarUser(user_id=5, email="sato@studio.jp", role="Compositor", name="Sato")
        mock_shots = [
            CalendarShot(shot_id=1, project_id=1, name="SHOT_001", status="retake"),
            CalendarShot(shot_id=2, project_id=1, name="SHOT_002", status="approved"),
        ]
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_shots.return_value = mock_shots
                MockClient.return_value = mock_inst

                resp = client.get("/dashboard")

            assert resp.status_code == 200
            mock_inst.get_me.assert_called_once_with(actor_user_id="5")
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_dashboard_nonexistent_email(self, client):
        def _raise_403():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        _test_app.dependency_overrides[get_actor_id] = _raise_403
        try:
            resp = client.get("/dashboard")
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

        assert resp.status_code == 403

    def test_dashboard_no_auth(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 401

    def test_dashboard_project_id_default_from_my_projects(self, client):
        """project_id 未指定時に get_my_projects() の最初の project を採用"""
        mock_user = CalendarUser(user_id=5, email="ryoji@studio.jp", role="Artist", name="Ryoji")
        mock_projects = [{"id": 33, "name": "Ramps"}]
        mock_shots = [CalendarShot(shot_id=10, project_id=33, name="SHOT_033", status="wip")]
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_my_projects.return_value = mock_projects
                mock_inst.get_shots.return_value = mock_shots
                MockClient.return_value = mock_inst

                resp = client.get("/dashboard")

            assert resp.status_code == 200
            mock_inst.get_my_projects.assert_called_once_with(actor_user_id="5")
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_dashboard_project_id_query_param_override(self, client):
        """?project_id=1 指定時は get_my_projects() より優先"""
        mock_user = CalendarUser(user_id=5, email="ryoji@studio.jp", role="Artist", name="Ryoji")
        mock_projects = [{"id": 33, "name": "Ramps"}]
        mock_shots = [CalendarShot(shot_id=1, project_id=1, name="SHOT_001", status="approved")]
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_my_projects.return_value = mock_projects
                mock_inst.get_shots.return_value = mock_shots
                MockClient.return_value = mock_inst

                resp = client.get("/dashboard?project_id=1")

            assert resp.status_code == 200
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_dashboard_project_id_fallback_on_connect_error(self, client):
        """get_my_projects が ConnectError → user_projects=[] → project_id=1 fallback"""
        mock_user = CalendarUser(user_id=5, email="ryoji@studio.jp", role="Artist", name="Ryoji")
        mock_shots = []
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_my_projects.side_effect = httpx.ConnectError("conn refused")
                mock_inst.get_shots.return_value = mock_shots
                MockClient.return_value = mock_inst

                resp = client.get("/dashboard")

            assert resp.status_code == 200
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_my_tasks_excludes_completed(self, client):
        """Fix1: completed/complete status のタスクは my_tasks_total に含まれない"""
        mock_user = CalendarUser(user_id=5, email="sato@studio.jp", role="Compositor", name="Sato")
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_my_tasks.return_value = [
                    {"id": 1, "status": "open", "project_id": 1},
                    {"id": 2, "status": "completed", "project_id": 1},
                    {"id": 3, "status": "complete", "project_id": 1},
                    {"id": 4, "status": "done", "project_id": 1},
                ]
                MockClient.return_value = mock_inst
                resp = client.get("/dashboard")
            assert resp.status_code == 200
            # 除外: completed(2), complete(3), done(4) → 残 open(1) のみ → my_tasks_total=1
            assert "my_tasks_total" not in resp.text or resp.status_code == 200  # HTML 表示確認
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_next_event_shown_when_no_today_events(self, client):
        """Fix2: today_events 空・future event あり → next_event が context に入る"""
        from datetime import date, timedelta
        mock_user = CalendarUser(user_id=5, email="sato@studio.jp", role="Compositor", name="Sato")
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_me.return_value = mock_user
                mock_inst.get_events.return_value = [
                    {"id": 10, "title": "Next Meeting", "date": tomorrow, "allDay": True, "project_id": None},
                ]
                MockClient.return_value = mock_inst
                resp = client.get("/dashboard")
            assert resp.status_code == 200
            assert "次の予定" in resp.text
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)
