"""cmd_187 (2026-08-27・殿ご裁可): Score利用ログ基盤・簡易版の実装。
正本は /mnt/e/test_box/score_usage_log_design_plan_simple_20260827.md
(cmd_184/186を経て確定した簡易版設計計画)。

表1つ(usage_events)・捕捉点1箇所(_UsageLogMiddleware)・読ませる口1本
(app/routers/usage_summary.py)という造りの上限を、既存score.dbとは別の
score_usage_simple.dbへ独立したengine/Sessionで実装する。
"""
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, create_engine, func
from sqlalchemy.orm import declarative_base, sessionmaker

USAGE_LOG_DATABASE_URL = os.getenv(
    "USAGE_LOG_DATABASE_URL", "sqlite:///./score_usage_simple.db"
)

usage_log_engine = create_engine(
    USAGE_LOG_DATABASE_URL,
    connect_args={"check_same_thread": False}
    if USAGE_LOG_DATABASE_URL.startswith("sqlite")
    else {},
)
UsageLogSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=usage_log_engine
)
UsageLogBase = declarative_base()

RETENTION_DAYS = 90


class UsageEvent(UsageLogBase):
    """簡易版設計計画§3の表定義そのもの。列はこの6つのみ(理由は正本§3参照)。"""

    __tablename__ = "usage_events"

    id = Column(Integer, primary_key=True, index=True)
    occurred_at = Column(DateTime, nullable=False, index=True)  # UTC, naive
    # 解決できない呼出はNULL(§3④): 非人間呼出・未認証・トークン検証失敗を含む
    user_id = Column(String, nullable=True, index=True)
    http_method = Column(String, nullable=False)
    path = Column(String, nullable=False, index=True)  # クエリ文字列は含めない(§3②)
    status_code = Column(Integer, nullable=False)
    duration_ms = Column(Integer, nullable=False)


def record_usage_event(
    *,
    occurred_at: datetime,
    user_id: str | None,
    http_method: str,
    path: str,
    status_code: int,
    duration_ms: int,
) -> None:
    """§3①のfail-soft: 失敗しても例外を外へ伝播させない(呼び出し元=Score本体の
    応答を壊さない)。cmd_172 _record_service_override と同じ方針。"""
    try:
        db = UsageLogSessionLocal()
        try:
            db.add(
                UsageEvent(
                    occurred_at=occurred_at,
                    user_id=user_id,
                    http_method=http_method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[usage_log] write failed: {e}", file=sys.stderr, flush=True)


def cleanup_old_usage_events(retention_days: int = RETENTION_DAYS) -> None:
    """§5「保存期間」: 起動時に1回、retention_days より古い行を消す。
    別プロセス・cronは設けない。失敗してもScore起動を止めない(fail-soft)。"""
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    try:
        db = UsageLogSessionLocal()
        try:
            db.query(UsageEvent).filter(UsageEvent.occurred_at < cutoff).delete()
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[usage_log] retention cleanup failed: {e}", file=sys.stderr, flush=True)


def query_usage_summary(window_from_utc: datetime, window_to_utc: datetime) -> dict:
    """§5「読ませる口」の集計本体。window_to_utcは排他的上限
    (occurred_at < window_to_utc)。user_id=NULLの行はnon_human枠へ分離する(§3④)。"""
    db = UsageLogSessionLocal()
    try:
        rows = (
            db.query(
                UsageEvent.user_id,
                func.count(UsageEvent.id),
                func.min(UsageEvent.occurred_at),
                func.max(UsageEvent.occurred_at),
            )
            .filter(
                UsageEvent.occurred_at >= window_from_utc,
                UsageEvent.occurred_at < window_to_utc,
            )
            .group_by(UsageEvent.user_id)
            .all()
        )
    finally:
        db.close()

    by_user = []
    non_human = {"api_call_count": 0, "first_seen_at": None, "last_seen_at": None}
    for user_id, count, first_seen_at, last_seen_at in rows:
        entry = {
            "api_call_count": count,
            "first_seen_at": first_seen_at.isoformat() + "Z",
            "last_seen_at": last_seen_at.isoformat() + "Z",
        }
        if user_id is None:
            non_human = entry
        else:
            by_user.append({"user_id": user_id, **entry})

    return {
        "window_from_utc": window_from_utc.isoformat() + "Z",
        "window_to_utc": window_to_utc.isoformat() + "Z",
        "by_user": by_user,
        "non_human": non_human,
    }
