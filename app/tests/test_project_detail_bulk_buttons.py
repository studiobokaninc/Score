"""project_detail の 全部展開 / 全部収納 / CSV 書き出し — subtask_371a (cmd_371)"""
import os
import re
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")

from app.deps import get_actor_id
from app.main import app

_SECRET = "test_secret_key_32bytes_minimum!"


def _auth_headers() -> dict:
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode({"sub": "sato@studio.jp", "exp": exp}, _SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def patch_jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _SECRET)


@pytest.fixture()
def client():
    app.dependency_overrides[get_actor_id] = lambda: "42"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _task(tid, name, seq, shot, status="wip", label="WIP"):
    return {"id": tid, "name": name, "type": "other", "status": status,
            "shotID": shot, "seqID": seq, "assigned_to": None,
            "status_color": None, "status_label": label, "status_category": None}


def _patch(monkeypatch, tasks):
    import app.adapters.calendar_client as cc

    monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_name", lambda pid, uid, **kw: "Score検証")
    monkeypatch.setattr("app.routers.pages_project_detail.resolve_project_members", lambda *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_shots", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_my_tasks", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_my_project_detail", lambda self, *a, **kw: {})
    monkeypatch.setattr(cc.CalendarClient, "get_users", lambda self, *a, **kw: [])
    monkeypatch.setattr(cc.CalendarClient, "get_tasks_by_project", lambda self, *a, **kw: tasks)


TASKS = [
    _task(1, "着弾・被弾FX", "s62", "c020"),
    _task(2, "=SUM(A1)", "s62", "c030", "deliver", "納品"),
    _task(3, "管理表", "00_prep", None),
]


class TestBulkButtons:
    def test_buttons_present_and_project_list_button_removed(self, client, monkeypatch):
        _patch(monkeypatch, TASKS)
        html = client.get("/project_detail/126", headers=_auth_headers()).text
        assert 'id="btn-expand-all"' in html and "全部展開" in html
        assert 'id="btn-collapse-all"' in html and "全部収納" in html
        assert 'href="/project_detail/126/export.csv"' in html and "CSV 書き出し" in html
        # 固定ボタンの『プロジェクト一覧』は無い(パンくずのリンクは残る)
        assert not re.search(r'<a href="/projects"[^>]*class="fixed', html)
        assert "fixed bottom-8 right-8" in html  # 三つを束ねる固定箱

    def test_every_panel_and_arrow_is_covered_by_bulk_toggle(self, client, monkeypatch):
        _patch(monkeypatch, TASKS)
        html = client.get("/project_detail/126", headers=_auth_headers()).text
        panels = re.findall(r'data-acc-panel="([^"]+)"', html)
        arrows = re.findall(r'data-acc-arrow="([^"]+)"', html)
        # SEQ 2 + SHOT 3(紐付けなし含む)
        assert len(panels) == 5 and len(set(panels)) == 5
        assert sorted(panels) == sorted(arrows)
        # 全パネルは id でも引け、個別の開閉と食い違わぬ
        for key in panels:
            if key.startswith("seq-"):
                assert f'id="{key}"' in html
            else:
                assert f'id="shot-tasks-{key[len("shot-"):]}"' in html
        assert "function setAllAccordions" in html


class TestCsvExport:
    def test_csv_bom_header_rows_and_shotless(self, client, monkeypatch):
        _patch(monkeypatch, TASKS)
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        raw = resp.content
        # cmd_374: cp932 で表せる内容は Shift_JIS(Excel で化けぬ)。BOM は付かぬ
        assert "charset=shift_jis" in resp.headers["content-type"]
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.startswith("ショット,カット".encode("cp932"))
        lines = raw.decode("cp932").splitlines()
        assert lines[0] == "ショット,カット,工程,ステータス,担当者"
        assert "s62,c020,着弾・被弾FX,WIP," in lines
        # 式注入対策
        assert "s62,c030,'=SUM(A1),DELIVER," in lines
        # SHOT 紐付けなし = カット列は空欄
        assert "00_prep,,管理表,WIP," in lines
        assert len(lines) == 4

    def test_csv_empty_project_has_header_only(self, client, monkeypatch):
        _patch(monkeypatch, [])
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.content.decode("cp932").splitlines() == ["ショット,カット,工程,ステータス,担当者"]

    def test_csv_non_cp932_char_falls_back_to_utf8_bom(self, client, monkeypatch):
        """cp932 に無い文字(絵文字)を含む時は BOM 付き UTF-8 で返し、文字を落とさぬ。"""
        _patch(monkeypatch, [_task(1, "仕上げ🔥", "s62", "c020")])
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert "charset=utf-8" in resp.headers["content-type"]
        assert resp.content.startswith(b"\xef\xbb\xbf")
        assert "仕上げ🔥" in resp.content.decode("utf-8-sig")
