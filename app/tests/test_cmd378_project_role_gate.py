"""cmd_378 (subtask_378a): 画面の役職ゲートを「全体の役職」でなく「その案件での役職」で解く。

殿の条件(11/18): 管理者権限を持つのは【全体の管理者(admin)】と【その案件に PM として
設定された者】の二者。案件 PM は当該案件内でのみ admin 同等、他案件へは及ばない。
役職が解けぬ時・Calendar から取れぬ時は通さぬ(fail-closed)。

本試験は get_actor_project_role を差し替えず実物を通す(Calendar だけ偽物)。
実 DB・実 .env・実 Calendar には触れない(偽 Calendar クライアントのみ)。
"""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id, get_actor_project_role
from app.routers import (
    pages_director,
    pages_director_qc_viewer,
    pages_project_detail,
    pages_qc,
    pages_shot,
)

PM_A = "32"        # 案件 A(=126) の PM。全体の役職は user
PM_B = "99"        # 案件 B(=200) の PM。全体の役職は user
DIRECTOR_A = "29"  # 案件 A の Director。全体の役職は user
NOBODY = "77"      # どの案件にも役職なし
ADMIN = "31"       # 全体の管理者(案件の役職表には載らぬ)

PROJ_A, PROJ_B = 126, 200
ROLES = {
    PROJ_A: {"director": 29, "pm": 32, "lead": 47},
    PROJ_B: {"director": 5, "pm": 99, "lead": 6},
}
GLOBAL_ROLE = {ADMIN: "admin"}  # 他は user


class FakeCal(MagicMock):
    """Calendar の偽物。役職表(get_project_roles)と全体役職(get_me)だけ本物らしく振る舞う。"""


def make_cal(actor_id: str, roles_fail: bool = False, me_fail: bool = False) -> FakeCal:
    cal = FakeCal()

    def _me(actor_user_id=None, **kw):
        if me_fail:
            raise RuntimeError("calendar down")
        return SimpleNamespace(
            user_id=int(actor_user_id), email=f"u{actor_user_id}@example.invalid",
            role=GLOBAL_ROLE.get(str(actor_user_id), "user"), name=f"u{actor_user_id}",
        )

    def _roles(project_id, actor_user_id=None, **kw):
        if roles_fail:
            raise RuntimeError("calendar roles down")
        return dict(ROLES.get(int(project_id), {}))

    cal.get_me.side_effect = _me
    cal.get_project_roles.side_effect = _roles
    return cal


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test_secret_key_32bytes_minimum!")


def _spy_templates(monkeypatch, *modules):
    """TemplateResponse へ渡る context を捕まえる(描画の重さ・偽物の穴を避ける)。"""
    captured = {}

    def _tr(**kw):
        captured.update(kw.get("context", {}))
        return HTMLResponse("ok")

    spy = SimpleNamespace(TemplateResponse=_tr)
    for m in modules:
        monkeypatch.setattr(m, "_templates", spy)
    return captured


# ---------------------------------------------------------------- 解決器そのもの
class TestResolver:
    """get_actor_project_role(実物)の三条件 + fail-closed。"""

    def _role(self, actor, pid, **kw):
        cal = make_cal(actor, **kw)
        with patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
            return get_actor_project_role(actor, pid, client=cal)

    def test_pm_is_pm_in_own_project(self):
        assert self._role(PM_A, PROJ_A) == "pm"

    def test_pm_is_not_pm_in_other_project(self):  # 条件3: 案件 A の PM は案件 B で PM でない
        assert self._role(PM_A, PROJ_B) == "user"

    def test_nobody_is_user(self):
        assert self._role(NOBODY, PROJ_A) == "user"

    def test_admin_everywhere(self):
        assert self._role(ADMIN, PROJ_A) == "admin"
        assert self._role(ADMIN, PROJ_B) == "admin"
        assert self._role(ADMIN, None) == "admin"

    def test_unresolved_project_fails_closed(self):
        assert self._role(PM_A, None) == "user"

    def test_roles_fetch_failure_fails_closed(self):
        assert self._role(PM_A, PROJ_A, roles_fail=True) == "user"

    def test_calendar_down_fails_closed_for_pm_and_admin_not_loosened(self):
        # get_me も落ちる(全体の役職が解けぬ)時、PM を昇格させぬ・user に倒れる
        assert self._role(PM_A, PROJ_A, me_fail=True, roles_fail=True) == "user"


# ---------------------------------------------------------------- QC ビューア(実描画)
def _qc_cal(actor, shot_project, **kw):
    cal = make_cal(actor, **kw)
    cal.get_shot.return_value = SimpleNamespace(
        shot_id=5, project_id=shot_project, seq_code="s01", shot_code="c010", name="c010",
    )
    cal.get_tasks.return_value = []
    cal.get_task.return_value = {
        "id": 3255, "project_id": shot_project, "type": "other", "name": "t", "status": "qc",
        "shotID": "c010", "shot_id": 5,
    }
    cal.get_assets_by_task.return_value = []
    cal.get_shot_detail.return_value = {"asset_list": [], "project_id": shot_project}
    cal.get_users.return_value = []
    return cal


