"""cmd_325a (2026-10-15) 回帰確認: タスク/ショット詳細から開くスレッドが対象と異なる件。

因(隔離環境で再現・前後比較済):
  ① 外部Calendar(post_dm_thread)は参加者の顔ぶれが同じなら別taskでも同じthreadを返す。
     Scoreは1task=1threadを前提にしており、cmd_279の門番は『別taskに紐付いたthreadを保存せぬ』
     だけで呼び出し元には共用threadのIDを返し続けるため、タスク画面の💬釦は他taskの会話も
     載るthreadを開いていた → 共用の事実を画面に明示する(get_all_thread_sharing)。
  ② messages.html の openRealThread は、連続して別threadを開くと遅れて返った前のthreadの
     応答が後から開いたthreadの見出しの下を上書きし、送信先(dmMode)まで取り違えた
     → 最後に開いた要求だけが反映されるようにした。
  ③ ?thread=shot001 等は画面に直書きの架空会話(見本)を引いていた → demo_mode限定にした。
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_APP = Path(__file__).resolve().parent.parent


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    from app.database import Base
    import app.models  # noqa: F401
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.helpers.task_threads.SessionLocal", factory)
    monkeypatch.setattr("app.helpers.task_threads._COLLISION_LOG_PATH", tmp_path / "collision.jsonl")
    return factory


def _add(factory, task_id, thread_id):
    from app.models import TaskThread
    db = factory()
    try:
        db.add(TaskThread(task_id=str(task_id), thread_id=str(thread_id), title=f"t{task_id}"))
        db.commit()
    finally:
        db.close()


class TestThreadSharing:
    def test_no_sharing_when_one_task_per_thread(self, isolated_db):
        from app.helpers.task_threads import get_all_thread_sharing, get_thread_sharing
        _add(isolated_db, 1, 100)
        _add(isolated_db, 2, 101)
        assert get_all_thread_sharing() == {}
        assert get_thread_sharing(100) == []

    def test_rows_sharing_a_thread_are_reported(self, isolated_db):
        from app.helpers.task_threads import get_all_thread_sharing, get_thread_sharing
        _add(isolated_db, 3636, 10000005)
        _add(isolated_db, 3642, 10000005)
        _add(isolated_db, 9, 7)
        assert get_all_thread_sharing() == {"10000005": ["3636", "3642"]}
        assert get_thread_sharing("10000005") == ["3636", "3642"]
        assert get_thread_sharing(None) == []

    def test_guard_skipped_task_counts_as_sharing(self, isolated_db):
        """門番に弾かれたtask(行が無い)も、画面は共用threadを開くので共用に数える。"""
        from app.helpers.task_threads import _set_task_thread, get_thread_sharing, get_task_thread_id
        _set_task_thread(11, 10000100)
        _set_task_thread(12, 10000100)  # 門番が保存を見送る
        assert get_task_thread_id(12) is None
        assert get_thread_sharing(10000100) == ["11", "12"]

    def test_numeric_sort_of_task_ids(self, isolated_db):
        from app.helpers.task_threads import get_thread_sharing
        _add(isolated_db, 100, 5)
        _add(isolated_db, 20, 5)
        assert get_thread_sharing(5) == ["20", "100"]

    def test_log_read_failure_is_soft(self, isolated_db, monkeypatch, tmp_path):
        from app.helpers.task_threads import get_all_thread_sharing
        bad = tmp_path / "bad.jsonl"
        bad.write_text("not json\n", encoding="utf-8")
        monkeypatch.setattr("app.helpers.task_threads._COLLISION_LOG_PATH", bad)
        _add(isolated_db, 1, 1)
        assert get_all_thread_sharing() == {}


class TestMessagesTemplateStatic:
    def _src(self):
        return (_APP / "templates" / "messages.html").read_text(encoding="utf-8")

    def test_open_real_thread_discards_stale_responses(self):
        s = self._src()
        assert "let _openRealThreadSeq = 0;" in s
        assert "const _mySeq = ++_openRealThreadSeq;" in s
        assert s.count("_mySeq !== _openRealThreadSeq") >= 3
        # 本文の更新は、最後に開いた要求だけが行う
        assert s.index("_mySeq !== _openRealThreadSeq") < s.index("area.innerHTML = (messages || [])")

    def test_open_real_thread_switches_dm_mode_before_fetch(self):
        # cmd_325b: 送信先(dmMode)は本文の読込(await fetch)より前に新threadへ切り替える
        s = self._src()
        body = s[s.index("async function openRealThread"):]
        assert body.index("dmMode = { threadId: t.thread_id") < body.index("await fetch(")
        assert s.count("dmMode = { threadId: t.thread_id") == 1

    def test_dm_mode_declared_before_show_thread_and_cleared_by_it(self):
        # cmd_325b: 見本thread表示で実threadの送信先を持ち越さぬ。宣言は初期描画の showThread より前(TDZ回避)
        s = self._src()
        assert s.count("let dmMode = null;") == 1
        assert s.index("let dmMode = null;") < s.index("function showThread(")
        sh = s[s.index("function showThread("):]
        assert "dmMode = null;" in sh[:sh.index("const row = ")]

    def test_demo_threads_only_in_demo_mode(self):
        s = self._src()
        assert "const _DEMO_MODE = {{ 'true' if demo_mode else 'false' }};" in s
        assert "if (_DEMO_MODE && typeof threads !== 'undefined' && threads[targetThread])" in s

    def test_shared_banner_present(self):
        s = self._src()
        assert 'id="conv-shared-banner"' in s
        assert "shared_task_ids" in s

    def test_task_detail_button_marks_shared(self):
        s = (_APP / "templates" / "shot_detail.html").read_text(encoding="utf-8")
        assert "task_thread_shared_with" in s
