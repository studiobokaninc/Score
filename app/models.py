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
