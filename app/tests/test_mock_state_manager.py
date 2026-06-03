import pytest
import os
from pathlib import Path


@pytest.fixture(autouse=True)
def reset_state(tmp_path, monkeypatch):
    """Redirect STATE_FILE and LOCK_FILE to tmp_path for isolation."""
    import app.adapters.mock_state_manager as msm
    state_file = tmp_path / "score_mock_state.yaml"
    lock_file = str(state_file) + ".lock"
    monkeypatch.setattr(msm, "STATE_FILE", state_file)
    monkeypatch.setattr(msm, "LOCK_FILE", lock_file)
    yield
    # cleanup lock file if present
    if Path(lock_file).exists():
        os.unlink(lock_file)


def test_init_state_creates_file(tmp_path, monkeypatch):
    import app.adapters.mock_state_manager as msm
    assert not msm.STATE_FILE.exists()
    msm.init_state()
    assert msm.STATE_FILE.exists()


def test_state_get_returns_list(monkeypatch):
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    result = msm.state_get("messages")
    assert isinstance(result, list)
    assert len(result) > 0


def test_state_append_increments_id():
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    notifications_before = msm.state_get("notifications")
    max_id = max(n["id"] for n in notifications_before)

    new_entry = msm.state_append("notifications", {"title": "Test", "body": "Hello", "read": False})
    assert new_entry["id"] == max_id + 1


def test_state_update_modifies_entry():
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    troubles = msm.state_get("troubles")
    target_id = troubles[0]["id"]

    updated = msm.state_update("troubles", target_id, {"status": "resolved"})
    assert updated is not None
    assert updated["status"] == "resolved"

    # Verify persisted
    reloaded = msm.state_get("troubles")
    match = next((t for t in reloaded if t["id"] == target_id), None)
    assert match["status"] == "resolved"


def test_state_file_persists():
    """init → append → re-read → append が残存することを確認"""
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    appended = msm.state_append("retakes", {"shot_code": "SHOT_003", "reason": "test"})
    appended_id = appended["id"]

    # Re-read state without re-init
    retakes = msm.state_get("retakes")
    ids = [r["id"] for r in retakes]
    assert appended_id in ids


def test_state_get_empty_section_returns_list():
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    # comments starts empty
    result = msm.state_get("comments")
    assert isinstance(result, list)
    assert result == []


def test_state_update_nonexistent_returns_none():
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    result = msm.state_update("notifications", 99999, {"read": True})
    assert result is None


def test_state_append_to_empty_section():
    import app.adapters.mock_state_manager as msm
    msm.init_state()
    entry = msm.state_append("comments", {"body": "test comment", "author": "user1"})
    assert entry["comment_id"] == 1
    assert entry["body"] == "test comment"
