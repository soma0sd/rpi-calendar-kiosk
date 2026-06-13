"""server: CalendarState snapshot + HTTP 핸들러 동작."""

from __future__ import annotations

import http.client
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from soma0sd_rpi_schedule.calendar.models import CalendarEvent, Task
from soma0sd_rpi_schedule.server import (
    CalendarState,
    _is_legal_holiday,
    _ThreadingServer,
    make_handler,
)

KST = ZoneInfo("Asia/Seoul")


def _sample_event() -> CalendarEvent:
    return CalendarEvent(
        id="x",
        summary="샘플",
        start_dt=datetime(2026, 5, 12, 9, tzinfo=KST),
        end_dt=datetime(2026, 5, 12, 10, tzinfo=KST),
        is_all_day=False,
        location="원격",
        description="",
    )


def test_state_snapshot_includes_events_and_metadata(tmp_path: Path, monkeypatch) -> None:
    # 캐시 위치를 빈 tmp로 우회 — 시작 시 빈 캐시
    from soma0sd_rpi_schedule import config

    monkeypatch.setattr(config, "cache_events_path", lambda: tmp_path / "events.json")
    state = CalendarState()
    state._override([_sample_event()])
    snap = state.snapshot()
    assert "events" in snap
    assert snap["events"][0]["id"] == "x"
    assert snap["auth_required"] is False
    assert snap["last_sync"] is not None
    assert snap["server_time"]


def test_state_snapshot_tasks_empty_by_default(tmp_path: Path, monkeypatch) -> None:
    from soma0sd_rpi_schedule import config

    monkeypatch.setattr(config, "cache_events_path", lambda: tmp_path / "events.json")
    snap = CalendarState().snapshot()
    assert snap["tasks"] == []


def test_state_snapshot_includes_tasks(tmp_path: Path, monkeypatch) -> None:
    from soma0sd_rpi_schedule import config

    monkeypatch.setattr(config, "cache_events_path", lambda: tmp_path / "events.json")
    state = CalendarState()
    state._override(
        [_sample_event()],
        tasks=[Task(id="t1", title="보고서 제출", due_date="2026-06-15")],
    )
    snap = state.snapshot()
    assert snap["tasks"][0]["id"] == "t1"
    assert snap["tasks"][0]["title"] == "보고서 제출"
    assert snap["tasks"][0]["due_date"] == "2026-06-15"


def _holiday(summary: str, description: str, day: int = 15) -> CalendarEvent:
    return CalendarEvent(
        id=f"h-{day}",
        summary=summary,
        start_dt=datetime(2026, 5, day, tzinfo=KST),
        end_dt=datetime(2026, 5, day + 1, tzinfo=KST),
        is_all_day=True,
        description=description,
        is_holiday=True,
    )


def test_is_legal_holiday_accepts_only_korean_official_label() -> None:
    # 한국 공휴일 캘린더에서 법정 공휴일은 description 이 정확히 "공휴일".
    assert _is_legal_holiday(_holiday("부처님오신날", "공휴일", day=24)) is True
    # 기념일(스승의날 등)은 "기념일\n…" 으로 시작 — 제외.
    teachers_day_desc = "기념일\n기념일을 숨기려면 Google Calendar 설정 > 대한민국의 휴일 캘린더로 이동하세요."
    assert _is_legal_holiday(_holiday("스승의날", teachers_day_desc, day=15)) is False
    # 빈 description 도 제외 (안전 디폴트).
    assert _is_legal_holiday(_holiday("?", "", day=1)) is False
    # 앞뒤 공백은 허용 — strip 처리.
    assert _is_legal_holiday(_holiday("어린이날", "  공휴일  ", day=5)) is True


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_handler_serves_index_and_state(tmp_path: Path, monkeypatch) -> None:
    from soma0sd_rpi_schedule import config

    monkeypatch.setattr(config, "cache_events_path", lambda: tmp_path / "events.json")
    state = CalendarState()
    state._override([_sample_event()])
    handler_cls = make_handler(state)
    port = _free_port()
    server = _ThreadingServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        time.sleep(0.05)
        # /api/state → JSON, 이벤트 1건
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/state")
        resp = conn.getresponse()
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "샘플" in body
        conn.close()

        # / → index.html (200, HTML content type)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert "text/html" in resp.getheader("Content-Type", "")
        assert b"soma0sd_RPi_Schedule" in resp.read()
        conn.close()

        # /static/style.css → 200, css content type
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/static/style.css")
        resp = conn.getresponse()
        assert resp.status == 200
        assert "text/css" in resp.getheader("Content-Type", "")
        conn.close()

        # /static/../config.py 같은 path traversal 시도는 404
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/static/../config.py")
        resp = conn.getresponse()
        assert resp.status == 404
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
