"""E2E story tests — 6 scenarios covering key user journeys"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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


def test_e2e_login(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_e2e_dashboard(client):
    from app.deps import get_actor_id

    app.dependency_overrides[get_actor_id] = lambda: "1"
    with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = MagicMock(user_id=1, name="Sato", email="sato@studio.jp")
        mock_inst.get_shots.return_value = []
        mock_inst.get_tasks.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get("/dashboard")
    app.dependency_overrides.pop(get_actor_id, None)

    assert resp.status_code == 200


def test_e2e_shot_detail(client):
    from app.deps import get_actor_id

    def _mock_actor_id():
        return "1"

    app.dependency_overrides[get_actor_id] = _mock_actor_id
    with patch("app.routers.pages_shot.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_tasks.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get(
            "/shot/1",
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
    app.dependency_overrides.pop(get_actor_id, None)

    assert resp.status_code == 200


def test_e2e_qc_viewer(client):
    from app.deps import get_actor_id

    def _mock_actor_id():
        return "1"

    app.dependency_overrides[get_actor_id] = _mock_actor_id
    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_tasks.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get(
            "/qc/1",
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
    app.dependency_overrides.pop(get_actor_id, None)

    assert resp.status_code == 200


def test_e2e_cross_projects(client):
    from app.deps import get_actor_id

    app.dependency_overrides[get_actor_id] = lambda: "1"
    with patch("app.routers.cross_projects.get_calendar_client") as mock_factory:
        mock_inst = MagicMock()
        mock_inst.get_my_projects.return_value = []
        mock_inst.get_my_shots.return_value = []
        mock_factory.return_value = mock_inst

        resp = client.get("/cross/projects")
    app.dependency_overrides.pop(get_actor_id, None)

    assert resp.status_code == 200


def test_e2e_goodbye(client):
    from app.deps import get_actor_id

    app.dependency_overrides[get_actor_id] = lambda: "1"
    with patch("app.routers.pages_misc.get_calendar_client") as mock_factory:
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = MagicMock(user_id=1, name="Sato", email="sato@studio.jp")
        mock_inst.get_my_projects.return_value = []
        mock_factory.return_value = mock_inst

        resp = client.get("/goodbye")
    app.dependency_overrides.pop(get_actor_id, None)

    assert resp.status_code == 200
