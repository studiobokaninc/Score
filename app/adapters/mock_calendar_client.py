"""MockCalendarClient — state file (YAML) で永続化・CALENDAR_MOCK=1 時に使用。

殿御命 (2026-05-21 22:15→23:00): cmd_461 mock DB 第2陣 (Lord 側 Claude Code session) 直接実装。
- GET 系: state file から read (actor 依存 filter)
- POST/PATCH 系: state file に書込 (永続化)
- 登場人物 5 名 (Tanaka PM・Yamada Director・Kato Lighting Lead・Sato/Suzuki Compositor) の DB
- login form の email で人物切替 → 各ロール視点で Score 体験
- state file 不在ならば _DEFAULT_STATE から init
- CALENDAR_MOCK_RESET=1 で state 再生成
- CALENDAR_MOCK_STATE_FILE で path override 可

CALENDAR_MOCK=0 (or unset) 時は本層 bypass=本物 CalendarClient 経由 (退行ゼロ)。

殿 demo 操作:
  /login で email 入力 (例 tanaka@studiobokan.com) → 該当ロールで dashboard 等表示
  logout → /login で別 user の email → 別ロール視点へ切替
"""
import os
import fcntl
from datetime import datetime
from pathlib import Path

import yaml

from app.adapters.calendar_client import CalendarClient
from app.adapters.dto import CalendarShot, CalendarUser, CalendarTask

