import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client
from app.helpers.task_status import STATUS_PRIORITY, COMPLETED_STATUSES, attach_status_meta

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")


# Priority order: open tasks needing attention first
# cmd_075 (2026-07-08): TaskStatus 新19値対応 — STATUS_PRIORITY/COMPLETED_STATUSES へ集約
_TASK_PRIORITY = STATUS_PRIORITY
_COMPLETED_STATUSES = COMPLETED_STATUSES


def _enrich_my_tasks(client, actor_id: str) -> list[dict]:
    """Fetch my_tasks and decorate with project_name / seq_code / shot_code."""
    try:
        raw = client.get_my_tasks(actor_user_id=actor_id) or [] if hasattr(client, "get_my_tasks") else []
    except Exception:
        raw = []
    my_tasks: list[dict] = []
    for t in raw:
        if isinstance(t, dict):
            my_tasks.append({
                "task_id": t.get("id") or t.get("task_id"),
                "shot_id": t.get("shot_id"),
                "shot_code": t.get("shot_code") or t.get("name") or "",
                "task_type": t.get("task_type") or t.get("type", ""),
                "name": t.get("name", ""),
                "status": t.get("status", ""),
                "status_color": t.get("status_color"),
                "status_label": t.get("status_label"),
                "status_category": t.get("status_category"),
            })
        else:
            my_tasks.append({
                "task_id": getattr(t, "task_id", None),
                "shot_id": getattr(t, "shot_id", None),
                "shot_code": getattr(t, "name", "") or "",
                "task_type": getattr(t, "type", ""),
                "status": getattr(t, "status", ""),
                "status_color": getattr(t, "status_color", None),
                "status_label": getattr(t, "status_label", None),
                "status_category": getattr(t, "status_category", None),
            })
    # build shot_id → {project_name, seq_code, shot_code} map
    try:
        projects = client.get_my_projects(actor_user_id=actor_id) or []
    except Exception:
        projects = []
    shot_map: dict = {}
    for p in projects:
        pid = p.get("id") if isinstance(p, dict) else getattr(p, "id", None)
        pname = (p.get("name") if isinstance(p, dict) else getattr(p, "name", None)) or "-"
        if pid is None:
            continue
        try:
            shots = client.get_shots(pid, actor_user_id=actor_id) or []
        except Exception:
            shots = []
        for s in shots:
            sid = (s.get("id") if isinstance(s, dict) else getattr(s, "shot_id", None) or getattr(s, "id", None))
            if sid is None:
                continue
            shot_map[sid] = {
                "project_name": pname,
                "shot_code": (s.get("shot_code") if isinstance(s, dict) else getattr(s, "shot_code", None))
                            or (s.get("name") if isinstance(s, dict) else getattr(s, "name", None))
                            or f"SHOT_{int(sid):03d}",
                "seq_code": (s.get("seq_code") if isinstance(s, dict) else getattr(s, "seq_code", None)) or "",
            }
    for t in my_tasks:
        info = shot_map.get(t.get("shot_id"), {})
        t["project_name"] = info.get("project_name", "")
        t["seq_code"] = info.get("seq_code", "")
        if not t.get("shot_code"):
            t["shot_code"] = info.get("shot_code") or (
                f"SHOT_{int(t['shot_id']):03d}" if t.get("shot_id") else ""
            )
    return my_tasks


