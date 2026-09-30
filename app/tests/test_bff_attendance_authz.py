"""#279e是正 — GET /api/bff/timecards/{target_user_id}/{date}
出退勤三欄(blocker/handover/next_priority)の読み口の認可・日付制限・
情報最小化(condition/user_name を返さない)を検証する
(score-san-ken-tougou-shuusei-keikakusho-2026-09-25 3節)。
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id, get_db
from app.main import app
from app.models import TimecardLog
from app.routers import bff as _mod

_SECRET = "test_secret_key_32bytes_minimum!"
_ACTOR_ID = "42"
_TARGET_ID = "99"


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_make_token()}"}


def _jst_today_str(days_back: int = 0) -> str:
    jst = timezone(timedelta(hours=9))
    return (datetime.now(jst).date() - timedelta(days=days_back)).isoformat()


def _make_row(user_id: str, date: str) -> TimecardLog:
    return TimecardLog(
        id=1,
        user_id=user_id,
        user_name=None,
        date=date,
        clock_out_time="20:00",
        mode="today",
        blocker="詰まり本文",
        handover="申し送り本文",
        next_priority="翌日優先本文",
        raw_json="{}",
    )


@pytest.fixture()
def client(monkeypatch):
    row_holder = {"row": None}

    def _db_override():
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = row_holder["row"]
        yield db

    app.dependency_overrides[get_db] = _db_override
    app.dependency_overrides[get_actor_id] = lambda: _ACTOR_ID
    with TestClient(app) as c:
        c._row_holder = row_holder
        yield c
    app.dependency_overrides.clear()


class TestAttendanceReadAuthz:
    def test_self_read_allowed_without_any_role_200(self, client):
        """本人自身の分は役職に関わらず無条件で読める。"""
        today = _jst_today_str()
        client._row_holder["row"] = _make_row(_ACTOR_ID, today)
        with patch.object(_mod, "get_calendar_client") as MockClient:
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.get(f"/api/bff/timecards/{_ACTOR_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_my_projects.assert_not_called()

    def test_other_user_no_shared_elevated_role_rejected_403(self, client):
        """対象者の所属案件いずれでもdirector/pm/leadでない者は403で弾く。"""
        today = _jst_today_str()
        client._row_holder["row"] = _make_row(_TARGET_ID, today)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="user"):
            mock_inst = MagicMock()
            mock_inst.get_my_projects.return_value = [{"id": 33}]
            MockClient.return_value = mock_inst
            resp = client.get(f"/api/bff/timecards/{_TARGET_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 403

    def test_shared_project_pm_allowed_200(self, client):
        """対象者の所属案件でpm役職にある者は読める。"""
        today = _jst_today_str()
        client._row_holder["row"] = _make_row(_TARGET_ID, today)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="pm"):
            mock_inst = MagicMock()
            mock_inst.get_my_projects.return_value = [{"id": 33}]
            MockClient.return_value = mock_inst
            resp = client.get(f"/api/bff/timecards/{_TARGET_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 200

    def test_admin_allowed_regardless_of_shared_project_200(self, client):
        """スタジオ全体のadminは対象者の所属案件を問わず読める。"""
        today = _jst_today_str()
        client._row_holder["row"] = _make_row(_TARGET_ID, today)
        with patch.object(_mod, "get_calendar_client") as MockClient, \
             patch.object(_mod, "get_actor_project_role", return_value="admin"):
            mock_inst = MagicMock()
            MockClient.return_value = mock_inst
            resp = client.get(f"/api/bff/timecards/{_TARGET_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 200
        mock_inst.get_my_projects.assert_not_called()

    def test_date_two_days_back_rejected_400(self, client):
        """既定の上限(当日+前日まで)を超える古い日付は400で拒否する。"""
        old_date = _jst_today_str(days_back=2)
        resp = client.get(f"/api/bff/timecards/{_ACTOR_ID}/{old_date}", headers=_auth_headers())
        assert resp.status_code == 400

    def test_date_yesterday_within_window_200(self, client):
        """前日は既定の読める範囲内。"""
        yesterday = _jst_today_str(days_back=1)
        client._row_holder["row"] = _make_row(_ACTOR_ID, yesterday)
        resp = client.get(f"/api/bff/timecards/{_ACTOR_ID}/{yesterday}", headers=_auth_headers())
        assert resp.status_code == 200

    def test_no_row_returns_null_fields_found_false(self, client):
        """該当日の提出が無い場合は404にはせず、foundフラグ付きでnullを返す。"""
        today = _jst_today_str()
        client._row_holder["row"] = None
        resp = client.get(f"/api/bff/timecards/{_ACTOR_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["found"] is False
        assert body["blocker"] is None

    def test_response_excludes_condition_and_user_name(self, client):
        """condition(体調)・user_nameはいずれのキーとしても含めない(#279e指図(四))。"""
        today = _jst_today_str()
        client._row_holder["row"] = _make_row(_ACTOR_ID, today)
        resp = client.get(f"/api/bff/timecards/{_ACTOR_ID}/{today}", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert "condition" not in body
        assert "user_name" not in body
        assert set(body.keys()) == {"user_id", "date", "blocker", "handover", "next_priority", "found"}
