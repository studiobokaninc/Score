import os as _os
from dotenv import load_dotenv
load_dotenv(dotenv_path=_os.path.join(_os.path.dirname(_os.path.dirname(__file__)), '.env'), override=True)

from fastapi import FastAPI
import uvicorn

from app.routers import (
    bff,
    pages_dashboard,
    pages_shot,
    pages_auth,
    pages_qc,
    bff_write,
    pages_misc,
    cross_projects,
    cross_production_tracker,
    bff_cross,
    auth_login,
    pages_notifications,
    pages_director,
    pages_kato,
    pages_project_detail,
    pages_pm,
    pages_calendar,
    pages_meeting_minutes,
    pages_director_dashboard,
    pages_lead_dashboard,
    pages_pm_dashboard,
    pages_director_qc_viewer,
    pages_routine,
    pages_notif_settings,
    sse_notifications,
    usage_summary,
)

app = FastAPI(title="Score BE", version="0.1.0")


# 殿御命 2026-06-04: SSR ページの 401 を /login に自動リダイレクト
# (JSON API は default の JSON 401 維持)
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import HTTPException as _HTTPExc


@app.exception_handler(_HTTPExc)
async def _auth_redirect_handler(request: Request, exc: _HTTPExc):
    if exc.status_code == 401:
        path = request.url.path or ""
        accept = (request.headers.get("accept") or "").lower()
        is_api = path.startswith("/api/")
        is_html = "text/html" in accept
        # ブラウザ SSR 経路 (HTML 受領可・/api/ 以外) は /login redirect
        if not is_api and is_html:
            # subtask_070e: 元の遷移先 (通知の QC ビューアリンク等) を next= として保持し、
            # 再ログイン後に post_login (auth_login.py) がそこへ戻せるようにする。
            from urllib.parse import quote as _quote
            original = path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse(
                url=f"/login?error=session_expired&next={_quote(original, safe='')}",
                status_code=303,
            )
    # default JSON response (API / 401 以外)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=getattr(exc, "headers", None) or {},
    )

# cmd_172⑦: 自動化印(Calendar送信ヘッダ)判定用ミドルウェア。app.deps.get_actor_id
# と同じ typ="service" 判別をリクエスト先頭で行い、CalendarClient._headers()
# (単一choke point)がリクエスト全体を通じて参照できる contextvar に格納する。
# ★観測用の印にすぎず認可判断には使わない(get_actor_id が別途厳密に再検証する)。
from starlette.middleware.base import BaseHTTPMiddleware


class _ServiceCallContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.request_context import set_service_call
        from app.auth import verify_service_jwt

        auth = request.headers.get("authorization")
        if not auth:
            score_token = request.cookies.get("score_token")
            auth = f"Bearer {score_token}" if score_token else None
        is_service = False
        if auth and auth.startswith("Bearer "):
            token = auth[len("Bearer "):]
            try:
                payload = verify_service_jwt(token)
                is_service = payload.get("typ") == "service"
            except Exception:
                is_service = False
        set_service_call(is_service)
        return await call_next(request)


app.add_middleware(_ServiceCallContextMiddleware)

# cmd_187 (2026-08-27・殿ご裁可): Score利用ログ基盤・簡易版の唯一の捕捉点。
# 正本(score_usage_log_design_plan_simple_20260827.md)§4のとおり
# _ServiceCallContextMiddleware とは別・同格の新規middlewareとする。
# ★user_idはapp.deps.get_actor_id側が同一requestのrequest.state.actor_idへ
# 書き残した値を読むのみ(このmiddleware自身はCalendarへの解決要求を一切
# 行わない・get_actor_idが既に行った解決の結果を再利用するのみ=二重の
# Calendar呼出を避ける)。get_actor_idが呼ばれなかった経路(未認証・
# ヘルスチェック等)ではNULLのまま記録し、§5の読む口でnon_human枠へ回る。
import time as _time


