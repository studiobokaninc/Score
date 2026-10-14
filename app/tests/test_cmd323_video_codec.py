"""cmd_323: 動画を中身(映像コーデック)で判じ、画面で映らぬ形(mp4v 等)は H.264 へ変える。

・判定部は合成した最小の入れ物で試す(ffmpeg 不要)。
・変換は同梱 ffmpeg で小さな合成動画を作って実際に通す(SKIP を出さぬため、同梱 ffmpeg が無ければ失敗させる)。
・実運用の Calendar へは一切書かぬ(ルータは asset_uploads の Score ローカル枠のみ・DB と保管先は差替え)。
"""
import os
import struct
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.helpers import video_codec as vc
from app.helpers import asset_uploads_store as store


def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), btype) + payload


def _mp4_with_stsd(fourcc: bytes, audio_first: bool = False) -> bytes:
    def stsd(fc):
        entry = struct.pack(">I4s", 16, fc) + b"\x00" * 8
        return _box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry)

    def trak(fc):
        return _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", stsd(fc)))))

    traks = (trak(b"mp4a") + trak(fourcc)) if audio_first else trak(fourcc)
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    return ftyp + _box(b"mdat", b"\x00" * 16) + _box(b"moov", traks)  # moov が末尾の形


class TestDetect:
    @pytest.mark.parametrize("fourcc,playable", [(b"avc1", True), (b"avc3", True), (b"mp4v", False), (b"hvc1", False), (b"hev1", False)])
    def test_mp4_sample_entry(self, fourcc, playable):
        codec = vc.detect_video_codec(_mp4_with_stsd(fourcc))
        assert codec == fourcc.decode()
        assert vc.is_browser_playable(codec) is playable

    def test_audio_track_first_is_skipped(self):
        assert vc.detect_video_codec(_mp4_with_stsd(b"mp4v", audio_first=True)) == "mp4v"

    @pytest.mark.parametrize("cid,playable", [(b"V_VP9", True), (b"V_VP8", True), (b"V_AV1", True), (b"V_MPEG4", False)])
    def test_webm(self, cid, playable):
        data = b"\x1a\x45\xdf\xa3" + b"\x00" * 40 + cid + b"\x00" * 10
        codec = vc.detect_video_codec(data)
        assert codec == cid.decode()
        assert vc.is_browser_playable(codec) is playable

    @pytest.mark.parametrize("data", [b"", b"abc", b"\x00" * 100, b"\xff" * 64])
    def test_broken_returns_none_without_raising(self, data):
        assert vc.detect_video_codec(data) is None
        assert vc.is_browser_playable(None) is None

    def test_truncated_box_does_not_hang(self):
        assert vc.detect_video_codec(_mp4_with_stsd(b"avc1")[:-6]) in (None, "avc1")


class TestPrepare:
    def test_non_video_untouched(self):
        r = vc.prepare_video_for_browser(b"hello", "a.txt", "text/plain")
        assert r["converted"] is False and r["content"] == b"hello" and r["error"] is None

    def test_playable_mp4_passthrough_no_ffmpeg_call(self, monkeypatch):
        monkeypatch.setattr(vc, "transcode_to_h264", lambda *a, **k: pytest.fail("H.264 を再変換してはならぬ"))
        data = _mp4_with_stsd(b"avc1")
        r = vc.prepare_video_for_browser(data, "a.mp4", "video/mp4")
        assert r["converted"] is False and r["content"] == data

    def test_undetectable_mp4_passthrough(self, monkeypatch):
        monkeypatch.setattr(vc, "transcode_to_h264", lambda *a, **k: pytest.fail("判定不能の .mp4 に触れてはならぬ"))
        r = vc.prepare_video_for_browser(b"\x00" * 100, "a.mp4", "video/mp4")
        assert r["converted"] is False

    def test_conversion_failure_keeps_original_and_reports_error(self):
        data = _mp4_with_stsd(b"mp4v")  # 判定は mp4v だが中身は ffmpeg が読めぬ作り物
        r = vc.prepare_video_for_browser(data, "a.mp4", "video/mp4")
        assert r["converted"] is False and r["content"] == data and r["error"]


