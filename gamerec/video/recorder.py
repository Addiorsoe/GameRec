"""Record the target window to MP4 and log a timestamp for every frame."""
from __future__ import annotations

import csv
import time

from ..process import window_rect
from ..recorder import Recorder


class ScreenRecorder(Recorder):
    kind = "video"

    def __init__(self, fps: float = 15.0, scale: float = 1.0, region: tuple[int, int, int, int] | None = None) -> None:
        super().__init__()
        self.fps = fps
        self.scale = scale
        self.region = region  # (left, top, width, height); None = target window / primary monitor

    def _grab_region(self) -> tuple[int, int, int, int]:
        if self.region:
            return self.region
        rect = window_rect(self.session.target.hwnd) if self.session.target.hwnd else None
        if rect and rect[2] > 0 and rect[3] > 0:
            return rect
        mon = self._sct.monitors[1]
        return mon["left"], mon["top"], mon["width"], mon["height"]

    def setup(self) -> None:
        import cv2  # noqa: PLC0415
        import mss  # noqa: PLC0415

        self._cv2 = cv2
        self._mss = mss
        # mss handles are tied to the creating thread on some versions (Windows GDI),
        # so this one is only used to size the video; run() opens its own.
        self._sct = mss.mss()
        try:
            left, top, w, h = self._grab_region()
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
        self._csv.writerow(["frame", "t_ns", "left", "top", "width", "height"])
        self.extra = {"fps": self.fps, "scale": self.scale, "size": list(self._size)}

    def run(self) -> None:
        import numpy as np

        cv2 = self._cv2
        self._sct = self._mss.mss()
        period = 1.0 / self.fps
        next_t = time.perf_counter()
        while not self.stopping:
            left, top, w, h = self._grab_region()
            t = self.now_ns()
            img = np.asarray(self._sct.grab({"left": left, "top": top, "width": w, "height": h}))
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            if frame.shape[1::-1] != self._size:
                frame = cv2.resize(frame, self._size, interpolation=cv2.INTER_AREA)
            self._writer.write(frame)
            self._csv.writerow([self.count, t, left, top, w, h])
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
        if getattr(self, "_sct", None):
            self._sct.close()
