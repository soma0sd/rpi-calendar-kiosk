"""시간·D-Day·RFC3339 처리 유틸. 표시 시각은 모두 Asia/Seoul."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import TIMEZONE_NAME

KST = ZoneInfo(TIMEZONE_NAME)


def now_kst() -> datetime:
    """현재 시각(KST). 테스트에서는 호출 지점을 모킹한다."""
    return datetime.now(tz=KST)


def today_kst() -> date:
    return now_kst().date()


def parse_rfc3339(value: str) -> datetime:
    """Google Calendar의 ``dateTime`` 필드(RFC3339)를 KST tz-aware datetime으로.

    들어오는 값:
    - ``2026-05-12T09:00:00+09:00``
    - ``2026-05-12T00:00:00Z`` (UTC)
    - ``2026-05-12T00:00:00.123456+00:00``
    """
    # Python 3.11+ ``fromisoformat``은 ``Z`` 접미사도 받지만, 호환성을 위해 명시 치환.
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"타임존 정보가 없는 datetime은 허용하지 않는다: {value}")
    return dt.astimezone(KST)


def parse_date(value: str) -> date:
    """all-day 이벤트의 ``start.date`` / ``end.date`` (``YYYY-MM-DD``)."""
    return date.fromisoformat(value)


def all_day_bounds(d: date) -> tuple[datetime, datetime]:
    """all-day 이벤트의 표시용 KST 범위. ``end``는 exclusive 규칙대로 다음날 0시."""
    start = datetime.combine(d, time.min, tzinfo=KST)
    end = start + timedelta(days=1)
    return start, end


def calc_dday(target: date, today: date | None = None) -> int:
    """양수: N일 후, 0: 오늘, 음수: N일 전.

    예) target = today + 3  →  3
        target = today      →  0
        target = today - 2  →  -2
    """
    base = today if today is not None else today_kst()
    return (target - base).days


def format_dday(n: int) -> str:
    """``D-3`` / ``D-Day`` / ``D+5`` 형식."""
    if n == 0:
        return "D-Day"
    if n > 0:
        return f"D-{n}"
    return f"D+{-n}"


def format_time_range(start: datetime, end: datetime, *, all_day: bool) -> str:
    """이벤트 목록 한 줄 시간 표기. KST 기준."""
    if all_day:
        return "종일"
    s = start.astimezone(KST)
    e = end.astimezone(KST)
    if s.date() == e.date():
        return f"{s:%H:%M} - {e:%H:%M}"
    return f"{s:%m/%d %H:%M} - {e:%m/%d %H:%M}"


def month_range(year: int, month: int) -> tuple[datetime, datetime]:
    """주어진 월 1일 00:00 KST ~ 다음달 1일 00:00 KST."""
    start = datetime(year, month, 1, tzinfo=KST)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=KST)
    else:
        end = datetime(year, month + 1, 1, tzinfo=KST)
    return start, end
