import yaml
import os
import fcntl
import tempfile
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / "data" / "score_mock_state.yaml"
MOCK_DATA_FILE = Path(__file__).parent.parent / "fixtures" / "mock_data.yaml"
LOCK_FILE = str(STATE_FILE) + ".lock"

# Section name → id field mapping for id-based operations
_ID_FIELDS = {
    "messages": "message_id",
    "notifications": "id",
    "troubles": "id",
    "retakes": "id",
    "routines": "id",
    "shots": "id",
    "projects": "id",
    "events": "event_id",
    "threads": "thread_id",
    "meeting_tasks": "meeting_task_id",
    "look_distributions": "distribution_id",
    "groups": "group_id",
    "reference_materials": "ref_id",
    "comments": "comment_id",
    "assets": "asset_id",
    "deliveries": "delivery_id",
}


def _read_state() -> dict:
    """state ファイル読込 (存在しない場合は空 dict)"""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _write_state(state: dict) -> None:
    """atomic write: flock + tempfile + os.replace"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=STATE_FILE.parent, suffix=".yaml.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    yaml.dump(state, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                os.replace(tmp_path, STATE_FILE)
            except Exception:
                os.unlink(tmp_path)
                raise
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def init_state() -> None:
    """mock_data.yaml から state ファイルを初期化 (既存 state 上書き)"""
    with open(MOCK_DATA_FILE, "r", encoding="utf-8") as f:
        mock = yaml.safe_load(f)

    endpoints = mock.get("endpoints", {})
    state: dict = {}

    # Extract list sections from GET endpoints
    section_map = {
        "messages": "GET /api/me/messages",
        "notifications": "GET /api/me/notifications",
        "troubles": "GET /api/me/troubles",
        "retakes": "GET /api/me/retakes",
        "shots": "GET /api/me/shots",
        "projects": "GET /api/me/projects",
        "events": "GET /api/me/events",
        "threads": "GET /api/me/dm/threads",
        "meeting_tasks": "GET /api/me/meeting_tasks",
        "look_distributions": "GET /api/me/look_distributions",
        "groups": "GET /api/me/groups",
        "reference_materials": "GET /api/reference_materials",
    }

    for section, endpoint_key in section_map.items():
        endpoint_data = endpoints.get(endpoint_key, {})
        if isinstance(endpoint_data, dict):
            # endpoint data is {section: [...]}, pick the list value
            list_val = endpoint_data.get(section)
            state[section] = list(list_val) if list_val else []
        else:
            state[section] = []

    # POST-only accumulation sections start empty
    for section in ("routines", "comments", "assets", "deliveries"):
        state[section] = []

    _write_state(state)


def state_get(section: str) -> list | dict:
    """指定 section の raw data 返却 (例: 'messages' → list)"""
    state = _read_state()
    return state.get(section, [])


def state_append(section: str, entry: dict) -> dict:
    """section に entry を append・新 id 採番して返却"""
    state = _read_state()
    items = state.get(section, [])

    id_field = _ID_FIELDS.get(section, "id")
    existing_ids = [item.get(id_field, 0) for item in items if isinstance(item, dict)]
    new_id = max(existing_ids, default=0) + 1

    new_entry = dict(entry)
    new_entry[id_field] = new_id

    items.append(new_entry)
    state[section] = items
    _write_state(state)
    return new_entry


def state_update(section: str, entry_id: int, updates: dict) -> dict | None:
    """section 内の id 一致 entry を updates で更新・更新後 entry 返却"""
    state = _read_state()
    items = state.get(section, [])
    id_field = _ID_FIELDS.get(section, "id")

    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get(id_field) == entry_id:
            items[i] = {**item, **updates}
            state[section] = items
            _write_state(state)
            return items[i]
    return None
