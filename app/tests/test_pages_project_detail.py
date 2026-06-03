import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.deps import get_actor_id
from app.routers import pages_project_detail

_test_app = FastAPI()
_test_app.include_router(pages_project_detail.router)


def test_project_detail_ok():
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        resp = c.get("/project_detail/1")
    _test_app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "Project" in resp.text


def test_project_detail_no_auth():
    with TestClient(_test_app) as c:
        resp = c.get("/project_detail/1")
    assert resp.status_code == 401
