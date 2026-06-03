import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id

router = APIRouter()
_templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader("app/templates"), cache_size=0)
)


@router.get("/cross/projects")
def read_cross_projects(
    request: Request,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    try:
        projects = client.get_my_projects(actor_user_id=actor_id)
    except httpx.ConnectError:
        projects = []
    try:
        shots = client.get_my_shots(actor_user_id=actor_id)
    except httpx.ConnectError:
        shots = []

    return _templates.TemplateResponse(
        request=request,
        name="cross_projects.html",
        context={"projects": projects, "shots": shots},
    )
