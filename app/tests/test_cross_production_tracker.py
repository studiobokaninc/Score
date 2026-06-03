"""cross_production_tracker テスト — GET /cross/production-tracker/{id} JWT認証 + get_calendar_client factory モック検証"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import cross_production_tracker

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"

_test_app = FastAPI()
_test_app.include_router(cross_production_tracker.router)


def _make_token(sub: str = "sato@studio.jp") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    return jwt.encode({"sub": sub, "exp": exp}, _SECRET, algorithm="HS256")


def _mock_get_actor_id():
    return _RESOLVED_ACTOR_ID


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    _test_app.dependency_overrides[get_actor_id] = _mock_get_actor_id
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


@pytest.fixture()
def client_no_auth():
    with TestClient(_test_app) as c:
        yield c


class TestCrossProductionTracker:
    def test_cross_tracker_valid_jwt(self, client):
        mock_tracker = {"shots": [], "retakes": [], "troubles": []}
        with patch("app.routers.cross_production_tracker.get_calendar_client") as mock_factory:
            mock_inst = MagicMock()
            mock_inst.get_production_tracker.return_value = mock_tracker
            mock_factory.return_value = mock_inst

            resp = client.get(
                "/cross/production-tracker/1",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )

        assert resp.status_code == 200
        mock_inst.get_production_tracker.assert_called_once_with(
            project_id="1", actor_user_id=_RESOLVED_ACTOR_ID
        )

    def test_cross_tracker_no_auth(self, client_no_auth):
        resp = client_no_auth.get("/cross/production-tracker/1")
        assert resp.status_code == 401

    def test_cross_tracker_user_not_found_returns_403(self):
        from fastapi import HTTPException

        def _actor_id_not_found():
            raise HTTPException(status_code=403, detail="User not found in Calendar")

        _test_app.dependency_overrides[get_actor_id] = _actor_id_not_found
        with TestClient(_test_app) as c:
            resp = c.get(
                "/cross/production-tracker/1",
                headers={"Authorization": f"Bearer {_make_token()}"},
            )
        _test_app.dependency_overrides.clear()
        assert resp.status_code == 403
