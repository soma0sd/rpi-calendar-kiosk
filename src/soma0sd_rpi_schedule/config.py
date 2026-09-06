"""앱 전역 상수와 경로 헬퍼.

플랫폼(Windows 개발 vs RPi 런타임)에 따라 token/credentials/cache 위치가 다르다.
- credentials.json은 항상 프로젝트의 ``secrets/`` 에서만 읽는다 (로컬 인증 전용, RPi 미배포).
- token.json은 로컬 ``secrets/`` 또는 RPi ``~/.config/soma0sd_rpi_schedule/`` 둘 다 지원.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

CALENDAR_ID = "primary"
# 한국 공식 공휴일 캘린더(공개). 동일 readonly scope 로 조회 가능.
# None 또는 빈 문자열로 비활성화.
HOLIDAY_CALENDAR_ID = "ko.south_korea#holiday@group.v.calendar.google.com"
OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/calendar.readonly",
    # 할일(Google Tasks) 읽기. 민감 범위: 추가 시 기존 token 무효 → 로컬 재인증 필요.
    "https://www.googleapis.com/auth/tasks.readonly",
)
TIMEZONE_NAME = "Asia/Seoul"

# 할일(Google Tasks) 조회
TASKS_TASKLIST_ID = "@default"   # 빈 값이면 첫 tasklist 자동 선택
TASKS_MAX_RESULTS = 100          # tasks.list 페이지 크기

# 폴링/디바운스 (초)
POLL_INTERVAL_SEC = 300         # 5분
MONTH_NAV_DEBOUNCE_MS = 300     # 좌/우 화살표 burst 방지

# UI 비율 (CLAUDE.md 강제 사항에 맞춰 짧은 변 기준)
FONT_RATIO_BODY = 1 / 24
FONT_RATIO_DDAY = 1 / 8
TOUCH_RATIO = 0.08
ROW_HEIGHT_RATIO = 0.12

# 조회 범위
UPCOMING_DAYS = 90
EVENT_LIST_MAX_ROWS = 30

# 캐시 TTL
CACHE_FRESH_HOURS = 24


def project_root() -> Path:
    """src 패키지 기준의 프로젝트 루트(`pyproject.toml`이 있는 디렉터리)."""
    return Path(__file__).resolve().parents[2]


def credentials_path() -> Path:
    """OAuth Desktop Client 비밀. 로컬에만 존재. 인증 시에만 읽는다."""
    return project_root() / "secrets" / "credentials.json"


def token_path() -> Path:
    """token.json 위치. 환경에 따라 다름.

    - Linux(RPi): ``~/.config/soma0sd_rpi_schedule/token.json``
    - 그 외(개발 머신): 프로젝트 ``secrets/token.json``
    """
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / "soma0sd_rpi_schedule" / "token.json"
    return project_root() / "secrets" / "token.json"


def monitor_token_path() -> Path:
    """PC monitor push authentication token path."""
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        return base / "soma0sd_rpi_schedule" / "monitor-token"
    return project_root() / "secrets" / "monitor-token.txt"


def cache_dir() -> Path:
    """이벤트 캐시 디렉터리. 없으면 생성하는 책임은 호출자."""
    if sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
        return base / "soma0sd_rpi_schedule"
    return project_root() / ".cache"


def cache_events_path() -> Path:
    return cache_dir() / "events.json"
