"""cmd_348: task thread の @メンション候補 = 案件にアサインされた人全員 / Shift+Enter 改行 / IME 確定 Enter で送らぬ。

隔離: Calendar は偽クライアント、DB はメモリ SQLite(load_dotenv(override=True) のため実 Calendar・実 DB には触れぬ)。
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.helpers import thread_mention
from app.models import ScoreUserRole, TaskThread

_APP = Path(__file__).resolve().parent.parent
_NOTIF = (_APP / "static" / "notif-client.js").read_text(encoding="utf-8")
_MSG = (_APP / "templates" / "messages.html").read_text(encoding="utf-8")
_BASE = (_APP / "templates" / "base.html").read_text(encoding="utf-8")
_BFF = (_APP / "routers" / "bff.py").read_text(encoding="utf-8")


class FakeClient:
    USERS = [
        {"id": 1, "name": "Aoki", "email": "aoki@x.jp"},
        {"id": 2, "name": "Baba", "email": "baba@x.jp"},
        {"id": 3, "name": "Chiba", "email": "chiba@x.jp"},
        {"id": 4, "name": "Daito", "email": "daito@x.jp"},
        {"id": 5, "name": "Endo", "email": "endo@x.jp"},
        {"id": 6, "name": "Fuji", "email": "fuji@x.jp"},
    ]
    TASKS = {101: {"id": 101, "project_id": 7}, 102: {"id": 102, "project_id": 8}}

    def get_users(self, actor_user_id=None):
        return list(self.USERS)

    def get_task(self, task_id, actor_user_id=None):
        return self.TASKS.get(task_id, {})

    def get_tasks_by_project(self, project_id, actor_user_id=None):
        # project 7: 担当は uid 3 と 4 (4 は 2 件持ち = 重複)
        if project_id == 7:
            return [{"assigned_to": 3}, {"assigned_to": 4}, {"assigned_to": 4}]
        return [{"assigned_to": 6}]

    def get_project_roles(self, project_id, actor_user_id=None):
        return {"director": 1, "pm": 2} if project_id == 7 else {}


@pytest.fixture
def mem_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(thread_mention, "SessionLocal", Session)
    return Session


def test_candidates_include_every_project_member_once(mem_db):
    db = mem_db()
    db.add(ScoreUserRole(user_id="endo@x.jp", project_id="7", role="editor"))  # email 形式
    db.add(ScoreUserRole(user_id="2", project_id="7", role="pm"))  # uid 形式・pm と重複
    db.commit()
    db.close()
    out = thread_mention.build_mention_candidates(FakeClient(), "x", ["101"])
    names = [u["name"] for u in out["users"]]
    assert out["scoped"] is True and out["project_ids"] == [7]
    assert names == ["Aoki", "Baba", "Chiba", "Daito", "Endo"]  # director/pm/担当/Score 役職・重複なし・名前順
    assert "Fuji" not in names  # 別案件の者は出ぬ


def test_shared_thread_unions_projects(mem_db):
    out = thread_mention.build_mention_candidates(FakeClient(), "x", ["101", "102"])
    assert out["project_ids"] == [7, 8]
    assert "Fuji" in [u["name"] for u in out["users"]]


def test_no_project_means_unscoped_fallback(mem_db):
    out = thread_mention.build_mention_candidates(FakeClient(), "x", [])
    assert out == {"scoped": False, "project_ids": [], "users": []}
    out = thread_mention.build_mention_candidates(FakeClient(), "x", ["999"])  # task が引けぬ
    assert out["scoped"] is False


def test_thread_task_ids_reads_task_threads(mem_db):
    db = mem_db()
    db.add(TaskThread(task_id="101", thread_id="55", title="t"))
    db.commit()
    db.close()
    assert thread_mention.thread_task_ids(55) == ["101"]
    assert thread_mention.thread_task_ids(56) == []


def test_route_registered():
    assert '"/api/bff/dm/threads/{thread_id}/mention_candidates"' in _BFF


# ---- 画面(静的な仕込み。実画面は開発機の隔離起動でスクショ確認) ----
def test_drawer_input_is_multiline_textarea():
    assert '<textarea id="score-drawer-input"' in _NOTIF
    assert '<input id="score-drawer-input"' not in _NOTIF


def test_drawer_shift_enter_newline_and_ime_guard():
    kd = _NOTIF.split("inp.addEventListener('keydown'")[1].split("return wrap;")[0]
    assert "e.isComposing" in kd and "e.keyCode === 229" in kd
    assert kd.index("e.isComposing") < kd.index("e.key === 'Enter'")  # 変換確定は Enter 判定より先に弾く
    assert "if (e.shiftKey) return;" in kd
    assert kd.index("if (e.shiftKey) return;") < kd.index("_drawerSendMessage()")
    assert "ArrowDown" in kd and "ArrowUp" in kd


def test_drawer_candidates_use_thread_scope_without_cap():
    seg = _NOTIF.split("async function _drawerMentionInput")[1].split("window._openThreadDrawer")[0]
    assert ".slice(0, 8)" not in seg  # 件数の上限を外した
    head = _NOTIF.split("async function _getDrawerMentionUsers")[1].split("function _drawerAutoResize")[0]
    assert "mention_candidates" in head and "d.scoped" in head
    assert "fetch('/api/bff/users'" in head  # 案件が引けぬ時は従来の全ユーザへ戻る


def test_messages_page_scoped_and_ime_guard():
    assert "mention_candidates" in _MSG
    assert "_scopedMentionUsers || mentionUsers" in _MSG
    kd = _MSG.split("function handleMentionKeydown(ev)")[1].split("function insertMention")[0]
    assert kd.index("ev.isComposing") < kd.index("ev.key === 'Enter'")
    # 宣言が最初の自動 open(= openRealThread 呼出)より前にある
    assert _MSG.index("let _scopedMentionUsers") < _MSG.index("async function openRealThread")


def test_notif_client_cache_busted_for_cmd348():
    assert "/static/notif-client.js?v=cmd348" in _BASE
