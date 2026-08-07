"""cmd_172 (2026-08-07・殿ご裁可=案1)フェーズ2(subtask_172b)実装の単体/結合試験。

外部の道具(Casper等)は操作者本人のJWTを持たないため、自らのサービス用資格
(typ="service" JWT・SCORE_SERVICE_JWT_SECRET で署名)でScoreを呼び、操作者本人の
uidを X-Score-Acting-User-Id ヘッダで別途名乗る形を app.deps.get_actor_id に実装した
(設計は subtask_172a・queue/reports/ashigaru3_report.yaml 参照)。

検証項目(a)〜(k)(subtask_172b タスクYAML記載)を本ファイルでカバーする:
  (a)(b)(c) は既存 test_bff_write_qc_authz.py 等の役職ゲート試験で担保済
      (get_actor_project_role は actor_id の由来を問わないため、本ファイルは
      get_actor_id 自体の受理/拒否ロジックに焦点を当てる)。
  (d)(e)(f)(g)(h)(i) get_actor_id の受理/拒否分岐
  (j) 記録機構(ServiceActorOverrideLog)
  (k) 自動化印(_headers())
"""
import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
os.environ.setdefault("SCORE_SERVICE_JWT_SECRET", "test_service_secret_key_32bytes_min!")

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.deps import ACTING_USER_ID_HEADER, get_actor_id, get_actor_role_strict, _decode_actor_token
from app.auth import create_score_token, create_service_token
from app.adapters.dto import CalendarUser

_USER_SECRET = "test_secret_key_32bytes_minimum!"
_SERVICE_SECRET = "test_service_secret_key_32bytes_min!"


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", _USER_SECRET)
    monkeypatch.setenv("SCORE_SERVICE_JWT_SECRET", _SERVICE_SECRET)


_test_app = FastAPI()


@_test_app.get("/whoami")
def _whoami(actor_id: str = Depends(get_actor_id)):
    return {"actor_id": actor_id}


def _client():
    return TestClient(_test_app, follow_redirects=False)


def _mock_user(uid: int, role: str, email: str = "acting@studio.jp") -> CalendarUser:
    return CalendarUser(user_id=uid, email=email, role=role, name="Acting User", icon_url=None)


def _service_headers(acting_uid, token=None) -> dict:
    h = {"Authorization": f"Bearer {token or create_service_token()}"}
    if acting_uid is not None:
        h[ACTING_USER_ID_HEADER] = str(acting_uid)
    return h


# ─── ①1) 通常JWT: 上書きヘッダは黙って無視される ──────────────────────────

def test_normal_user_jwt_ignores_override_header():
    """通常の利用者JWTでは X-Score-Acting-User-Id が紛れ込んでいても無視され、
    従来どおり sub(email)から解決した actor_id が使われる。"""
    token = create_score_token("sato@studio.jp")
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.resolve_email_to_user_id.return_value = 99
        MockFactory.return_value = mock_client
        resp = _client().get(
            "/whoami",
            headers={"Authorization": f"Bearer {token}", ACTING_USER_ID_HEADER: "999999"},
        )
    assert resp.status_code == 200
    assert resp.json()["actor_id"] == "99"  # 999999(上書きヘッダ)ではなく sub 解決結果


# ─── (e) 資格なし → 401 ──────────────────────────────────────────────────

def test_no_credentials_401():
    resp = _client().get("/whoami")
    assert resp.status_code == 401


# ─── (d) 一般の利用者JWT(非サービス)が上書きヘッダを送っても名乗りが効かない ──

def test_untrusted_normal_jwt_with_override_header_not_effective():
    """許していない相手(通常の利用者JWT)が上書きヘッダを送っても、名乗りは採用
    されず、そのJWT本来の身元(sub解決結果)で処理されること。"""
    token = create_score_token("sato@studio.jp")
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.resolve_email_to_user_id.return_value = 7
        MockFactory.return_value = mock_client
        resp = _client().get(
            "/whoami",
            headers={"Authorization": f"Bearer {token}", ACTING_USER_ID_HEADER: "1"},
        )
    assert resp.status_code == 200
    assert resp.json()["actor_id"] == "7"


