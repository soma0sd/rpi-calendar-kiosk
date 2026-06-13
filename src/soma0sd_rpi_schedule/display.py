"""디스플레이 정보 감지 (sysfs 폴백 전용).

웹뷰 키오스크 구조에서는 Qt가 없으므로 ``/sys/class/drm`` 만 사용한다. CLI 진단용.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    width: int
    height: int
    refresh_rate: float
    is_primary: bool


_DRM_ROOT = Path("/sys/class/drm")


def detect_screens_sysfs(drm_root: Path = _DRM_ROOT) -> list[ScreenInfo]:
    if not drm_root.exists():
        return []
    screens: list[ScreenInfo] = []
    for card_dir in sorted(drm_root.iterdir()):
        if not card_dir.is_dir() or "-" not in card_dir.name:
            continue
        status_path = card_dir / "status"
        if not status_path.exists() or status_path.read_text().strip() != "connected":
            continue
        modes_path = card_dir / "modes"
        if not modes_path.exists():
            continue
        first_mode = ""
        for line in modes_path.read_text().splitlines():
            stripped = line.strip()
            if stripped:
                first_mode = stripped
                break
        if not first_mode or "x" not in first_mode:
            continue
        try:
            w_str, rest = first_mode.split("x", 1)
            h_str = rest.split("@", 1)[0].split()[0]
            width = int(w_str)
            height = int(h_str)
        except (ValueError, IndexError):
            continue
        screens.append(
            ScreenInfo(
                name=card_dir.name,
                width=width,
                height=height,
                refresh_rate=0.0,
                is_primary=(len(screens) == 0),
            )
        )
    return screens


def format_screen(s: ScreenInfo) -> str:
    parts = [f"{s.name}: {s.width}x{s.height}"]
    if s.refresh_rate > 0:
        parts.append(f"@{s.refresh_rate:.2f}Hz")
    if s.is_primary:
        parts.append("[primary]")
    return " ".join(parts)


def log_screens(screens: list[ScreenInfo]) -> None:
    if not screens:
        print("[soma0sd-rpi-schedule] 감지된 디스플레이가 없다")
        return
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="감지된 디스플레이")
        for col in ("name", "size", "refresh(Hz)", "primary"):
            table.add_column(col)
        for s in screens:
            table.add_row(
                s.name,
                f"{s.width}x{s.height}",
                f"{s.refresh_rate:.2f}" if s.refresh_rate > 0 else "?",
                "yes" if s.is_primary else "",
            )
        console.print(table)
    except ImportError:
        for s in screens:
            print(format_screen(s))
