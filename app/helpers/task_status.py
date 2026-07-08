"""Task status registry — single source for the 19-value TaskStatus system
(cmd_075 刷新対応・2026-07-08).

一次ソースは Calendar 側 backend/app/status_meta.py。色/ラベルは
GET /api/readonly/task-statuses から動的取得しキャッシュする(ハードコード禁止・
calendar_status_color_guide_for_score_2026-07-08.md 準拠)。カテゴリ集合は
Score 側の判定ロジック用(_can_judge・完了判定・優先度ソート等)。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

NEW_TASK_STATUSES = frozenset({
    "mk", "wip", "modeling", "lookdev", "caching", "rig", "facial",
    "v1qc", "qc", "qc_fb", "ap", "ap_fb", "dir_wt", "dir_ap", "dir_fb",
    "fix", "deliver", "omit", "wt",
})

# 旧7値 → 新値。delayed は状態ではなく isOverdue 派生フラグへ移行するが、
# 万一 patch_task に旧値が届いた場合の互換変換先として wip を維持する。
OLD_TO_NEW_STATUS = {
    "todo": "mk",
    "in-progress": "wip",
    "in_progress": "wip",
    "review": "qc",
    "retake": "qc_fb",
    "approved": "ap",
    "completed": "deliver",
    "delayed": "wip",
}

# ロジック上の状態カテゴリ (6分類・score_compatibility_check.md §2.2 準拠)
STATUS_CATEGORY: dict[str, frozenset[str]] = {
    "not_started": frozenset({"mk"}),
    "in_progress": frozenset({"wip", "modeling", "lookdev", "caching", "rig", "facial"}),
    "internal_check": frozenset({"v1qc", "qc", "qc_fb", "ap"}),
    "external_check": frozenset({"dir_wt", "dir_ap", "dir_fb", "ap_fb", "fix"}),
    "completed": frozenset({"deliver"}),
    "held": frozenset({"omit", "wt"}),
}

COMPLETED_STATUSES = STATUS_CATEGORY["completed"]  # {"deliver"} — 唯一の完了
HELD_STATUSES = STATUS_CATEGORY["held"]
CHECK_STATUSES = STATUS_CATEGORY["internal_check"] | STATUS_CATEGORY["external_check"]

# Director/PM が判定アクションを取れる対象 (承認済 ap/dir_ap/fix は除く=判定待ちのみ)
JUDGE_TARGET_STATUSES = frozenset({"qc", "v1qc", "qc_fb", "ap_fb", "dir_wt", "dir_fb"})
# PM ダッシュボード「受領待ち成果物」カウント対象 (FB系=再修正中は含めない)
RECEPTION_PENDING_STATUSES = frozenset({"qc", "v1qc", "dir_wt"})

# ダッシュボード等のソート優先度 (小さいほど要対応度が高い)
STATUS_PRIORITY: dict[str, int] = {
    "qc_fb": 0, "ap_fb": 0, "dir_fb": 0,
    "qc": 1, "v1qc": 1, "dir_wt": 1,
    "mk": 2,
    "wip": 3, "modeling": 3, "lookdev": 3, "caching": 3, "rig": 3, "facial": 3,
    "wt": 4,
    "ap": 5, "fix": 5, "dir_ap": 5,
    "deliver": 9, "omit": 9,
}

# 退勤レポート「作業要」判定用 (exit_report.html — score_compatibility_check.md §3.3.D)
# FB系 (再修正中) も作業要に含む
WIP_STATUSES: list[str] = [
    "mk", "wip", "modeling", "lookdev", "caching", "rig", "facial",
    "qc_fb", "ap_fb", "dir_fb",
]

# 退勤レポート等の進捗率デフォルト (score_compatibility_check.md §3.3.D)
STATUS_DEFAULT_PROGRESS: dict[str, int] = {
    "deliver": 100, "dir_ap": 95, "fix": 95, "ap": 85,
    "qc": 70, "v1qc": 70, "dir_wt": 70,
    "wip": 40, "modeling": 40, "lookdev": 40, "caching": 40, "rig": 40, "facial": 40,
    "qc_fb": 40, "ap_fb": 40, "dir_fb": 40,
    "wt": 20, "mk": 0,
}

FALLBACK_COLOR = "#BDBDBD"  # 不明ステータスのデフォルト (color guide §2 準拠)


def canonicalize_status(status: str | None) -> str | None:
    """旧値→新値正規化。新値・未知値はそのまま返す。"""
    if not status:
        return status
    return OLD_TO_NEW_STATUS.get(status, status)


def status_category_of(status: str | None) -> str | None:
    for cat, values in STATUS_CATEGORY.items():
        if status in values:
            return cat
    return None


def is_completed(status: str | None) -> bool:
    return status in COMPLETED_STATUSES


def is_overdue(status: str | None, due_date: str | None) -> bool:
    """due_date < 今日(Asia/Tokyo) かつ status not in {deliver, omit} (§2.3 派生フラグ)。"""
    if not due_date or status in COMPLETED_STATUSES or status in HELD_STATUSES:
        return False
    try:
        jst = timezone(timedelta(hours=9))
        today = datetime.now(jst).date()
        d = datetime.fromisoformat(str(due_date)[:10]).date()
        return d < today
    except (ValueError, TypeError):
        return False


# ─── Calendar API からの動的色/ラベル取得 (ハードコード禁止) ────────────────

_CACHE_TTL_SECONDS = 600  # 10分キャッシュ (起動毎回fetchは避けつつドリフトなし)
_status_meta_cache: dict[str, dict[str, Any]] | None = None
_status_meta_cache_at: float = 0.0


def _fetch_status_meta(client) -> dict[str, dict[str, Any]]:
    rows = client.get_task_statuses()
    return {
        r["value"]: {"label": r.get("label"), "color": r.get("color"), "category": r.get("category")}
        for r in rows
        if isinstance(r, dict) and r.get("value")
    }


def get_status_meta_map(client=None) -> dict[str, dict[str, Any]]:
    """value -> {label, color, category} (GET /api/readonly/task-statuses・TTLキャッシュ)。
    client 省略時は既存キャッシュのみ返す(fetch不可なら空dict)。"""
    global _status_meta_cache, _status_meta_cache_at
    now = time.time()
    if _status_meta_cache is not None and (now - _status_meta_cache_at) < _CACHE_TTL_SECONDS:
        return _status_meta_cache
    if client is None:
        return _status_meta_cache or {}
    try:
        _status_meta_cache = _fetch_status_meta(client)
        _status_meta_cache_at = now
    except Exception:
        if _status_meta_cache is None:
            _status_meta_cache = {}
    return _status_meta_cache


def status_color(status: str | None, client=None) -> str:
    meta = get_status_meta_map(client)
    return (meta.get(status) or {}).get("color") or FALLBACK_COLOR


def status_label(status: str | None, client=None) -> str:
    meta = get_status_meta_map(client)
    label = (meta.get(status) or {}).get("label")
    return label or (status or "").upper()


def attach_status_meta(tasks: Iterable[Any], client=None) -> list[Any]:
    """tasks の各要素へ status_color/status_label/status_category を付与。
    colors.py の attach_task_palettes() と同じ dict/オブジェクト両対応パターン。"""
    meta = get_status_meta_map(client)
    out: list[Any] = []
    for t in tasks or []:
        st = t.get("status") if isinstance(t, dict) else getattr(t, "status", None)
        m = meta.get(st) or {}
        color = m.get("color") or FALLBACK_COLOR
        label = m.get("label") or (st or "").upper()
        category = m.get("category")
        if isinstance(t, dict):
            new_t = dict(t)
            new_t["status_color"] = color
            new_t["status_label"] = label
            new_t["status_category"] = category
            out.append(new_t)
        else:
            try:
                setattr(t, "status_color", color)
                setattr(t, "status_label", label)
                setattr(t, "status_category", category)
                out.append(t)
            except Exception:
                out.append(t)
    return out
