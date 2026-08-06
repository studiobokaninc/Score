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
