"""Record the target window to MP4 and log a timestamp for every frame."""
from __future__ import annotations

import csv
import time

from ..process import window_rect
from ..recorder import Recorder


class ScreenRecorder(Recorder):
    kind = "video"

    def __init__(self, fps: float = 15.0, scale: float = 0.75, region: tuple[int, int, int, int] | None = None) -> None:
        super().__init__()
        self.fps = fps
        self.scale = scale
        self.region = region  # (left, top, width, height); None = target window / primary monitor

    def _grab_region(self) -> tuple[int, int, int, int, str]:
        """(left, top, width, height, source) - source says what the frame really shows."""
        if self.region:
            return (*self.region, "region")
        rect = window_rect(self.session.target.hwnd) if self.session.target.hwnd else None
        if rect and rect[2] > 0 and rect[3] > 0:
            return (*rect, "window")
        mon = self._sct.monitors[1]  # no window / minimized: whole screen, flagged as such
        return mon["left"], mon["top"], mon["width"], mon["height"], "screen"

    def setup(self) -> None:
        import cv2  # noqa: PLC0415
        import mss  # noqa: PLC0415

        self._cv2 = cv2
        self._mss = mss
        # mss handles are tied to the creating thread on some versions (Windows GDI),
        # so this one is only used to size the video; run() opens its own.
        self._sct = mss.mss()
        try:
            left, top, w, h, _ = self._grab_region()
        finally:
            self._sct.close()
            self._sct = None
        self._size = (max(2, int(w * self.scale)) // 2 * 2, max(2, int(h * self.scale)) // 2 * 2)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self._writer = cv2.VideoWriter(str(self.path("video/screen.mp4")), fourcc, self.fps, self._size)
        if not self._writer.isOpened():
            raise OSError("cannot open video writer")
        self._fh = open(self.path("video/frames.csv"), "w", newline="", encoding="utf-8")
        self._csv = csv.writer(self._fh)
        # dup=1: repeated frame so the mp4 keeps real time when capture is slower than fps
        self._csv.writerow(["frame", "t_ns", "left", "top", "width", "height", "source", "dup"])
        self.extra = {"fps": self.fps, "scale": self.scale, "size": list(self._size)}

    def run(self) -> None:
        import numpy as np

        cv2 = self._cv2
        self._sct = self._mss.mss()
        try:
            self._loop(np, cv2)
        finally:
            # GDI handles must be released on the thread that created them (Windows)
            try:
                self._sct.close()
            finally:
                self._sct = None

    def _loop(self, np, cv2) -> None:
        period = 1.0 / self.fps
        start = time.perf_counter()
        next_t = start
        max_dup = int(self.fps * 2)  # a long stall is not padded forever
        while not self.stopping:
            left, top, w, h, src = self._grab_region()
            t = self.now_ns()
            img = np.asarray(self._sct.grab({"left": left, "top": top, "width": w, "height": h}))
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            if frame.shape[1::-1] != self._size:
                frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_AREA)
            # frames the mp4 should hold by now at a constant fps; repeat this one to catch up
            due = int((time.perf_counter() - start) * self.fps) + 1
            n = max(1, min(due - self.count, max_dup))
            for i in range(n):
                self._writer.write(frame)
                self._csv.writerow([self.count, t, left, top, w, h, src, int(i > 0)])
                self.count += 1
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_t = time.perf_counter()

    def teardown(self) -> None:
        if getattr(self, "_writer", None):
            self._writer.release()
        if getattr(self, "_fh", None):
            self._fh.close()
        if self.count == 0 and self.status == "running":
            # an empty video must not be reported as "ok"
            raise RuntimeError("no frames were captured (screen capture stalled or failed)")
