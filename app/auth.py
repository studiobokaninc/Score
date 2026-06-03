import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException

JST = timezone(timedelta(hours=9))


def _get_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    return secret


def verify_jwt(token: str) -> dict:
    """Verify HS256 JWT and return payload. Raises HTTPException(401) on failure."""
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_next_5am_jst() -> datetime:
    """Return the next 05:00 JST as a UTC-aware datetime."""
    now_jst = datetime.now(JST)
    today_5am_jst = now_jst.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_jst < today_5am_jst:
        return today_5am_jst
    return today_5am_jst + timedelta(days=1)


def create_score_token(email: str) -> str:
    """Create a JWT for email with exp = next 05:00 JST."""
    exp = get_next_5am_jst()
    payload = {"sub": email, "exp": exp}
    return jwt.encode(payload, _get_secret(), algorithm="HS256")


def get_actor_user_id(jwt_sub: str, override_id: str | None = None) -> str:
    """Return override_id if given, otherwise jwt_sub (the X-Actor-User-Id value)."""
    if override_id is not None:
        return override_id
    return jwt_sub
