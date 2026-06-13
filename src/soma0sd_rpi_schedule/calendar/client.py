"""Google Calendar API v3 래퍼. ``events.list`` 페이지네이션 + 모델 변환."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from ..config import CALENDAR_ID, TASKS_MAX_RESULTS, TASKS_TASKLIST_ID, TIMEZONE_NAME
from .models import CalendarEvent, Task


class GoogleCalendarClient:
    """1개 인증 세션에 대한 캘린더 클라이언트.

    HTTP 모킹(테스트)에서는 ``service`` 인자로 ``build()`` 결과를 직접 주입해 우회 가능.
    """

    def __init__(self, credentials: Credentials, *, service: Any | None = None) -> None:
        self._service = service or build(
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        calendar_id: str = CALENDAR_ID,
        *,
        is_holiday: bool = False,
    ) -> list[CalendarEvent]:
        if time_min.tzinfo is None or time_max.tzinfo is None:
            raise ValueError("time_min/time_max는 tz-aware 여야 한다")
        events: list[CalendarEvent] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    timeZone=TIMEZONE_NAME,
                    maxResults=250,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                try:
                    events.append(CalendarEvent.from_api_dict(item, is_holiday=is_holiday))
                except (KeyError, ValueError):
                    # 형식이 어긋난 단일 항목은 건너뛴다 (예: status=cancelled 일부).
                    continue
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return events


class GoogleTasksClient:
    """1개 인증 세션에 대한 Google Tasks 클라이언트.

    ``GoogleCalendarClient`` 와 동일하게 ``service`` 인자 주입으로 테스트 모킹 가능.
    """

    def __init__(self, credentials: Credentials, *, service: Any | None = None) -> None:
        self._service = service or build(
            "tasks",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )

    def list_tasks(self, tasklist_id: str = TASKS_TASKLIST_ID) -> list[Task]:
        """미완료 task 목록. ``tasklist_id`` 가 비면 첫 번째 tasklist 를 자동 선택."""
        if not tasklist_id:
            tasklist_id = self._first_tasklist_id()
            if tasklist_id is None:
                return []
        tasks: list[Task] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.tasks()
                .list(
                    tasklist=tasklist_id,
                    showCompleted=False,
                    showHidden=False,
                    maxResults=TASKS_MAX_RESULTS,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("items", []):
                if item.get("status") == "completed":
                    # showCompleted=False 와 별개로 이중 방어.
                    continue
                try:
                    tasks.append(Task.from_api_dict(item))
                except (KeyError, ValueError):
                    # 형식이 어긋난 단일 항목은 건너뛴다 (events 와 동일 관례).
                    continue
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return tasks

    def _first_tasklist_id(self) -> str | None:
        response = self._service.tasklists().list(maxResults=1).execute()
        items = response.get("items", [])
        return items[0]["id"] if items else None
