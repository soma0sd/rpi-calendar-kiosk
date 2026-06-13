"""time_utils: D-Day 계산, RFC3339 파싱, 시간대 변환."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from soma0sd_rpi_schedule.time_utils import (
    KST,
    all_day_bounds,
    calc_dday,
    format_dday,
    format_time_range,
    month_range,
    parse_date,
    parse_rfc3339,
)

UTC = ZoneInfo("UTC")


def test_calc_dday_positive_zero_negative() -> None:
    today = date(2026, 5, 12)
    assert calc_dday(date(2026, 5, 15), today) == 3
    assert calc_dday(date(2026, 5, 12), today) == 0
    assert calc_dday(date(2026, 5, 10), today) == -2


def test_format_dday() -> None:
    assert format_dday(0) == "D-Day"
    assert format_dday(5) == "D-5"
    assert format_dday(-3) == "D+3"


def test_parse_rfc3339_with_offset_returns_kst() -> None:
    dt = parse_rfc3339("2026-05-12T09:00:00+09:00")
    assert dt.tzinfo == KST
    assert dt.year == 2026
    assert dt.hour == 9


def test_parse_rfc3339_utc_z_converts_to_kst() -> None:
    dt = parse_rfc3339("2026-05-12T00:00:00Z")
    # UTC 00:00 → KST 09:00
    assert dt.hour == 9
    assert dt.tzinfo == KST


def test_parse_rfc3339_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        parse_rfc3339("2026-05-12T09:00:00")


def test_parse_date_iso_format() -> None:
    d = parse_date("2026-05-12")
    assert d == date(2026, 5, 12)


def test_all_day_bounds_returns_kst_midnight_to_next() -> None:
    start, end = all_day_bounds(date(2026, 5, 12))
    assert start == datetime(2026, 5, 12, 0, 0, tzinfo=KST)
    assert end == datetime(2026, 5, 13, 0, 0, tzinfo=KST)
    assert (end - start) == timedelta(days=1)


def test_format_time_range_all_day_label() -> None:
    s, e = all_day_bounds(date(2026, 5, 12))
    assert format_time_range(s, e, all_day=True) == "종일"


def test_format_time_range_timed_same_day() -> None:
    s = datetime(2026, 5, 12, 9, 0, tzinfo=KST)
    e = datetime(2026, 5, 12, 10, 30, tzinfo=KST)
    assert format_time_range(s, e, all_day=False) == "09:00–10:30"


def test_format_time_range_crosses_midnight() -> None:
    s = datetime(2026, 5, 12, 23, 30, tzinfo=KST)
    e = datetime(2026, 5, 13, 1, 0, tzinfo=KST)
    out = format_time_range(s, e, all_day=False)
    assert "05/12" in out and "05/13" in out


def test_month_range_december_wraps_year() -> None:
    s, e = month_range(2026, 12)
    assert s == datetime(2026, 12, 1, tzinfo=KST)
    assert e == datetime(2027, 1, 1, tzinfo=KST)


def test_month_range_mid_year() -> None:
    s, e = month_range(2026, 5)
    assert s == datetime(2026, 5, 1, tzinfo=KST)
    assert e == datetime(2026, 6, 1, tzinfo=KST)
