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
    """GET /projects → 200 + projects 一覧描画
    殿御命 2026-07-09 (cmd_076⑤): get_actor_role 未 mock だと本テスト環境から到達可能な
    実 Calendar backend (192.168.44.253:8001) に実通信してしまい、role 解決結果が
    実サーバの応答次第という隠れた非決定性があった (client.get_projects() 追加前は
    m2m_token 401 で必ず fallback するという「旧バグ頼みの疑似分離」が偶然成立して
    いただけ。真因修正でこの隠れた依存が露呈したため、get_actor_role を明示 mock して
    テストを実ネットワークから独立させる)。"""
    from unittest.mock import patch
    from app.adapters import calendar_client as cc
    monkeypatch.setattr(cc.CalendarClient, "get_my_projects",
                        lambda self, **kw: [{"id": 33, "name": "Ramps", "status": "active"}])
    with patch("app.deps.get_actor_role", return_value="user"):
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
    get_actor_role` により毎回呼び出し時に解決されるため、app.deps 側を patch する)
    殿御命 2026-07-09 (cmd_076⑤): 全 project 母集合の取得手段を旧
    _fetch_all_projects (m2m_token 直叩き・raw httpx) から client.get_projects()
    (admin JWT 経由・正規 auth) に差替えたため、mock も httpx.get patch から
    client.get_projects の mock に変更 (m2m_token 直叩きは本番 401 の真因だった)。"""
    from unittest.mock import patch

    mock_client = MagicMock()
    # score 側の明示的メンバー一覧には project 73 が含まれない
    mock_client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    # 全 project 母集合 (/api/projects 相当) には project 73 も含まれる
    mock_client.get_projects.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]

    def _roles_for(pid, **kw):
        if pid == 73:
            return {"director": 53, "pm": 52}
        return {}
    mock_client.get_project_roles.side_effect = _roles_for

    with patch("app.deps.get_actor_role", return_value="user"), \
         patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client), \
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
    mock_client.get_projects.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]

    with patch("app.deps.get_actor_role", return_value="admin"), \
         patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client):
        resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    assert "Marukome" in resp.text
    assert "Ramps" in resp.text


def test_projects_page_admin_get_projects_fails_falls_back_to_resolve_visible(client_fixture, monkeypatch):
    """殿御命 2026-07-09 (cmd_076⑤): client.get_projects() が例外 (401 等) を
    投げても、admin/pm は resolve_visible_projects 経由の fallback で
    自身の明示メンバー + auto-membership 分だけは表示できること (完全な空白よりまし)。"""
    from unittest.mock import patch

    mock_client = MagicMock()
    mock_client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    mock_client.get_projects.side_effect = Exception("401 Unauthorized")

    with patch("app.deps.get_actor_role", return_value="admin"), \
         patch("app.routers.pages_misc.get_calendar_client", return_value=mock_client):
        resp = client_fixture.get("/projects", headers={"Authorization": "Bearer test-token"})

    assert resp.status_code == 200
    assert "Ramps" in resp.text
