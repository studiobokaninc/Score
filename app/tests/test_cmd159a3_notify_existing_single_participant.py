"""cmd_159a3 (2026-08-05・軍師検分是正 QC159A2-1) 回帰防止テスト

subtask_159a2 で POST /api/bff/qc/notify_existing を正本thread化 (task_id 単位で
get_or_create_task_thread 経由に統一) した際、既存2箇所 (_notify_thread L145 /
post_asset_upload L984) が備える「len(participants) >= 2」の門番をこの経路だけ
欠いていた。get_or_create_task_thread は内部で len(participant_ids) < 2 だと
None を返す設計 (app/helpers/task_threads.py) なので、宛先が1名 (director==pm==
送信者) に畳まれる座組では thread_id=None のまま client.post_dm(int(None)) に
流れ込み TypeError → HTTP 500 になっていた (新規回帰・旧コードには無かった)。

本ファイルは:
  ① 宛先1名の座組で 500 にならず、従来通り post_dm_thread 直呼び経路へ
     フォールバックすることを確認する。
  ② 宛先2名以上の主経路 (subtask_159a2 で確認済) に回帰が無いことを対照確認する。
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.main import app

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_actor_id] = lambda: _RESOLVED_ACTOR_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _base_mock_inst(director_uid: int, pm_uid: int, thread_id: int = 901):
    mock_inst = MagicMock()
    mock_inst.get_shot_detail.return_value = {}
    mock_inst.get_shot.return_value = None
    mock_inst.get_task.return_value = {"project_id": 80, "seqID": "SEQ_PM", "type": "other"}
    mock_inst.get_tasks.return_value = []
    mock_inst.get_my_projects.return_value = []
    mock_inst.get_project.return_value = {"name": "Score検証"}
    mock_inst.get_project_roles.return_value = {"director": director_uid, "pm": pm_uid}
    mock_inst.get_me.return_value = None
    mock_inst.get_users.return_value = []
    mock_inst.post_dm_thread.return_value = {"thread_id": thread_id}
    mock_inst.post_dm.return_value = {}
    return mock_inst


class TestNotifyExistingSingleParticipantGuard:
    def test_single_participant_does_not_500(self, client, monkeypatch):
        """director == pm == sender_cuid_int(42) の座組では participants が [42] の
        1件に畳まれる。門番追加後は get_or_create_task_thread を経由せず、従来通り
        client.post_dm_thread を直接呼ぶ経路へ落ち、200 で完走することを確認する。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = _base_mock_inst(director_uid=42, pm_uid=42, thread_id=901)
            MockClient.return_value = mock_inst

            resp = client.post(
                "/api/bff/qc/notify_existing",
                json={"asset_id": 500, "shot_id": 0, "task_id": 3282, "submission_type": "qc"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["participants"] == [42]
        assert body["thread_id"] == 901
        # 正本thread管理 (get_or_create_task_thread) は participants>=2 の門番により
        # スキップされ、post_dm_thread が直接 (task_id 付きで) 呼ばれたことを確認する。
        mock_inst.post_dm_thread.assert_called_once_with(
            participant_ids=[42], task_id=3282, actor_user_id=_RESOLVED_ACTOR_ID
        )
        mock_inst.post_dm.assert_called_once_with(901, mock_inst.post_dm.call_args[0][1], actor_user_id=_RESOLVED_ACTOR_ID)

    def test_multi_participant_main_path_unaffected(self, client, monkeypatch):
        """回帰確認: director(7) != pm(3) != sender(42) の主経路 (subtask_159a2 で
        確認済) は本修正後も get_or_create_task_thread 経由の正本thread化を維持する
        (門番追加が participants>=2 の既存経路を壊していないこと)。"""
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            mock_inst = _base_mock_inst(director_uid=7, pm_uid=3, thread_id=902)
            MockClient.return_value = mock_inst

            with patch(
                "app.helpers.task_threads.get_or_create_task_thread", return_value=902
            ) as mock_get_or_create:
                resp = client.post(
                    "/api/bff/qc/notify_existing",
                    json={"asset_id": 500, "shot_id": 0, "task_id": 3282, "submission_type": "qc"},
                    headers=_auth_headers(),
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert sorted(body["participants"]) == [3, 7, 42]
        assert body["thread_id"] == 902
        mock_get_or_create.assert_called_once()
        mock_inst.post_dm.assert_called_once_with(902, mock_inst.post_dm.call_args[0][1], actor_user_id=_RESOLVED_ACTOR_ID)