def _has_prev_day_exit_submitted(client, actor_id: str) -> bool:
    """殿御命 2026-06-05 (#25): 前 1 業務日 (JST 5am-5am 区切り) の退勤 (clock_out) 提出済 check.

    timecard shape (nibu 殿納品 2026-06-01): {id, user_id, date, clock_out_at, worked_minutes, break_minutes, memo}
      - `type` field は存在しない (旧 logic は type='clock_out' filter で全 record skip していたバグ)
    業務日定義: 5:00 JST 〜 翌 5:00 JST。 例: 5am 前にログインなら 業務日 = 前日付。
    判定: 前業務日 もしくは 当業務日 で clock_out_at が記録されていれば True。
    content check: memo (退勤メモ) もしくは worked_minutes > 0 を伴うものを優先するが、
                  clock_out_at の存在のみでも valid (memo は任意仕様)。
    """
    from datetime import datetime, timedelta, timezone
    if not hasattr(client, "get_timecards"):
        return False
    try:
        tcs = client.get_timecards(actor_user_id=actor_id) or []
    except Exception:
        return False
    JST = timezone(timedelta(hours=9))
    now_jst = datetime.now(JST)
    # JST 5am 業務日 boundary: 5am 前は 前日付の業務日
    if now_jst.hour < 5:
        biz_today = (now_jst - timedelta(days=1)).date()
    else:
        biz_today = now_jst.date()
    biz_yesterday = biz_today - timedelta(days=1)
    for t in tcs:
        if not isinstance(t, dict):
            continue
        # 業務日 = date field 優先・無ければ clock_out_at の日付部
        d_raw = t.get("date") or ((t.get("clock_out_at") or "")[:10])
        if not d_raw:
            continue
        try:
            d = datetime.strptime(str(d_raw)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if d != biz_yesterday and d != biz_today:
            continue
        # content check: clock_out_at 必須 (打刻あり)
        if not t.get("clock_out_at"):
            continue
        return True
    return False


@router.get("/routine")
def get_routine(request: Request, actor_id: str = Depends(get_actor_id)):
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None

    # 「本日やるべきタスク」を priority 順で組み立て
    my_tasks = _enrich_my_tasks(client, actor_id)
    # 殿御命 2026-06-05: 完了 enum 一括除外 (approved/completed/complete/done/完了)
    today_tasks = [t for t in my_tasks if (t.get("status") or "").lower() not in _COMPLETED_STATUSES]

    # 殿御命 2026-06-05: 受信 QC/Review 依頼を「本日のタスク」先頭に統合 (dashboard と同様)
    try:
        if hasattr(client, "get_my_dm_threads"):
            qc_inbox = []
            for _thr in (client.get_my_dm_threads(actor_user_id=actor_id) or []):
                if not isinstance(_thr, dict):
                    continue
                lm = (_thr.get("last_message") or "").strip()
                if not lm:
                    continue
                first_line = lm.split("\n")[0]
                if not (first_line.startswith("🔍 QC 依頼") or first_line.startswith("📌 Review 依頼")):
                    continue
                lines = lm.split("\n")
                title = lines[1] if len(lines) > 1 else ""
                parts = [p.strip() for p in title.split("/")]
                qc_inbox.append({
                    "task_id": None,
                    "thread_id": _thr.get("thread_id"),
                    "shot_id": None,
                    "shot_code": parts[2] if len(parts) >= 3 else title,
                    "seq_code": parts[1] if len(parts) >= 2 else "",
                    "task_type": parts[3] if len(parts) >= 4 else ("Review" if first_line.startswith("📌") else "QC"),
                    "status": "qc_inbox" if first_line.startswith("🔍") else "review_inbox",
                    "priority": "",
                    "project_id": None,
                    "project_name": parts[0] if len(parts) >= 1 else "",
                    "due_date": "",
                    "is_qc_inbox": True,
                    "kind": "qc" if first_line.startswith("🔍") else "review",
                    "updated_at": _thr.get("updated_at"),
                })
            qc_inbox.sort(key=lambda x: x.get("updated_at",""), reverse=True)
            today_tasks = qc_inbox + today_tasks  # 受信 QC を 先頭に挿入
    except Exception:
        pass

    today_tasks.sort(key=lambda x: (0 if x.get("is_qc_inbox") else 1, _TASK_PRIORITY.get((x.get("status") or "").lower(), 5)))
    today_tasks = attach_status_meta(today_tasks, client)  # cmd_075: status_color/status_label 動的付与

    prev_exit_submitted = _has_prev_day_exit_submitted(client, actor_id)

    return _templates.TemplateResponse(
        request=request, name="routine.html",
        context={
            "role": role,
            "active": "routine",
            "user": user,
            "today_tasks": today_tasks,
            "prev_exit_submitted": prev_exit_submitted,
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
        },
    )
