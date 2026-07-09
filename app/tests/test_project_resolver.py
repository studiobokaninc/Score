from unittest.mock import MagicMock, patch
from app.adapters.calendar_client import CalendarClient
from app.helpers.project_resolver import (
    resolve_project_name, resolve_project_members, resolve_visible_projects, _CACHE, _TTL_SECONDS,
)
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


def test_resolve_project_name_falls_back_to_get_projects_for_non_member():
    """殿御命 2026-07-09 (cmd_076⑤): actor が明示メンバーでない project (auto-membership
    のみ・director/pm/lead 割当) は get_my_projects に含まれず旧実装は常に "-" だった
    (076d 実機確認 known_side_observation_out_of_scope の根治)。get_projects() (全件)
    fallback で正しく project 名が解決されることを確認する。"""
    _CACHE.clear()
    client = MagicMock(spec=CalendarClient)
    client.get_my_projects.return_value = [{"id": 1, "name": "Alpha"}]
    client.get_projects.return_value = [
        {"id": 1, "name": "Alpha"},
        {"id": 72, "name": "Marukome"},
    ]
    result = resolve_project_name(72, "ryoji-nonmember@test.com", client=client)
    assert result == "Marukome"


def test_resolve_project_name_get_projects_unavailable_still_dash():
    """get_projects() 自体が例外を投げても resolve_project_name は "-" にフォールバックし
    500 にはならない。"""
    _CACHE.clear()
    client = MagicMock(spec=CalendarClient)
    client.get_my_projects.return_value = []
    client.get_projects.side_effect = Exception("401")
    result = resolve_project_name(72, "ryoji-nonmember2@test.com", client=client)
    assert result == "-"


# ===== resolve_visible_projects (cmd_076⑤ my projects一覧の auto-membership) =====

def test_resolve_visible_projects_includes_non_member_director_project():
    """非member director/PM の project が明示メンバー一覧に無くても、Calendar の
    project_roles 経由で auto-membership union されること (/projects 一覧の真因修正)。"""
    client = MagicMock(spec=CalendarClient)
    client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    client.get_projects.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]
    client.get_project_roles.side_effect = (
        lambda pid, **kw: {"director": 53, "pm": 52} if pid == 73 else {}
    )
    with patch("app.adapters.calendar_client._to_calendar_uid", return_value=53):
        projects = resolve_visible_projects("yamada@studiobokan.com", client=client)
    ids = {p["id"] for p in projects}
    assert ids == {33, 73}


def test_resolve_visible_projects_no_duplicate_when_already_explicit_member():
    """明示メンバーかつ director でもある project は重複追加されない。"""
    client = MagicMock(spec=CalendarClient)
    client.get_my_projects.return_value = [{"id": 73, "name": "Marukome", "status": "active"}]
    client.get_projects.return_value = [{"id": 73, "name": "Marukome", "status": "active"}]
    client.get_project_roles.return_value = {"director": 53}
    with patch("app.adapters.calendar_client._to_calendar_uid", return_value=53):
        projects = resolve_visible_projects("yamada@studiobokan.com", client=client)
    assert len(projects) == 1
    assert projects[0]["id"] == 73


def test_resolve_visible_projects_get_projects_missing_no_crash():
    """client に get_projects が無い (旧 mock 等) 場合でも例外にならず、
    明示メンバー一覧のみで安全に返る。"""
    client = MagicMock(spec=["get_my_projects", "get_project_roles"])
    client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    projects = resolve_visible_projects("someone@test.com", client=client)
    assert [p["id"] for p in projects] == [33]


def test_resolve_visible_projects_actor_uid_unresolvable_no_crash():
    """actor_user_id が数値変換不能 (テスト用文字列 actor 等) でも auto-membership
    union を静かに skip し、明示メンバー一覧のみ返す (500 にならない)。"""
    client = MagicMock(spec=CalendarClient)
    client.get_my_projects.return_value = [{"id": 33, "name": "Ramps", "status": "active"}]
    client.get_projects.return_value = [
        {"id": 33, "name": "Ramps", "status": "active"},
        {"id": 73, "name": "Marukome", "status": "active"},
    ]
    projects = resolve_visible_projects("not-a-numeric-id", client=client)
    assert [p["id"] for p in projects] == [33]


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
