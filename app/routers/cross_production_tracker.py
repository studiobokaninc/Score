from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.adapters.calendar_factory import get_calendar_client
from app.deps import get_actor_id

router = APIRouter()
_templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader("app/templates"), cache_size=0)
)


@router.get("/cross/production-tracker/{project_id}")
def read_cross_production_tracker(
    project_id: str,
    request: Request,
    actor_id: str = Depends(get_actor_id),
):
    client = get_calendar_client()
    tracker = client.get_production_tracker(project_id=project_id, actor_user_id=actor_id)

    return _templates.TemplateResponse(
        request=request,
        name="cross_production_tracker.html",
        context={"project_id": project_id, "tracker": tracker},
    )
