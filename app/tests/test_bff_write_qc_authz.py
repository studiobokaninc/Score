"""cmd_141 (2026-07-23・殿御命): Score書込API server側 role/権限検査ゲートの単体テスト。

bff_write.py には従来 get_actor_id (認証) はあっても get_actor_role / is_qc_delegated
(認可) が一度も呼ばれておらず、URL直叩きで無権限アクターが承認・差戻し・状態改変を
実行できた (cmd_118/119/144で確定済の欠陥)。本ファイルは新設した _require_qc_judge_authority
ゲートについて、対象3EP (POST /api/bff/qc/approve, POST /api/bff/retakes,
PATCH /api/bff/tasks/{id}) それぞれで最低限の3パターンを検証する:
  (a) 無権限アクター(role='user'・非委任) の書込拒否 (403)
  (b) 正権限アクター(director/pm/admin) の書込許可 (200)
  (c) is_qc_delegated による委任者の書込許可 (200) — 既存委任機能を壊していないことの証跡
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id, get_db
from app.main import app
from app.routers import bff_write as _bff_write_module

app.include_router(_bff_write_module.router)

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _db_override():
    yield MagicMock()


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_actor_id] = lambda: _RESOLVED_ACTOR_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


def _deny_delegation(monkeypatch):
    monkeypatch.setattr("app.routers.bff_write.is_qc_delegated", lambda *a, **k: False)


def _grant_delegation(monkeypatch):
    monkeypatch.setattr(
        "app.routers.bff_write.is_qc_delegated",
        lambda actor_id, task_id=None, shot_id=None: True,
    )


# ─── POST /api/bff/qc/approve ────────────────────────────────────────────────

class TestQcApproveAuthz:
    def test_unauthorized_user_role_rejected_403(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_my_dm_threads.return_value = []
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 5, "task_id": 10, "comment": ""},
                headers=_auth_headers(),
            )
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_director_role_allowed_200(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_my_dm_threads.return_value = []
            mock_inst.patch_task.return_value = {}
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 5, "task_id": 10, "comment": ""},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "ap"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_qc_delegated_user_allowed_200(self, client, monkeypatch):
        """★最重要: is_qc_delegated による委任者Approveは新gateでも維持されねばならない"""
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _grant_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_my_dm_threads.return_value = []
            mock_inst.patch_task.return_value = {}
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/qc/approve",
                json={"shot_id": 5, "task_id": 10, "comment": ""},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "ap"}, actor_user_id=_RESOLVED_ACTOR_ID)


# ─── POST /api/bff/retakes ───────────────────────────────────────────────────

def _mock_retake_calendar(mock_inst):
    mock_inst.get_shot_detail.return_value = {"shotID": "SH010", "seqID": "SEQ01", "project_id": 33}
    mock_inst.get_tasks.return_value = []
    mock_inst.get_project.return_value = {"name": "Score検証"}
    mock_inst.get_project_roles.return_value = {"pm": 3, "director": 7, "lead": 9}
    mock_inst.get_me.return_value = None
    mock_inst.post_dm_thread.return_value = {"thread_id": 900}
    mock_inst.post_dm.return_value = {}
    mock_inst.post_retakes.return_value = {"ok": True}
    mock_inst.patch_task.return_value = {}


class TestRetakesAuthz:
    def test_unauthorized_user_role_rejected_403(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 10, "direction": "色味調整"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()
        mock_inst.post_retakes.assert_not_called()

    def test_director_role_allowed_200(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            _mock_retake_calendar(mock_inst)
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 10, "direction": "色味調整"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "qc_fb"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_qc_delegated_user_allowed_200(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _grant_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            _mock_retake_calendar(mock_inst)
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 10, "direction": "色味調整"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "qc_fb"}, actor_user_id=_RESOLVED_ACTOR_ID)


# ─── PATCH /api/bff/tasks/{id} ───────────────────────────────────────────────

class TestPatchTaskAuthz:
    def test_privileged_status_unauthorized_rejected_403(self, client, monkeypatch):
        """URL直叩きでの status=ap 直接書込 (qc/approve 迂回) は無権限アクター拒否"""
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "ap"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_privileged_status_director_allowed_200(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "qc"}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "ap"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "ap"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_privileged_status_qc_delegated_allowed_200(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _grant_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "qc"}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "qc_fb"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "qc_fb"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_non_privileged_status_any_actor_allowed_200(self, client, monkeypatch):
        """回帰防止: 通常の自己管理系遷移 (wip 等) は従来通り role 制限なしで通ること
        (artist の自己進捗更新を壊さない — shot_detail.html changeTaskStatus() 相当)"""
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "mk"}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wip"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "wip"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_regression_from_completed_status_blocked_for_non_judge(self, client, monkeypatch):
        """任意項目③(限定実装): 完了済(ap)からの逆行(wip等)は判定権限者以外拒否"""
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "user")
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "ap"}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wip"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_regression_from_completed_status_allowed_for_director(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.bff_write.get_actor_role", lambda actor_id: "director")
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "ap"}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wip"}, headers=_auth_headers())
        assert resp.status_code == 200
