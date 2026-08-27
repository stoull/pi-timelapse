from __future__ import annotations

import math
from datetime import datetime, timedelta


def next_slot(now: datetime, interval_sec: float) -> datetime:
    timestamp = now.timestamp()
    slot = (math.floor(timestamp / interval_sec) + 1) * interval_sec
    return datetime.fromtimestamp(slot, tz=now.tzinfo)


def advance_slot(slot: datetime, interval_sec: float, now: datetime) -> datetime:
    candidate = slot + timedelta(seconds=interval_sec)
    return next_slot(now, interval_sec) if candidate <= now else candidate
