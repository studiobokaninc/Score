"""cmd_352: アセット履歴の「by user_36」を実ユーザー名で出す。

原因: shot_detail.html のアセット履歴が、テンプレ内に固定で書いた名簿(_names)だけを引き、
名簿に無い ID は user_<番号> に落ちていた。/shot 経由は user_name_map も渡していなかった。
修正: Calendar の get_users 由来の user_name_map を先に引く。引けぬ時だけ従来表示。
"""
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_shot

_SECRET = "test_secret_key_32bytes_minimum!"
_test_app = FastAPI()
_test_app.include_router(pages_shot.router)


def _headers() -> dict:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    tok = jwt.encode({"sub": "sato@studio.jp", "exp": exp}, _SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    _test_app.dependency_overrides[get_actor_id] = lambda: "42"
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


def _asset(created_by):
    return {"id": 1, "shot_id": 1, "task_id": 7, "version": "v001", "file_path": "a/shot_1_task_7_v001_x.mov",
            "created_by": created_by, "created_at": "2026-10-22T10:00:00", "status": "submitted"}


def _setup(monkeypatch, users, created_by):
    from app.adapters import calendar_client as cc
    from app.adapters.dto import CalendarShot

    shot = CalendarShot(shot_id=1, project_id=33, name="SC001", status="in_progress",
                        shot_code="SC001", seq_code="SQ001")
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: shot)
    monkeypatch.setattr(cc.CalendarClient, "get_shot_detail",
                        lambda self, *a, **kw: {"asset_list": [_asset(created_by)]})
    monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: users)
    monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda pid, uid, **kw: "P")


def test_asset_history_shows_real_name_not_user_id(client, monkeypatch):
    _setup(monkeypatch, [{"id": 36, "name": "Hanako Real"}], 36)
    resp = client.get("/shot/1", headers=_headers())
    assert resp.status_code == 200
    assert "Hanako Real" in resp.text
    assert "user_36" not in resp.text


def test_asset_history_falls_back_to_user_id_when_name_unknown(client, monkeypatch):
    _setup(monkeypatch, [], 36)
    resp = client.get("/shot/1", headers=_headers())
    assert resp.status_code == 200
    assert "user_36" in resp.text


def test_asset_history_survives_get_users_failure(client, monkeypatch):
    from app.adapters import calendar_client as cc
    _setup(monkeypatch, [], 36)

    def _boom(self, *a, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(cc.CalendarClient, "get_users", _boom)
    resp = client.get("/shot/1", headers=_headers())
    assert resp.status_code == 200
    assert "user_36" in resp.text