def _qc_get(monkeypatch, actor, shot_project, path="/qc/5?task_id=3255", **kw):
    app = FastAPI()
    app.include_router(pages_qc.router)
    app.dependency_overrides[get_actor_id] = lambda: actor
    cal = _qc_cal(actor, shot_project, **kw)
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda *a, **k: "P")
    monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **k: [])
    monkeypatch.setattr("app.routers.pages_qc.get_task_thread_id", lambda *a, **k: None)
    monkeypatch.setattr("app.routers.pages_qc.is_qc_delegated", lambda *a, **k: False)
    captured = _spy_templates(monkeypatch, pages_qc)
    with patch("app.routers.pages_qc.get_calendar_client", return_value=cal), \
         patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
        with TestClient(app) as c:
            resp = c.get(path)
    assert resp.status_code == 200
    return captured


class TestQcViewerGate:
    def test_pm_sees_judge_in_own_project(self, monkeypatch):  # 受入1・条件0(通る)
        ctx = _qc_get(monkeypatch, PM_A, PROJ_A)
        assert ctx["role"] == "pm" and ctx["actual_role"] == "pm"

    def test_pm_not_in_other_project(self, monkeypatch):  # 受入3・条件0(他案件は通らぬ)
        ctx = _qc_get(monkeypatch, PM_A, PROJ_B)
        assert ctx["role"] == "user"

    def test_nobody_gets_user(self, monkeypatch):  # 条件0(PMでもadminでもない者)
        ctx = _qc_get(monkeypatch, NOBODY, PROJ_A)
        assert ctx["role"] == "user"

    def test_admin_all_projects(self, monkeypatch):  # 受入2
        assert _qc_get(monkeypatch, ADMIN, PROJ_A)["role"] == "admin"
        assert _qc_get(monkeypatch, ADMIN, PROJ_B)["role"] == "admin"

    def test_pm_cannot_use_admin_role_preview(self, monkeypatch):
        # as_role によるプレビューは admin 限定のまま(PM は使えぬ・昇格の抜け道にせぬ)
        ctx = _qc_get(monkeypatch, PM_A, PROJ_A, path="/qc/5?task_id=3255&as_role=director")
        assert ctx["role"] == "pm" and ctx["preview_role"] is None

    def test_admin_can_preview(self, monkeypatch):
        ctx = _qc_get(monkeypatch, ADMIN, PROJ_A, path="/qc/5?task_id=3255&as_role=lead")
        assert ctx["role"] == "lead" and ctx["actual_role"] == "admin"

    def test_roles_down_fails_closed(self, monkeypatch):
        ctx = _qc_get(monkeypatch, PM_A, PROJ_A, roles_fail=True)
        assert ctx["role"] == "user"

    def test_unlinked_task_query_project_cannot_escalate(self, monkeypatch):
        # shot 不在で ?project_id=(案件 A)を偽っても、task 自身の案件(B)で解かれる
        cal_actor = PM_A
        app = FastAPI()
        app.include_router(pages_qc.router)
        app.dependency_overrides[get_actor_id] = lambda: cal_actor
        cal = make_cal(cal_actor)
        cal.get_shot.return_value = None
        cal.get_tasks.return_value = []
        cal.get_task.return_value = {"id": 3255, "project_id": PROJ_B, "type": "other",
                                     "name": "t", "status": "qc", "shotID": None, "shot_id": None}
        cal.get_tasks_by_project.return_value = []
        cal.get_assets_by_task.return_value = []
        monkeypatch.setattr("app.routers.pages_qc.resolve_project_name", lambda *a, **k: "P")
        monkeypatch.setattr("app.routers.pages_qc.resolve_project_members", lambda *a, **k: [])
        monkeypatch.setattr("app.routers.pages_qc.get_task_thread_id", lambda *a, **k: None)
        monkeypatch.setattr("app.routers.pages_qc.is_qc_delegated", lambda *a, **k: False)
        captured = _spy_templates(monkeypatch, pages_qc)
        with patch("app.routers.pages_qc.get_calendar_client", return_value=cal), \
             patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
            with TestClient(app) as c:
                resp = c.get(f"/qc/0?task_id=3255&project_id={PROJ_A}")
        assert resp.status_code == 200
        assert captured["role"] == "user"