class _UsageLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = _time.monotonic()
        response = await call_next(request)
        try:
            from app.usage_log import record_usage_event
            from datetime import datetime as _dt

            duration_ms = int((_time.monotonic() - start) * 1000)
            record_usage_event(
                occurred_at=_dt.utcnow(),
                user_id=getattr(request.state, "actor_id", None),
                http_method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        except Exception as e:
            import sys as _sys
            print(f"[usage_log] middleware failed: {e}", file=_sys.stderr, flush=True)
        return response


app.add_middleware(_UsageLogMiddleware)

# static asset mount (/static/*) — sidemenu ロゴ等の画像配信用
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# 殿御命 2026-06-08: avatar 等の user upload 画像配信用 (/uploads/*)
# mock の post_my_avatar が score_be/uploads/ 配下へ実体保存 → ここで配信
_uploads_dir = _Path(__file__).parent.parent / "uploads"
_uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

# 殿御命 2026-06-08: tutorial を score_be 経由で配信 (/tutorial)
_tutorial_dir = _Path(__file__).parent.parent / "tutorial"
if _tutorial_dir.exists():
    app.mount("/tutorial", StaticFiles(directory=str(_tutorial_dir), html=True), name="tutorial")


# 殿御命 2026-06-04 cmd_477: Service Worker を root scope (/) で配信
# (Push 通知用 SW は controller として全 page に作用する必要・/static/sw.js では scope=/static/ で不適)
from fastapi.responses import FileResponse

@app.get("/sw.js")
def serve_sw_at_root():
    sw_file = _static_dir / "sw.js"
    return FileResponse(
        str(sw_file),
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )

# 起動時に DB table を auto-create (alembic migration を待たず)
from app.database import Base, engine
Base.metadata.create_all(bind=engine)

# cmd_187: usage_events は既存score.dbとは別ファイル(score_usage_simple.db)の
# ため、既存Base(score.db向け)とは別にUsageLogBase.metadata.create_allを呼ぶ。
# alembicは使わない(正本§6・殿の手数最少の理由は同節参照)。
from app.usage_log import UsageLogBase, usage_log_engine, cleanup_old_usage_events
UsageLogBase.metadata.create_all(bind=usage_log_engine)
# §5「保存期間」: 別プロセス/cronを設けず起動時に1回だけ古い行を消す。
cleanup_old_usage_events()

# 殿御命 2026-06-09: 既存 SQLite への列追加 (create_all は既存テーブルを ALTER せぬため idempotent migration)
def _ensure_columns():
    from sqlalchemy import text as _sql_text
    _migrations = [
        ("bug_reports", "operation_log", "TEXT"),
        ("bug_reports", "user_agent", "VARCHAR"),
    ]
    try:
        with engine.begin() as conn:
            for table, col, coltype in _migrations:
                existing = [r[1] for r in conn.execute(_sql_text(f"PRAGMA table_info({table})"))]
                if col not in existing:
                    conn.execute(_sql_text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}"))
    except Exception as _e:
        import sys as _sys
        print(f"[migration] skip/err: {_e}", file=_sys.stderr)
_ensure_columns()

app.include_router(bff.router, prefix="")
app.include_router(pages_dashboard.router)
app.include_router(pages_shot.router)
app.include_router(pages_auth.router)
app.include_router(pages_qc.router)
app.include_router(bff_write.router)
app.include_router(pages_misc.router)
app.include_router(pages_meeting_minutes.router)
app.include_router(cross_projects.router)
app.include_router(cross_production_tracker.router)
app.include_router(bff_cross.router, prefix="")
app.include_router(auth_login.router)
app.include_router(pages_notifications.router)
app.include_router(pages_director.router)
app.include_router(pages_kato.router)
app.include_router(pages_project_detail.router)
app.include_router(pages_pm.router)
app.include_router(pages_calendar.router)
app.include_router(pages_director_dashboard.router)
app.include_router(pages_lead_dashboard.router)
app.include_router(pages_pm_dashboard.router)
app.include_router(pages_director_qc_viewer.router)
app.include_router(pages_routine.router)
app.include_router(pages_notif_settings.router)
app.include_router(sse_notifications.router)
app.include_router(usage_summary.router)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "score-be"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8100, reload=True)
