"""Log keyboard and mouse events (what the player pressed) on the session timeline."""
from __future__ import annotations

import json
import threading
import time

from ..recorder import Recorder


class InputLogger(Recorder):
    kind = "input"

    def __init__(self, mouse_move_hz: float = 20.0) -> None:
        super().__init__()
        self._min_move_ns = int(1e9 / mouse_move_hz) if mouse_move_hz else 0
        self._last_move = 0
        self._lock = threading.Lock()
        self.on_key = None  # optional callback(key_name) used for hotkeys (e.g. F9 marker)

    def setup(self) -> None:
        from pynput import keyboard, mouse  # noqa: PLC0415

        self._fh = open(self.path("input/events.jsonl"), "w", encoding="utf-8")
        self._kb = keyboard.Listener(on_press=self._key_down, on_release=self._key_up)
        self._ms = mouse.Listener(on_move=self._move, on_click=self._click, on_scroll=self._scroll)
        self._kb.start()
        self._ms.start()

    def _write(self, rec: dict) -> None:
        rec = {"t_ns": self.now_ns(), **rec}
        with self._lock:
            if self._fh.closed:
                return
            self._fh.write(json.dumps(rec) + "\n")
            self.count += 1

    @staticmethod
    def _name(key) -> str:
        return getattr(key, "char", None) or str(key).replace("Key.", "")

    def _key_down(self, key):
        name = self._name(key)
        self._write({"kind": "key_down", "key": name})
        if self.on_key:
            self.on_key(name)

    def _key_up(self, key):
        self._write({"kind": "key_up", "key": self._name(key)})

    def _move(self, x, y):
        t = time.perf_counter_ns()
        if t - self._last_move >= self._min_move_ns:
            self._last_move = t
            self._write({"kind": "mouse_move", "x": x, "y": y})

    def _click(self, x, y, button, pressed):
        self._write({"kind": "mouse_down" if pressed else "mouse_up", "x": x, "y": y, "button": str(button).replace("Button.", "")})

    def _scroll(self, x, y, dx, dy):
        self._write({"kind": "scroll", "x": x, "y": y, "dx": dx, "dy": dy})

    def run(self) -> None:
        self._stop.wait()

    def teardown(self) -> None:
        for lst in ("_kb", "_ms"):
            if getattr(self, lst, None):
                getattr(self, lst).stop()
        if getattr(self, "_fh", None):
            with self._lock:
                self._fh.close()
