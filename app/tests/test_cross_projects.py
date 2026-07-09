"""cross_projects テスト — GET /cross/projects Depends(get_actor_id) + CalendarClient モック検証"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import cross_projects

_test_app = FastAPI()
_test_app.include_router(cross_projects.router)


@pytest.fixture()
def client():
    with TestClient(_test_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_with_auth():
    _test_app.dependency_overrides[get_actor_id] = lambda: "5"
    with TestClient(_test_app, raise_server_exceptions=False) as c:
        yield c
    _test_app.dependency_overrides.pop(get_actor_id, None)


class TestCrossProjects:
    def test_cross_projects_valid_jwt(self, client):
        mock_projects = [{"id": 1, "name": "Project A", "status": "active"}]
        mock_shots = [{"id": 1, "name": "SHOT_001", "status": "in_progress"}]
        _test_app.dependency_overrides[get_actor_id] = lambda: "5"
        try:
            with patch("app.routers.cross_projects.get_calendar_client") as MockClient:
                mock_inst = MagicMock()
                mock_inst.get_my_projects.return_value = mock_projects
                mock_inst.get_my_shots.return_value = mock_shots
                MockClient.return_value = mock_inst

                resp = client.get("/cross/projects")

            assert resp.status_code == 200
            mock_inst.get_my_projects.assert_called_once_with(actor_user_id="5")
            mock_inst.get_my_shots.assert_called_once_with(actor_user_id="5")
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

    def test_cross_projects_nonexistent_email(self, client):
        def _raise_403():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        _test_app.dependency_overrides[get_actor_id] = _raise_403
        try:
            resp = client.get("/cross/projects")
        finally:
            _test_app.dependency_overrides.pop(get_actor_id, None)

        assert resp.status_code == 403

    def test_cross_projects_no_auth(self, client):
        resp = client.get("/cross/projects")
        assert resp.status_code == 401

    def test_cross_projects_shot_rendering_no_dict_literal(self, client_with_auth):
        """shot が dict で渡されても dict literal でなく shot_code が描画されること"""
        mock_shots = [{"shot_code": "as01", "seq_code": "SEQ001", "status": "planning"}]
        mock_projects = [{"id": 1, "name": "Project A", "status": "active"}]
        with patch("app.routers.cross_projects.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_my_shots.return_value = mock_shots
            mock_inst.get_my_projects.return_value = mock_projects
            MockClient.return_value = mock_inst

            resp = client_with_auth.get("/cross/projects")

        assert resp.status_code == 200
        html = resp.text
        assert "{'shot_code'" not in html  # dict literal not rendered
        assert "{'name'" not in html       # fallback dict literal not rendered
        assert "as01" in html