# ────────────────────────────────────────────────────────────────
# 登場人物 5 名 + 各自データ (殿 demo 用 fixture)
# ────────────────────────────────────────────────────────────────
_DEFAULT_STATE = {
    # 登場人物 5 名 (Calendar API spec: Tanaka=admin・他=user / Score 上の ScoreUserRole 分離)
    "users": [
        {
            "user_id": 1, "email": "tanaka@studiobokan.com",
            "name": "田中 太郎", "role": "pm",   # PM = admin (Calendar) / pm (Score Role)
            "iconUrl": None, "title": "プロダクションマネージャー",
        },
        {
            "user_id": 10, "email": "yamada@studiobokan.com",
            "name": "山田 博", "role": "director",
            "iconUrl": None, "title": "ディレクター",
        },
        {
            "user_id": 20, "email": "kato@studiobokan.com",
            "name": "加藤 健司", "role": "lead",  # 殿御命 2026-06-05: lighting_lead → lead (各分野 Lead)
            "iconUrl": None, "title": "各分野 Lead (Lighting/Animation/FX 等)",
        },
        {
            "user_id": 30, "email": "sato@studiobokan.com",
            "name": "佐藤 花子", "role": "user",  # 一般業務担当 (Compositor)
            "iconUrl": None, "title": "User (Compositor/Animator/FX 等の一般業務)",
        },
        {
            "user_id": 40, "email": "suzuki@studiobokan.com",
            "name": "鈴木 一郎", "role": "user",  # 一般業務担当
            "iconUrl": None, "title": "User (一般業務担当)",
        },
        # 殿 (殿御命 2026-06-05: lead → admin・内容変更なし)
        {
            "user_id": 99, "email": "ryoji@studiobokan.com",
            "name": "Ryoji Bokan", "role": "admin",
            "iconUrl": None, "title": "殿",
        },
    ],

    # Project 一覧 (全 user 共通の権威 data)
    "projects": [
        {"id": 33, "name": "Ramps", "status": "in-progress",
         "description": "メインプロジェクト・全 5 名が関与",
         "color": "#4A90E2"},
        {"id": 34, "name": "Bali", "status": "in-progress",
         "description": "サブプロジェクト",
         "color": "#7ED321"},
        {"id": 35, "name": "Nina PARK 資料作成", "status": "in-progress",
         "description": "資料作成プロジェクト",
         "color": "#F5A623"},
    ],

    # 各 user の担当 project mapping (Score 側 ScoreUserRole 相当)
    "user_projects": {
        # PM=全 project に admin として関与
        1:  [{"project_id": 33, "role": "pm"}, {"project_id": 34, "role": "pm"}, {"project_id": 35, "role": "pm"}],
        # Director=Ramps と Bali を担当
        10: [{"project_id": 33, "role": "director"}, {"project_id": 34, "role": "director"}],
        # Lead=Ramps の各分野統括 (旧 lighting_lead)
        20: [{"project_id": 33, "role": "lead"}],
        # Sato (User: 一般業務)=Ramps を担当 (旧 compositor)
        30: [{"project_id": 33, "role": "user"}],
        # Suzuki (User: 一般業務)=Ramps と Bali を担当 (旧 compositor)
        40: [{"project_id": 33, "role": "user"}, {"project_id": 34, "role": "user"}],
        # 殿=Ramps と Bali を User として (旧 compositor)
        99: [{"project_id": 33, "role": "user"}, {"project_id": 34, "role": "user"}],
    },

    # Shots (project 33=Ramps 中心)
    "shots": [
        {"id": 1, "project_id": 33, "name": "SHOT_001", "status": "in_review",
         "shot_code": "SHOT_001", "seq_code": "SEQ01"},
        {"id": 2, "project_id": 33, "name": "SHOT_002", "status": "retake",
         "shot_code": "SHOT_002", "seq_code": "SEQ01"},
        {"id": 3, "project_id": 33, "name": "SHOT_003", "status": "in_progress",
         "shot_code": "SHOT_003", "seq_code": "SEQ02"},
        {"id": 4, "project_id": 33, "name": "SHOT_004", "status": "open",
         "shot_code": "SHOT_004", "seq_code": "SEQ02"},
        {"id": 5, "project_id": 34, "name": "BALI_001", "status": "in_progress",
         "shot_code": "BALI_001", "seq_code": "SEQ01"},
    ],

    # Tasks (shot 配下 5 工程 × 各 user の assign)
    "tasks": [
        # SHOT_001 (Ramps) — 殿 (Sato/Compositor 役割) と Yamada(Director レビュー)
        {"task_id": 11, "shot_id": 1, "type": "Animation", "assignee_id": 30, "status": "approved"},
        {"task_id": 12, "shot_id": 1, "type": "Lighting", "assignee_id": 30, "status": "retake"},
        {"task_id": 13, "shot_id": 1, "type": "FX", "assignee_id": 30, "status": "reviewing"},
        {"task_id": 14, "shot_id": 1, "type": "MattePaint", "assignee_id": 30, "status": "retake"},
        {"task_id": 15, "shot_id": 1, "type": "Composite", "assignee_id": 30, "status": "open"},
        # SHOT_002 (Ramps) — Suzuki がメイン
        {"task_id": 21, "shot_id": 2, "type": "Animation", "assignee_id": 40, "status": "approved"},
        {"task_id": 22, "shot_id": 2, "type": "Lighting", "assignee_id": 20, "status": "reviewing"},
        {"task_id": 23, "shot_id": 2, "type": "Composite", "assignee_id": 40, "status": "retake"},
        # SHOT_003 (Ramps) — Suzuki Compositor + Kato Look 統括
        {"task_id": 31, "shot_id": 3, "type": "Animation", "assignee_id": 40, "status": "approved"},
        {"task_id": 32, "shot_id": 3, "type": "Lighting", "assignee_id": 20, "status": "reviewing"},
        {"task_id": 33, "shot_id": 3, "type": "Look", "assignee_id": 40, "status": "reviewing"},
        {"task_id": 34, "shot_id": 3, "type": "FX", "assignee_id": 40, "status": "open"},
        {"task_id": 35, "shot_id": 3, "type": "Composite", "assignee_id": 40, "status": "open"},
        # SHOT_004 (Ramps) — Sato 担当
        {"task_id": 41, "shot_id": 4, "type": "Composite", "assignee_id": 30, "status": "open"},
        # BALI_001 (Bali) — 殿が Compositor
        {"task_id": 51, "shot_id": 5, "type": "Composite", "assignee_id": 99, "status": "in_progress"},
    ],

    # Notifications (各 user 宛 — actor_user_id で filter)
    "notifications": [
        {"id": 1, "user_id": 30, "title": "QC コメント",
         "body": "SHOT_001 修正依頼 (山田より)", "read": False, "created_at": "2026-05-21"},
        {"id": 2, "user_id": 30, "title": "Look 配布",
         "body": "Ramps v04 配布 (加藤より)", "read": False, "created_at": "2026-05-20"},
        {"id": 3, "user_id": 10, "title": "リテイク確認待ち",
         "body": "SHOT_002 v05 確認願います", "read": False, "created_at": "2026-05-21"},
        {"id": 4, "user_id": 20, "title": "トラブル報告",
         "body": "SHOT_003 レンダー遅延の件", "read": False, "created_at": "2026-05-21"},
        {"id": 5, "user_id": 1, "title": "デリバリ準備",
         "body": "Ramps v04 納品準備お願いします", "read": False, "created_at": "2026-05-21"},
        {"id": 6, "user_id": 40, "title": "SHOT_002 リテイク",
         "body": "SHOT_002 Composite 修正お願いします", "read": False, "created_at": "2026-05-21"},
        {"id": 7, "user_id": 99, "title": "Welcome",
         "body": "殿 demo 環境へようこそ", "read": False, "created_at": "2026-05-21"},
    ],

    # Messages — 5人で SHOT_001/002/003 制作現場の物語 thread (14件・DM+channel mix)
    # schema: id/sender_id/sender/recipient_id(None=channel)/channel(DM時None)
    #         /reply_to(thread親 None=新規)/shot_id/project_id/body/created_at
    "messages": [
        # ── SHOT_001 リテイク → approve thread (Sato 担当) ──
        {"id": 1, "sender_id": 1, "sender": "田中", "recipient_id": 10, "channel": None,
         "reply_to": None, "shot_id": 1, "project_id": 33,
         "body": "SHOT_001 v04 上がりました・確認お願いします", "created_at": "2026-05-21 09:15"},
        {"id": 2, "sender_id": 10, "sender": "山田", "recipient_id": 30, "channel": None,
         "reply_to": 1, "shot_id": 1, "project_id": 33,
         "body": "確認しました・MattePaint と Lighting にリテイク必要です", "created_at": "2026-05-21 09:30"},
        {"id": 3, "sender_id": 30, "sender": "佐藤", "recipient_id": 10, "channel": None,
         "reply_to": 2, "shot_id": 1, "project_id": 33,
         "body": "了解です・修正中・19時頃完了予定", "created_at": "2026-05-21 10:00"},
        {"id": 4, "sender_id": 30, "sender": "佐藤", "recipient_id": None, "channel": "ramps",
         "reply_to": 3, "shot_id": 1, "project_id": 33,
         "body": "SHOT_001 v05 完了・ご確認願います", "created_at": "2026-05-21 19:20"},
        {"id": 5, "sender_id": 10, "sender": "山田", "recipient_id": 30, "channel": None,
         "reply_to": 4, "shot_id": 1, "project_id": 33,
         "body": "v05 拝見・OK approve しました", "created_at": "2026-05-21 19:45"},

        # ── SHOT_003 Look 検討 thread (Kato 統括 → Suzuki 適用) ──
        {"id": 6, "sender_id": 10, "sender": "山田", "recipient_id": 20, "channel": None,
         "reply_to": None, "shot_id": 3, "project_id": 33,
         "body": "SHOT_003 の Look 検討お願いします", "created_at": "2026-05-20 13:00"},
        {"id": 7, "sender_id": 20, "sender": "加藤", "recipient_id": None, "channel": "ramps",
         "reply_to": 6, "shot_id": 3, "project_id": 33,
         "body": "Ramps v04 として Look 配布資料作成しました", "created_at": "2026-05-20 13:30"},
        {"id": 8, "sender_id": 20, "sender": "加藤", "recipient_id": 40, "channel": None,
         "reply_to": 7, "shot_id": 3, "project_id": 33,
         "body": "Ramps v04 配布完了・確認願います", "created_at": "2026-05-20 14:00"},
        {"id": 9, "sender_id": 40, "sender": "鈴木", "recipient_id": 20, "channel": None,
         "reply_to": 8, "shot_id": 3, "project_id": 33,
         "body": "確認しました・SHOT_003 に適用します", "created_at": "2026-05-20 16:00"},

        # ── SHOT_003 トラブル対応 thread (Suzuki → Kato → Tanaka) ──
        {"id": 10, "sender_id": 40, "sender": "鈴木", "recipient_id": 20, "channel": None,
         "reply_to": None, "shot_id": 3, "project_id": 33,
         "body": "SHOT_003 レンダー遅延の件 troubles 起票しました", "created_at": "2026-05-21 11:00"},
        {"id": 11, "sender_id": 20, "sender": "加藤", "recipient_id": 1, "channel": None,
         "reply_to": 10, "shot_id": 3, "project_id": 33,
         "body": "SHOT_003 レンダーノード追加要請お願いします", "created_at": "2026-05-21 11:30"},
        {"id": 12, "sender_id": 1, "sender": "田中", "recipient_id": None, "channel": "ramps",
         "reply_to": 11, "shot_id": 3, "project_id": 33,
         "body": "レンダーノード追加 OK・木曜まで作業時間延長します", "created_at": "2026-05-21 12:15"},

        # ── SHOT_002 リテイク thread (Director → Suzuki) ──
        {"id": 13, "sender_id": 10, "sender": "山田", "recipient_id": 40, "channel": None,
         "reply_to": None, "shot_id": 2, "project_id": 33,
         "body": "SHOT_002 v04 確認・リテイク発行しました", "created_at": "2026-05-21 10:00"},
        {"id": 14, "sender_id": 40, "sender": "鈴木", "recipient_id": 10, "channel": None,
         "reply_to": 13, "shot_id": 2, "project_id": 33,
         "body": "SHOT_002 リテイク確認しました・対応します", "created_at": "2026-05-21 14:00"},
    ],

    # 各 user の routines_latest (最新 routine 報告)
    "user_routines": {
        30: {  # Sato
            "prev_tasks": [
                {"title": "SHOT_001 Composite 修正", "status": "done"},
                {"title": "QC チェック対応", "status": "done"},
            ]
        },
        40: {  # Suzuki
            "prev_tasks": [
                {"title": "SHOT_002 Animation 仕上げ", "status": "done"},
                {"title": "SHOT_003 FX 着手", "status": "in_progress"},
            ]
        },
        20: {  # Kato
            "prev_tasks": [
                {"title": "Ramps Look 全体調整", "status": "done"},
                {"title": "Kato lighting troubleshoot", "status": "done"},
            ]
        },
        10: {  # Yamada
            "prev_tasks": [
                {"title": "SHOT_001/002 QC レビュー", "status": "done"},
                {"title": "SHOT_002 リテイク発行", "status": "done"},
            ]
        },
        1: {  # Tanaka
            "prev_tasks": [
                {"title": "Ramps 全体進行確認", "status": "done"},
                {"title": "デリバリ準備チェック", "status": "in_progress"},
            ]
        },
        99: {  # 殿
            "prev_tasks": [
                {"title": "Score demo 確認", "status": "done"},
                {"title": "Mock DB 体験", "status": "in_progress"},
            ]
        },
    },

    "troubles": [
        {"id": 1, "title": "SHOT_003 レンダー遅延",
         "actor_id": 40, "assignee_id": 20, "status": "open",
         "created_at": "2026-05-21"},
    ],

    "look_distributions": [
        {"id": 1, "name": "Ramps v04", "shot_ids": [1, 2, 3],
         "source_director": 10, "assignee_id": 20, "status": "pending"},
    ],

    "reference_materials": [
        {"id": 1, "title": "Color Reference v04 (Ramps)", "url": "#",
         "project_id": 33, "tags": ["color", "ramps"]},
        {"id": 2, "title": "Look Dev Ref (Bali)", "url": "#",
         "project_id": 34, "tags": ["look", "bali"]},
    ],

    "events": [
        {"id": 1, "user_ids": [1, 10, 20, 30, 40], "time": "10:00",
         "title": "朝会 — Ramps チーム", "date": "2026-05-22"},
        {"id": 2, "user_ids": [10, 30, 40], "time": "14:00",
         "title": "QC レビュー — SHOT_002", "date": "2026-05-22"},
    ],

    # 各 user の meeting_tasks (AI スケジュール提案)
    "user_meeting_tasks": {
        30: [{"id": 1, "content": "SHOT_001 修正 — 推定3時間", "suggested_assignee": 30, "status": "pending"}],
        40: [{"id": 2, "content": "SHOT_003 FX 着手 — 推定5時間", "suggested_assignee": 40, "status": "pending"}],
    },

    # 各 project の team_members
    "team_members_by_project": {
        "33": [  # Ramps
            {"user_id": 1, "name": "田中 太郎", "role": "PM"},
            {"user_id": 10, "name": "山田 博", "role": "Director"},
            {"user_id": 20, "name": "加藤 健司", "role": "Lighting Lead"},
            {"user_id": 30, "name": "佐藤 花子", "role": "Compositor"},
            {"user_id": 40, "name": "鈴木 一郎", "role": "Compositor"},
        ],
        "34": [  # Bali
            {"user_id": 1, "name": "田中 太郎", "role": "PM"},
            {"user_id": 10, "name": "山田 博", "role": "Director"},
            {"user_id": 40, "name": "鈴木 一郎", "role": "Compositor"},
            {"user_id": 99, "name": "Ryoji Bokan", "role": "Compositor"},
        ],
        "35": [  # Nina PARK
            {"user_id": 1, "name": "田中 太郎", "role": "PM"},
        ],
    },

    "groups": [],
    "retakes": [],
    "approvals": [],
    "timecards": [],
    "routines_log": [],     # 過去 routines 履歴 (post_routines 結果)
    "change_requests": [],
    "shot_comments": {},     # {str(shot_id): [{id, body, ...}]}
    "assets": [],
}

