import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deps import get_actor_id
from app.routers import pages_calendar

_test_app = FastAPI()
_test_app.include_router(pages_calendar.router)

def test_calendar_ok():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/calendar")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "Calendar" in resp.text

def test_calendar_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/calendar")
    assert resp.status_code == 401
