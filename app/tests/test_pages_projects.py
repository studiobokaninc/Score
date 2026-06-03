import os
from unittest.mock import MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_misc

_SECRET = "test_secret_key_32bytes_minimum!"
_test_app = FastAPI()
_test_app.include_router(pages_misc.router)


def _mock_get_actor_id():
    return "test-actor"


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client_fixture():
    _test_app.dependency_overrides[get_actor_id] = _mock_get_actor_id
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


def test_projects_page_ok(client_fixture, monkeypatch):
    """GET /projects → 200 + projects 一覧描画"""
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_my_projects",
                        lambda self, **kw: [{"id": 33, "name": "Ramps", "status": "active"}])
    resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "Ramps" in resp.text


def test_projects_page_connect_error(client_fixture):
    """GET /projects ConnectError → 空list → 200"""
    import httpx
    from unittest.mock import MagicMock, patch
    mock_client = MagicMock()
    mock_client.get_my_projects.side_effect = httpx.ConnectError("")
    with patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client):
        resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "プロジェクトはありません" in resp.text
