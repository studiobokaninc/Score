"""殿御命 2026-06-08: 議事録 page (簡易実装・mtg event と連携)"""
import os
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_env = Environment(loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")), cache_size=0)
_templates = Jinja2Templates(env=_env)


@router.get("/meeting_minutes/{event_id}")
def get_meeting_minutes(event_id: int, request: Request, actor_id: str = Depends(get_actor_id)):
    """議事録 page — Calendar event ID 経由で mtg 詳細を表示。
    mock 環境: hardcode sample data・本式は別途 minutes table から fetch 予定。
    """
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    # mock minutes data (event_id 別に変える余地あり・現状は sample 固定)
    minutes = {
        "event_id": event_id,
        "title": "shot01 QC レビュー mtg",
        "date": "2026-06-08",
        "time": "14:00-15:30",
        "location": "mtg room A",
        "zoom_url": "https://zoom.us/j/1234567890?pwd=projectalpha_sample",
        "project_name": "プロジェクトアルファ",
        "participants": [
            {"emoji": "📋", "name": "田中 太郎", "role": "PM"},
            {"emoji": "🎬", "name": "山田 博", "role": "Director"},
            {"emoji": "💡", "name": "加藤 健司", "role": "Lighting Lead"},
            {"emoji": "🖥️", "name": "佐藤 花子", "role": "Compositor"},
        ],
        "agenda": [
            "1. shot01 Compositing v003 進捗共有 (担当: 佐藤 Compositor)",
            "2. Compositing 品質方針すり合わせ (担当: 山田 Director)",
            "3. Lighting テクニカル issue 共有 (担当: 加藤 Lead)",
            "4. 翌週 shot02 着手計画 (担当: 全員)",
            "5. AOB",
        ],
        "decisions": [
            "✓ shot01 c05 Composite v003 を Retake 1 回 + Approve まで 6/9 完了予定",
            "✓ shot02 Lighting 着手日: 2026-06-10 朝一",
            "✓ 翌週月曜 (6/15) 試写日確定 (18:30-)",
        ],
        "next_actions": [
            {"who": "@佐藤 花子", "what": "shot01 c05 Composite Retake 修正", "when": "2026-06-09 18:00"},
            {"who": "@山田 博", "what": "shot01 c05 QC 判定", "when": "2026-06-10 12:00"},
            {"who": "@加藤 健司", "what": "shot02 light rig 配布", "when": "2026-06-10 朝一"},
        ],
    }
    return _templates.TemplateResponse(
        request=request, name="meeting_minutes.html",
        context={
            "role": role, "active": "calendar",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "minutes": minutes,
        },
    )
