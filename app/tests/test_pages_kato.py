"""pages_kato テスト — GET /kato_troubleshoot JWT認証"""
import os
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_kato

_SECRET = "test_secret_key_32bytes_minimum!"
_RESOLVED_ACTOR_ID = "42"

_test_app = FastAPI()
_test_app.include_router(pages_kato.router)


def _mock_get_actor_id():
    return _RESOLVED_ACTOR_ID


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client_fixture(monkeypatch):
    monkeypatch.setattr("app.routers.pages_kato.get_actor_role", lambda actor_id: "lighting_lead")
    _test_app.dependency_overrides[get_actor_id] = _mock_get_actor_id
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()


@pytest.fixture()
def client_no_auth():
    with TestClient(_test_app) as c:
        yield c


def test_kato_troubleshoot_ok(client_fixture):
    resp = client_fixture.get("/kato_troubleshoot", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200


def test_kato_troubleshoot_no_auth(client_no_auth):
    resp = client_no_auth.get("/kato_troubleshoot")
    assert resp.status_code == 401
