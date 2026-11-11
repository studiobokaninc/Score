"""cmd_372: QC ビューアーの動画名・ダウンロード名を上げた時の元の動画名にする。"""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.helpers.asset_names import (
    attach_display_names, content_disposition, legacy_original_name, save_original_name,
)
from app.routers import bff_write, pages_qc

_app = FastAPI()
_app.include_router(pages_qc.router)
_app.include_router(bff_write.router)


@pytest.fixture
def isolated_db(monkeypatch):
    from app.database import Base
    import app.models  # noqa: F401
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    return factory


@pytest.fixture
def client():
    _app.dependency_overrides[get_actor_id] = lambda: "42"
    with TestClient(_app) as c:
        yield c
    _app.dependency_overrides.clear()


class TestLegacyName:
    def test_tonos_example_primary(self):
        # 版名が自動採番でない (primary_v01) → 末尾を削らず元の名のまま
        assert legacy_original_name(
            "uploads/shot_978_task_4095_primary_v01_s62_c064A_primary_v01.mp4", "primary_v01"
        ) == "s62_c064A_primary_v01.mp4"

    def test_tonos_example_fx_auto_version_suffix_removed(self):
        assert legacy_original_name(
            "uploads/shot_978_task_4095_v001_s62_c064A_fx_v01_v001.mp4", "v001"
        ) == "s62_c064A_fx_v01.mp4"

    def test_unprefixed_name_unchanged(self):
        assert legacy_original_name("a/b/sample_x.mp4", "v001") == "sample_x.mp4"

    def test_name_without_original_part_kept(self):
        assert legacy_original_name("shot_none_task_3282_v003.png", "v003") == "shot_none_task_3282_v003.png"

    def test_japanese_and_space(self):
        assert legacy_original_name("shot_1_task_2_v001_夕焼け 最終_v001.mp4", "v001") == "夕焼け 最終.mp4"


class TestStoredName:
    def test_stored_name_wins_over_legacy(self, isolated_db):
        save_original_name(342, "s62_c064A_primary_v01.mp4")
        a = [{"id": 342, "version": "v001", "file_path": "uploads/shot_978_task_4095_v001_zzz_v001.mp4"}]
        attach_display_names(a)
        assert a[0]["display_name"] == "s62_c064A_primary_v01.mp4"

    def test_no_row_falls_back_to_legacy(self, isolated_db):
        a = [{"id": 9, "version": "v001", "file_path": "shot_1_task_2_v001_abc_v001.mp4"}]
        attach_display_names(a)
        assert a[0]["display_name"] == "abc.mp4"


def test_content_disposition_japanese_and_space():
    cd = content_disposition("夕焼け 最終.mp4")
    assert "filename*=UTF-8''%E5%A4%95%E7%84%BC%E3%81%91%20%E6%9C%80%E7%B5%82.mp4" in cd
    cd.encode("latin-1")  # ヘッダに載せられる (化けぬ)


def _fake_client(assets):
    m = MagicMock()
    m.get_assets_by_task.return_value = assets
    return m


class TestDownloadRoute:
    def test_download_uses_original_name_from_calendar_stream(self, client, isolated_db, monkeypatch):
        save_original_name(342, "s62_c064A_primary_v01.mp4")
        assets = [{"id": 342, "task_id": 4095, "version": "v001",
                   "file_path": "uploads/shot_978_task_4095_v001_s62_c064A_primary_v01_v001.mp4"}]
        monkeypatch.setenv("CALENDAR_MOCK", "0")

        class _Resp:
            status_code = 200
            headers = {"content-type": "video/mp4"}
            def iter_bytes(self): yield b"abc"
            def close(self): pass

        class _Cli:
            def __init__(self, *a, **k): pass
            def build_request(self, m, url): return url
            def send(self, req, stream=False): return _Resp()
            def close(self): pass

        monkeypatch.setattr("httpx.Client", _Cli)
        with patch("app.routers.bff_write.get_calendar_client", return_value=_fake_client(assets)):
            r = client.get("/api/bff/assets/342/download?task_id=4095")
        assert r.status_code == 200 and r.content == b"abc"
        assert "s62_c064A_primary_v01.mp4" in r.headers["content-disposition"]
        assert "shot_978" not in r.headers["content-disposition"]

    def test_download_requires_task_id(self, client):
        assert client.get("/api/bff/assets/1/download").status_code == 400

    def test_unknown_asset_404(self, client, isolated_db):
        with patch("app.routers.bff_write.get_calendar_client", return_value=_fake_client([])):
            assert client.get("/api/bff/assets/1/download?task_id=2").status_code == 404

    def test_original_route_uses_encoded_disposition(self, client, tmp_path, monkeypatch):
        p = tmp_path / "7_夕焼け 最終.mov"
        p.write_bytes(b"x")
        monkeypatch.setattr("app.helpers.asset_originals.find_original", lambda _id: p)
        r = client.get("/api/bff/assets/7/original")
        assert r.status_code == 200
        assert "%E5%A4%95%E7%84%BC%E3%81%91%20%E6%9C%80%E7%B5%82.mov" in r.headers["content-disposition"]


class TestQcViewerShowsOriginalName:
    def test_viewer_title_and_download_link(self, client, isolated_db, monkeypatch):
        monkeypatch.setenv("CALENDAR_MOCK", "0")
        save_original_name(342, "s62_c064A_primary_v01.mp4")
        assets = [{"id": 342, "task_id": 4095, "shot_id": 978, "version": "v001", "created_at": "2026-11-11",
                   "file_path": "uploads/shot_978_task_4095_v001_s62_c064A_primary_v01_v001.mp4"}]
        with patch("app.routers.pages_qc.get_calendar_client") as MC:
            inst = MagicMock()
            inst.get_tasks.return_value = []
            inst.get_assets_by_task.return_value = assets
            MC.return_value = inst
            r = client.get("/qc/978?task_id=4095&asset_id=342")
        assert r.status_code == 200
        assert "v001 — s62_c064A_primary_v01.mp4" in r.text
        assert "/api/bff/assets/342/download?task_id=4095" in r.text
