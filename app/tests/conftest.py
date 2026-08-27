import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def seeded_user_email():
    return "sato@studio.jp"


@pytest.fixture(autouse=True)
def _isolate_usage_log_db(monkeypatch):
    """利用ログ機構(app.usage_log)の _UsageLogMiddleware は app.main.app 全体に
    登録済のため、TestClient(app.main.app) を使うあらゆる既存/新規の試験が
    対象になる。既存の score.db 隔離(各試験ファイルの isolated_*_db
    autouse フィクスチャ、test_cmd172_service_actor_override.py 等)と同じ理由で、
    実運用の score_usage_simple.db へ試験データが混入するのを防ぐため、
    conftest.py で全試験へ無条件適用する。"""
    from app.usage_log import UsageLogBase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    UsageLogBase.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.usage_log.UsageLogSessionLocal", TestSessionLocal)
    return TestSessionLocal
