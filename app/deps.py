from typing import Generator

from fastapi import Cookie, Header, HTTPException, Request

from app.database import SessionLocal

# cmd_172 (2026-08-07・殿ご裁可=案1): 外部の道具(Casper等)は操作者本人のJWTを
# 持てないため、自らのサービス用資格でScoreを呼び、操作者本人のuidを別途この
# ヘッダで名乗る形を受け入れる。既存 X-Actor-User-Id は Score→Calendar 送信用・
# Score レスポンス用の別方向・別用途で34箇所使用済のため衝突を避け別名とする
# (subtask_172a②a)。
ACTING_USER_ID_HEADER = "X-Score-Acting-User-Id"


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _decode_actor_token(auth: str | None) -> tuple[dict, str]:
    """cmd_172: Authorization(Bearer JWT)を 利用者用シークレット→サービス用
    シークレット の順に検証する。どちらの鍵で検証が通ったか(key_kind:
    "user"|"service")と、ペイロード内 typ クレームの整合を確認する
    (軍師QC172A-2)。利用者用鍵で通ったのに typ="service" を名乗る、逆に
    サービス用鍵で通ったのに typ が "service" でない、のいずれも401とする
    (片方の鍵が漏洩した際に被害をもう一方の信頼機構へ波及させないため)。"""
    from app.auth import verify_jwt, verify_service_jwt

    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth.removeprefix("Bearer ")
    try:
        payload = verify_jwt(token)
        key_kind = "user"
    except HTTPException:
        try:
            payload = verify_service_jwt(token)
        except RuntimeError:
            # SCORE_SERVICE_JWT_SECRET 未設定環境(既存の回帰防止): サービス鍵
            # 検証自体が使えない場合は、単純に「利用者用鍵でも無効」という
            # 従来どおりの401に倒す(500化させない)。
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        key_kind = "service"
    typ = payload.get("typ")
    if key_kind == "user" and typ == "service":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if key_kind == "service" and typ != "service":
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload, key_kind


def get_actor_role_strict(actor_id: str, client=None) -> str:
    """get_actor_role の fail-open ではない版(cmd_172 軍師QC172A-3対応)。
    Calendar 未応答・実在しない uid 等で例外が起きた場合、握り潰さず呼び出し元へ
    伝播させる。★この関数は「サービス資格による名乗り上書きが admin へ成り
    代われないことを保証する関門」専用に新設したものであり、既存 get_actor_role
    の他の呼び出し元(fail-open が正しい安全側の挙動として機能している箇所)には
    一切影響を与えない。
    (理由): 「役職不明なら昇格させぬ」という get_actor_role の fail-open
    フォールバック("user"を返す)は、通常の権限判定では安全側に働くが、
    「名乗られた uid が admin か否か」を見分ける関門にそのまま転用すると
    「不明→adminではないと断定」という危険側の意味に反転してしまう
    (Calendar 未応答の一瞬に誰でも admin へ成り代われる穴になる)。ゆえに
    この関門では例外を飲み込まない変種を用いる。"""
    if client is None:
        from app.adapters.calendar_factory import get_calendar_client
        client = get_calendar_client()
    user = client.get_me(actor_user_id=actor_id)
    email = (getattr(user, "email", "") or "").lower()
    if email in SCORE_ROLE_MAP:
        return SCORE_ROLE_MAP[email]
    return user.role if user and user.role else "user"


