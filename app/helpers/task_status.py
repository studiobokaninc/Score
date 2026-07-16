"""Task status registry — single source for the 9-value TaskStatus system
(cmd_106 STEP2a 9値刷新対応・2026-07-16).

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
    "wt", "mk", "wip", "qc", "qc_fb", "ap", "client_ap", "deliver", "omit",
})

# 旧値 → 新9値。Calendar 側 canonicalize と同一の写像。
OLD_TO_NEW_STATUS = {
    # 旧7値
    "todo": "mk", "in-progress": "wip", "in_progress": "wip",
    "review": "qc", "retake": "qc_fb", "approved": "ap",
    "completed": "deliver", "delayed": "wip",
    # 旧19体系 → 新9体系(集約)
    "modeling": "wip", "lookdev": "wip", "caching": "wip", "rig": "wip", "facial": "wip",
    "v1qc": "qc", "dir_wt": "qc",
    "ap_fb": "qc_fb", "dir_fb": "qc_fb", "fix": "qc_fb",
    "dir_ap": "ap",
    "client-ap": "client_ap",  # ハイフン表記の救済
}

# ロジック上の状態カテゴリ (V2 の5分類。internal_check/external_check は廃止)
STATUS_CATEGORY: dict[str, frozenset[str]] = {
    "not_started": frozenset({"mk"}),
    "in_progress": frozenset({"wip"}),
    "review":      frozenset({"qc", "qc_fb"}),
    "completed":   frozenset({"ap", "client_ap", "deliver"}),  # ★3値すべて完了
    "held":        frozenset({"wt", "omit"}),
}

COMPLETED_STATUSES = STATUS_CATEGORY["completed"]   # {"ap","client_ap","deliver"}
HELD_STATUSES = STATUS_CATEGORY["held"]
CHECK_STATUSES = STATUS_CATEGORY["review"]          # 旧 internal|external の代替

# Director/PM が判定アクションを取れる対象 (判定待ちのみ)
JUDGE_TARGET_STATUSES = frozenset({"qc", "qc_fb"})
# PM ダッシュボード「受領待ち成果物」カウント対象 (FB系=再修正中は含めない)
RECEPTION_PENDING_STATUSES = frozenset({"qc"})

# ダッシュボード等のソート優先度 (小さいほど要対応度が高い)
STATUS_PRIORITY: dict[str, int] = {
    "qc_fb": 0,          # FB修正 最優先
    "qc": 1,             # 社内チェック(判定待ち)
    "mk": 2,             # 未着手
    "wip": 3,            # 進行中
    "wt": 4,             # 待機
    "ap": 9, "client_ap": 9, "deliver": 9, "omit": 9,  # 完了・対象外は最下位
}

# 退勤レポート「作業要」判定用 (exit_report.html — score_compatibility_check.md §3.3.D)
# FB系 (再修正中) も作業要に含む
WIP_STATUSES: list[str] = ["mk", "wip", "qc_fb"]

# 退勤レポート等の進捗率デフォルト (score_compatibility_check.md §3.3.D)。完了3値=100
STATUS_DEFAULT_PROGRESS: dict[str, int] = {
    "deliver": 100, "client_ap": 100, "ap": 100,
    "qc": 70,
    "wip": 40, "qc_fb": 40,
    "wt": 0, "mk": 0,
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
    colors.py の attach_task_palettes() と同じ dict/オブジェクト両対応パターン。

    優先順位: (1) Calendar の task 応答に既に inline 同梱されている値
    (get_tasks/get_tasks_by_project/get_task/get_my_tasks は実機確認済で全て含む)
    → (2) GET /api/readonly/task-statuses の一括キャッシュ (専用 read-only token 要・
    現状 401 で未疎通のため通常は (1) 側が使われる) → (3) FALLBACK_COLOR。"""
    meta = get_status_meta_map(client)
    out: list[Any] = []
    for t in tasks or []:
        is_dict = isinstance(t, dict)
        st = t.get("status") if is_dict else getattr(t, "status", None)
        inline_color = t.get("status_color") if is_dict else getattr(t, "status_color", None)
        inline_label = t.get("status_label") if is_dict else getattr(t, "status_label", None)
        inline_category = t.get("status_category") if is_dict else getattr(t, "status_category", None)
        if inline_color:
            color, label, category = inline_color, (inline_label or st), inline_category
        else:
            m = meta.get(st) or {}
            color = m.get("color") or FALLBACK_COLOR
            label = m.get("label") or (st or "").upper()
            category = m.get("category")
        if is_dict:
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
