"""cmd_151 (2026-07-30・殿御命): shot_detail.html の各アセット履歴項目に「Retake確認」
導線を出すため、指定 shot_id/task_id に紐づく retake の asset_id 集合を返す。
pages_director.py の get_retake_view が持つ /tmp/score_retake_refs/*/meta.json 走査ロジック
と同一のデータソースを参照するが、既存の get_retake_view 実装(退行禁止対象)には手を
入れず、新規の読み取り専用ヘルパーとして独立させる。"""
import json
from pathlib import Path

_REFS_ROOT = Path("/tmp/score_retake_refs")


def get_task_retake_asset_ids(shot_id, task_id) -> set[int]:
    """shot_id/task_id に一致する retake meta から asset_id を集めて返す。
    (asset_id が meta に無い古いデータは無視 — 該当 task に retake はあるが
    どの asset かは不明なため、誤って全 asset にボタンを出さないほうを選ぶ。)"""
    asset_ids: set[int] = set()
    if not _REFS_ROOT.exists():
        return asset_ids
    for d in _REFS_ROOT.iterdir():
        if not (d.is_dir() and (d / "meta.json").exists()):
            continue
        try:
            m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(m.get("shot_id")) != str(shot_id) or str(m.get("task_id")) != str(task_id):
            continue
        aid = m.get("asset_id")
        if aid not in (None, "", "None"):
            try:
                asset_ids.add(int(aid))
            except (TypeError, ValueError):
                pass
    return asset_ids
