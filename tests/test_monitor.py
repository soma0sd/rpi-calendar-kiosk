"""PC system monitor validation and in-memory state tests."""

from __future__ import annotations

import time

import pytest

from soma0sd_rpi_schedule.monitor import SystemMonitorState, normalize_monitor_sample


def _sample() -> dict:
    return {
        "hostname": "WORKSTATION",
        "captured_at": "2026-09-03T16:30:00+09:00",
        "cpu_name": "Intel Test CPU",
        "cpu_percent": 12.5,
        "cpu_temperature_c": 55.0,
        "memory_percent": 48.2,
        "disk_percent": 71.0,
        "gpu_percent": 33.0,
        "gpu_memory_percent": 25.0,
        "memory_used_gb": 15.2,
        "memory_total_gb": 31.8,
        "disk_used_gb": 700.0,
        "disk_total_gb": 1000.0,
        "gpu_memory_used_gb": 4.0,
        "gpu_memory_total_gb": 16.0,
        "gpu_temperature_c": 52.0,
        "network_rx_mbps": 1.2,
        "network_tx_mbps": 0.4,
        "uptime_seconds": 3600.0,
        "gpus": [
            {
                "index": 0,
                "name": "NVIDIA Test GPU",
                "utilization_percent": 33.0,
                "vram_percent": 25.0,
                "vram_used_gb": 4.0,
                "vram_total_gb": 16.0,
                "temperature_c": 52.0,
            },
            {
                "index": 1,
                "name": "NVIDIA Test GPU 2",
                "utilization_percent": 10.0,
                "vram_percent": 12.5,
                "vram_used_gb": 2.0,
                "vram_total_gb": 16.0,
                "temperature_c": 44.0,
            },
        ],
        "disks": [
            {
                "name": "C:",
                "mountpoint": "C:\\",
                "usage_percent": 71.0,
                "used_gb": 700.0,
                "total_gb": 1000.0,
                "temperature_c": None,
            },
            {
                "name": "D:",
                "mountpoint": "D:\\",
                "usage_percent": 42.0,
                "used_gb": 840.0,
                "total_gb": 2000.0,
                "temperature_c": None,
            },
        ],
    }


def test_normalize_monitor_sample_accepts_expected_values() -> None:
    sample = normalize_monitor_sample(_sample())
    assert sample["hostname"] == "WORKSTATION"
    assert sample["cpu_percent"] == 12.5
    assert sample["gpu_temperature_c"] == 52.0
    assert sample["cpu_name"] == "Intel Test CPU"
    assert len(sample["gpus"]) == 2
    assert sample["gpus"][1]["vram_percent"] == 12.5
    assert len(sample["disks"]) == 2
    assert sample["disks"][1]["name"] == "D:"


@pytest.mark.parametrize("key", ["cpu_percent", "memory_percent", "disk_percent", "gpu_percent"])
def test_normalize_monitor_sample_rejects_invalid_percent(key: str) -> None:
    sample = _sample()
    sample[key] = 101
    with pytest.raises(ValueError, match=key):
        normalize_monitor_sample(sample)


def test_normalize_monitor_sample_rejects_invalid_nested_percent() -> None:
    sample = _sample()
    sample["gpus"][1]["vram_percent"] = 101
    with pytest.raises(ValueError, match=r"gpus\[1\]\.vram_percent"):
        normalize_monitor_sample(sample)


def test_monitor_state_marks_missing_and_stale_samples() -> None:
    state = SystemMonitorState(stale_after_sec=0.01)
    assert state.snapshot() == {"available": False, "stale": True}
    assert state.snapshots() == []
    state.update(_sample())
    assert state.snapshot()["available"] is True
    time.sleep(0.02)
    snapshot = state.snapshot()
    assert snapshot["available"] is False
    assert snapshot["stale"] is True


def test_monitor_state_keeps_multiple_hosts_in_stable_order() -> None:
    state = SystemMonitorState()
    second = _sample()
    second["hostname"] = "zeta"
    first = _sample()
    first["hostname"] = "Alpha"
    state.update(second)
    state.update(first)

    snapshots = state.snapshots()
    assert [sample["hostname"] for sample in snapshots] == ["Alpha", "zeta"]
    assert all(sample["available"] is True for sample in snapshots)


def test_monitor_state_orders_hosts_by_display_order() -> None:
    state = SystemMonitorState()
    second = _sample()
    second.update(hostname="host-b", display_order=1)
    first = _sample()
    first.update(hostname="host-a", display_order=0)
    state.update(second)
    state.update(first)

    assert [sample["hostname"] for sample in state.snapshots()] == ["host-a", "host-b"]


def test_monitor_state_replaces_same_hostname_case_insensitively() -> None:
    state = SystemMonitorState()
    original = _sample()
    original["hostname"] = "WORKSTATION"
    updated = _sample()
    updated["hostname"] = "workstation"
    updated["cpu_percent"] = 77
    state.update(original)
    state.update(updated)

    snapshots = state.snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["hostname"] == "workstation"
    assert snapshots[0]["cpu_percent"] == 77