# ─── (f) 存在しないuidを名乗る → 拒否 ─────────────────────────────────────

def test_service_credential_nonexistent_uid_rejected():
    """get_me が失敗(存在しない uid)する場合、fail-closed で拒否(403)される。"""
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.side_effect = Exception("404 not found")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=555))
    assert resp.status_code == 403


# ─── (g) admin役職に解決されるuidを名乗る → 拒否(最重要・軍師QC172A-1) ──────

def test_service_credential_admin_uid_rejected():
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(1, "admin")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=1))
    assert resp.status_code == 403


def test_service_credential_admin_via_score_role_map_rejected():
    """SCORE_ROLE_MAP経由でadminに解決される場合(ryoji@studiobokan.com)も同様に
    拒否されること(get_actor_role_strict は get_actor_role と同じ優先順位ロジック
    (SCORE_ROLE_MAP優先)を持つ)。"""
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(2, "user", email="ryoji@studiobokan.com")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=2))
    assert resp.status_code == 403


# ─── admin以外(PM/User)への成り代わりは正しく採用される ────────────────────

def test_service_credential_non_admin_uid_accepted():
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(28, "user")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=28))
    assert resp.status_code == 200
    assert resp.json()["actor_id"] == "28"


# ─── サービス資格だが名乗りヘッダが無い場合は401 ────────────────────────────

def test_service_credential_without_header_401():
    resp = _client().get("/whoami", headers=_service_headers(acting_uid=None))
    assert resp.status_code == 401


def test_service_credential_non_numeric_header_rejected():
    resp = _client().get(
        "/whoami",
        headers={"Authorization": f"Bearer {create_service_token()}", ACTING_USER_ID_HEADER: "not-a-number"},
    )
    assert resp.status_code == 403


# ─── (h) Calendar未応答を模したuid → fail-closed拒否(軍師QC172A-3) ─────────

def test_service_credential_calendar_unresponsive_rejected():
    """get_actor_role_strict が例外を伝播させ(fail-open の"user"への一律収束を
    しない)、get_actor_id 側が admin か否か判定できないことを検出して拒否する
    (b)/(c) 区分の弁別確認。"""
    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        import httpx as _httpx
        mock_client.get_me.side_effect = _httpx.ConnectError("connection refused")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=42))
    assert resp.status_code == 403


# ─── (i) 鍵とtypの不整合 → 拒否(軍師QC172A-2) ──────────────────────────────

def test_key_type_mismatch_user_key_claims_service_typ_rejected():
    """利用者用鍵で署名されたトークンが typ="service" を名乗っても、鍵種別との
    不整合として拒否されること(利用者鍵が漏洩しても、サービス側の信頼を
    詐称できないようにする歯止め)。"""
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    forged = jwt.encode({"typ": "service", "exp": exp}, _USER_SECRET, algorithm="HS256")
    resp = _client().get(
        "/whoami",
        headers={"Authorization": f"Bearer {forged}", ACTING_USER_ID_HEADER: "1"},
    )
    assert resp.status_code == 401


