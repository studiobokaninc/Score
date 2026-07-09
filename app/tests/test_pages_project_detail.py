import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
import app.adapters.calendar_client as cc
from app.deps import get_actor_id
from app.routers import pages_project_detail

_test_app = FastAPI()
_test_app.include_router(pages_project_detail.router)


def test_project_detail_ok():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/project_detail/1")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "Project" in resp.text


def test_project_detail_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/project_detail/1")
    assert resp.status_code == 401


def test_project_detail_members_include_director_pm_without_tasks(monkeypatch):
    """cmd_076④ regression: director/PM が project の task に一切 assign されて
    いなくても、参加メンバー一覧(members)に表示されることを確認する
    (旧実装は task の assigned_to のみを member としており、ここが漏れていた)。"""
    monkeypatch.setattr(cc.CalendarClient, "get_my_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shots", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_project_roles", lambda self, *a, **kw: {"director": 28, "pm": 31})
    monkeypatch.setattr(
        cc.CalendarClient, "get_users",
        lambda self, *a, **kw: [
            {"id": 28, "name": "Director Yamada"},
            {"id": 31, "name": "PM Suzuki"},
        ],
    )
    assert not hasattr(cc.CalendarClient, "get_team_members")

    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/project_detail/1")
    _test_app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "担当者なし" not in resp.text
    assert "Director Yamada" in resp.text
    assert "PM Suzuki" in resp.text
