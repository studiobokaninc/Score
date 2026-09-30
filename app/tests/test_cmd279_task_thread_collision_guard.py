"""cmd_279乙 (2026-09-30) 回帰防止テスト

task_threads (app/helpers/task_threads.py) は 1 task_id につき 1 thread_id を
正本管理する設計だが、外部Calendar API (post_dm_thread) が task_id単位で
重複排除している保証は無い (同ファイル冒頭docstring参照)。参加者
(participant_ids) の顔ぶれが同一な別タスクへ、外部側が既存threadを使い回して
返す実例を dev DB で確認済み (thread_id=10000007 が task_id 3255/3256/3282/
3327 の4件に跨って記録されていた)。

_set_task_thread に「返ってきた thread_id が既に別の task_id で使用済みなら
書き込みをスキップする」門番を追加した (新規発生分のみを止める・既存の重複行は
対象外)。本ファイルはその門番の回帰確認:
  ① 別タスクが既に使っている thread_id が外部から返っても、新たな重複行を
     作らない (書き込みスキップ)。
  ② 未使用の thread_id は従来通り正常に書き込まれる (回帰無し確認)。

cmd_279丁 (2026-09-30・QC279D-1/ADV279D-2是正) で追加した回帰確認:
  ③ ADV279D-2: 既存行の更新経路 (task_idの行が既にある場合) でも同じ門番が
     掛かり、他task_idが使用中のthread_idへは上書きしない (従前は新規行の
     作成経路にしか門番が掛かっていなかった)。
  ④ QC279D-1(二): 書込スキップが追記専用のJSONLファイルにも残り、
     get_task_thread_collision_stats() で件数として数えられる (stderrのみで
     見えなくなる事への手当て)。★ score.db への新規テーブル追加は退けた —
     このdev機の score.db が root所有で非rootから書込不可であることを実機で
     確認し(uploads/→asset_uploads/移設と同種の既知パターン)、app.main
     importのたびに走る Base.metadata.create_all が新規テーブル1つで
     score.db に触れるあらゆる試験を壊すため。
"""
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def isolated_task_thread_db(monkeypatch):
    """本物の score.db を汚さないよう、専用の in-memory SQLite に差し替える。
    task_threads.py はモジュール冒頭で `from app.database import SessionLocal`
    しており、`app.database.SessionLocal` への monkeypatch だけではこの
    モジュールに既に束縛された名前までは差し替わらない — 直接
    `app.helpers.task_threads.SessionLocal` を差し替える。"""
    from app.database import Base
    import app.models  # noqa: F401 (Base.metadata へ TaskThread 等を登録するため import 必須)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.helpers.task_threads.SessionLocal", TestSessionLocal)
    return TestSessionLocal


@pytest.fixture
def isolated_collision_log(monkeypatch, tmp_path):
    """可視化ログ(JSONLファイル)を実ファイル(app/data/配下)ではなく試験専用の
    一時パスへ差し替える(試験間の状態漏れ・実ファイル汚染を防ぐ)。"""
    log_path = tmp_path / "task_thread_collision_log.jsonl"
    monkeypatch.setattr("app.helpers.task_threads._COLLISION_LOG_PATH", log_path)
    return log_path


def _rows(session_factory):
    from app.models import TaskThread
    db = session_factory()
    try:
        return db.query(TaskThread).all()
    finally:
        db.close()


class TestTaskThreadCollisionGuard:
    def test_reused_external_thread_id_is_not_persisted_for_unrelated_task(self, isolated_task_thread_db, isolated_collision_log):
        from app.helpers.task_threads import get_or_create_task_thread

        db = isolated_task_thread_db()
        from app.models import TaskThread
        db.add(TaskThread(task_id="100", thread_id="500", title="taskA-Thread"))
        db.commit()
        db.close()

        client = MagicMock()
        # 外部Calendarが task_id=200 に対しても、既に task_id=100 が使用中の
        # thread_id=500 を(参加者の顔ぶれが同一等の理由で)使い回して返す状況を再現。
        client.post_dm_thread.return_value = {"thread_id": 500}

        result = get_or_create_task_thread(
            client, actor_id="42", task_id=200, participant_ids=[1, 2],
            title="taskB-Thread",
        )

        # 通知送信自体は継続 (返り値はそのまま渡す・呼び出し元の post_dm は妨げない)
        assert result == 500

        rows = _rows(isolated_task_thread_db)
        assert len(rows) == 1
        assert rows[0].task_id == "100"
        assert rows[0].thread_id == "500"

    def test_fresh_thread_id_is_persisted_normally(self, isolated_task_thread_db):
        from app.helpers.task_threads import get_or_create_task_thread

        client = MagicMock()
        client.post_dm_thread.return_value = {"thread_id": 600}

        result = get_or_create_task_thread(
            client, actor_id="42", task_id=300, participant_ids=[1, 2],
            title="taskC-Thread",
        )

        assert result == 600

        rows = _rows(isolated_task_thread_db)
        assert len(rows) == 1
        assert rows[0].task_id == "300"
        assert rows[0].thread_id == "600"

    def test_update_path_also_blocked_by_collision_guard(self, isolated_task_thread_db, isolated_collision_log):
        """ADV279D-2 回帰確認: 行が既に存在する更新経路でも、他task_idが使用中の
        thread_idへは上書きしない(従前は新規行作成時にしか門番が掛かっていなかった)。"""
        from app.helpers.task_threads import _set_task_thread
        from app.models import TaskThread

        db = isolated_task_thread_db()
        db.add(TaskThread(task_id="100", thread_id="500", title="taskA-Thread"))
        db.add(TaskThread(task_id="200", thread_id="600", title="taskB-Thread"))
        db.commit()
        db.close()

        # task_id=200 の既存行を、task_id=100 が使用中の thread_id=500 へ
        # 上書きしようとする(何らかの理由で再送/raceが起きた想定)。
        _set_task_thread(200, 500, title="taskB-Thread")

        rows = _rows(isolated_task_thread_db)
        row100 = next(r for r in rows if r.task_id == "100")
        row200 = next(r for r in rows if r.task_id == "200")
        assert row100.thread_id == "500"
        assert row200.thread_id == "600"  # 上書きされていない

    def test_collision_skip_is_logged_for_visibility(self, isolated_task_thread_db, isolated_collision_log):
        """QC279D-1(二) 回帰確認: 書込スキップがJSONLログへ残り、件数として数えられる
        (stderrへ出るだけでは『どれだけ混じっておるか』が誰にも分からなくなる事への手当て)。"""
        from app.helpers.task_threads import get_or_create_task_thread, get_task_thread_collision_stats
        from app.models import TaskThread

        db = isolated_task_thread_db()
        db.add(TaskThread(task_id="100", thread_id="500", title="taskA-Thread"))
        db.commit()
        db.close()

        client = MagicMock()
        client.post_dm_thread.return_value = {"thread_id": 500}

        get_or_create_task_thread(
            client, actor_id="42", task_id=200, participant_ids=[1, 2],
            title="taskB-Thread",
        )

        stats = get_task_thread_collision_stats()
        assert stats is not None
        assert stats["count"] == 1
        assert stats["task_ids"] == ["200"]

    def test_collision_stats_empty_when_no_skips(self, isolated_task_thread_db, isolated_collision_log):
        """スキップが一度も起きていなければ件数0(集計不能のNoneとは区別する)。"""
        from app.helpers.task_threads import get_task_thread_collision_stats

        stats = get_task_thread_collision_stats()
        assert stats == {"count": 0, "task_ids": []}
