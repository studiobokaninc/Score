import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from fastapi.testclient import TestClient

from app.deps import get_actor_id
from app.main import app

_client = TestClient(app, raise_server_exceptions=False)


def test_sidemenu_no_dead_links():
    """/dashboard HTML に /cross/projects・/exit-report が存在せず /projects・/exit_report が存在する"""
    from app.adapters.dto import CalendarUser

    app.dependency_overrides[get_actor_id] = lambda: "1"
    try:
        with patch("app.routers.pages_dashboard.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = CalendarUser(
                user_id=1, email="test@studio.jp", role="Compositor", name="Test"
            )
            mock_inst.get_shots.return_value = []
            mock_inst.get_my_projects.return_value = []
            MockClient.return_value = mock_inst

            resp = _client.get("/dashboard")

        html = resp.text
        assert resp.status_code == 200
        assert "/cross/projects" not in html, "旧リンク /cross/projects が残存"
        assert 'href="/projects"' in html
        assert "/exit-report" not in html, "typo /exit-report が残存"
        assert "/exit_report" in html
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client_fixture():
    from app.adapters.dto import CalendarUser
    app.dependency_overrides[get_actor_id] = lambda: "1"
    try:
        with patch("app.routers.pages_dashboard.get_calendar_client") as MockDash:
            mock_inst = MagicMock()
            mock_inst.get_me.return_value = CalendarUser(
                user_id=1, email="test@studio.jp", role="Compositor", name="Test"
            )
            mock_inst.get_shots.return_value = []
            mock_inst.get_my_projects.return_value = []
            MockDash.return_value = mock_inst
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c
    finally:
        app.dependency_overrides.clear()


def test_dashboard_renders_with_sidemenu(client_fixture):
    resp = client_fixture.get("/dashboard", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert 'href="/projects"' in resp.text


def test_shot_detail_renders_with_sidemenu(client_fixture, monkeypatch):
    from app.adapters import calendar_client as cc
    from app.helpers import project_resolver as pr
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: None)
    resp = client_fixture.get("/shot/1", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert 'href="/projects"' in resp.text
