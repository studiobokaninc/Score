"""pages_shot テスト — GET /shot/{id} JWT認証 + CalendarClient モック検証"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.adapters.dto import CalendarTask
from app.deps import get_actor_id
from app.routers import pages_shot

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"

# main.py include_router は ashigaru3(subtask_445n) が担当。テスト用アプリを個別作成。
_test_app = FastAPI()
_test_app.include_router(pages_shot.router)


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _mock_get_actor_id():
    return _RESOLVED_ACTOR_ID


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    _test_app.dependency_overrides[get_actor_id] = _mock_get_actor_id
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


@pytest.fixture()
def client_no_auth():
    with TestClient(_test_app) as c:
        yield c


class TestPagesShotDetail:
    def test_shot_valid_jwt(self, client):
        mock_tasks = [
            CalendarTask(task_id=10, shot_id=1, type="Animation", assignee_id=5, status="ap"),
            CalendarTask(task_id=11, shot_id=1, type="Lighting", assignee_id=6, status="qc"),
        ]
        with patch("app.routers.pages_shot.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = mock_tasks
            mock_inst.get_shot.return_value = None
            MockClient.return_value = mock_inst

            resp = client.get(
                "/shot/1",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        mock_inst.get_tasks.assert_called_once_with(1, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_shot_no_auth(self, client_no_auth):
        resp = client_no_auth.get("/shot/1")
        assert resp.status_code == 401

    def test_shot_user_not_found_returns_403(self):
        from fastapi import HTTPException

        def _actor_id_not_found():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        _test_app.dependency_overrides[get_actor_id] = _actor_id_not_found
        with TestClient(_test_app) as c:
            resp = c.get("/shot/1", headers={"Authorization": f"Bearer {_make_token()}"})
        _test_app.dependency_overrides.clear()
        assert resp.status_code == 403


from unittest.mock import MagicMock
from app.adapters.dto import CalendarShot


def make_shot(project_id=33, shot_code="SC001"):
    return CalendarShot(shot_id=1, project_id=project_id, name=shot_code, status="in_progress",
                        shot_code=shot_code, seq_code="SQ001")


def test_shot_detail_project_name(client, monkeypatch):
    """project_name context が 'Ramps' で渡されること"""
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: make_shot())
    monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda pid, uid, **kw: "Ramps")
    resp = client.get("/shot/1", headers={"Authorization": f"Bearer {_make_token()}"})
    assert resp.status_code == 200
    assert "Ramps" in resp.text
    assert "Project Alpha" not in resp.text


def test_shot_detail_project_members_includes_auto_membership_director(client, monkeypatch):
    """殿御命 2026-07-09 (cmd_076③): GET /shot/{id} (project_detail.html からの通常導線・
    isolated_task=False) は project_members を一切 context に渡していなかった不具合の
    回帰防止。director が明示的 team member/task assignee でなくても
    resolve_project_members 経由で mention 一覧に現れることを確認する。"""
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: make_shot(project_id=73))
    monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_project_roles", lambda self, *a, **kw: {"director": 29, "pm": 31})
    monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [
        {"id": 29, "name": "Yamada"}, {"id": 31, "name": "Tanaka"},
    ])
    resp = client.get("/shot/1", headers={"Authorization": f"Bearer {_make_token()}"})
    assert resp.status_code == 200
    assert "project 担当者 0 件" not in resp.text
    assert "Yamada" in resp.text
    assert "Tanaka" in resp.text


def test_shot_detail_project_name_none_shot(client, monkeypatch):
    """get_shot が None の場合 project_name = '-'"""
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: None)
    resp = client.get("/shot/1", headers={"Authorization": f"Bearer {_make_token()}"})
    assert resp.status_code == 200
    assert "Project Alpha" not in resp.text


def test_shot_detail_project_name_connect_error(client, monkeypatch):
    """resolve_project_name が '-' を返す場合 fallback が出ること"""
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: make_shot())
    monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda pid, uid, **kw: "-")
    resp = client.get("/shot/1", headers={"Authorization": f"Bearer {_make_token()}"})
    assert resp.status_code == 200
    assert "Project Alpha" not in resp.text
