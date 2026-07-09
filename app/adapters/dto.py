from dataclasses import dataclass


@dataclass
class CalendarUser:
    user_id: int
    email: str
    role: str
    name: str
    icon_url: str | None = None


@dataclass
class CalendarShot:
    shot_id: int
    project_id: int
    name: str
    status: str
    shot_code: str | None = None
    seq_code: str | None = None


@dataclass
class CalendarTask:
    task_id: int
    shot_id: int
    type: str
    assignee_id: int
    status: str
    # cmd_075 (2026-07-08): Calendar が task 応答に inline 同梱するようになった動的色/ラベル
    status_color: str | None = None
    status_label: str | None = None
    status_category: str | None = None