def test_key_type_mismatch_service_key_without_service_typ_rejected():
    """サービス用鍵で署名されていても typ が "service" でなければ拒否される
    (サービス鍵が漏洩しても、通常の利用者トークンとして扱われない歯止め)。"""
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    forged = jwt.encode({"sub": "attacker@studio.jp", "exp": exp}, _SERVICE_SECRET, algorithm="HS256")
    resp = _client().get("/whoami", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


def test_invalid_token_401():
    resp = _client().get("/whoami", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


# ─── _decode_actor_token 直接試験(境界値の明確化) ──────────────────────────

class TestDecodeActorToken:
    def test_user_token_decodes_as_user(self):
        token = create_score_token("sato@studio.jp")
        payload, key_kind = _decode_actor_token(f"Bearer {token}")
        assert key_kind == "user"
        assert payload["sub"] == "sato@studio.jp"

    def test_service_token_decodes_as_service(self):
        token = create_service_token(client_id="casper")
        payload, key_kind = _decode_actor_token(f"Bearer {token}")
        assert key_kind == "service"
        assert payload["typ"] == "service"
        assert payload["cid"] == "casper"

    def test_missing_auth_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _decode_actor_token(None)
        assert exc_info.value.status_code == 401


# ─── get_actor_role_strict 直接試験 ─────────────────────────────────────────

class TestGetActorRoleStrict:
    def test_propagates_exception_instead_of_swallowing(self):
        """既存 get_actor_role は例外を "user" に握り潰すが、strict版は伝播させる
        (fail-open→fail-closed への転用に必要な性質)。"""
        mock_client = MagicMock()
        mock_client.get_me.side_effect = RuntimeError("calendar down")
        with pytest.raises(RuntimeError):
            get_actor_role_strict("1", client=mock_client)

    def test_returns_role_from_score_role_map(self):
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(1, "user", email="tanaka@studiobokan.com")
        assert get_actor_role_strict("1", client=mock_client) == "pm"

    def test_returns_calendar_role_fallback(self):
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(1, "user", email="nobody@example.com")
        assert get_actor_role_strict("1", client=mock_client) == "user"


# ─── (j) 記録機構: 名乗り採用時に監査ログが実際に書き込まれること ──────────

@pytest.fixture(autouse=True)
def isolated_audit_db(monkeypatch):
    """本物の score.db を汚さないよう、専用の in-memory SQLite に差し替える
    (test_pages_routine.py の isolated_routine_db と同型)。★app.deps は
    `from app.database import SessionLocal` でモジュール importステップ時に
    参照を保持するため、app.database.SessionLocal ではなく app.deps.SessionLocal
    自体を差し替える必要がある。★autouse=True: 名乗り採用が成功する経路の
    テストは本fixtureを明示要求せずとも監査ログ書込みが発生しうる
    (test_service_credential_non_admin_uid_accepted 等)。個別テストの要求漏れで
    本物の score.db に試験データが混入するのを防ぐため、本ファイル全体へ
    無条件適用する(実機汚染事故の再発防止・2026-08-07実機検証時に発覚)。"""
    from app.database import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.deps.SessionLocal", TestSessionLocal)
    return TestSessionLocal


def test_service_override_writes_audit_record(isolated_audit_db):
    from app.models import ServiceActorOverrideLog

    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(28, "user")
        MockFactory.return_value = mock_client
        resp = _client().get(
            "/whoami",
            headers=_service_headers(acting_uid=28, token=create_service_token(client_id="casper")),
        )
    assert resp.status_code == 200

    db = isolated_audit_db()
    try:
        rows = db.query(ServiceActorOverrideLog).all()
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0].acting_user_id == "28"
    assert rows[0].service_client_id == "casper"
    assert rows[0].endpoint == "/whoami"
    assert rows[0].method == "GET"


def test_rejected_override_does_not_write_audit_record(isolated_audit_db):
    """admin成り代わり拒否等、名乗りが不採用の場合は記録も残さない
    (採用された事実のみを記録する設計であることの確認)。"""
    from app.models import ServiceActorOverrideLog

    with patch("app.adapters.calendar_factory.get_calendar_client") as MockFactory:
        mock_client = MagicMock()
        mock_client.get_me.return_value = _mock_user(1, "admin")
        MockFactory.return_value = mock_client
        resp = _client().get("/whoami", headers=_service_headers(acting_uid=1))
    assert resp.status_code == 403

    db = isolated_audit_db()
    try:
        rows = db.query(ServiceActorOverrideLog).all()
    finally:
        db.close()
    assert len(rows) == 0


# ─── (k) 自動化印: _headers() 単一choke pointにのみ付与される ───────────────

class TestAutomationMarkerHeader:
    def test_marker_present_when_service_call_context_is_set(self, monkeypatch):
        from app.adapters.calendar_client import CalendarClient
        from app.request_context import set_service_call

        set_service_call(True)
        try:
            monkeypatch.setenv("CALENDAR_MOCK", "1")  # m2m token 経路(admin JWT 取得不要)
            client = CalendarClient()
            headers = client._headers(actor_user_id="28")
        finally:
            set_service_call(False)
        from urllib.parse import quote, unquote
        assert headers.get("X-Score-Action-Source") == quote("システム操作")
        assert unquote(headers["X-Score-Action-Source"]) == "システム操作"

    def test_all_header_values_are_ascii_safe(self, monkeypatch):
        """実機検証で発見した回帰の再発防止: HTTPヘッダの値は ASCII/latin-1 で
        なければならず(RFC 7230)、生の日本語をそのまま値に積むと httpx が送信時に
        例外を送出する(Calendar呼出そのものが失敗し、fail-closed側のexceptに
        飲まれて正当な利用者への名乗りまで403になる・モックのみの単体試験では
        検出できず実機検証で初めて判明した)。全ヘッダ値がASCIIエンコード可能で
        あることを機械的に保証する。"""
        from app.adapters.calendar_client import CalendarClient
        from app.request_context import set_service_call

        set_service_call(True)
        try:
            monkeypatch.setenv("CALENDAR_MOCK", "1")
            client = CalendarClient()
            headers = client._headers(actor_user_id="28")
        finally:
            set_service_call(False)
        for k, v in headers.items():
            v.encode("ascii")  # 例外が飛ばないこと自体がアサーション

    def test_marker_absent_for_normal_calls(self, monkeypatch):
        from app.adapters.calendar_client import CalendarClient
        from app.request_context import set_service_call

        set_service_call(False)
        monkeypatch.setenv("CALENDAR_MOCK", "1")
        client = CalendarClient()
        headers = client._headers(actor_user_id="28")
        assert "X-Score-Action-Source" not in headers

    def test_marker_not_present_in_body_helpers(self):
        """語彙・付与場所の確認: body側(patch_task の payload 組立)にこの種の
        フィールドを注入する実装が無いことを確認する回帰ガード。patch_task は
        {"status":..., "progress":...} 以外のキーを一切送らない前提
        (subtask_172a⑥調査結果)。"""
        import inspect
        from app.adapters import calendar_client as _cc_module
        src = inspect.getsource(_cc_module.CalendarClient.patch_task)
        assert "システム操作" not in src
        assert "X-Score-Action-Source" not in src


# ─── (a)(b)(c) 実ルートハンドラ(PATCH /api/bff/tasks/{id})経由の結合試験 ──
# app.main.app(_ServiceCallContextMiddleware込み)を直接使い、サービス資格+
# 名乗りヘッダで実際に役職ゲートが actor_id の由来に関わらず一貫して効くこと、
# および実リクエストで自動化印が実際に付与されることを end-to-end で確認する。

from app.main import app as _real_app  # noqa: E402


def _real_client():
    return TestClient(_real_app, follow_redirects=False)


class TestServiceOverrideThroughRealRoute:
    def _patch_both_client_lookups(self, mock_inst):
        return (
            patch("app.adapters.calendar_factory.get_calendar_client", return_value=mock_inst),
            patch("app.routers.bff_write.get_calendar_client", return_value=mock_inst),
        )

    def test_a_general_user_override_can_set_wip(self, isolated_audit_db):
        """(a) 許した相手(サービス資格)+一般作業者のuid → WIP/QC/DELIVERが通ること"""
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = _mock_user(28, "user")
        mock_inst.get_task.return_value = {"status": "wt", "project_id": 33}
        mock_inst.patch_task.return_value = {"ok": True}
        p1, p2 = self._patch_both_client_lookups(mock_inst)
        with p1, p2:
            resp = _real_client().patch(
                "/api/bff/tasks/10",
                json={"status": "wip"},
                headers=_service_headers(acting_uid=28),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "wip"}, actor_user_id="28")

    def test_b_general_user_override_rejected_for_pm_only_status(self, isolated_audit_db):
        """(b) 同じ一般作業者のuidでは WT/MK/CLIENT_AP/COMPLETED は403"""
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = _mock_user(28, "user")
        mock_inst.get_project_roles.return_value = {}
        p1, p2 = self._patch_both_client_lookups(mock_inst)
        with p1, p2, patch("app.routers.bff_write.is_qc_delegated", return_value=False):
            resp = _real_client().patch(
                "/api/bff/tasks/10",
                json={"status": "completed"},
                headers=_service_headers(acting_uid=28),
            )
        assert resp.status_code == 403
        mock_inst.patch_task.assert_not_called()

    def test_c_pm_override_can_set_pm_only_status(self, isolated_audit_db):
        """(c) 許した相手+PMのuid → PM専用の値が通ること"""
        mock_inst = MagicMock()
        mock_inst.get_me.return_value = _mock_user(30, "user")  # PM本人はadminではない
        mock_inst.get_task.return_value = {"status": "wip", "project_id": 33}
        mock_inst.get_project_roles.return_value = {"pm": 30}
        mock_inst.patch_task.return_value = {"ok": True}
        p1, p2 = self._patch_both_client_lookups(mock_inst)
        with p1, p2:
            resp = _real_client().patch(
                "/api/bff/tasks/10",
                json={"status": "completed"},
                headers=_service_headers(acting_uid=30),
            )
        assert resp.status_code == 200
        mock_inst.patch_task.assert_called_once_with(10, {"status": "completed"}, actor_user_id="30")

    def test_middleware_sets_service_context_for_real_service_request(self, isolated_audit_db):
        """(k) 配線確認: 実リクエスト(サービス資格)中、ミドルウェアが contextvar を
        True に設定していることを、CalendarClient._headers() の単体挙動
        (TestAutomationMarkerHeader で確認済)と組み合わせて実際のリクエスト経路上で
        検証する。route ハンドラ内から request_context.is_service_call() を直接
        読み出せるよう、一時的に patch_task をラップして観測する。"""
        from app import request_context as _rc

        mock_inst = MagicMock()
        mock_inst.get_me.return_value = _mock_user(28, "user")
        mock_inst.get_task.return_value = {"status": "wt", "project_id": 33}
        observed = {}

        def _observing_patch_task(task_id, body, actor_user_id=None):
            observed["is_service_call"] = _rc.is_service_call()
            return {"ok": True}

        mock_inst.patch_task.side_effect = _observing_patch_task
        p1, p2 = self._patch_both_client_lookups(mock_inst)
        with p1, p2:
            resp = _real_client().patch(
                "/api/bff/tasks/10",
                json={"status": "wip"},
                headers=_service_headers(acting_uid=28),
            )
        assert resp.status_code == 200
        assert observed["is_service_call"] is True

    def test_middleware_does_not_set_service_context_for_normal_request(self, isolated_audit_db):
        from app import request_context as _rc

        mock_inst = MagicMock()
        mock_inst.resolve_email_to_user_id.return_value = 42
        mock_inst.get_task.return_value = {"status": "wt", "project_id": 33}
        observed = {}

        def _observing_patch_task(task_id, body, actor_user_id=None):
            observed["is_service_call"] = _rc.is_service_call()
            return {"ok": True}

        mock_inst.patch_task.side_effect = _observing_patch_task
        p1, p2 = self._patch_both_client_lookups(mock_inst)
        token = create_score_token("sato@studio.jp")
        with p1, p2:
            resp = _real_client().patch(
                "/api/bff/tasks/10",
                json={"status": "wip"},
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200
        assert observed["is_service_call"] is False
