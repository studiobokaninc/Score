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
    pages_director_dashboard,
    pages_lead_dashboard,
    pages_pm_dashboard,
    pages_director_qc_viewer,
    pages_routine,
)

app = FastAPI(title="Score BE", version="0.1.0")

# static asset mount (/static/*) — sidemenu ロゴ等の画像配信用
from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_static_dir = _Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# 起動時に DB table を auto-create (alembic migration を待たず)
from app.database import Base, engine
Base.metadata.create_all(bind=engine)

app.include_router(bff.router, prefix="")
app.include_router(pages_dashboard.router)
app.include_router(pages_shot.router)
app.include_router(pages_auth.router)
app.include_router(pages_qc.router)
app.include_router(bff_write.router)
app.include_router(pages_misc.router)
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


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "score-be"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8100, reload=True)
