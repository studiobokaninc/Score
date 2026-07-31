from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint
from app.database import Base


class ScoreUserRole(Base):
    __tablename__ = "score_user_roles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False)
    project_id = Column(String, nullable=False)
    role = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_user_project"),)


class BugReport(Base):
    """バグ報告 — 殿 demo grade。開発 FB 用に蓄積。"""
    __tablename__ = "bug_reports"

    id = Column(Integer, primary_key=True, index=True)
    reporter_user_id = Column(String, nullable=True)    # email or username (resolve_email 経由)
    reporter_name = Column(String, nullable=True)       # 入力時 user.name 記録
    title = Column(String, nullable=False)              # 短い件名
    description = Column(Text, nullable=False)          # 詳細(再現手順含む)
    severity = Column(String, nullable=False, default="medium")  # low/medium/high/critical
    page_url = Column(String, nullable=True)            # 発生 URL (任意)
    operation_log = Column(Text, nullable=True)         # 殿御命 2026-06-09: 直近操作ログ(JSON文字列・再現用)
    user_agent = Column(String, nullable=True)          # 殿御命 2026-06-09: ブラウザ UA (環境切り分け用)
    status = Column(String, nullable=False, default="open")  # open/in_progress/resolved/wontfix
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class QcDelegation(Base):
    """殿御命 2026-06-09 (案A): QC/Review 依頼で mention された user に、その依頼に限り
    Approve/Retake を許可する委任記録。グローバルな role 昇格ではなく『特定依頼 1 件』単位。
    依頼が approve/retake 済になれば status=resolved → 権限自然失効。"""
    __tablename__ = "qc_delegations"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, nullable=True, index=True)
    shot_id = Column(String, nullable=True, index=True)
    asset_id = Column(String, nullable=True)
    submission_type = Column(String, nullable=False, default="qc")   # qc | review
    mentioned_uids = Column(Text, nullable=False, default="")        # Calendar uid の CSV (例 ",52,55,")
    requested_by = Column(String, nullable=True)                     # 依頼発行者 actor
    status = Column(String, nullable=False, default="open")          # open | resolved
    created_at = Column(DateTime, default=datetime.utcnow)


class RoutineLog(Base):
    """殿御命 2026-06-09: 朝ルーティン (出勤時刻 + 体調 condition) を Score 側にも保存。
    Calendar 送信に加え Score DB へ記録し、取りこぼしを防ぐ (表示は後日でもデータは残す)。"""
    __tablename__ = "routine_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)   # Calendar cuid (or actor)
    user_name = Column(String, nullable=True)
    condition = Column(String, nullable=True)             # 体調 (🤩🙂🥱🤒 等)
    date = Column(String, nullable=True, index=True)       # YYYY-MM-DD
    submitted_at = Column(String, nullable=True)           # 出勤時刻 (ISO+TZ)
    created_at = Column(DateTime, default=datetime.utcnow)


class TaskThread(Base):
    """cmd_156 (2026-07-31・殿御命): スレッドの単位を『宛先の組み合わせ』から『タスク』へ
    改めるための正本マッピング。1 task_id につき 1 thread_id を保持し、宛先の顔ぶれが
    異なっても同一タスクの会話は同一スレッド(thread_id)に集約されるようにする。
    Score 自身のDB(外部Calendarサービスとは別)で管理する新規テーブル — 外部Calendar
    APIのスレッド重複排除挙動に依存しないための正本索引。"""
    __tablename__ = "task_threads"

    id = Column(Integer, primary_key=True, index=True)
    task_id = Column(String, nullable=False, unique=True, index=True)
    thread_id = Column(String, nullable=False, index=True)
    title = Column(String, nullable=True)  # project-shot-cut-task-Thread 形式 (cmd_156②)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TimecardLog(Base):
    """殿御命 2026-06-09: 退勤打刻を Score 側にも保存 (Calendar に加え記録保持)。"""
    __tablename__ = "timecard_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    user_name = Column(String, nullable=True)
    date = Column(String, nullable=True, index=True)
    clock_out_time = Column(String, nullable=True)
    mode = Column(String, nullable=True)
    blocker = Column(Text, nullable=True)                  # 当日の課題/詰まり
    handover = Column(Text, nullable=True)                 # 申し送り
    next_priority = Column(Text, nullable=True)            # 翌日の優先
    raw_json = Column(Text, nullable=True)                 # 元 body 全体 (保険)
    created_at = Column(DateTime, default=datetime.utcnow)
