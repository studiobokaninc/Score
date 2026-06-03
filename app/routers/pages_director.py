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

@router.get("/director_retake_input")
def get_director_retake_input(
    request: Request,
    actor_id: str = Depends(get_actor_id),
    shot_id: int | None = None,
    task_id: int | None = None,
):
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
        request=request, name="director_retake_input.html",
        context={
            "role": role, "active": "director",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "shots": shots,
            "shot_id": shot_id or 1,
            "task_id": task_id,
        },
    )
