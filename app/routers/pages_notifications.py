import os
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from app.deps import get_actor_id, get_actor_role
from app.adapters.calendar_factory import get_calendar_client

router = APIRouter()
_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates")),
    cache_size=0,
)
_templates = Jinja2Templates(env=_env)


_MENTION_RE = re.compile(r"@\w+")
_NOTICE_KEYWORDS = ("リリース", "お知らせ", "アップデート", "メンテナンス", "新機能", "Score v", "v2.", "v3.")
_RETAKE_KEYWORDS = ("retake", "リテイク", "差し戻", "修正依頼")
_APPROVED_KEYWORDS = ("approved", "承認")


def _categorize(n: dict) -> str:
    """Return one of: 'mention', 'notice', 'unread'.

    Heuristic until Calendar adds a proper ``type`` field:
    - explicit ``type`` field if present
    - @-pattern in title/body → mention
    - release/announcement keywords → notice
    - otherwise → unread (general activity feed)
    """
    explicit = (n.get("type") or "").lower().strip()
    if explicit in ("mention", "notice", "unread"):
        return explicit

    title = n.get("title") or ""
    body = n.get("body") or ""
    blob = f"{title} {body}"

    if _MENTION_RE.search(blob):
        return "mention"
    if any(kw.lower() in blob.lower() for kw in _NOTICE_KEYWORDS):
        return "notice"
    return "unread"


def _emoji_for(n: dict, category: str) -> str:
    """Pick a leading emoji icon for the card based on content/category."""
    blob = f"{n.get('title','')} {n.get('body','')}".lower()
    if any(kw.lower() in blob for kw in _APPROVED_KEYWORDS):
        return "🟢"
    if any(kw.lower() in blob for kw in _RETAKE_KEYWORDS):
        return "🔴"
    if "ai" in blob or "提案" in blob or "朝会" in blob:
        return "🤖"
    if category == "mention":
        return "💬"
    if category == "notice":
        return "📢"
    return "🔔"


def _icon_bg(n: dict, category: str) -> str:
    """Tailwind bg class for the icon circle, matched to the emoji semantics."""
    blob = f"{n.get('title','')} {n.get('body','')}".lower()
    if any(kw.lower() in blob for kw in _APPROVED_KEYWORDS):
        return "bg-emerald-100"
    if any(kw.lower() in blob for kw in _RETAKE_KEYWORDS):
        return "bg-rose-100"
    if "ai" in blob or "提案" in blob:
        return "bg-indigo-100"
    if category == "mention":
        return "bg-indigo-100"
    if category == "notice":
        return "bg-slate-100"
    return "bg-amber-100"


def _format_when(created_at) -> str:
    """Best-effort human-readable timestamp (今日 HH:MM / 昨日 / YYYY-MM-DD)."""
    if not created_at:
        return ""
    s = str(created_at)
    formats = [
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M",    16),
        ("%Y-%m-%dT%H:%M",    16),
        ("%Y-%m-%d",          10),
    ]
    for fmt, length in formats:
        if len(s) < length:
            continue
        try:
            dt = datetime.strptime(s[:length], fmt)
        except ValueError:
            continue
        today = datetime.now().date()
        has_time = "%H" in fmt
        if dt.date() == today:
            return f"今日 {dt.strftime('%H:%M')}" if has_time else "今日"
        if (today - dt.date()).days == 1:
            return f"昨日 {dt.strftime('%H:%M')}" if has_time else "昨日"
        return dt.strftime("%Y-%m-%d")
    return s


def _decorate(notifications: list[dict]) -> list[dict]:
    """Return a fresh list of notifications with category/emoji/icon_bg/when filled in."""
    out = []
    for n in (notifications or []):
        if not isinstance(n, dict):
            continue
        if n.get("read"):
            continue
        d = dict(n)
        cat = _categorize(n)
        d["category"] = cat
        d["emoji"] = _emoji_for(n, cat)
        d["icon_bg"] = _icon_bg(n, cat)
        d["when_label"] = _format_when(n.get("created_at"))
        out.append(d)
    # newest first if created_at is sortable
    out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return out


@router.get("/notification_center")
def get_notification_center(request: Request, actor_id: str = Depends(get_actor_id)):
    role = get_actor_role(actor_id)
    client = get_calendar_client()
    try:
        user = client.get_me(actor_user_id=actor_id)
    except Exception:
        user = None
    try:
        notifications = client.get_notifications(actor_user_id=actor_id)
    except Exception:
        notifications = []
    try:
        messages = client.get_messages(actor_user_id=actor_id)
    except Exception:
        messages = []

    decorated = _decorate(notifications)
    groups = {"unread": [], "mention": [], "notice": []}
    for n in decorated:
        groups[n["category"]].append(n)
    counts = {k: len(v) for k, v in groups.items()}

    return _templates.TemplateResponse(
        request=request, name="notification_center.html",
        context={
            "role": role,
            "active": "notifications",
            "demo_mode": os.getenv("CALENDAR_MOCK", "0") == "1",
            "user": user,
            "notifications": decorated,
            "groups": groups,
            "counts": counts,
            "messages": messages,
        },
    )
