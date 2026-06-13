"""stdlib http.server 기반 단일 페이지 키오스크 서버.

- 백그라운드 스레드가 5분 주기로 구글 캘린더 동기화 → 메모리 상태(``CalendarState``)
- HTTP: ``/`` (HTML), ``/static/<file>``, ``/api/state`` (JSON)
- 외부 의존성 없이 standard library 만 사용. localhost 바인딩.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from .calendar import cache as cache_mod
from .calendar.auth import ReauthRequired, load_credentials
from .calendar.client import GoogleCalendarClient, GoogleTasksClient
from .calendar.models import CalendarEvent, Task
from .config import (
    HOLIDAY_CALENDAR_ID,
    POLL_INTERVAL_SEC,
    UPCOMING_DAYS,
    cache_events_path,
    token_path,
)
from .time_utils import now_kst

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _is_legal_holiday(event: CalendarEvent) -> bool:
    """Google 한국 공휴일 캘린더 항목 중 법정 공휴일만 식별.

    Google `ko.south_korea#holiday` 캘린더는 법정 공휴일 외에 기념일(스승의날·어버이날 등)을
    같이 내려준다. 두 종류는 ``description`` 으로만 구분된다.

    - 법정 공휴일: ``description == "공휴일"``
    - 기념일/관찰일: ``description`` 이 ``"기념일\\n…"`` 또는 다른 카테고리 텍스트로 시작.

    빨간 날 표시는 법정 공휴일만 — 기념일은 시각적 혼란이라 제외.
    """
    return event.description.strip() == "공휴일"


class CalendarState:
    """이벤트 + 마지막 동기화 상태 + 에러를 안전하게 공유하는 컨테이너."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[CalendarEvent] = []
        self._tasks: list[Task] = []
        self._last_sync: datetime | None = None
        self._last_error: str | None = None
        self._auth_required: bool = False
        # 시작 직후 캐시 즉시 표시
        cached = cache_mod.load(cache_events_path())
        if cached:
            self._events = list(cached.events)
            self._tasks = list(cached.tasks)
            self._last_sync = cached.saved_at

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "events": [e.to_json_dict() for e in self._events],
                "tasks": [t.to_json_dict() for t in self._tasks],
                "last_sync": self._last_sync.isoformat() if self._last_sync else None,
                "last_error": self._last_error,
                "auth_required": self._auth_required,
                "server_time": now_kst().isoformat(),
            }

    def fetch_once(self) -> None:
        try:
            creds = load_credentials(token_path())
            client = GoogleCalendarClient(creds)
            start = now_kst() - timedelta(days=1)
            end = now_kst() + timedelta(days=UPCOMING_DAYS)
            events = client.list_events(start, end)
        except ReauthRequired as exc:
            with self._lock:
                self._auth_required = True
                self._last_error = str(exc)
            return
        except Exception as exc:  # noqa: BLE001 — 네트워크/HTTP 광범위
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            return

        # 공휴일 캘린더는 partial success — 실패해도 primary 결과는 유지하고 경고만 부착.
        holidays: list[CalendarEvent] = []
        holiday_warning: str | None = None
        if HOLIDAY_CALENDAR_ID:
            try:
                raw_holidays = client.list_events(
                    start, end,
                    calendar_id=HOLIDAY_CALENDAR_ID,
                    is_holiday=True,
                )
                # 기념일(스승의날 등) 제외하고 법정 공휴일만 통과시킴.
                holidays = [h for h in raw_holidays if _is_legal_holiday(h)]
            except Exception as exc:  # noqa: BLE001
                holiday_warning = f"공휴일 동기화 실패: {type(exc).__name__}: {exc}"

        # 할일(Google Tasks)도 partial success — 같은 creds 재사용. 실패해도 이벤트는 유지.
        tasks: list[Task] = []
        tasks_warning: str | None = None
        try:
            tasks = GoogleTasksClient(creds).list_tasks()
        except Exception as exc:  # noqa: BLE001
            tasks_warning = f"할일 동기화 실패: {type(exc).__name__}: {exc}"

        warnings = [w for w in (holiday_warning, tasks_warning) if w]
        merged = list(events) + holidays
        with self._lock:
            self._events = merged
            # tasks 는 성공 시에만 교체 — 일시 장애에 직전 할일이 사라지지 않게.
            if tasks_warning is None:
                self._tasks = tasks
            self._last_sync = now_kst()
            self._last_error = " | ".join(warnings) if warnings else None
            self._auth_required = False
            cached_tasks = list(self._tasks)
        try:
            cache_mod.save(merged, cache_events_path(), tasks=cached_tasks)
        except OSError:
            pass

    # 테스트 헬퍼: 외부에서 이벤트·할일 직접 주입.
    def _override(self, events: Iterable[CalendarEvent], tasks: Iterable[Task] = ()) -> None:
        with self._lock:
            self._events = list(events)
            self._tasks = list(tasks)
            self._last_sync = now_kst()


def start_sync_thread(state: CalendarState, *, interval_sec: int = POLL_INTERVAL_SEC) -> threading.Thread:
    def _loop() -> None:
        while True:
            state.fetch_once()
            time.sleep(interval_sec)

    t = threading.Thread(target=_loop, name="calendar-sync", daemon=True)
    t.start()
    return t


def _mime_for(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
        ".json": "application/json; charset=utf-8",
    }.get(path.suffix, "application/octet-stream")


def make_handler(state: CalendarState) -> type[http.server.BaseHTTPRequestHandler]:
    """``CalendarState`` 를 클로저로 캡처."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 — http.server 시그니처
            if self.path in ("/", "/index.html"):
                self._serve_file(_STATIC_DIR / "index.html")
            elif self.path == "/api/state":
                self._serve_json(state.snapshot())
            elif self.path.startswith("/static/"):
                rel = self.path[len("/static/") :].split("?", 1)[0]
                target = (_STATIC_DIR / rel).resolve()
                if _STATIC_DIR.resolve() in target.parents and target.is_file():
                    self._serve_file(target)
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

        def _serve_file(self, path: Path) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", _mime_for(path))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _serve_json(self, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
            pass  # 키오스크 환경에서 요청 로그 노이즈 차단

    return _Handler


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    state: CalendarState | None = None,
) -> None:
    """블로킹 서빙. ``state`` 미지정 시 캘린더 동기화 스레드를 함께 시작."""
    if state is None:
        state = CalendarState()
        start_sync_thread(state)
    handler_cls = make_handler(state)
    server = _ThreadingServer((host, port), handler_cls)
    print(f"[soma0sd-rpi-schedule] HTTP 서빙 시작: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
