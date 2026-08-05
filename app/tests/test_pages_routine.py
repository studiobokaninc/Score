import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
import datetime as _datetime_module
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.deps import get_actor_id
from app.routers import pages_routine
from app.routers.pages_routine import _has_submitted_routine_today, _has_prev_day_exit_submitted

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


# ─── _has_prev_day_exit_submitted (cmd_165: 休日を挟んだ誤『退勤してません』是正) ──

class _FakeCalendarClient:
    def __init__(self, timecards=None, holidays=None):
        self._timecards = timecards or []
        self._holidays = holidays or []

    def get_timecards(self, actor_user_id=None):
        return self._timecards

    def get_holidays(self, year, actor_user_id=None):
        return [h for h in self._holidays if str(h.get("date", "")).startswith(str(year))]


class _FrozenDateTime(_datetime_module.datetime):
    """datetime.now(tz) を固定するための最小限の freezegun 代替。"""
    _frozen = None

    @classmethod
    def now(cls, tz=None):
        base = cls._frozen
        return base.astimezone(tz) if tz is not None else base


@pytest.fixture
def frozen_now(monkeypatch):
    def _freeze(dt):
        _FrozenDateTime._frozen = dt
        monkeypatch.setattr(_datetime_module, "datetime", _FrozenDateTime)
    return _freeze


class TestHasPrevDayExitSubmitted:
    JST = timezone(timedelta(hours=9))

    def test_normal_next_business_day_no_gap(self, frozen_now):
        # 2026-08-06 (木) 09:00 にログイン。前業務日は 2026-08-05 (水)、退勤済み → True
        frozen_now(datetime(2026, 8, 6, 9, 0, tzinfo=self.JST))
        client = _FakeCalendarClient(
            timecards=[{"date": "2026-08-05", "clock_out_at": "2026-08-05T19:00:00+09:00"}],
        )
        assert _has_prev_day_exit_submitted(client, "1") is True

    def test_weekend_gap_still_finds_friday_clockout(self, frozen_now):
        # 2026-08-10 (月) 09:00 にログイン。土日 (08-08/09) は打刻無し、
        # 直前の実業務日である金曜 (08-07) の退勤記録が有効と判定されねばならぬ
        # (cmd_165 の再現条件そのもの)。
        frozen_now(datetime(2026, 8, 10, 9, 0, tzinfo=self.JST))
        client = _FakeCalendarClient(
            timecards=[{"date": "2026-08-07", "clock_out_at": "2026-08-07T19:00:00+09:00"}],
        )
        assert _has_prev_day_exit_submitted(client, "1") is True

    def test_multi_day_holiday_gap_still_finds_last_business_day(self, frozen_now):
        # 2026-08-11 (火) 09:00 にログイン。08-10 (月) を祝日として扱い、
        # 土日 (08-08/09) + 祝日 (08-10) の3日連続空白があっても、
        # その前の実業務日である金曜 (08-07) の退勤記録まで遡って拾えること。
        frozen_now(datetime(2026, 8, 11, 9, 0, tzinfo=self.JST))
        client = _FakeCalendarClient(
            timecards=[{"date": "2026-08-07", "clock_out_at": "2026-08-07T19:00:00+09:00"}],
            holidays=[{"date": "2026-08-10", "name": "祝日(test)"}],
        )
        assert _has_prev_day_exit_submitted(client, "1") is True

    def test_truly_not_clocked_out_still_warns(self, frozen_now):
        # ★最重要の歯止め: 本当に退勤していない場合は休日を挟んでも警告が出続けること。
        # 2026-08-10 (月) 09:00 にログイン。直近の実業務日である金曜 (08-07) に
        # 打刻記録が一切無い (退勤し忘れ) → False (警告対象) のまま。
        frozen_now(datetime(2026, 8, 10, 9, 0, tzinfo=self.JST))
        client = _FakeCalendarClient(timecards=[])
        assert _has_prev_day_exit_submitted(client, "1") is False

    def test_regular_weekday_no_gap_still_warns_when_missing(self, frozen_now):
        # 休日を挟まぬ通常の翌営業日でも、前日退勤が無ければ従来どおり警告対象。
        frozen_now(datetime(2026, 8, 6, 9, 0, tzinfo=self.JST))
        client = _FakeCalendarClient(timecards=[])
        assert _has_prev_day_exit_submitted(client, "1") is False
