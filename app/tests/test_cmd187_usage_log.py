"""Score 利用ログ 簡易版設計計画の実装(app/usage_log.py・app/routers/usage_summary.py・
app/main.py の _UsageLogMiddleware)に対する単体/結合試験。

正本: score_usage_log_design_plan_simple_20260827.md

落としてはならぬ四点をそれぞれ以下でカバーする:
  一(記録失敗でScore本体を壊さない) → TestRecordUsageEvent / TestMiddlewareFailSoft
  二(個人にまつわる事を記録しない)   → TestNoQueryStringRecorded
  三(日付境界の明記と実装の一致)     → TestDateBoundary
  四(人でない呼出を人として数えない) → TestQuerySummaryNonHumanSeparation /
                                        TestEndpointEndToEnd (非人間行が実在する
                                        状態でその分岐を実際に通す)

DB隔離は app/tests/conftest.py の autouse フィクスチャ(_isolate_usage_log_db)
により、本ファイル内の全試験・既存の全試験の両方で score_usage_simple.db への
書込みが in-memory SQLite へ差し替わっている。
"""
import os

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
os.environ.setdefault("SCORE_SERVICE_JWT_SECRET", "test_service_secret_key_32bytes_min!")

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import usage_log as usage_log_mod
from app.adapters.dto import CalendarUser
from app.auth import create_service_token
from app.deps import ACTING_USER_ID_HEADER
from app.usage_log import (
    UsageEvent,
    cleanup_old_usage_events,
    query_usage_summary,
    record_usage_event,
)


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key_32bytes_minimum!")
    monkeypatch.setenv("SCORE_SERVICE_JWT_SECRET", "test_service_secret_key_32bytes_min!")


def _service_headers(acting_uid, token=None) -> dict:
    h = {"Authorization": f"Bearer {token or create_service_token()}"}
    if acting_uid is not None:
        h[ACTING_USER_ID_HEADER] = str(acting_uid)
    return h


def _mock_calendar_client(uid=42, role="user", users=None):
    client = MagicMock()
    client.get_me.return_value = CalendarUser(user_id=uid, email="test@studio.jp", role=role, name="Test User")
    client.get_users.return_value = users if users is not None else []
    return client


def _all_rows():
    db = usage_log_mod.UsageLogSessionLocal()
    try:
        return db.query(UsageEvent).all()
    finally:
        db.close()


# ─── 落としてはならぬ四点・一: 記録失敗がScore本体を壊さない ───────────────

class TestRecordUsageEvent:
    def test_record_writes_one_row(self):
        record_usage_event(
            occurred_at=datetime(2026, 8, 27, 10, 0, 0),
            user_id="7",
            http_method="GET",
            path="/api/health",
            status_code=200,
            duration_ms=5,
        )
        rows = _all_rows()
        assert len(rows) == 1
        assert rows[0].user_id == "7"
        assert rows[0].path == "/api/health"
        assert rows[0].status_code == 200
        assert rows[0].duration_ms == 5

    def test_record_fail_soft_does_not_raise(self, monkeypatch):
        """記録先(セッション取得そのもの)を意図的に壊しても例外が外へ伝播しない。"""

        def _boom():
            raise RuntimeError("usage db is down")

        monkeypatch.setattr("app.usage_log.UsageLogSessionLocal", _boom)
        record_usage_event(
            occurred_at=datetime.utcnow(),
            user_id="1",
            http_method="GET",
            path="/x",
            status_code=200,
            duration_ms=1,
        )  # ここで例外が飛べば試験は失敗する


class TestMiddlewareFailSoft:
    def test_request_still_returns_200_when_record_raises(self):
        """★最も落としやすい所: 記録先を意図的に壊した状態でリクエストが
        200を返す事を確かめる(記録の為に本体を壊しては本末転倒)。"""
        from app.main import app as real_app

        with patch("app.usage_log.record_usage_event", side_effect=RuntimeError("usage db broken")):
            resp = TestClient(real_app).get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # 記録先が壊れているため、行は当然1つも残っていないこと(fail-softの副作用確認)
        assert _all_rows() == []


# ─── 落としてはならぬ四点・二: 個人にまつわる事を記録しない ────────────────

class TestNoQueryStringRecorded:
    def test_query_string_excluded_from_recorded_path(self):
        from app.main import app as real_app

        TestClient(real_app).get("/api/health?token=SECRET123&user=leak@example.com")
        rows = _all_rows()
        assert len(rows) == 1
        assert rows[0].path == "/api/health"
        assert "SECRET123" not in rows[0].path
        assert "leak@example.com" not in rows[0].path


# ─── 落としてはならぬ四点・三: 日付境界(暦日・排他的上限) ──────────────────

class TestDateBoundary:
    def test_window_is_calendar_day_utc_with_exclusive_upper_bound(self):
        day = datetime(2026, 8, 27)
        db = usage_log_mod.UsageLogSessionLocal()
        try:
            db.add_all(
                [
                    UsageEvent(occurred_at=datetime(2026, 8, 26, 23, 59, 59), user_id="1",
                               http_method="GET", path="/a", status_code=200, duration_ms=1),  # 前日
                    UsageEvent(occurred_at=datetime(2026, 8, 27, 0, 0, 0), user_id="1",
                               http_method="GET", path="/a", status_code=200, duration_ms=1),  # 当日開始(含む)
                    UsageEvent(occurred_at=datetime(2026, 8, 27, 23, 59, 59), user_id="1",
                               http_method="GET", path="/a", status_code=200, duration_ms=1),  # 当日終了(含む)
                    UsageEvent(occurred_at=datetime(2026, 8, 28, 0, 0, 0), user_id="1",
                               http_method="GET", path="/a", status_code=200, duration_ms=1),  # 翌日(含まない)
                ]
            )
            db.commit()
        finally:
            db.close()

        result = query_usage_summary(day, day + timedelta(days=1))
        assert result["window_from_utc"] == "2026-08-27T00:00:00Z"
        assert result["window_to_utc"] == "2026-08-28T00:00:00Z"
        assert len(result["by_user"]) == 1
        assert result["by_user"][0]["api_call_count"] == 2


