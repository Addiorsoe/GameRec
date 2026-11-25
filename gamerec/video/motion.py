"""Tells from the game window whether the view is panning (player walking) or still.

Used by the finder's auto mode: in games whose camera follows the player, the whole scene
shifts while the player walks. The global shift between consecutive small grayscale frames is
measured with phase correlation on the central part of the window (the HUD around the edges
doesn't move and would pull the estimate towards zero). Animations and effects change pixels
but produce no consistent global shift, so they read as "still".

This only says whether the VIEW moves; it is a timing signal for filtering memory, not a
conclusion about what any memory value means.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

SIZE = (320, 180)       # analysis resolution
MOVE_PX = 0.45          # global shift per frame (at 320 px width) that counts as panning
MIN_RESPONSE = 0.05     # below this the match is unreliable (big effects, scene change): frame ignored
                        # (real walking in the game measured 0.16-0.32, standing 0.5-0.9)
MAX_SHIFT = 20.0        # px per frame at 320 px width; a larger "shift" is a false match
LOW_FRAMES = 4          # this many unreliable frames in a row = scene change / menu -> "unknown"
FPS = 8
FLAT_STD = 2.0          # frames this uniform (grey levels) can't show motion
BLUR = 2.0              # Gaussian sigma at analysis resolution


class MotionDetector:
    def __init__(self, grab: Callable[[], np.ndarray | None], fps: float = FPS) -> None:
        """grab() returns the current window image (any size, BGR/BGRA/gray) or None."""
        self.grab = grab
        self.fps = fps
        self.state = "unknown"          # walk | still | unknown
        self.since = time.monotonic()   # when the current state began
        self.last_shift = 0.0
        self._recent: deque[bool] = deque(maxlen=5)
        self._prev = None
        self._low = 0
        self._low_since = 0.0
        self._win = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""

    # ----------------------------------------------------------- analysis
    def _prep(self, img: np.ndarray) -> np.ndarray:
        import cv2

        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY if img.shape[2] == 4 else cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]
        img = img[int(h * 0.15): int(h * 0.80), int(w * 0.15): int(w * 0.85)]  # skip HUD at the edges
        img = cv2.resize(img, SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
        # blur away fine detail: flickering effects/particles then barely disturb the match,
        # while the scene's large structure (which pans as a whole) still locks it
        return cv2.GaussianBlur(img, (0, 0), BLUR)

    def feed(self, img: np.ndarray, now: float | None = None) -> str:
        """Analyse one frame (called by the thread; tests call it directly)."""
        import cv2

        now = time.monotonic() if now is None else now
        cur = self._prep(img)
        if self._win is None:
            self._win = cv2.createHanningWindow(SIZE, cv2.CV_32F)
        if self._prev is not None:
            (dx, dy), resp = cv2.phaseCorrelate(self._prev, cur, self._win)
            self.last_shift = float(np.hypot(dx, dy))
            flat = float(cur.std()) < FLAT_STD   # loading screen / black fade: nothing to track
            if flat or not np.isfinite(resp) or resp < MIN_RESPONSE or self.last_shift > MAX_SHIFT:
                # single bad matches happen with big effects on screen; a run of them = scene change.
                # "unknown" is dated from the first bad frame so callers drop samples from then on.
                if self._low == 0:
                    self._low_since = now
                self._low += 1
                if self._low >= LOW_FRAMES:
                    self._recent.clear()
                    self._set("unknown", self._low_since)
            else:
                self._low = 0
                self._recent.append(self.last_shift > MOVE_PX)
                moving = sum(self._recent)
                if len(self._recent) >= 3 and sum(list(self._recent)[-3:]) >= 2:
                    self._set("walk", now)
                elif len(self._recent) == self._recent.maxlen and moving == 0:
                    self._set("still", now)
        self._prev = cur
        return self.state

    def _set(self, state: str, now: float) -> None:
        if state != self.state:
            self.state = state
            self.since = now

    # ---------------------------------------------------------- queries
    def walking_since(self, t: float) -> bool:
        return self.state == "walk" and self.since <= t - 0.0

    def still_for(self) -> float:
        return time.monotonic() - self.since if self.state == "still" else 0.0

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="motion")
        self._thread.start()

    def _run(self) -> None:
        period = 1.0 / self.fps
        while not self._stop.is_set():
            t = time.monotonic()
            try:
                img = self.grab()
                if img is not None:
                    self.feed(img)
            except Exception as e:  # noqa: BLE001 - keep running; report once
                self.error = f"{type(e).__name__}: {e}"
            self._stop.wait(max(0.0, period - (time.monotonic() - t)))

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)


def window_grabber(hwnd: int) -> Callable[[], np.ndarray | None]:
    """grab() for a window (mss handle created lazily on the calling thread)."""
    from ..process import window_rect

    state: dict = {}

    def grab():
        import mss

        if "sct" not in state:
            state["sct"] = mss.mss()
        rect = window_rect(hwnd) if hwnd else None
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            return None
        left, top, w, h = rect
        return np.asarray(state["sct"].grab({"left": left, "top": top, "width": w, "height": h}))

    return grab
