"""GoogleCalendarClient: ``service`` 주입 방식의 모킹으로 페이지네이션·필드 변환 검증."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from soma0sd_rpi_schedule.calendar.client import GoogleCalendarClient

KST = ZoneInfo("Asia/Seoul")


class _StubService:
    """Google API 클라이언트의 fluent 인터페이스를 흉내내는 stub.

    ``service.events().list(...).execute()`` 호출이 순서대로 ``pages`` 를 반환한다.
    """

    def __init__(self, pages: list[dict]) -> None:
        self._pages = list(pages)
        self._call_count = 0

    def events(self):
        return self

    def list(self, **kwargs):  # noqa: A002, Google API 시그니처 따름
        self._last_kwargs = kwargs
        return self

    def execute(self):
        if not self._pages:
            return {"items": []}
        page = self._pages.pop(0)
        self._call_count += 1
        return page


def test_list_events_single_page_converts_items() -> None:
    page = {
        "items": [
            {
                "id": "1",
                "summary": "단건",
                "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
                "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
            }
        ]
    }
    client = GoogleCalendarClient(MagicMock(), service=_StubService([page]))
    events = client.list_events(
        datetime(2026, 5, 1, tzinfo=KST),
        datetime(2026, 6, 1, tzinfo=KST),
    )
    assert len(events) == 1
    assert events[0].id == "1"


def test_list_events_handles_pagination() -> None:
    page1 = {
        "items": [
            {
                "id": "a",
                "summary": "A",
                "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
                "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
            }
        ],
        "nextPageToken": "p2",
    }
    page2 = {
        "items": [
            {
                "id": "b",
                "summary": "B",
                "start": {"date": "2026-05-13"},
                "end": {"date": "2026-05-14"},
            }
        ]
    }
    client = GoogleCalendarClient(MagicMock(), service=_StubService([page1, page2]))
    events = client.list_events(
        datetime(2026, 5, 1, tzinfo=KST),
        datetime(2026, 6, 1, tzinfo=KST),
    )
    assert [e.id for e in events] == ["a", "b"]
    assert events[1].is_all_day is True


def test_list_events_skips_malformed_items() -> None:
    page = {
        "items": [
            {"id": "ok", "summary": "OK",
             "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
             "end": {"dateTime": "2026-05-12T10:00:00+09:00"}},
            {"id": "bad"},  # start/end 누락
        ]
    }
    client = GoogleCalendarClient(MagicMock(), service=_StubService([page]))
    events = client.list_events(
        datetime(2026, 5, 1, tzinfo=KST),
        datetime(2026, 6, 1, tzinfo=KST),
    )
    assert [e.id for e in events] == ["ok"]


def test_list_events_rejects_naive_datetime() -> None:
    client = GoogleCalendarClient(MagicMock(), service=_StubService([]))
    with pytest.raises(ValueError):
        client.list_events(datetime(2026, 5, 1), datetime(2026, 6, 1) + timedelta(seconds=0))


def test_list_events_marks_holiday_when_flag_set() -> None:
    page = {
        "items": [
            {
                "id": "kr_childrens_day",
                "summary": "어린이날",
                "start": {"date": "2026-05-05"},
                "end": {"date": "2026-05-06"},
            },
            {
                "id": "kr_buddha",
                "summary": "부처님오신날",
                "start": {"date": "2026-05-24"},
                "end": {"date": "2026-05-25"},
            },
        ]
    }
    client = GoogleCalendarClient(MagicMock(), service=_StubService([page]))
    events = client.list_events(
        datetime(2026, 5, 1, tzinfo=KST),
        datetime(2026, 6, 1, tzinfo=KST),
        calendar_id="ko.south_korea#holiday@group.v.calendar.google.com",
        is_holiday=True,
    )
    assert len(events) == 2
    assert all(e.is_holiday for e in events)


def test_list_events_default_is_not_holiday() -> None:
    page = {
        "items": [
            {
                "id": "1",
                "summary": "회의",
                "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
                "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
            }
        ]
    }
    client = GoogleCalendarClient(MagicMock(), service=_StubService([page]))
    events = client.list_events(
        datetime(2026, 5, 1, tzinfo=KST),
        datetime(2026, 6, 1, tzinfo=KST),
    )
    assert events[0].is_holiday is False
