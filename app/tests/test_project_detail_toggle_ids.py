"""project_detail の SEQ > SHOT アコーディオン開閉 id 重複の回帰テスト — subtask_359a (cmd_359)

不具合: 各工程(SEQ)の「📋 (SHOT 紐付けなし)」箱を開くと、先頭工程(00_prep)の箱が
開いてしまう。原因は shot 側の開閉 id が `{shot の通し番号}-{shot_code}` だけで作られ、
通し番号が SEQ ごとに 0 から振り直されるため、複数 SEQ の先頭に同名の箱が並ぶと
同じ id が重なり getElementById が最初の要素を返していた。
"""
import os
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.main import app

_SECRET = "test_secret_key_32bytes_minimum!"


def _auth_headers() -> dict:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"sub": "sato@studio.jp", "exp": exp}, _SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_actor_id] = lambda: "42"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _task(tid, seq, shot):
    return {"id": tid, "name": f"t{tid}", "type": "other", "status": "wip",
            "shotID": shot, "seqID": seq, "assigned_to": None,
            "status_color": None, "status_label": None, "status_category": None}


def _render(client, monkeypatch, tasks):
    import app.adapters.calendar_client as cc

    monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_name", lambda pid, uid, **kw: "Score検証")
    monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_members", lambda *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shots", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_my_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_my_project_detail", lambda self, *a, **kw: {})
    monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: tasks)
    resp = client.get("/project_detail/126", headers=_auth_headers())
    assert resp.status_code == 200
    return resp.text


class TestProjectDetailToggleIdsUnique:
    def test_shotless_buckets_in_each_seq_have_distinct_ids(self, client, monkeypatch):
        tasks = [
            _task(1, "00_prep", None), _task(2, "01_lookdev", None),
            _task(3, "02_master", None), _task(4, "99_delivery", None),
            _task(5, "01_lookdev", "e0400"),
        ]
        html = _render(client, monkeypatch, tasks)

        panel_ids = re.findall(r'id="(shot-tasks-[^"]+)"', html)
        arrow_ids = re.findall(r'id="(arrow-shot-[^"]+)"', html)
        # 箱 5 個(紐付けなし 4 + e0400)・全て別 id
        assert len(panel_ids) == 5
        assert len(set(panel_ids)) == len(panel_ids)
        assert len(set(arrow_ids)) == len(arrow_ids)

    def test_each_button_targets_its_own_panel(self, client, monkeypatch):
        tasks = [_task(1, "00_prep", None), _task(2, "01_lookdev", None)]
        html = _render(client, monkeypatch, tasks)

        keys = re.findall(r"onclick=\"toggleShot\('([^']+)'\)", html)
        assert len(keys) == 2 and len(set(keys)) == 2
        for k in keys:
            assert html.count(f'id="shot-tasks-{k}"') == 1
            assert html.count(f'id="arrow-shot-{k}"') == 1
