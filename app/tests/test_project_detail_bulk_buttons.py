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


def _task(tid, name, seq, shot, status="wip", label="WIP", progress=None):
    return {"progress": progress, "id": tid, "name": name, "type": "other", "status": status,
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
        assert lines[0] == "ショット,カット,工程,ステータス,担当者,進捗(%)"
        assert "s62,c020,着弾・被弾FX,WIP,,0" in lines
        # 式注入対策
        assert "s62,c030,'=SUM(A1),DELIVER,,0" in lines
        # SHOT 紐付けなし = カット列は空欄
        assert "00_prep,,管理表,WIP,,0" in lines
        # 表 3 行 + 空行 + 案件全体の進捗(deliver 1 / 3 件 = 33)
        assert len(lines) == 6
        assert lines[4] == "" and lines[5] == "案件全体の進捗,,,,,33"

    def test_csv_empty_project_has_header_only(self, client, monkeypatch):
        _patch(monkeypatch, [])
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.content.decode("cp932").splitlines() == ["ショット,カット,工程,ステータス,担当者,進捗(%)", "", "案件全体の進捗,,,,,0"]

    def test_csv_pm_task_and_shotless_rows_stay_shift_jis(self, client, monkeypatch):
        """管理タスク(SEQ/SHOT とも無し)・SHOT 紐付けなし行の 📋 飾りは CSV から外し、Shift_JIS・BOM 無しで返す。"""
        tasks = [
            _task(1, "着弾・被弾FX", "s62", "c020"),
            _task(2, "管理表", "", None),
            _task(3, "進行表", "00_prep", None),
        ]
        _patch(monkeypatch, tasks)
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert "charset=shift_jis" in resp.headers["content-type"]
        raw = resp.content
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw.startswith("ショット,カット".encode("cp932"))
        text = raw.decode("cp932")
        assert "📋" not in text
        lines = text.splitlines()
        assert "プロジェクト管理タスク,,管理表,WIP,,0" in lines
        assert "00_prep,,進行表,WIP,,0" in lines
        # 画面の表示は変えぬ
        html = client.get("/project_detail/126", headers=_auth_headers()).text
        assert "📋 プロジェクト管理タスク" in html

    def test_csv_non_cp932_char_falls_back_to_utf8_bom(self, client, monkeypatch):
        """cp932 に無い文字(絵文字)を含む時は BOM 付き UTF-8 で返し、文字を落とさぬ。"""
        _patch(monkeypatch, [_task(1, "仕上げ🔥", "s62", "c020")])
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert resp.status_code == 200
        assert "charset=utf-8" in resp.headers["content-type"]
        assert resp.content.startswith(b"\xef\xbb\xbf")
        assert "仕上げ🔥" in resp.content.decode("utf-8-sig")


class TestCsvProgress:
    """cmd_376: CSV にタスクの進捗 % と案件全体の進捗 % を加える。"""

    TASKS = [
        _task(1, "FX", "s62", "c020", progress=40),
        _task(2, "合成", "s62", "c020", "deliver", "納品", progress=100),
        _task(3, "ルック", "s62", "c030", "ap", "承認", progress=None),
        _task(4, "管理表", "00_prep", None, "wip", "WIP", progress=5),
    ]

    def test_task_progress_column_appended_last_and_old_columns_unchanged(self, client, monkeypatch):
        _patch(monkeypatch, self.TASKS)
        raw = client.get("/project_detail/126/export.csv", headers=_auth_headers()).content
        lines = raw.decode("cp932").splitlines()
        # 既存 5 列の名と順は不変、進捗は末尾
        assert lines[0].split(",") == ["ショット", "カット", "工程", "ステータス", "担当者", "進捗(%)"]
        assert "s62,c020,FX,WIP,,40" in lines
        assert "s62,c020,合成,DELIVER,,100" in lines
        # 未設定(None)は画面のスライダー同様 0
        assert "s62,c030,ルック,AP,,0" in lines or "s62,c030,ルック,承認,,0" in lines
        assert "00_prep,,管理表,WIP,,5" in lines

    def test_overall_progress_row_matches_screen_value(self, client, monkeypatch):
        _patch(monkeypatch, self.TASKS)
        html = client.get("/project_detail/126", headers=_auth_headers()).text
        screen = int(re.search(r"(\d+)%</p>", html).group(1))
        assert screen == 25  # deliver 1 / 全 4 件 = 25
        lines = client.get("/project_detail/126/export.csv", headers=_auth_headers()).content.decode("cp932").splitlines()
        # 表の後に空行を一つ空けて、案件全体の進捗の行(進捗(%)列)
        assert lines[-2] == ""
        assert lines[-1] == f"案件全体の進捗,,,,,{screen}"

    def test_overall_progress_truncates_like_screen(self, client, monkeypatch):
        tasks = [_task(i, f"t{i}", "s62", "c01", "deliver" if i == 1 else "wip") for i in range(1, 4)]
        _patch(monkeypatch, tasks)
        html = client.get("/project_detail/126", headers=_auth_headers()).text
        screen = int(re.search(r"(\d+)%</p>", html).group(1))
        assert screen == 33  # 1/3 = 33.3… を切り捨て
        lines = client.get("/project_detail/126/export.csv", headers=_auth_headers()).content.decode("cp932").splitlines()
        assert lines[-1] == "案件全体の進捗,,,,,33"

    def test_progress_additions_stay_cp932(self, client, monkeypatch):
        _patch(monkeypatch, self.TASKS)
        resp = client.get("/project_detail/126/export.csv", headers=_auth_headers())
        assert "charset=shift_jis" in resp.headers["content-type"]
        assert not resp.content.startswith(b"\xef\xbb\xbf")
        resp.content.decode("cp932")  # 例外なく読める
        assert "%".encode("cp932") and "案件全体の進捗".encode("cp932")
