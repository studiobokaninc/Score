import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deps import get_actor_id
from app.routers import pages_pm_dashboard

_test_app = FastAPI()
_test_app.include_router(pages_pm_dashboard.router)

def test_pm_dashboard_ok(monkeypatch):
    monkeypatch.setattr("app.routers.pages_pm_dashboard.get_actor_role", lambda actor_id: "pm")
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app, follow_redirects=False) as c:
        resp = c.get("/pm_dashboard")
    _test_app.dependency_overrides.clear()
    assert resp.status_code in (200, 302, 307)

def test_pm_dashboard_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/pm_dashboard")
    assert resp.status_code == 401
