"""Shared session clock. Every stream writes t_ns = nanoseconds since session start."""
from __future__ import annotations

import time
from datetime import datetime, timezone


class SessionClock:
    def __init__(self) -> None:
        self._perf0 = time.perf_counter_ns()
        self.start_epoch_ns = time.time_ns()
        self.start_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def now_ns(self) -> int:
        return time.perf_counter_ns() - self._perf0

    def epoch_to_t_ns(self, epoch_s: float) -> int:
        """Convert an epoch timestamp (e.g. a pcap packet time) into session t_ns."""
        return int(epoch_s * 1e9) - self.start_epoch_ns

    def to_dict(self) -> dict:
        return {
            "start_utc": self.start_utc,
            "start_epoch_ns": self.start_epoch_ns,
            "unit": "t_ns = nanoseconds since session start (monotonic)",
        }
