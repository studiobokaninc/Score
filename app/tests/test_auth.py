import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET", "test_secret_key")

from app.auth import (
    create_score_token,
    get_actor_user_id,
    get_next_5am_jst,
    verify_jwt,
)

JST = timezone(timedelta(hours=9))
SECRET = "test_secret_key"


def _make_token(sub: str, exp: datetime) -> str:
    return jwt.encode({"sub": sub, "exp": exp}, SECRET, algorithm="HS256")


@pytest.fixture(autouse=True)
def patch_jwt_secret_auth(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)


class TestVerifyJwt:
    def test_verify_valid_jwt(self):
        exp = datetime.now(timezone.utc) + timedelta(hours=1)
        token = _make_token("sato@studio.jp", exp)
        payload = verify_jwt(token)
        assert payload["sub"] == "sato@studio.jp"

    def test_verify_expired_jwt(self):
        exp = datetime.now(timezone.utc) - timedelta(seconds=1)
        token = _make_token("sato@studio.jp", exp)
        with pytest.raises(HTTPException) as exc_info:
            verify_jwt(token)
        assert exc_info.value.status_code == 401


class TestGetNext5amJst:
    def test_get_next_5am_before_5am(self):
        # 現在 03:00 JST → 本日 05:00 JST を返す
        fake_now = datetime(2026, 5, 16, 3, 0, 0, tzinfo=JST)
        with patch("app.auth.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = get_next_5am_jst()
        expected = datetime(2026, 5, 16, 5, 0, 0, tzinfo=JST)
        assert result == expected

    def test_get_next_5am_after_5am(self):
        # 現在 06:00 JST → 翌日 05:00 JST を返す
        fake_now = datetime(2026, 5, 16, 6, 0, 0, tzinfo=JST)
        with patch("app.auth.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            result = get_next_5am_jst()
        expected = datetime(2026, 5, 17, 5, 0, 0, tzinfo=JST)
        assert result == expected


class TestActorUserId:
    def test_actor_user_id_with_override(self):
        result = get_actor_user_id("sato@studio.jp", override_id="tanaka@studio.jp")
        assert result == "tanaka@studio.jp"

    def test_actor_user_id_without_override(self):
        result = get_actor_user_id("sato@studio.jp")
        assert result == "sato@studio.jp"
