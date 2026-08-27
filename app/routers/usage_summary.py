"""Score 利用ログ 簡易版設計計画 §5「読ませる口(1本のみ)」の実装。

GET /api/audit/usage-summary?cycle_date=YYYY-MM-DD
Authorization: Bearer <サービストークン> (+ 名乗りヘッダ。既存の
app.deps.get_actor_id のサービス資格経路をそのまま用いる。新しい鍵種別は
設けない)。

姉妹系(Calendar)が新設した「利用状況を読むだけの口」と同じ形に揃える:
- パス・クエリパラメータ名を揃える(cycle_date)
- 非人間行(user_id解決不能)を by_user に混ぜず non_human 別枠で返す
- 識別子は name と user_id のみ(email・電話・生年月日は返さない)
Calendar側にはaction別件数の内訳があるが、これは本計画(簡易版)にない
機能のため追加しない。

★★日付境界: Score は暦日(UTCの 00:00〜23:59)を集計単位とする。
朝5時JST境ではない。Calendarのcycle_dateは朝5時JST境で切られているため、
Scoreの数と単純に横並びで見ると深夜0時〜5時分の扱いが食い違う
(Score自身の内部判定—routine提出済判定・score_token失効等—は朝5時JST境
で動くが、この口が返す集計はそれとは別に暦日で切る)。occurred_atは生の
UTC時刻を記録しているため、5時境での比較が必要になった場合は集計側で
導出し直せる。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_actor_id
from app.usage_log import query_usage_summary

router = APIRouter()


def _parse_cycle_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="cycle_date must be YYYY-MM-DD")


def _resolve_names(user_ids: list, actor_id: str) -> dict:
    """user_id → name の解決。識別子は名前とuser_idのみ(email/電話/生年月日は
    返さない)。名前解決に失敗しても口全体は壊さない(fail-soft・
    該当ユーザーのnameはNoneのまま返す)。"""
    if not user_ids:
        return {}
    try:
        from app.adapters.calendar_factory import get_calendar_client

        client = get_calendar_client()
        users_raw = client.get_users(actor_user_id=actor_id) or []
    except Exception:
        return {}
    name_map = {}
    for u in users_raw:
        if not isinstance(u, dict):
            continue
        uid = u.get("id") or u.get("user_id")
        if uid is None:
            continue
        name_map[str(uid)] = u.get("name") or u.get("full_name") or u.get("username") or ""
    return name_map


@router.get("/api/audit/usage-summary")
def get_usage_summary(
    cycle_date: str = Query(...),
    actor_id: str = Depends(get_actor_id),
):
    """cycle_date 1日分の利用状況を、利用者ごとの件数・最初の刻・最後の刻で返す。

    Scoreは暦日(UTCの00:00〜23:59)を集計単位とする。朝5時JST境ではない。
    (Score自身の内部判定 — routine提出済判定・score_token失効等 — は朝5時JST境
    で動くため、それらと突き合わせる場合は暦日のままだと深夜0時〜5時分が
    食い違う。occurred_atは生UTC時刻を保持しているため、必要になれば
    5時境へ導出し直せる。)

    user_idを解決できない呼出(非人間の自動処理・未認証・トークン検証失敗を
    含む)は by_user に混ぜず non_human に別枠で集計する。識別子は name と
    user_id のみを返し、email・電話・生年月日等は返さない。
    """
    window_from_utc = _parse_cycle_date(cycle_date)
    window_to_utc = window_from_utc + timedelta(days=1)

    result = query_usage_summary(window_from_utc, window_to_utc)

    name_map = _resolve_names([u["user_id"] for u in result["by_user"]], actor_id)
    for entry in result["by_user"]:
        entry["name"] = name_map.get(entry["user_id"])

    return result