# ---------------------------------------------------------------- ショット/タスク詳細
class TestShotDetailGate:
    def _get(self, monkeypatch, actor, shot_project, path):
        app = FastAPI()
        app.include_router(pages_shot.router)
        app.dependency_overrides[get_actor_id] = lambda: actor
        cal = make_cal(actor)
        cal.get_shot.return_value = SimpleNamespace(
            shot_id=5, project_id=shot_project, seq_code="s01", shot_code="c010", name="c010",
        )
        cal.get_tasks.return_value = []
        cal.get_shot_detail.return_value = {"asset_list": [], "project_id": shot_project}
        cal.get_users.return_value = []
        cal.get_task.return_value = {"id": 3255, "project_id": shot_project, "type": "other",
                                     "name": "t", "status": "qc", "shotID": "c010", "shot_id": 5}
        monkeypatch.setattr("app.routers.pages_shot.resolve_project_name", lambda *a, **k: "P")
        monkeypatch.setattr("app.routers.pages_shot.resolve_project_members", lambda *a, **k: [])
        captured = _spy_templates(monkeypatch, pages_shot)
        with patch("app.routers.pages_shot.get_calendar_client", return_value=cal), \
             patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get(path)
        assert resp.status_code == 200, resp.text[:300]
        return captured

    def test_shot_pm_own_other_none_admin(self, monkeypatch):
        assert self._get(monkeypatch, PM_A, PROJ_A, "/shot/5")["role"] == "pm"
        assert self._get(monkeypatch, PM_A, PROJ_B, "/shot/5")["role"] == "user"
        assert self._get(monkeypatch, NOBODY, PROJ_A, "/shot/5")["role"] == "user"
        assert self._get(monkeypatch, ADMIN, PROJ_B, "/shot/5")["role"] == "admin"


# ---------------------------------------------------------------- Director の QC ビューア
class TestDirectorQcViewerGate:
    def _status(self, monkeypatch, actor, my_projects, **kw):
        app = FastAPI()
        app.include_router(pages_director_qc_viewer.router)
        app.dependency_overrides[get_actor_id] = lambda: actor
        cal = make_cal(actor, **kw)
        cal.get_my_projects.return_value = my_projects
        cal.get_shots.return_value = []
        _spy_templates(monkeypatch, pages_director_qc_viewer)
        with patch("app.routers.pages_director_qc_viewer.get_calendar_client", return_value=cal), \
             patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
            with TestClient(app) as c:
                return c.get("/director_qc_viewer").status_code

    def test_director_pm_admin_pass_others_blocked(self, monkeypatch):
        mine = [{"id": PROJ_A}]
        assert self._status(monkeypatch, DIRECTOR_A, mine) == 200
        assert self._status(monkeypatch, PM_A, mine) == 200      # 案件内 PM = admin 同等
        assert self._status(monkeypatch, ADMIN, mine) == 200
        assert self._status(monkeypatch, NOBODY, mine) == 403
        # PM A は案件 B が先頭の画面では通らぬ(他案件へ及ばぬ)
        assert self._status(monkeypatch, PM_A, [{"id": PROJ_B}]) == 403

    def test_fail_closed(self, monkeypatch):
        assert self._status(monkeypatch, PM_A, [{"id": PROJ_A}], roles_fail=True) == 403
        assert self._status(monkeypatch, PM_A, []) == 403  # 案件が解けぬ


# ---------------------------------------------------------------- Retake 閲覧 / 案件詳細
class TestPassThroughPages:
    def test_project_detail_role_is_per_project(self, monkeypatch):
        app = FastAPI()
        app.include_router(pages_project_detail.router)
        app.dependency_overrides[get_actor_id] = lambda: PM_A
        cal = make_cal(PM_A)
        monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_name", lambda *a, **k: "P")
        got = {}
        for pid in (PROJ_A, PROJ_B):
            captured = _spy_templates(monkeypatch, pages_project_detail)
            with patch("app.routers.pages_project_detail.get_calendar_client", return_value=cal), \
                 patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
                with TestClient(app) as c:
                    assert c.get(f"/project_detail/{pid}").status_code == 200
            got[pid] = captured.get("role")
        assert got[PROJ_A] == "pm" and got[PROJ_B] == "user"


class TestRetakeViewGate:
    def _role(self, monkeypatch, actor, shot_project):
        app = FastAPI()
        app.include_router(pages_director.router)
        app.dependency_overrides[get_actor_id] = lambda: actor
        cal = make_cal(actor)
        cal.get_shot.return_value = SimpleNamespace(
            shot_id=7, project_id=shot_project, seq_code="s01", shot_code="c010", name="c010",
        )
        cal.get_tasks.return_value = []
        cal.get_task.return_value = {"project_id": shot_project, "name": "t"}
        cal.get_assets_by_task.return_value = []
        cal.get_shot_detail.return_value = {}
        cal.get_users.return_value = []
        captured = _spy_templates(monkeypatch, pages_director)
        with patch("app.routers.pages_director.get_calendar_client", return_value=cal), \
             patch("app.adapters.calendar_factory.get_calendar_client", return_value=cal):
            with TestClient(app) as c:
                resp = c.get("/retake_view/7/321")
        assert resp.status_code == 200
        return captured["role"]

    def test_per_project(self, monkeypatch):
        assert self._role(monkeypatch, PM_A, PROJ_A) == "pm"
        assert self._role(monkeypatch, PM_A, PROJ_B) == "user"
        assert self._role(monkeypatch, ADMIN, PROJ_B) == "admin"
