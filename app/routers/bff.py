from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.adapters.calendar_factory import get_calendar_client
from app.auth import verify_jwt
from app.deps import get_actor_id, get_db
from app.models import ScoreUserRole

router = APIRouter()


def _extract_jwt_sub(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    payload = verify_jwt(token)
    return str(payload["sub"])


@router.get("/api/bff/me")
def get_me(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    jwt_sub = _extract_jwt_sub(authorization)
    db.query(ScoreUserRole).filter(ScoreUserRole.user_id == jwt_sub).all()
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    user = client.get_me(actor_user_id=actor_id_str)
    return JSONResponse(
        content=asdict(user),
        headers={"X-Actor-User-Id": jwt_sub},
    )


@router.get("/api/bff/shots")
def get_shots(
    project_id: int,
    authorization: str | None = Header(default=None),
):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    shots = client.get_shots(project_id, actor_user_id=actor_id_str)
    return JSONResponse(
        content=[asdict(s) for s in shots],
        headers={"X-Actor-User-Id": jwt_sub},
    )


@router.get("/api/bff/users")
def get_all_users(
    actor_id: str = Depends(get_actor_id),
):
    """Calendar 全ユーザ一覧 (admin JWT) — 送信先セレクトの全ユーザー表示用"""
    client = get_calendar_client()
    users = client.get_users(actor_user_id=actor_id) or []
    return JSONResponse(
        content=[
            {"id": u.get("id"), "name": u.get("name") or u.get("email", ""), "email": u.get("email", "")}
            for u in users if isinstance(u, dict)
        ]
    )


@router.get("/api/bff/shots/{id}/tasks")
def get_shot_tasks(
    id: int,
    authorization: str | None = Header(default=None),
):
    jwt_sub = _extract_jwt_sub(authorization)
    client = get_calendar_client()
    actor_id = client.resolve_email_to_user_id(jwt_sub)
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    actor_id_str = str(actor_id)
    tasks = client.get_tasks(id, actor_user_id=actor_id_str)
    return JSONResponse(
        content=[asdict(t) for t in tasks],
        headers={"X-Actor-User-Id": jwt_sub},
    )
