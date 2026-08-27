from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from ..config_schema import WindowConfig


def _clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def window_label(config: WindowConfig) -> str:
    if config.type == "always":
        return "全天"
    if config.type == "event":
        return "手动事件窗口"
    if config.type == "clock" and config.clock is not None:
        return f"{config.clock.start}–{config.clock.end}"
    if config.type == "solar" and config.solar is not None:
        return f"{config.solar.start}→{config.solar.end}"
    return config.type


def in_capture_window(config: WindowConfig, now: datetime, event_active: bool = True) -> bool:
    local = now.astimezone(ZoneInfo(config.timezone))
    if config.type == "always":
        return True
    if config.type == "event":
        return event_active
    if config.type == "clock":
        assert config.clock is not None
        start, end = _clock(config.clock.start), _clock(config.clock.end)
        current = local.time().replace(tzinfo=None)
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    assert config.solar and config.latitude is not None and config.longitude is not None
    location = LocationInfo(
        latitude=config.latitude,
        longitude=config.longitude,
        timezone=config.timezone,
    )
    events = sun(location.observer, date=local.date(), tzinfo=ZoneInfo(config.timezone))
    start = events[config.solar.start] + timedelta(minutes=config.solar.start_offset_min)
    end = events[config.solar.end] + timedelta(minutes=config.solar.end_offset_min)
    return start <= local < end
