import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deps import get_actor_id
from app.routers import pages_director_qc_viewer

_test_app = FastAPI()
_test_app.include_router(pages_director_qc_viewer.router)

def test_director_qc_viewer_ok(monkeypatch):
    monkeypatch.setattr("app.routers.pages_director_qc_viewer.get_actor_role", lambda actor_id: "director")
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/director_qc_viewer")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200

def test_director_qc_viewer_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/director_qc_viewer")
    assert resp.status_code == 401
