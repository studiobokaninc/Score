import os
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_notifications

_SECRET = "test_secret_key_32bytes_minimum!"
_test_app = FastAPI()
_test_app.include_router(pages_notifications.router)

@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)

@pytest.fixture()
def client_fixture():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()

def test_notification_center_ok(client_fixture):
    """GET /notification_center → 200"""
    resp = client_fixture.get("/notification_center",
                               headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "通知" in resp.text

def test_notification_center_no_auth():
    """GET /notification_center no-auth → 401"""
    with TestClient(_test_app) as c:
        resp = c.get("/notification_center")
    assert resp.status_code == 401
