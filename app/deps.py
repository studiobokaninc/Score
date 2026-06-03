from typing import Generator

from fastapi import Cookie, Header, HTTPException

from app.database import SessionLocal


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_actor_id(
    authorization: str | None = Header(default=None),
    score_token: str | None = Cookie(default=None, alias="score_token"),
) -> str:
    from app.adapters.calendar_factory import get_calendar_client
    from app.routers.bff import _extract_jwt_sub
    import httpx as _httpx

    auth = authorization or (f"Bearer {score_token}" if score_token else None)
    jwt_sub = _extract_jwt_sub(auth)
    client = get_calendar_client()
    # Calendar 接続失敗時の resilience: retry 1 回 + 503 で fail-friendly
    actor_id = None
    for _attempt in range(2):
        try:
            actor_id = client.resolve_email_to_user_id(jwt_sub)
            break
        except (_httpx.ConnectError, _httpx.ReadTimeout, _httpx.RequestError):
            if _attempt == 0:
                continue  # 1 回 retry
            # 2 回失敗 → Calendar BE 不調として 503 (500 より明示的)
            raise HTTPException(status_code=503, detail="Calendar BE 接続失敗 — 暫く待って再試行してください")
        except Exception:
            raise
    if actor_id is None:
        raise HTTPException(status_code=403, detail="User not found in Calendar")
    return str(actor_id)


# ===== ScoreUserRole 暫定 mapping (2026-05-27 nibu 殿御回答経由) =====
# Calendar 側 User.role は 公式合意書 §3.1 で 'admin' / 'user' の 2 択のみ。
# Score 側 業務ロール (lead / director / pm / lighting_lead / compositor) は
# ScoreUserRole 多対多テーブル (発注書 2026-05-14 §3.2) で管理する設計だが、
# 本式 DB 化(F-3)までの暫定として Python dict で email → score_role mapping を保持する。
# 将来: Score 側に score_user_roles テーブル新設 + Calendar.email join で書換予定。
SCORE_ROLE_MAP: dict[str, str] = {
    "ryoji@studiobokan.com":  "lead",            # 殿 / Lead (部署 oversight)
    "tanaka@studiobokan.com": "pm",              # PM
    "yamada@studiobokan.com": "director",        # Director
    "kato@studiobokan.com":   "lighting_lead",   # Lighting Lead
    "sato@studiobokan.com":   "user",            # Compositor
    "suzuki@studiobokan.com": "user",            # Compositor
}


def get_actor_role(actor_id: str) -> str:
    """Score 内業務ロールを返す。
    優先順位:
      1. SCORE_ROLE_MAP に email 一致あれば その score_role を返す (lead/director/pm/lighting_lead 等)
      2. なければ Calendar User.role (admin/user) を fallback で返す
      3. エラー時は 'user' fallback
    """
    try:
        from app.adapters.calendar_factory import get_calendar_client
        client = get_calendar_client()
        user = client.get_me(actor_user_id=actor_id)
        # email mapping 優先 (Score 業務ロール)
        email = (getattr(user, "email", "") or "").lower()
        if email in SCORE_ROLE_MAP:
            return SCORE_ROLE_MAP[email]
        # fallback: Calendar User.role (admin/user)
        return user.role if user and user.role else "user"
    except Exception:
        return "user"
