"""server: CalendarState snapshot + HTTP 핸들러 동작."""

from __future__ import annotations

import http.client
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from soma0sd_rpi_schedule import signature
from soma0sd_rpi_schedule.calendar.models import CalendarEvent, Task
from soma0sd_rpi_schedule.monitor import SystemMonitorState
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
    # 캐시 위치를 빈 tmp로 우회: 시작 시 빈 캐시
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
    # 기념일(스승의날 등)은 "기념일\n..." 으로 시작: 제외.
    teachers_day_desc = "기념일\n기념일을 숨기려면 Google Calendar 설정 > 대한민국의 휴일 캘린더로 이동하세요."
    assert _is_legal_holiday(_holiday("스승의날", teachers_day_desc, day=15)) is False
    # 빈 description 도 제외 (안전 디폴트).
    assert _is_legal_holiday(_holiday("?", "", day=1)) is False
    # 앞뒤 공백은 허용: strip 처리.
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


def test_monitor_push_requires_a_valid_signature_and_updates_state(
    tmp_path: Path, monkeypatch
) -> None:
    from soma0sd_rpi_schedule import config

    monkeypatch.setattr(config, "cache_events_path", lambda: tmp_path / "events.json")
    calendar = CalendarState()
    monitor = SystemMonitorState()
    handler_cls = make_handler(calendar, monitor_state=monitor, monitor_token="test-secret")
    port = _free_port()
    server = _ThreadingServer(("127.0.0.1", port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sample = {
        "hostname": "WORKSTATION",
        "captured_at": "2026-09-03T16:30:00+09:00",
        "cpu_percent": 20,
        "memory_percent": 40,
        "disk_percent": 60,
    }
    body = json.dumps(sample).encode("utf-8")

    def signed_headers(token: str, payload: bytes) -> dict[str, str]:
        timestamp, sig = signature.sign_now(token, payload)
        return {
            "Content-Type": "application/json",
            signature.TIMESTAMP_HEADER: timestamp,
            signature.SIGNATURE_HEADER: sig,
        }

    try:
        # 다른 키로 만든 서명은 거부한다.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("POST", "/api/system", body=body, headers=signed_headers("wrong", body))
        assert conn.getresponse().status == 401
        conn.close()

        # 서명 헤더가 아예 없으면 거부한다.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request(
            "POST", "/api/system", body=body, headers={"Content-Type": "application/json"}
        )
        assert conn.getresponse().status == 401
        conn.close()

        # 본문이 서명 이후 바뀌면 거부한다.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        tampered = json.dumps({**sample, "cpu_percent": 99}).encode("utf-8")
        conn.request(
            "POST", "/api/system", body=tampered, headers=signed_headers("test-secret", body)
        )
        assert conn.getresponse().status == 401
        conn.close()

        # 허용 범위를 벗어난 타임스탬프는 거부한다 (재전송 창 제한).
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        stale = str(int(time.time()) - signature.TOLERANCE_SECONDS - 5)
        conn.request(
            "POST",
            "/api/system",
            body=body,
            headers={
                "Content-Type": "application/json",
                signature.TIMESTAMP_HEADER: stale,
                signature.SIGNATURE_HEADER: signature.sign("test-secret", stale, body),
            },
        )
        assert conn.getresponse().status == 401
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("POST", "/api/system", body=body, headers=signed_headers("test-secret", body))
        assert conn.getresponse().status == 204
        conn.close()

        # DNS 리바인딩으로 루프백 정책을 우회하지 못한다.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/state", headers={"Host": "attacker.example"})
        assert conn.getresponse().status == 400
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/state")
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["system_monitor"]["available"] is True
        assert payload["system_monitor"]["hostname"] == "WORKSTATION"
        assert len(payload["system_monitors"]) == 1
        assert payload["system_monitors"][0]["hostname"] == "WORKSTATION"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
