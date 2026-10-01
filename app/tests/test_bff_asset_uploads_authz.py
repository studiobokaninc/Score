"""#276是正 — GET /api/bff/asset_uploads/{id}/file
・/download の認可検証。_get_row_or_404 が shot_id/project_id/uploaded_by いずれも
actor_id と照合していなかった穴(score-san-ken-tougou-shuusei-keikakusho-2026-09-25
1節)に対し、Calendar側 GET /api/me/shots/{id} (get_shot_detail) の project member
限定応答に乗せた是正を検証する。

#283是正: shot_id==0(shotに紐付かない行)のうちtask_idがある行は、
task_id→Calendar get_task→実shot_id→既存の_actor_can_access_shot(get_shot_detail・
member限定)へ橋渡しする。

QC283B-1是正(2026-10-01): task_idが無い行・taskがshot非紐づきの行も、project_idが
あれば#279e(_actor_can_read_attendance)と同じ道具(get_my_projects+
get_actor_project_role)に乗せた_actor_can_access_projectで判ずる。project_idも
task_idも無い行のみが計画書の指図どおり意図的に未解決のまま残る(残る穴)。"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id, get_db
from app.main import app
from app.models import UploadedAsset
from app.routers import bff_asset_uploads as _mod

app.include_router(_mod.router)

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


def _make_row(
    shot_id: int,
    stored_filename: str = "1_a.txt",
    task_id: Optional[int] = 10,
    project_id: Optional[int] = 33,
) -> UploadedAsset:
    row = UploadedAsset(
        id=1,
        shot_id=shot_id,
        task_id=task_id,
        project_id=project_id,
        filename="a.txt",
        stored_filename=stored_filename,
        content_type="text/plain",
        size_bytes=3,
        uploaded_by=_RESOLVED_ACTOR_ID,
    )
    return row


@pytest.fixture()
def client(tmp_path, monkeypatch):
    row_holder = {"row": _make_row(shot_id=5)}

    def _db_override():
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row_holder["row"]
        yield db

    # resolve_path: 実ファイルを用意し FileResponse がそのまま送れるようにする
    real_file = tmp_path / "1_a.txt"
    real_file.write_bytes(b"abc")
    monkeypatch.setattr(_mod, "resolve_path", lambda stored_filename: real_file)

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_actor_id] = lambda: _RESOLVED_ACTOR_ID
    with TestClient(app) as c:
        c._row_holder = row_holder
        yield c
    app.dependency_overrides.clear()


class TestAssetUploadFileAuthz:
    def test_project_member_can_view_200(self, client):
        """関係者(project member)は従来どおり閲覧できる。"""
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {"id": 5, "project_id": 33}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_called_once_with(5, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_non_member_rejected_403(self, client):
        """有効な資格を持つが当該案件の関係者でない者は403で弾かれる
        (get_shot_detail が非member時に空dict/例外を返すパターンに追随)。"""
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 403

    def test_non_member_rejected_403_on_exception(self, client):
        """get_shot_detail が例外(403相当のHTTPStatusError等)を投げた場合も
        fail-closed で拒否する。"""
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.side_effect = RuntimeError("403 from calendar")
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/download", headers=_auth_headers())
        assert resp.status_code == 403

    def test_download_project_member_allowed_200(self, client):
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_shot_detail.return_value = {"id": 5, "project_id": 33}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/download", headers=_auth_headers())
        assert resp.status_code == 200

    def test_shot_id_zero_no_task_id_but_project_member_allowed_200(self, client):
        """QC283B-1是正: shot_id==0・task_id無しでもproject_idがあれば
        _actor_can_access_project(get_my_projects+get_actor_project_role)で
        判じ、明示的team member登録(get_my_projects)なら閲覧できる。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=None, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_my_projects.return_value = [{"id": 33, "name": "p"}]
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_task.assert_not_called()
        mock_inst.get_shot_detail.assert_not_called()
        mock_inst.get_my_projects.assert_called_once_with(actor_user_id=_RESOLVED_ACTOR_ID)

    def test_shot_id_zero_no_task_id_project_pm_allowed_200(self, client):
        """QC283B-1是正: その案件のpm(auto-membership)ならget_my_projectsに
        現れずとも_actor_can_access_project(#279eと同じ道具)で通る。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=None, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="pm"):
            mock_inst = MagicMock()
            mock_inst.get_my_projects.return_value = []
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200

    def test_shot_id_zero_no_task_id_project_non_member_rejected_403(self, client):
        """QC283B-1是正: project_idはあるがactorがその案件のmember・
        director/pm/lead・adminいずれでもなければ403で弾く。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=None, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_my_projects.return_value = [{"id": 99, "name": "other"}]
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/download", headers=_auth_headers())
        assert resp.status_code == 403

    def test_shot_id_zero_no_task_id_no_project_id_unresolved_case_not_blocked(self, client):
        """shot_id==0・task_id無し・project_id無し(真に手掛かり無し)の行のみが
        計画書の指図どおり意図的に未解決のまま残る(Calendar照会をスキップし
        従来どおり通す)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=None, project_id=None)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_task.assert_not_called()
        mock_inst.get_shot_detail.assert_not_called()
        mock_inst.get_my_projects.assert_not_called()

    def test_shot_id_zero_task_id_resolves_member_allowed_200(self, client):
        """#283是正: shot_id==0だがtask_idがある行——get_task経由で実shot_idを
        引き当て、そのshotのmemberなら従来どおり閲覧できる。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 5, "project_id": 33}
            mock_inst.get_shot_detail.return_value = {"id": 5, "project_id": 33}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_task.assert_called_once_with(10, actor_user_id=_RESOLVED_ACTOR_ID)
        mock_inst.get_shot_detail.assert_called_once_with(5, actor_user_id=_RESOLVED_ACTOR_ID)

    def test_shot_id_zero_task_id_resolves_non_member_rejected_403(self, client):
        """#283是正: get_task経由で引き当てた実shot_idのmemberでなければ403で弾く。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 5, "project_id": 33}
            mock_inst.get_shot_detail.return_value = {}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/download", headers=_auth_headers())
        assert resp.status_code == 403

    def test_shot_id_zero_task_not_linked_to_shot_falls_back_to_project_gate_200(self, client):
        """QC283B-1是正: taskがshotに紐づかない(get_task応答のshot_idが0/None)
        場合、従来は未解決のまま通していたが、project_idがあれば
        _actor_can_access_projectへ落として判ずる。memberなら閲覧できる。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 0, "project_id": 33}
            mock_inst.get_my_projects.return_value = [{"id": 33}]
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_not_called()

    def test_shot_id_zero_task_not_linked_to_shot_falls_back_to_project_gate_403(self, client):
        """QC283B-1是正: 同上だがproject memberでなければ403で弾く
        (未解決のまま通す従来挙動からの是正)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 0, "project_id": 33}
            mock_inst.get_my_projects.return_value = [{"id": 99}]
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 403

    def test_shot_id_zero_task_id_get_task_error_falls_back_to_project_gate_200(self, client):
        """QC283B-1是正: get_task自体が404/例外の場合も(get_taskをここでは関所に
        しない設計は従来どおり)project_idがあれば_actor_can_access_projectへ
        落として判ずる。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10, project_id=33)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_task.side_effect = RuntimeError("404 from calendar")
            mock_inst.get_my_projects.return_value = [{"id": 33}]
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_not_called()

    def test_shot_id_zero_task_not_linked_no_project_id_unresolved_not_blocked(self, client):
        """#283是正でも塞ぎ切れぬ残りの穴: taskがshotに紐づかずproject_idも無い
        行は真に手掛かり無し・従来どおり未解決のまま通す(繕わない)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10, project_id=None)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 0, "project_id": None}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_not_called()
        mock_inst.get_my_projects.assert_not_called()
