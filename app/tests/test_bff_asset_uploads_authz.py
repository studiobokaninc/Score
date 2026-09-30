"""#276是正 — GET /api/bff/asset_uploads/{id}/file
・/download の認可検証。_get_row_or_404 が shot_id/project_id/uploaded_by いずれも
actor_id と照合していなかった穴(score-san-ken-tougou-shuusei-keikakusho-2026-09-25
1節)に対し、Calendar側 GET /api/me/shots/{id} (get_shot_detail) の project member
限定応答に乗せた是正を検証する。

#283是正: shot_id==0(shotに紐付かない行)のうちtask_idがある行は、
task_id→Calendar get_task→実shot_id→既存の_actor_can_access_shot(get_shot_detail・
member限定)へ橋渡しする。task_idが無い行・taskがshot非紐づきの行は計画書の指図
どおり意図的に未解決のまま(残る穴)。"""
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


def _make_row(shot_id: int, stored_filename: str = "1_a.txt", task_id: Optional[int] = 10) -> UploadedAsset:
    row = UploadedAsset(
        id=1,
        shot_id=shot_id,
        task_id=task_id,
        project_id=33,
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

    def test_shot_id_zero_no_task_id_unresolved_case_not_blocked(self, client):
        """shot_id==0かつtask_idも無い行(手掛かり無し)は#283の当たりでも
        引き当て不能・意図的に未解決のまま(Calendar照会をスキップし従来どおり通す)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=None)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_task.assert_not_called()
        mock_inst.get_shot_detail.assert_not_called()

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

    def test_shot_id_zero_task_not_linked_to_shot_unresolved_not_blocked(self, client):
        """#283是正でも塞ぎ切れぬ残りの穴: taskがshotに紐づかない
        (get_task応答のshot_idが0/None)場合は引き当て不能・従来どおり未解決のまま
        通す(繕わない)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.return_value = {"id": 10, "shot_id": 0, "project_id": 33}
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_not_called()

    def test_shot_id_zero_task_id_get_task_error_fails_open_unresolved(self, client):
        """get_task自体が404/例外の場合も引き当て不能・従来どおり未解決のまま通す
        (get_taskをここでは関所にしない設計どおり)。"""
        client._row_holder["row"] = _make_row(shot_id=0, task_id=10)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            mock_inst.get_task.side_effect = RuntimeError("404 from calendar")
            MockClient.return_value = mock_inst
            resp = client.get("/api/bff/asset_uploads/1/file", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_shot_detail.assert_not_called()
