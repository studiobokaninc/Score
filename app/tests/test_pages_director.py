import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.routers import pages_director

_test_app = FastAPI()
_test_app.include_router(pages_director.router)

@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key_32bytes_minimum!")

@pytest.fixture()
def client_fixture(monkeypatch):
    monkeypatch.setattr("app.routers.pages_director.get_actor_role", lambda actor_id: "director")
    _test_app.dependency_overrides[get_actor_id] = lambda: "test-actor"
    with TestClient(_test_app) as c:
        yield c
    _test_app.dependency_overrides.clear()

def test_director_retake_input_ok(client_fixture):
    """GET /director_retake_input → 200"""
    resp = client_fixture.get("/director_retake_input",
                               headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    assert "リテイク" in resp.text

def test_director_retake_input_no_auth():
    """GET /director_retake_input no-auth → 401"""
    with TestClient(_test_app) as c:
        resp = c.get("/director_retake_input")
    assert resp.status_code == 401
