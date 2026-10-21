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


def _stub_calendar(monkeypatch, *, my_detail=None, project=None, project_raises=False):
    """cmd_346: Calendar は丸ごと差し替え(実 Calendar へは出ぬ。load_dotenv(override=True) ゆえ環境変数隔離に頼らぬ)。"""
    monkeypatch.setattr(cc.CalendarClient, "get_my_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shots", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_project_roles", lambda self, *a, **kw: {})
    monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_my_project_detail", lambda self, *a, **kw: my_detail or {})

    def _get_project(self, *a, **kw):
        if project_raises:
            raise RuntimeError("calendar down")
        return project or {}

    monkeypatch.setattr(cc.CalendarClient, "get_project", _get_project)


def _get_detail():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    try:
        with TestClient(_test_app) as c:
            return c.get("/project_detail/115")
    finally:
        _test_app.dependency_overrides.clear()


def test_overview_shows_description_from_get_project_keeping_newlines(monkeypatch):
    _stub_calendar(monkeypatch, project={"description": "一行目\n二行目"})
    resp = _get_detail()
    assert resp.status_code == 200
    assert "プロジェクト概要" in resp.text
    assert "一行目\n二行目" in resp.text  # 改行は保たれ(pre-wrap)、<br> に置換もされぬ
    assert "whitespace-pre-wrap" in resp.text


def test_overview_prefers_my_project_detail_description(monkeypatch):
    _stub_calendar(monkeypatch, my_detail={"description": "from-my-detail"}, project={"description": "from-get-project"})
    resp = _get_detail()
    assert "from-my-detail" in resp.text
    assert "from-get-project" not in resp.text


def test_overview_escapes_html(monkeypatch):
    _stub_calendar(monkeypatch, project={"description": "<script>alert(1)</script><b>x</b>"})
    resp = _get_detail()
    assert resp.status_code == 200
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in resp.text
    assert "<b>x</b>" not in resp.text


def test_overview_unset_when_description_empty_or_null(monkeypatch):
    for desc in ("", "   \n ", None):
        _stub_calendar(monkeypatch, project={"description": desc})
        resp = _get_detail()
        assert resp.status_code == 200
        assert "プロジェクト概要" in resp.text
        assert 'id="project-overview-text">未設定<' in resp.text


def test_overview_does_not_break_when_calendar_fails(monkeypatch):
    _stub_calendar(monkeypatch, project_raises=True)
    resp = _get_detail()
    assert resp.status_code == 200
    assert 'id="project-overview-text">未設定<' in resp.text


def test_overview_unset_when_description_not_a_string(monkeypatch):
    _stub_calendar(monkeypatch, project={"description": 123})
    resp = _get_detail()
    assert resp.status_code == 200
    assert 'id="project-overview-text">未設定<' in resp.text
