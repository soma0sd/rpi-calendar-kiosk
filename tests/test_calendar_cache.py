"""디스크 캐시 라운드트립과 손상 처리."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from soma0sd_rpi_schedule.calendar import cache
from soma0sd_rpi_schedule.calendar.models import CalendarEvent, Task

KST = ZoneInfo("Asia/Seoul")


def _sample_event(eid: str = "1") -> CalendarEvent:
    return CalendarEvent(
        id=eid,
        summary="샘플",
        start_dt=datetime(2026, 5, 12, 9, tzinfo=KST),
        end_dt=datetime(2026, 5, 12, 10, tzinfo=KST),
        is_all_day=False,
        location="",
        description="",
    )


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    events = [_sample_event("a"), _sample_event("b")]
    cache.save(events, path)
    loaded = cache.load(path)
    assert loaded is not None
    assert [e.id for e in loaded.events] == ["a", "b"]
    # ``datetime.fromisoformat`` 은 IANA ZoneInfo 가 아닌 동등 offset(``timezone(+09:00)``)으로
    # 복원하므로 인스턴스 비교 대신 UTC 오프셋이 일치하는지 검증한다.
    assert loaded.saved_at.utcoffset() == datetime.now(tz=KST).utcoffset()


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert cache.load(tmp_path / "nope.json") is None


def test_load_corrupted_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    assert cache.load(path) is None


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "events.json"
    cache.save([_sample_event()], path)
    assert path.exists()


def test_save_is_atomic_tmp_file_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    cache.save([_sample_event()], path)
    # tmp가 남아 있으면 안 됨
    assert not (tmp_path / "events.json.tmp").exists()


def test_save_and_load_tasks_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "events.json"
    tasks = [
        Task(id="t1", title="보고서", due_date="2026-06-15"),
        Task(id="t2", title="무기한", due_date=None),
    ]
    cache.save([_sample_event("a")], path, tasks=tasks)
    loaded = cache.load(path)
    assert loaded is not None
    assert [t.id for t in loaded.tasks] == ["t1", "t2"]
    assert loaded.tasks[1].due_date is None


def test_load_legacy_cache_without_tasks_key(tmp_path: Path) -> None:
    # 구버전 캐시(events.json)에 tasks 키가 없어도 로드 가능, tasks 는 빈 목록.
    path = tmp_path / "events.json"
    payload = {
        "saved_at": datetime.now(tz=KST).isoformat(),
        "events": [_sample_event("a").to_json_dict()],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    loaded = cache.load(path)
    assert loaded is not None
    assert loaded.tasks == []
