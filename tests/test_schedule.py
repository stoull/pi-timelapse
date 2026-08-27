from datetime import datetime
from zoneinfo import ZoneInfo

from timelapse.config_schema import WindowConfig
from timelapse.schedule.ticker import next_slot
from timelapse.schedule.window import in_capture_window


TZ = ZoneInfo("Asia/Shanghai")


def test_next_slot_is_aligned_and_future():
    now = datetime(2026, 8, 23, 12, 0, 7, tzinfo=TZ)
    assert next_slot(now, 10) == datetime(2026, 8, 23, 12, 0, 10, tzinfo=TZ)


def test_cross_midnight_clock_window():
    config = WindowConfig.model_validate(
        {
            "type": "clock",
            "timezone": "Asia/Shanghai",
            "clock": {"start": "22:00", "end": "02:00"},
        }
    )
    assert in_capture_window(config, datetime(2026, 8, 23, 23, tzinfo=TZ))
    assert in_capture_window(config, datetime(2026, 8, 24, 1, tzinfo=TZ))
    assert not in_capture_window(config, datetime(2026, 8, 24, 12, tzinfo=TZ))


def test_event_window_obeys_runtime_flag():
    config = WindowConfig(type="event")
    now = datetime.now(TZ)
    assert in_capture_window(config, now, True)
    assert not in_capture_window(config, now, False)
