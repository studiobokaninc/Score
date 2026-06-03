import os
os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
from app.adapters.mock_calendar_client import MockCalendarClient


def test_get_me_returns_user():
    c = MockCalendarClient()
    u = c.get_me()
    assert hasattr(u, 'user_id') or hasattr(u, 'name')


def test_get_shots_returns_list():
    c = MockCalendarClient()
    shots = c.get_shots(33)
    assert isinstance(shots, list)
    assert len(shots) >= 1


def test_get_shot_returns_shot_or_none():
    c = MockCalendarClient()
    shot = c.get_shot(1)
    assert shot is not None
    shot_missing = c.get_shot(99999)
    assert shot_missing is None


def test_get_my_projects():
    c = MockCalendarClient()
    result = c.get_my_projects("test")
    assert isinstance(result, list)
    assert len(result) >= 1


def test_get_notifications():
    c = MockCalendarClient()
    notifs = c.get_notifications()
    assert isinstance(notifs, list)


def test_post_routines():
    c = MockCalendarClient()
    result = c.post_routines({"condition": "😊", "date": "2026-05-21"}, "test")
    assert isinstance(result, dict)


def test_resolve_email_tanaka():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("tanaka@studiobokan.com") == 1


def test_resolve_email_yamada():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("yamada@studiobokan.com") == 10


def test_resolve_email_kato():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("kato@studiobokan.com") == 20


def test_resolve_email_sato():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("sato@studiobokan.com") == 30


def test_resolve_email_suzuki():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("suzuki@studiobokan.com") == 40


def test_resolve_email_ryoji():
    c = MockCalendarClient()
    assert c.resolve_email_to_user_id("ryoji@studiobokan.com") == 99
