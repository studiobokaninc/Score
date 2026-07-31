"""cmd_156 (2026-07-31・殿御命): スレッドの単位を『宛先の組み合わせ』から『タスク』へ
改める。1 task_id につき 1 thread_id を Score 自身のDB(task_threads テーブル)で
正本管理し、宛先の顔ぶれが異なっても同一タスクの会話は同一スレッドに集約する。
外部Calendar API(post_dm_thread)がtask_id単位で重複排除しているかは不明/検証不能
(Score repoの管轄外)のため、Score側で信頼できる索引を持つ。
★DBアクセスは全てtry/exceptで保護する(既存のQcDelegation書込パターン踏襲・
bff_write.py参照)。このテーブルへの読み書き失敗が、通知送信という既存の重要機能
(cmd_149/151)を巻き込んで500にしてはならない — 失敗時は「これまで通り毎回
post_dm_threadする」旧動作にfallbackする。"""
import sys
from datetime import datetime

from app.database import SessionLocal
from app.models import TaskThread


def build_thread_title(proj_name, seq_code, shot_code, task_type, task_id=None) -> str:
    """project-shot-cut-task-Thread 形式のタイトルを構成する (cmd_156②)。
    ★構成要素が欠けている場合、その要素は単に詰めて省く(捏造値で埋めない)。
    全要素が欠けている場合のみ task_id ベースの最低限の識別名にfallbackする。"""
    parts = [p for p in (proj_name, seq_code, shot_code, task_type) if p]
    if not parts:
        return f"task{task_id}-Thread" if task_id else "Thread"
    return "-".join(str(p) for p in parts) + "-Thread"


def get_task_thread_id(task_id) -> str | None:
    """このtaskに既存のthreadがあればthread_idを返す。無ければ(または取得失敗時)None。"""
    if task_id is None:
        return None
    try:
        db = SessionLocal()
        try:
            row = db.query(TaskThread).filter(TaskThread.task_id == str(task_id)).first()
            return row.thread_id if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[task_threads] get_task_thread_id skip: {e}", file=sys.stderr)
        return None


def get_task_thread_title(task_id) -> str | None:
    if task_id is None:
        return None
    try:
        db = SessionLocal()
        try:
            row = db.query(TaskThread).filter(TaskThread.task_id == str(task_id)).first()
            return row.title if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[task_threads] get_task_thread_title skip: {e}", file=sys.stderr)
        return None


def get_all_task_threads() -> dict:
    """thread_id → title の辞書を返す(messages.html一覧描画で一括参照する用)。
    取得失敗時は空dict(全threadが従来通り宛先名表示にfallbackするだけで安全)。"""
    try:
        db = SessionLocal()
        try:
            rows = db.query(TaskThread).all()
            return {row.thread_id: row.title for row in rows if row.title}
        finally:
            db.close()
    except Exception as e:
        print(f"[task_threads] get_all_task_threads skip: {e}", file=sys.stderr)
        return {}


def _set_task_thread(task_id, thread_id, title=None) -> None:
    try:
        db = SessionLocal()
        try:
            row = db.query(TaskThread).filter(TaskThread.task_id == str(task_id)).first()
            if row:
                row.thread_id = str(thread_id)
                if title and not row.title:
                    row.title = title
                row.updated_at = datetime.utcnow()
            else:
                row = TaskThread(task_id=str(task_id), thread_id=str(thread_id), title=title)
                db.add(row)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[task_threads] _set_task_thread skip: {e}", file=sys.stderr)


def get_or_create_task_thread(client, actor_id, task_id, participant_ids, title=None) -> str | None:
    """このtaskの正本スレッドを返す。既存なければ外部Calendar APIへpost_dm_threadで新規作成し、
    Score側のtask_threadsに記録する。task_id が None (task非紐付の一般DM等) の場合、または
    Score側DBの読み書きに失敗した場合は、毎回 post_dm_thread する旧動作にfallbackする
    (このヘルパーの不調で通知送信自体が止まってはならないため)。"""
    if task_id is None or not hasattr(client, "post_dm_thread") or len(participant_ids) < 2:
        return None
    existing = get_task_thread_id(task_id)
    if existing:
        # DB には str で保存しているが、呼び出し側(_notify_thread)は int(thread_id) を
        # 前提にしている箇所があるため、数値ならint化して返し既存呼出元との型不整合を防ぐ。
        try:
            return int(existing)
        except (TypeError, ValueError):
            return existing
    thread_resp = client.post_dm_thread(participant_ids=participant_ids, task_id=task_id, actor_user_id=actor_id)
    thread_id = thread_resp.get("thread_id") or thread_resp.get("id")
    if thread_id:
        _set_task_thread(task_id, thread_id, title=title)
    return thread_id
