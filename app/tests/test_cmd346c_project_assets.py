"""cmd_346c: project asset — プロジェクト直下の置き場(uploaded_assets.scope='project')。

・DB は in-memory SQLite、保管先は tmp_path へ差し替え(実 score.db・実 asset_uploads/ に触れぬ)。
・Calendar は呼ばぬ(関所 _actor_can_access_project / _actor_can_access_shot は monkeypatch で差し替え)。
・既存のタスク/ショット asset の振る舞い(scope NULL の行)が変わらぬ事の回帰も含む。
・殿の御裁き(乙): 上げる口・一覧の口は既存と同じく関係者検査なし。ここではその事実のまま試す。
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.helpers import asset_uploads_store as store


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from app.deps import get_actor_id, get_db
    from app.main import app
    from app.models import Base, UploadedAsset
    from app.routers import bff_asset_uploads as mod

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
    state = {"project_ok": True}
    monkeypatch.setattr(mod, "_actor_can_access_project", lambda *a, **k: state["project_ok"])
    monkeypatch.setattr(mod, "_actor_can_access_shot", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_resolve_shot_id_via_task", lambda *a, **k: None)
    with TestClient(app) as c:
        yield c, Session, state, UploadedAsset
    app.dependency_overrides.clear()


def _up(c, project_id, name="a.txt", body=b"hello", ctype="text/plain"):
    return c.post("/api/bff/project_assets", data={"project_id": str(project_id)}, files={"file": (name, body, ctype)})


def test_upload_records_project_scope_row(ctx):
    c, Session, _, UA = ctx
    r = _up(c, 115)
    assert r.status_code == 200, r.text
    body = r.json()
    db = Session()
    row = db.query(UA).filter(UA.id == body["id"]).one()
    assert (row.scope, row.shot_id, row.task_id, row.project_id) == ("project", 0, None, 115)
    assert row.uploaded_by == "42" and row.filename == "a.txt"
    assert store.resolve_path(row.stored_filename) is not None
    db.close()


def test_list_is_strictly_per_project(ctx):
    c, *_ = ctx
    a = _up(c, 115, "a.txt").json()["id"]
    b = _up(c, 116, "b.txt").json()["id"]
    l115 = c.get("/api/bff/project_assets", params={"project_id": 115}).json()
    l116 = c.get("/api/bff/project_assets", params={"project_id": 116}).json()
    assert [x["id"] for x in l115] == [a] and [x["id"] for x in l116] == [b]
    assert l115[0]["view_url"] == f"/api/bff/asset_uploads/{a}/file"
    assert l115[0]["download_url"] == f"/api/bff/asset_uploads/{a}/download"
    assert c.get("/api/bff/project_assets", params={"project_id": 999}).json() == []


def test_view_and_download_work_via_existing_routes(ctx):
    c, *_ = ctx
    i = _up(c, 115, "memo.txt", b"project-wide").json()["id"]
    v = c.get(f"/api/bff/asset_uploads/{i}/file")
    assert v.status_code == 200 and v.content == b"project-wide"
    d = c.get(f"/api/bff/asset_uploads/{i}/download")
    assert d.status_code == 200 and d.content == b"project-wide"
    assert "memo.txt" in d.headers.get("content-disposition", "")


def test_view_and_download_pass_through_existing_project_gate(ctx):
    """閲覧/DL は既存の _get_row_or_404(project_id の関所)に乗る=既存の振る舞い。関係者でなければ 403。"""
    c, _, state, _ = ctx
    i = _up(c, 115).json()["id"]
    state["project_ok"] = False
    assert c.get(f"/api/bff/asset_uploads/{i}/file").status_code == 403
    assert c.get(f"/api/bff/asset_uploads/{i}/download").status_code == 403


def test_upload_and_list_have_no_membership_check_like_task_asset(ctx):
    """乙: 上げる・一覧に関係者検査を置かぬ(既存のタスク asset と同じ)。関所が False でも通る事実を固定する。"""
    c, _, state, _ = ctx
    state["project_ok"] = False
    r = _up(c, 115)
    assert r.status_code == 200
    assert len(c.get("/api/bff/project_assets", params={"project_id": 115}).json()) == 1


def test_legacy_task_asset_unchanged_and_not_mixed(ctx):
    c, Session, _, UA = ctx
    pid = _up(c, 115, "proj.txt").json()["id"]
    r = c.post("/api/bff/asset_uploads", data={"shot_id": "0", "task_id": "10", "project_id": "115"},
               files={"file": ("task.txt", b"t", "text/plain")})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    db = Session()
    assert db.query(UA).filter(UA.id == tid).one().scope is None  # 従来の行は scope NULL のまま
    db.close()
    # 従来の一覧(shot_id=0・task_id 指定)には task の物だけ。プロジェクト直下は混ざらぬ
    assert [x["id"] for x in c.get("/api/bff/asset_uploads", params={"shot_id": 0, "task_id": 10}).json()] == [tid]
    # shot_id=0 のみで引く従来の一覧にも、プロジェクト直下の行は出ぬ(従来の行は出る)
    ids = [x["id"] for x in c.get("/api/bff/asset_uploads", params={"shot_id": 0}).json()]
    assert tid in ids and pid not in ids
    # 逆に project asset の一覧に task の物は出ぬ
    assert [x["id"] for x in c.get("/api/bff/project_assets", params={"project_id": 115}).json()] == [pid]


def test_legacy_shot_upload_still_works(ctx):
    c, *_ = ctx
    r = c.post("/api/bff/asset_uploads", data={"shot_id": "5", "task_id": "10"},
               files={"file": ("s.txt", b"s", "text/plain")})
    assert r.status_code == 200
    items = c.get("/api/bff/asset_uploads", params={"shot_id": 5}).json()
    assert len(items) == 1 and items[0]["filename"] == "s.txt"


def test_upload_requires_project_id(ctx):
    c, *_ = ctx
    r = c.post("/api/bff/project_assets", files={"file": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 422


def test_ensure_columns_adds_scope_idempotently_and_keeps_existing_rows(monkeypatch):
    """起動時の列追加: 旧い表(scope 無し)へ NULL 既定で足し、既存行は不変。2 度走らせても壊れぬ。"""
    import app.main as m

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE uploaded_assets (id INTEGER PRIMARY KEY, shot_id INTEGER NOT NULL, task_id INTEGER,"
            " project_id INTEGER, filename VARCHAR NOT NULL, stored_filename VARCHAR NOT NULL,"
            " content_type VARCHAR, size_bytes INTEGER, uploaded_by VARCHAR, created_at DATETIME)"))
        conn.execute(text("CREATE TABLE bug_reports (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO uploaded_assets (id, shot_id, task_id, project_id, filename, stored_filename)"
                          " VALUES (1, 5, 10, 115, 'old.txt', '1_old.txt')"))
    monkeypatch.setattr(m, "engine", eng)
    m._ensure_columns()
    m._ensure_columns()  # 冪等
    with eng.begin() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(uploaded_assets)"))]
        assert cols.count("scope") == 1
        row = conn.execute(text("SELECT id, shot_id, task_id, project_id, filename, scope FROM uploaded_assets")).one()
    assert tuple(row) == (1, 5, 10, 115, "old.txt", None)