# 議事録 fallback (state.meetings 不在時の暫定・2nd 将軍 schema 化予定)
# Calendar の Meeting model 準拠: id/project_id/title/date/transcript/decisions/tasks/discussion_points/deadlines/status
_MOCK_MEETINGS_FALLBACK = [
    {
        "id": 1,
        "project_id": 33,
        "title": "Ramps チーム 朝会 (2026-05-22)",
        "date": "2026-05-22T11:30:00",
        "status": "completed",
        "transcript": "Yamada: 本日の議題は SHOT_001 v04 Retake 方針・SHOT_002 緊急対応・SHOT_003 Look レビュー。Tanaka: スケジュール厳守 5/15。Kato: Look_v05 配布予定 17:00。各 Compositor は Look_v05 適用後の調整推奨。Yamada: 朝会終了。SHOT_001 Retake は 12:00 ちょうどで発令します。",
        "decisions": [
            "SHOT_001 v04 → Retake 発令(Composite + MattePaint + Lighting 3 工程)",
            "SHOT_002 緊急対応・Suzuki 担当・11:00 clients review 間に合わせる",
            "Look_v05 配布: 17:00 (Kato)",
        ],
        "tasks": [
            "Sato: SHOT_001 Composite v05 提出 (〆切 19:00)",
            "Sato: SHOT_001 MattePaint v05 提出 (〆切 15:30)",
            "Sato: SHOT_001 Lighting v05 調整 (Look_v05 適用後)",
            "Suzuki: SHOT_002 Composite v05 提出 (〆切 10:00)",
            "Kato: Look_v05 配布 (17:00)",
        ],
        "discussion_points": [
            "SHOT_001 の色温度・寒色寄りの判断基準",
            "SHOT_002 緊急対応の代休スケジュール調整",
        ],
        "deadlines": [
            "2026-05-15: SHOT_001 v05 final",
            "2026-05-23 11:00: SHOT_002 clients review",
        ],
        "version_group": "ramps_morning",
        "created_at": "2026-05-22T11:30:00",
    },
    {
        "id": 2,
        "project_id": 33,
        "title": "Ramps 中間レビュー (2026-05-22 14:00)",
        "date": "2026-05-22T14:00:00",
        "status": "completed",
        "transcript": "Yamada: SHOT_001 v05 進捗確認。Tanaka: SHOT_003 Look の Kato 上申待ち。Sato: MattePaint v05 はほぼ完了・Composite 並行作業中。",
        "decisions": [
            "SHOT_001 MattePaint v05 → Approved 見込み(16:00 確定)",
            "SHOT_003 Look v02 → Director 判定後 Compositor 配布",
        ],
        "tasks": [
            "Director Yamada: SHOT_003 Look v02 判定 (Kato 上申分)",
        ],
        "discussion_points": [
            "SHOT_001 の Lighting 統一性",
        ],
        "deadlines": [
            "2026-05-22 16:00: MattePaint v05 Approved 判定",
            "2026-05-22 17:00: Look_v05 配布",
        ],
        "version_group": "ramps_mid_review",
        "created_at": "2026-05-22T14:00:00",
    },
]

