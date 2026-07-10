import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.deps import get_actor_id
from app.routers import pages_routine
from app.routers.pages_routine import _has_submitted_routine_today

_test_app = FastAPI()
_test_app.include_router(pages_routine.router)


def test_routine_ok():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/routine")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200


def test_routine_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/routine")
    assert resp.status_code == 401


# ─── _has_submitted_routine_today (cmd_087: サーバ側DB提出済み判定) ──────────

@pytest.fixture
def isolated_routine_db(monkeypatch):
    """本物の score.db を汚さないよう、専用の in-memory SQLite に差し替える。"""
    from app.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", TestSessionLocal)
    return TestSessionLocal


def _add_routine_log(session_factory, user_id: str, created_at: datetime):
    from app.models import RoutineLog
    db = session_factory()
    db.add(RoutineLog(user_id=user_id, condition="good", date=created_at.date().isoformat(),
                       submitted_at=created_at.isoformat(), created_at=created_at))
    db.commit()
    db.close()


class TestHasSubmittedRoutineToday:
    def test_no_row_returns_false(self, isolated_routine_db):
        assert _has_submitted_routine_today(1) is False

    def test_row_within_business_day_returns_true(self, isolated_routine_db):
        from app.auth import get_business_day_window_utc
        start_utc, _end_utc = get_business_day_window_utc()
        _add_routine_log(isolated_routine_db, "1", start_utc + timedelta(hours=1))
        assert _has_submitted_routine_today(1) is True

    def test_row_belongs_to_different_user_returns_false(self, isolated_routine_db):
        from app.auth import get_business_day_window_utc
        start_utc, _end_utc = get_business_day_window_utc()
        _add_routine_log(isolated_routine_db, "2", start_utc + timedelta(hours=1))
        assert _has_submitted_routine_today(1) is False

    def test_row_before_business_day_start_returns_false(self, isolated_routine_db):
        # 前業務日 (5am JST 境界より前) の提出は当日分としてカウントしない
        from app.auth import get_business_day_window_utc
        start_utc, _end_utc = get_business_day_window_utc()
        _add_routine_log(isolated_routine_db, "1", start_utc - timedelta(minutes=1))
        assert _has_submitted_routine_today(1) is False

    def test_row_at_or_after_business_day_end_returns_false(self, isolated_routine_db):
        # 次業務日開始 (5am JST) 以降の提出はまだ発生していない = 当日分ではない
        from app.auth import get_business_day_window_utc
        _start_utc, end_utc = get_business_day_window_utc()
        _add_routine_log(isolated_routine_db, "1", end_utc)
        assert _has_submitted_routine_today(1) is False
