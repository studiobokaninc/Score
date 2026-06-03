import os
import pytest

os.environ.setdefault("JWT_SECRET", "test_secret_key_32bytes_minimum!")
os.environ.setdefault("CALENDAR_BASE_URL", "http://localhost:8001")
os.environ.setdefault("CALENDAR_M2M_TOKEN", "test_token")

from app.adapters.calendar_factory import get_calendar_client
from app.adapters.calendar_client import CalendarClient
from app.adapters.mock_calendar_client import MockCalendarClient


def test_calendar_mock_unset_returns_real_client(monkeypatch):
    monkeypatch.delenv("CALENDAR_MOCK", raising=False)
    client = get_calendar_client()
    assert isinstance(client, CalendarClient)
    assert not isinstance(client, MockCalendarClient)


def test_calendar_mock_0_returns_real_client(monkeypatch):
    monkeypatch.setenv("CALENDAR_MOCK", "0")
    client = get_calendar_client()
    assert isinstance(client, CalendarClient)
    assert not isinstance(client, MockCalendarClient)


def test_calendar_mock_1_returns_mock_client(monkeypatch):
    monkeypatch.setenv("CALENDAR_MOCK", "1")
    client = get_calendar_client()
    assert isinstance(client, MockCalendarClient)
