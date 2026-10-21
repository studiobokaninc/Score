"""cmd_346d: uploaded_assets に scope 列が無い DB(実 score.db が書込不可で列追加が失敗した時)でも、
従来のタスク asset の一覧・アップロード・閲覧が 500 にならぬ事。project asset だけ平易な文言で使えぬ旨を返す。
DB は in-memory SQLite(scope 無しの旧い表)、保管先は tmp_path。実 DB・Calendar には触れぬ。
"""
import logging
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.helpers import asset_uploads_store as store

_OLD_DDL = (
    "CREATE TABLE uploaded_assets (id INTEGER PRIMARY KEY, shot_id INTEGER NOT NULL, task_id INTEGER,"
    " project_id INTEGER, filename VARCHAR NOT NULL, stored_filename VARCHAR NOT NULL,"
    " content_type VARCHAR, size_bytes INTEGER, uploaded_by VARCHAR, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from app.deps import get_actor_id, get_db
    from app.main import app
    from app.routers import bff_asset_uploads as mod

    monkeypatch.setattr(store, "_UPLOADS_DIR", tmp_path / "au")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text(_OLD_DDL))
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_actor_id] = lambda: "42"
    monkeypatch.setattr(mod, "_actor_can_access_project", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_actor_can_access_shot", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_resolve_shot_id_via_task", lambda *a, **k: None)
    with TestClient(app) as c:
        yield c, engine
    app.dependency_overrides.clear()


def _cols(engine):
    with engine.begin() as conn:
        return [r[1] for r in conn.execute(text("PRAGMA table_info(uploaded_assets)"))]


def test_fixture_really_has_no_scope_column(ctx):
    _, engine = ctx
    assert "scope" not in _cols(engine)


def test_existing_task_asset_list_is_not_500(ctx):
    c, engine = ctx
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO uploaded_assets (id, shot_id, task_id, project_id, filename, stored_filename)"
                          " VALUES (1, 0, 10, 115, 'old.txt', '1_old.txt')"))
    r = c.get("/api/bff/asset_uploads", params={"shot_id": 0, "task_id": 10})
    assert r.status_code == 200, r.text
    assert [x["filename"] for x in r.json()] == ["old.txt"]


def test_task_asset_upload_and_view_still_work(ctx):
    c, engine = ctx
    r = c.post("/api/bff/asset_uploads", data={"shot_id": "0", "task_id": "10", "project_id": "115"},
               files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert "scope" not in _cols(engine)  # 列追加は試みぬ(読む/書く経路だけが退く)
    lst = c.get("/api/bff/asset_uploads", params={"shot_id": 0, "task_id": 10})
    assert lst.status_code == 200 and len(lst.json()) == 1
    f = c.get(f"/api/bff/asset_uploads/{new_id}/file")
    assert f.status_code == 200 and f.content == b"hello"
    d = c.get(f"/api/bff/asset_uploads/{new_id}/download")
    assert d.status_code == 200 and d.content == b"hello"


def test_project_asset_reports_unavailable_in_plain_words(ctx):
    c, _ = ctx
    g = c.get("/api/bff/project_assets", params={"project_id": 115})
    assert g.status_code == 503
    assert "project asset" in g.json()["detail"] and "管理者" in g.json()["detail"]
    p = c.post("/api/bff/project_assets", data={"project_id": "115"}, files={"file": ("a.txt", b"x", "text/plain")})
    assert p.status_code == 503
    assert "project asset" in p.json()["detail"]


def test_ensure_columns_failure_is_warned_not_swallowed(monkeypatch, caplog, capsys):
    """ALTER が失敗(書込不可の想定)しても起動は止めず、警告をログと stderr に残す。"""
    import app.main as m

    class _Boom:
        def begin(self):
            raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr(m, "engine", _Boom())
    with caplog.at_level(logging.WARNING):
        m._ensure_columns()  # 例外を出さぬ
    assert any("uploaded_assets.scope" in rec.getMessage() and rec.levelno == logging.WARNING for rec in caplog.records)
    assert "uploaded_assets.scope" in capsys.readouterr().err
