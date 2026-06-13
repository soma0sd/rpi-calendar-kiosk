"""캘린더 이벤트 모델. Google API 응답과 JSON 캐시 사이의 단일 표현."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from ..time_utils import KST, all_day_bounds, parse_date, parse_rfc3339


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    summary: str
    start_dt: datetime
    end_dt: datetime
    is_all_day: bool
    location: str = ""
    description: str = ""
    is_holiday: bool = False

    @property
    def start_date(self) -> date:
        return self.start_dt.astimezone(KST).date()

    @property
    def end_date(self) -> date:
        return self.end_dt.astimezone(KST).date()

    def occurs_on(self, day: date) -> bool:
        """all-day는 end exclusive, timed는 같은 KST 날짜에 시작했는지 기준.

        월 그리드 인디케이터용. ``start_date <= day < end_date`` 로 판단해
        여러 날에 걸친 all-day 이벤트도 매일 표시된다.
        """
        return self.start_date <= day < self.end_date if self.is_all_day else self.start_date == day

    @classmethod
    def from_api_dict(cls, data: dict[str, Any], *, is_holiday: bool = False) -> CalendarEvent:
        """Google Calendar API v3 ``events.list`` 응답의 항목 하나를 변환.

        - ``start.date`` 가 있으면 all-day, 그 외엔 ``start.dateTime``.
        - all-day 이벤트는 KST 자정 기준으로 매핑.
        - ``is_holiday`` 는 호출자가 공휴일 캘린더 결과를 마킹할 때 True 로 전달.
        """
        start_block = data.get("start") or {}
        end_block = data.get("end") or {}
        if "date" in start_block:
            start_dt, _ = all_day_bounds(parse_date(start_block["date"]))
            end_only = parse_date(end_block["date"]) if "date" in end_block else parse_date(start_block["date"])
            end_dt, _ = all_day_bounds(end_only)
            is_all_day = True
        else:
            start_dt = parse_rfc3339(start_block["dateTime"])
            end_dt = parse_rfc3339(end_block.get("dateTime", start_block["dateTime"]))
            is_all_day = False
        return cls(
            id=str(data.get("id", "")),
            summary=str(data.get("summary", "(제목 없음)")),
            start_dt=start_dt,
            end_dt=end_dt,
            is_all_day=is_all_day,
            location=str(data.get("location", "")),
            description=str(data.get("description", "")),
            is_holiday=is_holiday,
        )

    def to_json_dict(self) -> dict[str, Any]:
        """JSON 캐시에 저장할 직렬화 형태."""
        d = asdict(self)
        d["start_dt"] = self.start_dt.isoformat()
        d["end_dt"] = self.end_dt.isoformat()
        return d

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> CalendarEvent:
        return cls(
            id=data["id"],
            summary=data["summary"],
            start_dt=datetime.fromisoformat(data["start_dt"]),
            end_dt=datetime.fromisoformat(data["end_dt"]),
            is_all_day=bool(data["is_all_day"]),
            location=data.get("location", ""),
            description=data.get("description", ""),
            is_holiday=bool(data.get("is_holiday", False)),
        )


@dataclass(frozen=True)
class Task:
    """Google Tasks 항목. 일정(CalendarEvent)과 함께 좌측 목록에 표시된다.

    ``due`` 는 Google Tasks 에서 항상 날짜만 의미(시각은 무시) 하므로 datetime 으로
    정규화하지 않고 ``"YYYY-MM-DD"`` 문자열로만 보관한다. 타임존 변환 시 자정이 하루
    밀릴 위험을 피하고, 프런트의 ``calcDday`` 가 날짜 문자열을 그대로 받기 때문.
    """

    id: str
    title: str
    due_date: str | None        # "YYYY-MM-DD" 또는 None(무기한)
    notes: str = ""
    parent: str = ""            # 서브태스크면 부모 task id, 아니면 ""

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> Task:
        """Google Tasks API v1 ``tasks.list`` 응답의 항목 하나를 변환.

        ``due`` 는 RFC3339(예 ``"2026-06-15T00:00:00.000Z"``) — 날짜부분만 슬라이스.
        형식이 짧거나 없으면 ``None``(무기한). ``title`` 이 공백이면 기본값.
        """
        due_raw = data.get("due")
        due_date = due_raw[:10] if isinstance(due_raw, str) and len(due_raw) >= 10 else None
        title = str(data.get("title") or "").strip() or "(제목 없음)"
        return cls(
            id=str(data.get("id", "")),
            title=title,
            due_date=due_date,
            notes=str(data.get("notes", "")),
            parent=str(data.get("parent", "")),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=data["id"],
            title=data["title"],
            due_date=data.get("due_date"),
            notes=data.get("notes", ""),
            parent=data.get("parent", ""),
        )
