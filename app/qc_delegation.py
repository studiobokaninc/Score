"""殿御命 2026-06-09 (案A): QC/Review 委任の判定ヘルパー。

QC/Review 依頼で mention された user は、その『特定依頼 1 件』に限り Approve/Retake 可。
グローバルな role 昇格ではなく依頼単位。依頼が resolved になれば失効。
"""
from app.database import SessionLocal
from app.models import QcDelegation
from app.adapters.calendar_client import _to_calendar_uid


def is_qc_delegated(actor_id, task_id=None, shot_id=None) -> bool:
    """actor が、この task (優先) もしくは shot の OPEN な QC/Review 依頼で
    mention されている (= 委任されている) なら True。"""
    try:
        cuid = _to_calendar_uid(actor_id)
        if cuid is None:
            return False
        token = f",{int(cuid)},"
        db = SessionLocal()
        try:
            q = db.query(QcDelegation).filter(QcDelegation.status == "open")
            if task_id is not None:
                rows = q.filter(QcDelegation.task_id == str(task_id)).all()
            elif shot_id is not None:
                rows = q.filter(QcDelegation.shot_id == str(shot_id)).all()
            else:
                rows = []
            return any(token in (r.mentioned_uids or "") for r in rows)
        finally:
            db.close()
    except Exception:
        return False


def resolve_delegation(task_id=None, shot_id=None) -> int:
    """approve/retake 済になった依頼の委任を resolved にする (権限失効)。更新件数を返す。"""
    try:
        db = SessionLocal()
        try:
            q = db.query(QcDelegation).filter(QcDelegation.status == "open")
            if task_id is not None:
                q = q.filter(QcDelegation.task_id == str(task_id))
            elif shot_id is not None:
                q = q.filter(QcDelegation.shot_id == str(shot_id))
            else:
                return 0
            n = 0
            for r in q.all():
                r.status = "resolved"
                n += 1
            db.commit()
            return n
        finally:
            db.close()
    except Exception:
        return 0
