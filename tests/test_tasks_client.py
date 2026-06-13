"""GoogleTasksClient: ``service`` 주입 모킹으로 변환·페이지네이션·필터 검증."""

from __future__ import annotations

from unittest.mock import MagicMock

from soma0sd_rpi_schedule.calendar.client import GoogleTasksClient


class _Paginator:
    """``service.tasks().list(...).execute()`` fluent 인터페이스 stub.

    while 루프가 매 반복마다 ``tasks()`` → ``list()`` → ``execute()`` 를 호출하므로,
    같은 paginator 인스턴스를 돌려주어 페이지가 순서대로 소진되게 한다.
    """

    def __init__(self, pages: list[dict]) -> None:
        self._pages = list(pages)
        self.last_kwargs: dict | None = None

    def list(self, **kwargs):  # noqa: A002 — Google API 시그니처 따름
        self.last_kwargs = kwargs
        return self

    def execute(self):
        if not self._pages:
            return {"items": []}
        return self._pages.pop(0)


class _TasklistsStub:
    def __init__(self, response: dict) -> None:
        self._response = response

    def list(self, **kwargs):  # noqa: A002
        return self

    def execute(self):
        return self._response


class _StubTasksService:
    def __init__(self, task_pages: list[dict], tasklists_response: dict | None = None) -> None:
        self._paginator = _Paginator(task_pages)
        self._tasklists = _TasklistsStub(tasklists_response or {"items": []})

    def tasks(self):
        return self._paginator

    def tasklists(self):
        return self._tasklists


def test_list_tasks_converts_items() -> None:
    page = {
        "items": [
            {"id": "t1", "title": "보고서 제출", "status": "needsAction",
             "due": "2026-06-15T00:00:00.000Z", "notes": "초안"},
        ]
    }
    svc = _StubTasksService([page])
    client = GoogleTasksClient(MagicMock(), service=svc)
    tasks = client.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == "t1"
    assert tasks[0].title == "보고서 제출"
    assert tasks[0].due_date == "2026-06-15"
    # 미완료만 요청하는지 확인.
    assert svc._paginator.last_kwargs["showCompleted"] is False


def test_list_tasks_excludes_completed() -> None:
    page = {
        "items": [
            {"id": "a", "title": "할일", "status": "needsAction"},
            {"id": "b", "title": "완료됨", "status": "completed"},
        ]
    }
    client = GoogleTasksClient(MagicMock(), service=_StubTasksService([page]))
    tasks = client.list_tasks()
    assert [t.id for t in tasks] == ["a"]


def test_list_tasks_handles_pagination() -> None:
    page1 = {"items": [{"id": "a", "title": "A", "status": "needsAction"}], "nextPageToken": "p2"}
    page2 = {"items": [{"id": "b", "title": "B", "status": "needsAction"}]}
    client = GoogleTasksClient(MagicMock(), service=_StubTasksService([page1, page2]))
    tasks = client.list_tasks()
    assert [t.id for t in tasks] == ["a", "b"]


def test_list_tasks_undated_task() -> None:
    page = {"items": [{"id": "u", "title": "무기한", "status": "needsAction"}]}
    client = GoogleTasksClient(MagicMock(), service=_StubTasksService([page]))
    assert client.list_tasks()[0].due_date is None


def test_list_tasks_minimal_item_uses_placeholder_title() -> None:
    page = {"items": [{"id": "m", "status": "needsAction"}]}
    client = GoogleTasksClient(MagicMock(), service=_StubTasksService([page]))
    assert client.list_tasks()[0].title == "(제목 없음)"


def test_list_tasks_empty_tasklist() -> None:
    client = GoogleTasksClient(MagicMock(), service=_StubTasksService([{"items": []}]))
    assert client.list_tasks() == []


def test_list_tasks_falls_back_to_first_tasklist() -> None:
    page = {"items": [{"id": "a", "title": "A", "status": "needsAction"}]}
    svc = _StubTasksService([page], tasklists_response={"items": [{"id": "list-xyz"}]})
    client = GoogleTasksClient(MagicMock(), service=svc)
    tasks = client.list_tasks(tasklist_id="")
    assert [t.id for t in tasks] == ["a"]
    # 빈 tasklist_id → 첫 tasklist id 로 조회.
    assert svc._paginator.last_kwargs["tasklist"] == "list-xyz"


def test_list_tasks_no_tasklists_returns_empty() -> None:
    svc = _StubTasksService([{"items": []}], tasklists_response={"items": []})
    client = GoogleTasksClient(MagicMock(), service=svc)
    assert client.list_tasks(tasklist_id="") == []
