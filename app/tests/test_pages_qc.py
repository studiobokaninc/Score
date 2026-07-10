"""pages_qc テスト — GET /qc/{id} / GET /reference/{id} JWT認証 + CalendarClient モック検証"""
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
from app.routers import pages_qc

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"

_test_app = FastAPI()
_test_app.include_router(pages_qc.router)


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


class TestPagesQc:
    def test_qc_valid_jwt(self, client):
        mock_tasks = [
            CalendarTask(task_id=10, shot_id=1, type="Comp", assignee_id=5, status="ap"),
        ]
        with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = mock_tasks
            MockClient.return_value = mock_inst

            resp = client.get(
                "/qc/1",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        mock_inst.get_tasks.assert_called_once_with(1, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_qc_no_auth(self, client_no_auth):
        resp = client_no_auth.get("/qc/1")
        assert resp.status_code == 401

    def test_reference_valid_jwt(self, client):
        mock_tasks = [
            CalendarTask(task_id=20, shot_id=2, type="Comp", assignee_id=5, status="qc"),
        ]
        with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_tasks.return_value = mock_tasks
            MockClient.return_value = mock_inst

            resp = client.get(
                "/reference/2",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        mock_inst.get_tasks.assert_called_once_with(2, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_reference_no_auth(self, client_no_auth):
        resp = client_no_auth.get("/reference/2")
        assert resp.status_code == 401

    def test_qc_user_not_found_returns_403(self):
        from fastapi import HTTPException

        def _actor_id_not_found():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        _test_app.dependency_overrides[get_actor_id] = _actor_id_not_found
        with TestClient(_test_app) as c:
            resp = c.get("/qc/1", headers={"Authorization": f"Bearer {_make_token()}"})
        _test_app.dependency_overrides.clear()
        assert resp.status_code == 403


from unittest.mock import MagicMock
from app.adapters.dto import CalendarShot


def make_shot(project_id=33):
    return CalendarShot(shot_id=1, project_id=project_id, name="SC001", status="in_progress",
                        shot_code="SC001", seq_code="SQ001")


def test_qc_viewer_project_name(client, monkeypatch):
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: make_shot())
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Ramps")
    resp = client.get("/qc/1", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "Ramps" in resp.text
    assert "Project Alpha" not in resp.text


def test_reference_viewer_project_name(client, monkeypatch):
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: make_shot())
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Ramps")
    resp = client.get("/reference/1", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "Ramps" in resp.text
    assert "Project Alpha" not in resp.text


def test_qc_viewer_shot_none_fallback(client, monkeypatch):
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shot", lambda self, *a, **kw: None)
    resp = client.get("/qc/1", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "Project Alpha" not in resp.text


def test_qc_viewer_asset_list_falls_back_when_shot_detail_forbidden(client, monkeypatch):
    """殿御命 2026-07-09 (cmd_076⑤): get_shot_detail (Calendar /api/me/shots/{id}) は
    明示的 project member 限定で非member は 403 になる (実機確認済み・task assignee でも
    member 登録が無ければ同様)。task_id 未指定で /qc/{shot_id} に来た非member
    director/PM は旧実装だと asset_list が常に空になり「QC 確認対象が何も無い」ページに
    なっていた。get_shot_detail が例外を投げても get_tasks + get_assets_by_task
    (どちらも membership 非依存・実機確認済み) で asset を復元できることを回帰確認する。"""
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Ramps")
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **kw: [])

    task1 = CalendarTask(task_id=10, shot_id=1, type="Comp", assignee_id=5, status="qc")
    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_shot.return_value = make_shot()
        mock_inst.get_tasks.return_value = [task1]
        mock_inst.get_shot_detail.side_effect = Exception("403 Forbidden")
        mock_inst.get_assets_by_task.return_value = [{"id": 900, "task_id": 10, "file_path": "v001.mov"}]
        MockClient.return_value = mock_inst

        # task_id 未指定 (push 通知クリック等・cmd_076⑤ 実機シナリオ)
        resp = client.get("/qc/1", headers={"Authorization": f"Bearer {_make_token()}"})

    assert resp.status_code == 200
    mock_inst.get_assets_by_task.assert_called_once_with(10, actor_user_id=_RESOLVED_ACTOR_ID)
    # fallback は L76 で既に取得済みの tasks を再利用し、get_tasks を再呼出ししないこと
    mock_inst.get_tasks.assert_called_once_with(1, actor_user_id=_RESOLVED_ACTOR_ID)


def test_qc_viewer_shot_unlinked_task_fallback_via_task_id(client, monkeypatch):
    """cmd_091: shot 不在 (SHOT_000・shot 紐付なし task 集約。実機再現: project 80
    "Score 検証" task_id=3255 status=qc) の場合、旧実装は client.get_tasks(id) が
    /api/shots/{id}/tasks 404→[] となり、実際に project 配下に存在する task が
    「このSHOTにはTaskが0件」と誤表示され QC判定ボタンも活性化しなかった (cmd_088 の
    /shot/{id} 修正と同根)。get_task(task_id) で解決した project_id から
    get_tasks_by_project fallback し、tasks が正しく埋まって Approve が活性化することを
    回帰確認する。"""
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Score 検証")
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **kw: [])
    monkeypatch.setattr("app.routers.pages_qc.get_actor_role", lambda actor_id: "director")

    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_shot.return_value = None
        mock_inst.get_tasks.return_value = []
        mock_inst.get_task.return_value = {
            "id": 3255, "project_id": 80, "type": "other", "name": "Score修正29日分",
            "status": "qc", "shotID": None, "shot_id": None,
        }
        mock_inst.get_tasks_by_project.return_value = [
            {"id": 3255, "project_id": 80, "type": "other", "status": "qc", "shotID": None, "shot_id": None},
            {"id": 9999, "project_id": 80, "type": "other", "status": "mk", "shotID": "c01", "shot_id": 5},
        ]
        mock_inst.get_assets_by_task.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get("/qc/0?task_id=3255", headers={"Authorization": f"Bearer {_make_token()}"})

    assert resp.status_code == 200
    mock_inst.get_tasks_by_project.assert_called_once_with(80, actor_user_id=_RESOLVED_ACTOR_ID)
    assert "0 件</strong>" not in resp.text
    assert "approve-btn" in resp.text
    assert "director_retake_input" in resp.text
    # shot 紐付ありの task (9999) は project 全 task から抽出時に除外されること
    assert "9999" not in resp.text


def test_qc_viewer_shot_unlinked_task_fallback_via_project_id_query_param(client, monkeypatch):
    """task_id 未指定 (SHOT_000 の task 選択一覧・project_detail からの導線を想定) でも
    ?project_id= クエリを渡せば同じ fallback で tasks が復元されることを確認する。
    fallback task の status は敢えて judge_target_statuses 外 (wip) にし、『task 未選択
    だが判定待ち task が1件もない』分岐 (= task 選択一覧表示) を狙って踏む。"""
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Score 検証")
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **kw: [])
    monkeypatch.setattr("app.routers.pages_qc.get_actor_role", lambda actor_id: "director")

    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_shot.return_value = None
        mock_inst.get_tasks.return_value = []
        mock_inst.get_tasks_by_project.return_value = [
            {"id": 3255, "project_id": 80, "type": "other", "status": "wip", "shotID": None, "shot_id": None},
        ]
        mock_inst.get_assets_by_task.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get("/qc/0?project_id=80", headers={"Authorization": f"Bearer {_make_token()}"})

    assert resp.status_code == 200
    mock_inst.get_tasks_by_project.assert_called_once_with(80, actor_user_id=_RESOLVED_ACTOR_ID)
    # task_id 未指定 → 「0件」ではなく task 選択一覧が出ること (旧実装は tasks が
    # 常に空のため「0件」誤表示のまま・一覧に辿り着けなかった)
    assert "0 件</strong>" not in resp.text
    assert "どの工程を" in resp.text


def test_qc_viewer_shot_status_passed_to_context(client, monkeypatch):
    """shot は route 内で取得済みなのに TemplateResponse の context に渡されておらず、
    テンプレートの shot.status が常に未定義 (Jinja Undefined) 扱いになり 'SHOT.status = -'
    と誤表示され続けるバグがあった (project名『-』表示バグと同根の『取得したのに渡し
    忘れ』パターン)。shot が実在し tasks が真に 0 件の場合に、実際の status 文字列が
    表示されることを回帰確認する。status は敢えて 'planning' にし (cmd_075 の
    _shot_in_review 判定対象は in_progress/approved のみ)、shot 起因の判定活性化と
    混同せず shot.status 表示そのものだけを検証する。"""
    monkeypatch.setattr("app.routers.pages_qc.get_actor_role", lambda actor_id: "director")
    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_shot.return_value = CalendarShot(
            shot_id=1, project_id=33, name="AS001", status="planning",
            shot_code="AS001", seq_code="SQ001",
        )
        mock_inst.get_tasks.return_value = []
        mock_inst.get_assets_by_task.return_value = []
        MockClient.return_value = mock_inst

        resp = client.get("/qc/1", headers={"Authorization": f"Bearer {_make_token()}"})

    assert resp.status_code == 200
    assert "0 件</strong>" in resp.text
    assert "approve-btn" not in resp.text
    assert 'SHOT.status = <span class="font-bold" translate="no">planning</span>' in resp.text


def test_qc_viewer_asset_list_shot_detail_success_no_fallback_call(client, monkeypatch):
    """get_shot_detail が正常に asset_list を返す (=explicit member) 場合は
    get_tasks 経由の fallback を呼ばない (既存の正常系を壊さない回帰防止)。"""
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda pid, uid, **kw: "Ramps")
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **kw: [])

    with patch("app.routers.pages_qc.get_calendar_client") as MockClient:
        mock_inst = MagicMock()
        mock_inst.get_shot.return_value = make_shot()
        mock_inst.get_tasks.return_value = []
        mock_inst.get_shot_detail.return_value = {
            "asset_list": [{"id": 901, "task_id": 10, "file_path": "v002.mov"}]
        }
        MockClient.return_value = mock_inst

        resp = client.get("/qc/1", headers={"Authorization": f"Bearer {_make_token()}"})

    assert resp.status_code == 200
    mock_inst.get_assets_by_task.assert_not_called()
