"""Windows PC system metrics collection and authenticated push support."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from typing import Any

import psutil

from .signature import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign_now

_PERCENT_FIELDS = (
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "gpu_percent",
    "gpu_memory_percent",
)
_NUMBER_FIELDS = (
    "cpu_temperature_c",
    "memory_used_gb",
    "memory_total_gb",
    "disk_used_gb",
    "disk_total_gb",
    "gpu_memory_used_gb",
    "gpu_memory_total_gb",
    "gpu_temperature_c",
    "network_rx_mbps",
    "network_tx_mbps",
    "uptime_seconds",
)


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return round(float(value), 2)


def _percent(payload: dict[str, Any], key: str, *, label: str = "") -> float | None:
    value = _optional_number(payload, key)
    field = label or key
    if value is not None and not 0 <= value <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return value


def _non_negative(payload: dict[str, Any], key: str, *, label: str = "") -> float | None:
    value = _optional_number(payload, key)
    field = label or key
    if value is not None and value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _normalize_gpu(payload: dict[str, Any], position: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"gpus[{position}] must be an object")
    name = str(payload.get("name") or f"GPU {position}").strip()[:96]
    return {
        "index": int(payload.get("index", position)),
        "name": name,
        "utilization_percent": _percent(
            payload, "utilization_percent", label=f"gpus[{position}].utilization_percent"
        ),
        "vram_percent": _percent(
            payload, "vram_percent", label=f"gpus[{position}].vram_percent"
        ),
        "vram_used_gb": _non_negative(
            payload, "vram_used_gb", label=f"gpus[{position}].vram_used_gb"
        ),
        "vram_total_gb": _non_negative(
            payload, "vram_total_gb", label=f"gpus[{position}].vram_total_gb"
        ),
        "temperature_c": _non_negative(
            payload, "temperature_c", label=f"gpus[{position}].temperature_c"
        ),
    }


def _normalize_disk(payload: dict[str, Any], position: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"disks[{position}] must be an object")
    name = str(payload.get("name") or f"Disk {position + 1}").strip()[:64]
    mountpoint = str(payload.get("mountpoint") or "").strip()[:128]
    return {
        "name": name,
        "mountpoint": mountpoint,
        "usage_percent": _percent(
            payload, "usage_percent", label=f"disks[{position}].usage_percent"
        ),
        "used_gb": _non_negative(payload, "used_gb", label=f"disks[{position}].used_gb"),
        "total_gb": _non_negative(payload, "total_gb", label=f"disks[{position}].total_gb"),
        "temperature_c": _non_negative(
            payload, "temperature_c", label=f"disks[{position}].temperature_c"
        ),
    }


def normalize_monitor_sample(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize one sample before it enters shared server state."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    hostname = str(payload.get("hostname") or "").strip()[:80]
    if not hostname:
        raise ValueError("hostname is required")

    captured_at = str(payload.get("captured_at") or "").strip()[:64]
    if not captured_at:
        raise ValueError("captured_at is required")

    normalized: dict[str, Any] = {
        "hostname": hostname,
        "captured_at": captured_at,
        "cpu_name": str(payload.get("cpu_name") or "CPU").strip()[:96],
    }
    display_order = payload.get("display_order")
    if display_order is not None:
        if isinstance(display_order, bool) or not isinstance(display_order, int):
            raise ValueError("display_order must be an integer")
        if not -1000 <= display_order <= 1000:
            raise ValueError("display_order must be between -1000 and 1000")
    normalized["display_order"] = display_order
    for key in _PERCENT_FIELDS:
        normalized[key] = _percent(payload, key)
    for key in _NUMBER_FIELDS:
        normalized[key] = _non_negative(payload, key)

    raw_gpus = payload.get("gpus") or []
    raw_disks = payload.get("disks") or []
    if not isinstance(raw_gpus, list) or len(raw_gpus) > 16:
        raise ValueError("gpus must be an array with at most 16 items")
    if not isinstance(raw_disks, list) or len(raw_disks) > 64:
        raise ValueError("disks must be an array with at most 64 items")
    normalized["gpus"] = [_normalize_gpu(item, index) for index, item in enumerate(raw_gpus)]
    normalized["disks"] = [_normalize_disk(item, index) for index, item in enumerate(raw_disks)]
    return normalized


class SystemMonitorState:
    """Thread-safe storage for the latest sample from each monitored PC."""

    def __init__(self, *, stale_after_sec: float = 12.0) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, tuple[dict[str, Any], float]] = {}
        self._stale_after_sec = stale_after_sec

    def update(self, payload: dict[str, Any]) -> None:
        sample = normalize_monitor_sample(payload)
        key = sample["hostname"].casefold()
        with self._lock:
            self._samples[key] = (sample, time.monotonic())

    def snapshot(self) -> dict[str, Any]:
        """Return the most recently received host for legacy API clients."""
        snapshots = self.snapshots()
        if not snapshots:
            return {"available": False, "stale": True}
        return min(snapshots, key=lambda sample: sample["age_seconds"])

    def snapshots(self) -> list[dict[str, Any]]:
        """Return every known host in stable hostname order."""
        with self._lock:
            received = [
                (dict(sample), received_at)
                for sample, received_at in self._samples.values()
            ]
        now = time.monotonic()
        snapshots: list[dict[str, Any]] = []
        for sample, received_at in received:
            age_sec = max(0.0, now - received_at)
            stale = age_sec > self._stale_after_sec
            sample.update(
                available=not stale,
                stale=stale,
                age_seconds=round(age_sec, 1),
            )
            snapshots.append(sample)
        return sorted(
            snapshots,
            key=lambda sample: (
                sample.get("display_order")
                if sample.get("display_order") is not None
                else 1001,
                sample["hostname"].casefold(),
            ),
        )


def _read_cpu_name() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor().strip() or "CPU"


def _read_cpu_temperature() -> float | None:
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return None
    try:
        groups = reader(fahrenheit=False)
    except (OSError, RuntimeError):
        return None
    preferred: list[float] = []
    fallback: list[float] = []
    for name, entries in groups.items():
        for entry in entries:
            current = getattr(entry, "current", None)
            if not isinstance(current, (int, float)) or current <= 0:
                continue
            label = f"{name} {getattr(entry, 'label', '')}".lower()
            if any(word in label for word in ("package", "cpu", "core")):
                preferred.append(float(current))
            else:
                fallback.append(float(current))
    values = preferred or fallback
    return max(values) if values else None


def _read_nvidia_gpus() -> list[dict[str, Any]]:
    command = shutil.which("nvidia-smi")
    if not command:
        return []
    try:
        result = subprocess.run(
            [
                command,
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    gpus: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        try:
            index_raw, name, utilization_raw, used_raw, total_raw, temperature_raw = [
                part.strip() for part in line.split(",", 5)
            ]
            used_mb = float(used_raw)
            total_mb = float(total_raw)
            gpus.append(
                {
                    "index": int(index_raw),
                    "name": name,
                    "utilization_percent": float(utilization_raw),
                    "vram_percent": used_mb / total_mb * 100 if total_mb > 0 else None,
                    "vram_used_gb": used_mb / 1024,
                    "vram_total_gb": total_mb / 1024,
                    "temperature_c": float(temperature_raw),
                }
            )
        except (TypeError, ValueError):
            continue
    return gpus


def _read_disks() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for partition in psutil.disk_partitions(all=False):
        mountpoint = partition.mountpoint
        if mountpoint in seen or not partition.fstype:
            continue
        seen.add(mountpoint)
        try:
            usage = psutil.disk_usage(mountpoint)
        except OSError:
            continue
        name = mountpoint.rstrip("\\/") or mountpoint
        disks.append(
            {
                "name": name,
                "mountpoint": mountpoint,
                "usage_percent": usage.percent,
                "used_gb": usage.used / 1024**3,
                "total_gb": usage.total / 1024**3,
                "temperature_c": None,
            }
        )
    return disks


class SystemMonitorCollector:
    """Collect PC metrics and derive network throughput between samples."""

    def __init__(self) -> None:
        self._cpu_name = _read_cpu_name()
        self._previous_net = psutil.net_io_counters()
        self._previous_net_at = time.monotonic()

    def collect(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disks = _read_disks()
        gpus = _read_nvidia_gpus()
        current_net = psutil.net_io_counters()
        current_net_at = time.monotonic()
        elapsed = max(0.001, current_net_at - self._previous_net_at)
        rx_mbps = max(0, current_net.bytes_recv - self._previous_net.bytes_recv) * 8 / elapsed / 1_000_000
        tx_mbps = max(0, current_net.bytes_sent - self._previous_net.bytes_sent) * 8 / elapsed / 1_000_000
        self._previous_net = current_net
        self._previous_net_at = current_net_at

        sample: dict[str, Any] = {
            "hostname": socket.gethostname(),
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cpu_name": self._cpu_name,
            "cpu_percent": psutil.cpu_percent(interval=0.15),
            "cpu_temperature_c": _read_cpu_temperature(),
            "memory_percent": memory.percent,
            "memory_used_gb": memory.used / 1024**3,
            "memory_total_gb": memory.total / 1024**3,
            "disk_percent": disks[0]["usage_percent"] if disks else None,
            "disk_used_gb": disks[0]["used_gb"] if disks else None,
            "disk_total_gb": disks[0]["total_gb"] if disks else None,
            "network_rx_mbps": rx_mbps,
            "network_tx_mbps": tx_mbps,
            "uptime_seconds": max(0, time.time() - psutil.boot_time()),
            "gpu_percent": gpus[0]["utilization_percent"] if gpus else None,
            "gpu_memory_percent": gpus[0]["vram_percent"] if gpus else None,
            "gpu_memory_used_gb": gpus[0]["vram_used_gb"] if gpus else None,
            "gpu_memory_total_gb": gpus[0]["vram_total_gb"] if gpus else None,
            "gpu_temperature_c": gpus[0]["temperature_c"] if gpus else None,
            "gpus": gpus,
            "disks": disks,
        }
        return normalize_monitor_sample(sample)


def push_monitor_sample(target: str, token: str, sample: dict[str, Any]) -> None:
    body = json.dumps(sample, ensure_ascii=False).encode("utf-8")
    # 토큰을 그대로 보내지 않고 본문에 대한 HMAC 서명만 실어 보낸다.
    timestamp, signature = sign_now(token, body)
    request = urllib.request.Request(
        target,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: signature,
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status != 204:
            raise RuntimeError(f"unexpected monitor response: HTTP {response.status}")


def run_monitor_pusher(
    target: str,
    token: str,
    *,
    interval_sec: float = 2.0,
    once: bool = False,
) -> int:
    if not target.startswith(("http://", "https://")):
        raise ValueError("monitor target must be an HTTP URL")
    if not token:
        raise ValueError("monitor token is empty")
    if interval_sec < 0.5:
        raise ValueError("monitor interval must be at least 0.5 seconds")

    collector = SystemMonitorCollector()
    last_error_log = 0.0
    while True:
        started = time.monotonic()
        try:
            push_monitor_sample(target, token, collector.collect())
        except Exception as exc:  # noqa: BLE001
            now = time.monotonic()
            if once or now - last_error_log >= 30:
                print(f"[system-monitor] push failed: {type(exc).__name__}: {exc}")
                last_error_log = now
            if once:
                return 1
        else:
            if once:
                return 0
        delay = max(0.0, interval_sec - (time.monotonic() - started))
        time.sleep(delay)
