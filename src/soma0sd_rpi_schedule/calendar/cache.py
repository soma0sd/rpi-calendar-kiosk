"""이벤트 디스크 캐시. 오프라인/네트워크 오류 시 마지막 성공 응답 표시."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..time_utils import KST
from .models import CalendarEvent, Task


@dataclass(frozen=True)
class CachedEvents:
    saved_at: datetime
    events: list[CalendarEvent]
    tasks: list[Task] = field(default_factory=list)


def save(events: list[CalendarEvent], path: Path, *, tasks: list[Task] = ()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(tz=KST).isoformat(),
        "events": [e.to_json_dict() for e in events],
        "tasks": [t.to_json_dict() for t in tasks],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load(path: Path) -> CachedEvents | None:
    """없거나 손상되면 ``None``. 호출자가 fresh fetch로 폴백 결정."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(raw["saved_at"])
        events = [CalendarEvent.from_json_dict(item) for item in raw.get("events", [])]
        # 구버전 캐시엔 tasks 키가 없다 → 빈 목록.
        tasks = [Task.from_json_dict(item) for item in raw.get("tasks", [])]
        return CachedEvents(saved_at=saved_at, events=events, tasks=tasks)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
