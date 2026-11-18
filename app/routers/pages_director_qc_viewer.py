import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_project_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")

@router.get("/director_qc_viewer")
def get_director_qc_viewer(request: Request, actor_id: str = Depends(get_actor_id)):
    client = get_calendar_client()
    # cmd_378: 役職は「その案件での役職」で解く。本画面が出す案件(自分の案件の先頭)で
    # director/pm(案件 PM は案件内で admin 同等)/admin のみ通す。案件が解けねば fail-closed。
    try:
        projects = client.get_my_projects(actor_user_id=actor_id)
        pid = projects[0]["id"] if projects else None
    except Exception:
        projects, pid = [], None
    role = get_actor_project_role(actor_id, pid, client=client)
    if role not in ("director", "pm", "admin"):
        raise HTTPException(status_code=403, detail="director role required")
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        shots = client.get_shots(pid if pid is not None else 33, actor_user_id=actor_id)
    except Exception:
        shots = []
    return _templates.TemplateResponse(
        request=request, name="director_qc_viewer.html",
        context={"role": role, "active": "director_qc_viewer", "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1", "user": user, "shots": shots},
    )
