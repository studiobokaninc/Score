from unittest.mock import MagicMock
from app.adapters.calendar_client import CalendarClient
from app.helpers.project_resolver import resolve_project_name, resolve_project_members, _CACHE, _TTL_SECONDS
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


# ===== resolve_project_members (cmd_076③ auto-membership) =====

def test_resolve_project_members_no_project_id():
    assert resolve_project_members(None, "ryoji@test.com", client=MagicMock()) == []


def test_resolve_project_members_real_client_director_not_task_assignee():
    """殿御命 2026-07-09 (cmd_076③): real CalendarClient には get_team_members が
    存在しない (production 経路)。Director が project の task に一切 assign されて
    いなくても、project_roles 由来で自動的にメンバー扱いされることを確認する。"""
    client = MagicMock(spec=CalendarClient)
    assert not hasattr(client, "get_team_members")
    client.get_tasks_by_project.return_value = [
        {"assigned_to": 5, "type": "Comp"},
        {"assigned_to": 6, "type": "Lighting"},
    ]
    client.get_project_roles.return_value = {"director": 28, "pm": 31}

    members = resolve_project_members(73, "ryoji@test.com", client=client)

    uids = {m["user_id"] for m in members}
    assert uids == {5, 6, 28, 31}
    roles = {m["user_id"]: m["role"] for m in members}
    assert roles[28] == "director"
    assert roles[31] == "pm"


def test_resolve_project_members_team_members_missing_director_still_unions():
    """get_team_members が非空でも director/pm が含まれていない場合、
    以前の実装は union 自体をスキップしていた。新実装は常に union する。"""
    client = MagicMock()
    client.get_team_members.return_value = [
        {"user_id": 5, "name": "Sato", "role": ""},
    ]
    client.get_project_roles.return_value = {"director": 28, "pm": 31}

    members = resolve_project_members(73, "ryoji@test.com", client=client)

    uids = {m["user_id"] for m in members}
    assert 28 in uids and 31 in uids, "director/pm must be auto-included even when get_team_members omits them"


def test_resolve_project_members_dedup_annotates_role_not_duplicate():
    """get_team_members に role なしで既に載っている user が project_roles でも
    director と判明した場合、重複エントリを作らず role のみ付与する。"""
    client = MagicMock()
    client.get_team_members.return_value = [
        {"user_id": 28, "name": "Yamada", "role": ""},
    ]
    client.get_project_roles.return_value = {"director": 28}

    members = resolve_project_members(73, "ryoji@test.com", client=client)

    matching = [m for m in members if m["user_id"] == 28]
    assert len(matching) == 1
    assert matching[0]["role"] == "director"


def test_resolve_project_members_no_project_roles_support():
    client = MagicMock(spec=CalendarClient)
    client.get_tasks_by_project.return_value = [{"assigned_to": 5}]
    client.get_project_roles.return_value = {}
    members = resolve_project_members(73, "ryoji@test.com", client=client)
    assert {m["user_id"] for m in members} == {5}
