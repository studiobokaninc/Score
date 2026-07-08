import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client
from app.helpers.task_status import RECEPTION_PENDING_STATUSES

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")

@router.get("/pm_dashboard")
def get_pm_dashboard(request: Request, actor_id: str = Depends(get_actor_id)):
    # 殿御命 2026-06-01: 統合 dashboard 化・本 route は /dashboard へ redirect
    return RedirectResponse(url="/dashboard", status_code=302)
    role = get_actor_role(actor_id)
    if role != "pm":
        raise HTTPException(status_code=403, detail="pm role required")
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        projects = client.get_my_projects(actor_user_id=actor_id)
    except Exception:
        projects = []

    # 受領待ち成果物 = 全 project の全 shot の tasks で判定待ち (qc/v1qc/dir_wt) を集計
    # qc_fb/ap_fb (再修正中) は受領待ちではないため含めない
    pending = []
    try:
        for proj in projects:
            pid = proj.get("id")
            shots = client.get_shots(pid, actor_user_id=actor_id) or []
            for shot in shots:
                tasks = client.get_tasks(shot.shot_id, actor_user_id=actor_id) or []
                for t in tasks:
                    if t.status in RECEPTION_PENDING_STATUSES:
                        pending.append({
                            "task_id": t.task_id,
                            "shot_id": shot.shot_id,
                            "shot_code": shot.shot_code or shot.name,
                            "task_type": t.type,
                            "status": t.status,
                            "assignee_id": t.assignee_id,
                            "project_name": proj.get("name") or "-",
                        })
    except Exception:
        pending = []

    # 未読メッセージ count (state.messages 全件・unread の概念は schema 未実装ゆえ全件で代用)
    try:
        messages = client.get_messages(actor_user_id=actor_id) or []
    except Exception:
        messages = []

    return _templates.TemplateResponse(
        request=request, name="pm_dashboard.html",
        context={
            "role": role, "active": "pm_dashboard",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user, "projects": projects,
            "pending_deliverables": pending,
            "messages": messages,
            "unread_count": len(messages),
        },
    )
