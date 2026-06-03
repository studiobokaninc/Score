import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")

@router.get("/director_qc_viewer")
def get_director_qc_viewer(request: Request, actor_id: str = Depends(get_actor_id)):
    role = get_actor_role(actor_id)
    if role != "director":
        raise HTTPException(status_code=403, detail="director role required")
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        projects = client.get_my_projects(actor_user_id=actor_id)
        pid = projects[0]["id"] if projects else 33
        shots = client.get_shots(pid, actor_user_id=actor_id)
    except Exception:
        shots = []
    return _templates.TemplateResponse(
        request=request, name="director_qc_viewer.html",
        context={"role": role, "active": "director_qc_viewer", "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1", "user": user, "shots": shots},
    )
