"""cmd_156 (2026-07-31・殿御命): スレッドの単位を『宛先の組み合わせ』から『タスク』へ
改める。1 task_id につき 1 thread_id を Score 自身のDB(task_threads テーブル)で
正本管理し、宛先の顔ぶれが異なっても同一タスクの会話は同一スレッドに集約する。
外部Calendar API(post_dm_thread)がtask_id単位で重複排除しているかは不明/検証不能
(Score repoの管轄外)のため、Score側で信頼できる索引を持つ。
★DBアクセスは全てtry/exceptで保護する(既存のQcDelegation書込パターン踏襲・
bff_write.py参照)。このテーブルへの読み書き失敗が、通知送信という既存の重要機能
(cmd_149/151)を巻き込んで500にしてはならない — 失敗時は「これまで通り毎回
post_dm_threadする」旧動作にfallbackする。"""
import json
import sys
from datetime import datetime
from pathlib import Path

from app.database import SessionLocal
from app.models import TaskThread

# cmd_279丁 (2026-09-30・QC279D-1(二)是正): 衝突スキップの可視化ログ置き場。
# score.db(SQLite)へ新規テーブルを足す案は退けた — 現況、このdev機の score.db が
# root所有で非root(通常の起動ユーザー)から書込不可であることを実機確認した
# (uploads/ が root所有で書込不可だった旨と同種・app.gitignore の asset_uploads/
# 註記(subtask_258a)参照)。Base.metadata.create_all は app.main import時に毎回
# 走るため、新規テーブルを1つ足しただけで score.db を触るあらゆる試験が
# 一斉に壊れる(実機確認済)。DBへ触れぬ単純な追記専用ファイルで代替する。
_COLLISION_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "task_thread_collision_log.jsonl"


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


def _log_collision_skip(task_id, thread_id, bound_task_id) -> None:
    """QC279D-1(二) (cmd_279丁・2026-09-30): 書込スキップがstderrへ出るだけでは
    「どれだけ混じっておるか」を誰も数えられない。追記専用のJSONLファイルへ
    1件1行で残す(集計は get_task_thread_collision_stats 参照)。この可視化記録
    自体の失敗が本来のスキップ判断(=フォールバック継続)を妨げてはならない
    ため、fail-softする。"""
    try:
        _COLLISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.utcnow().isoformat(),
            "task_id": str(task_id),
            "thread_id": str(thread_id),
            "bound_task_id": str(bound_task_id),
        }
        with _COLLISION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[task_threads] collision log write failed: {e}", file=sys.stderr)


def get_task_thread_collision_stats() -> dict | None:
    """QC279D-1(二): 見える化用ヘルパー。累計件数と対象task_id一覧を返す。
    ログファイルが未生成(=スキップ0件)の場合は0件として返す。読み取り自体が
    失敗した場合のみNoneを返す(『0件』と『集計不能』を取り違えぬよう区別する)。
    ★件数をどこかへ言上する前に、この帳面が試験由来の噪(isolated_collision_log
    未使用の試験が本物のファイルへ書き込んでしまった記録)を含んでいないことを
    一度確かめよ(cmd_279丁2・2026-09-30の教訓。過去の混入分は
    task_thread_collision_log_test_noise_20260930.jsonl へ退避済み)。"""
    if not _COLLISION_LOG_PATH.exists():
        return {"count": 0, "task_ids": []}
    try:
        task_ids = set()
        count = 0
        with _COLLISION_LOG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                count += 1
                task_ids.add(record["task_id"])
        return {"count": count, "task_ids": sorted(task_ids)}
    except Exception as e:
        print(f"[task_threads] get_task_thread_collision_stats skip: {e}", file=sys.stderr)
        return None


def _set_task_thread(task_id, thread_id, title=None) -> None:
    try:
        db = SessionLocal()
        try:
            # cmd_279乙/丁 (2026-09-30): 外部Calendar API(post_dm_thread)がtask_id単位で
            # 重複排除している保証はない(本ファイル冒頭docstring参照)。参加者
            # (participant_ids)の顔ぶれが同一な別taskへ、外部側が既存threadを使い
            # 回して返す実例を確認済(dev DB: thread_id=10000007がtask_id 3255/
            # 3256/3282/3327の4件に跨っていた)。この thread_id が既に「別の」
            # task_id で使用済みなら、正本表へこのtask専用のthreadとして書き込む
            # こと自体が「無関係な案件が一本のスレッドを分け合う」新規発生を生む
            # ため、新規行はもちろん既存行の上書きでも同じ門番を通し、
            # 書き込まずスキップする(通知送信自体は継続・次回も毎回
            # post_dm_thread する旧動作にfallbackするだけで安全)。ADV279D-2:
            # 従前はこの門番が新規行の作成時にしか掛かっておらず、既存行の
            # 更新経路だけが素通りしていたため、両経路とも同じチェックを
            # 通すよう揃えた。
            dup = db.query(TaskThread).filter(
                TaskThread.thread_id == str(thread_id),
                TaskThread.task_id != str(task_id),
            ).first()
            if dup:
                print(
                    f"[task_threads] _set_task_thread skip: thread_id={thread_id} "
                    f"already bound to task_id={dup.task_id} (task_id={task_id} not persisted)",
                    file=sys.stderr,
                )
                _log_collision_skip(task_id, thread_id, dup.task_id)
                return
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
