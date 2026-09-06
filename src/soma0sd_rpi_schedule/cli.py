"""커맨드라인 진입점.

서브 모드:
- ``--print-resolution`` : sysfs 폴백 출력 후 종료
- ``--auth``             : OAuth 최초 인증 (로컬 머신에서만)
- ``--serve``            : HTTP 서버만 시작 (브라우저는 별도로 띄움)
- ``--kiosk`` (default)  : HTTP 서버 + ``chromium --kiosk`` 동시 실행
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="soma0sd-rpi-schedule",
        description="라즈베리 파이 4 + chromium kiosk 용 구글 캘린더 일정 키오스크",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--print-resolution", action="store_true",
                      help="GUI/브라우저 없이 sysfs 디스플레이 정보만 출력")
    mode.add_argument("--auth", action="store_true",
                      help="OAuth 최초 인증을 수행해 secrets/token.json 생성 (로컬에서만)")
    mode.add_argument("--serve", action="store_true",
                      help="HTTP 서버만 시작 (브라우저는 별도로 띄운다)")
    mode.add_argument("--kiosk", action="store_true",
                      help="HTTP 서버 + chromium --kiosk 동시 실행 (기본)")
    mode.add_argument("--push-monitor", action="store_true",
                      help="이 PC의 시스템 지표를 Raspberry Pi로 주기 전송")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--monitor-target", default=os.environ.get("SOMA0SD_MONITOR_TARGET", ""))
    parser.add_argument("--monitor-token-file", default="")
    parser.add_argument("--monitor-interval", type=float, default=2.0)
    parser.add_argument("--monitor-once", action="store_true")
    args = parser.parse_args(argv)

    if args.print_resolution:
        from .display import detect_screens_sysfs, log_screens

        log_screens(detect_screens_sysfs())
        return 0

    if args.auth:
        return _run_auth()

    if args.push_monitor:
        return _run_monitor_pusher(
            args.monitor_target,
            args.monitor_token_file,
            args.monitor_interval,
            args.monitor_once,
        )

    if not _token_present():
        _print_initial_auth_help()
        return 2

    # 기본은 kiosk. --serve 명시 시에만 브라우저 안 띄움.
    if args.serve:
        return _run_serve(args.host, args.port)
    return _run_kiosk(args.host, args.port)


def _run_auth() -> int:
    from .calendar.auth import run_auth_flow
    from .config import credentials_path, token_path

    try:
        run_auth_flow(credentials_path(), token_path())
    except FileNotFoundError as exc:
        print(f"[soma0sd-rpi-schedule] {exc}", file=sys.stderr)
        return 3
    print(f"[soma0sd-rpi-schedule] 인증 완료. token: {token_path()}")
    return 0


def _token_present() -> bool:
    from .config import token_path

    return token_path().exists()


def _print_initial_auth_help() -> None:
    from .config import credentials_path, token_path

    print(
        "[soma0sd-rpi-schedule] token.json이 없다. 다음 절차로 진행해라.\n"
        f"  1. Google Cloud Console에서 OAuth Desktop Client 생성 → ``{credentials_path()}`` 에 둔다.\n"
        "  2. 로컬에서 ``uv run python -m soma0sd_rpi_schedule --auth`` 실행 (브라우저 동의).\n"
        f"  3. RPi 사용자라면 ``pwsh scripts/push_token.ps1`` 로 token을 ``{token_path()}`` 위치에 복사.\n",
        file=sys.stderr,
    )


def _run_serve(host: str, port: int) -> int:
    from .server import serve

    serve(host=host, port=port)
    return 0


def _run_monitor_pusher(
    target: str,
    token_file: str,
    interval_sec: float,
    once: bool,
) -> int:
    from .config import monitor_token_path
    from .monitor import run_monitor_pusher

    path = monitor_token_path() if not token_file else Path(token_file)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"[system-monitor] token read failed: {exc}", file=sys.stderr)
        return 3
    if not target:
        print("[system-monitor] --monitor-target is required", file=sys.stderr)
        return 3
    try:
        return run_monitor_pusher(
            target,
            token,
            interval_sec=interval_sec,
            once=once,
        )
    except ValueError as exc:
        print(f"[system-monitor] {exc}", file=sys.stderr)
        return 3


def _run_kiosk(host: str, port: int) -> int:
    from .monitor import SystemMonitorState
    from .server import CalendarState, serve, start_sync_thread

    state = CalendarState()
    monitor_state = SystemMonitorState()
    start_sync_thread(state)

    server_thread = threading.Thread(
        target=lambda: serve(
            host=host,
            port=port,
            state=state,
            monitor_state=monitor_state,
        ),
        name="http-server",
        daemon=True,
    )
    server_thread.start()
    time.sleep(0.5)

    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{port}"
    chromium = _find_chromium()
    if chromium is None:
        print("[soma0sd-rpi-schedule] chromium 실행 파일을 찾지 못했다. --serve로 폴백.", file=sys.stderr)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            return 0

    user_data_dir = _ensure_kiosk_profile()

    # Bookworm chromium 138 은 --disable-features=Translate 명령행 플래그를 무시한다.
    # 격리된 user-data-dir + Preferences 사전 작성 + Policies 파일로 번역/팝업을 차단.
    cmd = [
        chromium,
        f"--user-data-dir={user_data_dir}",
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-translate",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-pinch",
        "--disable-session-crashed-bubble",
        "--check-for-update-interval=31536000",
        "--autoplay-policy=no-user-gesture-required",
        "--enable-features=UseOzonePlatform",
        "--ozone-platform=wayland",
        "--password-store=basic",
        "--lang=ko-KR",
        url,
    ]
    print(f"[soma0sd-rpi-schedule] chromium 키오스크 시작: {url}")
    env = os.environ.copy()
    proc = subprocess.Popen(cmd, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


def _ensure_kiosk_profile() -> str:
    """chromium 키오스크 전용 user-data-dir + Preferences + 정책 파일을 사전 작성.

    `~/.config/chromium-soma0sd-kiosk/` 에 격리해 기존 사용자 chromium 세션과 분리.
    번역 풍선/세션 복구 다이얼로그 등 풀스크린을 가리는 UI 를 정책 레벨에서 차단.
    """
    import json
    from pathlib import Path

    root = Path.home() / ".config" / "chromium-soma0sd-kiosk"
    (root / "Default").mkdir(parents=True, exist_ok=True)
    prefs_path = root / "Default" / "Preferences"
    # Preferences 는 chromium 이 자기 자체로 덮어쓰므로 매 실행마다 다시 쓴다.
    # exit 직후엔 ``exited_cleanly: False`` 가 박혀 세션 복구 풍선이 뜨므로 강제로 ``True``.
    prefs_path.write_text(
        json.dumps(
            {
                "translate": {"enabled": False},
                "translate_blocked_languages": ["ko", "en", "ja", "zh-CN"],
                "translate_site_blocklist_with_time": {},
                "translate_accepted_count": {},
                "translate_denied_count": {},
                "browser": {
                    "check_default_browser": False,
                    "has_seen_welcome_page": True,
                    "show_home_button": False,
                },
                "profile": {
                    "default_content_setting_values": {"notifications": 2},
                    "exit_type": "Normal",
                    "exited_cleanly": True,
                },
                "session": {"restore_on_startup": 4},  # 마지막 세션 복구 안 함
            }
        ),
        encoding="utf-8",
    )
    policies = root / "Policies" / "Managed"
    policies.mkdir(parents=True, exist_ok=True)
    (policies / "kiosk.json").write_text(
        json.dumps(
            {
                "TranslateEnabled": False,
                "DefaultBrowserSettingEnabled": False,
                "MetricsReportingEnabled": False,
                "PasswordManagerEnabled": False,
                "BrowserSignin": 0,
                "PromotionalTabsEnabled": False,
            }
        ),
        encoding="utf-8",
    )
    return str(root)


def _find_chromium() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome"):
        path = _which(name)
        if path:
            return path
    return None


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)
