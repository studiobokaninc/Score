"""
Calendar API アダプタ契約テスト（フィクスチャ固定・実API不要）
endpoints: GET /api/me (§8) / GET /api/shots (§4) / GET /api/shots/{id}/tasks (§4)
calender_api_complete_list.md 準拠
"""
import json
import unittest.mock
from pathlib import Path

import pytest

from app.adapters.calendar_client import CalendarClient
from app.adapters.dto import CalendarShot, CalendarTask, CalendarUser

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def mock_httpx_post():
    """_get_admin_token が httpx.post を使うため全テストで mock。"""
    mock = unittest.mock.MagicMock()
    mock.json.return_value = {"access_token": "test-admin-token"}
    mock.raise_for_status.return_value = None
    with unittest.mock.patch("httpx.post", return_value=mock):
        yield


def _mock_get(payload):
    mock = unittest.mock.MagicMock()
    mock.json.return_value = payload
    mock.raise_for_status.return_value = None
    return mock


def test_get_me_returns_user():
    payload = json.loads((FIXTURES / "user_me.json").read_text())
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        user = CalendarClient().get_me()
    assert isinstance(user, CalendarUser)
    assert user.user_id == 5
    assert user.email == "sato@score.local"
    assert user.role == "compositor"
    assert user.name == "Sato Ichiro"


def test_get_shots_returns_list():
    payload = json.loads((FIXTURES / "shots_list.json").read_text())
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        shots = CalendarClient().get_shots(project_id=1)
    assert isinstance(shots, list)
    assert len(shots) == 3
    assert all(isinstance(s, CalendarShot) for s in shots)
    names = [s.name for s in shots]
    assert names == ["SHOT_001", "SHOT_002", "SHOT_003"]
    assert all(s.project_id == 1 for s in shots)


def test_get_tasks_returns_list():
    payload = json.loads((FIXTURES / "tasks_list.json").read_text())
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        tasks = CalendarClient().get_tasks(shot_id=1)
    assert isinstance(tasks, list)
    assert len(tasks) == 2
    assert all(isinstance(t, CalendarTask) for t in tasks)
    types = {t.type for t in tasks}
    assert types == {"Composite", "Lighting"}
    assert all(t.shot_id == 1 for t in tasks)


def test_resolve_email_to_user_id_found():
    payload = [
        {"id": 1, "email": "tanaka@example.com"},
        {"id": 28, "email": "ryoji@studiobokan.com"},
        {"id": 29, "email": "kohei@studiobokan.com"},
    ]
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        result = CalendarClient().resolve_email_to_user_id("ryoji@studiobokan.com")
    assert result == 28


def test_resolve_email_to_user_id_not_found():
    payload = [
        {"id": 1, "email": "tanaka@example.com"},
        {"id": 28, "email": "ryoji@studiobokan.com"},
    ]
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        result = CalendarClient().resolve_email_to_user_id("nonexistent@example.com")
    assert result is None


def test_resolve_email_to_user_id_cached():
    payload = [{"id": 5, "email": "sato@studio.jp"}]
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)) as mock_get:
        client = CalendarClient()
        result1 = client.resolve_email_to_user_id("sato@studio.jp")
        result2 = client.resolve_email_to_user_id("sato@studio.jp")
    assert result1 == 5
    assert result2 == 5
    assert mock_get.call_count == 1


def test_get_me_name_fallback_name_present():
    """name あり → そのまま"""
    payload = {"id": 1, "email": "tanaka@score.local", "role": "compositor", "name": "田中"}
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        user = CalendarClient().get_me()
    assert user.name == "田中"


def test_get_me_name_fallback_name_empty():
    """name='' → username='ryoji' を採用"""
    payload = {"id": 2, "email": "ryoji@score.local", "role": "artist", "name": "", "username": "ryoji"}
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        user = CalendarClient().get_me()
    assert user.name == "ryoji"


def test_get_me_name_fallback_all_empty():
    """name='', full_name=None, username='' → 'ユーザ'"""
    payload = {"id": 3, "email": "anon@score.local", "role": "viewer", "name": "", "full_name": None, "username": ""}
    with unittest.mock.patch("httpx.get", return_value=_mock_get(payload)):
        user = CalendarClient().get_me()
    assert user.name == "ユーザ"


def _mock_shot_resp(status_code, payload=None):
    mock = unittest.mock.MagicMock()
    mock.status_code = status_code
    if payload is not None:
        mock.json.return_value = payload
    return mock


def test_get_shot_success():
    payload = {"id": 1, "project_id": 33, "shot_code": "SC001", "seq_code": "SQ001", "status": "in_progress"}
    with unittest.mock.patch("httpx.get", return_value=_mock_shot_resp(200, payload)):
        shot = CalendarClient().get_shot(1)
    assert shot is not None
    assert shot.shot_id == 1
    assert shot.shot_code == "SC001"
    assert shot.seq_code == "SQ001"


def test_get_shot_not_found():
    with unittest.mock.patch("httpx.get", return_value=_mock_shot_resp(404)):
        assert CalendarClient().get_shot(999) is None


def test_get_shot_connect_error():
    import httpx as httpx_mod
    with unittest.mock.patch("httpx.get", side_effect=httpx_mod.ConnectError("")):
        assert CalendarClient().get_shot(2) is None
