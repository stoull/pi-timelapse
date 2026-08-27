from pathlib import Path

from timelapse.system import SystemMonitor


def test_system_monitor_reads_cpu_temp_and_memory(tmp_path):
    stat = tmp_path / "stat"
    mem = tmp_path / "meminfo"
    thermal = tmp_path / "temp"
    stat.write_text("cpu  100 0 100 800 0 0 0 0 0 0\n", encoding="utf-8")
    mem.write_text(
        "MemTotal:        2048000 kB\nMemAvailable:     512000 kB\n",
        encoding="utf-8",
    )
    thermal.write_text("45123\n", encoding="utf-8")

    monitor = SystemMonitor(stat, mem, thermal)
    first = monitor.snapshot()
    assert first["cpu_percent"] is None
    assert first["cpu_temp_c"] == 45.1
    assert first["memory_total_bytes"] == 2048000 * 1024
    assert first["memory_used_bytes"] == (2048000 - 512000) * 1024
    assert first["memory_percent"] == 75.0

    stat.write_text("cpu  200 0 200 850 0 0 0 0 0 0\n", encoding="utf-8")
    second = monitor.snapshot()
    assert second["cpu_percent"] == 80.0


def test_system_monitor_missing_files_are_safe(tmp_path):
    monitor = SystemMonitor(tmp_path / "missing-stat", tmp_path / "missing-mem", tmp_path / "missing-temp")
    snap = monitor.snapshot()
    assert snap["cpu_percent"] is None
    assert snap["cpu_temp_c"] is None
    assert snap["memory_used_bytes"] is None
    assert snap["memory_total_bytes"] is None
