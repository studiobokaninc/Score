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


# ===== cmd_076③ auto-membership: director/pm/lead 割当 project の可視化 =====

def test_projects_page_director_not_explicit_member_still_visible(client_fixture, monkeypatch):
    """殿御命 2026-07-09 (cmd_076③): 一般 user (director 含む) が Calendar 側で
    project の director/pm/lead に割当られているが get_my_projects (score 側の
    明示的メンバー一覧) には含まれない場合でも、/projects 一覧に表示されること。
    実機で発生した「Director だが score member 未登録のため project が
    一覧から消え QC ビューアへ到達不能になる」不具合の回帰防止。
    (get_actor_role は pages_misc.get_projects 内で `from app.deps import
    get_actor_role` により毎回呼び出し時に解決されるため、app.deps 側を patch する)"""
    from unittest.mock import patch

    mock_client = MagicMock()
    # score 側の明示的メンバー一覧には project 73 が含まれない
    mock_client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    mock_client.base_url = "http://calendar.test"
    mock_client.m2m_token = "test-m2m-token"
    # 全 project 母集合 (m2m /api/projects 相当) には project 73 も含まれる
    _all_resp = MagicMock()
    _all_resp.json.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]
    _all_resp.raise_for_status.return_value = None

    def _roles_for(pid, **kw):
        if pid == 73:
            return {"director": 53, "pm": 52}
        return {}
    mock_client.get_project_roles.side_effect = _roles_for

    with patch("app.deps.get_actor_role", return_value="user"), \
         patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client), \
         patch("app.routers.pages_misc.httpx.get", return_value=_all_resp), \
         patch("app.adapters.calendar_client._to_calendar_uid", return_value=53):
        resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    assert "Marukome" in resp.text, "Calendar 側 director 割当 project は score member 未登録でも表示されるべき"
    assert "Ramps" in resp.text


def test_projects_page_admin_sees_all_regardless_of_membership_filter(client_fixture, monkeypatch):
    """殿御命 2026-07-09 (cmd_076③): pm/admin は「全 project 閲覧可」の既定方針
    (コード既存コメント) を持つが、旧実装は明示的メンバー一覧での再絞り込みが
    role を問わず適用され、admin であっても非メンバー project が一覧から
    消えていた。admin ロールでは絞り込み自体が skip されることを確認する。"""
    from unittest.mock import patch

    mock_client = MagicMock()
    # 明示的メンバー一覧には project 73 が含まれない (admin 本人はメンバー登録なし)
    mock_client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    mock_client.base_url = "http://calendar.test"
    mock_client.m2m_token = "test-m2m-token"
    _all_resp = MagicMock()
    _all_resp.json.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]
    _all_resp.raise_for_status.return_value = None

    with patch("app.deps.get_actor_role", return_value="admin"), \
         patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client), \
         patch("app.routers.pages_misc.httpx.get", return_value=_all_resp):
        resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    assert "Marukome" in resp.text
    assert "Ramps" in resp.text
