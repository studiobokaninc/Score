import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)

@router.get("/pm_delivery")
def get_pm_delivery(request: Request, actor_id: str = Depends(get_actor_id)):
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
    return _templates.TemplateResponse(
        request=request, name="pm_delivery.html",
        context={"role": role, "active": "pm", "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1", "user": user, "projects": projects},
    )
