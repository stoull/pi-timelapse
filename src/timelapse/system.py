from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple


class SystemMonitor:
    def __init__(
        self,
        stat_path: Path = Path("/proc/stat"),
        meminfo_path: Path = Path("/proc/meminfo"),
        thermal_path: Path = Path("/sys/class/thermal/thermal_zone0/temp"),
    ) -> None:
        self.stat_path = stat_path
        self.meminfo_path = meminfo_path
        self.thermal_path = thermal_path
        self._cpu_sample: Optional[Tuple[int, int]] = None

    def snapshot(self) -> dict[str, Any]:
        used, total = self._memory_bytes()
        percent = None
        if used is not None and total:
            percent = round(used / total * 100, 1)
        return {
            "cpu_percent": self._cpu_percent(),
            "cpu_temp_c": self._cpu_temp_c(),
            "memory_used_bytes": used,
            "memory_total_bytes": total,
            "memory_percent": percent,
        }

    def _cpu_times(self) -> Optional[Tuple[int, int]]:
        try:
            line = self.stat_path.read_text(encoding="utf-8").splitlines()[0]
        except OSError:
            return None
        parts = line.split()
        if not parts or parts[0] != "cpu" or len(parts) < 5:
            return None
        values = [int(item) for item in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values[:8])
        return idle, total

    def _cpu_percent(self) -> Optional[float]:
        sample = self._cpu_times()
        if sample is None:
            return None
        previous = self._cpu_sample
        self._cpu_sample = sample
        if previous is None:
            return None
        idle_delta = sample[0] - previous[0]
        total_delta = sample[1] - previous[1]
        if total_delta <= 0:
            return None
        busy = 1.0 - (idle_delta / total_delta)
        return round(max(0.0, min(100.0, busy * 100.0)), 1)

    def _cpu_temp_c(self) -> Optional[float]:
        try:
            raw = int(self.thermal_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if raw > 1000:
            return round(raw / 1000.0, 1)
        return round(float(raw), 1)

    def _memory_bytes(self) -> Tuple[Optional[int], Optional[int]]:
        try:
            lines = self.meminfo_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None, None
        values: dict[str, int] = {}
        for line in lines:
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            number = raw.strip().split()[0]
            try:
                values[key] = int(number) * 1024
            except ValueError:
                continue
        total = values.get("MemTotal")
        if not total:
            return None, None
        available = values.get("MemAvailable")
        if available is None:
            available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
        used = max(0, total - available)
        return used, total


monitor = SystemMonitor()
