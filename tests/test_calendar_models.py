"""CalendarEvent.from_api_dict: all-day vs timed, 누락 필드, JSON 라운드트립."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from soma0sd_rpi_schedule.calendar.models import CalendarEvent, Task

KST = ZoneInfo("Asia/Seoul")


def test_from_api_timed_event() -> None:
    raw = {
        "id": "abc",
        "summary": "회의",
        "location": "회의실 A",
        "description": "주간",
        "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
        "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
    }
    ev = CalendarEvent.from_api_dict(raw)
    assert ev.id == "abc"
    assert ev.summary == "회의"
    assert ev.is_all_day is False
    assert ev.start_dt == datetime(2026, 5, 12, 9, tzinfo=KST)
    assert ev.end_dt == datetime(2026, 5, 12, 10, tzinfo=KST)
    assert ev.occurs_on(date(2026, 5, 12)) is True
    assert ev.occurs_on(date(2026, 5, 13)) is False


def test_from_api_all_day_event() -> None:
    raw = {
        "id": "x",
        "summary": "휴가",
        "start": {"date": "2026-05-12"},
        "end": {"date": "2026-05-15"},  # exclusive
    }
    ev = CalendarEvent.from_api_dict(raw)
    assert ev.is_all_day is True
    assert ev.start_date == date(2026, 5, 12)
    assert ev.end_date == date(2026, 5, 15)
    # 5/12, 5/13, 5/14는 발생, 5/15는 exclusive
    assert ev.occurs_on(date(2026, 5, 12))
    assert ev.occurs_on(date(2026, 5, 14))
    assert ev.occurs_on(date(2026, 5, 15)) is False
    assert ev.occurs_on(date(2026, 5, 11)) is False


def test_from_api_missing_summary_uses_placeholder() -> None:
    raw = {
        "id": "n",
        "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
        "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
    }
    ev = CalendarEvent.from_api_dict(raw)
    assert ev.summary == "(제목 없음)"


def test_from_api_utc_dateTime_converted_to_kst() -> None:
    raw = {
        "id": "u",
        "summary": "UTC",
        "start": {"dateTime": "2026-05-12T00:00:00Z"},
        "end": {"dateTime": "2026-05-12T01:00:00Z"},
    }
    ev = CalendarEvent.from_api_dict(raw)
    # UTC 00:00 → KST 09:00
    assert ev.start_dt.hour == 9
    assert ev.start_dt.tzinfo == KST


def test_json_round_trip_preserves_fields() -> None:
    original = CalendarEvent(
        id="r",
        summary="라운드",
        start_dt=datetime(2026, 5, 12, 9, tzinfo=KST),
        end_dt=datetime(2026, 5, 12, 10, tzinfo=KST),
        is_all_day=False,
        location="원격",
        description="설명",
    )
    restored = CalendarEvent.from_json_dict(original.to_json_dict())
    assert restored == original


def test_occurs_on_timed_event_spans_one_day_only() -> None:
    raw = {
        "id": "t",
        "summary": "긴 회의",
        "start": {"dateTime": "2026-05-12T23:00:00+09:00"},
        "end": {"dateTime": "2026-05-13T01:00:00+09:00"},
    }
    ev = CalendarEvent.from_api_dict(raw)
    # timed 이벤트는 시작 날짜에만 표시 (월 그리드 정책)
    assert ev.occurs_on(date(2026, 5, 12))
    assert ev.occurs_on(date(2026, 5, 13)) is False


def test_from_api_dict_marks_holiday_when_requested() -> None:
    raw = {
        "id": "h",
        "summary": "어린이날",
        "start": {"date": "2026-05-05"},
        "end": {"date": "2026-05-06"},
    }
    ev = CalendarEvent.from_api_dict(raw, is_holiday=True)
    assert ev.is_holiday is True
    assert ev.summary == "어린이날"


def test_from_api_dict_default_is_not_holiday() -> None:
    raw = {
        "id": "e",
        "summary": "일반",
        "start": {"dateTime": "2026-05-12T09:00:00+09:00"},
        "end": {"dateTime": "2026-05-12T10:00:00+09:00"},
    }
    ev = CalendarEvent.from_api_dict(raw)
    assert ev.is_holiday is False


def test_from_json_dict_missing_is_holiday_key_defaults_false() -> None:
    # 기존 캐시(events.json)에 is_holiday 키가 없어도 로드 가능해야 한다 (forward compat).
    legacy = {
        "id": "old",
        "summary": "구버전",
        "start_dt": "2026-05-12T09:00:00+09:00",
        "end_dt": "2026-05-12T10:00:00+09:00",
        "is_all_day": False,
        "location": "",
        "description": "",
    }
    ev = CalendarEvent.from_json_dict(legacy)
    assert ev.is_holiday is False


def test_json_round_trip_preserves_is_holiday() -> None:
    original = CalendarEvent(
        id="h",
        summary="석가탄신일",
        start_dt=datetime(2026, 5, 25, tzinfo=KST),
        end_dt=datetime(2026, 5, 26, tzinfo=KST),
        is_all_day=True,
        is_holiday=True,
    )
    restored = CalendarEvent.from_json_dict(original.to_json_dict())
    assert restored == original
    assert restored.is_holiday is True


# ── Task ──────────────────────────────────────────────────────────────────


def test_task_from_api_parses_due_date_only() -> None:
    # due 는 RFC3339(자정 UTC): 날짜부분만 슬라이스, 타임존 보정 없음.
    raw = {"id": "t1", "title": "보고서 제출", "status": "needsAction",
           "due": "2026-06-15T00:00:00.000Z", "notes": "초안"}
    t = Task.from_api_dict(raw)
    assert t.id == "t1"
    assert t.title == "보고서 제출"
    assert t.due_date == "2026-06-15"
    assert t.notes == "초안"
    assert t.parent == ""


def test_task_from_api_without_due_is_none() -> None:
    t = Task.from_api_dict({"id": "t2", "title": "무기한", "status": "needsAction"})
    assert t.due_date is None


def test_task_from_api_malformed_due_is_none() -> None:
    # 10자 미만/형식 이상이면 None: 크래시 없음.
    assert Task.from_api_dict({"id": "t3", "title": "x", "due": "2026"}).due_date is None
    assert Task.from_api_dict({"id": "t4", "title": "y", "due": None}).due_date is None


def test_task_blank_title_uses_placeholder() -> None:
    assert Task.from_api_dict({"id": "t5", "title": "   "}).title == "(제목 없음)"
    assert Task.from_api_dict({"id": "t6"}).title == "(제목 없음)"


def test_task_records_parent_for_subtasks() -> None:
    t = Task.from_api_dict({"id": "c", "title": "서브", "parent": "p1"})
    assert t.parent == "p1"


def test_task_json_round_trip() -> None:
    original = Task(id="r", title="라운드", due_date="2026-06-15", notes="n", parent="")
    assert Task.from_json_dict(original.to_json_dict()) == original
    # 무기한(due_date None) 도 라운드트립.
    undated = Task(id="u", title="무기한", due_date=None)
    assert Task.from_json_dict(undated.to_json_dict()) == undated
