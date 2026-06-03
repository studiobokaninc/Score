from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

import httpx

from app.adapters.calendar_factory import get_calendar_client
from app.auth import verify_jwt

router = APIRouter()


def _extract_jwt_sub(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    payload = verify_jwt(token)
    return str(payload["sub"])


@router.get("/api/bff/cross/projects")
def bff_cross_projects(authorization: str | None = Header(default=None)):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found")
    try:
        projects = client.get_my_projects(actor_id)
    except httpx.ConnectError:
        projects = []
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    try:
        shots = client.get_my_shots(actor_id)
    except httpx.ConnectError:
        shots = []
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    return JSONResponse(
        content={"projects": projects, "shots": shots},
        headers={"X-Actor-User-Id": jwt_sub},
    )


@router.get("/api/bff/cross/production-tracker/{project_id}")
def bff_cross_production_tracker(
    project_id: str,
    authorization: str | None = Header(default=None),
):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found")
    try:
        tracker = client.get_production_tracker(project_id, actor_id)
    except httpx.ConnectError:
        tracker = {}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=str(exc))
    return JSONResponse(
        content={"project_id": project_id, "tracker": tracker},
        headers={"X-Actor-User-Id": jwt_sub},
    )
