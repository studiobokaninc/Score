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


def _get_service_secret() -> str:
    """cmd_172: 外部の道具(Casper等)用サービス JWT の検証/署名鍵。利用者用
    JWT_SECRET とは別の環境変数(SCORE_SERVICE_JWT_SECRET)に分離する——
    軍師QC172A-2(鍵種別とtypクレームの整合)の前提であり、片方の鍵が漏洩しても
    もう一方の信頼機構へ被害が及ばないようにするため。"""
    secret = os.environ.get("SCORE_SERVICE_JWT_SECRET")
    if not secret:
        raise RuntimeError("SCORE_SERVICE_JWT_SECRET is not set")
    return secret


def verify_jwt(token: str) -> dict:
    """Verify HS256 JWT and return payload. Raises HTTPException(401) on failure."""
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def verify_service_jwt(token: str) -> dict:
    """cmd_172: サービス専用シークレットで HS256 JWT を検証する。利用者用
    verify_jwt とは別鍵(_get_service_secret)を用いる。失敗時 401(呼び出し元の
    app.deps._decode_actor_token が利用者鍵での検証失敗後にこちらを試す)。"""
    try:
        payload = jwt.decode(token, _get_service_secret(), algorithms=["HS256"])
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


def get_business_day_window_utc() -> tuple[datetime, datetime]:
    """cmd_087: 現在の「業務日」ウィンドウ (5am JST 境界, score_token 失効/get_next_5am_jst と
    同一基準) を naive UTC datetime の (start, end) で返す。SQLite の DateTime 列 (naive UTC
    保存) との比較に使う。"""
    end_jst = get_next_5am_jst()
    start_jst = end_jst - timedelta(days=1)
    return (
        start_jst.astimezone(timezone.utc).replace(tzinfo=None),
        end_jst.astimezone(timezone.utc).replace(tzinfo=None),
    )


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


def create_service_token(client_id: str = "casper", exp_days: int = 365) -> str:
    """cmd_172②c: 外部の道具(Casper等)用サービス JWT を発行する。
    email・個人属性は一切積まない(admin login フロー・SCORE_ROLE_MAP・
    Calendar 側の役職解決を一切経由しない身元とするため)。client_id は
    どのサービス資格かを示す識別子(記録機構⑥用途)であり、実在の個人を
    指すものではない。実運用での発行はこの関数を ops が手動で1回呼ぶ形を
    想定する(発行専用の API エンドポイントは新設しない・攻撃対象面を
    増やさないため)。"""
    exp = datetime.now(timezone.utc) + timedelta(days=exp_days)
    payload = {"typ": "service", "cid": client_id, "exp": exp}
    return jwt.encode(payload, _get_service_secret(), algorithm="HS256")
