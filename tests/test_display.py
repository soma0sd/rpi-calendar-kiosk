"""디스플레이 감지 (sysfs 폴백) 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from soma0sd_rpi_schedule.display import (
    ScreenInfo,
    detect_screens_sysfs,
    format_screen,
)


def _make_fake_drm(tmp_path: Path) -> Path:
    root = tmp_path / "drm"
    root.mkdir()
    # card0: GPU only, status 파일 없음 → 건너뜀
    (root / "card0").mkdir()
    # card1-HDMI-A-1: connected, 모드 400x1280 (실제 타깃)
    a1 = root / "card1-HDMI-A-1"
    a1.mkdir()
    (a1 / "status").write_text("connected\n")
    (a1 / "modes").write_text("400x1280\n1280x720\n320x1480\n")
    # card1-HDMI-A-2: disconnected
    a2 = root / "card1-HDMI-A-2"
    a2.mkdir()
    (a2 / "status").write_text("disconnected\n")
    (a2 / "modes").write_text("")
    return root


def test_detect_screens_sysfs_picks_connected_only(tmp_path: Path) -> None:
    screens = detect_screens_sysfs(_make_fake_drm(tmp_path))
    assert len(screens) == 1
    assert screens[0].name == "card1-HDMI-A-1"
    assert screens[0].width == 400
    assert screens[0].height == 1280
    assert screens[0].is_primary is True


def test_detect_screens_sysfs_missing_root(tmp_path: Path) -> None:
    assert detect_screens_sysfs(tmp_path / "nonexistent") == []


def test_detect_screens_sysfs_mode_with_refresh(tmp_path: Path) -> None:
    root = tmp_path / "drm"
    root.mkdir()
    d = root / "card0-DP-1"
    d.mkdir()
    (d / "status").write_text("connected\n")
    (d / "modes").write_text("1920x1080@60.00\n")
    screens = detect_screens_sysfs(root)
    assert len(screens) == 1
    assert screens[0].width == 1920
    assert screens[0].height == 1080


def test_format_screen_includes_resolution_and_primary_marker() -> None:
    s = ScreenInfo(
        name="HDMI-A-1",
        width=400,
        height=1280,
        refresh_rate=0.0,
        is_primary=True,
    )
    out = format_screen(s)
    assert "400x1280" in out
    assert "[primary]" in out
