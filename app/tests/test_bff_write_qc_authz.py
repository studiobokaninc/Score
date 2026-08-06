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
        """cmd_167: role は系B(get_project_roles)で解決される。project_id/role とも
        未 mock (デフォルト) のため actor はどの案件役職にも一致せず fail-closed で 'user'
        に解決される (get_actor_role の直接 monkeypatch は cmd_167 以降不要)。"""
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
        """cmd_167: 系B(get_project_roles)で actor_id(=42) が該当案件の director と
        一致する場合に許可されることを検証 (旧: get_actor_role直接monkeypatch)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_my_dm_threads.return_value = []
            mock_inst.patch_task.return_value = {}
            mock_inst.get_task.return_value = {"project_id": 33}
            mock_inst.get_project_roles.return_value = {"director": int(_RESOLVED_ACTOR_ID)}
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
    mock_inst.get_task.return_value = {"project_id": 33}
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
        """cmd_167: role は系B(get_project_roles)で解決される。project_id/role未 mock
        (デフォルト) のため actor はどの案件役職にも一致せず fail-closed で 'user' に
        解決される (get_actor_role の直接 monkeypatch は cmd_167 以降不要)。"""
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
        """cmd_167: 系B(get_project_roles)で actor_id(=42) が該当案件の director と
        一致する場合に許可されることを検証。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            _mock_retake_calendar(mock_inst)
            mock_inst.get_project_roles.return_value = {"director": int(_RESOLVED_ACTOR_ID)}
            MockClient.return_value = mock_inst
            resp = client.post(
                "/api/bff/retakes",
                json={"shot_id": 5, "task_id": 10, "direction": "色味調整"},
                headers=_auth_headers(),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "qc_fb"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_qc_delegated_user_allowed_200(self, client, monkeypatch):
        """actor の案件役職(get_project_roles: pm=3/director=7/lead=9)は42と不一致=非昇格
        ('user')のまま、is_qc_delegated による委任のみで許可されることを検証。"""
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
        """URL直叩きでの status=ap 直接書込 (qc/approve 迂回) は無権限アクター拒否
        (cmd_167: role は系B・project_id/role とも未 mock のため fail-closed で 'user')"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "ap"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_privileged_status_director_allowed_200(self, client, monkeypatch):
        """cmd_167: 系B(get_project_roles)で actor_id(=42) が該当案件の director と
        一致する場合に許可されることを検証。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "qc", "project_id": 33}
            mock_inst.get_project_roles.return_value = {"director": int(_RESOLVED_ACTOR_ID)}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "ap"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "ap"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_privileged_status_qc_delegated_allowed_200(self, client, monkeypatch):
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
        """任意項目③(限定実装): 完了済(ap)からの逆行(wip等)は判定権限者以外拒否
        (cmd_167: role は系B・project_id/role とも未 mock のため fail-closed で 'user')"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "ap"}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wip"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_regression_from_completed_status_allowed_for_director(self, client, monkeypatch):
        """cmd_167: 系B(get_project_roles)で actor_id(=42) が該当案件の director と
        一致する場合に許可されることを検証。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "ap", "project_id": 33}
            mock_inst.get_project_roles.return_value = {"director": int(_RESOLVED_ACTOR_ID)}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wip"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "wip"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_deliver_status_user_role_allowed_200(self, client, monkeypatch):
        """cmd_162① 回帰防止: 確定稿ではDELIVERの変更者はUser。cmd_141がCOMPLETED_STATUSES
        ({"ap","client_ap","deliver"}) を丸ごと判定権限バケットへ流用した際にDELIVERが
        巻き添えで403化していた (旧コードでは本テストはrole='user'のまま403になっていた)。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "qc"}
            mock_inst.patch_task.return_value = {"ok": True}
            mock_inst.get_project_roles.return_value = {}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "deliver"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "deliver"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_client_ap_status_unauthorized_user_still_rejected_403(self, client, monkeypatch):
        """cmd_162①の限界確認: DELIVER以外(client_ap)の権限は緩めていないこと。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "client_ap"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_qc_fb_status_unauthorized_user_still_rejected_403(self, client, monkeypatch):
        """cmd_162①の限界確認: DELIVER以外(qc_fb)の権限は緩めていないこと。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "qc_fb"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()


# ─── cmd_164② 値ごとの要求役職(確定稿突合の是正) ────────────────────────

class TestPatchTaskStatusSpecificRoles:
    """確定稿: WT/MK=PM、QC_FB/AP=Dir・PM、CLIENT_AP/COMPLETED=PM。
    単一バケット(旧PRIVILEGED_TASK_STATUSES)では表現できなかった
    値ごとの要求役職の粒度(STATUS_REQUIRED_ROLES)を検証する。"""

    def _mock_client(self, actor_uid: int, role: str, current_status: str = "wip"):
        mock_inst = MagicMock()
        mock_inst.get_task.return_value = {"status": current_status, "project_id": 33}
        mock_inst.patch_task.return_value = {"ok": True}
        mock_inst.get_project_roles.return_value = {role: actor_uid} if role != "admin" else {}
        return mock_inst

    def test_client_ap_rejects_director_pm_only_per_confirmed_spec(self, client, monkeypatch):
        """★是正の核心: 確定稿ではCLIENT_AP=PMのみ。旧実装はdirectorも通していたが
        (cmd_158突合で発見済の乖離)、cmd_164②是正後はdirectorが403で拒否される
        こと(role='director'では通らない・pmのみ通ること)を検証する。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "director")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "client_ap"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_client_ap_allowed_for_pm(self, client, monkeypatch):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "pm")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "client_ap"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "client_ap"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_completed_allowed_for_pm(self, client, monkeypatch):
        """cmd_164①②: 新設COMPLETEDはPM限定で書込可。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "pm")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "completed"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "completed"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_completed_rejects_director(self, client, monkeypatch):
        """①検証: PM以外(Director)がCOMPLETEDへ直接書込を試みると403で弾かれること。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "director")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "completed"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_completed_rejects_plain_user(self, client, monkeypatch):
        """①検証: PM以外(User=無役職)がCOMPLETEDへ直接書込を試みると403で弾かれること。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "user")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "completed"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_wt_rejects_user_now_gated_per_confirmed_spec(self, client, monkeypatch):
        """★是正の核心: 確定稿ではWT=PMのみ。旧実装は無条件で誰でも書込可だったが
        (cmd_158突合で発見済の乖離)、cmd_164②是正後はPM以外が403で拒否されること
        を検証する。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "user")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "wt"}, headers=_auth_headers())
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_mk_allowed_for_pm(self, client, monkeypatch):
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = self._mock_client(int(_RESOLVED_ACTOR_ID), "pm")
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "mk"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "mk"}, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_admin_always_passes_wt_mk_client_ap_completed(self, client, monkeypatch):
        """④admin据え置き: 新設・是正した全ゲート(wt/mk/client_ap/completed)で
        adminは引き続き通ること。"""
        for status in ("wt", "mk", "client_ap", "completed"):
            with patch("app.routers.bff_write.get_calendar_client") as MockClient, \
                 patch("app.deps.get_actor_role", return_value="admin"):
                mock_inst = MagicMock()
                mock_inst.get_task.return_value = {"status": "wip", "project_id": 33}
                mock_inst.patch_task.return_value = {"ok": True}
                MockClient.return_value = mock_inst
                resp = client.patch("/api/bff/tasks/10", json={"status": status}, headers=_auth_headers())
            assert resp.status_code == 200, f"admin should pass for status={status}"

    def test_omit_remains_ungated(self, client, monkeypatch):
        """殿ご裁可(2026-08-05・案B): OMITの変更者は確定稿に定めが無いため、
        cmd_164②の是正対象外(ゲート新設せず現状=誰でも書込可のまま)。"""
        _deny_delegation(monkeypatch)
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"status": "wip"}
            mock_inst.patch_task.return_value = {"ok": True}
            MockClient.return_value = mock_inst
            resp = client.patch("/api/bff/tasks/10", json={"status": "omit"}, headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "omit"}, actor_user_id=_RESOLVED_ACTOR_ID)