def _record_service_override(method: str, path: str, service_client_id: str | None, acting_user_id: str) -> None:
    """cmd_172⑥・殿ご裁可の条件②: 名乗り上書き機構と同一PR/同一リリースで導入する
    記録機構。代理の名乗りは性質上「誰がやったか」を分かりにくくする方向へ働く
    ため、この記録が無ければ導入期間中の行為が事後に一切追跡できなくなる
    (軍師OBS172A-1)。監査記録の書込失敗が本来の書込処理そのものを止めては
    ならない(qc_delegation.py 等、既存の付随記録処理と同じ fail-soft 方針)ため
    例外は握り潰すが、可視化のため stderr には残す。"""
    from app.models import ServiceActorOverrideLog
    try:
        db = SessionLocal()
        try:
            db.add(ServiceActorOverrideLog(
                method=method,
                endpoint=path,
                service_client_id=service_client_id or "service",
                acting_user_id=acting_user_id,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        import sys
        print(f"[cmd_172 audit] ServiceActorOverrideLog write failed: {e}", file=sys.stderr, flush=True)


def get_actor_id(
    request: Request,
    authorization: str | None = Header(default=None),
    score_token: str | None = Cookie(default=None, alias="score_token"),
    x_score_acting_user_id: str | None = Header(default=None, alias=ACTING_USER_ID_HEADER),
) -> str:
    from app.adapters.calendar_factory import get_calendar_client
    import httpx as _httpx

    auth = authorization or (f"Bearer {score_token}" if score_token else None)
    payload, key_kind = _decode_actor_token(auth)
    client = get_calendar_client()

    if key_kind != "service":
        # cmd_172①1): 通常の利用者JWTでは、上書きヘッダが紛れ込んでいても
        # 黙って無視する(actor解決に一切使わない=誤混入で通常ユーザの
        # リクエストをエラーにしない・無視が安全側)。従来どおりの解決経路。
        jwt_sub = str(payload["sub"])
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

    # cmd_172①2)〜3): サービス種別JWT に限り、名乗り(ACTING_USER_ID_HEADER)を
    # 読み actor として採用してよい。
    if not x_score_acting_user_id:
        raise HTTPException(status_code=401, detail="Acting user id header required for service credential")
    try:
        acting_uid = int(x_score_acting_user_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=403, detail="Invalid acting user id")
    # 軍師QC172A-1/QC172A-3: 三区分の fail-closed 判定。
    #   a) admin と判定できた → 拒否 (403)
    #   b) admin 以外と判定できた → 採用
    #   c) 判定できなかった (Calendar 未応答・実在しない uid 等の例外) → 拒否 (403)
    try:
        role = get_actor_role_strict(str(acting_uid), client=client)
    except Exception:
        raise HTTPException(status_code=403, detail="Acting user could not be resolved")
    if role == "admin":
        raise HTTPException(status_code=403, detail="Acting as an admin-resolved user is forbidden")
    _record_service_override(
        method=request.method,
        path=request.url.path,
        service_client_id=payload.get("cid"),
        acting_user_id=str(acting_uid),
    )
    return str(acting_uid)


# ===== ScoreUserRole 暫定 mapping (2026-05-27 nibu 殿御回答経由) =====
# Calendar 側 User.role は 公式合意書 §3.1 で 'admin' / 'user' の 2 択のみ。
# Score 側 業務ロール (lead / director / pm / lighting_lead / compositor) は
# ScoreUserRole 多対多テーブル (発注書 2026-05-14 §3.2) で管理する設計だが、
# 本式 DB 化(F-3)までの暫定として Python dict で email → score_role mapping を保持する。
# 将来: Score 側に score_user_roles テーブル新設 + Calendar.email join で書換予定。
SCORE_ROLE_MAP: dict[str, str] = {
    # 殿御命 2026-06-05: role 名 大改編
    # 旧 lead (Ryoji) → admin (内容変更なし・role 名のみ rename)
    # 旧 lighting_lead (Kato) → lead (各分野 Lead = Lighting / Animation / FX 等の技術アート責任者)
    # 旧 compositor → user (一般業務担当 = Compositor / Animator / FX 等)
    "ryoji@studiobokan.com":  "admin",   # 殿 / Admin (旧 lead, スタジオ全体管理)
    "tanaka@studiobokan.com": "pm",       # PM
    "yamada@studiobokan.com": "director", # Director
    "kato@studiobokan.com":   "lead",     # Lead (旧 lighting_lead, 各分野 技術アート責任者)
    "sato@studiobokan.com":   "user",     # User (旧 compositor, 一般業務担当)
    "suzuki@studiobokan.com": "user",     # User (旧 compositor, 一般業務担当)
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


# cmd_167 (2026-08-05・殿ご裁可=案あ): 役職ゲートの解決先を系A(本関数・グローバル
# per-user固定表)から系B(案件ごとの実役職・director_uid/pm_uid/lead_uid)へ寄せる。
# SCORE_ROLE_MAP は6件中5件が実在せず、Calendar User.role もadmin/userの2択のみの
# ため、get_actor_role() は実運用上 admin/user の2値しか返さない
# (director/pm/lead に解決される者が一人も居ない=十数箇所の役職ゲートが悉く
# 「adminだけが通る門」と化していた・cmd_162 QC162B-1で判明)。
def get_actor_project_role(actor_id: str, project_id: int | None, client=None) -> str:
    """役職を (利用者 actor_id × 案件 project_id) の組で解決する。
    優先順位:
      1. admin はスタジオ全体の据え置き権限 (案件に紐付かない) のため、系A の
         get_actor_role() が "admin" を返す場合はそれを最優先で返す (④admin据え置き)。
      2. project_id があれば 系B (CalendarClient.get_project_roles・実案件の
         director/pm/lead 割当) を引き、actor_id と一致する役職名を返す。
      3. project_id が未解決、または系Bの取得に失敗、またはどの役職にも一致しない
         場合は "user" を返す (⑤fail-closed・昇格権限なしがデフォルト)。
    client: 呼び出し元が既に保持する CalendarClient インスタンス (bff_write.py の
    _require_qc_judge_authority 等・route 内で既に取得/mock 済のものを再利用する。
    省略時のみ本関数が自前で取得する)。
    """
    if get_actor_role(actor_id) == "admin":
        return "admin"
    if project_id is None:
        return "user"
    try:
        if client is None:
            from app.adapters.calendar_factory import get_calendar_client
            client = get_calendar_client()
        roles = client.get_project_roles(int(project_id), actor_user_id=actor_id) or {}
        if not isinstance(roles, dict):
            roles = {}
    except Exception:
        roles = {}
    try:
        uid = int(actor_id)
    except (ValueError, TypeError):
        return "user"
    if roles.get("director") is not None and int(roles["director"]) == uid:
        return "director"
    if roles.get("pm") is not None and int(roles["pm"]) == uid:
        return "pm"
    _lead = roles.get("lead") if roles.get("lead") is not None else roles.get("lighting_lead")
    if _lead is not None and int(_lead) == uid:
        return "lead"
    return "user"
