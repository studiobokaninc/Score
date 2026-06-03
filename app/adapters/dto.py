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