class TestRetentionCleanup:
    def test_old_rows_deleted_recent_rows_kept(self):
        now = datetime.utcnow()
        old = now - timedelta(days=91)
        recent = now - timedelta(days=1)
        db = usage_log_mod.UsageLogSessionLocal()
        try:
            db.add_all(
                [
                    UsageEvent(occurred_at=old, user_id="1", http_method="GET", path="/a",
                               status_code=200, duration_ms=1),
                    UsageEvent(occurred_at=recent, user_id="1", http_method="GET", path="/a",
                               status_code=200, duration_ms=1),
                ]
            )
            db.commit()
        finally:
            db.close()

        cleanup_old_usage_events()

        remaining = _all_rows()
        assert len(remaining) == 1
        assert remaining[0].occurred_at == recent


# ─── 落としてはならぬ四点・四: 人でない呼出を人として数えない ──────────────
# ★重要な試験の教訓(Calendar側の先例): 該当日に非人間行が実在しない状態で
# 検証しても分岐は一度も通っていない。以下は必ず非人間行を実データとして
# 用意した上でその分岐を通す。

class TestQuerySummaryNonHumanSeparation:
    def test_non_human_rows_separated_with_real_non_human_data_present(self):
        day = datetime(2026, 8, 27)
        db = usage_log_mod.UsageLogSessionLocal()
        try:
            db.add_all(
                [
                    # 非人間行(user_id=NULL): 実データとして2件用意する
                    UsageEvent(occurred_at=day.replace(hour=1), user_id=None,
                               http_method="GET", path="/api/health", status_code=200, duration_ms=2),
                    UsageEvent(occurred_at=day.replace(hour=2), user_id=None,
                               http_method="POST", path="/internal/cron", status_code=200, duration_ms=3),
                    # 人間行
                    UsageEvent(occurred_at=day.replace(hour=3), user_id="42",
                               http_method="GET", path="/api/bff/me", status_code=200, duration_ms=4),
                ]
            )
            db.commit()
        finally:
            db.close()

        result = query_usage_summary(day, day + timedelta(days=1))

        # 分岐が実際に通ったことの証跡: non_human の件数が実データどおり2
        assert result["non_human"]["api_call_count"] == 2
        assert result["non_human"]["first_seen_at"] is not None
        assert result["non_human"]["last_seen_at"] is not None
        # by_user に非人間行が混入していないこと
        assert [u["user_id"] for u in result["by_user"]] == ["42"]
        assert result["by_user"][0]["api_call_count"] == 1


# ─── 実測: 実際のリクエスト→記録1行→読ませる口の実測 ──────────────────

class TestEndpointEndToEnd:
    def test_real_request_produces_row_then_readable_via_endpoint(self):
        from app.main import app as real_app

        client = TestClient(real_app, follow_redirects=False)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        mock_client = _mock_calendar_client(uid=42, users=[{"id": 42, "name": "Elvis Presley"}])

        with patch("app.adapters.calendar_factory.get_calendar_client", return_value=mock_client):
            # 1本目: サービス資格+名乗り(acting_uid=42)で認証済リクエストを1つ発生させる
            resp1 = client.get(
                f"/api/audit/usage-summary?cycle_date={today}",
                headers=_service_headers(acting_uid=42),
            )
            assert resp1.status_code == 200

            # 未認証のヘルスチェック(非人間行として記録される想定)
            resp_health = client.get("/api/health")
            assert resp_health.status_code == 200

            # 記録が1行入ったことをDBへ直接問い合わせて確認(証跡)
            rows = _all_rows()
            assert len(rows) >= 2
            assert any(r.user_id == "42" for r in rows)
            assert any(r.user_id is None for r in rows)

            # 2本目: 読ませる口を実際に叩き、1本目の行が返ることを確認
            resp2 = client.get(
                f"/api/audit/usage-summary?cycle_date={today}",
                headers=_service_headers(acting_uid=42),
            )

        assert resp2.status_code == 200
        body = resp2.json()
        assert "window_from_utc" in body
        assert "window_to_utc" in body
        user_entries = {u["user_id"]: u for u in body["by_user"]}
        assert "42" in user_entries
        assert user_entries["42"]["api_call_count"] >= 1
        assert user_entries["42"]["name"] == "Elvis Presley"
        assert body["non_human"]["api_call_count"] >= 1

    def test_invalid_cycle_date_returns_422(self):
        from app.main import app as real_app

        mock_client = _mock_calendar_client(uid=42)
        with patch("app.adapters.calendar_factory.get_calendar_client", return_value=mock_client):
            resp = TestClient(real_app).get(
                "/api/audit/usage-summary?cycle_date=not-a-date",
                headers=_service_headers(acting_uid=42),
            )
        assert resp.status_code == 422
