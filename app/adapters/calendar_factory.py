import os
from app.adapters.calendar_client import CalendarClient
from app.adapters.mock_calendar_client import MockCalendarClient


def get_calendar_client() -> CalendarClient:
    """env CALENDAR_MOCK=1 → MockCalendarClient, else → CalendarClient"""
    if os.getenv("CALENDAR_MOCK", "0") == "1":
        return MockCalendarClient()
    return CalendarClient()
