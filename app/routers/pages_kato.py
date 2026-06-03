import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_templates = Jinja2Templates(directory="app/templates")


@router.get("/troubleshoot")
def get_troubleshoot(request: Request, actor_id: str = Depends(get_actor_id)):
    """技術トラブル一覧 — Lead/Lighting Lead 用 (role 別 scope label)。"""
    role = get_actor_role(actor_id)
    if role not in ("lighting_lead", "lead", "kato"):
        raise HTTPException(status_code=403, detail="lead role required")
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        # 全 troubles 取得 (filter なし・将来 state.troubles に project_id/discipline field 追加後に scope filter 実装)
        troubles = client.get_troubles(actor_user_id=None) or []
    except Exception:
        troubles = []
    # scope label (role 別)
    if role in ("lighting_lead", "kato"):
        scope_label = "Lighting team"
    else:
        scope_label = "プロジェクト全体"

    # 各 trouble の title から SHOT_NNN 抽出 → 関連 task (Lighting) を解決して trouble entry に注入
    import re
    enriched_troubles = []
    for tr in (troubles or []):
        tr_dict = dict(tr) if isinstance(tr, dict) else {}
        title = tr_dict.get("title", "")
        # title 中の SHOT_NNN を抽出
        m = re.search(r'SHOT_(\d+)', title)
        if m:
            shot_id = int(m.group(1))
            tr_dict["shot_id_resolved"] = shot_id
            # 関連 Lighting task を探す
            try:
                tasks = client.get_tasks(shot_id, actor_user_id=actor_id) or []
                # title の context から discipline 推測 (現状 Lighting/Lighting Lead 視点)
                # 「レンダー遅延」「ノイズ」「ライティング」 → Lighting
                # 「Nuke クラッシュ」 → Composite
                # demo の単純化: Lighting Lead 視点なら Lighting 優先・なければ最初の task
                preferred_types = []
                if "レンダー" in title or "ライティング" in title or "光" in title or "ノイズ" in title:
                    preferred_types = ["Lighting", "Look"]
                elif "Nuke" in title or "Comp" in title or "コンポ" in title:
                    preferred_types = ["Composite"]
                elif "MattePaint" in title or "マット" in title:
                    preferred_types = ["MattePaint"]
                else:
                    preferred_types = ["Lighting"]  # 既定: Lead 視点

                related_task = None
                for ptype in preferred_types:
                    for t in tasks:
                        if t.type == ptype:
                            related_task = {"task_id": t.task_id, "type": t.type, "status": t.status}
                            break
                    if related_task:
                        break
                if related_task is None and tasks:
                    related_task = {"task_id": tasks[0].task_id, "type": tasks[0].type, "status": tasks[0].status}
                tr_dict["related_task"] = related_task
            except Exception:
                tr_dict["related_task"] = None
        enriched_troubles.append(tr_dict)

    return _templates.TemplateResponse(
        request=request, name="kato_troubleshoot.html",
        context={
            "role": role, "active": "troubleshoot",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user, "troubles": enriched_troubles,
            "scope_label": scope_label,
        })


# 旧 URL 後方互換: /kato_troubleshoot → /troubleshoot
@router.get("/kato_troubleshoot")
def get_kato_troubleshoot_legacy():
    return RedirectResponse(url="/troubleshoot", status_code=301)
