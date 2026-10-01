import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_director

_test_app = FastAPI()
_test_app.include_router(pages_director.router)

@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key_32bytes_minimum!")

@pytest.fixture()
def client_fixture(monkeypatch):
    # cmd_167: /director_retake_input の役職ゲートは get_actor_project_role (系B) 経由。
    # 本テストはページ描画の smoke test が主眼のため、ゲート解決自体の検証は
    # test_bff_write_qc_authz.py 側の専用テストに譲り、ここでは authorized 前提を注入する。
    monkeypatch.setattr(
        "app.routers.pages_director.get_actor_project_role",
        lambda actor_id, project_id, client=None: "director",
    )
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()

def test_director_retake_input_ok(client_fixture):
    """GET /director_retake_input → 200"""
    resp = client_fixture.get("/director_retake_input",
                               headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "リテイク" in resp.text

def test_director_retake_input_no_auth():
    """GET /director_retake_input no-auth → 401"""
    with TestClient(_test_app) as c:
        resp = c.get("/director_retake_input")
    assert resp.status_code == 401


# ─── /retake_view/{shot_id}/{task_id} の版指定不具合 — どの版を指して開いても
# 常にその task の最新 Retake に飛ばされていた。asset_id クエリで絞り込めること・
# 未指定時は従来挙動を保つこと・指定版の記録が無い場合に黙って他の版へ逃がさない
# ことを検証する。

def _write_retake_meta(dir_name: str, **fields) -> Path:
    d = Path(f"/tmp/score_retake_refs/{dir_name}")
    d.mkdir(parents=True, exist_ok=True)
    base = {
        "retake_id": dir_name,
        "priority": "high", "due_date": "", "reference_url": "",
        "markers": [], "comments": [], "ref_imgs": [], "ref_videos": [], "ref_docs": [],
        "submitted_by": "1",
    }
    base.update(fields)
    (d / "meta.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return d


class TestRetakeViewAssetIdVersionSelection:
    @pytest.fixture()
    def two_version_metas(self):
        """同一 shot/task に紐づく 2 件の Retake meta (古い版=asset_id 100, 新しい
        版=asset_id 200)。新しい版のほうが submitted_at が後。"""
        stamp = int(time.time() * 1000)
        old_dir = _write_retake_meta(
            f"r_test288_old_{stamp}", shot_id=7, task_id=321, asset_id=100,
            direction="古い版への指摘", submitted_at="2026-10-01T10:00:00",
        )
        new_dir = _write_retake_meta(
            f"r_test288_new_{stamp}", shot_id=7, task_id=321, asset_id=200,
            direction="新しい版への指摘", submitted_at="2026-10-01T12:00:00",
        )
        try:
            yield
        finally:
            for d in (old_dir, new_dir):
                (d / "meta.json").unlink(missing_ok=True)
                d.rmdir()

    def _base_mock(self):
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = None
        mock_inst.get_shot.return_value = None
        mock_inst.get_task.return_value = {"project_id": 80}
        mock_inst.get_tasks.return_value = []
        mock_inst.get_shot_detail.return_value = {}
        mock_inst.get_assets_by_task.return_value = []
        mock_inst.get_users.return_value = []
        return mock_inst

    def test_old_version_asset_id_shows_old_meta_not_latest(self, client_fixture, monkeypatch, two_version_metas):
        """指定した版 (asset_id=100・古い版) の Retake を開くと、その版の内容が
        出ること。task 全体の最新 (asset_id=200) へすり替わってはならない。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        mock_inst = self._base_mock()
        with patch("app.routers.pages_director.get_calendar_client", return_value=mock_inst):
            resp = client_fixture.get("/retake_view/7/321?asset_id=100", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert "古い版への指摘" in resp.text
        assert "新しい版への指摘" not in resp.text

    def test_new_version_asset_id_shows_new_meta(self, client_fixture, monkeypatch, two_version_metas):
        """正の対照実験: 新しい版 (asset_id=200) を指定すればその内容が出る。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        mock_inst = self._base_mock()
        with patch("app.routers.pages_director.get_calendar_client", return_value=mock_inst):
            resp = client_fixture.get("/retake_view/7/321?asset_id=200", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert "新しい版への指摘" in resp.text
        assert "古い版への指摘" not in resp.text

    def test_no_asset_id_param_keeps_legacy_latest_behavior(self, client_fixture, monkeypatch, two_version_metas):
        """asset_id 未指定 (旧リンク・通知等) は従来通り task 全体で最新の Retake を
        出す — 既存の振る舞いを壊さないことの確認。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        mock_inst = self._base_mock()
        with patch("app.routers.pages_director.get_calendar_client", return_value=mock_inst):
            resp = client_fixture.get("/retake_view/7/321", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert "新しい版への指摘" in resp.text

    def test_asset_id_with_no_matching_record_shows_honest_message_not_fallback(self, client_fixture, monkeypatch, two_version_metas):
        """指定した版 (asset_id=999) に記録が無い場合、黙って他の版 (最新など) へ
        逃がさず「この版の Retake 記録は残っておりません」と正直に出す。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        mock_inst = self._base_mock()
        with patch("app.routers.pages_director.get_calendar_client", return_value=mock_inst):
            resp = client_fixture.get("/retake_view/7/321?asset_id=999", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert "この版の Retake 記録は残っておりません" in resp.text
        assert "新しい版への指摘" not in resp.text
        assert "古い版への指摘" not in resp.text

    def test_target_asset_resolved_via_meta_asset_id_not_task_latest(self, client_fixture, monkeypatch, two_version_metas):
        """表示する対象 asset は、その下げ戻しの meta.json が持つ asset_id から
        引く。task 全体の最新 asset (created_at 最新) に化けてはならない。"""
        monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
        mock_inst = self._base_mock()
        mock_inst.get_shot_detail.return_value = {
            "asset_list": [
                {"id": 100, "task_id": 321, "version": "v001",
                 "file_path": "/data/assets/old_v001.mov", "created_at": "2026-10-01T09:00:00"},
                {"id": 200, "task_id": 321, "version": "v002",
                 "file_path": "/data/assets/new_v002.mov", "created_at": "2026-10-01T11:00:00"},
            ]
        }
        with patch("app.routers.pages_director.get_calendar_client", return_value=mock_inst):
            resp = client_fixture.get("/retake_view/7/321?asset_id=100", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200
        assert "old_v001.mov" in resp.text
        assert "new_v002.mov" not in resp.text
