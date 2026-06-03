import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deps import get_actor_id
from app.routers import pages_pm

_test_app = FastAPI()
_test_app.include_router(pages_pm.router)

def test_pm_delivery_ok(monkeypatch):
    monkeypatch.setattr("app.routers.pages_pm.get_actor_role", lambda actor_id: "pm")
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/pm_delivery")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "成果物" in resp.text

def test_pm_delivery_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/pm_delivery")
    assert resp.status_code == 401
