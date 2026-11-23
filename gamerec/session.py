"""A recording session: one folder, one clock, many recorders."""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from .clock import SessionClock
from .process import TargetInfo
from .recorder import Recorder

log = logging.getLogger("gamerec")


class Session:
    def __init__(self, target: TargetInfo, recorders: list[Recorder], root: str | Path = "sessions") -> None:
        self.target = target
        self.recorders = recorders
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^\w.-]+", "_", target.name)
        self.name = f"session_{stamp}_{safe}"
        self.dir = Path(root) / self.name
        self.clock: SessionClock | None = None
        self.started = False
        self.stopped = False
        self.duration_ns = 0
        self.markers: list[dict] = []
        self._markers_lock = threading.Lock()
        self._log_handler: logging.Handler | None = None
        self.notes = ""

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "logs").mkdir(exist_ok=True)
        self._log_handler = logging.FileHandler(self.dir / "logs" / "recorder.log", encoding="utf-8")
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
        log.addHandler(self._log_handler)
        log.setLevel(logging.INFO)
        self.clock = SessionClock()
        self.target_snapshot = self.target.to_dict()
        log.info("session %s start, target=%s", self.name, self.target_snapshot)
        for r in self.recorders:
            r.start(self)
            log.info("recorder %s: %s %s", r.kind, r.status, r.error or "")
        self.started = True

    def mark(self, label: str = "") -> dict:
        assert self.clock is not None
        m = {"t_ns": self.clock.now_ns(), "label": label or f"marker {len(self.markers) + 1}"}
        with self._markers_lock:
            self.markers.append(m)
        log.info("marker %s", m)
        return m

    def stop(self) -> None:
        if self.stopped or not self.started:
            return
        assert self.clock is not None
        self.duration_ns = self.clock.now_ns()
        for r in self.recorders:
            r.stop()
            log.info("recorder %s stopped: %s count=%d", r.kind, r.status, r.count)
        with open(self.dir / "markers.jsonl", "w", encoding="utf-8") as f:
            for m in self.markers:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        self.stopped = True
        log.info("session stopped, duration %.2fs", self.duration_ns / 1e9)
        if self._log_handler:
            log.removeHandler(self._log_handler)
            self._log_handler.close()

    def stats(self) -> dict[str, tuple[str, int]]:
        return {r.kind: (r.status, r.count) for r in self.recorders}