# ────────────────────────────────────────────────────────────────
# state file 操作 (yaml + flock)
# ────────────────────────────────────────────────────────────────
_STATE_FILE = Path(os.environ.get("CALENDAR_MOCK_STATE_FILE", "/tmp/score_mock_state.yaml"))


def _init_state() -> None:
    """state file 不在 or CALENDAR_MOCK_RESET=1 ならば _DEFAULT_STATE で初期化。"""
    if _STATE_FILE.exists() and os.environ.get("CALENDAR_MOCK_RESET") != "1":
        return
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(_DEFAULT_STATE, f, allow_unicode=True, sort_keys=False)
    os.environ.pop("CALENDAR_MOCK_RESET", None)


def _read_state() -> dict:
    """state file の現在値を読込。shared lock。"""
    _init_state()
    with open(_STATE_FILE, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            return yaml.safe_load(f) or {}
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _update_state(updater):
    """exclusive lock で state を read-modify-write。
    updater(state: dict) → 戻り値 (返却用 entry)。state は inplace で更新可。"""
    _init_state()
    with open(_STATE_FILE, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            state = yaml.safe_load(f) or {}
            result = updater(state)
            f.seek(0)
            f.truncate()
            yaml.safe_dump(state, f, allow_unicode=True, sort_keys=False)
            return result
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _next_id(items: list) -> int:
    """list of dict から最大 id+1 を返す (空なら 1)。"""
    if not items:
        return 1
    return max((m.get("id", 0) for m in items if isinstance(m, dict)), default=0) + 1


def _find_user(state: dict, user_id) -> dict | None:
    """user_id (int/str) から user 辞書を返す。"""
    try:
        target_id = int(user_id)
    except (ValueError, TypeError):
        return None
    for u in state.get("users", []):
        if u.get("user_id") == target_id:
            return u
    return None


# ────────────────────────────────────────────────────────────────
# MockCalendarClient — state 連動・登場人物 5 名対応
# ────────────────────────────────────────────────────────────────
class MockCalendarClient(CalendarClient):
    """CalendarClient と同 interface・state file (YAML) で永続化。

    殿 demo 操作:
      /login で email 入力 (tanaka/yamada/kato/sato/suzuki @studiobokan.com)
      → 該当ロールで Score 各画面が見える
      logout → 別 email で login → 別ロール視点
    """

    def __init__(self):
        self._user_id_cache = {}
        _init_state()

    # ── ユーザー解決 (login flow の根幹) ──
    def resolve_email_to_user_id(self, email: str) -> int | None:
        """email から user_id を引く。未登録 email は None (fail-closed)。"""
        if not email:
            return None
        state = _read_state()
        for u in state.get("users", []):
            if u.get("email", "").lower() == email.lower():
                return u["user_id"]
        return None

    # ── GET 系 (state 経由 read・actor 依存) ──
    def get_me(self, actor_user_id: str | None = None) -> CalendarUser:
        """actor_user_id から該当 user を返す。未指定/未登録なら 殿 (id 99) を default。"""
        state = _read_state()
        if actor_user_id is not None:
            u = _find_user(state, actor_user_id)
            if u:
                return CalendarUser(
                    user_id=u["user_id"], email=u["email"],
                    name=u["name"], role=u["role"],
                )
        # default = 殿
        default_u = next((x for x in state.get("users", []) if x.get("user_id") == 99), None)
        if default_u:
            return CalendarUser(
                user_id=default_u["user_id"], email=default_u["email"],
                name=default_u["name"], role=default_u["role"],
            )
        # final fallback
        return CalendarUser(user_id=99, email="ryoji@studiobokan.com",
                            name="Ryoji Bokan", role="lead")

    def get_shots(self, project_id: int, actor_user_id: str | None = None) -> list[CalendarShot]:
        return [
            CalendarShot(
                shot_id=s["id"], project_id=s["project_id"],
                name=s["name"], status=s["status"],
                shot_code=s.get("shot_code"), seq_code=s.get("seq_code"),
            )
            for s in _read_state().get("shots", [])
            if s.get("project_id") == project_id
        ]

    def get_shot(self, shot_id: int, actor_user_id: str | None = None) -> CalendarShot | None:
        for s in _read_state().get("shots", []):
            if s.get("id") == shot_id:
                return CalendarShot(
                    shot_id=s["id"], project_id=s["project_id"],
                    name=s["name"], status=s["status"],
                    shot_code=s.get("shot_code"), seq_code=s.get("seq_code"),
                )
        return None

    def get_shot_detail(self, shot_id: int, actor_user_id: str | None = None) -> dict:
        state = _read_state()
        for s in state.get("shots", []):
            if s.get("id") == shot_id:
                # 殿御命 2026-06-08: asset_list を attach (mock asset を shot_id で filter)
                out = dict(s)
                out["asset_list"] = [a for a in state.get("assets", []) if a.get("shot_id") == shot_id]
                return out
        shot = self.get_shot(shot_id, actor_user_id)
        if shot is None:
            return {}
        return {"id": shot.shot_id, "project_id": shot.project_id,
                "shot_code": shot.shot_code, "seq_code": shot.seq_code,
                "status": shot.status, "name": shot.name,
                "asset_list": [a for a in state.get("assets", []) if a.get("shot_id") == shot_id]}

    def get_tasks(self, shot_id: int, actor_user_id: str | None = None) -> list[CalendarTask]:
        return [
            CalendarTask(
                task_id=t["task_id"], shot_id=t["shot_id"],
                type=t["type"], assignee_id=t["assignee_id"], status=t["status"],
            )
            for t in _read_state().get("tasks", [])
            if t.get("shot_id") == shot_id
        ]

    def get_my_projects(self, actor_user_id: str) -> list:
        """actor の担当 project 一覧を返す (各 user で見える project が違う)。"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else None
        except (ValueError, TypeError):
            uid = None
        if uid is None:
            return state.get("projects", [])
        user_projects = state.get("user_projects", {}).get(uid, [])
        # int key vs str key 両対応
        if not user_projects:
            user_projects = state.get("user_projects", {}).get(str(uid), [])
        project_ids = [up.get("project_id") for up in user_projects]
        return [
            {**p, "role": next((up["role"] for up in user_projects if up["project_id"] == p["id"]), None)}
            for p in state.get("projects", [])
            if p.get("id") in project_ids
        ]

    def get_my_project_detail(self, project_id: int, actor_user_id: str | None = None) -> dict:
        """GET /api/me/projects/{id} mock — get_my_projects_detail に委譲"""
        return self.get_my_projects_detail(project_id, actor_user_id)

    def get_my_shots(self, actor_user_id: str) -> list:
        """actor が担当する shot 一覧 (tasks.assignee_id で filter)。"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else None
        except (ValueError, TypeError):
            uid = None
        if uid is None:
            return state.get("shots", [])
        my_task_shot_ids = {t["shot_id"] for t in state.get("tasks", [])
                            if t.get("assignee_id") == uid}
        return [s for s in state.get("shots", []) if s.get("id") in my_task_shot_ids]

    def get_production_tracker(self, project_id: str, actor_user_id: str) -> dict:
        return {"entries": [], "project_id": project_id}

    def get_meetings(self, project_id: int, actor_user_id: str | None = None) -> list:
        """state.meetings から project 別 filter で返却。
        meetings record schema: id / project_id / title / date / transcript / decisions[] / tasks[] / discussion_points[] / deadlines[] / status / event_id?
        2nd 将軍 schema 拡張で state.meetings 充実予定・暫定 _DEFAULT_STATE fallback あり"""
        state = _read_state()
        all_meetings = state.get("meetings", [])
        if not all_meetings:
            # state に未存在の場合 _DEFAULT 風 fallback (今後 2nd 将軍が schema/data 化予定)
            return _MOCK_MEETINGS_FALLBACK if int(project_id) == 33 else []
        try:
            pid = int(project_id)
        except (ValueError, TypeError):
            return all_meetings
        return [m for m in all_meetings if m.get("project_id") == pid]

    def get_holidays(self, year: int, actor_user_id: str | None = None) -> list:
        """日本祝日 mock (本式は Calendar /api/holidays)"""
        return [
            {"date": f"{year}-01-01", "name": "元日"},
            {"date": f"{year}-01-13", "name": "成人の日"},
            {"date": f"{year}-02-11", "name": "建国記念の日"},
            {"date": f"{year}-02-23", "name": "天皇誕生日"},
            {"date": f"{year}-03-20", "name": "春分の日"},
            {"date": f"{year}-04-29", "name": "昭和の日"},
            {"date": f"{year}-05-03", "name": "憲法記念日"},
            {"date": f"{year}-05-04", "name": "みどりの日"},
            {"date": f"{year}-05-05", "name": "こどもの日"},
            {"date": f"{year}-07-21", "name": "海の日"},
            {"date": f"{year}-08-11", "name": "山の日"},
            {"date": f"{year}-09-15", "name": "敬老の日"},
            {"date": f"{year}-09-22", "name": "秋分の日"},
            {"date": f"{year}-10-13", "name": "スポーツの日"},
            {"date": f"{year}-11-03", "name": "文化の日"},
            {"date": f"{year}-11-23", "name": "勤労感謝の日"},
        ]

    def get_meetings_by_event(self, event_id: int, actor_user_id: str | None = None) -> list:
        """event 紐付き議事録 mock (本式は Calendar /api/events/{id}/meetings)"""
        state = _read_state()
        all_meetings = state.get("meetings", []) or _MOCK_MEETINGS_FALLBACK
        return [m for m in all_meetings if m.get("event_id") == int(event_id)]

    def get_meeting(self, meeting_id: int, actor_user_id: str | None = None) -> dict:
        """議事録単体 mock (本式は Calendar /api/meetings/{id})"""
        state = _read_state()
        all_meetings = state.get("meetings", []) or _MOCK_MEETINGS_FALLBACK
        for m in all_meetings:
            if m.get("id") == int(meeting_id):
                return m
        return {}

    def post_meeting(self, project_id: int, body: dict, actor_user_id: str | None = None) -> dict:
        """手動議事録投稿 mock (本式は Calendar POST /api/projects/{id}/meetings)"""
        def upd(state):
            mtgs = state.setdefault("meetings", [])
            new_id = max([m.get("id", 0) for m in mtgs] + [0]) + 1
            entry = {
                "id": new_id,
                "project_id": int(project_id),
                "status": "completed",
                "created_at": datetime.now().isoformat(),
                **body,
            }
            mtgs.append(entry)
            return entry
        return _update_state(upd)

    def patch_meeting(self, meeting_id: int, body: dict, actor_user_id: str | None = None) -> dict:
        """議事録更新 mock (本式は Calendar PATCH /api/meetings/{id})"""
        def upd(state):
            for m in state.get("meetings", []):
                if m.get("id") == int(meeting_id):
                    m.update(body)
                    return m
            return {}
        return _update_state(upd)

    # ── §5-bis profile mock (state.users 経由) ──
    def get_my_profile(self, actor_user_id: str) -> dict:
        """自身の profile 取得 mock (state.users から actor の record 引出)"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        for u in state.get("users", []):
            if u.get("user_id") == uid:
                # demo 用 default 補完
                prof = dict(u)
                prof.setdefault("birthday", None)
                prof.setdefault("bio", None)
                prof.setdefault("phone", None)
                prof.setdefault("line_id", None)
                prof.setdefault("work_start_time", "10:00")
                prof.setdefault("work_end_time", "19:00")
                prof.setdefault("skills", [])
                prof.setdefault("settings_json", {})
                prof.setdefault("google_linked", False)
                prof.setdefault("google_email", None)
                prof.setdefault("avatar_url", None)
                prof.setdefault("is_active", True)
                return prof
        return {}

    def patch_my_profile(self, body: dict, actor_user_id: str) -> dict:
        """自身の profile 更新 mock (state.users の actor record を merge)"""
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        def upd(state):
            for u in state.get("users", []):
                if u.get("user_id") == uid:
                    # editable fields のみ受入
                    editable = {'full_name', 'name', 'birthday', 'bio', 'phone', 'line_id',
                                'work_start_time', 'work_end_time', 'skills',
                                'settings_json', 'google_linked', 'google_email', 'avatar_url'}
                    for k, v in body.items():
                        if k in editable:
                            u[k] = v
                    return u
            return {}
        return _update_state(upd)

    def get_user_profile(self, user_id: int, actor_user_id: str) -> dict:
        """他者 profile mock (Calendar 4 段階 access 制御を簡略実装)"""
        state = _read_state()
        target = None
        actor = None
        try:
            actor_uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            actor_uid = 0
        for u in state.get("users", []):
            if u.get("user_id") == int(user_id):
                target = dict(u)
            if u.get("user_id") == actor_uid:
                actor = u
        if not target:
            return {}
        # 公開範囲制御 (簡易)
        actor_role = (actor or {}).get("role", "")
        is_admin = actor_role in ("pm", "admin", "director")
        is_self = (target.get("user_id") == actor_uid)
        # 全員公開: name, role, avatar_url, bio, skills (常に開示)
        # 本人限定: settings_json, google_linked, google_email (他者は None)
        if not is_self:
            target["settings_json"] = None
            target["google_linked"] = False
            target["google_email"] = None
        # admin only: phone
        if not (is_admin or is_self):
            target["phone"] = None
        return target

    def get_my_tasks(self, actor_user_id: str | None = None) -> list:
        """自分の担当 task mock"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        return [t for t in state.get("tasks", []) if t.get("assignee_id") == uid]

    def get_my_dm_threads(self, actor_user_id: str | None = None) -> list:
        """殿御命 2026-06-08: 自分が participants な thread list を返す mock。
        state.yaml の dm_threads section から filter。"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        out = []
        for thr in state.get("dm_threads", []) or []:
            participants = thr.get("participants") or []
            if uid in participants or str(uid) in [str(p) for p in participants]:
                out.append(dict(thr))
        return out

    def get_users(self, actor_user_id: str | None = None) -> list:
        """全 user 一覧 mock (state.users)。real Calendar API: GET /api/users 相当。"""
        state = _read_state()
        out = []
        for u in state.get("users", []) or []:
            entry = dict(u)
            # real BE は 'id' field を返すと推測される — alias を併設
            if "id" not in entry and "user_id" in entry:
                entry["id"] = entry["user_id"]
            out.append(entry)
        return out

    def get_tasks_by_project(self, project_id: int, actor_user_id: str | None = None) -> list:
        """project 配下の全 task を返す mock (shot.project_id 経由で紐付け)。
        Real Calendar API: GET /api/tasks?project_id=N の挙動を模倣。
        task の shot_id を shots テーブルで引いて project_id 一致する物のみ返す。"""
        state = _read_state()
        shots = state.get("shots", []) or []
        # shot_id → project_id map
        shot_pid: dict = {}
        for s in shots:
            sid = s.get("id") or s.get("shot_id")
            if sid is not None:
                shot_pid[sid] = s.get("project_id")
        # 殿御命 2026-06-08: shotID + seqID を attach (pages_project_detail.py の seq_groups 構築用)
        shot_info_map = {}
        for s in shots:
            sid = s.get("id") or s.get("shot_id")
            if sid is not None:
                shot_info_map[sid] = {
                    "shot_code": s.get("shot_code") or s.get("name") or f"SHOT_{sid:03d}",
                    "seq_code": s.get("seq_code", ""),
                }
        out = []
        for t in state.get("tasks", []):
            sid = t.get("shot_id")
            if sid is not None and shot_pid.get(sid) == project_id:
                entry = dict(t)
                entry.setdefault("id", t.get("task_id") or t.get("id"))
                entry.setdefault("assigned_to", t.get("assignee_id"))
                _si = shot_info_map.get(sid, {})
                entry.setdefault("shotID", _si.get("shot_code", ""))
                entry.setdefault("seqID", _si.get("seq_code", ""))
                out.append(entry)
        return out

    def get_my_notifications(self, actor_user_id: str | None = None) -> list:
        """自分宛通知 mock"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        items = [n for n in state.get("notifications", []) if n.get("user_id") == uid]
        items.sort(key=lambda n: (n.get("read", False), n.get("created_at", "")))
        return items[:50]

    def get_my_retakes(self, actor_user_id: str | None = None) -> list:
        """自分発行 retake mock"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        return [r for r in state.get("retakes", []) if r.get("actor") == uid]

    def get_my_troubles(self, actor_user_id: str | None = None) -> list:
        """自分関連 trouble mock"""
        state = _read_state()
        try:
            uid = int(actor_user_id) if actor_user_id else 0
        except (ValueError, TypeError):
            uid = 0
        return [tr for tr in state.get("troubles", [])
                if tr.get("actor_id") == uid or tr.get("assignee_id") == uid]

    def get_birthdays_today(self, project_id: int, actor_user_id: str | None = None) -> list:
        """本日誕生日メンバー mock (state.users の birthday と 本日 date を照合)"""
        state = _read_state()
        today = datetime.now()
        today_md = (today.month, today.day)
        results = []
        for u in state.get("users", []):
            bd = u.get("birthday")
            if not bd:
                continue
            try:
                # 'YYYY-MM-DD' 形式想定
                parts = bd.split('-')
                if len(parts) >= 3:
                    m, d = int(parts[1]), int(parts[2])
                    if (m, d) == today_md and u.get("is_active", True):
                        results.append({
                            "user_id": u.get("user_id"),
                            "name": u.get("name"),
                            "avatar_url": u.get("avatar_url"),
                        })
            except (ValueError, TypeError):
                continue
        return results

    # ── POST/PATCH 系 (state 書込・永続化) ──
    def post_retakes(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("retakes", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "actor": str(actor_user_id),
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            return entry
        return _update_state(upd)

    def post_shot_approve(self, shot_id: int, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            for s in state.get("shots", []):
                if s.get("id") == shot_id:
                    s["status"] = "approved"
                    break
            items = state.setdefault("approvals", [])
            new_id = _next_id(items)
            items.append({"id": new_id, "shot_id": shot_id, "actor": str(actor_user_id),
                          "created_at": datetime.now().isoformat(timespec="seconds"), **body})
            return {"ok": True, "shot_id": shot_id}
        return _update_state(upd)

    def post_look_distributions(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("look_distributions", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "status": "pending",
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            return entry
        return _update_state(upd)

    def post_timecard_clock_out(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("timecards", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "actor": str(actor_user_id), "type": "clock_out",
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            return {"ok": True, "id": new_id}
        return _update_state(upd)

    def get_timecards(self, actor_user_id: str | None = None,
                       from_date: str | None = None, to_date: str | None = None,
                       limit: int | None = None) -> list[dict]:
        """actor 宛 timecard のみ返す (actor 一致)。未指定なら全件。"""
        state = _read_state()
        items = state.get("timecards", []) or []
        if actor_user_id is None:
            return list(items)
        a = str(actor_user_id)
        return [t for t in items if str(t.get("actor")) == a]

    def post_routines(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("routines_log", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "actor": str(actor_user_id),
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            # user_routines latest も更新
            try:
                uid = int(actor_user_id)
                user_routines = state.setdefault("user_routines", {})
                user_routines[uid] = body if isinstance(body, dict) else {}
            except (ValueError, TypeError):
                pass
            return entry
        return _update_state(upd)

    def post_change_requests(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("change_requests", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "actor": str(actor_user_id),
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            return entry
        return _update_state(upd)

    def post_troubles(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("troubles", [])
            new_id = _next_id(items)
            entry = {"id": new_id, "status": "open", "actor": str(actor_user_id),
                     "created_at": datetime.now().isoformat(timespec="seconds"), **body}
            items.append(entry)
            return entry
        return _update_state(upd)

    def patch_trouble_resolve(self, trouble_id: int, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            for t in state.get("troubles", []):
                if t.get("id") == trouble_id:
                    t["status"] = "resolved"
                    t["resolved_actor"] = str(actor_user_id)
                    t["resolved_at"] = datetime.now().isoformat(timespec="seconds")
                    if isinstance(body, dict):
                        t.update({k: v for k, v in body.items() if k != "status"})
                    return {"ok": True, "id": trouble_id}
            return {"ok": False, "id": trouble_id}
        return _update_state(upd)

    def post_messages(self, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            items = state.setdefault("messages", [])
            new_id = _next_id(items)
            # sender name lookup
            sender_name = str(actor_user_id)
            try:
                uid = int(actor_user_id)
                u = _find_user(state, uid)
                if u:
                    sender_name = u.get("name", str(uid))
            except (ValueError, TypeError):
                pass
            msg_body = ""
            if isinstance(body, dict):
                msg_body = body.get("body") or body.get("message") or body.get("text") or ""
            entry = {
                "id": new_id,
                "sender_id": int(actor_user_id) if str(actor_user_id).isdigit() else None,
                "sender": sender_name,
                "body": msg_body,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            if isinstance(body, dict):
                for k, v in body.items():
                    if k not in ("id", "sender", "sender_id", "body", "created_at"):
                        entry[k] = v
            items.append(entry)
            return entry
        return _update_state(upd)

    def patch_notification_read(self, notification_id: int, body: dict, actor_user_id: str) -> dict:
        def upd(state):
            for n in state.get("notifications", []):
                if n.get("id") == notification_id:
                    n["read"] = True
                    n["read_at"] = datetime.now().isoformat(timespec="seconds")
                    return {"ok": True, "id": notification_id}
            return {"ok": False, "id": notification_id}
        return _update_state(upd)

    # ── 22+4 件 未実装 API の mock (actor filter 対応) ──
    def get_notifications(self, actor_user_id: str | None = None) -> list[dict]:
        """actor 宛 notification のみ返す (user_id == actor)。"""
        state = _read_state()
        all_n = state.get("notifications", [])
        if actor_user_id is None:
            return all_n
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return all_n
        return [n for n in all_n if n.get("user_id") == uid]

    def get_messages(self, actor_user_id: str | None = None) -> list[dict]:
        """messages 一覧 (全 user 共通の channel・現状 fixture は全件返す)。"""
        return _read_state().get("messages", [])

    def get_meeting_tasks(self, actor_user_id: str | None = None) -> list[dict]:
        state = _read_state()
        if actor_user_id is None:
            return []
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return []
        return state.get("user_meeting_tasks", {}).get(uid,
               state.get("user_meeting_tasks", {}).get(str(uid), []))

    def get_routines_latest(self, actor_user_id: str | None = None) -> dict:
        state = _read_state()
        if actor_user_id is None:
            return state.get("user_routines", {}).get(99,
                   state.get("user_routines", {}).get("99", {}))
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return {}
        return state.get("user_routines", {}).get(uid,
               state.get("user_routines", {}).get(str(uid), {}))

    def get_reference_materials(self, actor_user_id: str | None = None) -> list[dict]:
        return _read_state().get("reference_materials", [])

    def get_my_projects_detail(self, project_id: int, actor_user_id: str | None = None) -> dict:
        state = _read_state()
        for p in state.get("projects", []):
            if p.get("id") == project_id:
                # team_members を含めて返す
                team = state.get("team_members_by_project", {}).get(str(project_id), [])
                return {**p, "team_members": team}
        return {}

    def get_team_members(self, project_id: int, actor_user_id: str | None = None) -> list[dict]:
        state = _read_state()
        return state.get("team_members_by_project", {}).get(str(project_id), [])

    def get_look_distributions(self, actor_user_id: str | None = None) -> list[dict]:
        """actor が assignee の look_distribution のみ (Kato 等 Lighting Lead 視点)。"""
        state = _read_state()
        all_ld = state.get("look_distributions", [])
        if actor_user_id is None:
            return all_ld
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return all_ld
        return [ld for ld in all_ld if ld.get("assignee_id") == uid or ld.get("source_director") == uid]

    def get_groups(self, actor_user_id: str | None = None) -> list[dict]:
        return _read_state().get("groups", [])

    def get_events(self, actor_user_id: str | None = None) -> list[dict]:
        """actor が user_ids に含まれる event のみ。"""
        state = _read_state()
        all_e = state.get("events", [])
        if actor_user_id is None:
            return all_e
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return all_e
        return [e for e in all_e if uid in (e.get("user_ids") or [])]

    def get_shot_comments(self, shot_id: int, actor_user_id: str | None = None) -> list[dict]:
        state = _read_state()
        return state.get("shot_comments", {}).get(str(shot_id), [])

    def get_troubles(self, actor_user_id: str | None = None) -> list[dict]:
        """actor が actor/assignee の trouble (Kato 視点で SHOT_003 trouble 等)。"""
        state = _read_state()
        all_t = state.get("troubles", [])
        if actor_user_id is None:
            return all_t
        try:
            uid = int(actor_user_id)
        except (ValueError, TypeError):
            return all_t
        return [t for t in all_t if t.get("actor_id") == uid or t.get("assignee_id") == uid]

    def get_assets_by_shot(self, shot_id: int, actor_user_id: str | None = None) -> list[dict]:
        """shot_id に紐づく asset 一覧 (7 field 確定形)。"""
        state = _read_state()
        return [a for a in state.get("assets", []) if a.get("shot_id") == shot_id]

    def post_asset(self, file_data: bytes, filename: str, content_type: str,
                   actor_user_id: str,
                   task_id: int | None = None,
                   shot_id: int | None = None,
                   project_id: int | None = None,
                   asset_type: str | None = None,
                   version: str | None = None,
                   notes: str | None = None) -> dict:
        """アセット投稿 mock — 7 field 確定形で永続化して返す。"""
        try:
            creator_id = int(actor_user_id)
        except (ValueError, TypeError):
            creator_id = 0

        def upd(state):
            items = state.setdefault("assets", [])
            new_id = _next_id(items)
            entry = {
                "id": new_id,
                "shot_id": shot_id,
                "task_id": task_id,
                "version": version or "v001",
                "file_path": f"uploads/{filename}",
                "created_by": creator_id,
                "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            items.append(entry)
            return entry

        return _update_state(upd)

    def post_my_avatar(self, file_data: bytes, filename: str, content_type: str,
                       actor_user_id: str) -> dict:
        """POST /api/me/avatar mock — state.users の avatar_url を更新して返す"""
        import time as _time
        ts = int(_time.time())
        avatar_url = f"/uploads/avatars/{actor_user_id}_{ts}_{filename}"
        def upd(state):
            for u in state.get("users", []):
                if str(u.get("id")) == str(actor_user_id) or str(u.get("user_id")) == str(actor_user_id):
                    u["avatar_url"] = avatar_url
                    break
            return {"avatar_url": avatar_url}
        return _update_state(upd)

    def get_project_directors(self, project_id: int, actor_user_id: str | None = None) -> list:
        """project_id の director uid リスト (user_projects state から resolve)"""
        state = _read_state()
        result = []
        for uid_key, roles in state.get("user_projects", {}).items():
            for r in roles:
                if r.get("project_id") == project_id and r.get("role") == "director":
                    result.append(int(uid_key))
        return result

    def get_project_pms(self, project_id: int, actor_user_id: str | None = None) -> list:
        """project_id の pm uid リスト (user_projects state から resolve)"""
        state = _read_state()
        result = []
        for uid_key, roles in state.get("user_projects", {}).items():
            for r in roles:
                if r.get("project_id") == project_id and r.get("role") == "pm":
                    result.append(int(uid_key))
        return result

    def send_notification_to_users(self, user_ids: list, title: str, body: str,
                                   actor_user_id: str | None = None) -> dict:
        """Score mock notification table に通知を追記"""
        import datetime as _dt
        def upd(state):
            notifs = state.setdefault("notifications", [])
            created = []
            for uid in user_ids:
                try:
                    uid_int = int(uid)
                except (ValueError, TypeError):
                    continue
                new_id = _next_id(notifs)
                notifs.append({
                    "id": new_id,
                    "user_id": uid_int,
                    "title": title,
                    "body": body,
                    "read": False,
                    "created_at": str(_dt.date.today()),
                    "sender_id": int(actor_user_id) if actor_user_id else None,
                })
                created.append(new_id)
            return {"ok": True, "created": created}
        return _update_state(upd)
