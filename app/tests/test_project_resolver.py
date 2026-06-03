from unittest.mock import MagicMock
from app.helpers.project_resolver import resolve_project_name, _CACHE, _TTL_SECONDS
import time


def make_client(projects):
    c = MagicMock()
    c.get_my_projects.return_value = projects
    return c


def test_resolve_cache_miss_hit():
    _CACHE.clear()
    client = make_client([{"id": 33, "name": "Ramps"}, {"id": 1, "name": "Alpha"}])
    result = resolve_project_name(33, "ryoji@test.com", client=client)
    assert result == "Ramps"
    client.get_my_projects.assert_called_once()


def test_resolve_cache_hit():
    _CACHE.clear()
    client = make_client([{"id": 33, "name": "Ramps"}])
    resolve_project_name(33, "ryoji@test.com", client=client)
    client2 = make_client([])
    result = resolve_project_name(33, "ryoji@test.com", client=client2)
    assert result == "Ramps"
    client2.get_my_projects.assert_not_called()


def test_resolve_cache_ttl_expired(monkeypatch):
    _CACHE.clear()
    client = make_client([{"id": 33, "name": "Ramps"}])
    resolve_project_name(33, "ryoji@test.com", client=client)
    _CACHE["ryoji@test.com"] = (time.time() - _TTL_SECONDS - 1, _CACHE["ryoji@test.com"][1])
    client2 = make_client([{"id": 33, "name": "Ramps_new"}])
    result = resolve_project_name(33, "ryoji@test.com", client=client2)
    assert result == "Ramps_new"
    client2.get_my_projects.assert_called_once()


def test_resolve_connect_error():
    _CACHE.clear()
    client = MagicMock()
    client.get_my_projects.side_effect = Exception("ConnectError")
    result = resolve_project_name(33, "ryoji@test.com", client=client)
    assert result == "-"


def test_resolve_project_not_found():
    _CACHE.clear()
    client = make_client([{"id": 1, "name": "Alpha"}])
    result = resolve_project_name(999, "ryoji@test.com", client=client)
    assert result == "-"