@pytest.fixture(scope="module")
def sample_videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("cmd323vid")
    ff = vc._ffmpeg_exe()
    out = {}
    for name, args in {
        "mp4v.mp4": ["-c:v", "mpeg4", "-pix_fmt", "yuv420p"],
        "h264.mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        "h264.mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        "odd.mp4": ["-c:v", "mpeg4", "-pix_fmt", "yuv420p"],  # 奇数寸法
    }.items():
        size = "101x75" if name == "odd.mp4" else "128x96"
        p = d / name
        proc = subprocess.run(
            [ff, "-y", "-f", "lavfi", "-i", f"testsrc=duration=1:size={size}:rate=12", *args, str(p)],
            capture_output=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr.decode(errors="replace")[-300:]
        out[name] = p.read_bytes()
    return out


class TestRealConversion:
    def test_mp4v_becomes_h264_mp4(self, sample_videos):
        data = sample_videos["mp4v.mp4"]
        assert vc.detect_video_codec(data) == "mp4v"
        r = vc.prepare_video_for_browser(data, "shot.mp4", "video/mp4")
        assert r["converted"] is True and r["filename"] == "shot.mp4" and r["content_type"] == "video/mp4"
        assert vc.detect_video_codec(r["content"]) == "avc1"
        assert r["notice"]

    def test_odd_dimensions_convert(self, sample_videos):
        r = vc.prepare_video_for_browser(sample_videos["odd.mp4"], "odd.mp4", "video/mp4")
        assert r["converted"] is True, r["error"]
        assert vc.detect_video_codec(r["content"]) == "avc1"

    def test_h264_mp4_not_reencoded(self, sample_videos):
        data = sample_videos["h264.mp4"]
        r = vc.prepare_video_for_browser(data, "ok.mp4", "video/mp4")
        assert r["converted"] is False and r["content"] == data

    def test_h264_mov_is_remuxed_to_mp4(self, sample_videos):
        r = vc.prepare_video_for_browser(sample_videos["h264.mov"], "clip.mov", "video/quicktime")
        assert r["converted"] is True and r["filename"] == "clip.mp4"
        assert vc.detect_video_codec(r["content"]) == "avc1"


@pytest.fixture()
def upload_client(tmp_path, monkeypatch):
    """asset_uploads の Score ローカル枠だけを、一時 DB・一時保管先へ差し替えて使う。"""
    from app.deps import get_actor_id, get_db
    from app.main import app
    from app.models import Base, UploadedAsset
    from app.routers import bff_asset_uploads as mod

    app.include_router(mod.router)
    monkeypatch.setattr(store, "_UPLOADS_DIR", tmp_path / "au")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine, tables=[UploadedAsset.__table__])
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_actor_id] = lambda: "42"
    monkeypatch.setattr(mod, "_actor_can_access_shot", lambda *a, **k: True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAssetUploadsRoute:
    def test_mp4v_upload_is_viewable_and_original_downloadable(self, upload_client, sample_videos):
        data = sample_videos["mp4v.mp4"]
        r = upload_client.post("/api/bff/asset_uploads", data={"shot_id": "5", "task_id": "10"},
                               files={"file": ("v1.mp4", data, "video/mp4")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["video_notice"]
        view = upload_client.get(f"/api/bff/asset_uploads/{body['id']}/file")
        assert view.status_code == 200 and vc.detect_video_codec(view.content) == "avc1"
        dl = upload_client.get(f"/api/bff/asset_uploads/{body['id']}/download")
        assert dl.status_code == 200 and dl.content == data  # ダウンロードは従来どおり原本
        assert "v1.mp4" in dl.headers.get("content-disposition", "")

    def test_h264_upload_unchanged_and_no_original(self, upload_client, sample_videos):
        data = sample_videos["h264.mp4"]
        r = upload_client.post("/api/bff/asset_uploads", data={"shot_id": "5"},
                               files={"file": ("ok.mp4", data, "video/mp4")})
        body = r.json()
        assert r.status_code == 200 and body["video_notice"] is None
        assert upload_client.get(f"/api/bff/asset_uploads/{body['id']}/file").content == data
        assert store.find_upload_original(body["id"]) is None

    def test_non_video_upload_unchanged(self, upload_client):
        r = upload_client.post("/api/bff/asset_uploads", data={"shot_id": "5"},
                               files={"file": ("a.txt", b"abc", "text/plain")})
        body = r.json()
        assert r.status_code == 200 and body["video_notice"] is None
        assert upload_client.get(f"/api/bff/asset_uploads/{body['id']}/file").content == b"abc"


class TestBffAssetsRoute:
    """QC/review 提出口(/api/bff/assets)。Calendar は MagicMock・原本保管先は一時 dir(実運用へは書かぬ)。"""

    @pytest.fixture()
    def bff_client(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch
        from app.deps import get_actor_id
        from app.main import app
        from app.helpers import asset_originals

        monkeypatch.setattr(asset_originals, "_ORIGINALS_DIR", tmp_path / "orig")
        app.dependency_overrides[get_actor_id] = lambda: "42"
        with patch("app.routers.bff_write.get_calendar_client") as MockClient:
            inst = MagicMock()
            inst.post_asset.return_value = {"id": 9001, "filename": "v1.mp4"}
            del inst.post_dm_thread  # 通知スレッド経路は本試験の対象外(MagicMock が JSON 化できぬため)
            MockClient.return_value = inst
            with TestClient(app) as c:
                c._inst = inst
                yield c
        app.dependency_overrides.clear()

    def test_mp4v_sent_to_calendar_as_h264_and_original_kept(self, bff_client, sample_videos):
        from app.helpers import asset_originals
        data = sample_videos["mp4v.mp4"]
        r = bff_client.post("/api/bff/assets", files={"file": ("v1.mp4", data, "video/mp4")})
        assert r.status_code == 200, r.text
        assert r.json()["video_notice"]
        sent = bff_client._inst.post_asset.call_args.kwargs
        assert vc.detect_video_codec(sent["file_data"]) == "avc1" and sent["filename"] == "v1.mp4"
        orig = asset_originals.find_original(9001)
        assert orig is not None and orig.read_bytes() == data

    def test_h264_mp4_sent_unchanged(self, bff_client, sample_videos):
        from app.helpers import asset_originals
        data = sample_videos["h264.mp4"]
        r = bff_client.post("/api/bff/assets", files={"file": ("ok.mp4", data, "video/mp4")})
        assert r.status_code == 200 and "video_notice" not in r.json()
        assert bff_client._inst.post_asset.call_args.kwargs["file_data"] == data
        assert asset_originals.find_original(9001) is None

    def test_mov_still_converted_like_before(self, bff_client, sample_videos):
        r = bff_client.post("/api/bff/assets", files={"file": ("c.mov", sample_videos["h264.mov"], "video/quicktime")})
        assert r.status_code == 200
        sent = bff_client._inst.post_asset.call_args.kwargs
        assert sent["filename"] == "c.mp4" and sent["content_type"] == "video/mp4"

    def test_broken_non_mov_video_is_not_rejected(self, bff_client):
        bad = _mp4_with_stsd(b"mp4v")
        r = bff_client.post("/api/bff/assets", files={"file": ("bad.mp4", bad, "video/mp4")})
        assert r.status_code == 200 and r.json()["video_notice"]
        assert bff_client._inst.post_asset.call_args.kwargs["file_data"] == bad
